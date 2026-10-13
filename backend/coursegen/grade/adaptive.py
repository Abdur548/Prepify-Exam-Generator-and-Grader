"""The next paper, aimed at what the student has not yet learned (§8).

Shared by the dry run (`/api/plan`) and generation (`pipeline.generate_paper`), so
the preview a student reads is the paper they then get.
"""
from __future__ import annotations

from dataclasses import dataclass

from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import ItemSpec
from coursegen.exam import allocate
from coursegen.grade.mastery import node_mastery, split_orphans
from coursegen.grade.store import Store
from coursegen.grade.tilt import avoid_repeats, mass_tilt

NOT_ADAPTED = "No answers yet — this paper is not adapted."


@dataclass(frozen=True)
class AdaptResult:
    specs: list[ItemSpec]
    coverage: CoverageReport
    # The paper the same blueprint would give with no history, for comparison.
    base_specs: list[ItemSpec]
    tilted_nodes: int
    reworded: int
    orphans: int

    @property
    def applied(self) -> bool:
        """True when the student's history changed anything at all."""
        return self.tilted_nodes > 0 or self.reworded > 0

    @property
    def changed_slots(self) -> int:
        """Slots now testing a different node than they would without history."""
        base = {s.slot_id: s.node_id for s in self.base_specs}
        return sum(1 for s in self.specs if base.get(s.slot_id) != s.node_id)

    def summary(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "tilted_nodes": self.tilted_nodes,
            "changed_slots": self.changed_slots,
            "reworded": self.reworded,
            "orphans": self.orphans,
        }


def adapt(nodes: list[CourseMapNode], blueprint: Blueprint, store: Store) -> AdaptResult:
    node_ids = {n.node_id for n in nodes}
    rows, orphans = split_orphans(store.all_answer_rows(), node_ids)
    tilt = mass_tilt(node_mastery(rows), node_ids)
    # Called through the module so the solver can be observed in tests.
    base_specs, base_coverage = allocate.solve(nodes, blueprint)
    if tilt:
        specs, coverage = allocate.solve(nodes, blueprint, mass_tilt=tilt)
    else:
        specs, coverage = base_specs, base_coverage
    specs, reworded = avoid_repeats(
        specs, store.submitted_spec_hashes(), store.seen_stems_by_node()
    )
    return AdaptResult(
        specs=specs, coverage=coverage, base_specs=base_specs,
        tilted_nodes=len(tilt), reworded=reworded, orphans=orphans,
    )
