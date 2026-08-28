"""Coverage report builder. Imported by allocate.py after slot fill is complete."""
from __future__ import annotations

from collections import defaultdict

from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport, NodeCoverage


def build_report(
    blueprint_id: str,
    course_map: list[CourseMapNode],
    node_slot_map: dict[str, list[str]],
    node_marks_map: dict[str, int],
    unfilled_slots: list[str],
    warnings: list[str],
    slots_total: int,
    slots_by_mass: int,
    slots_by_fallthrough: int,
) -> CoverageReport:
    covered_ids = set(node_slot_map.keys())
    nodes_total = len(course_map)
    nodes_covered = len(covered_ids)
    coverage_ratio = nodes_covered / nodes_total if nodes_total > 0 else 0.0
    mass_covered = sum(
        n.instructional_mass for n in course_map if n.node_id in covered_ids
    )

    # coverage_ratio counts nodes TOUCHED. It reads 1.00 on a paper that is missing
    # four questions. fill_ratio is what proves the paper is actually complete.
    slots_filled = slots_total - len(unfilled_slots)
    fill_ratio = slots_filled / slots_total if slots_total > 0 else 0.0

    # Every filled slot was placed through exactly one of the solver's two branches:
    # the node still had apportionment deficit (mass), or it did not (fallthrough).
    # A mismatch means a slot was filled by a third path neither branch counted —
    # that is a real bug, and it should crash rather than report a plausible number.
    #
    # Raised explicitly rather than asserted: `python -O` strips `assert`, which would
    # turn the one check standing between a broken count and a believable-looking
    # coverage table into a no-op. A silent failure here produces exactly the
    # plausible-wrong report this metric exists to prevent.
    if slots_by_mass + slots_by_fallthrough != slots_filled:
        raise ValueError(
            f"Slot accounting broken: slots_by_mass={slots_by_mass} + "
            f"slots_by_fallthrough={slots_by_fallthrough} != slots_filled={slots_filled}"
        )

    # fill_ratio proves the paper is complete; it says nothing about HOW it was
    # filled. allocation_fidelity is the share placed because mass asked for it,
    # rather than because the mass-preferred nodes had no spans left.
    allocation_fidelity = slots_by_mass / slots_filled if slots_filled > 0 else 0.0

    per_node = [
        NodeCoverage(
            node_id=n.node_id,
            path=n.path,
            instructional_mass=n.instructional_mass,
            marks_allocated=node_marks_map.get(n.node_id, 0),
            slots=node_slot_map.get(n.node_id, []),
        )
        for n in sorted(course_map, key=lambda n: n.node_id)
    ]

    return CoverageReport(
        blueprint_id=blueprint_id,
        nodes_total=nodes_total,
        nodes_covered=nodes_covered,
        coverage_ratio=coverage_ratio,
        slots_total=slots_total,
        slots_filled=slots_filled,
        fill_ratio=fill_ratio,
        slots_by_mass=slots_by_mass,
        slots_by_fallthrough=slots_by_fallthrough,
        allocation_fidelity=allocation_fidelity,
        mass_covered=mass_covered,
        per_node=per_node,
        unfilled_slots=unfilled_slots,
        warnings=warnings,
    )
