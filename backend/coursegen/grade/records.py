"""The plain data the practice loop passes around. No database, no model, no I/O.

Kept dependency-free on purpose. The store produces these, the marking prompt and
the marker consume them, and mastery is computed from them — if this module
imported any of those, they could not import it back without a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class UnknownSheet(LookupError):
    """No sheet with this id. The API reports it as 404."""


class UnknownAttempt(LookupError):
    """No attempt with this id. The API reports it as 404."""


class InvalidSubmission(ValueError):
    """The request cannot be honoured as sent. The API reports it as 422."""


@dataclass(frozen=True)
class SheetItem:
    """One answerable question on a sheet, as the store keeps it.

    `spec` is the full `ItemSpec` the question was written from, so a fresh
    re-test can be built from it later with every authored field carried over.
    """
    slot_id: str
    node_id: str
    span_ids: list[str]
    spec_hash: str
    item_type: str
    bloom: str
    marks: int
    correct_option: Optional[str]
    from_material: bool
    spec: dict[str, Any]
    # Review items only: the (sheet_id, slot_id) of the question being re-tested.
    retest_of: Optional[tuple[str, str]] = None
    form: Optional[str] = None          # "identical" | "fresh" | "fallback"


@dataclass(frozen=True)
class SheetRecord:
    """An answerable document — a generated paper or a review set."""
    sheet_id: str
    kind: str                           # "paper" | "review"
    blueprint_id: Optional[str]
    title: str
    created_at: str
    document: dict[str, Any]
    items: list[SheetItem] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerRow:
    """One answer with the mark that currently counts for it.

    `marks_awarded` is None both for a blank answer and for one nobody has marked
    yet. Those are different facts — a skipped question is evidence of not
    knowing, an unmarked one is no evidence at all — so `status` keeps them
    apart rather than letting a None stand for both.
    """
    attempt_id: str
    seq: int
    sheet_id: str
    sheet_kind: str
    slot_id: str
    node_id: str
    item_type: str
    marks_available: int
    response: str
    marks_awarded: Optional[int]
    source: Optional[str]               # "key" | "model" | "student"
    confidence: Optional[str]
    reason: Optional[str]
    retest_of: Optional[tuple[str, str]]
    # (marks, source) of the latest non-student mark, present only when the mark
    # that counts is the student's own contest.
    original: Optional[tuple[int, str]]

    @property
    def status(self) -> str:
        if not self.response.strip():
            return "unanswered"
        return "unmarked" if self.marks_awarded is None else "marked"


@dataclass(frozen=True)
class WrittenEntry:
    """Everything the marker sees for one written answer."""
    slot_id: str
    item_type: str
    marks_available: int
    stem: str
    model_answer: str
    explanation: str
    passage: Optional[str]              # None for a synthesis question
    from_material: bool
    response: str


@dataclass(frozen=True)
class WrittenMark:
    """The marker's verdict on one answer. `marks_awarded` None means unmarked,
    and `reason` then says why."""
    slot_id: str
    marks_awarded: Optional[int]
    confidence: Optional[str]
    reason: str


@dataclass(frozen=True)
class MarkingOutcome:
    """What a marking run produced. `degraded` is a student-safe sentence when the
    run stopped early (quota, network, no key), else None."""
    marks: list[WrittenMark]
    degraded: Optional[str] = None


WrittenMarker = Callable[[list[WrittenEntry]], MarkingOutcome]


def document_items(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """slot_id -> item, for every FILLED item in a sheet document.

    An unfilled slot has no question on it, so it is not answerable and never
    appears here.
    """
    return {
        item["slot_id"]: item
        for section in document.get("sections", [])
        for item in section.get("items", [])
        if item.get("filled", True) and "stem" in item
    }
