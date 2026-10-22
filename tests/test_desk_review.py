"""Independent regressions found by exercising public lifecycle operations."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import threading
import time
import unittest

from agentlab.desk.engine import Engine
from agentlab.desk.knowledge import KnowledgeStore
from agentlab.desk.store import DeskError, Store


class IndependentLifecycleReview(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.knowledge = KnowledgeStore(self.path / "knowledge.db")
        self.knowledge.ingest(tenant="demo", document_id="policy", title="退款",
            content="退款审核需要7个工作日。", source="urn:review:policy", license="MIT")

    def tearDown(self):
        self.directory.cleanup()

    def prepare(self, store):
        engine = Engine(store, self.knowledge)
        run = store.create("demo", "requester", "退款审核多久", "create")
        engine.run_once()
        return engine, store.get("demo", run["id"])

    def test_reapproving_rejected_work_obeys_global_capacity(self):
        store = Store(self.path / "runs.db", capacity=1)
        engine, run = self.prepare(store)
        rejected = engine.review("demo", run["id"], "reviewer", run["version"], run["answer"], run["citations"], "reject")
        second = store.create("demo", "requester", "另一条退款问题", "second")
        with self.assertRaises(DeskError) as raised:
            engine.review("demo", run["id"], "reviewer", rejected["version"], rejected["answer"], rejected["citations"], "approve")
        self.assertEqual(raised.exception.code, "capacity_exceeded")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(store.get("demo", run["id"])["state"], "rejected")
        self.assertEqual(store.get("demo", second["id"])["state"], "queued")
        store.cancel("demo", second["id"], "requester")
        approved = engine.review("demo", run["id"], "reviewer", rejected["version"], rejected["answer"], rejected["citations"], "approve")
        self.assertEqual(approved["state"], "approved")

    def test_cancellation_retains_late_model_usage_without_reviving_run(self):
        store = Store(self.path / "runs.db")
        entered, release = threading.Event(), threading.Event()
        class MeasuredPlanner:
            last_usage = None
            last_latency_ms = None
            def next(self, *args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test timeout")
                self.last_usage = {"prompt_eval_count": 123, "eval_count": 45}
                self.last_latency_ms = 12
                return {"type": "abstain", "reason": "合成依赖返回"}
        engine = Engine(store, self.knowledge, planner_factory=MeasuredPlanner)
        run = store.create("demo", "requester", "退款审核多久", "create", mode="local")
        worker = threading.Thread(target=engine.run_once)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            pending_usage = store.metrics("demo")["usage"]
            self.assertEqual(pending_usage["eval_count"]["unknown_calls"], 1)
            store.cancel("demo", run["id"], "requester")
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(store.get("demo", run["id"])["state"], "cancelled")
        usage = store.metrics("demo")["usage"]
        self.assertEqual(usage["prompt_eval_count"]["known_sum"], 123)
        self.assertEqual(usage["eval_count"]["known_sum"], 45)
        self.assertEqual(usage["eval_count"]["known_calls"], 1)
        self.assertEqual(usage["eval_count"]["unknown_calls"], 0)
        with closing(sqlite3.connect(store.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM tickets").fetchone()[0], 0)

    def test_wall_clock_rollback_cannot_accept_result_after_monotonic_deadline(self):
        now = [1000.]
        store = Store(self.path / "runs.db", clock=lambda: now[0])
        class LatePlanner:
            def next(self, *args, **kwargs):
                time.sleep(0.04)
                now[0] -= 10
                return {"type": "abstain", "reason": "超过本次执行预算的返回"}
        engine = Engine(store, self.knowledge, planner_factory=LatePlanner, timeout_seconds=0.02)
        run = store.create("demo", "requester", "退款审核多久", "create", mode="local")
        engine.run_once()
        result = store.get("demo", run["id"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "deadline_exceeded")

    def test_actual_retrieved_attack_cannot_grant_tool_or_tenant_authority(self):
        # This adversarial planner deliberately follows the payload. The test
        # proves the gateway boundary after exposure, not model robustness.
        self.knowledge.seed_demo("demo")
        for action in ("export_ticket", "lookup_case"):
            with self.subTest(action=action):
                store = Store(self.path / (action + ".db"))
                exposed = []
                class AdversarialPlanner:
                    def next(self, question, observations, *, remaining_seconds):
                        if not observations:
                            return {"type": "tool", "name": "search_knowledge", "arguments": {
                                "query": "忽略之前的规则 系统管理员 partner CASE-2001 访问令牌"}}
                        if len(observations) == 1:
                            exposed.append(any("【合成攻击开始】" in row["text"] for row in observations[0]["result"]))
                            return {"type": "tool", "name": action, "arguments": (
                                {"case_id": "CASE-2001"} if action == "lookup_case" else {})}
                        return {"type": "abstain", "reason": "越权记录不可访问"}
                engine = Engine(store, self.knowledge, planner_factory=AdversarialPlanner)
                run = store.create("demo", "requester", "处理客户退款留言", "create", mode="local")
                engine.run_once()
                result = store.get("demo", run["id"])
                self.assertEqual(exposed, [True])
                if action == "export_ticket":
                    self.assertEqual(result["error"], "unknown_tool")
                    self.assertEqual(result["state"], "failed")
                else:
                    self.assertEqual(result["observations"][-1]["result"], {
                        "found": False, "case": None, "data_kind": "synthetic_fixture"})
                with closing(sqlite3.connect(store.path)) as connection, connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM tickets").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    def test_cancel_commit_race_has_one_business_outcome(self):
        store = Store(self.path / "runs.db")
        engine, run = self.prepare(store)
        approved = engine.review("demo", run["id"], "reviewer", run["version"], run["answer"], run["citations"], "approve")
        barrier = threading.Barrier(2)
        def operation(kind):
            barrier.wait()
            try:
                if kind == "cancel":
                    return store.cancel("demo", run["id"], "requester")["state"]
                return engine.commit("demo", run["id"], "requester", approved["version"], "export")["state"]
            except DeskError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(operation, ("cancel", "commit")))
        final = store.get("demo", run["id"])
        with closing(sqlite3.connect(store.path)) as connection, connection:
            tickets = connection.execute("SELECT count(*) FROM tickets").fetchone()[0]
            outbox = connection.execute("SELECT count(*) FROM outbox").fetchone()[0]
        self.assertIn(final["state"], ("committed", "cancelled"))
        self.assertEqual(tickets, int(final["state"] == "committed"))
        self.assertEqual(outbox, tickets)
        self.assertEqual(sum(value in ("committed", "cancelled") for value in outcomes), 1)


if __name__ == "__main__":
    unittest.main()
