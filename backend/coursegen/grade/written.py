"""Marking written answers with the model, in validated batches.

## Reject, never clamp

Every mark the model returns is checked: the slot was asked for and is named once,
the mark is a whole number within what the question is worth, the reason is a
real sentence of bounded length, the confidence is one of two words. A mark that
fails is re-sent once, alone; failing again it is left UNMARKED with the reason
said. It is never coerced into range — a 9 for a 4-mark question stored as 4
would be a plausible wrong mark the student has no reason to doubt, and this
codebase raises on plausible wrong artifacts rather than producing them.

## Confidence is the model's own report

It is not a calibrated probability, and nothing here pretends otherwise. A
synthesis question has no passage to anchor the judgement, so its confidence is
forced to low whatever the model says. Whether the label carries information at
all is measured, not assumed — see §13.1 of the design.

## When the model is unavailable

Quota, a network failure or a provider error stops the run: marks already given
are kept, the rest are left unmarked, and the outcome carries a sentence the
student can act on. The attempt is already saved by then, so this is a degraded
result, never a crash. Anything else is a bug and propagates.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from coursegen import config
from coursegen.grade.records import (
    MarkingOutcome,
    WrittenEntry,
    WrittenMark,
    WrittenMarker,
)
from coursegen.llm.client import BudgetExceeded, LLMClient, TokenBudget
from coursegen.llm.prompts import build_marking_messages, marking_response_schema

INVALID_MARK = "The marker returned an invalid mark."
STOPPED = "Marking stopped before this answer was marked."
QUOTA_STOP = (
    "Marking stopped: the model's call limit was reached. Your answers are saved — "
    "retry marking later, or mark them yourself."
)
NETWORK_STOP = (
    "Marking stopped: the model could not be reached. Your answers are saved — "
    "retry marking, or mark them yourself."
)
NO_KEY = (
    "No API key is configured, so written answers were not marked. Your answers are "
    "saved — mark them yourself, or add a key and retry marking."
)
_CONFIDENCES = {"high", "low"}


def default_client() -> LLMClient:
    """One submission's client, with a call cap of its own.

    The per-exam cap is for generating a paper; marking one submission is a
    different job with a different worst case — its batches plus one retry each.
    """
    return LLMClient(budget=TokenBudget(per_exam_call_cap=config.PER_SUBMISSION_CALL_CAP))


def make_marker(llm_client_factory: Callable[[], Any] = default_client) -> WrittenMarker:
    """A marker that builds its client only when there is something to mark."""

    def marker(entries: list[WrittenEntry]) -> MarkingOutcome:
        try:
            client = llm_client_factory()
        except EnvironmentError:
            return MarkingOutcome(
                [WrittenMark(e.slot_id, None, None, NO_KEY) for e in entries],
                degraded=NO_KEY,
            )
        return mark_written(entries, client)

    return marker


def mark_written(entries: list[WrittenEntry], llm_client: Any) -> MarkingOutcome:
    marks: dict[str, WrittenMark] = {}
    degraded = None
    try:
        retry: list[WrittenEntry] = []
        for start in range(0, len(entries), config.GRADE_BATCH_SIZE):
            valid, invalid = _mark_batch(entries[start:start + config.GRADE_BATCH_SIZE], llm_client)
            marks.update(valid)
            retry.extend(invalid)
        for entry in retry:
            valid, invalid = _mark_batch([entry], llm_client)
            marks.update(valid)
            for bad in invalid:
                marks[bad.slot_id] = WrittenMark(bad.slot_id, None, None, INVALID_MARK)
    except BudgetExceeded:
        degraded = QUOTA_STOP
    except httpx.HTTPError:
        degraded = NETWORK_STOP
    return MarkingOutcome(
        [marks.get(e.slot_id) or WrittenMark(e.slot_id, None, None, STOPPED) for e in entries],
        degraded=degraded,
    )


def _mark_batch(
    batch: list[WrittenEntry], llm_client: Any
) -> tuple[dict[str, WrittenMark], list[WrittenEntry]]:
    """One call. Returns the valid marks by slot, and the entries to retry."""
    response = llm_client.call(
        build_marking_messages(batch), response_schema=marking_response_schema()
    )
    try:
        raw_marks = _extract_marks(response)
    except (KeyError, IndexError, TypeError, ValueError):
        # Unreadable is the same failure as an invalid mark for every item in it.
        return {}, list(batch)

    by_slot = {e.slot_id: e for e in batch}
    seen: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_marks:
        if isinstance(raw, dict) and raw.get("slot_id") in by_slot:
            seen.setdefault(raw["slot_id"], []).append(raw)

    valid: dict[str, WrittenMark] = {}
    invalid: list[WrittenEntry] = []
    for entry in batch:
        candidates = seen.get(entry.slot_id, [])
        mark = _validate(candidates[0], entry) if len(candidates) == 1 else None
        if mark is None:
            invalid.append(entry)
        else:
            valid[entry.slot_id] = mark
    return valid, invalid


def _extract_marks(response: dict[str, Any]) -> list[Any]:
    data = response if "marks" in response else json.loads(
        response["choices"][0]["message"]["content"]
    )
    marks = data["marks"]
    if not isinstance(marks, list):
        raise TypeError("marks is not a list")
    return marks


def _validate(raw: dict[str, Any], entry: WrittenEntry) -> WrittenMark | None:
    value = raw.get("marks_awarded")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= entry.marks_available:
        return None
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    reason = reason.strip()
    if len(reason) > config.GRADE_REASON_MAX_CHARS:
        return None
    confidence = raw.get("confidence")
    if confidence not in _CONFIDENCES:
        return None
    if not entry.from_material:
        confidence = "low"
    return WrittenMark(entry.slot_id, value, confidence, reason)
