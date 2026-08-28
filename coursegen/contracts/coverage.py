from __future__ import annotations

from pydantic import BaseModel, Field


class NodeCoverage(BaseModel):
    node_id: str
    path: list[str]
    instructional_mass: float
    marks_allocated: int
    slots: list[str]


class TopicCoverage(BaseModel):
    """One row per AUTHORED topic — what the student's own material could answer.

    ``matched_node_count == 0`` with ``slots_filled == 0`` is the row that matters
    most: the blueprint asked for marks on a topic the uploaded documents do not
    cover, and those slots are deliberately left unfilled rather than quietly
    refilled from the whole course map.

    ``best_score`` records the near-miss. A topic that matches WEAKLY is more
    dangerous than one that matches nothing, because it silently draws from the
    wrong nodes; a match at 0.31 and a match at 0.95 are the same "matched" until
    somebody can see the number.
    """

    topic: str
    section_id: str
    matched_node_ids: list[str]
    matched_node_count: int
    best_score: float          # highest score among matched nodes; 0.0 if none matched
    slots_requested: int
    slots_filled: int


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
    # One row per section carrying an authored `topic`, in blueprint order.
    # EMPTY for topic-free (derived) blueprints — the three shipped ones included.
    per_topic: list[TopicCoverage] = Field(default_factory=list)
    unfilled_slots: list[str]
    warnings: list[str]
