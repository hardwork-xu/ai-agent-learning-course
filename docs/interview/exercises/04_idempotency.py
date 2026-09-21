"""Local transactional ticket creation; no external side effects or authentication."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class Conflict(ValueError):
    pass


def initialize(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (
            tenant TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
            result TEXT NOT NULL, PRIMARY KEY (tenant, key));
        """)


def create_ticket(path, tenant, key, payload):
    if any(type(x) is not str or not x.strip() for x in (tenant, key)):
        raise ValueError("tenant and key required")
    if type(payload) is not dict or set(payload) != {"title"}:
        raise ValueError("expected title")
    if type(payload["title"]) is not str or not 1 <= len(payload["title"].strip()) <= 100:
        raise ValueError("invalid title")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT digest,result FROM requests WHERE tenant=? AND key=?",
                         (tenant, key)).fetchone()
        if old:
            if old[0] != digest:
                raise Conflict("key reused for different payload")
            result = json.loads(old[1])
        else:
            cursor = db.execute("INSERT INTO tickets(tenant,title) VALUES (?,?)",
                                (tenant, payload["title"]))
            result = {"ticket_id": cursor.lastrowid, "status": "created"}
            db.execute("INSERT INTO requests VALUES (?,?,?,?)",
                       (tenant, key, digest, json.dumps(result, sort_keys=True)))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "store.db")
        initialize(self.path)

    def count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

    def test_replay_after_new_connection(self):
        first = create_ticket(self.path, "tenant-demo", "request-1", {"title": "Demo"})
        self.assertEqual(first, create_ticket(self.path, "tenant-demo", "request-1", {"title": "Demo"}))
        self.assertEqual(self.count(), 1)

    def test_changed_payload_rejected(self):
        create_ticket(self.path, "tenant-demo", "request-1", {"title": "Demo"})
        with self.assertRaises(Conflict):
            create_ticket(self.path, "tenant-demo", "request-1", {"title": "Changed"})
        self.assertEqual(self.count(), 1)

    def test_tenant_scoped_key(self):
        for tenant in ("tenant-a", "tenant-b"):
            create_ticket(self.path, tenant, "same-key", {"title": "Demo"})
        self.assertEqual(self.count(), 2)

    def test_concurrent_replays(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: create_ticket(
                self.path, "tenant-demo", "parallel", {"title": "Demo"}), range(8)))
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
