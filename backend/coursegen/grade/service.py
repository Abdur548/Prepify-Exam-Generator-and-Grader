"""The practice operations the API exposes: submit, results, contest, re-mark.

Everything a student can observe about their marks is decided here, so the HTTP
layer stays a thin mapping of exceptions onto status codes.

## What the results never claim

A model's mark is a judgement and is labelled with its source; the student can
contest any mark, and the contest is the mark that counts. Totals print what has
been MARKED beside what was available — an unmarked answer is never counted as
zero, because "31 / 60" read off a paper with two answers still waiting to be
marked is a number the student cannot trust.
"""
from __future__ import annotations

from typing import Any, Optional

from coursegen import config
from coursegen.grade.mark import mark_objective
from coursegen.grade.records import (
    AnswerRow,
    InvalidSubmission,
    MarkingOutcome,
    SheetRecord,
    WrittenEntry,
    WrittenMarker,
    document_items,
)
from coursegen.grade.store import Store

NOT_MARKED_YET = "Written answers are not marked yet — mark them yourself."


def submit_attempt(
    store: Store,
    sheet_id: str,
    responses: dict[str, str],
    *,
    now: str,
    marker: Optional[WrittenMarker] = None,
    unavailable: Optional[str] = None,
) -> dict[str, Any]:
    """Save an attempt, mark what can be marked, return the results.

    `marker` marks written answers (the model, in production). When it cannot be
    offered — no consent, a busy pipeline — the caller passes `unavailable`, a
    student-safe sentence saying why, and the written answers wait unmarked.
    The attempt and its objective marks are saved either way.
    """
    check_lengths(responses)
    sheet = store.get_sheet(sheet_id)
    attempt_id, _seq = store.add_attempt(sheet_id, responses, now)

    for item in sheet.items:
        if item.correct_option is None:
            continue
        marks = mark_objective(item, responses.get(item.slot_id, ""))
        if marks is not None:
            store.add_mark(attempt_id, item.slot_id, marks, "key", at=now)

    status, warnings = "ok", []
    entries = written_entries(store, attempt_id, only_unmarked=True)
    if entries:
        if marker is not None:
            outcome = marker(entries)
            warnings.extend(apply_written(store, attempt_id, outcome, now=now))
            if outcome.degraded:
                status = "degraded"
                warnings.insert(0, outcome.degraded)
        elif unavailable:
            status, warnings = "degraded", [unavailable]
        else:
            warnings.append(NOT_MARKED_YET)
    return results(store, attempt_id, status=status, warnings=warnings)


def remark(
    store: Store,
    attempt_id: str,
    *,
    marker: Optional[WrittenMarker],
    unavailable: Optional[str] = None,
    now: str,
) -> dict[str, Any]:
    """Mark the written answers that still have no mark at all.

    "No mark at all" is deliberate: an answer the student marked themselves while
    the model was unavailable carries a mark, and sending it to the model now
    would make the model's mark the latest one — silently replacing the student's.
    """
    entries = written_entries(store, attempt_id, only_unmarked=True)
    if not entries:
        return results(store, attempt_id)
    if marker is None:
        return results(store, attempt_id, status="degraded",
                       warnings=[unavailable or NOT_MARKED_YET])
    outcome = marker(entries)
    warnings = apply_written(store, attempt_id, outcome, now=now)
    if outcome.degraded:
        return results(store, attempt_id, status="degraded",
                       warnings=[outcome.degraded, *warnings])
    return results(store, attempt_id, warnings=warnings)


def needs_marker(store: Store, sheet_id: str, responses: dict[str, str]) -> bool:
    """Whether this submission would send anything to the model.

    Only a non-blank answer to a written question does. A blank one is
    unanswered, and an objective one is marked against the key.
    """
    sheet = store.get_sheet(sheet_id)
    return any(
        item.correct_option is None and responses.get(item.slot_id, "").strip()
        for item in sheet.items
    )


def written_entries(
    store: Store, attempt_id: str, *, only_unmarked: bool
) -> list[WrittenEntry]:
    """The non-blank written answers of an attempt, as the marker needs them."""
    sheet = store.get_sheet(store.attempt_sheet_id(attempt_id))
    items = {i.slot_id: i for i in sheet.items}
    doc = document_items(sheet.document)
    entries = []
    for row in store.attempt_rows(attempt_id):
        item = items[row.slot_id]
        if item.correct_option is not None or row.status == "unanswered":
            continue
        if only_unmarked and store.has_marks(attempt_id, row.slot_id):
            continue
        d = doc.get(row.slot_id, {})
        entries.append(WrittenEntry(
            slot_id=row.slot_id,
            item_type=row.item_type,
            marks_available=row.marks_available,
            stem=d.get("stem", ""),
            model_answer=d.get("model_answer", ""),
            explanation=d.get("explanation", ""),
            passage=d.get("source_excerpt") if item.from_material else None,
            from_material=item.from_material,
            response=row.response,
        ))
    return entries


def apply_written(
    store: Store, attempt_id: str, outcome: MarkingOutcome, *, now: str
) -> list[str]:
    """Store the model's marks. Returns a warning for each answer left unmarked."""
    warnings = []
    for mark in outcome.marks:
        if mark.marks_awarded is None:
            warnings.append(f"{mark.slot_id}: {mark.reason}")
            continue
        store.add_mark(attempt_id, mark.slot_id, mark.marks_awarded, "model",
                       confidence=mark.confidence, reason=mark.reason, at=now)
    return warnings


def contest(
    store: Store, attempt_id: str, slot_id: str, marks_awarded: int, *, now: str
) -> dict[str, Any]:
    """The student's own mark, which becomes the mark that counts."""
    row = next((r for r in store.attempt_rows(attempt_id) if r.slot_id == slot_id), None)
    if row is None:
        raise InvalidSubmission(f"{slot_id!r} is not a question on this sheet.")
    if row.status == "unanswered":
        raise InvalidSubmission(f"There is no answer to mark for {slot_id}.")
    store.add_mark(attempt_id, slot_id, marks_awarded, "student", at=now)
    return next(i for i in results(store, attempt_id)["items"] if i["slot_id"] == slot_id)


def results(
    store: Store,
    attempt_id: str,
    *,
    status: str = "ok",
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    sheet = store.get_sheet(store.attempt_sheet_id(attempt_id))
    rows = store.attempt_rows(attempt_id)
    items = [_item_result(row, sheet) for row in rows]
    marked = [r for r in rows if r.status == "marked"]
    counted = [r for r in rows if r.status in ("marked", "unanswered")]
    return {
        "attempt_id": attempt_id,
        "sheet_id": sheet.sheet_id,
        "sheet_kind": sheet.kind,
        "status": status,
        "totals": {
            "marks_awarded": sum(r.marks_awarded or 0 for r in marked),
            # What has been marked, so the total is read against it — a blank
            # answer has been marked (zero), an unmarked one has not.
            "marks_marked": sum(r.marks_available for r in counted),
            "marks_available": sum(r.marks_available for r in rows),
            "unmarked": sum(1 for r in rows if r.status == "unmarked"),
            "unanswered": sum(1 for r in rows if r.status == "unanswered"),
        },
        "items": items,
        "warnings": list(warnings or []),
    }


def _item_result(row: AnswerRow, sheet: SheetRecord) -> dict[str, Any]:
    doc = document_items(sheet.document).get(row.slot_id, {})
    from_material = bool(doc.get("from_material", True))
    return {
        "slot_id": row.slot_id,
        "item_type": row.item_type,
        "marks_available": row.marks_available,
        "response": row.response,
        "status": row.status,
        "marks_awarded": row.marks_awarded,
        "source": row.source,
        "confidence": row.confidence,
        "reason": row.reason,
        "original": (
            {"marks_awarded": row.original[0], "source": row.original[1]}
            if row.original else None
        ),
        # The student is disagreeing with the answer KEY, which the UI says in
        # those words: a generated key can be wrong, and contesting it is allowed.
        "objective_contest": row.source == "student" and doc.get("correct_option") is not None,
        "form": doc.get("form"),
        "feedback": {
            "stem": doc.get("stem", ""),
            "options": doc.get("options", []),
            "correct_option": doc.get("correct_option"),
            "model_answer": doc.get("model_answer", ""),
            "explanation": doc.get("explanation", ""),
            "from_material": from_material,
            "topic": doc.get("topic"),
            # Where to revise. A synthesis question was written to need something
            # new, so it points at no page — pointing at its context passage would
            # send the student hunting for an answer that was never there.
            "revise": doc.get("source") if from_material else None,
            "source_excerpt": doc.get("source_excerpt") if from_material else None,
        },
    }


def check_lengths(responses: dict[str, str]) -> None:
    for slot_id, text in responses.items():
        if not isinstance(text, str):
            raise InvalidSubmission(f"{slot_id}: an answer must be text.")
        if len(text) > config.MAX_ANSWER_CHARS:
            raise InvalidSubmission(
                f"{slot_id}: answers are limited to {config.MAX_ANSWER_CHARS} characters "
                f"(this one has {len(text)})."
            )
