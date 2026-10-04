"""Loopback-only teaching API. Synthetic data; not a production web server."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import re
import sqlite3
import stat
import time
from typing import Callable
import uuid

from .workflow import TicketWorkflow, WorkflowError, identifier
from .console import configure_utf8_output

MAX_BODY = 16384


class ServiceError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ValueError("object_required")
    return result


def fields(body: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if not required <= body.keys() or body.keys() - required - optional:
        raise ServiceError(400, "invalid_fields")


def load_config(path: Path) -> dict:
    """Fail closed; reread on every request so revocation takes effect immediately."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("unsafe_config")
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise ValueError("config_permissions")
        config = strict_json(handle.read(65537))
    if set(config) != {"version", "write_enabled", "principals"} or config["version"] != 1:
        raise ValueError("invalid_config")
    if type(config["write_enabled"]) is not bool or not isinstance(config["principals"], list):
        raise ValueError("invalid_config")
    names, tokens, identities = set(), set(), set()
    for principal in config["principals"]:
        if not isinstance(principal, dict) or set(principal) != {"name", "token", "tenant", "actor", "role"}:
            raise ValueError("invalid_principal")
        for key in ("name", "tenant", "actor"):
            identifier(principal[key])
        token = principal["token"]
        identity = (principal["tenant"], principal["actor"])
        if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token)
                or principal["role"] not in {"requester", "reviewer"}
                or token in tokens or principal["name"] in names or identity in identities):
            raise ValueError("invalid_principal")
        tokens.add(token)
        names.add(principal["name"])
        identities.add(identity)
    return config


def init_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"version": 1, "write_enabled": True, "principals": []}
    for tenant in ("campus", "partner"):
        for role in ("requester", "reviewer"):
            config["principals"].append({"name": f"{tenant}-{role}", "tenant": tenant,
                "actor": f"{role}-1", "role": role, "token": secrets.token_urlsafe(32)})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def prepare_database(path: Path, *, clock: Callable[[], float] = time.time) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TicketWorkflow(path, outbox=True, clock=clock) as workflow:
        workflow.db.executescript("""
            CREATE TABLE IF NOT EXISTS service_queries (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS service_events (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                route TEXT NOT NULL, status INTEGER NOT NULL, code TEXT NOT NULL, latency_ms REAL NOT NULL);
        """)
    if os.name == "posix":
        path.chmod(0o600)


def deliver_outbox(database: Path, downstream: Path, *, crash_after_effect: bool = False) -> dict:
    """Two independent SQLite databases. A crash can lose the receipt, not dedup state.

    The downstream primary key is the event ID and its digest binds the payload.
    This is at-least-once delivery with an idempotent consumer, not generic exactly once.
    """
    if database.resolve() == downstream.resolve():
        raise ValueError("downstream_must_be_separate")
    downstream.parent.mkdir(parents=True, exist_ok=True)
    delivered = 0
    with TicketWorkflow(database, outbox=True) as workflow, closing(sqlite3.connect(downstream)) as target:
        target.execute("""CREATE TABLE IF NOT EXISTS received_tickets (
            event_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL)""")
        target.commit()
        if os.name == "posix":
            downstream.chmod(0o600)
        rows = workflow.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY event_id").fetchall()
        for row in rows:
            with target:
                previous = target.execute("SELECT tenant,digest FROM received_tickets WHERE event_id=?",
                                          (row["event_id"],)).fetchone()
                if previous and previous != (row["tenant"], row["digest"]):
                    raise ValueError("downstream_idempotency_conflict")
                target.execute("INSERT OR IGNORE INTO received_tickets VALUES (?,?,?,?)",
                    (row["event_id"], row["tenant"], row["digest"], row["payload"]))
            if crash_after_effect:
                raise RuntimeError("simulated_crash_after_effect")
            with workflow.transaction():
                workflow.db.execute("UPDATE outbox SET state='delivered',receipt=? WHERE event_id=?",
                                    (row["event_id"], row["event_id"]))
            delivered += 1
    return {"delivered": delivered, "delivery": "at_least_once", "consumer": "deduplicated"}


class Application:
    def __init__(self, config: Path, database: Path, *, rag=None, clock=time.time):
        self.config, self.database, self.clock = Path(config), Path(database), clock
        load_config(self.config)
        prepare_database(self.database, clock=clock)
        if rag is None:
            from .rag import GroundedRAG
            rag = GroundedRAG()
        self.rag = rag

    def authenticate(self, authorization: str) -> tuple[dict, dict]:
        try:
            config = load_config(self.config)
        except (OSError, ValueError, TypeError, KeyError):
            raise ServiceError(503, "auth_config_unavailable") from None
        if not authorization.startswith("Bearer "):
            raise ServiceError(401, "unauthorized")
        token = authorization[7:]
        if not token.isascii():
            raise ServiceError(401, "unauthorized")
        for principal in config["principals"]:
            if hmac.compare_digest(token, principal["token"]):
                return principal, config
        raise ServiceError(401, "unauthorized")

    def dispatch(self, method: str, path: str, body: dict, principal: dict, config: dict) -> tuple[int, dict]:
        tenant, actor, role = (principal[k] for k in ("tenant", "actor", "role"))
        # Query is read-only with respect to tickets, but persists its evidence packet.
        if method in {"POST", "PATCH"} and not config["write_enabled"]:
            raise ServiceError(503, "writes_disabled")
        if path == "/v1/query" and method == "POST":
            fields(body, {"question"})
            if role != "requester":
                raise ServiceError(403, "role_forbidden")
            question = body["question"]
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
                raise ServiceError(400, "invalid_question")
            try:
                question.encode("utf-8")
            except UnicodeEncodeError:
                raise ServiceError(400, "invalid_question") from None
            try:
                result = self.rag.answer(question, tenant=tenant)
                if not isinstance(result, dict) or type(result.get("abstained")) is not bool:
                    raise ValueError("invalid_dependency_result")
                if (not result["abstained"] and (not isinstance(result.get("answer"), str)
                        or not result["answer"] or not result.get("citations")
                        or result.get("verification", {}).get("publishable") is not True)):
                    raise ValueError("invalid_dependency_result")
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode()) > 65536:
                    raise ValueError("dependency_result_too_large")
            except Exception:
                raise ServiceError(503, "retrieval_unavailable") from None
            query_id = "query-" + uuid.uuid4().hex
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("INSERT INTO service_queries VALUES (?,?,?,?)", (query_id, tenant, actor, encoded))
            return 200, {**result, "query_id": query_id}
        with TicketWorkflow(self.database, clock=self.clock, outbox=True) as workflow:
            if path == "/v1/requests" and method == "POST":
                fields(body, {"query_id", "request_id", "subject", "priority"})
                if role != "requester":
                    raise ServiceError(403, "role_forbidden")
                identifier(body["query_id"])
                row = workflow.db.execute("SELECT result FROM service_queries WHERE id=? AND tenant=? AND actor=?",
                                          (body["query_id"], tenant, actor)).fetchone()
                if row is None:
                    raise ServiceError(404, "query_not_found")
                result = json.loads(row["result"])
                if result["abstained"]:
                    raise ServiceError(422, "evidence_required")
                citations = "\n".join(f"[{c['id']}@{c['revision']}] {c['quote']}" for c in result["citations"])
                payload = {"subject": body["subject"], "priority": body["priority"],
                           "body": result["answer"] + "\n\n证据：\n" + citations}
                workflow.create(tenant, body["request_id"], payload, creator=actor)
                return 201, workflow.read(tenant, body["request_id"])
            if path == "/v1/metrics" and method == "GET":
                params = (tenant,) if role == "reviewer" else (tenant, actor)
                where = "tenant=?" + ("" if role == "reviewer" else " AND actor=?")
                events = workflow.db.execute(f"SELECT status,code,latency_ms FROM service_events WHERE {where}", params).fetchall()
                request_where = "tenant=?" + ("" if role == "reviewer" else " AND creator=?")
                states = workflow.db.execute(f"SELECT state,COUNT(*) FROM requests WHERE {request_where} GROUP BY state", params)
                return 200, {"scope": "tenant" if role == "reviewer" else "own",
                    "requests": dict(states), "http_status_counts": dict(Counter(str(r[0]) for r in events)),
                    "error_code_counts": dict(Counter(r[1] for r in events if r[0] >= 400)),
                    "observed_requests": len(events),
                    "mean_latency_ms": round(sum(r[2] for r in events) / len(events), 3) if events else None}
            parts = path.strip("/").split("/")
            if len(parts) not in (3, 4) or parts[:2] != ["v1", "requests"]:
                raise ServiceError(404, "not_found")
            request_id = parts[2]
            view = workflow.read(tenant, request_id)
            if role == "requester" and view["creator"] != actor:
                raise ServiceError(404, "request_not_found")
            if len(parts) == 3 and method == "GET":
                return 200, view
            if len(parts) == 3 and method == "PATCH":
                fields(body, {"payload", "expected_version"})
                if role != "requester":
                    raise ServiceError(403, "role_forbidden")
                if type(body["expected_version"]) is not int:
                    raise ServiceError(400, "invalid_version")
                workflow.amend(tenant, request_id, body["payload"], expected_version=body["expected_version"])
                return 200, workflow.read(tenant, request_id)
            if len(parts) == 4 and parts[3] in {"approve", "commit"} and method == "POST":
                action = parts[3]
                required = {"expected_version", "expected_digest"}
                if action == "commit":
                    required.add("idempotency_key")
                fields(body, required, {"ttl_seconds"} if action == "approve" else set())
                if role != ("reviewer" if action == "approve" else "requester"):
                    raise ServiceError(403, "role_forbidden")
                if (type(body["expected_version"]) is not int or view["version"] != body["expected_version"]
                        or view["digest"] != body["expected_digest"]):
                    raise ServiceError(409, "stale_review")
                if action == "approve":
                    workflow.approve(tenant, request_id, reviewer=actor, ttl_seconds=body.get("ttl_seconds", 300),
                        expected_version=body["expected_version"], expected_digest=body["expected_digest"])
                    return 200, workflow.read(tenant, request_id)
                row = workflow._request(tenant, request_id)
                result = workflow.execute(tenant, request_id, approval_token=row["approval_token"],
                                          idempotency_key=body["idempotency_key"], expected_version=body["expected_version"],
                                          expected_digest=body["expected_digest"])
                outbox = workflow.db.execute("SELECT state FROM outbox WHERE tenant=? AND request_id=?", (tenant, request_id)).fetchone()
                return 200, {**result, "delivery_state": outbox[0]}
            raise ServiceError(405, "method_not_allowed")

    def trace(self, trace_id: str, principal: dict, route: str, status: int, code: str, elapsed: float) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO service_events VALUES (?,?,?,?,?,?,?)", (trace_id, principal["tenant"],
                principal["actor"], route, status, code, round(elapsed * 1000, 3)))


def make_server(app: Application, *, host: str = "127.0.0.1", port: int = 8765) -> HTTPServer:
    if host != "127.0.0.1":
        raise ValueError("only_loopback_supported")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentLearningLab"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass  # Never log credentials, request paths or user content.

        def handle_request(self):
            started, trace_id, principal = time.monotonic(), uuid.uuid4().hex, None
            status, result = 500, {"error": "internal_error"}
            route = "/v1/requests/:id" if self.path.startswith("/v1/requests/") else self.path
            if route not in {"/v1/query", "/v1/requests", "/v1/requests/:id", "/v1/metrics", "/health"}:
                route = "unknown"
            try:
                host = self.headers.get("Host", "")
                if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                    raise ServiceError(400, "invalid_host")
                if "?" in self.path or "#" in self.path:
                    raise ServiceError(400, "query_parameters_not_supported")
                if self.path == "/health" and self.command == "GET":
                    status, result = 200, {"status": "ok"}
                else:
                    auth_values = self.headers.get_all("Authorization", [])
                    if len(auth_values) != 1:
                        raise ServiceError(401, "unauthorized")
                    principal, config = app.authenticate(auth_values[0])
                    body = {}
                    if self.headers.get("Transfer-Encoding"):
                        raise ServiceError(400, "transfer_encoding_not_supported")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) > 1:
                        raise ServiceError(400, "invalid_content_length")
                    if self.command in {"POST", "PATCH"}:
                        if not lengths:
                            raise ServiceError(411, "content_length_required")
                        try:
                            length = int(lengths[0])
                        except ValueError:
                            raise ServiceError(400, "invalid_content_length") from None
                        if not 0 < length <= MAX_BODY:
                            raise ServiceError(413, "body_too_large")
                        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                            raise ServiceError(415, "json_required")
                        try:
                            raw = self.rfile.read(length)
                            if len(raw) != length:
                                raise ValueError("incomplete_body")
                            body = strict_json(raw)
                        except (ValueError, UnicodeError):
                            raise ServiceError(400, "invalid_json") from None
                    status, result = app.dispatch(self.command, self.path, body, principal, config)
            except ServiceError as exc:
                status, result = exc.status, {"error": exc.code}
            except WorkflowError as exc:
                code = str(exc)
                status = 404 if code == "request_not_found" else (400 if code.startswith("invalid_") else 409)
                result = {"error": code}
            except TimeoutError:
                status, result = 408, {"error": "request_timeout"}
            except (sqlite3.Error, OSError):
                status, result = 503, {"error": "storage_unavailable"}
            except Exception:
                status, result = 500, {"error": "internal_error"}
            try:
                encoded = json.dumps({**result, "trace_id": trace_id}, ensure_ascii=False, allow_nan=False).encode()
            except (ValueError, UnicodeError, TypeError):
                status, result = 500, {"error": "invalid_response"}
                encoded = json.dumps({**result, "trace_id": trace_id}).encode()
            if principal:
                try:
                    app.trace(trace_id, principal, route, status, result.get("error", "ok"), time.monotonic() - started)
                except sqlite3.Error:
                    pass  # Telemetry is not the business transaction or delivery receipt.
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        do_GET = handle_request
        do_POST = handle_request
        do_PATCH = handle_request
        do_DELETE = handle_request

    return HTTPServer((host, port), Handler)


def main(argv=None) -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("--config", type=Path, default=Path("work/service-auth.json"))
    serve = sub.add_parser("serve")
    recover = sub.add_parser("recover")
    for command in (serve, recover):
        command.add_argument("--config", type=Path, default=Path("work/service-auth.json"))
        command.add_argument("--database", type=Path, default=Path("work/service.sqlite3"))
    serve.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--mode", choices=["extractive", "local"], default="extractive")
    serve.add_argument("--model")
    recover.add_argument("--downstream", type=Path, default=Path("work/downstream.sqlite3"))
    recover.add_argument("--crash-after-effect", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            init_config(args.config)
            print(json.dumps({"status": "credentials_created", "secrets_printed": False}))
        elif args.command == "recover":
            if not load_config(args.config)["write_enabled"]:
                raise ValueError("writes_disabled")
            if not args.database.is_file():
                raise ValueError("database_not_found")
            print(json.dumps(deliver_outbox(args.database, args.downstream, crash_after_effect=args.crash_after_effect)))
        else:
            from .rag import GroundedRAG
            app = Application(args.config, args.database, rag=GroundedRAG(mode=args.mode, model=args.model))
            server = make_server(app, host=args.host, port=args.port)
            print(json.dumps({"status": "listening", "host": args.host, "port": server.server_port,
                              "mode": args.mode}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        return 0
    except Exception:
        # No config contents, file paths, raw dependency exceptions or tokens in CLI output.
        print(json.dumps({"error": "operation_failed", "command": args.command}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
