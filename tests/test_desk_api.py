"""HTTP contract checks with real stores, actors and source lifecycle."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

HAS_API = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
if HAS_API:
    from fastapi.testclient import TestClient
    from agentlab.desk.api import create_app
from agentlab.service import init_config, load_config


@unittest.skipUnless(HAS_API, "install .[desk,desk-test] for API tests")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        init_config(self.root / "auth.json")
        self.tokens = {p["name"]: p["token"] for p in load_config(self.root / "auth.json")["principals"]}
        self.app = create_app(data_dir=self.root, workers=0, capacity=2)
        self.app.state.knowledge.seed_demo("campus")
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def call(self, method, path, body=None, role="campus-requester"):
        return self.client.request(method, path, json=body,
            headers={"Authorization": "Bearer " + self.tokens[role]})

    def create(self, key="one"):
        response = self.call("POST", "/api/runs", {"question": "CASE-1001 退款审核和到账需要多久？", "idempotency_key": key})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def draft(self):
        run = self.create()
        self.app.state.engine.run_once()
        result = self.call("GET", "/api/runs/" + run["id"]).json()
        self.assertEqual(result["state"], "awaiting_review", result)
        return result

    def test_roles_tenants_and_server_owned_identity(self):
        run = self.create()
        path = "/api/runs/" + run["id"]
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.call("GET", path, role="partner-reviewer").status_code, 404)
        self.assertEqual(self.call("GET", path + "/events", role="partner-requester").status_code, 404)
        self.assertEqual(self.call("POST", path + "/cancel", role="campus-reviewer").status_code, 403)
        self.assertEqual(self.call("POST", "/api/runs", {"question": "x", "idempotency_key": "two", "tenant": "partner"}).status_code, 422)
        self.assertEqual(self.call("GET", "/api/runs", role="partner-reviewer").json()["runs"], [])

    def test_create_replay_and_capacity_include_human_waiting(self):
        first = self.create()
        self.app.state.engine.run_once()
        self.assertEqual(self.create()["id"], first["id"])
        self.create("two")
        denied = self.call("POST", "/api/runs", {"question": "x", "idempotency_key": "three"})
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(self.call("POST", "/api/runs", {"question": "changed", "idempotency_key": "one"}).status_code, 409)

    def test_rewrite_review_commit_replay_and_revocation(self):
        run = self.draft(); path = "/api/runs/" + run["id"]
        operation = {"expected_version": run["version"], "idempotency_key": "commit"}
        self.assertEqual(self.call("POST", path + "/commit", operation).status_code, 409)
        review = {"expected_version": run["version"], "answer": "测试审核人修订后的答复。", "citations": run["citations"], "decision": "approve"}
        self.assertEqual(self.call("POST", path + "/review", review).status_code, 403)
        approved = self.call("POST", path + "/review", review, "campus-reviewer")
        self.assertEqual(approved.status_code, 200, approved.text)
        operation["expected_version"] = approved.json()["version"]
        committed = self.call("POST", path + "/commit", operation)
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(self.call("GET", path).json()["answer"], review["answer"])
        self.assertEqual(self.call("POST", path + "/commit", operation).json()["ticket_id"], committed.json()["ticket_id"])
        config = load_config(self.root / "auth.json")
        config["principals"] = [p for p in config["principals"] if p["name"] != "campus-requester"]
        (self.root / "auth.json").write_text(json.dumps(config))
        self.assertEqual(self.call("GET", path).status_code, 401)

    def test_withdrawn_source_blocks_approval_but_allows_rejection(self):
        run = self.draft(); path = "/api/runs/" + run["id"]
        evidence = self.call("GET", "/api/evidence/" + run["citations"][0]["id"]).json()
        self.assertEqual(self.call("GET", "/api/evidence/" + evidence["id"], role="partner-requester").status_code, 404)
        withdrawn = self.call("POST", "/api/documents/" + evidence["document_id"] + "/withdraw",
            {"expected_revision": evidence["revision"]}, "campus-reviewer")
        self.assertEqual(withdrawn.status_code, 200)
        review = {"expected_version": run["version"], "answer": run["answer"], "citations": run["citations"], "decision": "approve"}
        self.assertEqual(self.call("POST", path + "/review", review, "campus-reviewer").status_code, 409)
        review["decision"] = "reject"
        self.assertEqual(self.call("POST", path + "/review", review, "campus-reviewer").status_code, 200)

    def test_strict_json_body_and_error_redaction(self):
        headers = {"Authorization": "Bearer " + self.tokens["campus-requester"], "Content-Type": "application/json"}
        for raw in ('{"question":"x","question":"y","idempotency_key":"x"}', '{"question":NaN}', '{'):
            self.assertEqual(self.client.post("/api/runs", content=raw, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/runs", content="x" * 160001, headers=headers).status_code, 413)
        response = self.call("POST", "/api/runs", {"question": {"private": "redaction-marker"}, "idempotency_key": "x"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("redaction-marker", response.text)
        self.assertEqual(self.call("POST", "/api/runs", {"question": "x", "mode": "local", "idempotency_key": "x"}).status_code, 409)

    def test_browser_boundaries_and_pause_writes(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("frame-ancestors 'none'", home.headers["content-security-policy"])
        self.assertEqual(home.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/assets/auth.json").status_code, 404)
        self.assertEqual(self.client.get("/api/me", headers={"Origin": "https://attacker.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/health", headers={"Host": "attacker.invalid"}).status_code, 400)
        config = load_config(self.root / "auth.json"); config["write_enabled"] = False
        (self.root / "auth.json").write_text(json.dumps(config))
        self.assertEqual(self.call("POST", "/api/runs", {"question": "x", "idempotency_key": "x"}).status_code, 503)
        self.assertEqual(self.call("GET", "/api/me").status_code, 200)

    def test_health_remains_responsive_during_slow_worker(self):
        entered, release = threading.Event(), threading.Event()
        class SlowEngine:
            def run_once(self):
                entered.set(); release.wait(2); return False
        app = create_app(data_dir=self.root, workers=1, engine=SlowEngine())
        with TestClient(app) as client:
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertLess(time.monotonic() - start, .75)
            release.set()


if __name__ == "__main__":
    unittest.main()
