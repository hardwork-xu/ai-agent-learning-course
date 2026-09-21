"""Small synthetic retrieval regression suite; no LLM or job-readiness score."""

from __future__ import annotations

import hashlib
import json

from .retrieval import demo_retriever, fixture


def metric(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def score(*, threshold: float, split: str = "test") -> dict:
    retriever = demo_retriever(threshold=threshold)
    cases = [case for case in fixture("evaluation.json") if case["split"] == split]
    rows = []
    for case in cases:
        answer = retriever.answer(case["query"], tenant=case["tenant"])
        relevant = set(case["relevant_ids"])
        returned = set(answer.citations)
        rows.append({"id": case["id"], "answerable": bool(relevant),
                     "expected_ids": sorted(relevant), "returned_ids": sorted(returned),
                     "abstained": answer.abstained, "top1_relevant": bool(relevant & returned),
                     "correct_decision": bool(relevant & returned) if relevant else answer.abstained})
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    answered = [r for r in rows if not r["abstained"]]
    return {
        "threshold": threshold,
        "metrics": {
            "top1_hit_rate_answerable": metric(sum(r["top1_relevant"] for r in answerable), len(answerable)),
            "abstention_recall_unanswerable": metric(sum(r["abstained"] for r in unanswerable), len(unanswerable)),
            "citation_precision_answered": metric(sum(r["top1_relevant"] for r in answered), len(answered)),
            "decision_accuracy": metric(sum(r["correct_decision"] for r in rows), len(rows)),
            "answer_coverage": metric(len(answered), len(rows)),
        },
        "cases": rows,
    }


def evaluate() -> dict:
    canonical = json.dumps({"corpus": fixture("corpus.json"), "cases": fixture("evaluation.json")}, sort_keys=True)
    return {
        "schema_version": 1,
        "kind": "offline_synthetic_retrieval_regression",
        "configuration": {"split": "test", "top_k": 1, "scoring": "unique query token coverage",
                          "tokenizer": "lowercase ASCII alphanumeric set, fixed stopwords",
                          "tenant_filter": "before ranking", "model": None,
                          "fixture_sha256": hashlib.sha256(canonical.encode()).hexdigest()},
        "limitations": ["12 hand-authored examples, 8 in the visible test split; not a hidden benchmark.",
                        "Threshold 0.6 is a teaching choice, not the result of a model comparison.",
                        "Citations are checked by document ID; this does not measure claim-level faithfulness.",
                        "No LLM runs, semantic similarity, latency measurement, or production reliability claim."],
        "baseline": score(threshold=0.0),
        "candidate": score(threshold=0.6),
    }
