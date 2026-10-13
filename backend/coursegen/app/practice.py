"""The practice API: take a paper, see marks, contest them, retry marking.

A router of its own rather than more of `main.py`, which already carries ingest,
generation, chat and the consent gate. `main.py` includes it. The consent flag and
the pipeline lock stay main's — read at call time through `_main()` — so there is
exactly one of each, whichever route a request arrives by.

## The error mapping a client relies on

| raised | HTTP |
|---|---|
| `UnknownSheet`, `UnknownAttempt` | 404 |
| `InvalidSubmission` | 422, detail safe to show |
| a written answer sent without consent | 403 |
| a retry of marking, or a review that needs the model, while the pipeline is busy | 409 |
| a review with no material ingested | 409 |
| anything else | 500, logged; the body says nothing about the server |

## Why a busy pipeline is degraded on submit but 409 on retry

A submission carries answers, and they must not be lost: it is saved, objective
questions are marked, and the written ones wait with a sentence saying why. A
retry carries nothing new — refusing it loses nothing, and "busy, try again" is
the whole story.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from types import ModuleType
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from coursegen.grade.records import (
    InvalidSubmission,
    UnknownAttempt,
    UnknownSheet,
    WrittenMarker,
)
from coursegen.grade.service import (
    check_lengths,
    contest,
    needs_marker,
    progress,
    remark,
    results,
    submit_attempt,
    written_entries,
)
from coursegen.grade.review import FreshGenerator, build_review, needs_model
from coursegen.grade.store import Store
from coursegen.grade.written import make_marker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

CONSENT_NEEDED = (
    "Consent is needed before your answers are sent to the model. "
    "POST /api/disclosure/accept first."
)


class AnswerIn(BaseModel):
    slot_id: str
    response: str = ""


class AttemptIn(BaseModel):
    sheet_id: str
    answers: list[AnswerIn]


class ContestIn(BaseModel):
    slot_id: str
    marks_awarded: int


def _main() -> ModuleType:
    """`coursegen.app.main`, imported when needed — it imports this module."""
    from coursegen.app import main

    return main


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_marker() -> WrittenMarker:
    """The route's one way to the model. Tests replace it."""
    return make_marker()


def _require_consent() -> None:
    if not _main()._disclosure_accepted:
        raise HTTPException(status_code=403, detail=CONSENT_NEEDED)


@contextmanager
def _pipeline_slot(label: str) -> Iterator[Optional[str]]:
    """Hold the pipeline lock for a model call, without waiting for it.

    Yields None when held, or what holds it ("generating a paper") when another
    job does. Marking and re-wording call the model like generation and chat do,
    so they take the same lock: the daily call ledger is safe only with one
    writer at a time, and re-wording also reads Qdrant, which ingest locks.
    """
    main = _main()
    if not main._PIPELINE_LOCK.acquire(blocking=False):
        yield main._pipeline_holder or "with another job"
        return
    main._pipeline_holder = label
    try:
        yield None
    finally:
        main._pipeline_holder = ""
        main._PIPELINE_LOCK.release()


def _busy_marking(holder: str) -> str:
    return f"Busy {holder}. Your answers are saved — retry marking when it finishes."


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except HTTPException:
        raise
    except (UnknownSheet, UnknownAttempt) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidSubmission as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        # R4: the traceback goes to the log, never to the client.
        logger.exception("Practice request failed")
        raise HTTPException(
            status_code=500, detail="Practice request failed. Check the server logs."
        )


def _responses(answers: list[AnswerIn]) -> dict[str, str]:
    responses: dict[str, str] = {}
    for answer in answers:
        if answer.slot_id in responses:
            # Two answers to one question: which one counts would be a guess.
            raise InvalidSubmission(f"{answer.slot_id} was answered twice.")
        responses[answer.slot_id] = answer.response
    return responses


@router.post("/attempts")
def post_attempt(request: AttemptIn) -> dict[str, Any]:
    with _errors(), Store() as store:
        responses = _responses(request.answers)
        # A request that cannot be honoured is a 422 whether or not consent was
        # given; asking for consent first would ask for something it cannot use.
        check_lengths(responses)
        if not needs_marker(store, request.sheet_id, responses):
            return submit_attempt(store, request.sheet_id, responses, now=_now())
        _require_consent()
        with _pipeline_slot("marking answers") as busy:
            return submit_attempt(
                store, request.sheet_id, responses, now=_now(),
                marker=None if busy else _new_marker(),
                unavailable=_busy_marking(busy) if busy else None,
            )


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        return results(store, attempt_id)


@router.post("/attempts/{attempt_id}/contest")
def post_contest(attempt_id: str, request: ContestIn) -> dict[str, Any]:
    with _errors(), Store() as store:
        return contest(store, attempt_id, request.slot_id, request.marks_awarded, now=_now())


@router.post("/attempts/{attempt_id}/remark")
def post_remark(attempt_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        if not written_entries(store, attempt_id, only_unmarked=True):
            return results(store, attempt_id)
        _require_consent()
        with _pipeline_slot("marking answers") as busy:
            if busy:
                raise HTTPException(status_code=409, detail=_busy_marking(busy))
            return remark(store, attempt_id, marker=_new_marker(), now=_now())


def _course_nodes() -> list:
    from coursegen import config
    from coursegen.ingest.coursemap import load_course_map

    if not config.COURSE_MAP_PATH.exists():
        raise HTTPException(
            status_code=409,
            detail="No course material has been ingested yet. Upload your notes first.",
        )
    return load_course_map(config.COURSE_MAP_PATH)


def _new_generator(nodes: list) -> FreshGenerator:
    """Write re-worded review questions with the paper pipeline's own generation
    and gates. A model that cannot be reached yields nothing, and every card that
    needed new wording falls back to its original — labelled, never passed off."""
    from coursegen import config

    def generate(specs: list) -> dict:
        import httpx

        from coursegen.exam.generate import generate_exam
        from coursegen.ingest.embed import dense_embedding_fn
        from coursegen.ingest.index import read_spans
        from coursegen.llm.client import BudgetExceeded, LLMClient

        main = _main()
        try:
            reranker, embed = main._get_reranker(), main._get_embed_model()
            span_text, span_source = read_spans(
                sorted({sid for s in specs for sid in s.span_ids}), config.DATA_DIR
            )
            result = generate_exam(
                specs=specs, course_map=nodes, span_text_by_id=span_text,
                span_source_by_id=span_source, llm_client=LLMClient(),
                # Its own output directory: generation writes run_manifest.json,
                # which must not overwrite the latest paper's.
                output_dir=config.OUTPUT_DIR / "review",
                cache_dir=config.OUTPUT_DIR / "cache",
                blueprint_id="review",
                groundedness_scorer=lambda a, src: float(reranker.predict([(a, src)])[0]),
                embedding_fn=dense_embedding_fn(embed),
            )
        except (BudgetExceeded, httpx.HTTPError, EnvironmentError) as exc:
            logger.warning("Review re-wording unavailable: %s", type(exc).__name__)
            return {}
        return {item.slot_id: item for item in result.items}

    return generate


@router.post("/review")
def post_review() -> dict[str, Any]:
    nodes = _course_nodes()
    with _errors(), Store() as store:
        if not needs_model(store, nodes):
            # Identical questions only: the student's own history, nothing else.
            return build_review(store, nodes, now=_now(), generate_fresh=None)
        _require_consent()
        with _pipeline_slot("building a review") as busy:
            if busy:
                raise HTTPException(
                    status_code=409,
                    detail=f"Busy {busy}. Build the review again when it finishes.",
                )
            return build_review(store, nodes, now=_now(), generate_fresh=_new_generator(nodes))


@router.get("/progress")
def get_progress() -> dict[str, Any]:
    nodes = _course_nodes()
    with _errors(), Store() as store:
        return progress(store, nodes)


@router.get("/sheets/{sheet_id}")
def get_sheet(sheet_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        return store.get_sheet(sheet_id).document
