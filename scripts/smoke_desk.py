"""Run a real Uvicorn HTTP workflow and recover a lost downstream receipt."""
from contextlib import closing
import argparse
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from agentlab.service import load_config
from agentlab.desk.store import DeskError, Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="explicit installed local Ollama model; omitted uses the fixed baseline")
    parser.add_argument("--protocol", choices=["original", "observed-evidence-v1", "observed-evidence-v2"], default="observed-evidence-v2")
    parser.add_argument("--think", choices=["default", "true", "false"], default="false")
    args = parser.parse_args()
    import httpx
    with tempfile.TemporaryDirectory(prefix="casedesk-smoke-") as temporary:
        root = Path(temporary)
        subprocess.run([sys.executable, "-m", "agentlab.desk", "init", "--data-dir", str(root), "--demo"], check=True, capture_output=True)
        tokens = {p["name"]: p["token"] for p in load_config(root / "auth.json")["principals"]}
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        command = [sys.executable, "-m", "agentlab.desk", "serve", "--data-dir", str(root), "--port", str(port)]
        if args.model:
            command.extend(["--model", args.model, "--protocol", args.protocol, "--think", args.think])
        process = subprocess.Popen(command,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError("server_exited")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(.05)
                else:
                    raise AssertionError("server_start_timeout")
                def call(method, path, body=None, role="requester", expected=200):
                    response = client.request(method, path, json=body,
                        headers={"Authorization": "Bearer " + tokens["campus-" + role]})
                    if response.status_code != expected:
                        raise AssertionError("unexpected_http_status_" + str(response.status_code))
                    return response.json()
                question = "CASE-1001 退款目前处于什么阶段？根据支付渠道，批准后预计还需多久到账？不要猜精确日期。"
                run = call("POST", "/api/runs", {"question": question, "idempotency_key": "smoke",
                           "mode": "local" if args.model else "baseline"}, expected=202)
                path = "/api/runs/" + run["id"]
                for _ in range(120 if args.model else 100):
                    run = call("GET", path)
                    if run["state"] not in {"queued", "running"}:
                        break
                    time.sleep(1 if args.model else .05)
                assert run["state"] == "awaiting_review", (run["state"], run["error"])
                events = call("GET", path + "/events")["events"]
                attempts = [e for e in events if e["phase"] == "planner_attempt_finished"]
                tool_sequence = [o["name"] for o in run["observations"] if o.get("type") == "tool"]
                if args.model:
                    assert "lookup_case" in tool_sequence and "search_knowledge" in tool_sequence
                    assert attempts and all(e.get("usage", {}).get("model_digest") for e in attempts)
                call("POST", path + "/commit", {"expected_version": run["version"], "idempotency_key": "commit"}, expected=409)
                approved = call("POST", path + "/review", {"expected_version": run["version"], "answer": run["answer"],
                    "citations": run["citations"], "decision": "approve", "comment": "simulated test reviewer"}, role="reviewer")
                operation = {"expected_version": approved["version"], "idempotency_key": "commit"}
                ticket = call("POST", path + "/commit", operation)
                assert call("POST", path + "/commit", operation)["ticket_id"] == ticket["ticket_id"]
                store = Store(root / "desk.sqlite3")
                sink = root / "sink.sqlite3"
                try:
                    store.deliver(sink, fail_after_effect=True)
                except DeskError as error:
                    assert error.code == "delivery_interrupted"
                else:
                    raise AssertionError("fault_not_reached")
                assert store.deliver(sink)["delivered"] == 1
                assert store.deliver(sink)["delivered"] == 0
                with closing(sqlite3.connect(sink)) as db:
                    assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
                report = {"status": "passed", "http": "real_uvicorn_loopback", "planner": "actual_local_model" if args.model else "fixed_baseline",
                    "model": args.model, "protocol": args.protocol if args.model else None, "think": args.think if args.model else None,
                    "reviewer": "simulated_test_actor", "question": question, "draft_answer": run["answer"],
                    "citations": run["citations"], "tool_sequence": tool_sequence,
                    "model_attempts": [{k: e.get(k) for k in ("usage", "latency_ms", "error")} for e in attempts],
                    "local_tickets": 1, "downstream_effects": 1, "recovered_lost_receipt": True,
                    "model_calls": len(attempts), "human_outcome_verified": False, "answer_correctness": None}
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)
        report["server_stopped"] = process.poll() is not None
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
