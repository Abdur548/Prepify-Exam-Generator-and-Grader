"""
Qdrant local-mode index: named dense + sparse vectors.

Idempotency: chunk IDs are uuid5 content-hashes, so upsert is a no-op on
re-ingest — same content → same ID → overwrite in place, no duplication (C1).

Single-worker requirement (S8): QdrantClient(path=…) takes an exclusive file
lock. FastAPI must run with --workers 1. Documented in README.md.
"""
from __future__ import annotations

import logging
from pathlib import Path

from coursegen import config
from coursegen.ingest.chunk import Chunk
from coursegen.ingest.embed import EmbedResult

logger = logging.getLogger(__name__)


def get_client(data_dir: Path) -> "QdrantClient":  # type: ignore[name-defined]
    from qdrant_client import QdrantClient

    qdrant_path = str(data_dir / "qdrant")
    logger.debug("Opening Qdrant at %s", qdrant_path)
    return QdrantClient(path=qdrant_path)


def get_or_create_collection(client: "QdrantClient") -> None:  # type: ignore[name-defined]
    """Idempotent: create the collection if it does not already exist."""
    from qdrant_client.models import Distance, SparseVectorParams, VectorParams

    existing = {c.name for c in client.get_collections().collections}
    if config.QDRANT_COLLECTION_NAME in existing:
        logger.debug("Collection %r already exists — skipping creation", config.QDRANT_COLLECTION_NAME)
        return

    client.create_collection(
        collection_name=config.QDRANT_COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(
                size=config.DENSE_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(),
        },
    )
    logger.info("Created Qdrant collection %r", config.QDRANT_COLLECTION_NAME)


def upsert_chunks(
    client: "QdrantClient",  # type: ignore[name-defined]
    chunks: list[Chunk],
    result: EmbedResult,
) -> None:
    """
    Upsert all chunks with their dense + sparse vectors.
    Batch size matches EMBEDDING_BATCH_SIZE to stay within memory limits.
    """
    from qdrant_client.models import PointStruct, SparseVector

    if not chunks:
        return

    points: list[PointStruct] = []
    for i, chunk in enumerate(chunks):
        dense_vec = result.dense[i].tolist()
        sparse_dict = result.sparse[i]
        if sparse_dict:
            sorted_items = sorted(sparse_dict.items())
            sparse_vec = SparseVector(
                indices=[k for k, _ in sorted_items],
                values=[float(v) for _, v in sorted_items],
            )
        else:
            sparse_vec = SparseVector(indices=[], values=[])

        points.append(PointStruct(
            id=chunk.chunk_id,
            vector={
                "dense": dense_vec,
                "sparse": sparse_vec,
            },
            payload={
                "source_file": chunk.source_file,
                "page": chunk.page,
                "path": chunk.path,
                "text": chunk.raw_text,
            },
        ))

    # Upsert in batches to avoid memory spikes.
    batch = config.EMBEDDING_BATCH_SIZE
    for start in range(0, len(points), batch):
        client.upsert(
            collection_name=config.QDRANT_COLLECTION_NAME,
            points=points[start: start + batch],
        )

    logger.info("Upserted %d chunks into %r", len(chunks), config.QDRANT_COLLECTION_NAME)
