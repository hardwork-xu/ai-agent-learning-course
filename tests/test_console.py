"""Exercise real CLI bytes under the legacy encoding used by Windows pipes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from agentlab.console import configure_utf8_output
from agentlab.service import Application, init_config, make_server

ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    def run_cli(self, *arguments, expected=0):
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0", PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stderr.decode("utf-8", errors="replace"))
        # Strict decoding ensures actual bytes are UTF-8, not merely ASCII escaping
        # or output read with the subprocess caller's platform-default codec.
        return result.stdout.decode("utf-8"), result.stderr.decode("utf-8")

    def test_importing_modules_does_not_reconfigure_library_callers(self):
        code = (
            "import sys, runpy; before = (sys.stdout.encoding, sys.stderr.encoding); "
            "import agentlab.console, agentlab.__main__, agentlab.rag_eval, agentlab.service; "
            "runpy.run_path('scripts/grade.py', run_name='imported_script'); "
            "assert before == (sys.stdout.encoding, sys.stderr.encoding); print(before[0])"
        )
        stdout, _ = self.run_cli("-c", code)
        self.assertEqual(stdout.strip().lower(), "cp1252")

    def test_stringio_capture_remains_usable(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            configure_utf8_output()
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
            print("中文结果")
            print("中文错误", file=sys.stderr)
        self.assertEqual(stdout.getvalue(), "中文结果\n")
        self.assertEqual(stderr.getvalue(), "中文错误\n")

    def test_legacy_rag_cli_prints_chinese_refusal(self):
        stdout, _ = self.run_cli("-m", "agentlab", "demo", "rag")
        result = json.loads(stdout)
        self.assertTrue(result["results"][1]["abstained"])
        self.assertIn("资料不足", stdout)

    def test_policy_cli_prints_chinese_answer(self):
        stdout, _ = self.run_cli("examples/ask_policy.py", "退款审核期限是多少天？")
        self.assertFalse(json.loads(stdout)["abstained"])
        self.assertIn("七个工作日", stdout)

    def test_grader_failure_preserves_expected_exit_and_chinese_feedback(self):
        stdout, _ = self.run_cli("scripts/grade.py", "--submission", "exercises/starter.py",
                                 "--task", "prerequisites", expected=1)
        self.assertIn("0/9 checks passed", stdout)
        self.assertIn("补课入口", stdout)

    def test_grader_load_error_prints_chinese_path_to_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "不存在的答案.py"
            _, stderr = self.run_cli("scripts/grade.py", "--submission", str(missing), expected=2)
        self.assertIn("LOAD FAILED", stderr)
        self.assertIn("不存在的答案.py", stderr)

    def test_rag_eval_prints_unicode_output_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "中文评测.json"
            stdout, _ = self.run_cli("-m", "agentlab.rag_eval", "--output", str(output))
            self.assertEqual(json.loads(stdout)["output"], str(output))
            self.assertIn("中文评测", stdout)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["dataset_counts"]["test"], 30)

    def test_service_client_prints_real_http_chinese_response(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "auth.json"
            init_config(config)
            application = Application(config, Path(directory) / "service.sqlite3")
            server = make_server(application, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            worker.start()
            try:
                stdout, _ = self.run_cli("examples/service_client.py", "--config", str(config),
                                         "--url", f"http://127.0.0.1:{server.server_port}", "query")
                self.assertEqual(json.loads(stdout)["http_status"], 200)
                self.assertIn("七个工作日", stdout)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
