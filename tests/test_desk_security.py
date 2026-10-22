"""Independent HTTP authorization and operator recovery contracts.

All identities and content are generated test fixtures. Tests do not invoke a
model, contact an external service, or infer human review quality.
"""
from contextlib import redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from agentlab.desk.api import create_app
except ImportError:
    TestClient = None

from agentlab.desk.__main__ import main
from agentlab.service import init_config


@unittest.skipIf(TestClient is None, "install the desk and desk-test extras")
class DeskSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.config = self.path / "auth.json"
        init_config(self.config)
        self.settings = json.loads(self.config.read_text())
        self.settings["principals"].append({
            "name": "campus-other", "tenant": "campus", "actor": "requester-2",
            "role": "requester", "token": secrets.token_urlsafe(32),
        })
        self.save_config()
        self.auth = {p["name"]: {"Authorization": "Bearer " + p["token"]}
                     for p in self.settings["principals"]}
        self.app = create_app(data_dir=self.path, workers=0)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def save_config(self):
        self.config.write_text(json.dumps(self.settings))
        if os.name == "posix":
            self.config.chmod(0o600)

    def request(self, method, url, actor="campus-requester", **kwargs):
        return self.client.request(method, url, headers=self.auth[actor], **kwargs)

    def create(self):
        response = self.request("POST", "/api/runs", json={
            "question": "Synthetic support question", "idempotency_key": "create-1"})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_no_token_cannot_read_business_data(self):
        self.create()
        for url in ("/api/me", "/api/runs", "/api/metrics", "/api/documents", "/api/openapi.json"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("Synthetic support question", response.text)

    def test_cross_tenant_and_nonowner_requests_cannot_find_or_change_run(self):
        run = self.create()
        for actor in ("partner-requester", "partner-reviewer", "campus-other"):
            for suffix in ("", "/events"):
                with self.subTest(actor=actor, suffix=suffix):
                    response = self.request("GET", f'/api/runs/{run["id"]}{suffix}', actor)
                    self.assertEqual(response.status_code, 404)
            listing = self.request("GET", "/api/runs", actor).json()["runs"]
            self.assertEqual(listing, [])
        for actor in ("partner-requester", "campus-other"):
            self.assertEqual(self.request("POST", f'/api/runs/{run["id"]}/cancel', actor).status_code, 404)
            self.assertEqual(self.request("POST", f'/api/runs/{run["id"]}/commit', actor,
                json={"expected_version": 1, "idempotency_key": "commit-1"}).status_code, 404)
        self.assertEqual(self.request("GET", f'/api/runs/{run["id"]}').json()["state"], "queued")

    def test_caller_cannot_inject_identity_or_promote_role(self):
        response = self.request("POST", "/api/runs", json={
            "question": "Synthetic", "idempotency_key": "create-1", "tenant": "partner", "role": "reviewer"})
        self.assertEqual(response.status_code, 422)
        doc = {"document_id": "policy", "title": "Synthetic", "content": "Synthetic policy.",
               "source": "urn:synthetic:policy", "license": "CC0-1.0"}
        self.assertEqual(self.request("POST", "/api/documents", json=doc).status_code, 403)
        self.assertEqual(self.request("POST", "/api/documents", "campus-reviewer", json=doc).status_code, 200)
        self.assertEqual(self.request("GET", "/api/documents", "partner-reviewer").json()["documents"], [])

    def test_revoked_token_and_write_switch_take_effect_without_restart(self):
        run = self.create()
        self.settings["write_enabled"] = False
        self.save_config()
        response = self.request("POST", f'/api/runs/{run["id"]}/cancel')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "writes_disabled")
        self.assertEqual(self.request("GET", f'/api/runs/{run["id"]}').status_code, 200)
        self.settings["principals"] = [p for p in self.settings["principals"] if p["name"] != "campus-requester"]
        self.save_config()
        self.assertEqual(self.request("GET", f'/api/runs/{run["id"]}').status_code, 401)

    def test_duplicate_credentials_json_keys_and_nonfinite_json_fail_before_creation(self):
        token = self.auth["campus-requester"]["Authorization"]
        response = self.client.post("/api/runs", headers=[("Authorization", token), ("Authorization", token)],
            json={"question": "Synthetic", "idempotency_key": "ambiguous"})
        self.assertEqual(response.status_code, 400)
        for body in ('{"question":"one","question":"two","idempotency_key":"dup"}',
                     '{"question":"Synthetic","idempotency_key":"nan","number":NaN}'):
            response = self.client.post("/api/runs", content=body,
                headers=self.auth["campus-requester"] | {"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.request("GET", "/api/runs").json()["runs"], [])

    def test_cross_origin_and_malformed_origin_fail_closed_as_client_errors(self):
        for origin in ("https://untrusted.invalid", "http://[", "null"):
            with self.subTest(origin=origin):
                response = self.client.post("/api/runs",
                    headers=self.auth["campus-requester"] | {"Origin": origin},
                    json={"question": "Synthetic", "idempotency_key": "origin"})
                self.assertIn(response.status_code, (400, 403))
        self.assertEqual(self.request("GET", "/api/runs").json()["runs"], [])

    def test_invalid_unicode_document_returns_client_error_and_keeps_index_unchanged(self):
        doc = {"document_id": "bad", "title": "Synthetic", "content": "Broken \ud800",
               "source": "urn:synthetic:bad", "license": "CC0-1.0"}
        response = self.client.post("/api/documents", content=json.dumps(doc),
            headers=self.auth["campus-reviewer"] | {"Content-Type": "application/json"})
        self.assertIn(response.status_code, (400, 422))
        self.assertEqual(self.request("GET", "/api/documents").json()["documents"], [])

    def test_unhandled_error_is_redacted_in_real_http_response_and_server_logs(self):
        # Test the actual server boundary: a sanitized response alone cannot stop
        # ASGI servers from logging an exception re-raised by inner middleware.
        import uvicorn
        marker = "SYNTHETIC_PRIVATE_REQUEST_BODY"
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger = logging.getLogger("uvicorn.error")
        previous_level = logger.level
        logger.addHandler(handler)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, log_config=None,
            access_log=False, log_level="warning", timeout_graceful_shutdown=1))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        try:
            worker.start()
            deadline = time.monotonic() + 5
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.started, "test HTTP server did not start")
            payload = json.dumps({"question": marker, "idempotency_key": "log-test"}).encode()
            request = Request(f"http://127.0.0.1:{port}/api/runs", data=payload,
                headers=self.auth["campus-requester"] | {"Content-Type": "application/json"})
            with patch.object(self.app.state.store, "create", side_effect=RuntimeError(marker)):
                with self.assertRaises(HTTPError) as ctx:
                    build_opener(ProxyHandler({})).open(request, timeout=3)
                response = ctx.exception.read().decode()
                ctx.exception.close()
                self.assertEqual(ctx.exception.code, 500)
                self.assertNotIn(marker, response)
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            listener.close()
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
        self.assertFalse(worker.is_alive(), "test HTTP server did not stop")
        self.assertTrue(marker not in captured.getvalue(), "raw request marker leaked to ASGI server logs")

    def test_boolean_version_and_oversized_body_do_not_modify_run(self):
        run = self.create()
        response = self.request("POST", f'/api/runs/{run["id"]}/reply',
            json={"text": "Synthetic", "expected_version": True})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/api/runs", content=b"x" * 160001,
            headers=self.auth["campus-requester"] | {"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.request("GET", f'/api/runs/{run["id"]}').json()["version"], run["version"])

    def test_withdrawal_returns_explicit_inactive_state_and_removes_retrieval(self):
        self.app.state.knowledge.ingest(tenant="campus", document_id="policy", title="退款审核",
            content="审核需要七天。", source="urn:synthetic:policy", license="CC0-1.0")
        doc = self.request("GET", "/api/documents").json()["documents"][0]
        self.assertTrue(doc["active"])
        response = self.request("POST", "/api/documents/policy/withdraw", "campus-reviewer",
            json={"expected_revision": doc["revision"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["active"])
        current = self.request("GET", "/api/documents").json()["documents"][0]
        self.assertFalse(current["active"])
        self.assertNotEqual(current["revision"], doc["revision"])
        self.assertEqual(self.app.state.knowledge.search("campus", "退款审核"), [])


class DeskOperatorRecoveryTests(unittest.TestCase):
    def test_backup_supports_documented_relative_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["init", "--data-dir", "data"]), 0)
                config = Path("data/auth.json")
                settings = json.loads(config.read_text())
                settings["write_enabled"] = False
                config.write_text(json.dumps(settings))
                with redirect_stdout(io.StringIO()) as output:
                    status = main(["backup", "--data-dir", "data", "--destination", "snapshot"])
                self.assertEqual(status, 0, output.getvalue())
                manifest = json.loads(Path("snapshot/manifest.json").read_text())
                self.assertFalse(manifest["credentials_included"])
                self.assertTrue(Path("snapshot/desk.sqlite3").is_file())
                self.assertTrue(Path("snapshot/knowledge.sqlite3").is_file())
                self.assertFalse(Path("snapshot/auth.json").exists())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
