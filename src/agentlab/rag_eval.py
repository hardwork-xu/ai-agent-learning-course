"""Visible Chinese RAG regression, source integrity, and generation review packet."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from .local_model import LocalModel, ModelError
from .rag import GroundedRAG, REWRITES, STOP_BIGRAMS, fixture


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def metric(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def summaries(rows: list[dict]) -> dict:
    answered = [row for row in rows if not row["trace"]["abstained"]]
    answerable = [row for row in rows if row["expected_ids"]]
    unanswerable = [row for row in rows if not row["expected_ids"]]
    return {
        "decision_accuracy": metric(sum(row["correct_decision"] for row in rows), len(rows)),
        "answer_coverage": metric(len(answered), len(rows)),
        "answerable_recall": metric(sum(row["correct_decision"] for row in answerable), len(answerable)),
        "abstention_recall_unanswerable": metric(sum(row["correct_decision"] for row in unanswerable), len(unanswerable)),
        "relevant_answer_precision": metric(sum(row["correct_decision"] for row in answered), len(answered)),
        "exact_quote_integrity_answered": metric(sum(row["quote_integrity"] for row in answered), len(answered)),
        "human_review_required": metric(sum(row["trace"]["reason"] == "semantic_review_required" for row in rows), len(rows)),
        "operational_failures": metric(sum(row["trace"]["reason"] == "model_error" for row in rows), len(rows)),
    }


class BudgetedGenerator:
    def __init__(self, generator, *, max_calls: int, max_seconds: float):
        self.generator, self.max_calls = generator, max_calls
        self.deadline = time.monotonic() + max_seconds
        self.calls = 0

    def generate(self, question, evidence):
        remaining = self.deadline - time.monotonic()
        if self.calls >= self.max_calls or remaining <= 0:
            raise ModelError("evaluation_budget_exhausted")
        self.calls += 1
        if isinstance(self.generator, LocalModel):
            previous = self.generator.timeout
            self.generator.timeout = min(previous, remaining)
            try:
                return self.generator.generate(question, evidence)
            finally:
                self.generator.timeout = previous
        return self.generator.generate(question, evidence)


def run_cases(rag: GroundedRAG, cases: list[dict], *, repeats: int) -> dict:
    rows = []
    for repeat in range(repeats):
        for case in cases:
            trace = rag.answer(case["query"], tenant=case["tenant"])
            expected = {(fact["id"], fact["revision"]) for fact in case["expected_facts"]}
            returned = {(ref["id"], ref["revision"]) for ref in trace["citations"]}
            available = {(doc.id, doc.revision, doc.text) for doc in rag.visible_documents(case["tenant"])}
            quote_integrity = bool(trace["citations"]) and all(
                (ref["id"], ref["revision"], ref["quote"]) in available for ref in trace["citations"])
            # A quote can be genuine but irrelevant. Evaluate relevance separately.
            correct = (not trace["abstained"] and returned == expected and quote_integrity) if expected else (
                trace["abstained"] and trace["reason"] in {"no_evidence", "low_coverage", "source_conflict", "model_abstained"})
            rows.append({"id": case["id"], "repeat": repeat + 1, "split": case["split"],
                         "slice": case["slice"], "query": case["query"], "tenant": case["tenant"],
                         "expected_ids": case["expected_ids"], "reference_facts": case["expected_facts"],
                         "correct_decision": bool(correct), "quote_integrity": bool(quote_integrity),
                         "trace": trace,
                         "human_review": {"status": "pending" if "generated_draft" in trace else "not_required_for_quote_integrity",
                                          "questions": ["是否回答了题目实际询问的事项？", "每项声明是否得到所附原文支持？",
                                                        "有无省略条件、改变数量或把申请误写成已执行？"]}})
    return {"metrics": summaries(rows),
            "slices": {slice_: summaries([row for row in rows if row["slice"] == slice_])
                       for slice_ in sorted({row["slice"] for row in rows})},
            "reason_counts": dict(sorted(Counter(row["trace"]["reason"] for row in rows).items())),
            "cases": rows}


def evaluate(*, mode: str = "extractive", model: str | None = None, split: str = "test",
             repeats: int = 1, min_coverage: float = 0.32, timeout: float = 20,
             max_output_tokens: int = 384, max_model_calls: int = 40,
             max_total_seconds: float = 180, generator=None) -> dict:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    if type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError("repeats must be in [1, 3]")
    if type(max_model_calls) is not int or not 1 <= max_model_calls <= 200:
        raise ValueError("max_model_calls must be in [1, 200]")
    if not 0 < max_total_seconds <= 1800:
        raise ValueError("max_total_seconds must be in (0, 1800]")
    corpus, dataset = fixture("zh_corpus.json"), fixture("zh_evaluation.json")
    cases = [case for case in dataset if case["split"] == split]
    config = {"mode": mode, "model": model, "generator": "injected_test_double" if generator is not None else mode,
              "split": split, "repeats": repeats, "as_of": "2026-10-01", "min_coverage": min_coverage,
              "retrieval": "BM25 k1=1.2 b=0.75, Chinese bigrams + ASCII words, declared rewrites",
              "query_rewrites": REWRITES, "stop_bigrams": sorted(STOP_BIGRAMS),
              "top_k": 3, "timeout_seconds_per_call": timeout,
              "max_output_tokens": max_output_tokens, "max_model_calls": max_model_calls,
              "max_total_seconds": max_total_seconds}
    baseline = run_cases(GroundedRAG(min_coverage=0.0), cases, repeats=repeats)
    budgeted = None
    if mode == "local":
        actual = generator if generator is not None else LocalModel(
            model, timeout=timeout, max_output_tokens=max_output_tokens)
        budgeted = BudgetedGenerator(actual, max_calls=max_model_calls, max_seconds=max_total_seconds)
    elif generator is not None or model is not None:
        raise ValueError("model/generator require mode='local'")
    candidate = run_cases(GroundedRAG(mode, model=model, generator=budgeted, min_coverage=min_coverage),
                          cases, repeats=repeats)
    return {"schema_version": 1, "kind": "visible_synthetic_chinese_rag_regression",
            "configuration": config, "configuration_sha256": digest(config),
            "dataset_sha256": digest(dataset), "corpus_sha256": digest(corpus),
            "implementation_sha256": digest({name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ("rag.py", "rag_eval.py", "local_model.py")}),
            "dataset_counts": {"dev": sum(case["split"] == "dev" for case in dataset),
                               "test": sum(case["split"] == "test" for case in dataset)},
            "model_calls_attempted": budgeted.calls if budgeted else 0,
            "baseline": baseline, "candidate": candidate,
            "limitations": [
                "全部问题和答案均公开且人工编写；这是回归检查，不是独立泛化测试。",
                "基线与候选使用同一 BM25；基线覆盖率阈值为 0，候选使用配置阈值。",
                "来源引用真实不代表回答切题；地图坐标题是保留的词法检索反例。",
                "exact_quote_integrity 只检验可见资料原文，不评估生成改写的语义蕴含。",
                "生成草稿的语义支持需人工复核；待复核和运行错误不计作答对。",
                "固定 as_of 用于复现实验，实际业务必须传入所需日期并更新资料。",
                "time/byte/token 预算限制客户端；已提交的服务器推理可能继续消耗资源。",
            ]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("extractive", "local"), default="extractive")
    parser.add_argument("--model")
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--min-coverage", type=float, default=0.32)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--max-output-tokens", type=int, default=384)
    parser.add_argument("--max-model-calls", type=int, default=40)
    parser.add_argument("--max-total-seconds", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        report = evaluate(**{key: value for key, value in vars(args).items() if key != "output"})
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "mode": args.mode,
                      "model_calls_attempted": report["model_calls_attempted"],
                      "metrics": report["candidate"]["metrics"]}, ensure_ascii=False))
    # A completed evaluation may show quality failures. Infrastructure failures
    # use a nonzero exit so CI cannot mistake an absent local model for evidence.
    return 2 if report["candidate"]["metrics"]["operational_failures"]["numerator"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
