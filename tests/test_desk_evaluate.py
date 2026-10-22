import json
from pathlib import Path
import tempfile
import unittest

from agentlab.desk.evaluate import evaluate, evaluate_predictions, main, metric, task_dataset
from agentlab.desk.knowledge import KnowledgeStore


class DeskEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.knowledge = KnowledgeStore(Path(self.directory.name) / "knowledge.db")
        self.knowledge.seed_demo("demo")

    def tearDown(self):
        self.directory.cleanup()

    def test_matched_retrieval_conditions_preserve_diagnostic_failures(self):
        report = evaluate(knowledge=self.knowledge)
        self.assertEqual(report["task_count"], 24)
        one, five = report["systems"]["bm25_top_1"], report["systems"]["bm25_top_5"]
        for name in ("k1", "b", "positive_score_only", "tokenizer", "title_and_heading_in_index", "query_rewrite"):
            self.assertEqual(report["configurations"]["bm25_top_1"][name], report["configurations"]["bm25_top_5"][name])
        # The top-k comparison must share prefixes; no label-based reranking.
        for first, second in zip(one["cases"], five["cases"]):
            self.assertEqual(first["retrieved"], second["retrieved"][:1])
        self.assertGreater(five["metrics"]["retrieved_characters"], one["metrics"]["retrieved_characters"])
        self.assertTrue(any(row["all_required_evidence_found"] is False for row in five["cases"]))
        self.assertTrue(any(row["unsupported_question_admitted"] is True for row in five["cases"]))
        attack = next(row for row in five["cases"] if row["id"] == "cha-13")
        self.assertTrue(attack["untrusted_payload_retrieved"])
        self.assertIsNone(five["metrics"]["task_completion"])
        self.assertIsNone(five["metrics"]["unsafe_effects"])

    def test_dataset_labels_exist_and_family_split_is_explicit(self):
        dataset = task_dataset()
        families = {}
        manifest = json.loads(Path(__file__).resolve().parents[1].joinpath("src/agentlab/desk/documents/manifest.json").read_text())
        texts = {item["id"]: Path(__file__).resolve().parents[1].joinpath("src/agentlab/desk/documents", item["file"]).read_text() for item in manifest["documents"]}
        for task in dataset["tasks"]:
            self.assertEqual(families.setdefault(task["family"], task["split"]), task["split"])
            for requirement in task["required_evidence"]:
                self.assertIn(requirement["quote"], texts[requirement["document_id"]])
        self.assertIn("共同编写", dataset["split_policy"]["limitations"])
        self.assertEqual(len({task["id"] for task in dataset["tasks"]}), len(dataset["tasks"]))

    def test_fingerprints_splits_and_empty_metric(self):
        first = evaluate(knowledge=self.knowledge, split="calibration")
        second = evaluate(knowledge=self.knowledge, split="challenge")
        self.assertEqual(first["task_count"], 10)
        self.assertEqual(second["task_count"], 14)
        self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
        self.assertEqual(first["index_versions_sha256"], second["index_versions_sha256"])
        doc = self.knowledge.list_documents("demo")[0]
        self.knowledge.withdraw(tenant="demo", document_id=doc["document_id"], expected_revision=doc["revision"])
        self.assertNotEqual(first["index_versions_sha256"], evaluate(knowledge=self.knowledge)["index_versions_sha256"])
        self.assertIsNone(metric(0, 0)["value"])

    def test_prediction_missing_wrong_tenant_stale_quote_and_wrong_answer_not_hidden(self):
        evidence = self.knowledge.search("demo", "退款申请材料齐全后审核期限")[0]
        prediction = {"id": "cal-01", "answer": "所有退款保证1秒到账。", "state": "committed",
                      "citations": [{"id": evidence["id"], "revision": evidence["revision"], "quote": evidence["text"]}]}
        report = evaluate_predictions(self.knowledge, "demo", [prediction], split="calibration")
        self.assertTrue(report["cases"][0]["citation_integrity"])
        self.assertEqual(report["missing_predictions"], 9)
        self.assertIsNone(report["cases"][0]["answer_correctness"])
        self.assertIsNone(report["task_completion"])
        self.assertEqual(report["cases"][0]["human_semantic_review"], "pending")
        self.assertFalse(evaluate_predictions(self.knowledge, "other", [prediction], split="calibration")["cases"][0]["citation_integrity"])
        self.knowledge.withdraw(tenant="demo", document_id=evidence["document_id"], expected_revision=evidence["revision"])
        self.assertFalse(evaluate_predictions(self.knowledge, "demo", [prediction], split="calibration")["cases"][0]["citation_integrity"])
        for invalid in ([prediction, prediction], [{"id": "not-a-task"}]):
            with self.assertRaisesRegex(ValueError, "invalid_prediction_identity"):
                evaluate_predictions(self.knowledge, "demo", invalid)

    def test_explicit_subset_has_correct_denominator_and_no_hidden_omissions(self):
        report = evaluate_predictions(self.knowledge, "demo", [], task_ids=["cha-08", "cha-13"])
        self.assertEqual(report["selected_task_ids"], ["cha-08", "cha-13"])
        self.assertEqual(report["unselected_count"], 22)
        self.assertEqual(report["missing_predictions"], 2)
        self.assertEqual(report["citation_integrity"]["denominator"], 2)
        self.assertEqual(report["selection"], "explicit_visible_subset_not_blind")
        for selection in ([], ["cha-08", "cha-08"], ["missing"], "cha-08"):
            with self.assertRaisesRegex(ValueError, "invalid_task_selection"):
                evaluate_predictions(self.knowledge, "demo", [], task_ids=selection)

    def test_cli_serializes_complete_report(self):
        output = Path(self.directory.name) / "report.json"
        self.assertEqual(main(["--split", "challenge", "--output", str(output)]), 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["task_count"], 14)
        self.assertEqual(len(result["systems"]["bm25_top_5"]["cases"]), 14)


if __name__ == "__main__":
    unittest.main()
