import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from agentlab.live import ClaudePlanner, NoRedirect
from agentlab.runtime import Finish, Runtime, demo_tools


class LiveAdapterTests(unittest.TestCase):
    """Contract tests use stub HTTP responses; no real network or real API key."""

    def planner(self):
        return ClaudePlanner(api_key="synthetic-test-value", model="synthetic-model")

    def test_real_adapter_request_contract_with_stub_transport(self):
        response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"type":"final","answer":"done"}'}]}
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            self.assertEqual(self.planner().next("synthetic", ()), Finish("done"))
            args, kwargs = opener.return_value.open.call_args
            request = args[0]
            self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
            self.assertEqual(json.loads(request.data)["max_tokens"], 400)
            self.assertEqual(request.get_header("X-api-key"), "synthetic-test-value")
            self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(kwargs["timeout"], 20)
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_truncated_model_output_fails_closed(self):
        response = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"type":"final","answer":"truncated"}'}]}
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            result = Runtime(demo_tools()).run("synthetic", self.planner())
        self.assertEqual(result.error, "provider_incomplete")

    def test_provider_error_is_not_retried_or_logged(self):
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError("SYNTHETIC_SECRET_IN_EXCEPTION")
            result = Runtime(demo_tools()).run("synthetic", self.planner())
            self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(result.error, "provider_timeout")
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result.trace))

    def test_redirect_is_rejected_before_credentials_can_be_forwarded(self):
        request = Request("https://api.anthropic.com/v1/messages")
        with self.assertRaises(HTTPError):
            NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.invalid/")

    def test_unexpected_response_schema_fails_closed(self):
        for response, code in (
            ({"stop_reason": "end_turn", "content": [{"type": "tool_use", "name": "approve"}]}, "provider_schema"),
            ({"stop_reason": "end_turn", "content": []}, "provider_schema"),
            ({"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"type":"final","answer":"ok","approved":true}'}]}, "provider_invalid_json"),
        ):
            with self.subTest(response=response), patch("agentlab.live.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
                result = Runtime(demo_tools()).run("synthetic", self.planner())
                self.assertEqual(result.error, code)

    def test_usage_is_preserved_without_error_bodies_or_prompt_data(self):
        response = {"id": "msg-synthetic", "model": "synthetic-returned", "stop_reason": "end_turn",
                    "usage": {"input_tokens": 137, "output_tokens": 19, "cache_read_input_tokens": 7},
                    "content": [{"type": "text", "text": '{"type":"final","answer":"done"}'}]}
        planner = self.planner()
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            planner.next("SYNTHETIC_PRIVATE_MARKER", ())
        record = planner.calls[0]
        self.assertEqual(record["usage"]["input_tokens"], 137)
        self.assertEqual(record["usage"]["cache_read_input_tokens"], 7)
        self.assertIsNone(record["usage"]["cache_creation_input_tokens"])
        self.assertEqual(record["returned_model"], "synthetic-returned")
        self.assertGreaterEqual(record["latency_ms"], 0)
        self.assertIsNone(record["cost"])
        self.assertNotIn("PRIVATE_MARKER", json.dumps(record))
        self.assertNotIn("synthetic-test-value", json.dumps(record))

    def test_rate_limit_and_bad_json_are_distinct_safe_failures(self):
        planner = self.planner()
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(
                "https://example.invalid", 429, "SYNTHETIC_SECRET", {}, io.BytesIO(b"private"))
            result = Runtime(demo_tools()).run("synthetic", planner)
        self.assertEqual(result.error, "provider_rate_limit")
        self.assertEqual(planner.calls[0]["status"], "provider_rate_limit")
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(planner.calls))
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b"not json")
            result = Runtime(demo_tools()).run("synthetic", self.planner())
        self.assertEqual(result.error, "provider_invalid_json")


if __name__ == "__main__":
    unittest.main()
