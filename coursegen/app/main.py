"""
FastAPI application — P5.

Single worker only (S8): QdrantClient(path=...) takes an exclusive file lock.
Run with: uvicorn coursegen.app.main:app --workers 1
"""
from __future__ import annotations

import json as _json
import logging
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from coursegen import config
from coursegen.app.preflight import preflight_status, run_preflight_checks
from coursegen.llm.client import BudgetExceeded

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model singletons — lazy-loaded on first chat/generation call (not at import).
# Both are module-level so they survive across requests; tests patch these
# helper functions to prevent real model loading.
# ---------------------------------------------------------------------------
_embed_model = None
_reranker_model = None

# One generation at a time. Every run writes the SAME files - output/exam.pdf,
# output/run_manifest.json, output/cache/ - because /api/files/{name} serves them
# from one fixed directory. Two overlapping runs interleave into that directory
# and the loser silently downloads the winner's paper.
#
# Not hypothetical: on 2026-09-01 a run_manifest was observed on disk describing a
# different paper than the artifacts beside it - claiming mcq_hygiene 8 and
# flagging a slot that the PDFs themselves contained - because a second process
# wrote into output/ mid-run. Behind an HTTP endpoint that stops being an accident
# and becomes one user double-clicking Generate.
#
# A lock rather than per-request directories because S8 already pins this to a
# single worker (Qdrant takes an exclusive file lock), so a process-level lock is
# sufficient and leaves the /api/files/{name} contract unchanged. The second
# caller waits instead of corrupting the first. If this ever has to serve
# concurrent users, the fix is per-run output directories with run-scoped download
# URLs - not a bigger lock.
_PIPELINE_LOCK = threading.Lock()


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from coursegen.ingest.embed import load_model
        _embed_model = load_model()
    return _embed_model


def _get_reranker():
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder
        _reranker_model = CrossEncoder(config.RERANKER_MODEL)
    return _reranker_model


# ---------------------------------------------------------------------------
# Blueprint directory (ships with the package)
# ---------------------------------------------------------------------------
_BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "exam" / "blueprints"




# ---------------------------------------------------------------------------
# App lifecycle (lifespan replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        run_preflight_checks()
    except RuntimeError as exc:
        logger.error("Pre-flight check failed at startup: %s", exc)
    yield


app = FastAPI(title="Prepify", lifespan=_lifespan)

# Disclosure state — single-user, single-process (see S7).
_disclosure_accepted: bool = False


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_UI_PATH = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def ui() -> HTMLResponse:
    return HTMLResponse(_UI_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/preflight")
def preflight() -> dict[str, str]:
    return preflight_status()


@app.get("/api/blueprints")
def list_blueprints() -> list[str]:
    return sorted(p.stem for p in _BLUEPRINT_DIR.glob("*.json"))


@app.post("/api/internal/reset-disclosure")
def _reset_disclosure() -> dict[str, bool]:
    """Test helper — resets disclosure flag. Must not be exposed in production UI."""
    global _disclosure_accepted
    _disclosure_accepted = False
    return {"reset": True}


# ---------------------------------------------------------------------------
# Disclosure (S7)
# ---------------------------------------------------------------------------

@app.post("/api/disclosure/accept")
def accept_disclosure() -> dict[str, bool]:
    global _disclosure_accepted
    _disclosure_accepted = True
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@app.post("/api/ingest")
async def ingest_files(files: list[UploadFile]) -> dict[str, Any]:
    """Upload course material files and run the ingest pipeline."""
    from coursegen.ingest.coursemap import ingest

    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = Path(tmpdir)
        for f in files:
            dest = source_dir / (f.filename or "upload")
            dest.write_bytes(await f.read())
        nodes = ingest(source_dir)

    return {"nodes_ingested": len(nodes)}


# ---------------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------------

@app.get("/api/files/{filename}")
def get_file(filename: str) -> FileResponse:
    # Prevent directory traversal — only allow the bare filename.
    safe_name = Path(filename).name
    path = config.OUTPUT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
    return FileResponse(str(path))


# ---------------------------------------------------------------------------
# Exam generation
# ---------------------------------------------------------------------------

class ExamRequest(BaseModel):
    blueprint_id: str
    title: str


@app.post("/api/exam")
def generate_exam(request: ExamRequest) -> dict[str, Any]:
    if not _disclosure_accepted:
        raise HTTPException(
            status_code=403,
            detail="Disclosure must be accepted before generating exams. "
                   "POST /api/disclosure/accept first.",
        )
    try:
        # Serialised: see _PIPELINE_LOCK. Held across the whole run because the
        # artifacts only agree with the manifest once render has finished.
        with _PIPELINE_LOCK:
            result = _run_exam_pipeline(request.blueprint_id, request.title)
        return result
    except BudgetExceeded:
        logger.warning("Generation degraded: quota limit reached")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation stopped early: quota limit reached. The exam may be incomplete."],
        }
    except httpx.RequestError as exc:
        logger.warning(f"Generation degraded: network error ({type(exc).__name__})")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation degraded by a network error. The exam may be incomplete."],
        }
    except Exception:
        # An unexpected exception is a FAILURE, not a degraded result.
        #
        # "degraded" is an operational state with a meaning: we ran, we hit a
        # known limit (quota, timeout), and what came back is real but partial.
        # Reporting a KeyError or a missing course map as degraded - with
        # fill_ratio 0.0 beside it - produces an HTTP 200 claiming a paper was
        # generated that filled nothing, which a caller cannot tell apart from a
        # paper that genuinely filled nothing. Before this change, a POST to
        # /api/exam with no pipeline at all returned 200 "degraded".
        #
        # Same distinction new_gate_report() draws between a gate that passed
        # everything and a gate that never ran, and why `skipped` is a bool there.
        # The two states R8 actually specifies - BudgetExceeded and
        # TimeoutException - are still degraded 200s, handled above.
        #
        # 500 here. Traceback is logged, never sent to the client (R4).
        logger.exception("Unexpected generation error")
        raise HTTPException(
            status_code=500,
            detail="Generation failed unexpectedly. Check the server logs.",
        )


def _run_exam_pipeline(blueprint_id: str, title: str) -> dict[str, Any]:
    """Adapt `pipeline.generate_paper` to the API contract.

    The stage sequence lives in `coursegen/pipeline.py` and is shared with the
    CLI. This function's only job is supplying the models and mapping the result
    onto the shape `API-CONTRACT.md` freezes.
    """
    from coursegen.pipeline import generate_paper

    reranker = _get_reranker()
    embed = _get_embed_model()

    def embedding_fn(texts: list[str]) -> list[list[float]]:
        out = embed.encode(
            texts, return_dense=True, return_sparse=False, return_colbert_vecs=False
        )
        return [v.tolist() for v in out["dense_vecs"]]

    result = generate_paper(
        blueprint_id=blueprint_id,
        title=title,
        groundedness_scorer=lambda answer, src: float(reranker.predict([(answer, src)])[0]),
        embedding_fn=embedding_fn,
    )

    # `coverage_ratio` is deliberately NOT returned (disconnected 2026-09-01). It
    # counts matched nodes against the WHOLE corpus, so under an authored
    # blueprint it read 0.04 on a paper that was 20/20 items and 100/100 marks.
    # `fill_ratio` and `allocation_fidelity` are the honest headline numbers.
    return {
        "status": result.status,
        "fill_ratio": result.coverage.fill_ratio,
        "allocation_fidelity": result.coverage.allocation_fidelity,
        "unfilled_slots": result.coverage.unfilled_slots,
        "items_count": len(result.items),
        "warnings": result.coverage.warnings,
        "downloads": {
            "exam_html": "/api/files/exam.html",
            "answer_key_html": "/api/files/answer_key.html",
            "coverage_html": "/api/files/coverage.html",
            "exam_pdf": "/api/files/exam.pdf",
            "answer_key_pdf": "/api/files/answer_key.pdf",
        },
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    history: list[list[str]] = []


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    history = [(h[0], h[1]) for h in request.history if len(h) == 2]
    return _run_chat_query(request.query, history)


def _run_chat_query(query: str, history: list[tuple[str, str]]) -> dict[str, Any]:
    """Wire query → embed → hybrid_search → rerank → answer_question (P4 chat path)."""
    from coursegen.ingest.index import get_client
    from coursegen.retrieve.hybrid import hybrid_search
    from coursegen.retrieve.rerank import rerank_chunks
    from coursegen.chat.answer import answer_question
    from coursegen.llm.client import LLMClient

    embed = _get_embed_model()
    reranker = _get_reranker()

    def retrieve(q: str):
        out = embed.encode(
            [q], return_dense=True, return_sparse=True, return_colbert_vecs=False
        )
        dense = out["dense_vecs"][0].tolist()
        sparse_dict = dict(out["lexical_weights"][0])
        sorted_items = sorted(sparse_dict.items())
        sparse_indices = [int(k) for k, _ in sorted_items]
        sparse_values = [float(v) for _, v in sorted_items]
        client = get_client(config.DATA_DIR)
        try:
            return hybrid_search(client, dense, sparse_indices, sparse_values)
        finally:
            client.close()

    def rerank(q: str, candidates):
        scorer = lambda q2, text: float(reranker.predict([(q2, text)])[0])
        return rerank_chunks(q, candidates, scorer)

    llm = LLMClient()
    answer = answer_question(query, history, retrieve, rerank, llm)

    return {
        "answer": answer.answer,
        "citations": answer.citations,
        "from_material": answer.from_material,
    }
