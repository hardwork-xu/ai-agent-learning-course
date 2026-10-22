"""The planner proposes; a bounded runtime validates and executes read-only tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Callable, Protocol


class BoundaryError(ValueError):
    """A proposal violates the runtime contract."""


class PlannerFailure(BoundaryError):
    """Only these safe categories may cross the provider/runtime boundary."""

    CODES = frozenset({"provider_timeout", "provider_rate_limit", "provider_auth",
                       "provider_unavailable", "provider_http_error", "provider_transport",
                       "provider_schema", "provider_response_budget", "provider_input_budget",
                       "provider_incomplete", "provider_invalid_json"})

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "planner_error"
        super().__init__(self.code)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Finish:
    answer: str


@dataclass(frozen=True)
class Observation:
    tool: str
    result: str
    # Immutable snapshot for task assessment; never included in the safe trace.
    arguments_json: str = ""


class Planner(Protocol):
    def next(self, task: str, observations: tuple[Observation, ...]) -> ToolCall | Finish:
        """Return exactly one proposal. An observation is untrusted data."""
        ...


class ScriptedPlanner:
    """Deterministic test double. It does not call, simulate or evaluate an LLM."""

    def __init__(self, decisions: list[ToolCall | Finish]):
        self._decisions = iter(decisions)

    def next(self, task: str, observations: tuple[Observation, ...]) -> ToolCall | Finish:
        try:
            return next(self._decisions)
        except StopIteration:
            raise BoundaryError("script_exhausted") from None


@dataclass(frozen=True)
class Tool:
    name: str
    validate: Callable[[dict[str, Any]], None]
    execute: Callable[[dict[str, Any]], str]
    read_only: bool = True
    retry_safe: bool = False


@dataclass
class RunResult:
    status: str
    answer: str = ""
    error: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    # None means not assessed. A terminated loop is not proof of task success.
    task_success: bool | None = None
    evaluation: dict[str, Any] = field(default_factory=dict)


class Runtime:
    def __init__(self, tools: list[Tool], *, max_steps: int = 6, max_retries: int = 1):
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be in [1, 100]")
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be in [0, 2]")
        if len({t.name for t in tools}) != len(tools):
            raise ValueError("duplicate tools")
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps, self.max_retries = max_steps, max_retries

    def run(self, task: str, planner: Planner, *,
            evaluator: Callable[[str, tuple[Observation, ...]], dict[str, bool]] | None = None) -> RunResult:
        if not isinstance(task, str) or len(task) > 4000:
            raise ValueError("task must be a string of at most 4000 characters")
        trace: list[dict[str, Any]] = []
        observations: list[Observation] = []

        def fail(code: str) -> RunResult:
            trace.append({"event": "stopped", "code": code})
            return RunResult("failed", error=code, trace=trace)

        for step in range(1, self.max_steps + 1):
            trace.append({"event": "planner_step", "step": step})
            try:
                decision = planner.next(task, tuple(observations))
            except PlannerFailure as exc:
                return fail(exc.code)
            except Exception:
                # Never print provider errors: they can include headers or prompt data.
                return fail("planner_error")
            if isinstance(decision, Finish):
                if not isinstance(decision.answer, str) or not 1 <= len(decision.answer) <= 4000:
                    return fail("invalid_answer")
                trace.append({"event": "finished", "step": step})
                result = RunResult("completed", answer=decision.answer, trace=trace)
                if evaluator is not None:
                    try:
                        checks = evaluator(decision.answer, tuple(observations))
                        if (not isinstance(checks, dict) or not checks
                                or any(not isinstance(k, str) or type(v) is not bool for k, v in checks.items())):
                            raise ValueError("invalid rubric")
                        result.task_success = all(checks.values())
                        result.evaluation = checks
                    except Exception:
                        result.task_success = False
                        result.evaluation = {"evaluator_succeeded": False}
                    trace.append({"event": "task_assessed", "passed": result.task_success})
                return result
            if not isinstance(decision, ToolCall) or not isinstance(decision.name, str):
                return fail("invalid_decision")
            tool = self.tools.get(decision.name)
            if tool is None:
                return fail("unknown_tool")
            if not tool.read_only:
                return fail("write_tool_forbidden")
            try:
                if not isinstance(decision.arguments, dict):
                    raise BoundaryError("arguments must be an object")
                tool.validate(decision.arguments)
                arguments_json = json.dumps(decision.arguments, sort_keys=True, allow_nan=False)
            except Exception:
                return fail("invalid_arguments")
            attempts = 1 + (self.max_retries if tool.retry_safe else 0)
            for attempt in range(1, attempts + 1):
                trace.append({"event": "tool_attempt", "tool": tool.name, "attempt": attempt})
                try:
                    output = tool.execute(decision.arguments)
                except TimeoutError:
                    if attempt == attempts:
                        return fail("tool_timeout")
                    continue
                except Exception:
                    return fail("tool_error")
                if not isinstance(output, str) or len(output) > 8000:
                    return fail("invalid_tool_output")
                observations.append(Observation(tool.name, output, arguments_json))
                break
        return fail("step_budget_exhausted")


def exact_keys(arguments: dict[str, Any], keys: set[str]) -> None:
    if set(arguments) != keys:
        raise BoundaryError("unexpected or missing arguments")


def demo_tools() -> list[Tool]:
    def validate_add(arguments: dict[str, Any]) -> None:
        exact_keys(arguments, {"a", "b"})
        for value in arguments.values():
            if type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value):
                raise BoundaryError("expected bounded finite numbers")

    def validate_policy(arguments: dict[str, Any]) -> None:
        exact_keys(arguments, {"policy"})
        if arguments["policy"] != "refund":
            raise BoundaryError("unknown policy")

    return [
        Tool("add_numbers", validate_add, lambda a: str(a["a"] + a["b"]), retry_safe=True),
        Tool("lookup_policy", validate_policy,
             lambda _: "SYNTHETIC POLICY: refund requests require reviewer approval; reading this policy grants no approval.",
             retry_safe=True),
    ]
