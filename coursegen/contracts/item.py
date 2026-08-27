from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MCQOption(BaseModel):
    label: str   # e.g. "A"
    text: str


class SourceRef(BaseModel):
    file: str
    pages: list[int]


class ItemSpec(BaseModel):
    """Output of the solver, input to the LLM. Zero LLM involvement in creation."""
    slot_id: str          # e.g. "A-01"
    item_type: str
    marks: int
    bloom: str
    node_id: str
    span_ids: list[str]   # 1 for mcq/short, 1-2 for long
    eligibility: list[str]
    spec_hash: str        # sha256(node_id + span_ids + item_type + bloom) — cache key


class GeneratedItem(BaseModel):
    """Schema-constrained LLM output. Every field produced in one call from one span."""
    slot_id: str
    stem: str
    options: Optional[list[MCQOption]] = None
    correct_option: Optional[str] = None
    model_answer: str
    explanation: str
    source_ref: SourceRef
