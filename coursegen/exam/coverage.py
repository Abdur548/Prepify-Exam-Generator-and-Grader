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
) -> CoverageReport:
    covered_ids = set(node_slot_map.keys())
    nodes_total = len(course_map)
    nodes_covered = len(covered_ids)
    coverage_ratio = nodes_covered / nodes_total if nodes_total > 0 else 0.0
    mass_covered = sum(
        n.instructional_mass for n in course_map if n.node_id in covered_ids
    )

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
        mass_covered=mass_covered,
        per_node=per_node,
        unfilled_slots=unfilled_slots,
        warnings=warnings,
    )
