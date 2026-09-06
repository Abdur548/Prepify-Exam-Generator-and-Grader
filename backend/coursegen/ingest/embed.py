"""
BGE-M3 embedding: dense (1024-d) + sparse in one forward pass.

Using only the dense output discards the sparse vector at zero saving — both
come from the same forward pass (PRD §9.1 step 5).

Device selection honours C6: detect CUDA/MPS and use it; never require it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from coursegen import config
from coursegen.ingest.chunk import Chunk

logger = logging.getLogger(__name__)


@dataclass
class EmbedResult:
    dense: np.ndarray               # shape (N, DENSE_VECTOR_SIZE)
    sparse: list[dict[int, float]]  # lexical_weights per chunk: {token_id: weight}


def load_model() -> "BGEM3FlagModel":  # type: ignore[name-defined]
    """Load BGE-M3 once. Caller caches the returned model across chunks."""
    from FlagEmbedding import BGEM3FlagModel  # type: ignore[import]

    device = _detect_device()
    logger.info("Loading %s on device=%s", config.EMBEDDING_MODEL, device)
    return BGEM3FlagModel(
        config.EMBEDDING_MODEL,
        use_fp16=(device != "cpu"),
        device=device,
    )


def dense_embedding_fn(model: "BGEM3FlagModel") -> Callable[[list[str]], list[list[float]]]:  # type: ignore[name-defined]
    """The `embedding_fn` the duplication gate takes, bound to a loaded model.

    Lives here, beside the model, because it existed only as a closure inside
    `app/main.py` and so only the HTTP route had one. The CLI passed no
    `embedding_fn` at all and therefore reported `duplication.skipped: true` on
    every run — including the 20-item paper `STATE.md` cites as the system's one
    real end-to-end result, which shipped with two of four gates never run (F12).

    `pipeline.py` was extracted to stop exactly this: two callers sequencing the
    same stages and drifting. That fixed the sequence and left the collaborators
    duplicated, so the drift moved rather than stopped. One function, two callers,
    nothing left to drift.
    """

    def embedding_fn(texts: list[str]) -> list[list[float]]:
        out = model.encode(
            texts, return_dense=True, return_sparse=False, return_colbert_vecs=False
        )
        return [v.tolist() for v in out["dense_vecs"]]

    return embedding_fn


def embed_chunks(chunks: list[Chunk], model: "BGEM3FlagModel") -> EmbedResult:  # type: ignore[name-defined]
    """
    Embed *chunks* in batches. One forward pass per batch yields both
    dense and sparse vectors (no second pass, no wasted quota).
    """
    if not chunks:
        return EmbedResult(
            dense=np.empty((0, config.DENSE_VECTOR_SIZE), dtype=np.float32),
            sparse=[],
        )

    texts = [c.text for c in chunks]

    # NOTE: do not pass `show_progress_bar` here. It is a sentence-transformers
    # parameter, not a FlagEmbedding one — BGEM3FlagModel.encode forwards unknown
    # kwargs straight to the tokenizer, and transformers >= 4.5x rejects it with
    # `_batch_encode_plus() got an unexpected keyword argument`. It was present
    # from P1 and never fired, because every test mocks embed_chunks: the real
    # embedding path first executed on 2026-08-30, against 14 real lecture decks,
    # and failed immediately. Progress is logged below instead.
    logger.info(
        "Embedding %d chunks (batch size %d) …",
        len(texts), config.EMBEDDING_BATCH_SIZE,
    )
    output = model.encode(
        texts,
        batch_size=config.EMBEDDING_BATCH_SIZE,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    logger.info("Embedded %d chunks.", len(texts))

    dense = np.array(output["dense_vecs"], dtype=np.float32)
    sparse = [dict(w) for w in output["lexical_weights"]]

    return EmbedResult(dense=dense, sparse=sparse)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _detect_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"
