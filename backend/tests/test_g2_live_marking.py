"""G2 — the marker against the real model. Opt-in: `pytest --live -m live`.

Two answers to one short question, one right and one wrong. The mocked suite
proves every rule around the marker; only a real call proves the prompt and the
response schema are something the provider accepts and answers sensibly — the
generation schema's dangling `$ref` survived every mocked test and failed on the
first live call.
"""
from __future__ import annotations

import pytest

from coursegen.grade.records import WrittenEntry
from coursegen.grade.written import default_client, mark_written

pytestmark = pytest.mark.live

PASSAGE = (
    "Breadth-first search expands the shallowest unexpanded node first. It keeps "
    "the frontier in a FIFO queue, so nodes are expanded level by level."
)


def _entry(slot: str, response: str) -> WrittenEntry:
    return WrittenEntry(
        slot_id=slot, item_type="short", marks_available=4,
        stem="What data structure does breadth-first search keep its frontier in, and why?",
        model_answer="A FIFO queue, so that the shallowest nodes are expanded first.",
        explanation="FIFO order expands nodes level by level.",
        passage=PASSAGE, from_material=True, response=response,
    )


def test_a_right_answer_outscores_a_wrong_one() -> None:
    out = mark_written(
        [
            _entry("B-01", "A first-in first-out queue, so it expands the shallowest nodes first."),
            _entry("B-02", "A stack, because it goes as deep as possible first."),
        ],
        default_client(),
    )
    assert out.degraded is None, out.degraded
    right, wrong = out.marks
    assert right.marks_awarded is not None and wrong.marks_awarded is not None, out.marks
    assert right.marks_awarded > wrong.marks_awarded
    assert right.reason and wrong.reason
