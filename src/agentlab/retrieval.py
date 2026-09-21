"""Lexical evidence extraction, intentionally without embedding or generation."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import re


STOPWORDS = frozenset("a an the is are was of for to in on and or how what does do can i my our".split())


def tokenize(text: str) -> set[str]:
    # This tiny corpus is English. Chinese segmentation is an explicit extension.
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOPWORDS


def fixture(name: str) -> list[dict]:
    return json.loads(files("agentlab").joinpath("fixtures", name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Document:
    id: str
    tenant: str
    title: str
    text: str


@dataclass(frozen=True)
class Hit:
    document: Document
    score: float


@dataclass(frozen=True)
class EvidenceAnswer:
    text: str
    citations: tuple[str, ...]
    abstained: bool
    hits: tuple[Hit, ...]


class Retriever:
    def __init__(self, documents: list[Document], *, threshold: float = 0.6):
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be in [0, 1]")
        if len({(d.tenant, d.id) for d in documents}) != len(documents):
            raise ValueError("duplicate document identity")
        self.documents, self.threshold = tuple(documents), threshold

    def search(self, query: str, *, tenant: str, top_k: int = 3) -> tuple[Hit, ...]:
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("top_k must be in [1, 20]")
        terms = tokenize(query)
        if not terms:
            return ()
        hits = []
        for document in self.documents:
            # Filter BEFORE ranking; tenant is trusted application context, not a model argument.
            if document.tenant != tenant:
                continue
            overlap = terms & tokenize(document.title + " " + document.text)
            score = len(overlap) / len(terms)
            if overlap and score >= self.threshold:
                hits.append(Hit(document, score))
        return tuple(sorted(hits, key=lambda h: (-h.score, h.document.id))[:top_k])

    def answer(self, query: str, *, tenant: str) -> EvidenceAnswer:
        hits = self.search(query, tenant=tenant, top_k=1)
        if not hits:
            return EvidenceAnswer("资料不足，暂不作答。", (), True, ())
        document = hits[0].document
        return EvidenceAnswer(f"{document.text} [{document.id}]", (document.id,), False, hits)


def demo_retriever(*, threshold: float = 0.6) -> Retriever:
    return Retriever([Document(**d) for d in fixture("corpus.json")], threshold=threshold)
