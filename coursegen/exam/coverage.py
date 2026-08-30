"""Coverage report builder. Imported by allocate.py after slot fill is complete."""
from __future__ import annotations

from collections import defaultdict

from typing import Optional

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport, NodeCoverage, TopicCoverage
from coursegen.contracts.item import ItemSpec


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
    per_topic: Optional[list[TopicCoverage]] = None,
    items: Optional[list[ItemSpec]] = None,
    cognitive_balance: Optional[dict[str, float]] = None,
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

    bloom_realised = _bloom_realised(items)
    all_warnings = list(warnings) + _cognitive_balance_warnings(
        declared=cognitive_balance, realised=bloom_realised
    )

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
        # None means "no authored topics in this blueprint" (the derived path),
        # which reports as an empty list — never as a missing field.
        per_topic=[] if per_topic is None else list(per_topic),
        bloom_realised=bloom_realised,
        # None means "the blueprint declared no target", which reports as None —
        # distinct from an empty dict, which would claim a target of nothing.
        cognitive_balance_declared=(
            None if cognitive_balance is None else dict(cognitive_balance)
        ),
        unfilled_slots=unfilled_slots,
        warnings=all_warnings,
    )


def _bloom_realised(items: Optional[list[ItemSpec]]) -> dict[str, float]:
    """Bloom level → fraction of the EMITTED items carrying it.

    Empty when nothing was emitted: a paper with no items has no realised
    distribution, and inventing one (or dividing by zero) would be worse than
    saying nothing. Keys are sorted so the report is byte-stable.
    """
    if not items:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.bloom] += 1
    total = len(items)
    return {level: counts[level] / total for level in sorted(counts)}


def _cognitive_balance_warnings(
    declared: Optional[dict[str, float]],
    realised: dict[str, float],
) -> list[str]:
    """Compare the blueprint's declared Bloom target against what was emitted.

    A WARNING, never a raise. The realised distribution is an allocation
    OUTCOME — a section that filled short cannot hit its declared share, and an
    under-filled paper missing its target is information, not a crash. This is
    also why the check cannot live on the Blueprint as a pydantic validator:
    nothing at parse time knows how many slots will fill.

    Levels are taken from the UNION of both sides, not just the declared keys. A
    level the target never mentions still counts against it — that is exactly how
    a paper drifts to easy recall questions the blueprint never asked for, and
    iterating only the declared keys would look straight past it. A level absent
    from either side contributes 0.0 on that side.
    """
    if declared is None:
        return []
    return [
        (
            f"Cognitive balance for {level!r}: declared {declared.get(level, 0.0):.2f}, "
            f"realised {realised.get(level, 0.0):.2f} — divergence "
            f"{abs(realised.get(level, 0.0) - declared.get(level, 0.0)):.2f} exceeds "
            f"COGNITIVE_BALANCE_TOLERANCE={config.COGNITIVE_BALANCE_TOLERANCE}."
        )
        for level in sorted(set(declared) | set(realised))
        if abs(realised.get(level, 0.0) - declared.get(level, 0.0))
        > config.COGNITIVE_BALANCE_TOLERANCE
    ]
