"""Copy an existing SQLite database to a NEW file using SQLite's backup API.

Stop application writes and outbox delivery before backing up both databases.
Two successful file backups are not a distributed atomic snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


class BackupError(ValueError):
    """Stable operator error codes, without file contents or private paths."""


def backup_database(source: str | Path, destination: str | Path, *, timeout: float = 30) -> dict:
    source, destination = Path(source), Path(destination)
    if type(timeout) not in (int, float) or not 0 < timeout <= 300:
        raise BackupError("invalid_timeout")
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError:
        raise BackupError("source_not_found") from None
    if not source.is_file():
        raise BackupError("source_not_file")
    # Resolve the parent, not the leaf: an existing destination symlink must be refused.
    try:
        parent = destination.parent.resolve(strict=True)
    except FileNotFoundError:
        raise BackupError("destination_parent_missing") from None
    destination = parent / destination.name
    opened_source = opened_target = None
    created_identity = None
    successful = False
    started = time.monotonic()
    try:
        # mode=ro prevents a misspelled source from silently becoming an empty database.
        opened_source = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            info = os.fstat(fd)
            created_identity = (info.st_dev, info.st_ino)
            if os.name == "posix":
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        opened_target = sqlite3.connect(destination.as_uri() + "?mode=rw", uri=True, timeout=5)

        def progress(_status, _remaining, _total):
            if time.monotonic() - started > timeout:
                raise BackupError("backup_timeout")

        opened_source.backup(opened_target, pages=64, progress=progress, sleep=0.01)
        rows = opened_target.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            raise BackupError("integrity_check_failed")
        opened_target.close()
        opened_target = None
        # Confirm SQLite did not replace the file whose exclusive creation we own.
        info = destination.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) != created_identity or not stat.S_ISREG(info.st_mode):
            raise BackupError("destination_changed")
        if os.name == "posix":
            destination.chmod(0o600)
        successful = True
        return {"status": "backup_created", "integrity_check": "ok", "bytes": info.st_size,
                "overwrote_existing": False}
    except FileExistsError:
        raise BackupError("destination_exists") from None
    except sqlite3.DatabaseError:
        raise BackupError("database_backup_failed") from None
    finally:
        if opened_target is not None:
            opened_target.close()
        if opened_source is not None:
            opened_source.close()
        if created_identity is not None and not successful:
            # Only remove the exact new file made by this invocation; never an older backup.
            try:
                info = destination.stat(follow_symlinks=False)
                if (info.st_dev, info.st_ino) == created_identity and stat.S_ISREG(info.st_mode):
                    destination.unlink()
            except OSError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(backup_database(args.source, args.destination)))
        return 0
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}))
    except Exception:
        print(json.dumps({"error": "backup_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
