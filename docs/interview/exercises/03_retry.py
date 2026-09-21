"""Retry controller with injected clock/sleep; run this file for offline tests."""
import math
import unittest


class TransientError(Exception):
    pass


class DeadlineExceeded(TimeoutError):
    pass


def retry(operation, *, clock, sleep, timeout=3.0, attempts=3, base_delay=0.25):
    """operation(remaining_seconds) must enforce its own per-call timeout.

    This synchronous controller cannot interrupt an operation that hangs.
    It is intended for read-only or independently idempotent operations.
    """
    if type(attempts) is not int or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for value in (timeout, base_delay):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid duration")
    deadline = clock() + timeout
    delay = min(base_delay, timeout)
    for attempt in range(attempts):
        remaining = deadline - clock()
        if remaining <= 0:
            raise DeadlineExceeded("overall deadline reached")
        try:
            result = operation(remaining)
        except TransientError:
            if attempt == attempts - 1:
                raise
            remaining = deadline - clock()
            if remaining <= delay:
                raise DeadlineExceeded("no budget for another attempt")
            sleep(delay)
            delay = min(delay * 2, timeout)
        else:
            if clock() >= deadline:
                raise DeadlineExceeded("operation returned after deadline")
            return result


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


class Tests(unittest.TestCase):
    def setUp(self):
        self.time = FakeClock()

    def run_retry(self, operation, **kwargs):
        return retry(operation, clock=self.time.clock, sleep=self.time.sleep, **kwargs)

    def test_transient_then_success(self):
        budgets = []
        def operation(remaining):
            budgets.append(remaining)
            if len(budgets) == 1:
                raise TransientError()
            return "ok"
        self.assertEqual(self.run_retry(operation), "ok")
        self.assertEqual(budgets, [3.0, 2.75])

    def test_permanent_error_is_not_retried(self):
        calls = []
        def operation(remaining):
            calls.append(1)
            raise PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.run_retry(operation)
        self.assertEqual(len(calls), 1)

    def test_attempt_and_deadline_limits(self):
        calls = []
        def operation(remaining):
            calls.append(remaining)
            raise TransientError()
        with self.assertRaises(TransientError):
            self.run_retry(operation, attempts=2)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(DeadlineExceeded):
            self.run_retry(operation, timeout=0)
        with self.assertRaises(DeadlineExceeded):
            self.run_retry(operation, timeout=0.1)

    def test_late_success_is_not_timely_success(self):
        def operation(remaining):
            self.time.now += remaining + 0.1
            return "late"
        with self.assertRaises(DeadlineExceeded):
            self.run_retry(operation)


if __name__ == "__main__":
    unittest.main()
