from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SectionSpec(BaseModel):
    section_id: str                              # e.g. "A"
    title: str                                   # e.g. "Multiple Choice"
    item_type: Literal["mcq", "short", "long"]
    count: int
    marks_each: int
    # Must be non-empty: allocate.py cycles bloom with `bloom_list[idx % len(bloom_list)]`,
    # so an empty list is a ZeroDivisionError at solve time.
    bloom: list[str] = Field(min_length=1)
    options_count: Optional[int] = None         # mcq only
    requires_flags_any: Optional[list[str]] = None  # e.g. ["has_figure"]


class Blueprint(BaseModel):
    blueprint_id: str
    title: str
    total_marks: int
    duration_minutes: int
    sections: list[SectionSpec]

    @model_validator(mode="after")
    def _section_marks_sum_to_total(self) -> "Blueprint":
        """A hand-authored blueprint must not print 'Total: 100 marks' over a 95-mark paper."""
        computed = sum(s.count * s.marks_each for s in self.sections)
        if computed != self.total_marks:
            raise ValueError(
                f"Blueprint {self.blueprint_id!r}: sections sum to {computed} marks "
                f"but total_marks is {self.total_marks}."
            )
        return self
