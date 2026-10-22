"""Versioned Markdown knowledge index; no network or model access.

Each operation owns its SQLite connection. Writers serialize on BEGIN IMMEDIATE;
readers observe one committed snapshot. Tenant filtering precedes BM25 statistics.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata

MAX_CONTENT = 128_000
MAX_DOCUMENTS = 500
MAX_CHUNKS = 600
CHUNK_SIZE = 1_000
SCHEMA_VERSION = 1


class KnowledgeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _string(value, name, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        raise KnowledgeError("invalid_" + name)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise KnowledgeError("invalid_" + name) from None
    return value


def _identity(value, name):
    _string(value, name, 100)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise KnowledgeError("invalid_" + name)
    return value


def _revision(value, *, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise KnowledgeError("invalid_revision")


def terms(text):
    """Unicode-normalized Latin words and overlapping Han bigrams.

    No synonyms or hand-curated question aliases; weak lexical coverage is exposed
    in the diagnostic report instead of being hidden by expected-answer routing.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    result = re.findall(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", text)
    for span in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(span[i:i + 2] for i in range(len(span) - 1))
        if len(span) == 1:
            result.append(span)
    return result


def chunk_markdown(content):
    """Split on headings outside fenced code, then bounded paragraph chunks.

    Text is kept verbatim apart from surrounding whitespace. There is no semantic
    chunking claim. Every split carries the complete current heading hierarchy.
    """
    sections = []
    heading = []
    buffer = []
    fence = None
    for line in content.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            char = marker[1][0]
            if fence is None:
                fence = (char, len(marker[1]))
            elif fence[0] == char and len(marker[1]) >= fence[1]:
                fence = None
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None and not marker else None
        if match:
            if "\n".join(buffer).strip():
                sections.append((" / ".join(heading), "\n".join(buffer).strip()))
            level, name = len(match[1]), match[2]
            heading = heading[:level - 1] + [name]
            buffer = []
        else:
            buffer.append(line)
    if "\n".join(buffer).strip():
        sections.append((" / ".join(heading), "\n".join(buffer).strip()))
    chunks = []
    for label, body in sections:
        current = ""
        for paragraph in re.split(r"\n\s*\n", body):
            if not paragraph:
                continue
            if len(current) + len(paragraph) + 2 <= CHUNK_SIZE:
                current += ("\n\n" if current else "") + paragraph
                continue
            if current:
                chunks.append((label, current))
                current = ""
            while len(paragraph) > CHUNK_SIZE:
                # Prefer a paragraph/sentence boundary in the latter half.
                cut = max(paragraph.rfind(p, CHUNK_SIZE // 2, CHUNK_SIZE) for p in ("。", "；", "\n", ". "))
                cut = cut + 1 if cut >= CHUNK_SIZE // 2 else CHUNK_SIZE
                chunks.append((label, paragraph[:cut].strip()))
                paragraph = paragraph[cut:].lstrip()
            current = paragraph
        if current:
            chunks.append((label, current))
    return chunks


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, SCHEMA_VERSION):
                    raise KnowledgeError("unsupported_schema")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS documents (
                        tenant TEXT NOT NULL, document_id TEXT NOT NULL,
                        revision TEXT NOT NULL, active INTEGER NOT NULL,
                        PRIMARY KEY (tenant, document_id)
                    );
                    CREATE TABLE IF NOT EXISTS versions (
                        tenant TEXT NOT NULL, document_id TEXT NOT NULL,
                        revision TEXT NOT NULL, parent_revision TEXT,
                        title TEXT NOT NULL, content TEXT NOT NULL,
                        source TEXT NOT NULL, license TEXT NOT NULL,
                        active INTEGER NOT NULL, created_at TEXT NOT NULL,
                        PRIMARY KEY (tenant, document_id, revision)
                    );
                    CREATE TABLE IF NOT EXISTS chunks (
                        tenant TEXT NOT NULL, document_id TEXT NOT NULL,
                        revision TEXT NOT NULL, id TEXT NOT NULL,
                        position INTEGER NOT NULL, heading TEXT NOT NULL,
                        text TEXT NOT NULL, terms_json TEXT NOT NULL,
                        token_count INTEGER NOT NULL,
                        PRIMARY KEY (tenant, id)
                    );
                    CREATE INDEX IF NOT EXISTS chunk_revision
                        ON chunks (tenant, document_id, revision);
                """)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except (OSError, sqlite3.Error):
            raise KnowledgeError("storage_unavailable") from None

    @contextmanager
    def _connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
        except sqlite3.Error:
            raise KnowledgeError("storage_unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def validation_guard(self):
        """Fence source mutation while an approval/commit is validated.

        Lock order is knowledge database then run database. This does not make
        two database writes atomic; no knowledge write is made inside the guard.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            finally:
                connection.rollback()

    @staticmethod
    def _current(connection, tenant, document_id):
        return connection.execute("""SELECT v.* FROM documents d JOIN versions v
            ON v.tenant=d.tenant AND v.document_id=d.document_id AND v.revision=d.revision
            WHERE d.tenant=? AND d.document_id=?""", (tenant, document_id)).fetchone()

    @staticmethod
    def _metadata(row):
        return {key: (bool(row[key]) if key == "active" else row[key]) for key in
                ("document_id", "revision", "title", "source", "license", "active", "created_at")}

    def ingest(self, *, tenant, document_id, title, content, source, license, expected_revision=None):
        _identity(tenant, "tenant")
        _identity(document_id, "document_id")
        _string(title, "title", 200)
        _string(content, "content", MAX_CONTENT)
        _string(source, "source", 2048)
        _string(license, "license", 128)
        _revision(expected_revision, optional=True)
        chunks = chunk_markdown(content)
        if not chunks or len(chunks) > MAX_CHUNKS:
            raise KnowledgeError("invalid_chunk_count")
        payload = {"title": title, "content": content, "source": source, "license": license}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, tenant, document_id)
            if (current is None and expected_revision is not None) or (
                current is not None and current["revision"] != expected_revision
            ):
                raise KnowledgeError("revision_conflict")
            if current is None:
                count = connection.execute("SELECT count(*) FROM documents WHERE tenant=?", (tenant,)).fetchone()[0]
                if count >= MAX_DOCUMENTS:
                    raise KnowledgeError("document_capacity")
            if current is not None and current["active"] and all(current[key] == value for key, value in payload.items()):
                connection.commit()
                return self._metadata(current)
            revision = _hash({"tenant": tenant, "document_id": document_id,
                              "parent": expected_revision, "active": True, **payload})
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tenant, document_id, revision, expected_revision, title, content, source, license, 1, now))
            connection.execute("INSERT INTO documents VALUES (?,?,?,1) ON CONFLICT(tenant,document_id) DO UPDATE SET revision=excluded.revision,active=1",
                               (tenant, document_id, revision))
            for position, (heading, text) in enumerate(chunks):
                counts = Counter(terms(title + " " + heading + " " + text))
                chunk_id = _hash({"tenant": tenant, "document_id": document_id, "revision": revision, "position": position})
                connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)", (tenant, document_id, revision, chunk_id,
                    position, heading, text, json.dumps(counts, ensure_ascii=False), sum(counts.values())))
            result = self._metadata(self._current(connection, tenant, document_id))
            connection.commit()
            return result

    def withdraw(self, *, tenant, document_id, expected_revision):
        _identity(tenant, "tenant")
        _identity(document_id, "document_id")
        _revision(expected_revision)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, tenant, document_id)
            if current is None or current["revision"] != expected_revision:
                raise KnowledgeError("revision_conflict")
            if not current["active"]:
                connection.commit()
                return self._metadata(current)
            revision = _hash({"tenant": tenant, "document_id": document_id, "parent": expected_revision, "active": False})
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?)", (tenant, document_id, revision, expected_revision,
                current["title"], current["content"], current["source"], current["license"], 0, now))
            connection.execute("UPDATE documents SET revision=?,active=0 WHERE tenant=? AND document_id=?", (revision, tenant, document_id))
            result = self._metadata(self._current(connection, tenant, document_id))
            connection.commit()
            return result

    def list_documents(self, tenant):
        _identity(tenant, "tenant")
        with self._connect() as connection:
            rows = connection.execute("""SELECT v.* FROM documents d JOIN versions v
                ON v.tenant=d.tenant AND v.document_id=d.document_id AND v.revision=d.revision
                WHERE d.tenant=? ORDER BY d.document_id""", (tenant,)).fetchall()
            return [self._metadata(row) for row in rows]

    @staticmethod
    def _evidence(row, score=None):
        result = {key: row[key] for key in ("id", "document_id", "revision", "title", "text", "source", "heading")}
        if score is not None:
            result["score"] = round(score, 8)
        return result

    def get(self, tenant, chunk_id):
        _identity(tenant, "tenant")
        if not isinstance(chunk_id, str) or not re.fullmatch(r"[0-9a-f]{64}", chunk_id):
            return None
        with self._connect() as connection:
            row = connection.execute("""SELECT c.*,v.title,v.source FROM chunks c
                JOIN documents d ON d.tenant=c.tenant AND d.document_id=c.document_id AND d.revision=c.revision
                JOIN versions v ON v.tenant=c.tenant AND v.document_id=c.document_id AND v.revision=c.revision
                WHERE c.tenant=? AND c.id=? AND d.active=1""", (tenant, chunk_id)).fetchone()
            return self._evidence(row) if row else None

    def search(self, tenant, query, limit=5):
        _identity(tenant, "tenant")
        _string(query, "query", 4_000)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise KnowledgeError("invalid_limit")
        query_terms = set(terms(query))
        if not query_terms:
            return []
        with self._connect() as connection:
            # One SQL statement gives a consistent snapshot, filtered before any
            # relevance statistics. Old/withdrawn/other-tenant text never ranks.
            rows = connection.execute("""SELECT c.*,v.title,v.source FROM chunks c
                JOIN documents d ON d.tenant=c.tenant AND d.document_id=c.document_id AND d.revision=c.revision
                JOIN versions v ON v.tenant=c.tenant AND v.document_id=c.document_id AND v.revision=c.revision
                WHERE c.tenant=? AND d.active=1""", (tenant,)).fetchall()
        if not rows:
            return []
        counts = [json.loads(row["terms_json"]) for row in rows]
        frequencies = Counter(term for count in counts for term in query_terms if term in count)
        average = sum(row["token_count"] for row in rows) / len(rows) or 1
        ranked = []
        for row, count in zip(rows, counts):
            score = 0.0
            for term in query_terms:
                frequency = count.get(term, 0)
                if frequency:
                    idf = math.log(1 + (len(rows) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                    score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * row["token_count"] / average))
            if score > 0:
                ranked.append(self._evidence(row, score))
        return sorted(ranked, key=lambda row: (-row["score"], row["document_id"], row["id"]))[:limit]

    def seed_demo(self, tenant):
        """Explicit demo import. Existing modifications are never overwritten."""
        _identity(tenant, "tenant")
        root = files("agentlab.desk").joinpath("documents")
        manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
        imported, skipped = [], []
        existing = {row["document_id"] for row in self.list_documents(tenant)}
        for item in manifest["documents"]:
            if item["id"] in existing:
                skipped.append(item["id"])
                continue
            try:
                document = self.ingest(tenant=tenant, document_id=item["id"], title=item["title"],
                    content=root.joinpath(item["file"]).read_text(encoding="utf-8"),
                    source=item["source"], license="MIT")
                if item.get("withdrawn"):
                    document = self.withdraw(tenant=tenant, document_id=item["id"], expected_revision=document["revision"])
                imported.append(document)
            except KnowledgeError as error:
                if error.code != "revision_conflict":
                    raise
                skipped.append(item["id"])
        return {"imported": imported, "skipped": skipped, "count": len(imported), "synthetic": True}
