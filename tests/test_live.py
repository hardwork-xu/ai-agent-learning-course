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
        self.assertEqual(result.error, "planner_error")

    def test_provider_error_is_not_retried_or_logged(self):
        with patch("agentlab.live.build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError("SYNTHETIC_SECRET_IN_EXCEPTION")
            result = Runtime(demo_tools()).run("synthetic", self.planner())
            self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(result.error, "planner_error")
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result.trace))

    def test_redirect_is_rejected_before_credentials_can_be_forwarded(self):
        request = Request("https://api.anthropic.com/v1/messages")
        with self.assertRaises(HTTPError):
            NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.invalid/")

    def test_unexpected_response_schema_fails_closed(self):
        for response in (
            {"stop_reason": "end_turn", "content": [{"type": "tool_use", "name": "approve"}]},
            {"stop_reason": "end_turn", "content": []},
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"type":"final","answer":"ok","approved":true}'}]},
        ):
            with self.subTest(response=response), patch("agentlab.live.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
                result = Runtime(demo_tools()).run("synthetic", self.planner())
                self.assertEqual(result.error, "planner_error")


if __name__ == "__main__":
    unittest.main()
