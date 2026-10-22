"""A missing model must produce incomplete evidence, never a passing comparison."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


class ModelRunnerTests(unittest.TestCase):
    def test_missing_daemon_preserves_failure_and_refuses_report_overwrite(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory, socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))  # Reserved but not listening: deterministic refusal.
            output = Path(directory) / "evidence.json"
            command = [sys.executable, str(repo / "scripts/evaluate_desk_model.py"),
                       "--model", "not-installed-test-fixture", "--base-url", f"http://127.0.0.1:{bound.getsockname()[1]}",
                       "--tasks", "cha-08", "--no-operational", "--total-timeout", "5", "--output", str(output)]
            env = dict(os.environ, PYTHONPATH=str(repo / "src"))
            result = subprocess.run(command, cwd=repo, env=env, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr.decode("utf-8", errors="replace"))
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["run_status"], "incomplete")
            self.assertEqual(report["model_preflight"]["status"], "failed")
            self.assertEqual(len(report["systems"]["baseline"]["cases"]), 1)
            self.assertEqual(report["systems"]["local"]["cases"], [])
            self.assertEqual(report["systems"]["local"]["not_run"], [{"id": "cha-08", "reason": "model_preflight_failed"}])
            self.assertIsNone(report["systems"]["local"]["summary"]["business_task_success"])
            self.assertEqual(report["systems"]["local"]["candidate_evidence_checks"]["missing_predictions"], 1)
            self.assertFalse(report["business_value_validated"])
            before = output.read_bytes()
            retry = subprocess.run(command, cwd=repo, env=env, capture_output=True, timeout=15)
            self.assertEqual(retry.returncode, 2)
            self.assertIn(b"output_exists", retry.stderr)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
