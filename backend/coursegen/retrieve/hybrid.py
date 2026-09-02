"""Hybrid Qdrant retrieval: dense + sparse prefetch with RRF fusion."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from coursegen import config


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    file: str
    page: int
    score: float

    def with_score(self, score: float) -> "RetrievedChunk":
        return replace(self, score=score)


def hybrid_search(
    client: Any,
    dense_vector: list[float],
    sparse_indices: list[int],
    sparse_values: list[float],
) -> list[RetrievedChunk]:
    try:
        from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

        prefetch = [
            Prefetch(query=dense_vector, using="dense", limit=config.RETRIEVE_TOP_K),
            Prefetch(
                query=SparseVector(indices=sparse_indices, values=sparse_values),
                using="sparse",
                limit=config.RETRIEVE_TOP_K,
            ),
        ]
        query = FusionQuery(fusion=Fusion.RRF)
    except Exception:
        prefetch = [
            {"query": dense_vector, "using": "dense", "limit": config.RETRIEVE_TOP_K},
            {"query": {"indices": sparse_indices, "values": sparse_values}, "using": "sparse", "limit": config.RETRIEVE_TOP_K},
        ]
        query = {"fusion": "rrf"}

    response = client.query_points(
        collection_name=config.QDRANT_COLLECTION_NAME,
        prefetch=prefetch,
        query=query,
        limit=config.RETRIEVE_TOP_K,
        with_payload=True,
    )
    return [_point_to_chunk(p) for p in response.points]


def _point_to_chunk(point: Any) -> RetrievedChunk:
    payload = point.payload or {}
    return RetrievedChunk(
        chunk_id=str(point.id),
        text=str(payload.get("text", "")),
        file=str(payload.get("source_file", "")),
        page=int(payload.get("page", 0)),
        score=float(point.score),
    )
