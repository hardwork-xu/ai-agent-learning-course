"""Strict JSON for small teaching protocols; no duplicate or non-finite values."""
import json
import math


def loads_object(text: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value

    def invalid_constant(_):
        raise ValueError("nonfinite_json_value")

    def finite_float(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("nonfinite_json_value")
        return value

    value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant,
                       parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value
