from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from .evaluation import evaluate
from .console import configure_utf8_output
from .quality import assess_refund_sum
from .retrieval import demo_retriever
from .runtime import Finish, Runtime, ScriptedPlanner, ToolCall, demo_tools
from .workflow import TicketWorkflow, WorkflowError


def tools_demo(*, live: bool = False) -> dict:
    task = ('Read the synthetic refund policy and add 12 plus 8. Final answer must be a JSON object '
            'with exactly sum (number), approval_required (boolean), action_performed (boolean). '
            'A final answer is assessed separately from loop termination; do not claim an unperformed effect.')
    if live:
        from .live import ClaudePlanner
        planner = ClaudePlanner.from_environment()
        mode = "live_llm"
    else:
        planner = ScriptedPlanner([
            ToolCall("lookup_policy", {"policy": "refund"}),
            ToolCall("add_numbers", {"a": 12, "b": 8}),
            Finish('{"sum":20,"approval_required":true,"action_performed":false}'),
        ])
        mode = "scripted_test_double"
    result = Runtime(demo_tools(), max_steps=6).run(task, planner, evaluator=assess_refund_sum)
    return {"mode": mode, **asdict(result), "model_calls": getattr(planner, "calls", [])}


def rag_demo() -> dict:
    retriever = demo_retriever()
    results = []
    for query in ("refund review window", "refund lunar mining insurance"):
        answer = retriever.answer(query, tenant="campus")
        results.append({"query": query, "text": answer.text, "citations": answer.citations,
                        "abstained": answer.abstained})
    return {"mode": "lexical_extract_only", "threshold": retriever.threshold, "results": results}


def workflow_demo() -> dict:
    payload = {"subject": "Synthetic support request", "body": "A fictional classroom service is unavailable.", "priority": "P1"}
    with tempfile.TemporaryDirectory(prefix="agentlab-") as directory:
        database = Path(directory) / "workflow.sqlite3"
        with TicketWorkflow(database) as workflow:
            created = workflow.create("campus", "request-1", payload)
            try:
                workflow.execute("campus", "request-1", approval_token="", idempotency_key="create-1")
            except WorkflowError as exc:
                blocked = str(exc)
            token = workflow.approve("campus", "request-1", reviewer="demo-reviewer")
        # A new connection resumes the durable checkpoint. Token is kept only in this demo's memory.
        with TicketWorkflow(database) as resumed:
            checkpoint = resumed.checkpoint("campus", "request-1")
            first = resumed.execute("campus", "request-1", approval_token=token, idempotency_key="create-1")
            replay = resumed.execute("campus", "request-1", approval_token=token, idempotency_key="create-1")
            return {"mode": "synthetic_local_sqlite", "initial_checkpoint": created,
                    "unapproved_attempt": blocked, "resumed_checkpoint": checkpoint,
                    "replay_returned_same_result": first == replay,
                    "ticket_count": resumed.ticket_count("campus"),
                    "final_checkpoint": resumed.checkpoint("campus", "request-1")}


def main(argv: list[str] | None = None) -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description="Offline learning labs; live calls require an explicit flag.")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("name", choices=["tools", "rag", "workflow"])
    demo.add_argument("--live", action="store_true", help="tools only: real HTTPS model call; incurs API usage")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--output", type=Path, help="Write a JSON report (also printed to stdout)")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            if args.live and args.name != "tools":
                parser.error("--live is supported only with 'demo tools'")
            result = tools_demo(live=args.live) if args.name == "tools" else rag_demo() if args.name == "rag" else workflow_demo()
        else:
            result = evaluate()
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if args.command == "evaluate" and args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 1 if result.get("status") == "failed" or result.get("task_success") is False else 0
    except (ValueError, OSError) as exc:
        # Avoid embedding untrusted paths, headers, responses or secret values in errors.
        print(json.dumps({"status": "failed", "error": type(exc).__name__,
                          "hint": "Check command arguments, writable output path, and required environment variables."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
