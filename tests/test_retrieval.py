import unittest

from agentlab.evaluation import evaluate, metric
from agentlab.retrieval import demo_retriever


class RetrievalTests(unittest.TestCase):
    def test_answer_quotes_existing_document_and_id(self):
        answer = demo_retriever().answer("refund review window", tenant="campus")
        self.assertFalse(answer.abstained)
        self.assertEqual(answer.citations, ("refund-policy",))
        self.assertIn(answer.hits[0].document.text, answer.text)

    def test_miss_and_stopwords_abstain(self):
        for query in ("", "the and of", "cafeteria opening schedule", "refund lunar mining insurance"):
            with self.subTest(query=query):
                answer = demo_retriever().answer(query, tenant="campus")
                self.assertTrue(answer.abstained)
                self.assertEqual(answer.citations, ())

    def test_tenant_is_filtered_before_ranking(self):
        retriever = demo_retriever(threshold=0.0)
        self.assertFalse(retriever.search("refund review window", tenant="missing"))
        hits = retriever.search("refund review window", tenant="campus")
        self.assertTrue(hits)
        self.assertTrue(all(hit.document.tenant == "campus" for hit in hits))
        self.assertNotIn("private-refund", [hit.document.id for hit in hits])

    def test_lexical_synonym_failure_is_visible(self):
        answer = demo_retriever().answer("password reset expiry seconds", tenant="campus")
        self.assertTrue(answer.abstained)  # The fixture says "valid for fifteen minutes".

    def test_evaluation_has_explicit_denominators_and_known_tradeoff(self):
        report = evaluate()
        baseline = report["baseline"]["metrics"]
        candidate = report["candidate"]["metrics"]
        self.assertEqual(candidate["decision_accuracy"]["denominator"], 8)
        self.assertEqual(candidate["top1_hit_rate_answerable"], metric(3, 4))
        self.assertEqual(candidate["abstention_recall_unanswerable"], metric(4, 4))
        self.assertEqual(baseline["top1_hit_rate_answerable"], metric(4, 4))
        self.assertEqual(baseline["abstention_recall_unanswerable"], metric(2, 4))
        self.assertIsNone(report["configuration"]["model"])
        self.assertIsNone(metric(0, 0)["value"])


if __name__ == "__main__":
    unittest.main()
