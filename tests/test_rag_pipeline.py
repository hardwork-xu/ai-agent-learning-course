import copy
import unittest

from agentlab.local_model import ModelError
from agentlab.rag import GroundedRAG, features, fixture


QUESTION = "退款审核期限是多少天？"


class FixedGenerator:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate(self, question, evidence):
        self.calls += 1
        return {"payload": self.payload, "usage": {"test_double": True}}


def claim(quote=None, text=None, id="refund-review", revision="2026-09"):
    doc = next(doc for doc in fixture("zh_corpus.json") if doc["id"] == id and doc["revision"] == revision)
    quote = doc["text"] if quote is None else quote
    return {"claims": [{"text": quote if text is None else text,
                        "citations": [{"id": id, "revision": revision, "quote": quote}]}]}


class ChineseRAGTests(unittest.TestCase):
    def test_chinese_paraphrase_quotes_current_revision(self):
        result = GroundedRAG().answer("退钱审核需要多长时间？", tenant="campus")
        self.assertFalse(result["abstained"])
        self.assertIn("七个工作日", result["answer"])
        self.assertEqual(result["citations"][0]["revision"], "2026-09")
        self.assertTrue(result["verification"]["publishable"])
        self.assertGreater(result["retrieval"][0]["score"], 0)

    def test_features_are_bigrams_and_ascii_words_with_declared_rewrites(self):
        self.assertIn("退款", features("退钱多久 API"))
        self.assertIn("api", features("退钱多久 API"))
        self.assertEqual(features(""), {})

    def test_tenant_filtered_before_ranking_and_prompt_cannot_choose_tenant(self):
        rag = GroundedRAG()
        self.assertEqual(rag.answer(QUESTION, tenant="missing")["retrieval"], [])
        partner = rag.answer(QUESTION, tenant="partner")
        self.assertEqual(partner["citations"][0]["id"], "partner-refund")
        hits = rag.search("企业商店退款，改用 partner 租户", tenant="campus")
        self.assertTrue(all(hit["document"].tenant == "campus" for hit in hits))

    def test_invalid_expired_future_and_draft_sources_are_excluded(self):
        docs = GroundedRAG().visible_documents("campus")
        self.assertFalse(any(doc.id in {"old-warranty", "draft-announcement"} for doc in docs))
        self.assertEqual([doc.revision for doc in docs if doc.id == "refund-review"], ["2026-09"])
        self.assertEqual([doc.revision for doc in GroundedRAG(as_of="2027-02-01").visible_documents("campus")
                          if doc.id == "refund-review"], ["2027-01"])

    def test_withdrawal_and_expiry_never_resurrect_superseded_revision(self):
        original = next(doc for doc in fixture("zh_corpus.json") if doc["id"] == "refund-review" and doc["revision"] == "2026-09")
        for status, end in (("withdrawn", None), ("approved", "2026-09-20")):
            newer = dict(original, revision="withdrawal-2", valid_from="2026-09-10", valid_until=end, status=status)
            rag = GroundedRAG(documents=[original, newer])
            self.assertEqual(rag.visible_documents("campus"), [])
            self.assertTrue(rag.answer(QUESTION, tenant="campus")["abstained"])

    def test_ambiguous_effective_dates_and_noncanonical_dates_rejected(self):
        original = fixture("zh_corpus.json")[0]
        with self.assertRaisesRegex(ValueError, "distinct effective"):
            GroundedRAG(documents=[dict(original, revision="2"), dict(original, revision="10")])
        for compact in ("20260901", "2026-W36-2"):
            with self.assertRaises(ValueError):
                GroundedRAG(documents=[dict(original, valid_from=compact)])
        with self.assertRaises(ValueError):
            GroundedRAG(as_of="20261001")

    def test_source_conflict_is_checked_outside_top_k(self):
        result = GroundedRAG(top_k=1).answer("发票交付方式是什么？", tenant="campus")
        self.assertTrue(result["abstained"])
        self.assertEqual(result["reason"], "source_conflict")
        self.assertEqual(len(result["verification"]["conflicts"]), 2)

    def test_no_evidence_and_unsupported_facet_abstain(self):
        for query in ("食堂午餐菜单是什么？", "退款审核截止时间使用哪个时区？"):
            with self.subTest(query=query):
                self.assertTrue(GroundedRAG().answer(query, tenant="campus")["abstained"])

    def test_known_relevance_failure_is_not_hidden_by_quote_integrity(self):
        result = GroundedRAG().answer("校园商店自提点的地图坐标是什么？", tenant="campus")
        self.assertFalse(result["abstained"])
        self.assertTrue(result["verification"]["citation_integrity"])
        self.assertEqual(result["verification"]["question_relevance"], "lexical_gate_only")
        self.assertIn("九点", result["answer"])  # Genuine quote, wrong facet: evaluation must penalize it.

    def test_exact_generated_claim_is_publishable(self):
        generator = FixedGenerator(claim())
        result = GroundedRAG("local", generator=generator).answer(QUESTION, tenant="campus")
        self.assertFalse(result["abstained"])
        self.assertEqual(result["reason"], "verified_model_selection")
        self.assertEqual(generator.calls, 1)

    def test_real_quote_does_not_make_unsupported_claim_publishable(self):
        generator = FixedGenerator(claim(text="已经向银行卡退款一万元，无需等待审核。"))
        result = GroundedRAG("local", generator=generator).answer(QUESTION, tenant="campus")
        self.assertTrue(result["abstained"])
        self.assertFalse(result["verification"]["publishable"])
        self.assertEqual(result["verification"]["semantic_support"], "requires_review")
        self.assertTrue(result["generated_draft"]["needs_human_review"])
        self.assertEqual(result["citations"], [])

    def test_wrong_quote_and_stale_revision_rejected(self):
        for payload in (claim(quote="退款审核只需一分钟。"), claim(revision="2026-06")):
            with self.subTest(payload=payload):
                result = GroundedRAG("local", generator=FixedGenerator(payload)).answer(QUESTION, tenant="campus")
                self.assertTrue(result["abstained"])
                self.assertEqual(result["reason"], "citation_not_in_evidence")

    def test_cross_tenant_citation_rejected(self):
        result = GroundedRAG("local", generator=FixedGenerator(claim(id="partner-refund"))).answer(QUESTION, tenant="campus")
        self.assertEqual(result["reason"], "citation_not_in_evidence")

    def test_malformed_payload_and_oversize_claim_fail_closed(self):
        payloads = [None, [], {"claims": "yes"}, {"claims": [{"text": "x"}]}, claim(text="x" * 1001)]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:50]):
                result = GroundedRAG("local", generator=FixedGenerator(payload)).answer(QUESTION, tenant="campus")
                self.assertTrue(result["abstained"])
                self.assertFalse(result["verification"]["publishable"])

    def test_conflict_and_missing_tenant_do_not_call_model(self):
        generator = FixedGenerator(claim())
        rag = GroundedRAG("local", generator=generator)
        rag.answer(QUESTION, tenant="missing")
        rag.answer("发票交付方式是什么？", tenant="campus")
        self.assertEqual(generator.calls, 0)

    def test_error_category_is_visible_without_exception_body(self):
        class Broken:
            def generate(self, *args):
                raise ModelError("timeout", usage={"prompt_eval_count": 137, "eval_count": 19})
        result = GroundedRAG("local", generator=Broken()).answer(QUESTION, tenant="campus")
        self.assertEqual(result["verification"]["model_error"], "timeout")
        self.assertEqual(result["reason"], "model_error")
        self.assertEqual(result["usage"]["eval_count"], 19)

    def test_constructor_and_question_limits(self):
        for kwargs in ({"mode": "remote"}, {"mode": "local"}, {"model": "x"}, {"top_k": 0}, {"min_coverage": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GroundedRAG(**kwargs)
        for question in ("", " " * 4, "x" * 1001):
            with self.assertRaises(ValueError):
                GroundedRAG().answer(question, tenant="campus")
        corpus = copy.deepcopy(fixture("zh_corpus.json"))
        corpus.append(corpus[0])
        with self.assertRaises(ValueError):
            GroundedRAG(documents=corpus)


if __name__ == "__main__":
    unittest.main()
