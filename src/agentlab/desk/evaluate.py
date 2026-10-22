"""Visible retrieval diagnostics and evidence checks for CaseDesk.

This is not a hidden benchmark, semantic answer judge, or business-value score.
Operational completion and human approval are deliberately not inferred from a
retrieved quotation. Every case, including failures, remains in the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import tempfile
import time

from agentlab.console import configure_utf8_output
from .knowledge import KnowledgeError, KnowledgeStore


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def metric(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def task_dataset():
    return json.loads(files("agentlab.desk").joinpath("documents", "tasks.json").read_text(encoding="utf-8"))


def _requirements(task, evidence):
    return [{**requirement, "found": any(item["document_id"] == requirement["document_id"]
                and requirement["quote"] in item["text"] for item in evidence)}
            for requirement in task["required_evidence"]]


def _one(knowledge, tenant, task, limit):
    start = time.perf_counter()
    evidence = knowledge.search(tenant, task["question"], limit=limit)
    latency_ms = (time.perf_counter() - start) * 1000
    requirements = _requirements(task, evidence)
    expected_documents = {item["document_id"] for item in task["required_evidence"]}
    return {
        "id": task["id"], "family": task["family"], "split": task["split"],
        "question": task["question"], "expected_behavior": task["expected_behavior"],
        "note": task["note"], "retrieved": evidence, "requirements": requirements,
        "all_required_evidence_found": all(item["found"] for item in requirements) if requirements else None,
        "retrieval_admitted": bool(evidence),
        "unsupported_question_admitted": bool(evidence) if task["expected_behavior"] == "abstain" else None,
        "extra_document_hits": sum(item["document_id"] not in expected_documents for item in evidence),
        "untrusted_payload_retrieved": any("【合成攻击开始】" in item["text"] for item in evidence),
        "retrieved_characters": sum(len(item["text"]) for item in evidence),
        "latency_ms": round(latency_ms, 3),
        "answer_correctness": None, "unsafe_effects": None,
    }


def _aggregate(rows):
    positive = [row for row in rows if row["all_required_evidence_found"] is not None]
    unsupported = [row for row in rows if row["unsupported_question_admitted"] is not None]
    requirements = [item for row in rows for item in row["requirements"]]
    return {
        "all_required_evidence_recall": metric(sum(row["all_required_evidence_found"] for row in positive), len(positive)),
        "requirement_evidence_recall": metric(sum(item["found"] for item in requirements), len(requirements)),
        "unsupported_question_admission_rate": metric(sum(row["unsupported_question_admitted"] for row in unsupported), len(unsupported)),
        "untrusted_payload_exposure": metric(sum(row["untrusted_payload_retrieved"] for row in rows), len(rows)),
        "retrieved_characters": sum(row["retrieved_characters"] for row in rows),
        "extra_document_hits": sum(row["extra_document_hits"] for row in rows),
        "latency_ms_total": round(sum(row["latency_ms"] for row in rows), 3),
        "model_calls": 0, "model_tokens": 0,
        "answer_correctness": None, "task_completion": None, "unsafe_effects": None,
        "human_review_burden": None, "human_review_status": "not_measured",
    }


def evaluate(*, knowledge=None, tenant="demo", split="all", limits=(1, 5)):
    if split not in ("all", "calibration", "challenge"):
        raise ValueError("invalid_split")
    if not limits or any(type(limit) is not int or not 1 <= limit <= 20 for limit in limits) or len(set(limits)) != len(limits):
        raise ValueError("invalid_limits")
    if knowledge is None:
        with tempfile.TemporaryDirectory() as directory:
            knowledge = KnowledgeStore(Path(directory) / "knowledge.db")
            knowledge.seed_demo(tenant)
            return evaluate(knowledge=knowledge, tenant=tenant, split=split, limits=limits)
    dataset = task_dataset()
    tasks = [task for task in dataset["tasks"] if split == "all" or task["split"] == split]
    versions = [{key: document[key] for key in ("document_id", "revision", "active")}
                for document in knowledge.list_documents(tenant)]
    configurations = {f"bm25_top_{limit}": {"k": limit, "k1": 1.2, "b": 0.75,
        "positive_score_only": True, "tokenizer": "nfkc_latin_and_han_bigrams",
        "title_and_heading_in_index": True, "query_rewrite": False} for limit in limits}
    result = {
        "kind": "visible_casedesk_retrieval_diagnostic", "generated_at": datetime.now(timezone.utc).isoformat(),
        "split": split, "task_count": len(tasks), "families": dict(Counter(task["family"] for task in tasks)),
        "split_policy": dataset["split_policy"], "dataset_sha256": _hash(dataset),
        "index_versions_sha256": _hash(versions), "index_versions": versions,
        "implementation_sha256": {name: sha256(files("agentlab.desk").joinpath(name).read_bytes()).hexdigest()
                                  for name in ("knowledge.py", "evaluate.py")},
        "configurations": configurations, "configuration_sha256": _hash(configurations), "systems": {},
        "interpretation": {
            "comparison": "同一索引、分词、BM25参数、正分数门槛与原始问题；唯一变量是返回上限k。",
            "unsupported": "BM25相关性不等于可回答性；不支持问题仍有命中会作为失败保留。",
            "extra_hits": "不在必需证据文档集合中的命中数量，不等同于已判定无关的文本数量。",
            "exposure": "恶意段落进入检索上下文的测量，不是模型安全性判决。",
            "quality": "没有生成答案、调用工具或人审；不得把检索命中率称为任务成功率。",
            "generalization": "公开原创题与实现共同编写，challenge不是盲测或未见分布。",
        },
    }
    for limit in limits:
        rows = [_one(knowledge, tenant, task, limit) for task in tasks]
        result["systems"][f"bm25_top_{limit}"] = {"metrics": _aggregate(rows), "cases": rows,
            "by_family": {family: _aggregate([row for row in rows if row["family"] == family]) for family in sorted({row["family"] for row in rows})}}
    return result


def evaluate_predictions(knowledge, tenant, predictions, *, split="all", task_ids=None):
    """Check externally collected candidate citations without inventing a judge.

    A missing prediction remains a failure/incomplete row. Extra and duplicate
    case IDs are rejected so they cannot silently change denominators. Reported
    states/latency/usage are explicitly unverified input fields, not service proof.
    Semantic correctness and actual unauthorized effects require separate evidence.
    """
    if split not in ("all", "calibration", "challenge"):
        raise ValueError("invalid_split")
    if not isinstance(predictions, list) or len(predictions) > 1000:
        raise ValueError("invalid_predictions")
    tasks = [task for task in task_dataset()["tasks"] if split == "all" or task["split"] == split]
    expected = {task["id"] for task in tasks}
    unselected_count = 0
    if task_ids is not None:
        if (not isinstance(task_ids, (list, tuple)) or not task_ids
                or any(not isinstance(task_id, str) or task_id not in expected for task_id in task_ids)
                or len(set(task_ids)) != len(task_ids)):
            raise ValueError("invalid_task_selection")
        unselected_count = len(tasks) - len(task_ids)
        tasks = [task for task in tasks if task["id"] in task_ids]
        expected = set(task_ids)
    indexed = {}
    for prediction in predictions:
        if not isinstance(prediction, dict) or prediction.get("id") not in expected or prediction["id"] in indexed:
            raise ValueError("invalid_prediction_identity")
        indexed[prediction["id"]] = prediction
    rows = []
    for task in tasks:
        prediction = indexed.get(task["id"])
        if prediction is None:
            rows.append({"id": task["id"], "status": "missing", "citation_integrity": False,
                         "all_required_citation_evidence": False if task["required_evidence"] else None,
                         "human_semantic_review": "pending", "answer_correctness": None})
            continue
        answer, citations = prediction.get("answer", ""), prediction.get("citations", [])
        if not isinstance(answer, str) or len(answer) > 16000 or not isinstance(citations, list) or len(citations) > 20:
            raise ValueError("invalid_prediction")
        checks, evidence = [], []
        for citation in citations:
            if not isinstance(citation, dict) or not all(isinstance(citation.get(key), str) for key in ("id", "revision", "quote")):
                raise ValueError("invalid_citation")
            current = knowledge.get(tenant, citation["id"])
            valid = bool(current and current["revision"] == citation["revision"] and citation["quote"].strip()
                         and citation["quote"] in current["text"])
            checks.append({"id": citation["id"], "valid_current_quote": valid})
            if valid:
                evidence.append({"document_id": current["document_id"], "text": citation["quote"]})
        requirements = _requirements(task, evidence)
        rows.append({"id": task["id"], "status": "observed_input", "answer": answer,
            "citation_checks": checks, "citation_integrity": bool(checks) and all(check["valid_current_quote"] for check in checks),
            "all_required_citation_evidence": all(item["found"] for item in requirements) if requirements else None,
            "requirements": requirements, "reported_state": prediction.get("state"),
            "reported_latency_ms": prediction.get("latency_ms"), "reported_usage": prediction.get("usage"),
            "unverified_candidate_fields": ["reported_state", "reported_latency_ms", "reported_usage"],
            "human_semantic_review": "pending", "answer_correctness": None, "unsafe_effects": None})
    positive = [row for row in rows if row["all_required_citation_evidence"] is not None]
    return {"kind": "casedesk_candidate_evidence_checks", "cases": rows,
        "selected_task_ids": [task["id"] for task in tasks], "unselected_count": unselected_count,
        "selection": "explicit_visible_subset_not_blind" if task_ids is not None else "all_tasks_in_split",
        "selection_sha256": _hash([task["id"] for task in tasks]),
        "missing_predictions": sum(row["status"] == "missing" for row in rows),
        "citation_integrity": metric(sum(row["citation_integrity"] for row in rows), len(rows)),
        "all_required_citation_evidence": metric(sum(row["all_required_citation_evidence"] for row in positive), len(positive)),
        "answer_correctness": None, "task_completion": None, "unsafe_effects": None,
        "human_semantic_review": "pending",
        "note": "引文真实、覆盖标签不代表答复正确；候选自报耗时/usage/状态尚未与服务事件交叉核验。"}


def main(argv=None):
    configure_utf8_output()
    parser = argparse.ArgumentParser(description="CaseDesk 可见检索诊断，完整保留失败题。")
    parser.add_argument("--split", choices=("all", "calibration", "challenge"), default="all")
    parser.add_argument("--database", type=Path, help="已有知识库；不自动写入或重置")
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--predictions", type=Path, help="可选：实际候选JSON数组，只做引用检查，不充当语义裁判")
    args = parser.parse_args(argv)
    try:
        if args.database is not None and not args.database.is_file():
            parser.error("database_not_found")
        with tempfile.TemporaryDirectory() as directory:
            knowledge = KnowledgeStore(args.database or Path(directory) / "knowledge.db")
            if args.database is None:
                knowledge.seed_demo(args.tenant)
            report = evaluate(knowledge=knowledge, tenant=args.tenant, split=args.split)
            if args.predictions:
                predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
                report["candidate_evidence_checks"] = evaluate_predictions(knowledge, args.tenant, predictions, split=args.split)
            output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output, encoding="utf-8")
            else:
                print(output, end="")
        return 0
    except (KnowledgeError, ValueError, OSError):
        parser.error("evaluation_input_or_storage_error")


if __name__ == "__main__":
    raise SystemExit(main())
