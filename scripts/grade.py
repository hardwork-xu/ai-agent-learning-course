"""Grade one trusted local student file; this command is not a sandbox."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from agentlab.console import configure_utf8_output
from exercises.grading import TASKS, load_submission, run_submission


def main():
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path, required=True, help="trusted local Python file")
    parser.add_argument("--task", choices=(*TASKS, "all"), default="all")
    parser.add_argument("--report", type=Path, help="optional local JSON report; use work/ to keep it private")
    args = parser.parse_args()
    if args.report and args.report.resolve() == args.submission.resolve():
        parser.error("report path must not overwrite the submission")
    try:
        raw = args.submission.read_bytes()
        module = load_submission(args.submission)
    except Exception as exc:
        print(f"LOAD FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = run_submission(module, args.task)
    role = "reference" if args.submission.resolve() == ROOT / "exercises/reference.py" else "learner_candidate"
    report.update({"submission": args.submission.name, "sha256": hashlib.sha256(raw).hexdigest(), "source_role": role,
                   "scope": "public contract checks; independent authorship and mastery are not verified"})
    for check in report["checks"]:
        print(f"{'PASS' if check['passed'] else 'FAIL'} {check['check']}")
        if not check["passed"]:
            print(f"  {check['detail']}\n  补课入口：{check['remedy']}")
    print(f"{report['passed_checks']}/{report['total_checks']} checks passed; source_role={role}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Local report written.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
