"""Objective marking: the student's option label against the stored key.

No model and no judgement, so this step can neither fail nor cost anything. True
or false is already a two-option MCQ in this codebase, so it takes the same path.
There is no partial credit and no negative marking.
"""
from __future__ import annotations

from typing import Optional

from coursegen.grade.records import SheetItem


def normalise_label(response: str) -> str:
    """What the student picked, compared the way the key is written."""
    return response.strip().upper()


def mark_objective(item: SheetItem, response: str) -> Optional[int]:
    """Full marks for the keyed label, 0 for any other, None when left blank.

    None rather than 0 for a blank: an unanswered question is a different fact
    from a wrong one, and the results screen shows them differently.
    """
    if item.correct_option is None:
        raise ValueError(f"{item.slot_id} has no answer key; it is not an objective item.")
    label = normalise_label(response)
    if not label:
        return None
    return item.marks if label == item.correct_option.strip().upper() else 0
