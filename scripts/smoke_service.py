"""One isolated offline end-to-end experiment using actual HTTP and the Chinese RAG."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))
from agentlab.service import Application, deliver_outbox, init_config, load_config, make_server
from service_client import send


def main():
    with tempfile.TemporaryDirectory(prefix="agentlab-service-") as temporary:
        root = Path(temporary)
        config, database, downstream = root / "auth.json", root / "service.sqlite3", root / "downstream.sqlite3"
        init_config(config)
        principals = {p["name"]: p["token"] for p in load_config(config)["principals"]}
        server = make_server(Application(config, database), port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        def call(role, method, path, payload=None, expected=200):
            status, result = send(url, principals["campus-" + role], method, path, payload)
            if status != expected:
                raise AssertionError(f"unexpected_status_{status}")
            return result
        try:
            answer = call("requester", "POST", "/v1/query", {"question": "退款审核期限是多少天？"})
            assert not answer["abstained"] and answer["verification"]["publishable"]
            draft = call("requester", "POST", "/v1/requests", {"query_id": answer["query_id"],
                "request_id": "smoke-request", "subject": "合成退款咨询", "priority": "P2"}, expected=201)
            path = "/v1/requests/smoke-request"
            binding = {"expected_version": draft["version"], "expected_digest": draft["digest"]}
            operation = {**binding, "idempotency_key": "smoke-operation"}
            call("requester", "POST", path + "/commit", operation, expected=409)
            # This is an explicit simulated reviewer in a test, not human approval evidence.
            reviewed = call("reviewer", "GET", path)
            assert reviewed["digest"] == draft["digest"]
            call("reviewer", "POST", path + "/approve", binding)
            ticket = call("requester", "POST", path + "/commit", operation)
            replay = call("requester", "POST", path + "/commit", operation)
            assert ticket["ticket_id"] == replay["ticket_id"]
            try:
                deliver_outbox(database, downstream, crash_after_effect=True)
            except RuntimeError as exc:
                assert str(exc) == "simulated_crash_after_effect"
            else:
                raise AssertionError("fault_point_not_reached")
            recovered = deliver_outbox(database, downstream)
            assert recovered["delivered"] == 1
            assert deliver_outbox(database, downstream)["delivered"] == 0
            with closing(sqlite3.connect(downstream)) as db:
                effects = db.execute("SELECT COUNT(*) FROM received_tickets").fetchone()[0]
            assert effects == 1
            print(json.dumps({"status": "passed", "mode": "extractive", "http": "real_loopback",
                "reviewer": "simulated_test_actor", "citations": len(answer["citations"]),
                "local_tickets": 1, "downstream_effects": effects, "outbox_recovered": True,
                "external_model_calls": 0, "human_learning_verified": False}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
