from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class SectionSpec(BaseModel):
    section_id: str                              # e.g. "A"
    title: str                                   # e.g. "Multiple Choice"
    item_type: Literal["mcq", "short", "long"]
    count: int
    marks_each: int
    bloom: list[str]
    options_count: Optional[int] = None         # mcq only
    requires_flags_any: Optional[list[str]] = None  # e.g. ["has_figure"]


class Blueprint(BaseModel):
    blueprint_id: str
    title: str
    total_marks: int
    duration_minutes: int
    sections: list[SectionSpec]
