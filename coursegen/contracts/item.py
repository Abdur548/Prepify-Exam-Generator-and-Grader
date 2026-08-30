from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, model_validator


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
    # Carried through from SectionSpec, already validated against
    # config.KNOWN_FORMAT_REQUIREMENTS there. None on every item the three shipped
    # blueprints produce.
    format_requirement: Optional[str] = None
    # Shared by the N items a single sub-questioned task expands into (§8.2). The
    # renderer will later group items carrying the same group_id under one
    # question number; nothing renders it yet.
    group_id: Optional[str] = None
    # sha256(node_id + span_ids + item_type + bloom + marks + options_count
    #        [+ format_requirement, appended only when it is set]) — cache key.
    # marks and options_count are in the hash because the cache is shared across papers:
    # a 4-mark short question (midterm) and a 5-mark short question (final) can be drawn
    # from the same node+span+bloom, and must not collide.
    #
    # format_requirement JOINS the hash because it changes the prompt: two items
    # differing only in format are different questions and must not share a
    # cached generation.
    #
    # group_id is deliberately NOT in the hash. It is PRESENTATIONAL — it changes
    # how items are DISPLAYED, not what is asked — so regrouping sub-questions
    # must not invalidate cached generations.
    spec_hash: str


class GeneratedItem(BaseModel):
    """Schema-constrained LLM output. Every field produced in one call from one span."""
    slot_id: str
    stem: str
    options: Optional[list[MCQOption]] = None
    correct_option: Optional[str] = None
    model_answer: str
    explanation: str
    source_ref: SourceRef

    @model_validator(mode="after")
    def _options_and_correct_option_agree(self) -> "GeneratedItem":
        """P3's first validation gate is a Pydantic parse, so it has to actually check something.

        There is no `item_type` field here, so item-type-specific rules are not
        expressible. What IS checkable is internal consistency: options and
        correct_option are present together, and correct_option names a real option.
        """
        if self.options is not None:
            if self.correct_option is None:
                raise ValueError(
                    f"Item {self.slot_id!r}: options are set but correct_option is None."
                )
            labels = [o.label for o in self.options]
            if self.correct_option not in labels:
                raise ValueError(
                    f"Item {self.slot_id!r}: correct_option {self.correct_option!r} "
                    f"is not one of the option labels {labels!r}."
                )
        elif self.correct_option is not None:
            raise ValueError(
                f"Item {self.slot_id!r}: correct_option is set but options is None."
            )
        return self
