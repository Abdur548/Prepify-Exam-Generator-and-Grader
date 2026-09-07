"""The dry run — what this paper would be, before spending anything.

The solver is deterministic and makes **zero LLM calls**. It reads the course map,
matches topics, apportions slots by instructional mass and returns a complete
assignment in 5–125 ms. Everything a student needs in order to decide whether a
paper is worth generating is therefore knowable for free.

That is a capability the obvious competitors do not have. A tool that assembles
from a pre-built question bank can only show you arithmetic on counts — *you have
asked for 40 marks and selected types worth 40*. Prepify can show you the real
thing: which of your own uploads feed each section, how many slots the material
actually supports, and where your notes are too thin to fill Section C. Before a
single token is spent.

## What this deliberately does not do

It does not write questions, and it must never be presented as if it had. The plan
says *what would be asked about*, never *what the question will say*. A student who
reads a plan as a paper will be disappointed by the paper.
"""
from __future__ import annotations

from typing import Any

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import ItemSpec


def build_plan(
    specs: list[ItemSpec],
    coverage: CoverageReport,
    blueprint: Blueprint,
    course_map: list[CourseMapNode],
) -> dict[str, Any]:
    """Assemble the dry run. No LLM calls; nothing here costs quota."""
    node_by_id = {n.node_id: n for n in course_map}
    topic_by_section = {t.section_id: t for t in coverage.per_topic}

    sections: list[dict[str, Any]] = []
    for section in blueprint.sections:
        prefix = f"{section.section_id}-"
        section_specs = [s for s in specs if s.slot_id.startswith(prefix)]
        topic = topic_by_section.get(section.section_id)

        # Which of the student's own files feed this section. This is the line
        # that makes a dry run worth reading: not "10 slots planned" but "from
        # 03_search.pdf and 06_CSP.pdf".
        files = sorted({
            node_by_id[s.node_id].source_file
            for s in section_specs
            if s.node_id in node_by_id
        })

        filled = len(section_specs)
        sections.append({
            "section_id": section.section_id,
            "title": section.title,
            "item_type": section.item_type,
            "marks_each": section.marks_each,
            "slots_requested": section.count,
            "slots_planned": filled,
            # A section the material cannot fill is the single most useful thing
            # a dry run can surface, because it is cheap to fix (upload more) and
            # expensive to discover after generating.
            "short_by": max(0, section.count - filled),
            "marks_planned": filled * section.marks_each,
            "from_material": section.grounding != "synthesis",
            "topic": section.topic,
            "matched_nodes": topic.matched_node_count if topic else None,
            "sources": files,
        })

    planned_marks = sum(s["marks_planned"] for s in sections)
    batch = max(1, config.BATCH_SIZE)
    return {
        "blueprint_id": blueprint.blueprint_id,
        "title": blueprint.title,
        "total_marks": blueprint.total_marks,
        "duration_minutes": blueprint.duration_minutes,
        "sections": sections,
        "summary": {
            "slots_total": coverage.slots_total,
            "slots_planned": coverage.slots_filled,
            "fill_ratio": coverage.fill_ratio,
            "allocation_fidelity": coverage.allocation_fidelity,
            "marks_planned": planned_marks,
            "short_by_marks": max(0, blueprint.total_marks - planned_marks),
            "synthesis_items": sum(1 for s in specs if s.grounding == "synthesis"),
            "sources_used": sorted({
                node_by_id[s.node_id].source_file
                for s in specs if s.node_id in node_by_id
            }),
            "bloom_realised": coverage.bloom_realised,
            "bloom_declared": coverage.cognitive_balance_declared,
            "warnings": list(coverage.warnings),
            # An honest cost preview. The student is about to spend their own
            # quota, and "about 4 calls" is a fact we have before they commit.
            "estimated_calls": -(-len(specs) // batch),
        },
    }
