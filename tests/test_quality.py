import contextlib
import io
import json
import unittest
from unittest.mock import patch

from agentlab.__main__ import main, tools_demo
from agentlab.live import parse_decision
from agentlab.quality import assess_refund_sum
from agentlab.runtime import BoundaryError, Finish, Runtime, ScriptedPlanner, ToolCall, demo_tools


class TaskQualityTests(unittest.TestCase):
    def observed(self, answer):
        return ScriptedPlanner([ToolCall("lookup_policy", {"policy": "refund"}),
                                ToolCall("add_numbers", {"a": 12, "b": 8}), Finish(answer)])

    def test_unassessed_termination_is_explicitly_unknown(self):
        result = Runtime([]).run("synthetic", ScriptedPlanner([Finish("12+8=999; refund executed")]))
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.task_success)

    def test_wrong_result_and_fabricated_action_fail_after_real_tool_success(self):
        for value in ({"sum": 999, "approval_required": True, "action_performed": False},
                      {"sum": 20, "approval_required": True, "action_performed": True},
                      {"sum": 20, "approval_required": 1, "action_performed": False}):
            with self.subTest(value=value):
                result = Runtime(demo_tools()).run("synthetic", self.observed(json.dumps(value)),
                                                   evaluator=assess_refund_sum)
                self.assertEqual(result.status, "completed")
                self.assertFalse(result.task_success)

    def test_correct_looking_answer_without_observation_fails(self):
        answer = '{"sum":20,"approval_required":true,"action_performed":false}'
        result = Runtime(demo_tools()).run("synthetic", ScriptedPlanner([Finish(answer)]),
                                           evaluator=assess_refund_sum)
        self.assertFalse(result.task_success)
        self.assertFalse(result.evaluation["calculation_observed"])
        self.assertTrue(tools_demo()["task_success"])

    def test_answer_protocol_rejects_ambiguous_and_nonfinite_json(self):
        for answer in ('{"sum":20,"sum":999,"approval_required":true,"action_performed":false}',
                       '{"sum":NaN,"approval_required":true,"action_performed":false}',
                       '{"sum":1e999,"approval_required":true,"action_performed":false}'):
            result = Runtime(demo_tools()).run("synthetic", self.observed(answer), evaluator=assess_refund_sum)
            self.assertFalse(result.task_success)
        with self.assertRaises(BoundaryError):
            parse_decision('{"type":"final","answer":"one","answer":"two"}')
        with self.assertRaises(BoundaryError):
            parse_decision('{"type":"tool","name":"add_numbers","arguments":{"a":1e999,"b":1}}')

    def test_equal_sum_from_wrong_arguments_does_not_meet_task_contract(self):
        planner = ScriptedPlanner([
            ToolCall("lookup_policy", {"policy": "refund"}),
            ToolCall("add_numbers", {"a": 19, "b": 1}),
            Finish('{"sum":20,"approval_required":true,"action_performed":false}')])
        result = Runtime(demo_tools()).run("synthetic", planner, evaluator=assess_refund_sum)
        self.assertFalse(result.task_success)
        self.assertFalse(result.evaluation["calculation_observed"])

    def test_evaluator_failure_does_not_escape_or_leak_details(self):
        def broken(*_):
            raise ValueError("SYNTHETIC_SECRET")
        result = Runtime([]).run("synthetic", ScriptedPlanner([Finish("done")]), evaluator=broken)
        self.assertFalse(result.task_success)
        self.assertEqual(result.evaluation, {"evaluator_succeeded": False})
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result.trace))

    def test_live_cli_stub_cannot_pass_with_incorrect_terminated_answer(self):
        response = {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": '{"type":"final","answer":"12+8=999; refund executed"}'}]}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "synthetic-key", "AGENT_MODEL": "synthetic-model"}), \
                patch("agentlab.live.build_opener") as opener, contextlib.redirect_stdout(io.StringIO()) as output:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            code = main(["demo", "tools", "--live"])
        self.assertEqual(code, 1)
        body = json.loads(output.getvalue())
        self.assertEqual(body["status"], "completed")
        self.assertFalse(body["task_success"])


if __name__ == "__main__":
    unittest.main()
