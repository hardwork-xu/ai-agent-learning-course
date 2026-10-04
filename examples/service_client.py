"""Synthetic local service client; credentials stay in an ignored config file."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentlab.service import load_config
from agentlab.console import configure_utf8_output


def send(url, token, method, path, payload=None):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path not in {"", "/"}:
        raise ValueError("only_loopback_http_supported")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_url")
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(url.rstrip("/") + path, data=raw, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=65) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def main():
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("work/service-auth.json"))
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--as", dest="principal", default="campus-requester")
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser("query")
    query.add_argument("--question", default="退款审核期限是多少天？")
    create = commands.add_parser("create")
    create.add_argument("--query-id", required=True)
    create.add_argument("--request-id", required=True)
    create.add_argument("--subject", default="合成退款咨询")
    create.add_argument("--priority", choices=["P1", "P2", "P3"], default="P2")
    for name in ("show", "approve", "commit"):
        command = commands.add_parser(name)
        command.add_argument("--request-id", required=True)
        if name != "show":
            command.add_argument("--expected-version", required=True, type=int)
            command.add_argument("--expected-digest", required=True)
        if name == "approve":
            command.add_argument("--ttl-seconds", type=int, default=300)
        if name == "commit":
            command.add_argument("--idempotency-key", required=True)
    commands.add_parser("metrics")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        principal = next(p for p in config["principals"] if p["name"] == args.principal)
        if args.command == "query":
            method, path, body = "POST", "/v1/query", {"question": args.question}
        elif args.command == "create":
            method, path, body = "POST", "/v1/requests", {"query_id": args.query_id,
                "request_id": args.request_id, "subject": args.subject, "priority": args.priority}
        elif args.command == "metrics":
            method, path, body = "GET", "/v1/metrics", None
        else:
            path = "/v1/requests/" + args.request_id
            method, body = "GET", None
            if args.command != "show":
                method, path = "POST", path + "/" + args.command
                body = {"expected_version": args.expected_version, "expected_digest": args.expected_digest}
                if args.command == "approve":
                    body["ttl_seconds"] = args.ttl_seconds
                else:
                    body["idempotency_key"] = args.idempotency_key
        status, result = send(args.url, principal["token"], method, path, body)
        print(json.dumps({"http_status": status, **result}, ensure_ascii=False, indent=2))
        return 0 if status < 400 else 1
    except Exception:
        print(json.dumps({"error": "client_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
