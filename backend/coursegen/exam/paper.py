"""The paper as structured data, for a client that renders it itself.

`render_exam_artifacts` produces HTML and PDF — finished documents, right for
printing and wrong for a UI. A frontend that wants to badge each question with its
source, filter out the ones not drawn from the student's material, or reveal the
passage a question came from cannot do any of that against a rendered blob.

Before this existed `/api/exam` returned `items_count` — a number — and the paper
itself was reachable only as `/api/files/exam.html`. Everything the design calls
for (provenance chips, the drag-to-source panel, "from my material only") was
therefore impossible to build, and the gap was invisible because the HTML looked
complete.

This module assembles the same paper as a document a client can reason about. It
adds nothing the pipeline does not already know: every field here is copied from
an `ItemSpec`, a `GeneratedItem`, the `Blueprint` or the span text. Nothing is
inferred, and nothing is generated.

## The two fields that exist for the UI's sake

- **`from_material`** — `grounding == "span"`. A synthesis item is written to
  invent its artifact, so it is not answerable from the upload. The manifest has
  always counted these; this is the first time a client can mark the individual
  question, which is what stops a student concluding their notes are incomplete.
- **`source_excerpt`** — the actual span text the question was written from. The
  provenance panel needs the passage, not just the filename.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem, ItemSpec

# A span can run to thousands of characters. The provenance panel shows a passage,
# not a document, and shipping the whole corpus to the client per question would
# make the payload larger than the paper.
EXCERPT_MAX_CHARS = 900


@dataclass(frozen=True)
class PaperDocument:
    data: dict[str, Any]


def build_paper(
    items: list[GeneratedItem],
    specs: list[ItemSpec],
    blueprint: Blueprint,
    coverage: CoverageReport,
    title: str,
    span_text_by_id: Optional[dict[str, str]] = None,
    factuality: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Assemble the paper as JSON, grouped by section in blueprint order.

    `factuality` maps slot_id -> verdict when gate 5 ran. Absent means the gate did
    not run, which is reported as `null` rather than omitted: a client must be able
    to tell "not checked" from "checked and unsupported".
    """
    spec_by_slot = {s.slot_id: s for s in specs}
    item_by_slot = {i.slot_id: i for i in items}
    span_text_by_id = span_text_by_id or {}
    factuality = factuality or {}

    sections: list[dict[str, Any]] = []
    for section in blueprint.sections:
        prefix = f"{section.section_id}-"
        slot_ids = sorted(s.slot_id for s in specs if s.slot_id.startswith(prefix))

        rendered: list[dict[str, Any]] = []
        for slot_id in slot_ids:
            spec = spec_by_slot[slot_id]
            item = item_by_slot.get(slot_id)
            if item is None:
                # An unfilled slot is part of the paper's truth. The renderer shows
                # a gap where a question should be, rather than silently closing up
                # and leaving the student to wonder why Section C is short.
                rendered.append({
                    "slot_id": slot_id,
                    "filled": False,
                    "marks": spec.marks,
                    "item_type": spec.item_type,
                })
                continue
            rendered.append(_render_item(spec, item, span_text_by_id, factuality))

        sections.append({
            "section_id": section.section_id,
            "title": section.title,
            "item_type": section.item_type,
            "marks_each": section.marks_each,
            "count": section.count,
            "instruction": _section_instruction(section, len(slot_ids)),
            "items": rendered,
        })

    synthesis = sum(1 for s in specs if s.grounding == "synthesis")
    return {
        "title": title,
        "blueprint_id": blueprint.blueprint_id,
        "blueprint_title": blueprint.title,
        "total_marks": blueprint.total_marks,
        "duration_minutes": blueprint.duration_minutes,
        "instructions": _general_instructions(blueprint, coverage),
        "sections": sections,
        "summary": {
            "items_total": len(items),
            "slots_total": coverage.slots_total,
            "fill_ratio": coverage.fill_ratio,
            "allocation_fidelity": coverage.allocation_fidelity,
            "unfilled_slots": list(coverage.unfilled_slots),
            "marks_available": sum(
                s.marks for s in specs if s.slot_id in item_by_slot
            ),
            # Counted, not hidden. A paper where most questions cannot be answered
            # from the upload is a legitimate blueprint outcome and a fact the
            # student needs before they start revising from it.
            "synthesis_items": synthesis,
            "warnings": list(coverage.warnings),
        },
    }


def _render_item(
    spec: ItemSpec,
    item: GeneratedItem,
    span_text_by_id: dict[str, str],
    factuality: dict[str, str],
) -> dict[str, Any]:
    from_material = spec.grounding == "span"
    source = item.source_ref
    return {
        "slot_id": spec.slot_id,
        "filled": True,
        "stem": item.stem,
        "options": [{"label": o.label, "text": o.text} for o in (item.options or [])],
        "correct_option": item.correct_option,
        "model_answer": item.model_answer,
        "explanation": item.explanation,
        "marks": spec.marks,
        "item_type": spec.item_type,
        "bloom": spec.bloom,
        # The provenance block. `from_material` False means the question was
        # deliberately written to require synthesis, and `source` is then the
        # context it was written against rather than a passage that answers it —
        # which is why the client must not present it as an answer location.
        "from_material": from_material,
        "source": (
            {"file": source.file, "pages": list(source.pages)} if source else None
        ),
        "source_excerpt": _excerpt(spec, span_text_by_id) if from_material else None,
        # None means gate 5 did not run. Distinct from a negative verdict.
        "factuality": factuality.get(spec.slot_id),
    }


def _excerpt(spec: ItemSpec, span_text_by_id: dict[str, str]) -> Optional[str]:
    text = "\n".join(
        span_text_by_id[sid] for sid in spec.span_ids if sid in span_text_by_id
    ).strip()
    if not text:
        return None
    if len(text) <= EXCERPT_MAX_CHARS:
        return text
    return text[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "…"


def _section_instruction(section, filled: int) -> str:
    """The line that sits under a section heading on a real exam paper.

    Built from the blueprint rather than authored, so it cannot drift from the
    paper it describes. Uses the FILLED count, not the requested one — an
    instruction promising ten questions above a section holding eight is worse
    than no instruction.
    """
    noun = "question" if filled == 1 else "questions"
    each = f"{section.marks_each} mark{'s' if section.marks_each != 1 else ''} each"
    return f"{filled} {noun}, {each}."


def _general_instructions(blueprint: Blueprint, coverage: CoverageReport) -> list[str]:
    """The numbered block a student reads before starting.

    Derived, never written down. TestMacher's papers carry the same block and it is
    most of what makes a generated paper read as a real one.
    """
    lines = [
        f"There are {coverage.slots_filled} questions in this paper.",
        f"The paper carries {blueprint.total_marks} marks and allows "
        f"{blueprint.duration_minutes} minutes.",
    ]
    for section in blueprint.sections:
        lines.append(
            f"Section {section.section_id} — {section.title}: "
            f"{section.count} × {section.marks_each} marks."
        )
    return lines
