"""Run after `python -m pip install -e .`; uses synthetic data and no network."""

import json

from agentlab.runtime import Runtime, ScriptedPlanner, ToolCall, demo_tools
from agentlab.workflow import TicketWorkflow, WorkflowError


def main():
    drills = {}
    for name, call in (
        ("unknown_tool", ToolCall("approve_refund", {})),
        ("wrong_type", ToolCall("add_numbers", {"a": "12", "b": 8})),
        ("unexpected_authority_field", ToolCall("add_numbers", {"a": 12, "b": 8, "approved": True})),
    ):
        drills[name] = Runtime(demo_tools()).run("synthetic", ScriptedPlanner([call])).error
    with TicketWorkflow(":memory:") as workflow:
        original = {"subject": "Synthetic request", "body": "Fictional incident", "priority": "P2"}
        workflow.create("campus", "request-1", original)
        token = workflow.approve("campus", "request-1", reviewer="demo-reviewer")
        workflow.amend("campus", "request-1", {**original, "priority": "P1"})
        try:
            workflow.execute("campus", "request-1", approval_token=token, idempotency_key="create-1")
        except WorkflowError as exc:
            drills["stale_approval"] = str(exc)
        drills["tickets_created"] = workflow.ticket_count("campus")
    print(json.dumps(drills, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
