"""
FastAPI application — P5.

Single worker only (S8): QdrantClient(path=...) takes an exclusive file lock.
Run with: uvicorn coursegen.app.main:app --workers 1
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from coursegen import config
from coursegen.app.preflight import preflight_status, run_preflight_checks
from coursegen.llm.client import BudgetExceeded

logger = logging.getLogger(__name__)

app = FastAPI(title="Prepify")

# Disclosure state — single-user, single-process (see S7).
_disclosure_accepted: bool = False


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup() -> None:
    try:
        run_preflight_checks()
    except RuntimeError as exc:
        # Log and continue: let the app start so the user sees the /api/preflight
        # endpoint rather than a bare startup crash. The generate endpoint will
        # re-surface the failure at request time.
        logger.error("Pre-flight check failed at startup: %s", exc)


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/preflight")
def preflight() -> dict[str, str]:
    return preflight_status()


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
        result = _run_exam_pipeline(request.blueprint_id, request.title)
        return result
    except BudgetExceeded:
        logger.warning("Generation degraded: quota limit reached")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "coverage_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation stopped early: quota limit reached. The exam may be incomplete."],
        }
    except httpx.TimeoutException:
        logger.warning("Generation degraded: request timed out")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "coverage_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation timed out. The exam may be incomplete."],
        }
    except Exception as exc:
        # Any other unexpected error — log but never expose traceback (R4).
        logger.exception("Unexpected generation error")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "coverage_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation failed unexpectedly. Check the server logs."],
        }


def _run_exam_pipeline(blueprint_id: str, title: str) -> dict[str, Any]:
    """
    Orchestrate ingest → solve → generate → render for one exam.
    This is the seam that tests patch; the real implementation wires the P1–P4 stages.
    """
    raise NotImplementedError(
        "_run_exam_pipeline is not yet wired end-to-end; "
        "individual stages (ingest, allocate, generate, render) are all implemented."
    )


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
    """Seam that tests patch; real implementation wires the P4 chat path."""
    raise NotImplementedError("_run_chat_query not yet wired end-to-end.")
