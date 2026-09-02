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


def read_spans(chunk_ids: list[str], data_dir: Path) -> tuple[dict[str, str], dict[str, tuple[str, int]]]:
    """Read span text AND provenance back out of Qdrant, in one open.

    `ingest()` persists chunk text and source metadata as payload fields but
    returns only the course map, so this is the only way to recover the spans an
    item is generated from — and the only way `source_ref` can be stamped with a
    real file and page instead of whatever the model invents (it sees only
    `<source_span id="...">`, so asked to cite, it echoes the delimiter).

    Returns `(text_by_id, source_by_id)`. One call rather than two because Qdrant
    local mode takes an EXCLUSIVE file lock (S8): every open is a chance to
    collide with another reader, and the two callers always wanted both maps
    anyway.

    Lived in `tests/run_exam.py` until 2026-09-01, which made a test-directory
    script a load-bearing dependency of anything that needed real spans.
    """
    client = get_client(data_dir)
    try:
        records = client.retrieve(
            collection_name=config.QDRANT_COLLECTION_NAME,
            ids=chunk_ids,
            with_payload=True,
        )
    finally:
        client.close()

    text_by_id: dict[str, str] = {}
    source_by_id: dict[str, tuple[str, int]] = {}
    for r in records:
        payload = r.payload or {}
        text_by_id[str(r.id)] = payload.get("text", "")
        source_by_id[str(r.id)] = (
            payload.get("source_file", "unknown"),
            int(payload.get("page", 0)),
        )
    return text_by_id, source_by_id
