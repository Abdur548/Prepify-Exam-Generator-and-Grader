"""Cross-encoder reranking helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from coursegen import config
from coursegen.retrieve.hybrid import RetrievedChunk


@dataclass(frozen=True)
class RerankedChunk(RetrievedChunk):
    pass


Scorer = Callable[[str, str], float]


def rerank_chunks(
    query: str,
    candidates: list[RetrievedChunk],
    scorer: Scorer,
) -> list[RerankedChunk]:
    scored = [
        RerankedChunk(
            chunk_id=c.chunk_id,
            text=c.text,
            file=c.file,
            page=c.page,
            score=float(scorer(query, c.text)),
        )
        for c in candidates
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:config.RERANK_TOP_K]


def should_use_material(chunks: list[RerankedChunk], threshold: float | None = None) -> bool:
    threshold = config.RERANKER_THRESHOLD if threshold is None else threshold
    return bool(chunks and chunks[0].score >= threshold)
