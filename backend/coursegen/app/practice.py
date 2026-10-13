"""The practice API: take a paper, see marks, contest them.

A router of its own rather than more of `main.py`, which already carries ingest,
generation, chat and the consent gate. `main.py` includes it.

## The error mapping a client relies on

| raised | HTTP |
|---|---|
| `UnknownSheet`, `UnknownAttempt` | 404 |
| `InvalidSubmission` | 422, detail safe to show |
| anything else | 500, logged; the body says nothing about the server |
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from coursegen.grade.records import InvalidSubmission, UnknownAttempt, UnknownSheet
from coursegen.grade.service import contest, results, submit_attempt
from coursegen.grade.store import Store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class AnswerIn(BaseModel):
    slot_id: str
    response: str = ""


class AttemptIn(BaseModel):
    sheet_id: str
    answers: list[AnswerIn]


class ContestIn(BaseModel):
    slot_id: str
    marks_awarded: int


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        return submit_attempt(
            store, request.sheet_id, _responses(request.answers), now=_now()
        )


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        return results(store, attempt_id)


@router.post("/attempts/{attempt_id}/contest")
def post_contest(attempt_id: str, request: ContestIn) -> dict[str, Any]:
    with _errors(), Store() as store:
        return contest(store, attempt_id, request.slot_id, request.marks_awarded, now=_now())


@router.get("/sheets/{sheet_id}")
def get_sheet(sheet_id: str) -> dict[str, Any]:
    with _errors(), Store() as store:
        return store.get_sheet(sheet_id).document
