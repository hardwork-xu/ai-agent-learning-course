"""Public contract tests; not a sandbox and not proof of independent authorship."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


TASKS = ("prerequisites", "inventory", "quote", "transfer")
REMEDIES = {
    "json": "docs/prerequisites.md：Python 与 JSON",
    "http": "docs/prerequisites.md：HTTP 与重试",
    "sql": "docs/prerequisites.md：SQL 与事务",
    "inventory": "docs/practice.md：第一关",
    "quote": "docs/practice.md：第二关",
    "transfer": "exercises/transfer-task.md：独立迁移任务",
}


def load_submission(path):
    """Import code with the current user's permissions, like running Python."""
    path = Path(path).resolve(strict=True)
    if path.suffix != ".py":
        raise ValueError("submission must be a Python file")
    spec = importlib.util.spec_from_file_location("learner_submission", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load submission")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ContractCases(unittest.TestCase):
    def __init__(self, methodName, submission):
        super().__init__(methodName)
        self.student = submission

    def test_json_normal(self):
        for units in (1, 7, 100):
            actual = self.student.parse_units('{"units": %d}' % units)
            self.assertIs(type(actual), int)
            self.assertEqual(actual, units)

    def test_json_bool_and_numeric_types(self):
        for raw in ('true', 'false', '"2"', '2.0', 'null', '[]', '{}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.student.parse_units('{"units": ' + raw + '}')

    def test_json_schema_and_bounds(self):
        for raw in ('{}', '[]', 'null', '{', '{"units": 0}', '{"units": 101}',
                    '{"units": 2, "admin": true}', '{"units": 1, "units": 2}',
                    None, b'{"units": 2}', bytearray(b'{"units": 2}'), {"units": 2}, 2):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.student.parse_units(raw)

    def test_http_read_and_idempotent_write(self):
        for status in (None, 429, 502, 503, 504):
            self.assertEqual(self.student.retry_action("GET", status, False), "retry")
            self.assertEqual(self.student.retry_action("POST", status, True), "retry")

    def test_http_unknown_write_and_permanent_error(self):
        for status in (None, 429, 502, 503, 504):
            self.assertEqual(self.student.retry_action("POST", status, False), "reconcile")
        for status in (200, 201, 400, 401, 403, 404, 409, 500):
            for method in ("GET", "POST"):
                self.assertEqual(self.student.retry_action(method, status, True), "stop")

    def test_http_invalid_input(self):
        for args in (("DELETE", 503, True), ("GET", True, True), ("GET", 99, True),
                     ("GET", 600, True), ("GET", "503", True), ("GET", 503, "yes")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.student.retry_action(*args)

    def test_sql_commit(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE diagnostic_events(label TEXT UNIQUE NOT NULL)")
            self.student.record_pair(connection, ("alpha", "beta"))
            self.assertFalse(connection.in_transaction, "success must commit")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM diagnostic_events").fetchone()[0], 2)

    def test_sql_rollback_second_failure(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE diagnostic_events(label TEXT UNIQUE NOT NULL)")
            connection.execute("INSERT INTO diagnostic_events VALUES ('exists')")
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                self.student.record_pair(connection, ("new-row", "exists"))
            self.assertFalse(connection.in_transaction, "failure must roll back")
            self.assertEqual(connection.execute("SELECT label FROM diagnostic_events").fetchall(), [("exists",)])

    def test_sql_parameter_binding(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE diagnostic_events(label TEXT UNIQUE NOT NULL)")
            label = "sample'); DROP TABLE diagnostic_events; --"
            self.student.record_pair(connection, (label, "safe"))
            self.assertEqual(connection.execute("SELECT label FROM diagnostic_events ORDER BY rowid").fetchall(),
                             [(label,), ("safe",)])

    def test_inventory_normal_and_zero_stock(self):
        catalog = {"PEN-001": 0, "BAG-017": 8}
        for sku, available in catalog.items():
            actual = self.student.inventory_tool({"name": "inventory.lookup", "arguments": {"sku": sku}}, catalog)
            self.assertEqual(actual, {"sku": sku, "available": available})
            self.assertIs(type(actual["available"]), int)
        self.assertEqual(catalog, {"PEN-001": 0, "BAG-017": 8}, "read-only tool must not change data")

    def test_inventory_exact_schema(self):
        for request in (None, [], {}, {"name": "inventory.lookup", "arguments": {"sku": "PEN-001"}, "admin": True},
                        {"name": "inventory.lookup", "arguments": {}},
                        {"name": "inventory.lookup", "arguments": {"sku": "PEN-001", "tenant": "other"}},
                        {"name": "inventory.delete", "arguments": {"sku": "PEN-001"}}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.student.inventory_tool(request, {"PEN-001": 3})

    def test_inventory_sku_and_unknown(self):
        for sku in (True, 123, "", "pen-001", "PEN-001\n", "../PEN-001"):
            with self.subTest(sku=sku), self.assertRaises(ValueError):
                self.student.inventory_tool({"name": "inventory.lookup", "arguments": {"sku": sku}}, {})
        with self.assertRaises(LookupError):
            self.student.inventory_tool({"name": "inventory.lookup", "arguments": {"sku": "BOX-999"}}, {})

    def test_quote_normal_and_changed_data(self):
        prices = {"PEN-001": 125, "BAG-017": 3980, "BOX-900": 10000}
        for sku, units in (("PEN-001", 3), ("BAG-017", 2), ("BOX-900", 100)):
            request = {"name": "inventory.quote", "arguments": {"sku": sku, "units": units}}
            actual = self.student.quote_tool(request, prices)
            self.assertEqual(actual, {"sku": sku, "units": units, "total_cents": prices[sku] * units})
            self.assertIs(type(actual["total_cents"]), int)
            self.assertIs(type(actual["units"]), int)
        self.assertEqual(prices, {"PEN-001": 125, "BAG-017": 3980, "BOX-900": 10000})

    def test_quote_bool_and_units_bounds(self):
        for units in (True, False, 0, -1, 101, 2.0, "2", None):
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.student.quote_tool({"name": "inventory.quote", "arguments": {"sku": "PEN-001", "units": units}}, {"PEN-001": 125})

    def test_quote_schema_unknown_and_total_limit(self):
        invalid = [None, {}, {"name": "inventory.quote", "arguments": {"sku": "PEN-001"}},
                   {"name": "inventory.quote", "arguments": {"sku": "PEN-001", "units": 1, "price": 0}},
                   {"name": "inventory.lookup", "arguments": {"sku": "PEN-001", "units": 1}},
                   {"name": "inventory.quote", "arguments": {"sku": "PEN-001", "units": 1}, "admin": True},
                   {"name": "inventory.quote", "arguments": {"sku": True, "units": 1}}]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.student.quote_tool(request, {"PEN-001": 125})
        for sku in ("", "pen-001", "PEN-001\n", "../PEN-001"):
            with self.subTest(sku=sku), self.assertRaises(ValueError):
                self.student.quote_tool({"name": "inventory.quote", "arguments": {"sku": sku, "units": 1}}, {"PEN-001": 125})
        with self.assertRaises(LookupError):
            self.student.quote_tool({"name": "inventory.quote", "arguments": {"sku": "BOX-999", "units": 1}}, {})
        with self.assertRaises(ValueError):
            self.student.quote_tool({"name": "inventory.quote", "arguments": {"sku": "BOX-900", "units": 100}}, {"BOX-900": 10001})

    def test_transfer_durable_replay_and_tenant_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "loans.sqlite")
            first = self.student.LoanBook(path).reserve("group_a", "key1", {"asset_id": "CA-001", "days": 3})
            self.assertEqual(set(first), {"request_id", "asset_id", "days", "state"})
            self.assertIs(type(first["request_id"]), int)
            self.assertIs(type(first["days"]), int)
            self.assertGreater(first["request_id"], 0)
            self.assertEqual({k: v for k, v in first.items() if k != "request_id"}, {"asset_id": "CA-001", "days": 3, "state": "pending"})
            replay = self.student.LoanBook(path).reserve("group_a", "key1", {"days": 3, "asset_id": "CA-001"})
            self.assertEqual(first, replay, "reconnect and field order must preserve identity")
            other = self.student.LoanBook(path).reserve("group_b", "key1", {"asset_id": "CA-001", "days": 3})
            self.assertNotEqual(first["request_id"], other["request_id"])
            new_key = self.student.LoanBook(path).reserve("group_a", "key2", {"asset_id": "CA-001", "days": 3})
            self.assertNotEqual(first["request_id"], new_key["request_id"], "new key means a new operation")
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 3)

    def test_transfer_conflicting_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "loans.sqlite")
            book = self.student.LoanBook(path)
            first = book.reserve("group_a", "same-key", {"asset_id": "CA-001", "days": 3})
            for payload in ({"asset_id": "CA-001", "days": 4}, {"asset_id": "MI-013", "days": 3}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    book.reserve("group_a", "same-key", payload)
            self.assertEqual(book.reserve("group_a", "same-key", {"asset_id": "CA-001", "days": 3}), first)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_transfer_invalid_schema_no_writes(self):
        invalid_payloads = [None, {}, {"asset_id": "CA-001", "days": 1, "approved": True},
                            {"asset_id": "CA-001", "days": True}, {"asset_id": "CA-001", "days": 1.0},
                            {"asset_id": "CA-001", "days": "1"}, {"asset_id": "CA-001", "days": 0},
                            {"asset_id": "CA-001", "days": 15}, {"asset_id": "ca-001", "days": 1},
                            {"asset_id": "CA-001\n", "days": 1},
                            {"asset_id": True, "days": 1}]
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "loans.sqlite")
            book = self.student.LoanBook(path)
            for payload in invalid_payloads:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    book.reserve("group_a", "key1", payload)
            for tenant, key in (("", "k"), ("group_a", ""), (True, "k"), ("group_a", True),
                                ("group_a", "x" * 65), ("a/b", "k"), ("group_a", "k\n")):
                with self.subTest(tenant=tenant, key=key), self.assertRaises(ValueError):
                    book.reserve(tenant, key, {"asset_id": "CA-001", "days": 1})
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0], 0)

    def test_transfer_concurrent_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "loans.sqlite")
            book = self.student.LoanBook(path)
            def call(_):
                return book.reserve("group_a", "parallel", {"asset_id": "MI-013", "days": 14})
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(call, range(8)))
            self.assertTrue(all(result == results[0] for result in results))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0], 1)

    def test_transfer_rollback_when_recording_key_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "loans.sqlite")
            book = self.student.LoanBook(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("""CREATE TRIGGER fail_key BEFORE INSERT ON idempotency
                    BEGIN SELECT RAISE(ABORT, 'injected key-recording failure'); END""")
            with self.assertRaises(sqlite3.DatabaseError):
                book.reserve("group_a", "rollback", {"asset_id": "CA-001", "days": 2})
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0,
                                 "request and idempotency record must commit together")


def run_submission(submission, task="all"):
    if task not in (*TASKS, "all"):
        raise ValueError("unknown task")
    prefixes = ("json", "http", "sql") if task == "prerequisites" else (task,)
    checks = []
    for name in unittest.defaultTestLoader.getTestCaseNames(ContractCases):
        group = name.split("_")[1]
        if task != "all" and group not in prefixes:
            continue
        result = unittest.TestResult()
        ContractCases(name, submission).run(result)
        failures = result.failures + result.errors
        reason = failures[0][1].strip().splitlines()[-1] if failures else ""
        if result.skipped:
            reason = "Skipped: " + str(result.skipped[0][1])
        passed = result.wasSuccessful() and not result.skipped and result.testsRun == 1
        checks.append({"check": name.removeprefix("test_"), "passed": bool(passed),
                       "detail": reason[:240], "remedy": REMEDIES[group]})
    return {"task": task, "passed": all(item["passed"] for item in checks),
            "passed_checks": sum(item["passed"] for item in checks), "total_checks": len(checks),
            "checks": checks}
