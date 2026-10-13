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
| a retry of marking while the pipeline is busy | 409 |
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
    remark,
    results,
    submit_attempt,
    written_entries,
)
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
def _marking_slot() -> Iterator[Optional[str]]:
    """Hold the pipeline lock for a marking call, without waiting for it.

    Yields None when held, or a student-safe sentence when another job has it.
    Marking calls the model like generation and chat do, so it takes the same
    lock: the daily call ledger is safe only with one writer at a time.
    """
    main = _main()
    if not main._PIPELINE_LOCK.acquire(blocking=False):
        holder = main._pipeline_holder or "with another job"
        yield (f"Busy {holder}. Your answers are saved — retry marking when it finishes.")
        return
    main._pipeline_holder = "marking answers"
    try:
        yield None
    finally:
        main._pipeline_holder = ""
        main._PIPELINE_LOCK.release()


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
        with _marking_slot() as busy:
            return submit_attempt(
                store, request.sheet_id, responses, now=_now(),
                marker=None if busy else _new_marker(), unavailable=busy,
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
        with _marking_slot() as busy:
            if busy:
                raise HTTPException(status_code=409, detail=busy)
            return remark(store, attempt_id, marker=_new_marker(), now=_now())


@router.get("/sheets/{sheet_id}")
def get_sheet(sheet_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        return store.get_sheet(sheet_id).document
