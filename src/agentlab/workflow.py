"""A local SQL transaction can atomically persist an effect and its dedup record.

The caller is trusted application code. This class does not authenticate humans.
Never expose approve() or a tenant selector as a model-callable tool.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Iterator
import uuid


class WorkflowError(ValueError):
    pass


def identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise WorkflowError("invalid_identifier")


def canonical_payload(payload: dict) -> tuple[str, str]:
    if not isinstance(payload, dict) or set(payload) != {"subject", "body", "priority"}:
        raise WorkflowError("invalid_payload")
    for key, limit in (("subject", 120), ("body", 2000)):
        if not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= limit:
            raise WorkflowError("invalid_payload")
        try:
            payload[key].encode("utf-8")
        except UnicodeEncodeError:
            # JSON may decode an escaped lone surrogate; reject before persisting it.
            raise WorkflowError("invalid_payload") from None
    if payload["priority"] not in ("P1", "P2", "P3"):
        raise WorkflowError("invalid_priority")
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


class TicketWorkflow:
    def __init__(self, database: str | Path, *, clock: Callable[[], float] = time.time, outbox: bool = False):
        self.clock = clock
        self.outbox = outbox
        self.db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                tenant TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                digest TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
                approval_token TEXT, approval_digest TEXT, approval_version INTEGER,
                approval_expires REAL, reviewer TEXT,
                PRIMARY KEY (tenant, id)
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_id TEXT NOT NULL,
                payload TEXT NOT NULL, UNIQUE (tenant, request_id),
                FOREIGN KEY (tenant, request_id) REFERENCES requests(tenant, id)
            );
            CREATE TABLE IF NOT EXISTS operations (
                tenant TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                request_id TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY (tenant, idempotency_key)
            );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(requests)")}
        if "creator" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN creator TEXT")
        if outbox:
            self.db.execute("""CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_id TEXT NOT NULL,
                payload TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                receipt TEXT, UNIQUE(tenant, request_id))""")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> TicketWorkflow:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _request(self, tenant: str, request_id: str) -> sqlite3.Row:
        identifier(tenant)
        identifier(request_id)
        row = self.db.execute("SELECT * FROM requests WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
        if row is None:
            raise WorkflowError("request_not_found")
        return row

    def create(self, tenant: str, request_id: str, payload: dict, *, creator: str | None = None) -> dict:
        identifier(tenant)
        identifier(request_id)
        if creator is not None:
            identifier(creator)
        encoded, digest = canonical_payload(payload)
        with self.transaction():
            try:
                self.db.execute("INSERT INTO requests (tenant,id,payload,digest,version,state,creator) VALUES (?,?,?,?,1,'pending',?)",
                                (tenant, request_id, encoded, digest, creator))
            except sqlite3.IntegrityError:
                raise WorkflowError("request_already_exists") from None
        return self.checkpoint(tenant, request_id)

    def amend(self, tenant: str, request_id: str, payload: dict, *, expected_version: int | None = None) -> dict:
        encoded, digest = canonical_payload(payload)
        with self.transaction():
            row = self._request(tenant, request_id)
            if expected_version is not None and (type(expected_version) is not int or row["version"] != expected_version):
                raise WorkflowError("stale_review")
            if row["state"] == "committed":
                raise WorkflowError("committed_request_immutable")
            self.db.execute("""UPDATE requests SET payload=?, digest=?, version=version+1, state='pending',
                approval_token=NULL, approval_digest=NULL, approval_version=NULL,
                approval_expires=NULL, reviewer=NULL WHERE tenant=? AND id=?""",
                (encoded, digest, tenant, request_id))
        return self.checkpoint(tenant, request_id)

    def approve(self, tenant: str, request_id: str, *, reviewer: str, ttl_seconds: int = 300,
                expected_version: int | None = None, expected_digest: str | None = None) -> str:
        """Trusted reviewer action, not a text instruction or a planner decision."""
        identifier(reviewer)
        if (expected_version is None) != (expected_digest is None):
            raise WorkflowError("invalid_review_binding")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise WorkflowError("invalid_approval_ttl")
        with self.transaction():
            row = self._request(tenant, request_id)
            if expected_version is not None and (type(expected_version) is not int
                    or expected_version != row["version"] or expected_digest != row["digest"]):
                raise WorkflowError("stale_review")
            if row["state"] == "committed":
                raise WorkflowError("committed_request_immutable")
            token = uuid.uuid4().hex
            self.db.execute("""UPDATE requests SET state='approved', approval_token=?, approval_digest=?,
                approval_version=?, approval_expires=?, reviewer=? WHERE tenant=? AND id=?""",
                (token, row["digest"], row["version"], self.clock() + ttl_seconds, reviewer, tenant, request_id))
        return token

    def execute(self, tenant: str, request_id: str, *, approval_token: str, idempotency_key: str,
                expected_version: int | None = None, expected_digest: str | None = None) -> dict:
        identifier(idempotency_key)
        if (expected_version is None) != (expected_digest is None):
            raise WorkflowError("invalid_review_binding")
        with self.transaction():
            row = self._request(tenant, request_id)
            if expected_version is not None and (type(expected_version) is not int
                    or expected_version != row["version"] or expected_digest != row["digest"]):
                raise WorkflowError("stale_review")
            previous = self.db.execute("SELECT * FROM operations WHERE tenant=? AND idempotency_key=?",
                                       (tenant, idempotency_key)).fetchone()
            if previous:
                if previous["request_id"] != request_id or previous["digest"] != row["digest"]:
                    raise WorkflowError("idempotency_conflict")
                # Return a committed result even if its original approval has now expired.
                # This creates no effect; caller authentication remains the application's job.
                return json.loads(previous["result"])
            if row["state"] == "committed":
                raise WorkflowError("already_committed_use_original_key")
            if (row["state"] != "approved" or not approval_token
                    or approval_token != row["approval_token"]
                    or row["approval_digest"] != row["digest"]
                    or row["approval_version"] != row["version"]
                    or row["approval_expires"] <= self.clock()):
                raise WorkflowError("approval_required_or_stale")
            ticket_id = "ticket-" + uuid.uuid4().hex
            result = {"ticket_id": ticket_id, "state": "created"}
            self.db.execute("INSERT INTO tickets (id,tenant,request_id,payload) VALUES (?,?,?,?)",
                            (ticket_id, tenant, request_id, row["payload"]))
            self.db.execute("INSERT INTO operations (tenant,idempotency_key,request_id,digest,result) VALUES (?,?,?,?,?)",
                            (tenant, idempotency_key, request_id, row["digest"], json.dumps(result, sort_keys=True)))
            if self.outbox:
                self.db.execute("""INSERT INTO outbox (event_id,tenant,request_id,payload,digest)
                    VALUES (?,?,?,?,?)""", (ticket_id, tenant, request_id, row["payload"], row["digest"]))
            self.db.execute("UPDATE requests SET state='committed' WHERE tenant=? AND id=?", (tenant, request_id))
        return result

    def checkpoint(self, tenant: str, request_id: str) -> dict:
        row = self._request(tenant, request_id)
        return {"state": row["state"], "version": row["version"]}

    def read(self, tenant: str, request_id: str) -> dict:
        """Application must authenticate and authorize the caller before exposing this."""
        row = self._request(tenant, request_id)
        return {"request_id": request_id, "payload": json.loads(row["payload"]),
                "digest": row["digest"], "version": row["version"], "state": row["state"],
                "creator": row["creator"], "reviewer": row["reviewer"],
                "approval_expires": row["approval_expires"]}

    def ticket_count(self, tenant: str) -> int:
        identifier(tenant)
        return self.db.execute("SELECT COUNT(*) FROM tickets WHERE tenant=?", (tenant,)).fetchone()[0]
