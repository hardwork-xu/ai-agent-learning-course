"""Worked reference; finish an independent attempt before reading this file."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_units(text: str) -> int:
    if type(text) is not str:
        raise ValueError("JSON text required")
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid JSON") from exc
    if type(value) is not dict or set(value) != {"units"}:
        raise ValueError("expected only units")
    units = value["units"]
    if type(units) is not int or not 1 <= units <= 100:
        raise ValueError("units must be integer 1..100")
    return units


def retry_action(method: str, status: int | None, has_key: bool) -> str:
    if type(method) is not str or method not in {"GET", "POST"}:
        raise ValueError("unsupported method")
    if type(has_key) is not bool:
        raise ValueError("has_key must be bool")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("invalid status")
    temporary = status is None or status in {429, 502, 503, 504}
    if not temporary:
        return "stop"
    return "retry" if method == "GET" or has_key else "reconcile"


def record_pair(connection: sqlite3.Connection, labels: tuple[str, str]) -> None:
    with connection:
        for label in labels:
            connection.execute("INSERT INTO diagnostic_events(label) VALUES (?)", (label,))


def _arguments(request, name, fields):
    if type(request) is not dict or set(request) != {"name", "arguments"}:
        raise ValueError("invalid envelope")
    if request["name"] != name:
        raise ValueError("unknown tool")
    arguments = request["arguments"]
    if type(arguments) is not dict or set(arguments) != fields:
        raise ValueError("invalid arguments")
    sku = arguments["sku"]
    if type(sku) is not str or not re.fullmatch(r"[A-Z]{3}-[0-9]{3}", sku):
        raise ValueError("invalid sku")
    return arguments


def inventory_tool(request: dict, catalog: dict[str, int]) -> dict:
    sku = _arguments(request, "inventory.lookup", {"sku"})["sku"]
    if sku not in catalog:
        raise LookupError("unknown sku")
    return {"sku": sku, "available": catalog[sku]}


def quote_tool(request: dict, prices: dict[str, int]) -> dict:
    arguments = _arguments(request, "inventory.quote", {"sku", "units"})
    sku, units = arguments["sku"], arguments["units"]
    if type(units) is not int or not 1 <= units <= 100:
        raise ValueError("invalid units")
    if sku not in prices:
        raise LookupError("unknown sku")
    total = units * prices[sku]
    if total > 1_000_000:
        raise ValueError("quote above limit")
    return {"sku": sku, "units": units, "total_cents": total}


class LoanBook:
    """One connection per operation; transaction covers local writes only."""

    def __init__(self, path: str):
        self.path = path
        with closing(sqlite3.connect(path, timeout=10)) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant TEXT NOT NULL, asset_id TEXT NOT NULL,
                    days INTEGER NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    tenant TEXT NOT NULL, key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, request_id INTEGER NOT NULL,
                    PRIMARY KEY (tenant, key),
                    FOREIGN KEY (request_id) REFERENCES requests(request_id)
                );
            """)

    def reserve(self, tenant: str, key: str, payload: dict) -> dict:
        for value in (tenant, key):
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                raise ValueError("invalid tenant or key")
        if type(payload) is not dict or set(payload) != {"asset_id", "days"}:
            raise ValueError("invalid payload")
        asset, days = payload["asset_id"], payload["days"]
        if type(asset) is not str or not re.fullmatch(r"[A-Z]{2}-[0-9]{3}", asset):
            raise ValueError("invalid asset_id")
        if type(days) is not int or not 1 <= days <= 14:
            raise ValueError("days must be integer 1..14")
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # BEGIN IMMEDIATE serializes writers before they check the key.
        # Both inserts are committed together; exceptions roll both back.
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT fingerprint, request_id FROM idempotency WHERE tenant=? AND key=?",
                (tenant, key),
            ).fetchone()
            if previous is not None:
                if previous[0] != fingerprint:
                    raise ValueError("idempotency conflict")
                request_id = previous[1]
            else:
                request_id = connection.execute(
                    "INSERT INTO requests(tenant, asset_id, days, state) VALUES (?, ?, ?, 'pending')",
                    (tenant, asset, days),
                ).lastrowid
                connection.execute(
                    "INSERT INTO idempotency(tenant, key, fingerprint, request_id) VALUES (?, ?, ?, ?)",
                    (tenant, key, fingerprint, request_id),
                )
            row = connection.execute(
                "SELECT request_id, asset_id, days, state FROM requests WHERE request_id=? AND tenant=?",
                (request_id, tenant),
            ).fetchone()
            return dict(zip(("request_id", "asset_id", "days", "state"), row))
