"""Run each self-contained interview reference solution and its behavioral tests."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
exercises = sorted((root / "docs" / "interview" / "exercises").glob("*.py"))
if not exercises:
    raise SystemExit("No exercise reference solutions found")
for exercise in exercises:
    print(f"Checking {exercise.name}", flush=True)
    subprocess.run([sys.executable, str(exercise)], cwd=root, check=True)
print(f"PASS: {len(exercises)} executable reference solutions.")
