"""Mastery and re-test cards, derived from answer history. Never stored.

Pure functions: rows in, values out — no database, no clock. Because nothing
here is kept between calls, a contested mark needs no repair step: the rows carry
the mark that now counts, and every number below follows from them. Contest a
miss up to full marks and the re-test card it created never existed.

## Mastery (§7.1)

`m <- m + MASTERY_ALPHA * (s - m)` per scored answer, in submission order, with
the first answer setting `m = s`. A blank answer scores 0 — skipping a question is
evidence of not knowing it. An unmarked answer is no evidence at all, and is
skipped. **A node nobody has answered has no mastery**: untested is not weak,
and the next paper's tilt leaves it alone.

## Re-test cards (§7.3)

A card is one missed question, keyed by where it was first asked. A score below
`RETEST_BELOW` on a paper question creates one in box 1. Every later scored
answer on that question — re-taking the paper, or answering it in a review set —
tests the card: a pass moves it up a box (passing box 4 retires it), a fail sends
it back to box 1, reviving it if it had retired.

Spacing is in ATTEMPTS, not days (`LEITNER_GAPS`), an accepted trade-off — this
must never be presented as time-based spacing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode
from coursegen.grade.records import AnswerRow


def score(row: AnswerRow) -> Optional[float]:
    """The answer's share of its marks: 0.0 when blank, None when unmarked."""
    if row.status == "unanswered":
        return 0.0
    if row.marks_awarded is None:
        return None
    return row.marks_awarded / row.marks_available


def _in_order(rows: list[AnswerRow]) -> list[AnswerRow]:
    return sorted(rows, key=lambda r: (r.seq, r.slot_id))


@dataclass(frozen=True)
class NodeMastery:
    node_id: str
    mastery: float
    answers: int


def node_mastery(rows: list[AnswerRow]) -> dict[str, NodeMastery]:
    state: dict[str, tuple[float, int]] = {}
    for row in _in_order(rows):
        s = score(row)
        if s is None:
            continue
        if row.node_id not in state:
            state[row.node_id] = (s, 1)
        else:
            m, n = state[row.node_id]
            state[row.node_id] = (m + config.MASTERY_ALPHA * (s - m), n + 1)
    return {nid: NodeMastery(nid, m, n) for nid, (m, n) in state.items()}


@dataclass(frozen=True)
class LectureMastery:
    lecture: str
    mastery: Optional[float]
    tested_share: float
    nodes_total: int
    nodes_tested: int
    nodes: list[dict[str, Any]] = field(default_factory=list)


def lecture_rollup(
    mastery: dict[str, NodeMastery], nodes: list[CourseMapNode]
) -> list[LectureMastery]:
    """Mastery per source file, weighted by instructional mass.

    Reported beside how much of the lecture has been tested at all: 90% mastery
    over a twentieth of a lecture says little, and the two numbers together say
    exactly how much.
    """
    by_lecture: dict[str, list[CourseMapNode]] = {}
    for node in nodes:
        by_lecture.setdefault(node.source_file, []).append(node)

    out = []
    for lecture in sorted(by_lecture):
        members = by_lecture[lecture]
        tested = [n for n in members if n.node_id in mastery]
        total_mass = sum(n.instructional_mass for n in members)
        tested_mass = sum(n.instructional_mass for n in tested)
        out.append(LectureMastery(
            lecture=lecture,
            mastery=(
                sum(n.instructional_mass * mastery[n.node_id].mastery for n in tested) / tested_mass
                if tested and tested_mass > 0 else
                (sum(mastery[n.node_id].mastery for n in tested) / len(tested) if tested else None)
            ),
            tested_share=tested_mass / total_mass if total_mass > 0 else 0.0,
            nodes_total=len(members),
            nodes_tested=len(tested),
            nodes=[
                {
                    "node_id": n.node_id,
                    "topic": " › ".join(n.path),
                    "mastery": mastery[n.node_id].mastery,
                    "answers": mastery[n.node_id].answers,
                }
                for n in sorted(tested, key=lambda n: n.node_id)
            ],
        ))
    return out


@dataclass(frozen=True)
class Card:
    origin: tuple[str, str]           # (sheet_id, slot_id) where it was first asked
    node_id: str
    box: int
    retired: bool
    passed_once: bool
    last_tested_seq: int
    tests: int                        # scored answers so far, the creating miss included

    @property
    def form(self) -> str:
        """The same question until it has been passed once; new wording after."""
        return "fresh" if self.passed_once else "identical"

    def elapsed(self, latest_seq: int) -> int:
        return latest_seq - self.last_tested_seq

    def is_due(self, latest_seq: int) -> bool:
        return not self.retired and self.elapsed(latest_seq) >= config.LEITNER_GAPS[self.box]


def cards(rows: list[AnswerRow]) -> dict[tuple[str, str], Card]:
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _in_order(rows):
        s = score(row)
        if s is None:
            continue
        if row.sheet_kind == "review":
            key = row.retest_of
            # A review question whose card is gone — its miss was contested away
            # after the review was built — tests nothing.
            if key is None or key not in state:
                continue
        else:
            key = (row.sheet_id, row.slot_id)
            if key not in state:
                if s < config.RETEST_BELOW:
                    state[key] = {"node_id": row.node_id, "box": 1, "retired": False,
                                  "passed_once": False, "last": row.seq, "tests": 1}
                continue
        card = state[key]
        card["tests"] += 1
        card["last"] = row.seq
        if s >= config.RETEST_BELOW:
            card["passed_once"] = True
            if not card["retired"]:
                if card["box"] >= max(config.LEITNER_GAPS):
                    card["retired"] = True
                else:
                    card["box"] += 1
        else:
            card["box"] = 1
            card["retired"] = False
    return {
        key: Card(origin=key, node_id=c["node_id"], box=c["box"], retired=c["retired"],
                  passed_once=c["passed_once"], last_tested_seq=c["last"], tests=c["tests"])
        for key, c in state.items()
    }


def select_review(
    all_cards: dict[tuple[str, str], Card],
    latest_seq: int,
    mass_by_node: dict[str, float],
    limit: int = config.REVIEW_MAX_ITEMS,
) -> list[Card]:
    """The due cards for one review set: lowest box first, then most overdue,
    then the heavier node, then by origin so ties are deterministic.

    At most one card per node — two questions on one passage in one set test the
    same thing twice. The other stays due for the next set.
    """
    due = sorted(
        (c for c in all_cards.values() if c.is_due(latest_seq)),
        key=lambda c: (
            c.box,
            -(c.elapsed(latest_seq) - config.LEITNER_GAPS[c.box]),
            -mass_by_node.get(c.node_id, 0.0),
            c.origin,
        ),
    )
    picked: list[Card] = []
    nodes: set[str] = set()
    for card in due:
        if card.node_id in nodes:
            continue
        nodes.add(card.node_id)
        picked.append(card)
        if len(picked) == limit:
            break
    return picked


def split_orphans(rows: list[AnswerRow], node_ids: set[str]) -> tuple[list[AnswerRow], int]:
    """Rows whose node is still in the course map, and how many were not.

    Material re-ingested under different names leaves answers pointing at nodes
    that no longer exist. They are left out and COUNTED, so the student is told
    rather than seeing their history quietly shrink.
    """
    kept = [r for r in rows if r.node_id in node_ids]
    return kept, len(rows) - len(kept)
