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
# Fixture facts, computed by hand from tests/fixtures/course_map_sample.json
# (N = 20 nodes).
#
# The score is MATCHED IDF MASS over path + key_terms — case-folded, split on
# non-alphanumeric, tokens shorter than 3 dropped:
#
#     idf(t) = ln((20 + 1) / (df(t) + 1)) + 1
#     mass   = Σ idf(t) for t in (topic_tokens ∩ node_tokens)
#
# Admission needs BOTH: mass >= TOPIC_MATCH_RELATIVE_FLOOR * best, and
# best >= TOPIC_MATCH_MIN_EVIDENCE.
# ---------------------------------------------------------------------------

# "Data Structures" → {data, structures}
#   df(data) = 6   (n02 "1.2 Data Types", n04–n08 "Ch 2 Data Structures > …")
#   df(structures) = 5  (n04–n08)
#   idf(data) = ln(21/7) + 1 = 2.0986 · idf(structures) = ln(21/6) + 1 = 2.2528
#
#   n04–n08 carry both tokens          → 4.3514  ← best
#   n02 carries "data" only            → 2.0986
#   everything else                    → 0.0
#
# n02 is EXCLUDED, and only just: the relative floor is 0.5 × 4.3514 = 2.1757 and
# n02 sits 0.077 under it. Semantically that is the right call — "1.2 Data Types"
# is not a data structure — but the margin is thin enough that this test is
# pinning a near-tie, and it is here as a fact about the fixture rather than as
# evidence the floor is well placed. Both constants are UNCALIBRATED (todo.md).
TOPIC_DATA_STRUCTURES = "Data Structures"
DATA_STRUCTURES_BEST_MASS = 4.3514
DATA_STRUCTURES_MATCHES = ["n04", "n05", "n06", "n07", "n08"]

# All five carry has_figure, so the flag filter does not change the set — but n02,
# which the topic filter now drops on its own, carries has_table rather than
# has_figure, so the two filters still have to compose for the right reason.
DATA_STRUCTURES_AND_FIGURE = ["n04", "n05", "n06", "n07", "n08"]

# Nothing in this fixture is about post-quantum cryptography, and — unlike the
# phrasing this constant used to carry — nothing in it shares a single token with
# this one either.
#
# The previous value, "Quantum Cryptography and Lattice Reduction", is NO LONGER
# an absent topic under the IDF rule and had to be replaced: "reduction" occurs
# in exactly one node (n17 NP-Completeness) and "and" in exactly one heading (n03
# "1.3 Variables and Scope"), and because both are RARE they are weighted UP —
# 3.351 each, comfortably past TOPIC_MATCH_MIN_EVIDENCE = 1.5. It would now match
# two nodes on two coincidental words. That is a real property of the new rule,
# not a fixture accident, and it is recorded in todo.md.
TOPIC_ABSENT = "Post-Quantum Cryptography from Lattice Assumptions"


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
        assert row.matched_node_count == 5
        # IDF mass, not a 0–1 fraction. Unbounded and corpus-dependent — see the
        # scale note on TopicCoverage.best_score.
        assert row.best_score == pytest.approx(DATA_STRUCTURES_BEST_MASS, abs=1e-4)
        assert row.slots_requested == 6
        assert row.slots_filled == 6

    def test_within_topic_allocation_is_still_mass_proportional(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Mass is renormalised over the matched nodes, not the whole map.

        Six slots over the five matched nodes, each holding 2+ spans: the
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
        """The rule itself, independent of the (uncalibrated) floors.

        Amendment 01's own example topic. n11 is "Ch 3 Algorithms > 3.3 Graph
        Traversal", key_terms BFS/DFS/visited/adjacency — the only fixture node
        that is really about search — and the matcher must rank it top.
        """
        scores = _topic_scores(
            "Uninformed and Informed Search (BFS, DFS, A*, Heuristics)", course_map
        )
        assert max(scores, key=lambda nid: (scores[nid], nid)) == "n11"

    def test_a_richer_phrasing_of_a_topic_scores_HIGHER_not_lower(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """THE DEFECT THIS RULE REPLACED. The regression guard for the whole change.

        The old rule was `|topic ∩ node| / |topic|`, and it PUNISHED SPECIFICITY.
        Measured on this fixture:

            "Search"                                            1.000 → matched
            "Uninformed and Informed Search (BFS, DFS, A*, …)"  0.286 → matched
                                                                        NOTHING

        A richer, more precise phrasing of the SAME topic scored 3.5× worse than
        the bare word, because every enumerated term the node happens not to
        carry sat in the denominator and diluted the score. Every topic in an
        authored AI blueprint is a long parenthetical string of exactly that
        shape, so essentially none of them would have matched.

        Under matched IDF mass there is no topic-length denominator, so the
        enumerated terms can only ADD evidence: 3.351 and 6.703 respectively.

        Asserted as an INEQUALITY, not against the two numbers: the property is
        "specificity is rewarded", and it must keep holding if the corpus, and
        therefore every idf, changes.
        """
        bare = _topic_scores("Search", course_map)
        rich = _topic_scores(
            "Uninformed and Informed Search (BFS, DFS, A*, Heuristics)", course_map
        )

        # Same node, the one that is genuinely about search.
        assert rich["n11"] > bare["n11"]
        # And the richer phrasing's best is at least the bare word's best — the
        # extra terms cannot cost it evidence anywhere.
        assert max(rich.values()) >= max(bare.values())

        # The old rule admitted "Search" and rejected the precise phrasing. Both
        # must now clear the evidence floor.
        assert max(bare.values()) >= config.TOPIC_MATCH_MIN_EVIDENCE
        assert max(rich.values()) >= config.TOPIC_MATCH_MIN_EVIDENCE

    def test_an_unmatched_term_never_subtracts_evidence(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Adding vocabulary a node does not carry must not lower that node's score.

        This is the denominator defect stated as a property. It is what makes the
        rule safe for authored topics, which are written for the human reading
        the paper and not trimmed to whatever the upload happens to contain.
        """
        base = _topic_scores("Graph Traversal", course_map)
        extended = _topic_scores(
            "Graph Traversal with Bidirectional Frontier Expansion", course_map
        )
        for node_id, score in base.items():
            assert extended[node_id] >= score


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
# Gate 3b — the two admission conditions, one test each
#
# Matched IDF mass is UNBOUNDED, so admission cannot be a single absolute cut.
# It takes two conditions and needs both, and each guards a failure the other
# cannot see:
#
#   relative floor   ranks against the section's best match, because idf depends
#                    on N and an absolute-only cut calibrated on a 20-node
#                    fixture would drift on a 200-node course.
#   evidence floor   stops "best of a bad lot": `mass >= 0.5 * best` is trivially
#                    true for the argmax, so a relative-only rule ALWAYS admits
#                    the best node however worthless its evidence.
# ---------------------------------------------------------------------------

def _uniform_course_map(count: int = 20) -> list[CourseMapNode]:
    """A course map whose nodes share one word and agree on nothing else.

    Every node's path opens with "Overview", so df(overview) = N and
    idf(overview) = ln((N+1)/(N+1)) + 1 = 1.0 exactly — the floor of the smoothed
    idf, and BELOW TOPIC_MATCH_MIN_EVIDENCE = 1.5.

    Built rather than taken from the fixture because the fixture CANNOT exercise
    this branch: its most common token is "data" at df = 6 of 20, worth 2.099, so
    every non-zero overlap in it clears the evidence floor. That is a fact about
    a 20-node corpus and not a reason to lower the constant — the floor bites on
    a corpus where a term really is ubiquitous, which is what a real lecture deck
    ("Introduction", "Overview", the course's own name in every heading) looks
    like.
    """
    return [
        CourseMapNode(
            node_id=f"u{i:02d}",
            path=["Overview", f"Distinct Heading {i:02d}"],
            source_file="uniform.pdf",
            page_span=(i, i),
            token_count=100 + i,
            chunk_ids=[f"u{i:02d}-s1", f"u{i:02d}-s2"],
            key_terms=[f"term{i:02d}"],
            flags={},
            instructional_mass=1.0 / count,
        )
        for i in range(count)
    ]


class TestAdmissionNeedsBothConditions:
    def test_evidence_floor_rejects_a_match_on_a_ubiquitous_term(self) -> None:
        """Best of a bad lot: one shared word that every node carries.

        Without the absolute floor the relative rule would admit ALL twenty
        nodes — every one scores exactly `best` — and the section would print a
        complete-looking paper drawn from the entire course map on the strength
        of the word "overview". That is the precise failure option C exists to
        prevent, arrived at through the front door instead of through a fallback.
        """
        nodes = _uniform_course_map()
        scores = _topic_scores("Overview", nodes)
        assert all(s == pytest.approx(1.0) for s in scores.values()), (
            "fixture assumption broken: idf of a term in every node must be 1.0"
        )
        assert max(scores.values()) < config.TOPIC_MATCH_MIN_EVIDENCE

        items, report = solve(
            nodes, _one_section_blueprint("weak_evidence", count=3, topic="Overview")
        )

        assert items == []
        assert report.unfilled_slots == ["A-01", "A-02", "A-03"]
        assert report.nodes_covered == 0
        assert report.per_topic[0].matched_node_count == 0
        assert report.per_topic[0].slots_filled == 0

        # The warning must say WHICH floor rejected it and name the topic — a
        # near-miss and a zero-overlap topic need different fixes.
        naming = [w for w in report.warnings if "Overview" in w]
        assert naming, f"No warning named the topic. Got: {report.warnings}"
        assert any("TOPIC_MATCH_MIN_EVIDENCE" in w for w in naming)
        assert any("no nodes matched topic" in w for w in naming)

    def test_evidence_floor_admits_the_same_shape_of_match_when_the_term_is_rare(
        self,
    ) -> None:
        """The control arm: identical structure, one distinctive term instead.

        Without this, `test_evidence_floor_rejects_...` would also pass if the
        topic filter rejected everything for some unrelated reason.
        """
        nodes = _uniform_course_map()
        scores = _topic_scores("term07", nodes)
        assert max(scores.values()) >= config.TOPIC_MATCH_MIN_EVIDENCE

        items, report = solve(
            nodes, _one_section_blueprint("rare_evidence", count=2, topic="term07")
        )
        assert [i.node_id for i in items] == ["u07", "u07"]
        assert report.per_topic[0].matched_node_ids == ["u07"]

    def test_relative_floor_excludes_the_weaker_of_two_real_matches(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Both nodes clear the evidence floor; only the stronger is admitted.

        "Graph Traversal" matches n11 ("3.3 Graph Traversal", key_terms BFS/DFS)
        on both tokens and n05/n08 on "traversal" alone. Every one of them scores
        above TOPIC_MATCH_MIN_EVIDENCE, so the absolute floor cannot tell them
        apart — the RELATIVE floor is the only thing that does.

        Asserted through the ranking rather than against the three numbers, so
        the property survives a change to the corpus or to the constant.
        """
        scores = _topic_scores("Graph Traversal", course_map)
        assert scores["n11"] > scores["n08"] > 0.0
        assert scores["n08"] >= config.TOPIC_MATCH_MIN_EVIDENCE, (
            "the weaker node must clear the ABSOLUTE floor, or this test is "
            "measuring the wrong guard"
        )
        assert scores["n08"] < config.TOPIC_MATCH_RELATIVE_FLOOR * scores["n11"]

        _, report = solve(
            course_map,
            _one_section_blueprint("relative", count=2, topic="Graph Traversal"),
        )
        assert report.per_topic[0].matched_node_ids == ["n11"]

    def test_best_is_ranked_over_candidates_the_flags_left(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """`best` comes from the flag SURVIVORS, not from the whole course map.

        n11 is the strongest "Graph Traversal" match but carries has_figure, not
        has_equation. Under has_equation the survivors are n08 (traversal, trees)
        and others, and the ranking must restart among them — ranking against a
        node this section can never draw from would set the floor too high and
        report "no material" about material that is there.
        """
        _, report = solve(
            course_map,
            _one_section_blueprint(
                "flag_scoped_best",
                count=2,
                topic="Trees and Graph Traversal (BFS, DFS)",
                requires_flags_any=["has_equation"],
            ),
        )
        row = report.per_topic[0]
        assert "n11" not in row.matched_node_ids, "flag filter was relaxed"
        assert row.matched_node_ids == ["n08"]
        assert row.slots_filled == 2


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

    def test_a_topic_of_only_function_words_raises(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """
        Was previously a RECORDED GAP: the length threshold alone kept "and" and
        "the" (both exactly TOPIC_MATCH_MIN_TOKEN_LEN), and matched IDF mass then
        made that load-bearing rather than cosmetic — "and" occurs in one of the
        fixture's twenty headings, so idf weighted it UP to 3.351, over twice
        TOPIC_MATCH_MIN_EVIDENCE. One English stopword carried enough evidence on
        its own to admit a node.

        Closed by `config.TOPIC_STOPWORDS`. A topic made only of function words
        now has no tokens at all, which is a malformed blueprint and raises —
        the behaviour Amendment 01 stage 1 specified for "of and the" in the
        first place.
        """
        assert _tokenise("of and the") == set()
        with pytest.raises(ValueError):
            _topic_scores("of and the", course_map)

    def test_stopwords_do_not_admit_an_unrelated_node(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """
        The regression this fix exists for, in its real form.

        "Adversarial Search and Minimax with Alpha-Beta Pruning" shares exactly
        one token with "1.3 Variables and Scope": the word "and". Before
        TOPIC_STOPWORDS that node was admitted at 3.351 — tying the genuinely
        relevant "3.2 Binary Search" — so a section on adversarial search would
        have drawn half its questions from a node about variable scoping.
        Amendment §7: a topic that half-matches is worse than one that does not
        match at all, because it looks like it worked.
        """
        scores = _topic_scores(
            "Adversarial Search and Minimax with Alpha-Beta Pruning", course_map
        )
        assert scores.get("n03", 0.0) == 0.0, (
            "'1.3 Variables and Scope' is admitted on the word 'and' alone"
        )
        best = max(scores.values())
        admitted = {
            nid for nid, s in scores.items()
            if best >= config.TOPIC_MATCH_MIN_EVIDENCE
            and s >= config.TOPIC_MATCH_RELATIVE_FLOOR * best
        }
        assert admitted == {"n10"}, f"expected only Binary Search, got {admitted}"

    def test_stopword_removal_keeps_short_technical_acronyms(self) -> None:
        """
        Raising TOPIC_MATCH_MIN_TOKEN_LEN to 4 would also remove the tokens a
        technical syllabus leans on hardest. Naming the function words is the
        only option that drops the noise without dropping the signal.
        """
        kept = _tokenise("MDP CSP BFS DFS ID3 and the with")
        assert kept == {"mdp", "csp", "bfs", "dfs", "id3"}


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
