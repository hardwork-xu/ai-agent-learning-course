"""Check repository-local Markdown links, anchors, encoding and code fences."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "work", "__pycache__", "build", "dist"}


def markdown_files():
    return sorted(p for p in ROOT.rglob("*.md") if not SKIP.intersection(p.relative_to(ROOT).parts))


def body_and_headings(text):
    fence = None
    body = []
    slugs = set()
    counts = {}
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        body.append(line)
        heading = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if heading:
            value = re.sub(r"[^\w\- ]", "", heading[1].lower()).replace(" ", "-")
            count = counts.get(value, 0)
            counts[value] = count + 1
            slugs.add(f"{value}-{count}" if count else value)
    return "\n".join(body), slugs, fence


def main():
    errors = []
    count = 0
    files = markdown_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        label = path.relative_to(ROOT)
        if "\ufffd" in text:
            errors.append(f"{label}: replacement character")
        body, _, fence = body_and_headings(text)
        if fence:
            errors.append(f"{label}: unclosed code fence")
        for match in re.finditer(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", body):
            raw = match[1].strip().split(' "', 1)[0].strip("<>")
            if urlsplit(raw).scheme or raw.startswith("//"):
                continue
            url = urlsplit(unquote(raw))
            target = (path.parent / url.path).resolve() if url.path else path
            count += 1
            if not target.is_relative_to(ROOT):
                errors.append(f"{label}: link escapes repository: {raw}")
            elif not target.exists():
                errors.append(f"{label}: missing link: {raw}")
            elif url.fragment and target.suffix == ".md":
                _, anchors, _ = body_and_headings(target.read_text(encoding="utf-8"))
                if url.fragment not in anchors:
                    errors.append(f"{label}: missing anchor: {raw}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(files)} Markdown files, {count} local links; fences and encoding valid.")


if __name__ == "__main__":
    main()
