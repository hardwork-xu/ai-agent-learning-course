from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

from agentlab.local_model import LocalModel, ModelError, strict_json


@contextmanager
def server(responder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            responder(self, None)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            responder(self, payload)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def reply(handler, body, status=200):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class LocalModelTests(unittest.TestCase):
    def test_no_remote_origins_credentials_queries_or_path(self):
        for url in ("https://127.0.0.1:11434", "http://example.com", "http://127.0.0.2",
                    "http://user@localhost", "http://localhost/api/chat", "http://localhost?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalModel("test-only", base_url=url)

    def test_adapter_uses_schema_and_records_identity_usage(self):
        calls = []
        def respond(handler, payload):
            calls.append(handler.path)
            if handler.path == "/api/version":
                reply(handler, {"version": "test-server"})
            elif handler.path == "/api/tags":
                reply(handler, {"models": [{"name": "test-only", "digest": "fixture-digest"}]})
            else:
                self.assertFalse(payload["stream"])
                self.assertEqual(payload["format"]["type"], "object")
                self.assertEqual(payload["options"]["num_predict"], 64)
                reply(handler, {"done": True, "model": "test-only", "message": {"content": '{"claims": []}'},
                                "prompt_eval_count": 137, "eval_count": 19, "prompt_eval_cached_count": 7,
                                "total_duration": 123456})
        with server(respond) as url:
            result = LocalModel("test-only", base_url=url, max_output_tokens=64).generate("退款期限", [])
        self.assertEqual(calls, ["/api/version", "/api/tags", "/api/chat"])
        self.assertEqual(result["payload"], {"claims": []})
        self.assertEqual(result["usage"]["model_digest"], "fixture-digest")
        self.assertEqual(result["usage"]["server_version"], "test-server")
        self.assertEqual(result["usage"]["prompt_eval_count"], 137)
        self.assertEqual(result["usage"]["eval_count"], 19)

    def test_redirect_is_not_followed(self):
        calls = []
        def respond(handler, payload):
            calls.append(handler.path)
            handler.send_response(302)
            handler.send_header("Location", "http://example.com/private")
            handler.end_headers()
        with server(respond) as url:
            model = LocalModel("test-only", base_url=url)
            model.metadata_checked = model.local_verified = True
            with self.assertRaisesRegex(ModelError, "redirect_rejected"):
                model.generate("query", [])
        self.assertEqual(calls, ["/api/chat"])

    def test_response_byte_budget(self):
        with server(lambda handler, payload: reply(handler, b" " * 513)) as url:
            model = LocalModel("test-only", base_url=url, max_response_bytes=512)
            model.metadata_checked = model.local_verified = True
            with self.assertRaisesRegex(ModelError, "response_too_large"):
                model.generate("query", [])

    def test_malformed_duplicate_json_and_truncated_outputs(self):
        responses = [
            ({"done": True, "message": {"content": '{"claims":[],"claims":[]}'}}, "malformed_model_json"),
            ({"done": False, "message": {"content": '{"claims": []}'}}, "incomplete_response"),
            ({"done": True, "done_reason": "length", "message": {"content": '{}'}}, "output_budget_exceeded"),
            ({"done": True, "eval_count": 100, "message": {"content": '{"claims": []}'}}, "output_budget_exceeded"),
        ]
        for response, error in responses:
            with self.subTest(error=error), server(lambda handler, payload: reply(handler, response)) as url:
                model = LocalModel("test-only", base_url=url, max_output_tokens=64)
                model.metadata_checked = model.local_verified = True
                with self.assertRaisesRegex(ModelError, error):
                    model.generate("query", [])

    def test_wall_clock_timeout_also_interrupts_drip_response(self):
        def respond(handler, payload):
            handler.send_response(200)
            handler.end_headers()
            try:
                for _ in range(30):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass
        with server(respond) as url:
            model = LocalModel("test-only", base_url=url, timeout=0.12)
            model.metadata_checked = model.local_verified = True
            started = time.monotonic()
            with self.assertRaisesRegex(ModelError, "timeout"):
                model.generate("query", [])
            self.assertLess(time.monotonic() - started, 0.8)

    def test_strict_json_disallows_ambiguous_and_nonfinite_values(self):
        for body in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            with self.assertRaises(ValueError):
                strict_json(body)

    def test_bad_generated_json_preserves_known_token_usage(self):
        response = {"done": True, "model": "test-only", "message": {"content": "{broken"},
                    "prompt_eval_count": 137, "eval_count": 19, "total_duration": 987654}
        with server(lambda handler, payload: reply(handler, response)) as url:
            model = LocalModel("test-only", base_url=url)
            model.metadata_checked = model.local_verified = True
            with self.assertRaises(ModelError) as raised:
                model.generate("query", [])
        self.assertEqual(raised.exception.code, "malformed_model_json")
        self.assertEqual(raised.exception.usage["prompt_eval_count"], 137)
        self.assertEqual(raised.exception.usage["eval_count"], 19)
        self.assertEqual(raised.exception.usage["total_duration"], 987654)
        self.assertGreaterEqual(raised.exception.latency_ms, 0)

    def test_remote_model_tag_is_rejected_before_prompt_is_sent(self):
        calls = []
        def respond(handler, payload):
            calls.append(handler.path)
            if handler.path == "/api/version":
                reply(handler, {"version": "test-server"})
            else:
                reply(handler, {"models": [{"name": "test-only", "digest": "fixture",
                                            "remote_host": "https://example.com", "remote_model": "upstream"}]})
        with server(respond) as url:
            with self.assertRaisesRegex(ModelError, "remote_model_rejected"):
                LocalModel("test-only", base_url=url).generate("question must remain local", [])
        self.assertEqual(calls, ["/api/version", "/api/tags"])

    def test_absent_model_does_not_issue_chat_or_pull(self):
        calls = []
        def respond(handler, payload):
            calls.append(handler.path)
            reply(handler, {"models": []})
        with server(respond) as url:
            with self.assertRaisesRegex(ModelError, "local_model_not_available"):
                LocalModel("missing", base_url=url).generate("query", [])
        self.assertEqual(calls, ["/api/version", "/api/tags"])


if __name__ == "__main__":
    unittest.main()
