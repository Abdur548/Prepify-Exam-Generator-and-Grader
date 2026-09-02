from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from coursegen import config


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
    # Free-text topic for an AUTHORED blueprint, e.g.
    # "Uninformed and Informed Search (BFS, DFS, A*, Heuristics)".
    # None is NOT a degenerate case — it IS the derived path and the existing
    # product: no topic filter, candidates are the whole course map, allocation
    # mass-proportional across everything. All three shipped blueprints leave it
    # None. One code path in allocate.py serves both modes.
    topic: Optional[str] = None
    # Generation-facing question format (§8.1). Deliberately NOT part of
    # `item_type`: that field drives renderer layout and gate selection — gate 4
    # (MCQ hygiene) keys off `item_type == "mcq"` — so folding TRUE_FALSE_SERIES
    # into it would either subject the item to MCQ rules that do not fit or force
    # gate 4 to learn every new format. Validated against
    # config.KNOWN_FORMAT_REQUIREMENTS below. None means "no finer instruction",
    # which is what all three shipped blueprints say.
    format_requirement: Optional[str] = None
    # Sub-questions (§8.2). A task with four sub-parts becomes four ItemSpecs
    # sharing this id, NOT one nested composite: everything downstream assumes
    # one ItemSpec = one item (span uniqueness, the spec_hash cache, per-item
    # validation, all four gates, slots_filled, allocation_fidelity). The
    # renderer will later group items sharing a group_id under one question
    # number — that is a LATER stage; nothing renders it yet.
    group_id: Optional[str] = None
    # Proportional Bloom mix within this section, e.g. {"remember": 0.6,
    # "understand": 0.4}. Without it the section cycles `bloom` round-robin, so
    # ["remember", "understand"] can only ever mean an even 50/50 — "60%
    # remembering, 40% understanding" is inexpressible. Absence IS the round-robin
    # and the regression guard; all three shipped blueprints leave it None.
    #
    # Must be non-empty for the same reason `bloom` must be: a mix naming no
    # levels apportions nothing, and reaches _hare_apportionment as a
    # ZeroDivisionError at solve time.
    bloom_mix: Optional[dict[str, float]] = Field(default=None, min_length=1)
    # How this section's items relate to their source span (§6.4).
    #
    #   "span"      — current behaviour and the default. The item is WRITTEN FROM
    #                 its span, and validation gate 2 (groundedness) scores the
    #                 model answer against that span.
    #   "synthesis" — the model INVENTS the artifact (a novel adversarial game
    #                 tree, a novel word problem). The span is context, not the
    #                 thing being reproduced, so gate 2 has nothing to score
    #                 against and records the item as NOT APPLICABLE — never as
    #                 a pass, and never as a failure.
    #
    # This is not a loophole for ungrounded questions: §7 records that a
    # synthesis item is not studiable from the upload, so the count reaches the
    # run_manifest and warns past config.SYNTHESIS_ITEM_WARN_RATIO.
    #
    # A Literal rather than a config set, unlike KNOWN_FORMAT_REQUIREMENTS:
    # these two values are not a growing vocabulary, they are a branch that code
    # in validate.py switches on. A third value would need code, not data.
    grounding: Literal["span", "synthesis"] = "span"
    # Per-section free text handed to the item writer, e.g. "Generate a novel
    # adversarial game tree with terminal leaf values; require the student to
    # show pruned branches." (§6.6)
    #
    # Goes in the USER message, NEVER the system prompt: L10 requires the system
    # prompt stay byte-identical across calls so provider-side prompt caching
    # applies, and templating variable content into it would defeat that on
    # every single call.
    #
    # S2 — this is author-supplied text arriving in the prompt AS INSTRUCTIONS
    # rather than as delimited data, which is the one thing SYSTEM_PROMPT tells
    # the model that source spans are not. That is safe only while blueprints
    # are authored by the project. The moment users can supply their own
    # blueprints this becomes a live prompt-injection surface and the text needs
    # the same delimiting-and-distrusting treatment source spans already get.
    # See todo.md.
    generation_instructions: Optional[str] = None

    @model_validator(mode="after")
    def _format_requirement_is_known(self) -> "SectionSpec":
        """An unrecognised format is a malformed blueprint, not a no-op.

        A typo'd format that fell through would generate an ordinary question
        while the blueprint LOOKED honoured — the plausible-but-wrong artifact.
        Raised rather than asserted: `python -O` strips `assert`.

        Checked here at the authoring boundary rather than again on ItemSpec:
        ItemSpec is the solver's output and copies this field from an already
        validated SectionSpec, so the blueprint is the only place a typo enters.
        """
        if (
            self.format_requirement is not None
            and self.format_requirement not in config.KNOWN_FORMAT_REQUIREMENTS
        ):
            raise ValueError(
                f"Section {self.section_id!r}: unknown format_requirement "
                f"{self.format_requirement!r}. Known values are "
                f"{sorted(config.KNOWN_FORMAT_REQUIREMENTS)}. Add it to "
                f"config.KNOWN_FORMAT_REQUIREMENTS if it is a real format."
            )
        return self

    @model_validator(mode="after")
    def _bloom_mix_names_only_listed_levels(self) -> "SectionSpec":
        """A mix naming a level the section does not list is malformed.

        Silently ignoring the stray level would apportion slots to a Bloom level
        the section never declared — or, worse, drop the proportion and quietly
        redistribute it — and the paper would still look like the blueprint asked
        for it. Raised, not asserted, for the same `python -O` reason.
        """
        if self.bloom_mix is None:
            return self
        stray = sorted(set(self.bloom_mix) - set(self.bloom))
        if stray:
            raise ValueError(
                f"Section {self.section_id!r}: bloom_mix names {stray} which "
                f"{'is' if len(stray) == 1 else 'are'} not in bloom={self.bloom!r}. "
                f"A mix may only apportion levels the section lists."
            )
        return self


class Blueprint(BaseModel):
    blueprint_id: str
    title: str
    total_marks: int
    duration_minutes: int
    sections: list[SectionSpec]
    # DECLARED exam-level Bloom target, e.g. {"remember": 0.2, "apply": 0.7,
    # "evaluate": 0.1} — the aggregate the sections are meant to sum to.
    #
    # NOT validated here, and deliberately so: it is a check on the REALISED
    # distribution, which is not knowable when the Blueprint is parsed. What each
    # section actually contributes depends on how many of its slots filled, which
    # is an allocation outcome. CoverageReport carries the realised mix beside
    # this target and WARNS on divergence beyond
    # config.COGNITIVE_BALANCE_TOLERANCE — it does not raise, because an
    # under-filled paper legitimately misses its target.
    cognitive_balance: Optional[dict[str, float]] = None

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
