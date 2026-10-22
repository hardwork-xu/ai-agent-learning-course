"""CaseDesk command line: explicit initialization, serving and recovery."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agentlab.console import configure_utf8_output
from agentlab.service import init_config, load_config


def main(argv=None):
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "deliver", "backup"):
        item = sub.add_parser(name)
        item.add_argument("--data-dir", type=Path, default=Path("work/casedesk"))
        if name == "init":
            item.add_argument("--demo", action="store_true", help="import original synthetic support policies")
        elif name == "serve":
            item.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0"])
            item.add_argument("--port", type=int, default=8766)
            item.add_argument("--allow-host", action="append", default=[])
            item.add_argument("--model", help="explicit locally installed Ollama tag")
            item.add_argument("--protocol", choices=["original", "observed-evidence-v1", "observed-evidence-v2"], default="observed-evidence-v2")
            item.add_argument("--think", choices=["default", "true", "false"], default="false")
            item.add_argument("--workers", type=int, default=2, help="bounded model workers, not ASGI processes")
            item.add_argument("--capacity", type=int, default=16)
        elif name == "deliver":
            item.add_argument("--downstream", type=Path, required=True)
        else:
            item.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .knowledge import KnowledgeStore
        from .store import DeskError, Store
        if args.command == "init":
            args.data_dir.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                args.data_dir.chmod(0o700)
            # Never overwrite an existing credential file or print its contents.
            config = args.data_dir / "auth.json"
            if not config.exists():
                init_config(config)
            settings = load_config(config)
            Store(args.data_dir / "desk.sqlite3")
            knowledge = KnowledgeStore(args.data_dir / "knowledge.sqlite3")
            if args.demo:
                for tenant in sorted({p["tenant"] for p in settings["principals"]}):
                    knowledge.seed_demo(tenant)
            print(json.dumps({"status": "initialized", "demo": args.demo, "secrets_printed": False}))
        elif args.command == "serve":
            import uvicorn
            from .api import create_app
            if not 0 < args.port < 65536 or not 1 <= args.workers <= 8 or not 1 <= args.capacity <= 256:
                raise DeskError("invalid_limits")
            hosts = ["127.0.0.1", "localhost"] + args.allow_host
            if any(not host or host == "*" or "/" in host for host in hosts):
                raise DeskError("invalid_hosts")
            thinking = None if args.think == "default" else args.think == "true"
            app = create_app(data_dir=args.data_dir, model=args.model, protocol=args.protocol, think=thinking,
                             workers=args.workers, capacity=args.capacity, allowed_hosts=hosts)
            uvicorn.run(app, host=args.host, port=args.port, access_log=False,
                        log_level="warning", limit_concurrency=100,
                        timeout_keep_alive=5, timeout_graceful_shutdown=5)
        else:
            settings = load_config(args.data_dir / "auth.json")
            if args.command == "deliver":
                if not settings["write_enabled"]:
                    raise DeskError("writes_disabled")
                print(json.dumps(Store(args.data_dir / "desk.sqlite3").deliver(args.downstream)))
            else:
                if settings["write_enabled"]:
                    raise DeskError("disable_writes_and_stop_service_before_backup")
                # Two independent snapshots require the documented stopped-service
                # operation; this command does not pretend they are atomic together.
                if args.destination.exists():
                    raise DeskError("destination_exists")
                args.destination.mkdir(parents=True, mode=0o700)
                import sqlite3
                from contextlib import closing
                try:
                    for name in ("desk.sqlite3", "knowledge.sqlite3"):
                        source = args.data_dir / name
                        if not source.is_file():
                            raise DeskError("database_missing")
                        target = args.destination / name
                        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
                            with closing(sqlite3.connect(target)) as dst:
                                src.backup(dst)
                                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                    raise DeskError("snapshot_integrity")
                        if os.name == "posix":
                            target.chmod(0o600)
                    (args.destination / "manifest.json").write_text(json.dumps({
                        "schema": 1, "files": ["desk.sqlite3", "knowledge.sqlite3"],
                        "credentials_included": False, "requires_stopped_service": True}) + "\n")
                except Exception:
                    # Keep incomplete output for diagnosis; it has no manifest.
                    raise
                print(json.dumps({"status": "snapshot_created", "credentials_included": False}))
        return 0
    except ImportError:
        print(json.dumps({"error": "desk_dependencies_missing", "install": "pip install -e '.[desk]'"}))
        return 1
    except Exception as error:
        code = getattr(error, "code", "operation_failed")
        print(json.dumps({"error": code, "command": args.command}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
