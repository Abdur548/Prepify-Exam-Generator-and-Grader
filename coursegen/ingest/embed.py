"""
BGE-M3 embedding: dense (1024-d) + sparse in one forward pass.

Using only the dense output discards the sparse vector at zero saving — both
come from the same forward pass (PRD §9.1 step 5).

Device selection honours C6: detect CUDA/MPS and use it; never require it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    output = model.encode(
        texts,
        batch_size=config.EMBEDDING_BATCH_SIZE,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
        show_progress_bar=len(texts) > config.EMBEDDING_BATCH_SIZE,
    )

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
