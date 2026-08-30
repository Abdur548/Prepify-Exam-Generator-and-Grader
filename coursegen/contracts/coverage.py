from __future__ import annotations

from typing import Optional

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
    wrong nodes; two topics both read as merely "matched" until somebody can see
    the number.

    READ THE SCALE BEFORE READING THE NUMBER. ``best_score`` is **matched IDF
    mass** — ``Σ idf(t)`` over the tokens the topic and the node share — NOT the
    0–1 fraction of the topic's own vocabulary that it carried before Amendment
    01's matching rule was replaced. It is unbounded, it grows with topic length,
    and because ``idf`` depends on the node count it is not comparable across
    course maps. A 4.35 here is not "worse than" the old 1.00; it is a different
    quantity. What it supports is a RANKING within one section's candidate set,
    which is exactly how ``TOPIC_MATCH_RELATIVE_FLOOR`` uses it.
    """

    topic: str
    section_id: str
    matched_node_ids: list[str]
    matched_node_count: int
    # Highest matched IDF mass among the ADMITTED nodes; 0.0 if none were admitted.
    # A topic rejected by TOPIC_MATCH_MIN_EVIDENCE therefore still reports 0.0 even
    # though its best candidate scored above zero — that near-miss lives only in
    # the warning text (recorded in todo.md).
    best_score: float
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
    # REALISED Bloom distribution over the items actually EMITTED — level →
    # fraction of emitted items, summing to 1.0 (empty when nothing was emitted).
    #
    # Over emitted items rather than requested slots, deliberately: an
    # under-filled paper's realised mix is the mix of the questions the student
    # actually sits, and that is the thing worth comparing against a target.
    bloom_realised: dict[str, float] = Field(default_factory=dict)
    # The blueprint's DECLARED cognitive_balance, when it gave one; None otherwise.
    # Carried beside the realised mix so the two are read together — a realised
    # number with no target next to it cannot be judged.
    #
    # Divergence beyond config.COGNITIVE_BALANCE_TOLERANCE appends a WARNING
    # naming both, and never raises: an under-filled paper legitimately misses its
    # target, and that is information rather than a crash. This pair is the only
    # thing that would notice a paper drifting to easy recall questions while the
    # blueprint asked for 70% apply/analyse — a silent quality failure that
    # coverage_ratio, fill_ratio and allocation_fidelity would all report as fine.
    cognitive_balance_declared: Optional[dict[str, float]] = None
    unfilled_slots: list[str]
    warnings: list[str]
