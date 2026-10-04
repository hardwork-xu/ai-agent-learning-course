import json
from pathlib import Path
import tempfile
import unittest

from agentlab.rag import fixture
from agentlab.rag_eval import evaluate, main, metric


class RAGEvaluationTests(unittest.TestCase):
    def test_dataset_covers_required_slices_with_explicit_expected_revisions(self):
        cases = fixture("zh_evaluation.json")
        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual({case["split"] for case in cases}, {"dev", "test"})
        self.assertTrue({"access", "conflict", "no_answer", "paraphrase", "revision", "unsupported_facet"}
                        <= {case["slice"] for case in cases})
        for case in cases:
            self.assertEqual(set(case["expected_ids"]), {fact["id"] for fact in case["expected_facts"]})

    def test_metrics_do_not_confuse_quote_integrity_with_relevance(self):
        result = evaluate()
        metrics = result["candidate"]["metrics"]
        self.assertEqual(metrics["decision_accuracy"]["denominator"], 30)
        self.assertLess(metrics["decision_accuracy"]["numerator"], 30)
        self.assertEqual(metrics["exact_quote_integrity_answered"]["value"], 1.0)
        self.assertLess(metrics["relevant_answer_precision"]["value"], 1.0)
        failure = next(row for row in result["candidate"]["cases"] if row["id"] == "zh-041")
        self.assertFalse(failure["correct_decision"])
        self.assertTrue(failure["quote_integrity"])
        self.assertEqual(result["model_calls_attempted"], 0)

    def test_hashes_stable_but_configuration_changes_are_identified(self):
        first, repeated, changed = evaluate(), evaluate(), evaluate(min_coverage=0.7)
        self.assertEqual(first["configuration_sha256"], repeated["configuration_sha256"])
        self.assertEqual(first["corpus_sha256"], changed["corpus_sha256"])
        self.assertNotEqual(first["configuration_sha256"], changed["configuration_sha256"])
        self.assertEqual(first["dataset_counts"], {"dev": 12, "test": 30})

    def test_dev_and_test_denominators_and_repeats(self):
        self.assertEqual(evaluate(split="dev", repeats=2)["candidate"]["metrics"]["decision_accuracy"]["denominator"], 24)
        self.assertIsNone(metric(0, 0)["value"])

    def test_budget_failures_are_not_counted_as_correct_abstentions(self):
        class Selector:
            def generate(self, question, evidence):
                return {"payload": {"claims": [{"text": evidence[0]["quote"], "citations": [evidence[0]]}]}}
        result = evaluate(mode="local", generator=Selector(), max_model_calls=1)
        self.assertEqual(result["model_calls_attempted"], 1)
        errors = [row for row in result["candidate"]["cases"] if row["trace"]["reason"] == "model_error"]
        self.assertTrue(errors)
        self.assertTrue(all(not row["correct_decision"] for row in errors))
        self.assertEqual(result["configuration"]["generator"], "injected_test_double")

    def test_generated_review_packet_keeps_reference_and_draft_separate(self):
        class Paraphraser:
            def generate(self, question, evidence):
                return {"payload": {"claims": [{"text": "这是一条必须人工核对的改写。", "citations": [evidence[0]]}]}}
        result = evaluate(mode="local", generator=Paraphraser())
        pending = [row for row in result["candidate"]["cases"] if row["human_review"]["status"] == "pending"]
        self.assertTrue(pending)
        self.assertTrue(all(not row["correct_decision"] for row in pending))
        self.assertIn("reference_facts", pending[0])
        self.assertIn("generated_draft", pending[0]["trace"])

    def test_cli_writes_parseable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            self.assertEqual(main(["--output", str(path)]), 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["kind"], "visible_synthetic_chinese_rag_regression")


if __name__ == "__main__":
    unittest.main()
