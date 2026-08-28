"""
Spec Amendment 01, stage 1 — authored topics mapped onto the ingested course map.

An authored blueprint names its own topics and sets the marks per topic by hand.
The blueprint sets the ACROSS-topic weights; the solver still does WITHIN-topic
allocation from the student's own material. Zero LLM calls — pure allocation.

The property these gates exist to protect:

    if the uploaded documents do not cover a topic the blueprint asks for, the
    system says so LOUDLY and leaves those slots unfilled — it never falls back
    to the whole course map, and never lets a model invent material the student
    cannot study from.

`topic` is optional, and its ABSENCE is the existing product. `TestDerivedPathUnchanged`
re-derives the whole pre-change allocation from the course map, the blueprint and
`_hare_apportionment`, without any notion of `topic`, and demands byte-identical
`ItemSpec[]`. That oracle is the regression guard for the derived path — and for
P6's solver-vs-baseline comparison, which runs on the topic-free blueprints.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.item import ItemSpec
from coursegen.exam.allocate import (
    _hare_apportionment,
    _spec_hash,
    _tokenise,
    _topic_scores,
    solve,
)

FIXTURES = Path(__file__).parent / "fixtures"
BLUEPRINT_DIR = Path(__file__).parent.parent / "coursegen" / "exam" / "blueprints"
SHIPPED = ["quiz_default", "midterm_default", "final_default"]


@pytest.fixture
def course_map() -> list[CourseMapNode]:
    raw = json.loads((FIXTURES / "course_map_sample.json").read_text(encoding="utf-8"))
    return [CourseMapNode.model_validate(n) for n in raw]


def _load_shipped(name: str) -> Blueprint:
    return Blueprint.model_validate(
        json.loads((BLUEPRINT_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# Fixture facts, computed by hand from tests/fixtures/course_map_sample.json.
#
# Scores are |topic tokens ∩ node tokens| / |topic tokens| over path + key_terms,
# case-folded, split on non-alphanumeric, tokens shorter than 3 dropped.
# ---------------------------------------------------------------------------

# "Data Structures" → {data, structures}
#   n04–n08 are "Ch 2 Data Structures > …"           → 2/2 = 1.00
#   n02 is "Ch 1 Fundamentals > 1.2 Data Types"      → 1/2 = 0.50  (token "data")
#   everything else                                   → 0.00
TOPIC_DATA_STRUCTURES = "Data Structures"
DATA_STRUCTURES_MATCHES = ["n02", "n04", "n05", "n06", "n07", "n08"]

# Of those, only n04–n08 carry has_figure; n02 carries has_table.
DATA_STRUCTURES_AND_FIGURE = ["n04", "n05", "n06", "n07", "n08"]

# Nothing in this fixture is about post-quantum cryptography.
TOPIC_ABSENT = "Quantum Cryptography and Lattice Reduction"


def _one_section_blueprint(
    blueprint_id: str,
    count: int,
    marks_each: int = 2,
    topic: str | None = None,
    requires_flags_any: list[str] | None = None,
) -> Blueprint:
    return Blueprint(
        blueprint_id=blueprint_id,
        title="Authored",
        total_marks=count * marks_each,
        duration_minutes=60,
        sections=[
            SectionSpec(
                section_id="A",
                title="Authored Section",
                item_type="mcq",
                count=count,
                marks_each=marks_each,
                bloom=["remember", "understand"],
                options_count=4,
                requires_flags_any=requires_flags_any,
                topic=topic,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Gate 1 — the derived path is untouched
#
# The expected ItemSpec[] is re-derived here from the same inputs the solver
# receives, by an oracle that has never heard of `topic`. No golden file: a
# stored blob would only prove the file did not change, not that the allocation
# did not move.
# ---------------------------------------------------------------------------

def _oracle_solve(
    nodes: list[CourseMapNode], blueprint: Blueprint
) -> list[ItemSpec]:
    """Independent re-derivation of the PRE-AMENDMENT solver (§9.3).

    Mirrors the documented algorithm, deliberately with no topic filter:
    most-constrained-first solve order, paper-wide span registry, renormalise
    mass over the flag-filtered candidates, Hare quota, fill by descending
    deficit (falling through to the lowest node_id with spans left),
    ascending-token_count difficulty ladder, bloom cycled, emit in blueprint
    order.
    """
    sorted_nodes = sorted(nodes, key=lambda n: n.node_id)
    used_spans: set[str] = set()
    per_section: dict[int, list[ItemSpec]] = {}

    solve_order = sorted(
        range(len(blueprint.sections)),
        key=lambda i: (0 if blueprint.sections[i].requires_flags_any else 1, i),
    )

    for i in solve_order:
        section = blueprint.sections[i]
        per_section[i] = []

        flags = section.requires_flags_any or []
        candidates = [
            n for n in sorted_nodes
            if not flags or any(getattr(n.flags, f, False) for f in flags)
        ]
        if not candidates:
            continue

        total_mass = sum(n.instructional_mass for n in candidates)
        if total_mass == 0:
            masses = {n.node_id: 1.0 / len(candidates) for n in candidates}
        else:
            masses = {
                n.node_id: n.instructional_mass / total_mass for n in candidates
            }

        deficit = dict(_hare_apportionment(masses, section.count))
        available = {
            n.node_id: [s for s in n.chunk_ids if s not in used_spans]
            for n in candidates
        }
        by_id = {n.node_id: n for n in candidates}

        picked: list[tuple[str, str]] = []
        while len(picked) < section.count:
            primary = sorted(
                [nid for nid in deficit if deficit[nid] > 0 and available[nid]],
                key=lambda nid: (-deficit[nid], nid),
            )
            if primary:
                nid = primary[0]
                deficit[nid] -= 1
            else:
                fallback = sorted(
                    n.node_id for n in candidates if available[n.node_id]
                )
                if not fallback:
                    break
                nid = fallback[0]
            span = available[nid].pop(0)
            used_spans.add(span)
            picked.append((nid, span))

        picked.sort(key=lambda p: (by_id[p[0]].token_count, p[0]))

        for idx, (nid, span) in enumerate(picked):
            node = by_id[nid]
            bloom = section.bloom[idx % len(section.bloom)]
            per_section[i].append(
                ItemSpec(
                    slot_id=f"{section.section_id}-{idx + 1:02d}",
                    item_type=section.item_type,
                    marks=section.marks_each,
                    bloom=bloom,
                    node_id=nid,
                    span_ids=[span],
                    eligibility=[
                        f for f in flags if getattr(node.flags, f, False)
                    ],
                    spec_hash=_spec_hash(
                        nid,
                        [span],
                        section.item_type,
                        bloom,
                        section.marks_each,
                        section.options_count,
                    ),
                )
            )

    return [
        item for i in range(len(blueprint.sections)) for item in per_section[i]
    ]


class TestDerivedPathUnchanged:
    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_topic_free_blueprint_matches_topic_blind_oracle(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """Byte-identical ItemSpec[] against an oracle with no topic support."""
        blueprint = _load_shipped(blueprint_name)
        assert all(s.topic is None for s in blueprint.sections), (
            f"{blueprint_name} must stay topic-free — it is the derived path"
        )

        items, _ = solve(course_map, blueprint)
        expected = _oracle_solve(course_map, blueprint)

        assert json.dumps(
            [i.model_dump() for i in items], sort_keys=True
        ) == json.dumps([i.model_dump() for i in expected], sort_keys=True)

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_per_topic_is_empty_for_shipped_blueprints(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        _, report = solve(course_map, _load_shipped(blueprint_name))
        assert report.per_topic == []

    def test_absent_topic_is_not_an_empty_string(self) -> None:
        """None and "" are different: "" would tokenise to nothing and raise."""
        section = SectionSpec(
            section_id="A",
            title="Derived",
            item_type="mcq",
            count=1,
            marks_each=2,
            bloom=["remember"],
        )
        assert section.topic is None


# ---------------------------------------------------------------------------
# Gate 2 — a topic restricts allocation to the nodes it matched
# ---------------------------------------------------------------------------

class TestTopicRestrictsAllocation:
    def test_items_come_only_from_matched_nodes(
        self, course_map: list[CourseMapNode]
    ) -> None:
        items, report = solve(
            course_map,
            _one_section_blueprint("topic_ds", count=6, topic=TOPIC_DATA_STRUCTURES),
        )

        assert len(items) == 6
        assert {i.node_id for i in items} <= set(DATA_STRUCTURES_MATCHES)
        # The nodes the topic did NOT match must not appear, however much mass
        # they carry — n18 is the highest-mass node in the fixture.
        assert "n18" not in {i.node_id for i in items}
        assert "n01" not in {i.node_id for i in items}

        row = report.per_topic[0]
        assert row.topic == TOPIC_DATA_STRUCTURES
        assert row.section_id == "A"
        assert row.matched_node_ids == DATA_STRUCTURES_MATCHES
        assert row.matched_node_count == 6
        assert row.best_score == pytest.approx(1.0)
        assert row.slots_requested == 6
        assert row.slots_filled == 6

    def test_within_topic_allocation_is_still_mass_proportional(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Mass is renormalised over the matched nodes, not the whole map.

        Six slots over six equally-eligible nodes, each holding 2+ spans: the
        highest-mass matched node (n04) must be served, and no node outside the
        match may receive marks.
        """
        _, report = solve(
            course_map,
            _one_section_blueprint("topic_ds_mass", count=6, topic=TOPIC_DATA_STRUCTURES),
        )
        with_marks = {
            n.node_id for n in report.per_node if n.marks_allocated > 0
        }
        assert with_marks <= set(DATA_STRUCTURES_MATCHES)
        assert "n04" in with_marks

    def test_topic_scores_rank_the_right_node_first(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """The rule itself, independent of the (uncalibrated) floor.

        Amendment 01's own example topic. n11 is "Ch 3 Algorithms > 3.3 Graph
        Traversal", key_terms BFS/DFS/visited/adjacency — the only fixture node
        that is really about search — and the matcher must rank it top.

        The floor is a separate question: on this fixture n11 scores 2/7 ≈ 0.286,
        just UNDER TOPIC_MATCH_MIN_SCORE = 0.3, so the semantically right node is
        excluded. That is evidence the floor is uncalibrated, recorded in todo.md,
        and it is deliberately NOT asserted here — pinning it would turn an honest
        calibration into a test failure.
        """
        scores = _topic_scores(
            "Uninformed and Informed Search (BFS, DFS, A*, Heuristics)", course_map
        )
        assert max(scores, key=lambda nid: (scores[nid], nid)) == "n11"


# ---------------------------------------------------------------------------
# Gate 3 — a topic that matches nothing is LOUD, and nothing is invented
#
# This is the most important output of the stage.
# ---------------------------------------------------------------------------

class TestTopicMatchesNothing:
    def test_no_match_leaves_slots_unfilled_and_says_so(
        self, course_map: list[CourseMapNode]
    ) -> None:
        items, report = solve(
            course_map,
            _one_section_blueprint("topic_absent", count=3, topic=TOPIC_ABSENT),
        )

        assert items == []
        assert report.unfilled_slots == ["A-01", "A-02", "A-03"]
        assert report.slots_filled == 0
        assert report.fill_ratio == 0.0

        # The warning must NAME the topic — "section skipped" alone tells the
        # student nothing about what their material is missing.
        naming = [w for w in report.warnings if TOPIC_ABSENT in w]
        assert naming, f"No warning named the topic. Got: {report.warnings}"
        assert any("no nodes matched topic" in w for w in naming)

        row = report.per_topic[0]
        assert row.topic == TOPIC_ABSENT
        assert row.matched_node_count == 0
        assert row.matched_node_ids == []
        assert row.best_score == 0.0
        assert row.slots_requested == 3
        assert row.slots_filled == 0

    def test_no_fallback_to_the_unfiltered_course_map(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """The failure mode this design exists to prevent.

        A silent fallback would fill all three slots from the whole course map
        and report a complete paper about material the topic never asked for.
        """
        items, report = solve(
            course_map,
            _one_section_blueprint("topic_absent_nofallback", count=3, topic=TOPIC_ABSENT),
        )
        assert items == []
        assert report.nodes_covered == 0
        assert report.mass_covered == 0.0
        assert all(n.marks_allocated == 0 for n in report.per_node)

    def test_unmatched_topic_does_not_starve_a_matched_one(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """One topic missing must not take the rest of the paper down with it."""
        blueprint = Blueprint(
            blueprint_id="mixed_topics",
            title="Mixed",
            total_marks=10,                       # 3*2 + 2*2
            duration_minutes=60,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Present",
                    item_type="mcq",
                    count=3,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    topic=TOPIC_DATA_STRUCTURES,
                ),
                SectionSpec(
                    section_id="B",
                    title="Absent",
                    item_type="mcq",
                    count=2,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    topic=TOPIC_ABSENT,
                ),
            ],
        )
        items, report = solve(course_map, blueprint)

        assert [i.slot_id for i in items] == ["A-01", "A-02", "A-03"]
        assert report.unfilled_slots == ["B-01", "B-02"]

        # per_topic is in blueprint order, one row per authored topic.
        assert [r.section_id for r in report.per_topic] == ["A", "B"]
        assert report.per_topic[0].slots_filled == 3
        assert report.per_topic[1].matched_node_count == 0
        assert report.per_topic[1].slots_filled == 0


# ---------------------------------------------------------------------------
# Gate 4 — flags and topic compose
# ---------------------------------------------------------------------------

class TestFiltersCompose:
    def test_both_filters_apply(self, course_map: list[CourseMapNode]) -> None:
        items, report = solve(
            course_map,
            _one_section_blueprint(
                "topic_and_flag",
                count=5,
                topic=TOPIC_DATA_STRUCTURES,
                requires_flags_any=["has_figure"],
            ),
        )

        figure_nodes = {n.node_id for n in course_map if n.flags.has_figure}
        for item in items:
            assert item.node_id in figure_nodes, "flag filter was relaxed"
            assert item.node_id in set(DATA_STRUCTURES_MATCHES), "topic filter was relaxed"

        # n02 matches the topic but carries has_table, not has_figure.
        assert "n02" not in {i.node_id for i in items}
        # n11 and n19 carry has_figure but are not Data Structures nodes.
        assert not {"n11", "n19"} & {i.node_id for i in items}

        assert report.per_topic[0].matched_node_ids == DATA_STRUCTURES_AND_FIGURE

    def test_intersection_can_be_empty_without_either_filter_being_empty(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """has_table nodes exist and Graph Traversal nodes exist — but not both."""
        items, report = solve(
            course_map,
            _one_section_blueprint(
                "disjoint",
                count=2,
                topic="Graph Traversal",
                requires_flags_any=["has_table"],
            ),
        )
        assert items == []
        assert report.per_topic[0].matched_node_count == 0
        assert any("no nodes matched topic" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Gate 5 — the two empty-candidate warnings are different problems
# ---------------------------------------------------------------------------

class TestWarningsDistinguishTheFilters:
    def test_topic_empty_and_flag_empty_read_differently(
        self, course_map: list[CourseMapNode]
    ) -> None:
        blueprint = Blueprint(
            blueprint_id="two_failures",
            title="Two Failures",
            total_marks=10,                       # 3*2 + 2*2
            duration_minutes=60,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Impossible Flag",
                    item_type="mcq",
                    count=3,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    requires_flags_any=["has_code"],   # no fixture node has it
                ),
                SectionSpec(
                    section_id="B",
                    title="Impossible Topic",
                    item_type="mcq",
                    count=2,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    topic=TOPIC_ABSENT,
                ),
            ],
        )
        _, report = solve(course_map, blueprint)

        flag_warnings = [w for w in report.warnings if "has_code" in w]
        topic_warnings = [w for w in report.warnings if TOPIC_ABSENT in w]

        assert len(flag_warnings) == 1
        assert len(topic_warnings) == 1

        # Neither message may be mistaken for the other: they have different fixes.
        assert "requires_flags_any" in flag_warnings[0]
        assert "topic" not in flag_warnings[0]
        assert "no nodes matched topic" in topic_warnings[0]
        assert "requires_flags_any" not in topic_warnings[0]

    def test_flag_empty_wins_when_both_would_fail(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Filter order decides which problem is reported, and it is the flag.

        The flag filter runs first, so a section whose flag matches nothing is
        reported as a flag failure — the topic was never given a set to filter.
        Its per_topic row is still emitted, showing zero matches.
        """
        _, report = solve(
            course_map,
            _one_section_blueprint(
                "both_fail",
                count=2,
                topic=TOPIC_ABSENT,
                requires_flags_any=["has_code"],
            ),
        )
        assert any("requires_flags_any" in w for w in report.warnings)
        assert not any("no nodes matched topic" in w for w in report.warnings)
        assert report.per_topic[0].matched_node_count == 0
        assert report.per_topic[0].slots_filled == 0


# ---------------------------------------------------------------------------
# Gate 6 — a topic that cannot be tokenised is a malformed blueprint
# ---------------------------------------------------------------------------

class TestMalformedTopicRaises:
    def test_topic_of_only_short_words_raises(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Every token below TOPIC_MATCH_MIN_TOKEN_LEN → no usable vocabulary.

        Dividing by zero, or treating an empty token set as matching everything,
        would hand the section the whole course map while LOOKING like a
        successful topic match. Raised, not asserted: `python -O` strips asserts.
        """
        with pytest.raises(ValueError, match="no usable tokens"):
            solve(course_map, _one_section_blueprint("bad", count=1, topic="of a *"))

    def test_helper_raises_before_dividing(
        self, course_map: list[CourseMapNode]
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            _topic_scores("of a *", course_map)
        message = str(excinfo.value)
        assert "of a *" in message                       # names the offending topic
        assert "TOPIC_MATCH_MIN_TOKEN_LEN" in message    # names the rule it broke

    def test_malformed_topic_raises_even_when_flags_match_nothing(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """The failure must not depend on which filter would have emptied first."""
        with pytest.raises(ValueError, match="no usable tokens"):
            solve(
                course_map,
                _one_section_blueprint(
                    "bad_and_flagged",
                    count=1,
                    topic="of a *",
                    requires_flags_any=["has_code"],
                ),
            )

    def test_three_letter_stopwords_survive_the_threshold(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """RECORDED GAP, not an endorsement — see todo.md.

        SPEC-AMENDMENT-01 stage 1 gives "of and the" as an example of a topic
        that should raise, but the rule it also specifies — drop tokens SHORTER
        than TOPIC_MATCH_MIN_TOKEN_LEN = 3 — keeps "and" and "the", which are
        exactly 3 characters. The rule was implemented as written, so this topic
        does not raise: it survives with two junk tokens and matches n03
        ("1.3 Variables and Scope") at 0.5 on the strength of the word "and".

        This test pins the ACTUAL behaviour so nobody assumes the guard covers
        it. The threshold is the knob; the human decides whether it moves.
        """
        assert _tokenise("of and the") == {"and", "the"}
        scores = _topic_scores("of and the", course_map)
        assert scores["n03"] == pytest.approx(0.5)
        assert scores["n03"] >= config.TOPIC_MATCH_MIN_SCORE


# ---------------------------------------------------------------------------
# Gate 7 — determinism holds with topics present
# ---------------------------------------------------------------------------

class TestDeterminismWithTopics:
    def test_byte_identical_across_solves(
        self, course_map: list[CourseMapNode]
    ) -> None:
        blueprint = Blueprint(
            blueprint_id="authored_determinism",
            title="Authored Determinism",
            total_marks=24,                       # 4*2 + 4*2 + 4*2
            duration_minutes=120,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Structures",
                    item_type="mcq",
                    count=4,
                    marks_each=2,
                    bloom=["remember", "understand"],
                    options_count=4,
                    topic=TOPIC_DATA_STRUCTURES,
                ),
                SectionSpec(
                    section_id="B",
                    title="Complexity",
                    item_type="short",
                    count=4,
                    marks_each=2,
                    bloom=["apply"],
                    topic="Complexity and Big-O Notation",
                ),
                SectionSpec(
                    section_id="C",
                    title="Figures",
                    item_type="long",
                    count=4,
                    marks_each=2,
                    bloom=["evaluate"],
                    requires_flags_any=["has_figure"],
                    topic="Trees and Graph Traversal (BFS, DFS)",
                ),
            ],
        )

        def run() -> tuple[str, str]:
            items, report = solve(course_map, blueprint)
            return (
                json.dumps([i.model_dump() for i in items], sort_keys=True),
                json.dumps(
                    [r.model_dump() for r in report.per_topic], sort_keys=True
                ),
            )

        assert run() == run()

    def test_matched_node_ids_are_span_unique_and_ordered(
        self, course_map: list[CourseMapNode]
    ) -> None:
        items, report = solve(
            course_map,
            _one_section_blueprint("ordering", count=6, topic=TOPIC_DATA_STRUCTURES),
        )
        ids = report.per_topic[0].matched_node_ids
        assert ids == sorted(ids), "matched_node_ids must be deterministic"
        spans = [s for i in items for s in i.span_ids]
        assert len(spans) == len(set(spans))
