"""Conservative publication checks; manual review is still required."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "work", "__pycache__", "build", "dist"}
PATTERNS = {
    "machine home path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "Windows home path": re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider credential": re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,})"),
    "assistant narration": re.compile(r"作为(?:一个|一名)?(?:AI|人工智能助手|语言模型)"),
}


def main():
    errors = []
    count = 0
    for path in sorted(ROOT.rglob("*")):
        parts = path.relative_to(ROOT).parts
        if not path.is_file() or SKIP.intersection(parts) or any(p.endswith(".egg-info") for p in parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            errors.append(f"{path.relative_to(ROOT)}: environment file")
            continue
        if path.suffix in {".pem", ".key", ".sqlite", ".db"}:
            errors.append(f"{path.relative_to(ROOT)}: sensitive file type")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append(f"{path.relative_to(ROOT)}: binary file requires manual review")
            continue
        count += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{path.relative_to(ROOT)}: {label}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {count} text files; no configured publication patterns found.")


if __name__ == "__main__":
    main()
