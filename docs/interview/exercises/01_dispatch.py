"""Run: python docs/interview/exercises/01_dispatch.py"""
import math
import unittest

LIMIT = 10**12


class InvalidCall(ValueError):
    pass


def number(value):
    if type(value) not in (int, float):  # bool must not pass as int
        raise InvalidCall("expected a number")
    if not -LIMIT <= value <= LIMIT or not math.isfinite(value):
        raise InvalidCall("number outside finite range")
    return value


def dispatch(call):
    if type(call) is not dict or set(call) != {"name", "arguments"}:
        raise InvalidCall("invalid call envelope")
    if call["name"] != "add_numbers":
        raise InvalidCall("unknown tool")
    args = call["arguments"]
    if type(args) is not dict or set(args) != {"a", "b"}:
        raise InvalidCall("expected exactly a and b")
    a, b = number(args["a"]), number(args["b"])
    return {"result": number(a + b)}


class Tests(unittest.TestCase):
    def call(self, a, b):
        return {"name": "add_numbers", "arguments": {"a": a, "b": b}}

    def test_success(self):
        self.assertEqual(dispatch(self.call(2, -1.5)), {"result": 0.5})

    def test_bad_numbers(self):
        for value in (True, "2", None, float("nan"), float("inf"), 10**1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(InvalidCall):
                    dispatch(self.call(value, 1))

    def test_exact_fields_and_tool(self):
        bad = self.call(1, 2)
        bad["arguments"]["admin"] = True
        for call in (bad, {"name": "shell", "arguments": {}}, [],
                     {"name": "add_numbers", "arguments": {"a": 1}}):
            with self.assertRaises(InvalidCall):
                dispatch(call)

    def test_result_outside_business_limit(self):
        with self.assertRaises(InvalidCall):
            dispatch(self.call(LIMIT, 1))


if __name__ == "__main__":
    unittest.main()
