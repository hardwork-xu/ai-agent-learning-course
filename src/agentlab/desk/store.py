"""Transactional task state, fencing leases and a replayable local outbox.

This is an application persistence layer, not an authentication boundary. The API
maps authenticated principals to tenant/actor and restricts reviewer operations.
SQLite transactions are short; model and tool work never runs under a write lock.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


class DeskError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def string(value, name="text", maximum=4000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise DeskError("invalid_" + name)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise DeskError("invalid_" + name) from None
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise DeskError("invalid_identifier")
    return value


def citations_valid(citations):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 12:
        raise DeskError("invalid_citations")
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {"id", "revision", "quote"}:
            raise DeskError("invalid_citations")
        for key in ("id", "revision"):
            string(citation[key], "citations", 200)
        string(citation["quote"], "citations", 3000)
    if len({encoded(c) for c in citations}) != len(citations):
        raise DeskError("duplicate_citation")


class Store:
    def __init__(self, path, *, capacity=16, clock=time.time, approval_seconds=600):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise DeskError("invalid_capacity")
        if not isinstance(approval_seconds, (int, float)) or not 1 <= approval_seconds <= 86400:
            raise DeskError("invalid_approval_seconds")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.capacity, self.clock, self.approval_seconds = capacity, clock, approval_seconds
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise DeskError("unsupported_schema", 503)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
                question TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, answer TEXT, citations TEXT NOT NULL DEFAULT '[]',
                clarification TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                ticket_id TEXT, reviewer TEXT, approval_digest TEXT, approval_expires REAL,
                observations TEXT NOT NULL DEFAULT '[]', pending TEXT, steps INTEGER NOT NULL DEFAULT 0,
                lease_until REAL, fence INTEGER NOT NULL DEFAULT 0, deadline REAL, started_at REAL,
                create_key TEXT NOT NULL, create_digest TEXT NOT NULL,
                UNIQUE(tenant, actor, create_key));
            CREATE INDEX IF NOT EXISTS runs_queue ON runs(state, lease_until, created_at);
            CREATE INDEX IF NOT EXISTS runs_tenant ON runs(tenant, actor, created_at);
            CREATE TABLE IF NOT EXISTS events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                created_at REAL NOT NULL, phase TEXT NOT NULL, details TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id));
            CREATE TABLE IF NOT EXISTS planner_attempts(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fence INTEGER NOT NULL,
                started_at REAL NOT NULL, ended_at REAL, usage TEXT, latency_ms REAL, error TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(id));
            CREATE INDEX IF NOT EXISTS attempts_run ON planner_attempts(run_id);
            CREATE TABLE IF NOT EXISTS tickets(
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(
                tenant TEXT NOT NULL, actor TEXT NOT NULL, operation_key TEXT NOT NULL,
                payload_digest TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(tenant, actor, operation_key));
            CREATE TABLE IF NOT EXISTS outbox(
                event_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, digest TEXT NOT NULL, delivered_at REAL);
            PRAGMA user_version=1;
            """)

    @contextmanager
    def _db(self, *, write=False):
        db = sqlite3.connect(str(self.path), isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _row(self, db, tenant, run_id, actor=None):
        identifier(tenant); identifier(run_id)
        if actor is not None:
            identifier(actor)
        row = db.execute("SELECT * FROM runs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
        if row is None or (actor is not None and actor != row["actor"]):
            raise DeskError("run_not_found", 404)
        return row

    @staticmethod
    def _public(row):
        keys = ("id", "tenant", "actor", "question", "mode", "state", "version", "answer",
                "clarification", "error", "created_at", "updated_at", "ticket_id", "reviewer", "approval_expires")
        result = {key: row[key] for key in keys}
        result["citations"] = json.loads(row["citations"])
        result["observations"] = json.loads(row["observations"])
        result["steps"] = row["steps"]
        return result

    def _event(self, db, run_id, phase, **details):
        db.execute("INSERT INTO events(run_id,created_at,phase,details) VALUES(?,?,?,?)",
                   (run_id, self.clock(), phase, encoded(details)))

    def create(self, tenant, actor, question, idempotency_key, mode="baseline"):
        identifier(tenant); identifier(actor); identifier(idempotency_key)
        string(question, "question")
        if mode not in {"baseline", "local"}:
            raise DeskError("invalid_mode")
        request_digest = digest({"question": question, "mode": mode})
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE tenant=? AND actor=? AND create_key=?",
                             (tenant, actor, idempotency_key)).fetchone()
            if row is not None:
                if row["create_digest"] != request_digest:
                    raise DeskError("idempotency_conflict", 409)
                return self._public(row)
            # Global bound includes human waiting states: waiting work also costs storage and attention.
            count = db.execute("SELECT COUNT(*) FROM runs WHERE state IN ('queued','running','needs_input','awaiting_review','approved')").fetchone()[0]
            if count >= self.capacity:
                raise DeskError("capacity_exceeded", 429)
            now, run_id = self.clock(), uuid.uuid4().hex
            db.execute("INSERT INTO runs(id,tenant,actor,question,mode,state,created_at,updated_at,create_key,create_digest) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                       (run_id, tenant, actor, question, mode, now, now, idempotency_key, request_digest))
            self._event(db, run_id, "submitted", mode=mode, state="queued")
            return self._public(self._row(db, tenant, run_id))

    def get(self, tenant, run_id, actor=None):
        with self._db() as db:
            return self._public(self._row(db, tenant, run_id, actor))

    def list_runs(self, tenant, actor=None, limit=50):
        identifier(tenant)
        if actor is not None:
            identifier(actor)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise DeskError("invalid_limit")
        query, args = "SELECT * FROM runs WHERE tenant=?", [tenant]
        if actor is not None:
            query += " AND actor=?"; args.append(actor)
        with self._db() as db:
            return [self._public(r) for r in db.execute(query + " ORDER BY created_at DESC,id LIMIT ?", (*args, limit))]

    def events(self, tenant, run_id, actor=None):
        with self._db() as db:
            self._row(db, tenant, run_id, actor)
            rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence LIMIT 500", (run_id,)).fetchall()
            return [{"id": r["sequence"], "run_id": run_id, "timestamp": r["created_at"], "phase": r["phase"], **json.loads(r["details"])} for r in rows]

    def cancel(self, tenant, run_id, actor):
        with self._db(write=True) as db:
            row = self._row(db, tenant, run_id, actor)
            if row["state"] == "committed":
                raise DeskError("already_committed", 409)
            if row["state"] == "cancelled":
                return self._public(row)
            db.execute("UPDATE runs SET state='cancelled',version=version+1,fence=fence+1,lease_until=NULL,pending=NULL,approval_digest=NULL,approval_expires=NULL,updated_at=? WHERE id=?", (self.clock(), run_id))
            self._event(db, run_id, "cancelled", state="cancelled")
            return self._public(self._row(db, tenant, run_id))

    @staticmethod
    def _version(row, expected):
        if type(expected) is not int or expected != row["version"]:
            raise DeskError("version_conflict", 409)

    def reply(self, tenant, run_id, actor, text, expected_version):
        string(text, "reply", 2000)
        with self._db(write=True) as db:
            row = self._row(db, tenant, run_id, actor)
            self._version(row, expected_version)
            if row["state"] != "needs_input":
                raise DeskError("not_waiting_for_input", 409)
            observations = json.loads(row["observations"])
            observations.append({"type": "user_reply", "question": row["clarification"], "text": text})
            db.execute("UPDATE runs SET observations=?,state='queued',clarification=NULL,deadline=NULL,version=version+1,updated_at=? WHERE id=?", (encoded(observations), self.clock(), run_id))
            self._event(db, run_id, "input_received", state="queued")
            return self._public(self._row(db, tenant, run_id))

    def review(self, tenant, run_id, reviewer, expected_version, answer, citations, decision, comment=""):
        identifier(reviewer); string(answer, "answer", 6000); string(comment, "comment", 1000, empty=True)
        citations_valid(citations)
        if decision not in {"approve", "reject"}:
            raise DeskError("invalid_review_decision")
        with self._db(write=True) as db:
            row = self._row(db, tenant, run_id)
            self._version(row, expected_version)
            if row["actor"] == reviewer:
                raise DeskError("self_review_forbidden", 403)
            if row["state"] not in {"awaiting_review", "approved", "rejected"}:
                raise DeskError("not_reviewable", 409)
            if row["state"] == "rejected" and decision == "approve":
                active = db.execute("SELECT COUNT(*) FROM runs WHERE state IN ('queued','running','needs_input','awaiting_review','approved')").fetchone()[0]
                if active >= self.capacity:
                    raise DeskError("capacity_exceeded", 429)
            state = "approved" if decision == "approve" else "rejected"
            expires = self.clock() + self.approval_seconds if decision == "approve" else None
            approval_digest = digest({"answer": answer, "citations": citations}) if expires else None
            db.execute("UPDATE runs SET state=?,answer=?,citations=?,version=version+1,reviewer=?,approval_digest=?,approval_expires=?,updated_at=? WHERE id=?",
                       (state, answer, encoded(citations), reviewer, approval_digest, expires, self.clock(), run_id))
            self._event(db, run_id, "reviewed", state=state, reviewer=reviewer, comment=comment,
                        content_digest=digest({"answer": answer, "citations": citations}), reviewed_version=expected_version)
            return self._public(self._row(db, tenant, run_id))

    def commit(self, tenant, run_id, actor, expected_version, idempotency_key):
        identifier(idempotency_key)
        request_digest = digest({"run_id": run_id, "expected_version": expected_version})
        with self._db(write=True) as db:
            row = self._row(db, tenant, run_id, actor)
            previous = db.execute("SELECT * FROM operations WHERE tenant=? AND actor=? AND operation_key=?", (tenant, actor, idempotency_key)).fetchone()
            if previous:
                if previous["payload_digest"] != request_digest:
                    raise DeskError("idempotency_conflict", 409)
                return json.loads(previous["result"])
            self._version(row, expected_version)
            if row["state"] != "approved":
                raise DeskError("approval_required", 409)
            if row["approval_expires"] is None or self.clock() >= row["approval_expires"]:
                raise DeskError("approval_expired", 409)
            content = {"answer": row["answer"], "citations": json.loads(row["citations"])}
            if digest(content) != row["approval_digest"]:
                raise DeskError("approval_content_changed", 409)
            ticket_id, event_id = uuid.uuid4().hex, uuid.uuid4().hex
            payload = {"ticket_id": ticket_id, "tenant": tenant, "run_id": run_id,
                       "question": row["question"], **content, "reviewer": row["reviewer"],
                       "approval_version": expected_version}
            payload_json, content_digest = encoded(payload), digest(payload)
            db.execute("INSERT INTO tickets VALUES(?,?,?,?,?)", (ticket_id, tenant, run_id, payload_json, content_digest))
            db.execute("INSERT INTO outbox(event_id,tenant,run_id,payload,digest) VALUES(?,?,?,?,?)", (event_id, tenant, run_id, payload_json, content_digest))
            db.execute("UPDATE runs SET state='committed',ticket_id=?,version=version+1,updated_at=? WHERE id=?", (ticket_id, self.clock(), run_id))
            result = {"run_id": run_id, "ticket_id": ticket_id, "event_id": event_id, "state": "committed", "version": expected_version + 1}
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?)", (tenant, actor, idempotency_key, request_digest, encoded(result)))
            self._event(db, run_id, "committed", state="committed", ticket_id=ticket_id, event_id=event_id)
            return result

    # Internal worker API. A stale fence cannot write even if its former lease has expired.
    def claim(self, *, lease_seconds=95, execution_seconds=90):
        if not 0 < execution_seconds <= 300 or not 0 < lease_seconds <= 360:
            raise DeskError("invalid_worker_budget")
        with self._db(write=True) as db:
            now = self.clock()
            row = db.execute("SELECT * FROM runs WHERE state='queued' OR (state='running' AND lease_until<=?) ORDER BY created_at,id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            recovered = row["state"] == "running"
            deadline = row["deadline"] if row["deadline"] is not None else now + execution_seconds
            db.execute("UPDATE runs SET state='running',fence=fence+1,lease_until=?,deadline=?,started_at=COALESCE(started_at,?),updated_at=? WHERE id=?", (now + lease_seconds, deadline, now, now, row["id"]))
            self._event(db, row["id"], "recovered" if recovered else "claimed", state="running", fence=row["fence"] + 1)
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())

    def _lease(self, db, run_id, fence):
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["state"] != "running" or row["fence"] != fence or row["lease_until"] <= self.clock():
            raise DeskError("lease_lost", 409)
        return row

    def renew(self, run_id, fence, *, lease_seconds=15):
        if not 0 < lease_seconds <= 360:
            raise DeskError("invalid_worker_budget")
        with self._db(write=True) as db:
            row = self._lease(db, run_id, fence)
            if self.clock() >= row["deadline"]:
                raise DeskError("deadline_exceeded", 408)
            db.execute("UPDATE runs SET lease_until=? WHERE id=?", (self.clock() + lease_seconds, run_id))

    def current(self, run_id, fence):
        with self._db() as db:
            return dict(self._lease(db, run_id, fence))

    def begin_attempt(self, run_id, fence):
        with self._db(write=True) as db:
            row = self._lease(db, run_id, fence)
            if self.clock() >= row["deadline"]:
                raise DeskError("deadline_exceeded", 408)
            attempt_id = uuid.uuid4().hex
            db.execute("INSERT INTO planner_attempts(id,run_id,fence,started_at) VALUES(?,?,?,?)",
                       (attempt_id, run_id, fence, self.clock()))
            self._event(db, run_id, "planner_attempt_started", attempt_id=attempt_id, fence=fence)
            return attempt_id

    def end_attempt(self, attempt_id, *, usage=None, latency_ms=None, error=None):
        # Accounting is deliberately independent of the current execution fence.
        # A cancelled/reclaimed worker may account for spent tokens, but cannot
        # change state, observations, approval or effects through this method.
        safe_usage = {"prompt_eval_count": None, "eval_count": None}
        if isinstance(usage, dict):
            for key in ("prompt_eval_count", "eval_count", "prompt_eval_cached_count", "total_duration",
                        "load_duration", "prompt_eval_duration", "eval_duration", "max_output_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    safe_usage[key] = value
            for key in ("requested_model", "reported_model", "server_version", "model_digest"):
                value = usage.get(key)
                if isinstance(value, str) and len(value) <= 160:
                    safe_usage[key] = value
            if isinstance(usage.get("timeout_seconds"), (int, float)) and math.isfinite(usage["timeout_seconds"]):
                safe_usage["timeout_seconds"] = usage["timeout_seconds"]
        if not isinstance(latency_ms, (int, float)) or not math.isfinite(latency_ms) or latency_ms < 0:
            latency_ms = None
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", error)):
            error = "planner_error"
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM planner_attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise DeskError("attempt_not_found", 404)
            if row["ended_at"] is not None:
                if row["usage"] != encoded(safe_usage) or row["error"] != error:
                    raise DeskError("attempt_conflict", 409)
                return
            db.execute("UPDATE planner_attempts SET ended_at=?,usage=?,latency_ms=?,error=? WHERE id=?",
                       (self.clock(), encoded(safe_usage), latency_ms, error, attempt_id))
            self._event(db, row["run_id"], "planner_attempt_finished", attempt_id=attempt_id,
                        fence=row["fence"], usage=safe_usage, latency_ms=latency_ms, error=error)

    def save_decision(self, run_id, fence, decision, *, latency_ms=None, usage=None):
        with self._db(write=True) as db:
            row = self._lease(db, run_id, fence)
            if self.clock() >= row["deadline"]:
                raise DeskError("deadline_exceeded", 408)
            if row["pending"] is not None:
                raise DeskError("decision_already_pending", 409)
            db.execute("UPDATE runs SET pending=?,steps=steps+1,updated_at=? WHERE id=?", (encoded(decision), self.clock(), run_id))
            self._event(db, run_id, "decision", step=row["steps"] + 1, decision_type=decision["type"],
                        tool=decision.get("name"), latency_ms=latency_ms, usage=usage)

    def save_observation(self, run_id, fence, observation, *, latency_ms=None):
        with self._db(write=True) as db:
            row = self._lease(db, run_id, fence)
            if self.clock() >= row["deadline"]:
                raise DeskError("deadline_exceeded", 408)
            if row["pending"] is None:
                raise DeskError("no_pending_decision", 409)
            observations = json.loads(row["observations"]); observations.append(observation)
            db.execute("UPDATE runs SET observations=?,pending=NULL,updated_at=? WHERE id=?", (encoded(observations), self.clock(), run_id))
            self._event(db, run_id, "observation", step=row["steps"], tool=observation["name"],
                        latency_ms=latency_ms, result_count=len(observation["result"]) if isinstance(observation["result"], list) else None)

    def finish(self, run_id, fence, *, state, answer=None, citations=None, clarification=None, error=None, usage=None, latency_ms=None):
        if state not in {"awaiting_review", "needs_input", "abstained", "failed"}:
            raise DeskError("invalid_worker_state")
        with self._db(write=True) as db:
            row = self._lease(db, run_id, fence)
            if state != "failed" and self.clock() >= row["deadline"]:
                raise DeskError("deadline_exceeded", 408)
            db.execute("UPDATE runs SET state=?,answer=?,citations=?,clarification=?,error=?,pending=NULL,lease_until=NULL,version=version+1,updated_at=? WHERE id=?",
                       (state, answer, encoded(citations or []), clarification, error, self.clock(), run_id))
            self._event(db, run_id, "completed", state=state, error=error, usage=usage, latency_ms=latency_ms)
            return self._public(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def metrics(self, tenant, actor=None):
        identifier(tenant)
        if actor is not None:
            identifier(actor)
        where, args = "tenant=?", [tenant]
        if actor is not None:
            where += " AND actor=?"; args.append(actor)
        with self._db() as db:
            states = {r[0]: r[1] for r in db.execute("SELECT state,COUNT(*) FROM runs WHERE " + where + " GROUP BY state", args)}
            recent = db.execute("SELECT * FROM runs WHERE " + where + " ORDER BY created_at DESC LIMIT 1000", args).fetchall()
            ids = [r["id"] for r in recent]
            records, attempts = [], []
            if ids:
                records = [{**json.loads(r["details"]), "_run_id": r["run_id"], "_created_at": r["created_at"]} for r in db.execute("SELECT run_id,created_at,details FROM events WHERE run_id IN (" + ",".join("?" for _ in ids) + ")", ids)]
                attempts = [json.loads(r["usage"]) if r["usage"] else {"prompt_eval_count": None, "eval_count": None}
                            for r in db.execute("SELECT usage FROM planner_attempts WHERE run_id IN (" + ",".join("?" for _ in ids) + ")", ids)]
        starts = {r["id"]: r["started_at"] for r in recent if r["started_at"] is not None}
        # Use the persisted investigation completion timestamp, not updated_at:
        # a later approval/commit must neither erase nor inflate the sample.
        latencies = sorted(round((r["_created_at"]-starts[r["_run_id"]])*1000, 3)
                           for r in records if r.get("state") in {"awaiting_review", "abstained", "failed"}
                           and r["_run_id"] in starts)
        usage = attempts
        def metric(key):
            known = [u[key] for u in usage if type(u.get(key)) is int and u[key] >= 0]
            return {"known_sum": sum(known), "known_calls": len(known), "unknown_calls": len(usage)-len(known)}
        return {"states": states, "total": sum(states.values()), "sample_limit": 1000,
                "latency_ms": {"definition": "first_claim_to_investigation_completion_including_input_wait", "samples": len(latencies), "p50": latencies[math.ceil(len(latencies)*.5)-1] if latencies else None, "p95": latencies[math.ceil(len(latencies)*.95)-1] if latencies else None},
                "usage": {k: metric(k) for k in ("prompt_eval_count", "eval_count")},
                "errors": {code: sum(r["error"] == code for r in recent) for code in sorted({r["error"] for r in recent if r["error"]})}}

    def backup(self, destination):
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise DeskError("backup_same_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise DeskError("backup_exists", 409) from None
        os.close(fd)
        try:
            with self._db() as source:
                dest = sqlite3.connect(str(target))
                try:
                    source.backup(dest)
                    if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise DeskError("backup_integrity_error", 500)
                finally:
                    dest.close()
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return {"status": "saved", "schema_version": 1}

    def deliver(self, downstream_path, *, fail_after_effect=False):
        destination = Path(downstream_path)
        if destination.resolve() == self.path.resolve():
            raise DeskError("sink_same_path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            pending = db.execute("SELECT * FROM outbox WHERE delivered_at IS NULL ORDER BY rowid LIMIT 100").fetchall()
        delivered = 0
        for event in pending:
            if digest(json.loads(event["payload"])) != event["digest"]:
                raise DeskError("outbox_integrity_error", 500)
            sink = sqlite3.connect(str(destination), isolation_level=None, timeout=5)
            try:
                sink.execute("CREATE TABLE IF NOT EXISTS deliveries(event_id TEXT PRIMARY KEY,tenant TEXT NOT NULL,run_id TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,UNIQUE(tenant,run_id))")
                sink.execute("BEGIN IMMEDIATE")
                previous = sink.execute("SELECT event_id,payload,digest FROM deliveries WHERE event_id=? OR (tenant=? AND run_id=?)", (event["event_id"], event["tenant"], event["run_id"])).fetchall()
                if previous:
                    if len(previous) != 1 or tuple(previous[0]) != (event["event_id"], event["payload"], event["digest"]):
                        raise DeskError("sink_idempotency_conflict", 409)
                else:
                    sink.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)", (event["event_id"], event["tenant"], event["run_id"], event["payload"], event["digest"]))
                sink.execute("COMMIT")
            finally:
                if sink.in_transaction:
                    sink.execute("ROLLBACK")
                sink.close()
            if fail_after_effect:
                raise DeskError("delivery_interrupted", 503)
            with self._db(write=True) as db:
                cursor = db.execute("UPDATE outbox SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL", (self.clock(), event["event_id"]))
                if cursor.rowcount:
                    self._event(db, event["run_id"], "delivered", event_id=event["event_id"])
                    delivered += 1
        return {"delivered": delivered, "examined": len(pending)}
