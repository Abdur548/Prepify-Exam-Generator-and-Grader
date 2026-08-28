from __future__ import annotations

from pydantic import BaseModel


class NodeCoverage(BaseModel):
    node_id: str
    path: list[str]
    instructional_mass: float
    marks_allocated: int
    slots: list[str]


class CoverageReport(BaseModel):
    """Three ratios that answer three different questions — and can disagree.

    - ``coverage_ratio``      — how much of the SYLLABUS was touched.
    - ``fill_ratio``          — how much of the PAPER got filled.
    - ``allocation_fidelity`` — how much of the paper was placed BY MASS rather
      than by exhaustion.

    They are not substitutes for one another. On a corpus where every node
    yields exactly one span (lecture slides — the primary use case), Hare
    apportionment routinely asks a node for more slots than it has spans; the
    surplus falls through to whichever node still has a span left. That paper
    can report ``coverage_ratio == 1.000`` and ``fill_ratio == 1.000`` while a
    large share of the allocation mechanism never operated.
    ``allocation_fidelity`` is the only field that shows it.
    """

    blueprint_id: str
    nodes_total: int
    nodes_covered: int
    coverage_ratio: float   # nodes TOUCHED / nodes total — says nothing about slots being filled
    slots_total: int        # sum of section.count across the blueprint
    slots_filled: int       # slots_total - len(unfilled_slots)
    fill_ratio: float       # slots_filled / slots_total — the number that proves the paper is complete
    slots_by_mass: int          # filled by a node that still had positive apportionment deficit
    slots_by_fallthrough: int   # filled by a node whose deficit was already zero
    allocation_fidelity: float  # slots_by_mass / slots_filled (0.0 when slots_filled == 0)
    mass_covered: float
    per_node: list[NodeCoverage]
    unfilled_slots: list[str]
    warnings: list[str]
