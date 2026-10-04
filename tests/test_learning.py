"""Check whether grading actually separates correct and broken submissions."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from exercises import reference, starter
from exercises.grading import load_submission, run_submission


class LearningChecks(unittest.TestCase):
    def test_reference_passes_all_contracts(self):
        report = run_submission(reference)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["total_checks"], 20)

    def test_untouched_starter_fails_every_gate(self):
        for task in ("prerequisites", "inventory", "quote", "transfer"):
            with self.subTest(task=task):
                report = run_submission(starter, task)
                self.assertFalse(report["passed"])
                self.assertEqual(report["passed_checks"], 0)

    def test_rejects_bool_accepting_json_implementation(self):
        import json
        wrong = SimpleNamespace(**vars(reference))
        def parse(text):
            units = json.loads(text)["units"]
            if not isinstance(units, int) or not 1 <= units <= 100:
                raise ValueError("invalid units")
            return units
        wrong.parse_units = parse
        report = run_submission(wrong, "prerequisites")
        failed = {row["check"] for row in report["checks"] if not row["passed"]}
        self.assertIn("json_bool_and_numeric_types", failed)
        self.assertIn("json_schema_and_bounds", failed)

    def test_rejects_tool_ignoring_extra_fields(self):
        wrong = SimpleNamespace(**vars(reference))
        def tool(request, catalog):
            sku = request["arguments"]["sku"]
            return {"sku": sku, "available": catalog[sku]}
        wrong.inventory_tool = tool
        report = run_submission(wrong, "inventory")
        self.assertFalse(report["passed"])
        self.assertTrue(any(row["check"] == "inventory_exact_schema" and not row["passed"] for row in report["checks"]))

    def test_rejects_json_coercing_non_string_input(self):
        wrong = SimpleNamespace(**vars(reference))
        def parse(text):
            if isinstance(text, (bytes, bytearray)):
                text = text.decode("utf-8")
            return reference.parse_units(text)
        wrong.parse_units = parse
        report = run_submission(wrong, "prerequisites")
        self.assertFalse(report["passed"])
        self.assertTrue(any(row["check"] == "json_schema_and_bounds" and not row["passed"] for row in report["checks"]))

    def test_skipped_work_is_not_reported_as_passed(self):
        wrong = SimpleNamespace(**vars(reference))
        def parse(_):
            raise unittest.SkipTest("not implemented yet")
        wrong.parse_units = parse
        report = run_submission(wrong, "prerequisites")
        self.assertFalse(report["passed"])
        self.assertEqual(report["passed_checks"], 6)

    def test_rejects_quote_lookup_before_format_validation(self):
        wrong = SimpleNamespace(**vars(reference))
        def quote(request, prices):
            if type(request) is dict and type(request.get("arguments")) is dict:
                sku = request["arguments"].get("sku")
                if type(sku) is str and sku not in prices:
                    raise LookupError("unknown sku")
            return reference.quote_tool(request, prices)
        wrong.quote_tool = quote
        report = run_submission(wrong, "quote")
        self.assertFalse(report["passed"])
        self.assertTrue(any(row["check"] == "quote_schema_unknown_and_total_limit" and not row["passed"] for row in report["checks"]))

    def test_rejects_conflict_ignoring_idempotency(self):
        wrong = SimpleNamespace(**vars(reference))
        class BrokenBook(reference.LoanBook):
            def __init__(self, path):
                super().__init__(path)
                self.cached = {}

            def reserve(self, tenant, key, payload):
                identity = tenant, key
                if identity in self.cached:
                    return self.cached[identity]
                result = super().reserve(tenant, key, payload)
                self.cached[identity] = result
                return result
        wrong.LoanBook = BrokenBook
        report = run_submission(wrong, "transfer")
        self.assertFalse(report["passed"])
        self.assertTrue(any(row["check"] == "transfer_conflicting_payload" and not row["passed"] for row in report["checks"]))

    def test_loads_the_supplied_file(self):
        path = Path(__file__).resolve().parents[1] / "exercises" / "reference.py"
        student = load_submission(path)
        self.assertEqual(Path(student.__file__), path)
        self.assertTrue(run_submission(student, "inventory")["passed"])


if __name__ == "__main__":
    unittest.main()
