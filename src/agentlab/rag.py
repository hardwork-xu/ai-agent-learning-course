"""Chinese lexical retrieval, evidence selection, and inspectable generation review.

Exact quote integrity is mechanically checkable; semantic entailment is not
silently inferred from a matching citation. The default publishes source quotes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
import math
import re
import time

from .local_model import LocalModel, ModelError, strict_json


REWRITES = (("找回密码", "密码重置"), ("忘记密码", "密码重置"), ("退钱", "退款"),
            ("多长时间", "期限"), ("多久", "期限"), ("几天", "期限"),
            ("工作日", "工作日"), ("多因素认证", "双重验证"))
STOP_BIGRAMS = {"请问", "一下", "什么", "多少", "是否", "可以", "怎么", "如何", "需要", "的是", "为什"}


def features(text: str) -> Counter:
    """Chinese bigrams plus ASCII words; a small declared rewrite dictionary."""
    text = text.lower()
    for source, target in REWRITES:
        text = text.replace(source, target)
    terms = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(run[i:i + 2] for i in range(len(run) - 1)
                     if run[i:i + 2] not in STOP_BIGRAMS)
    return Counter(terms)


def fixture(name: str):
    return strict_json(files("agentlab").joinpath("fixtures", name).read_text(encoding="utf-8"))


def canonical_date(value: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("dates must use canonical YYYY-MM-DD format")
    return value


@dataclass(frozen=True)
class PolicyDocument:
    id: str
    revision: str
    tenant: str
    title: str
    text: str
    fact_key: str
    value: str
    valid_from: str
    valid_until: str | None
    status: str = "approved"
    aliases: tuple[str, ...] = ()

    def searchable(self):
        return f"{self.title} {self.text} {' '.join(self.aliases)}"

    def citation(self):
        return {"id": self.id, "revision": self.revision, "quote": self.text}


class GroundedRAG:
    def __init__(self, mode: str = "extractive", model: str | None = None, *,
                 documents: list[PolicyDocument | dict] | None = None,
                 generator=None, as_of: str = "2026-10-01",
                 min_coverage: float = 0.32, top_k: int = 3):
        if mode not in {"extractive", "local"}:
            raise ValueError("mode must be extractive or local")
        if not 0 <= min_coverage <= 1:
            raise ValueError("min_coverage must be in [0, 1]")
        if type(top_k) is not int or not 1 <= top_k <= 5:
            raise ValueError("top_k must be in [1, 5]")
        canonical_date(as_of)
        if mode == "extractive" and (model is not None or generator is not None):
            raise ValueError("generation requires mode='local'")
        if mode == "local" and generator is None and model is None:
            raise ValueError("local mode requires an explicit model")
        self.mode, self.as_of, self.min_coverage, self.top_k = mode, as_of, min_coverage, top_k
        self.generator = generator if generator is not None else (LocalModel(model) if mode == "local" else None)
        raw = fixture("zh_corpus.json") if documents is None else documents
        self.documents = [item if isinstance(item, PolicyDocument) else PolicyDocument(**item) for item in raw]
        identities = set()
        effective_dates = set()
        for doc in self.documents:
            identity = (doc.tenant, doc.id, doc.revision)
            if identity in identities:
                raise ValueError("duplicate document revision")
            identities.add(identity)
            effective = (doc.tenant, doc.id, doc.valid_from)
            if effective in effective_dates:
                raise ValueError("document revisions must have distinct effective dates")
            effective_dates.add(effective)
            for field in (doc.tenant, doc.id, doc.revision, doc.title, doc.text, doc.fact_key, doc.value):
                if not isinstance(field, str) or not field.strip():
                    raise ValueError("document fields must be nonempty strings")
            if len(doc.text) > 2000 or len(doc.searchable()) > 6000:
                raise ValueError("document exceeds evidence budget")
            canonical_date(doc.valid_from)
            if doc.valid_until is not None:
                canonical_date(doc.valid_until)
                if doc.valid_until <= doc.valid_from:
                    raise ValueError("valid_until must follow valid_from")
            if doc.status not in {"approved", "draft", "withdrawn"}:
                raise ValueError("unknown document status")

    def visible_documents(self, tenant: str) -> list[PolicyDocument]:
        # Tenant and time are trusted server context; apply before scoring.
        latest = {}
        for doc in self.documents:
            # Drafts do not publish or revoke. A published withdrawal is a
            # tombstone, so it must participate before status/expiry filtering.
            if doc.tenant != tenant or doc.status == "draft" or doc.valid_from > self.as_of:
                continue
            previous = latest.get(doc.id)
            if previous is None or doc.valid_from > previous.valid_from:
                latest[doc.id] = doc
        return sorted((doc for doc in latest.values() if doc.status == "approved"
                       and (doc.valid_until is None or self.as_of < doc.valid_until)), key=lambda item: item.id)

    def search(self, question: str, *, tenant: str) -> list[dict]:
        docs = self.visible_documents(tenant)
        query = features(question)
        if not query or not docs:
            return []
        vectors = [features(doc.searchable()) for doc in docs]
        average = sum(sum(vector.values()) for vector in vectors) / len(vectors) or 1
        frequency = Counter(term for vector in vectors for term in vector)
        hits = []
        for doc, vector in zip(docs, vectors):
            score, length = 0.0, sum(vector.values())
            for term in query:
                tf = vector[term]
                if tf:
                    idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
            if score > 0:
                coverage = len(set(query) & set(vector)) / len(query)
                hits.append({"id": doc.id, "revision": doc.revision, "score": round(score, 6),
                             "query_feature_coverage": round(coverage, 6), "document": doc})
        return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))[:self.top_k]

    @staticmethod
    def verify_claims(payload, documents: list[PolicyDocument]) -> dict:
        allowed = {(doc.id, doc.revision, doc.text) for doc in documents}
        if not isinstance(payload, dict) or set(payload) != {"claims"}:
            return {"ok": False, "reason": "invalid_claim_schema"}
        claims = payload["claims"]
        if not isinstance(claims, list) or len(claims) > 3:
            return {"ok": False, "reason": "invalid_claim_schema"}
        if not claims:
            return {"ok": False, "reason": "model_abstained"}
        citations, exact = [], True
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {"text", "citations"}:
                return {"ok": False, "reason": "invalid_claim_schema"}
            text, refs = claim["text"], claim["citations"]
            if (not isinstance(text, str) or not text.strip() or len(text) > 1000
                    or not isinstance(refs, list) or not 1 <= len(refs) <= 3):
                return {"ok": False, "reason": "invalid_claim_schema"}
            for ref in refs:
                if (not isinstance(ref, dict) or set(ref) != {"id", "revision", "quote"}
                        or any(not isinstance(ref[key], str) for key in ref)):
                    return {"ok": False, "reason": "invalid_citation_schema"}
                if (ref["id"], ref["revision"], ref["quote"]) not in allowed:
                    return {"ok": False, "reason": "citation_not_in_evidence"}
                if ref not in citations:
                    citations.append(ref)
            if text not in [ref["quote"] for ref in refs]:
                exact = False
        return {"ok": True, "reason": "exact_quotes" if exact else "semantic_review_required",
                "exact": exact, "citations": citations, "claims": claims}

    def answer(self, question: str, *, tenant: str) -> dict:
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError("question must contain 1 to 1000 characters")
        if not isinstance(tenant, str) or not tenant or len(tenant) > 80:
            raise ValueError("tenant must be nonempty trusted application context")
        started = time.perf_counter()
        result = {"mode": self.mode, "answer": "资料不足，暂不作答。", "citations": [],
                  "abstained": True, "retrieval": [], "verification": {
                      "publishable": False, "citation_integrity": None,
                      "semantic_support": "not_evaluated", "question_relevance": "lexical_gate_only"},
                  "usage": None, "latency_ms": 0, "reason": "no_evidence"}

        def finish(reason):
            result["reason"] = reason
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return result

        hits = self.search(question, tenant=tenant)
        result["retrieval"] = [{key: value for key, value in hit.items() if key != "document"} for hit in hits]
        if not hits:
            return finish("no_evidence")
        if hits[0]["query_feature_coverage"] < self.min_coverage:
            return finish("low_coverage")
        selected = [hit["document"] for hit in hits
                    if hit["query_feature_coverage"] >= self.min_coverage]
        # Compare all visible sources for the winning fact, even if a conflict
        # would fall below top-k. The fact_key/value metadata is curator supplied.
        winning_key = selected[0].fact_key
        peers = [doc for doc in self.visible_documents(tenant) if doc.fact_key == winning_key]
        if len({doc.value for doc in peers}) > 1:
            result["answer"] = "有效资料对同一事项存在冲突，暂不作答，请核对资料版本。"
            result["verification"]["conflicts"] = [doc.citation() for doc in peers]
            return finish("source_conflict")
        if self.mode == "extractive":
            doc = selected[0]
            result.update(answer=doc.text, citations=[doc.citation()], abstained=False)
            result["verification"].update(publishable=True, citation_integrity=True,
                                          semantic_support="exact_source_quote")
            return finish("extractive_answer")
        # Include only facts that agree across currently valid sources.
        visible = self.visible_documents(tenant)
        selected = [doc for doc in selected if len({peer.value for peer in visible if peer.fact_key == doc.fact_key}) == 1]
        try:
            generated = self.generator.generate(question, [doc.citation() for doc in selected])
        except ModelError as exc:
            result["answer"] = "已检索到资料，但生成请求未完成。请检查模型运行状态或生成预算后重试。"
            result["verification"]["model_error"] = exc.code
            result["usage"] = exc.usage
            return finish("model_error")
        if not isinstance(generated, dict):
            return finish("invalid_generation_envelope")
        result["usage"] = generated.get("usage")
        checked = self.verify_claims(generated.get("payload"), selected)
        if not checked["ok"]:
            result["verification"]["citation_integrity"] = False
            return finish(checked["reason"])
        result["verification"]["citation_integrity"] = True
        if not checked["exact"]:
            result["verification"]["semantic_support"] = "requires_review"
            result["generated_draft"] = {"answer": "\n".join(claim["text"] for claim in checked["claims"]),
                                         "claims": checked["claims"], "needs_human_review": True}
            result["answer"] = "已生成待复核草稿；逐项核对声明与引用后再采用。"
            return finish("semantic_review_required")
        result.update(answer="\n".join(claim["text"] for claim in checked["claims"]),
                      citations=checked["citations"], abstained=False)
        result["verification"].update(publishable=True, semantic_support="exact_source_quote")
        return finish("verified_model_selection")
