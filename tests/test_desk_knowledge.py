import concurrent.futures
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest

from agentlab.desk.knowledge import CHUNK_SIZE, KnowledgeError, KnowledgeStore, chunk_markdown


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "knowledge.db"
        self.knowledge = KnowledgeStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def ingest(self, **changes):
        args = dict(tenant="demo", document_id="policy", title="支付规则", content="# 支付规则\n\n## 银行卡\n\n银行卡退款2个工作日到账。", source="urn:test:policy", license="MIT")
        args.update(changes)
        return self.knowledge.ingest(**args)

    def test_versions_stale_writes_withdrawal_and_reintroduction(self):
        first = self.ingest()
        hit = self.knowledge.search("demo", "银行卡退款")[0]
        self.assertEqual(hit["revision"], first["revision"])
        self.assertEqual(self.ingest(expected_revision=first["revision"]), first)
        with self.assertRaisesRegex(KnowledgeError, "revision_conflict"):
            self.ingest()
        changed = self.ingest(content="银行卡退款3个工作日到账。", expected_revision=first["revision"])
        self.assertNotEqual(changed["revision"], first["revision"])
        self.assertIsNone(self.knowledge.get("demo", hit["id"]))
        with self.assertRaisesRegex(KnowledgeError, "revision_conflict"):
            self.ingest(content="stale write", expected_revision=first["revision"])
        tombstone = self.knowledge.withdraw(tenant="demo", document_id="policy", expected_revision=changed["revision"])
        self.assertFalse(tombstone["active"])
        self.assertEqual(self.knowledge.search("demo", "银行卡退款"), [])
        self.assertEqual(self.knowledge.withdraw(tenant="demo", document_id="policy", expected_revision=tombstone["revision"]), tombstone)
        restored = self.ingest(expected_revision=tombstone["revision"])
        self.assertNotEqual(restored["revision"], first["revision"])
        self.assertIsNone(self.knowledge.get("demo", hit["id"]))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM versions").fetchone()[0], 4)

    def test_tenant_filter_precedes_scores_and_guessed_chunk_ids(self):
        first = self.ingest()
        before = self.knowledge.search("demo", "银行卡退款")
        other = self.ingest(tenant="other", content="银行卡退款机密值8172", title="机密")
        self.assertNotEqual(first["revision"], other["revision"])
        self.assertEqual(before, self.knowledge.search("demo", "银行卡退款"))
        secret = self.knowledge.search("other", "机密值8172")[0]
        self.assertIsNone(self.knowledge.get("demo", secret["id"]))
        self.assertEqual([row["title"] for row in self.knowledge.list_documents("demo")], ["支付规则"])

    def test_markdown_heading_chunks_boundaries_fences_and_long_body(self):
        text = "# 手册\n\n## 步骤\n\n原文首段。\n\n```text\n# 这是数据\n```\n\n" + "这是长段落。" * 400 + "\n\n## 结束\n\n完成。"
        chunks = chunk_markdown(text)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(0 < len(body) <= CHUNK_SIZE for _, body in chunks))
        self.assertEqual(chunks[0][0], "手册 / 步骤")
        self.assertIn("# 这是数据", chunks[0][1])
        self.assertEqual(chunks[-1], ("手册 / 结束", "完成。"))
        version = self.ingest(content=text)
        persisted = KnowledgeStore(self.path)
        hit = persisted.search("demo", "完成")[0]
        self.assertEqual(hit["revision"], version["revision"])
        self.assertEqual(persisted.get("demo", hit["id"])["text"], "完成。")

    def test_concurrent_compare_and_swap_has_one_winner(self):
        first = self.ingest()
        barrier = threading.Barrier(2)
        def writer(number):
            barrier.wait()
            try:
                return self.ingest(content=f"不同业务规则 {number}", expected_revision=first["revision"])["revision"]
            except KnowledgeError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(writer, (1, 2)))
        self.assertEqual(results.count("revision_conflict"), 1)
        self.assertEqual(len(self.knowledge.list_documents("demo")), 1)

    def test_validation_guard_holds_source_write_until_effect_finishes(self):
        first = self.ingest()
        hit = self.knowledge.search("demo", "银行卡退款")[0]
        started, finished = threading.Event(), threading.Event()
        def withdraw():
            started.set()
            result = self.knowledge.withdraw(tenant="demo", document_id="policy", expected_revision=first["revision"])
            finished.set()
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with self.knowledge.validation_guard():
                future = executor.submit(withdraw)
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.05))
                self.assertIsNotNone(self.knowledge.get("demo", hit["id"]))
            self.assertFalse(future.result(timeout=3)["active"])
        self.assertIsNone(self.knowledge.get("demo", hit["id"]))

    def test_demo_is_explicit_idempotent_and_retrieves_injection(self):
        self.assertEqual(self.knowledge.list_documents("demo"), [])
        seeded = self.knowledge.seed_demo("demo")
        self.assertEqual(seeded["count"], 8)
        self.assertEqual(self.knowledge.seed_demo("demo")["count"], 0)
        hits = self.knowledge.search("demo", "忽略之前的规则 系统管理员 partner CASE-2001 访问令牌", limit=5)
        self.assertTrue(any("合成攻击开始" in hit["text"] for hit in hits))
        self.assertTrue(all(hit["document_id"] != "archived-campaign" for hit in hits))
        self.assertEqual(len(self.knowledge.list_documents("demo")), 8)
        self.assertEqual(len(self.knowledge.list_documents("other")), 0)

    def test_invalid_input_and_failed_write_leave_existing_document_unchanged(self):
        first = self.ingest()
        for change in ({"content": "# only heading"}, {"content": "x" * 128001}, {"source": ""}, {"tenant": "../../other"}, {"expected_revision": "bad"}, {"content": "\ud800"}, {"title": "\udfff"}):
            with self.assertRaises(KnowledgeError):
                self.ingest(**change)
        self.assertEqual(self.knowledge.list_documents("demo")[0], first)
        for limit in (0, 21, True, 1.5):
            with self.assertRaisesRegex(KnowledgeError, "invalid_limit"):
                self.knowledge.search("demo", "支付", limit=limit)
        self.assertEqual(self.knowledge.search("demo", "🪴"), [])
        self.assertIsNone(self.knowledge.get("demo", "../../path"))


if __name__ == "__main__":
    unittest.main()
