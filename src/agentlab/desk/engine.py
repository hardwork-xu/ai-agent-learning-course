"""Durable read-only investigation followed by explicit human semantic approval.

Only source verification plus the run transaction hold the knowledge write guard.
No network/model call runs under either database's lock. A process crash may repeat
an unfinished read-only tool call; checkpoints never replay a committed side effect.
"""
from __future__ import annotations

import json
from importlib.resources import files
import threading
import time

from agentlab.local_model import ModelError
from .planner import BaselinePlanner, LocalPlanner, validate_decision
from .store import DeskError, citations_valid


class Engine:
    def __init__(self, store, knowledge, *, planner_factory=None, max_steps=8,
                 timeout_seconds=90, lease_seconds=15):
        if type(max_steps) is not int or not 1 <= max_steps <= 32:
            raise DeskError("invalid_step_budget")
        if not 0 < timeout_seconds <= 300 or not 0 < lease_seconds <= 360:
            raise DeskError("invalid_worker_budget")
        self.store, self.knowledge = store, knowledge
        self.planner_factory = planner_factory
        self.max_steps, self.timeout_seconds, self.lease_seconds = max_steps, timeout_seconds, lease_seconds

    def _planner(self, run):
        if run["mode"] == "baseline":
            return BaselinePlanner()
        if self.planner_factory is None:
            raise DeskError("local_model_not_configured", 503)
        planner = self.planner_factory()
        if isinstance(planner, LocalPlanner):
            for event in self.store.events(run["tenant"], run["id"]):
                usage = event.get("usage")
                if isinstance(usage, dict) and usage.get("model_digest"):
                    if planner.expected_digest and planner.expected_digest != usage["model_digest"]:
                        raise DeskError("model_identity_changed", 409)
                    planner.expected_digest = usage["model_digest"]
        return planner

    def _lookup(self, tenant, case_id):
        data = json.loads(files("agentlab.desk").joinpath("cases.json").read_text(encoding="utf-8"))
        for record in data["cases"]:
            if record["tenant"] == tenant and record["case_id"] == case_id:
                return {"found": True, "case": {k: v for k, v in record.items() if k != "tenant"}, "data_kind": "synthetic_fixture"}
        # Same response for absent and another tenant's record.
        return {"found": False, "case": None, "data_kind": "synthetic_fixture"}

    def _tool(self, run, decision):
        name, args = decision["name"], decision["arguments"]
        if name == "lookup_case":
            result = self._lookup(run["tenant"], args["case_id"])
        elif name == "search_knowledge":
            result = self.knowledge.search(run["tenant"], args["query"], limit=args.get("limit", 5))
        else:
            raise DeskError("unknown_tool")
        return {"type": "tool", "name": name, "arguments": args, "result": result}

    def _validate_citations(self, tenant, citations, observations):
        citations_valid(citations)
        seen = {(e["id"], e["revision"]) for o in observations if o.get("name") == "search_knowledge"
                for e in o["result"]}
        for citation in citations:
            if (citation["id"], citation["revision"]) not in seen:
                raise DeskError("citation_not_observed", 409)
            evidence = self.knowledge.get(tenant, citation["id"])
            if evidence is None or evidence["revision"] != citation["revision"]:
                raise DeskError("citation_stale", 409)
            if not citation["quote"].strip() or citation["quote"] not in evidence["text"]:
                raise DeskError("citation_quote_mismatch", 409)

    def run_once(self):
        run = self.store.claim(lease_seconds=self.lease_seconds, execution_seconds=self.timeout_seconds)
        if run is None:
            return False
        run_id, fence = run["id"], run["fence"]
        stop, lost = threading.Event(), threading.Event()
        # Wall-clock deadline survives restarts; monotonic budget bounds this worker
        # even when the local wall clock moves backwards while it runs.
        monotonic_deadline = time.monotonic() + max(0, run["deadline"] - self.store.clock())

        def heartbeat():
            while not stop.wait(min(5, self.lease_seconds / 3)):
                try:
                    self.store.renew(run_id, fence, lease_seconds=self.lease_seconds)
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True, name="casedesk-lease")
        thread.start()
        try:
            planner = self._planner(run)
            while True:
                if lost.is_set():
                    raise DeskError("lease_lost", 409)
                current = self.store.current(run_id, fence)
                remaining = min(current["deadline"] - self.store.clock(), monotonic_deadline - time.monotonic())
                if remaining <= 0:
                    raise DeskError("deadline_exceeded", 408)
                observations = json.loads(current["observations"])
                if current["pending"] is not None:
                    # A recovered action was already validated and persisted, but
                    # revalidate the schema so DB edits cannot add tool capabilities.
                    decision = validate_decision(json.loads(current["pending"]))
                else:
                    if current["steps"] >= self.max_steps:
                        raise DeskError("step_budget_exceeded", 409)
                    attempt_id = self.store.begin_attempt(run_id, fence) if run["mode"] == "local" else None
                    attempt_start = time.perf_counter()
                    failure = None
                    try:
                        decision = validate_decision(planner.next(run["question"], observations,
                                                                 remaining_seconds=remaining))
                    except Exception as exc:
                        failure = exc
                        raise
                    finally:
                        if attempt_id:
                            self.store.end_attempt(attempt_id,
                                usage=failure.usage if isinstance(failure, ModelError) else getattr(planner, "last_usage", None),
                                latency_ms=round((time.perf_counter() - attempt_start)*1000, 3),
                                error=getattr(failure, "code", "planner_error") if failure else None)
                    if time.monotonic() >= monotonic_deadline:
                        raise DeskError("deadline_exceeded", 408)
                    self.store.save_decision(run_id, fence, decision,
                        latency_ms=getattr(planner, "last_latency_ms", None))
                if time.monotonic() >= monotonic_deadline:
                    raise DeskError("deadline_exceeded", 408)
                kind = decision["type"]
                if kind == "tool":
                    started = time.perf_counter()
                    # Fence checked again before execution; a cancellation during
                    # read-only work is checked atomically before saving the result.
                    self.store.current(run_id, fence)
                    result = self._tool(run, decision)
                    if time.monotonic() >= monotonic_deadline:
                        raise DeskError("deadline_exceeded", 408)
                    self.store.save_observation(run_id, fence, result,
                                               latency_ms=round((time.perf_counter()-started)*1000, 3))
                elif kind == "finish":
                    with self.knowledge.validation_guard():
                        self._validate_citations(run["tenant"], decision["citations"], observations)
                        if time.monotonic() >= monotonic_deadline:
                            raise DeskError("deadline_exceeded", 408)
                        self.store.finish(run_id, fence, state="awaiting_review", answer=decision["answer"], citations=decision["citations"])
                    return True
                elif kind == "clarify":
                    self.store.finish(run_id, fence, state="needs_input", clarification=decision["question"])
                    return True
                else:
                    self.store.finish(run_id, fence, state="abstained", answer=decision["reason"])
                    return True
        except Exception as exc:
            code = exc.code if isinstance(exc, (DeskError, ModelError)) else "worker_error"
            try:
                # Cancellation/reclaim wins: finish is still fenced. No raw
                # exception, provider body, prompt or private reasoning is logged.
                self.store.finish(run_id, fence, state="failed", error=code,
                    latency_ms=exc.latency_ms if isinstance(exc, ModelError) else None)
            except DeskError as stale:
                if stale.code != "lease_lost":
                    raise
            return True
        finally:
            stop.set()
            thread.join(timeout=0.2)

    def review(self, tenant, run_id, reviewer, expected_version, answer, citations, decision, comment=""):
        # Fixed lock order: knowledge then run DB. Withdraw/update cannot slip
        # between validation and approval. No call in Store acquires a KB lock.
        with self.knowledge.validation_guard():
            run = self.store.get(tenant, run_id)
            # Rejecting a stale draft must remain possible: it creates no effect
            # and frees capacity. Approval alone requires current source validity.
            if decision == "approve":
                self._validate_citations(tenant, citations, run["observations"])
            return self.store.review(tenant, run_id, reviewer, expected_version, answer, citations, decision, comment)

    def commit(self, tenant, run_id, actor, expected_version, idempotency_key):
        with self.knowledge.validation_guard():
            run = self.store.get(tenant, run_id, actor)
            # Replay of an already committed key returns historical effect even
            # after source withdrawal. A new effect must use current evidence.
            if run["state"] != "committed":
                self._validate_citations(tenant, run["citations"], run["observations"])
            return self.store.commit(tenant, run_id, actor, expected_version, idempotency_key)
