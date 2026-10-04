import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentlab.service import Application, deliver_outbox, init_config
from agentlab.workflow import TicketWorkflow
from scripts.backup_service import BackupError, backup_database, main


PAYLOAD = {"subject": "合成申请", "body": "只用于备份与恢复练习", "priority": "P2"}


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "service.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        with TicketWorkflow(self.source, outbox=True) as workflow:
            workflow.create("campus", "request-1", PAYLOAD, creator="requester-1")
            view = workflow.read("campus", "request-1")
            token = workflow.approve("campus", "request-1", reviewer="reviewer-1",
                expected_version=view["version"], expected_digest=view["digest"])
            return workflow.execute("campus", "request-1", approval_token=token, idempotency_key="operation-1")

    def test_existing_destination_is_refused_and_preserved(self):
        self.seed()
        destination = self.root / "already-exists.sqlite3"
        before = b"previous backup must survive"
        destination.write_bytes(before)
        with self.assertRaisesRegex(BackupError, "destination_exists"):
            backup_database(self.source, destination)
        self.assertEqual(destination.read_bytes(), before)
        source_before = self.source.read_bytes()
        with self.assertRaisesRegex(BackupError, "destination_exists"):
            backup_database(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_missing_source_does_not_create_source_or_destination(self):
        destination = self.root / "new.sqlite3"
        with self.assertRaisesRegex(BackupError, "source_not_found"):
            backup_database(self.source, destination)
        self.assertFalse(self.source.exists())
        self.assertFalse(destination.exists())

    def test_corrupt_source_removes_only_new_partial_destination(self):
        self.source.write_bytes(b"This is a synthetic non-SQLite file")
        destination = self.root / "partial.sqlite3"
        with self.assertRaisesRegex(BackupError, "database_backup_failed"):
            backup_database(self.source, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(self.source.read_bytes(), b"This is a synthetic non-SQLite file")

    def test_uri_paths_permissions_and_safe_cli_output(self):
        self.seed()
        # Windows forbids '?'; spaces and '#' still exercise URI escaping there.
        suffix = " ? #.sqlite3" if os.name == "posix" else " # with spaces.sqlite3"
        oddly_named = self.root / ("source" + suffix)
        self.source.rename(oddly_named)
        destination = self.root / ("restored" + suffix)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--source", str(oddly_named), "--destination", str(destination)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["integrity_check"], "ok")
        self.assertNotIn(str(self.root), output.getvalue())
        if os.name == "posix":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--source", str(oddly_named), "--destination", str(destination)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "destination_exists")
        self.assertNotIn(str(self.root), output.getvalue())

    def test_both_databases_restore_then_replay_preserves_one_effect(self):
        committed = self.seed()
        downstream = self.root / "downstream.sqlite3"
        with self.assertRaisesRegex(RuntimeError, "simulated_crash_after_effect"):
            deliver_outbox(self.source, downstream, crash_after_effect=True)
        service_backup, downstream_backup = self.root / "service-backup.sqlite3", self.root / "downstream-backup.sqlite3"
        # Writers and delivery are stopped; both databases are backed up as a pair.
        backup_database(self.source, service_backup)
        backup_database(downstream, downstream_backup)
        service_restored, downstream_restored = self.root / "service-restored.sqlite3", self.root / "downstream-restored.sqlite3"
        backup_database(service_backup, service_restored)
        backup_database(downstream_backup, downstream_restored)
        config = self.root / "auth.json"
        init_config(config)
        Application(config, service_restored)  # Restored state opens through the real service schema.
        with TicketWorkflow(service_restored, outbox=True) as workflow:
            self.assertEqual(workflow.read("campus", "request-1")["state"], "committed")
            replay = workflow.execute("campus", "request-1", approval_token="", idempotency_key="operation-1")
            self.assertEqual(replay, committed)
        self.assertEqual(deliver_outbox(service_restored, downstream_restored)["delivered"], 1)
        self.assertEqual(deliver_outbox(service_restored, downstream_restored)["delivered"], 0)
        with contextlib.closing(sqlite3.connect(downstream_restored)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM received_tickets").fetchone()[0], 1)
        # Original files remain the original fault state; restore did not overwrite them.
        with contextlib.closing(sqlite3.connect(self.source)) as connection:
            self.assertEqual(connection.execute("SELECT state FROM outbox").fetchone()[0], "pending")

    def test_wal_content_is_backed_up_through_sqlite_not_raw_file_copy(self):
        with contextlib.closing(sqlite3.connect(self.source)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE data(value TEXT)")
            connection.execute("INSERT INTO data VALUES ('committed in WAL')")
            connection.commit()
            destination = self.root / "wal-backup.sqlite3"
            backup_database(self.source, destination)
            with contextlib.closing(sqlite3.connect(destination)) as restored:
                self.assertEqual(restored.execute("SELECT value FROM data").fetchone()[0], "committed in WAL")


if __name__ == "__main__":
    unittest.main()
