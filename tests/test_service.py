"""Real loopback HTTP plus durable SQLite tests; no model or external API calls."""
import http.client
from contextlib import closing
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest

from agentlab.service import Application, deliver_outbox, init_config, load_config, make_server
from agentlab.workflow import TicketWorkflow


class Evidence:
    def answer(self, question, *, tenant):
        return {"mode": "extractive", "answer": "退款审核期为七天。", "abstained": False,
                "citations": [{"id": "policy", "revision": "2", "quote": "退款审核期为七天。"}],
                "verification": {"publishable": True}, "usage": {}, "latency_ms": 0, "retrieval": []}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "auth.json"
        self.database = self.root / "service.sqlite3"
        self.downstream = self.root / "downstream.sqlite3"
        init_config(self.config)
        self.credentials = load_config(self.config)
        self.credentials["principals"].append({"name": "campus-other", "actor": "requester-2",
            "tenant": "campus", "role": "requester", "token": secrets.token_urlsafe(32)})
        self.save_config()
        self.tokens = {p["name"]: p["token"] for p in self.credentials["principals"]}
        self.now = [1000.0]
        self.app = Application(self.config, self.database, rag=Evidence(), clock=lambda: self.now[0])
        self.start()

    def start(self):
        self.server = make_server(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def save_config(self):
        self.config.write_text(json.dumps(self.credentials))
        self.config.chmod(0o600)

    def request(self, method, path, body=None, *, who="campus-requester", raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=7)
        request_headers = {"Content-Type": "application/json"}
        if who:
            request_headers["Authorization"] = "Bearer " + self.tokens.get(who, who)
        if headers:
            request_headers.update(headers)
        encoded = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def draft(self, request_id="request-1", who="campus-requester"):
        status, query = self.request("POST", "/v1/query", {"question": "退款审核期限？"}, who=who)
        self.assertEqual(status, 200, query)
        status, draft = self.request("POST", "/v1/requests", {"query_id": query["query_id"],
            "request_id": request_id, "subject": "合成退款咨询", "priority": "P2"}, who=who)
        self.assertEqual(status, 201, draft)
        return draft

    @staticmethod
    def binding(draft):
        return {"expected_version": draft["version"], "expected_digest": draft["digest"]}

    def approve(self, draft, ttl=60):
        return self.request("POST", f"/v1/requests/{draft['request_id']}/approve",
            {**self.binding(draft), "ttl_seconds": ttl}, who="campus-reviewer")

    def commit(self, draft, key="operation-1"):
        return self.request("POST", f"/v1/requests/{draft['request_id']}/commit",
            {**self.binding(draft), "idempotency_key": key})

    def test_real_http_end_to_end_and_restart(self):
        draft = self.draft()
        self.assertIn("七天", draft["payload"]["body"])
        self.assertEqual(self.approve(draft)[0], 200)
        status, committed = self.commit(draft)
        self.assertEqual(status, 200)
        self.assertEqual(committed["delivery_state"], "pending")
        self.stop()
        self.app = Application(self.config, self.database, rag=Evidence(), clock=lambda: self.now[0])
        self.start()
        self.now[0] += 1000
        replay = self.commit(draft)[1]
        self.assertEqual(committed["ticket_id"], replay["ticket_id"])
        self.assertEqual(deliver_outbox(self.database, self.downstream)["delivered"], 1)
        self.assertEqual(self.commit(draft)[1]["delivery_state"], "delivered")
        with TicketWorkflow(self.database) as workflow:
            self.assertEqual(workflow.ticket_count("campus"), 1)

    def test_auth_roles_tenant_owner_and_query_isolation(self):
        self.assertEqual(self.request("GET", "/health", who=None)[0], 200)
        for who in (None, "invalid-token"):
            self.assertEqual(self.request("POST", "/v1/query", {"question": "期限"}, who=who)[0], 401)
        draft = self.draft()
        for who in ("partner-requester", "partner-reviewer", "campus-other"):
            self.assertEqual(self.request("GET", "/v1/requests/request-1", who=who)[0], 404)
        self.assertEqual(self.request("POST", "/v1/requests/request-1/approve", self.binding(draft))[0], 403)
        self.assertEqual(self.request("POST", "/v1/requests/request-1/commit",
            {**self.binding(draft), "idempotency_key": "x"}, who="campus-reviewer")[0], 403)
        _, query = self.request("POST", "/v1/query", {"question": "期限"})
        self.assertEqual(self.request("POST", "/v1/requests", {"query_id": query["query_id"],
            "request_id": "stolen", "subject": "x", "priority": "P2"}, who="campus-other")[0], 404)

    def test_stale_review_and_expiry_and_amend_invalidation(self):
        draft = self.draft()
        self.assertEqual(self.commit(draft)[0], 409)
        changed = {**draft["payload"], "priority": "P1"}
        status, amended = self.request("PATCH", "/v1/requests/request-1",
            {"payload": changed, "expected_version": draft["version"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.approve(draft)[1]["error"], "stale_review")
        self.assertEqual(self.approve(amended, ttl=1)[0], 200)
        self.now[0] += 1
        self.assertEqual(self.commit(amended)[1]["error"], "approval_required_or_stale")
        self.assertEqual(self.approve(amended)[0], 200)
        self.assertEqual(self.request("PATCH", "/v1/requests/request-1", {"payload": changed,
            "expected_version": amended["version"]})[0], 200)
        self.assertEqual(self.commit(amended)[1]["error"], "stale_review")

    def test_exact_digest_and_boolean_version_are_rejected(self):
        draft = self.draft()
        for change in ({"expected_digest": "0" * 64}, {"expected_version": True}):
            status, result = self.request("POST", "/v1/requests/request-1/approve",
                {**self.binding(draft), **change}, who="campus-reviewer")
            self.assertEqual((status, result["error"]), (409, "stale_review"))

    def test_idempotency_conflict_and_replay(self):
        first = self.draft()
        self.approve(first)
        result = self.commit(first)[1]
        self.assertEqual(self.commit(first)[1]["ticket_id"], result["ticket_id"])
        other = self.draft("request-2")
        self.approve(other)
        self.assertEqual(self.commit(other)[1]["error"], "idempotency_conflict")
        self.assertEqual(self.commit(first, "new-key")[1]["error"], "already_committed_use_original_key")

    def test_revocation_and_write_switch_apply_without_restart(self):
        draft = self.draft()
        self.credentials["write_enabled"] = False
        self.save_config()
        self.assertEqual(self.approve(draft)[1]["error"], "writes_disabled")
        self.assertEqual(self.request("GET", "/v1/requests/request-1")[0], 200)
        self.credentials["principals"] = [p for p in self.credentials["principals"] if p["name"] != "campus-requester"]
        self.save_config()
        self.assertEqual(self.request("GET", "/v1/requests/request-1")[0], 401)
        self.config.write_text("broken")
        self.assertEqual(self.request("GET", "/v1/metrics", who="campus-reviewer")[0], 503)

    def test_injected_fields_duplicate_keys_body_boundaries_and_host(self):
        for key in ("tenant", "actor", "role", "state", "approval_token"):
            self.assertEqual(self.request("POST", "/v1/query", {"question": "期限", key: "injected"})[0], 400)
        self.assertEqual(self.request("POST", "/v1/query", raw=b'{"question":"a","question":"b"}')[0], 400)
        self.assertEqual(self.request("POST", "/v1/query", raw=b' ' * 16385)[0], 413)
        self.assertEqual(self.request("GET", "/health", headers={"Host": "attacker.invalid"})[0], 400)
        self.assertEqual(self.request("GET", "/v1/metrics?tenant=partner")[0], 400)

    def test_lone_surrogate_is_rejected_before_creating_or_amending(self):
        status, result = self.request("POST", "/v1/query", raw=b'{"question":"\\ud800"}')
        self.assertEqual((status, result["error"]), (400, "invalid_question"))
        _, query = self.request("POST", "/v1/query", {"question": "期限"})
        bad_create = {"query_id": query["query_id"], "request_id": "unreadable",
                      "subject": chr(0xD800), "priority": "P2"}
        status, result = self.request("POST", "/v1/requests", raw=json.dumps(bad_create).encode())
        self.assertEqual((status, result["error"]), (400, "invalid_payload"))
        self.assertEqual(self.request("GET", "/v1/requests/unreadable")[0], 404)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
        draft = self.draft()
        for field in ("subject", "body"):
            mutation = {"payload": {**draft["payload"], field: chr(0xDC00)}, "expected_version": draft["version"]}
            status, result = self.request("PATCH", "/v1/requests/request-1", raw=json.dumps(mutation).encode())
            self.assertEqual((status, result["error"]), (400, "invalid_payload"))
            _, unchanged = self.request("GET", "/v1/requests/request-1")
            self.assertEqual(unchanged["version"], draft["version"])
            self.assertEqual(unchanged["payload"], draft["payload"])
        self.assertEqual(self.request("GET", "/health", who=None)[0], 200)

    def test_incomplete_request_body_times_out(self):
        status, result = self.request("POST", "/v1/query", raw=b'{', headers={"Content-Length": "2"})
        self.assertEqual((status, result["error"]), (408, "request_timeout"))
        self.assertEqual(self.request("GET", "/health", who=None)[0], 200)

    def test_dependency_failure_abstention_and_safe_traces(self):
        class Broken:
            def answer(self, *_args, **_kwargs):
                raise RuntimeError("private prompt and fake secret")
        self.app.rag = Broken()
        status, result = self.request("POST", "/v1/query", {"question": "never log this"})
        self.assertEqual((status, result["error"]), (503, "retrieval_unavailable"))
        self.assertNotIn("private", json.dumps(result))
        class Abstaining(Evidence):
            def answer(self, *args, **kwargs):
                return {**super().answer(*args, **kwargs), "abstained": True}
        self.app.rag = Abstaining()
        _, query = self.request("POST", "/v1/query", {"question": "期限"})
        self.assertEqual(self.request("POST", "/v1/requests", {"query_id": query["query_id"],
            "request_id": "bad", "subject": "x", "priority": "P2"})[0], 422)
        with closing(sqlite3.connect(self.database)) as db, db:
            events = db.execute("SELECT * FROM service_events").fetchall()
        self.assertNotIn("never log this", str(events))
        self.assertNotIn(self.tokens["campus-requester"], str(events))
        _, metrics = self.request("GET", "/v1/metrics", who="campus-other")
        self.assertEqual(metrics["observed_requests"], 0)
        self.assertEqual(metrics["scope"], "own")

    def test_outbox_effect_receipt_crash_retry_is_one_downstream_effect(self):
        draft = self.draft()
        self.approve(draft)
        self.commit(draft)
        with self.assertRaisesRegex(RuntimeError, "simulated_crash_after_effect"):
            deliver_outbox(self.database, self.downstream, crash_after_effect=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM outbox").fetchone()[0], "pending")
        with closing(sqlite3.connect(self.downstream)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM received_tickets").fetchone()[0], 1)
        self.assertEqual(deliver_outbox(self.database, self.downstream)["delivered"], 1)
        self.assertEqual(deliver_outbox(self.database, self.downstream)["delivered"], 0)
        with closing(sqlite3.connect(self.downstream)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM received_tickets").fetchone()[0], 1)

    def test_downstream_rejects_same_event_with_different_digest(self):
        draft = self.draft()
        self.approve(draft)
        self.commit(draft)
        with self.assertRaises(RuntimeError):
            deliver_outbox(self.database, self.downstream, crash_after_effect=True)
        with closing(sqlite3.connect(self.downstream)) as db, db:
            db.execute("UPDATE received_tickets SET digest='changed'")
        with self.assertRaisesRegex(ValueError, "downstream_idempotency_conflict"):
            deliver_outbox(self.database, self.downstream)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM outbox").fetchone()[0], "pending")

    def test_local_ticket_operation_and_outbox_roll_back_together(self):
        draft = self.draft()
        self.approve(draft)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'fault'); END")
        self.assertEqual(self.commit(draft)[0], 503)
        with closing(sqlite3.connect(self.database)) as db, db:
            for table in ("tickets", "operations", "outbox"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.request("GET", "/v1/requests/request-1")[1]["state"], "approved")

    def test_config_safety_and_loopback_only(self):
        with self.assertRaises(FileExistsError):
            init_config(self.config)
        with self.assertRaisesRegex(ValueError, "only_loopback"):
            make_server(self.app, host="0.0.0.0")
        if __import__("os").name == "posix":
            self.config.chmod(0o644)
            self.assertEqual(self.request("GET", "/v1/metrics")[1]["error"], "auth_config_unavailable")


if __name__ == "__main__":
    unittest.main()
