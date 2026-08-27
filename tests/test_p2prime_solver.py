"""P2' gate tests: solver determinism, span uniqueness, coverage reporting, edge cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.exam.allocate import _hare_apportionment, _spec_hash, solve

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def course_map() -> list[CourseMapNode]:
    raw = json.loads((FIXTURES / "course_map_sample.json").read_text())
    return [CourseMapNode.model_validate(n) for n in raw]


@pytest.fixture
def midterm_blueprint() -> Blueprint:
    raw = json.loads(
        (Path(__file__).parent.parent / "coursegen/exam/blueprints/midterm_default.json").read_text()
    )
    return Blueprint.model_validate(raw)


@pytest.fixture
def final_blueprint() -> Blueprint:
    raw = json.loads(
        (Path(__file__).parent.parent / "coursegen/exam/blueprints/final_default.json").read_text()
    )
    return Blueprint.model_validate(raw)


@pytest.fixture
def quiz_blueprint() -> Blueprint:
    raw = json.loads(
        (Path(__file__).parent.parent / "coursegen/exam/blueprints/quiz_default.json").read_text()
    )
    return Blueprint.model_validate(raw)


# ---------------------------------------------------------------------------
# Gate: determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_midterm_byte_identical(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        items1, _ = solve(course_map, midterm_blueprint)
        items2, _ = solve(course_map, midterm_blueprint)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2, "Solver output is not deterministic"

    def test_final_byte_identical(
        self, course_map: list[CourseMapNode], final_blueprint: Blueprint
    ) -> None:
        items1, _ = solve(course_map, final_blueprint)
        items2, _ = solve(course_map, final_blueprint)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2

    def test_quiz_byte_identical(
        self, course_map: list[CourseMapNode], quiz_blueprint: Blueprint
    ) -> None:
        items1, _ = solve(course_map, quiz_blueprint)
        items2, _ = solve(course_map, quiz_blueprint)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2


# ---------------------------------------------------------------------------
# Gate: span uniqueness across the entire paper
# ---------------------------------------------------------------------------

class TestSpanUniqueness:
    def test_midterm_no_span_reuse(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        items, _ = solve(course_map, midterm_blueprint)
        all_spans = [s for item in items for s in item.span_ids]
        assert len(all_spans) == len(set(all_spans)), (
            f"Span(s) reused: {[s for s in all_spans if all_spans.count(s) > 1]}"
        )

    def test_final_no_span_reuse(
        self, course_map: list[CourseMapNode], final_blueprint: Blueprint
    ) -> None:
        items, _ = solve(course_map, final_blueprint)
        all_spans = [s for item in items for s in item.span_ids]
        assert len(all_spans) == len(set(all_spans))

    def test_no_span_reuse_across_sections(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        """Span uniqueness must hold across section boundaries, not just within."""
        items, _ = solve(course_map, midterm_blueprint)
        all_spans = [s for item in items for s in item.span_ids]
        # If per-section uniqueness were the only guarantee, duplicates could appear
        # at section boundaries. This test catches that failure mode.
        assert len(all_spans) == len(set(all_spans))


# ---------------------------------------------------------------------------
# Gate: coverage report populated
# ---------------------------------------------------------------------------

class TestCoverageReport:
    def test_ratio_in_range(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        _, report = solve(course_map, midterm_blueprint)
        assert 0.0 <= report.coverage_ratio <= 1.0

    def test_nodes_covered_le_total(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        _, report = solve(course_map, midterm_blueprint)
        assert report.nodes_covered <= report.nodes_total
        assert report.nodes_total == len(course_map)

    def test_mass_covered_positive(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        _, report = solve(course_map, midterm_blueprint)
        assert report.mass_covered > 0.0

    def test_per_node_marks_match_items(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        items, report = solve(course_map, midterm_blueprint)
        # Total marks from items must equal total marks from coverage report
        items_marks = sum(i.marks for i in items)
        report_marks = sum(n.marks_allocated for n in report.per_node)
        assert items_marks == report_marks

    def test_all_slots_accounted_for(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        items, report = solve(course_map, midterm_blueprint)
        filled_slots = {i.slot_id for i in items}
        unfilled_slots = set(report.unfilled_slots)
        # Every expected slot must be either filled or explicitly unfilled
        for section in midterm_blueprint.sections:
            for i in range(section.count):
                slot_id = f"{section.section_id}-{i + 1:02d}"
                assert slot_id in filled_slots or slot_id in unfilled_slots, (
                    f"Slot {slot_id} is neither filled nor in unfilled_slots"
                )


# ---------------------------------------------------------------------------
# Gate: exhaustion produces warnings + unfilled slots, not a crash
# ---------------------------------------------------------------------------

class TestExhaustion:
    def test_overallocated_blueprint_warns_and_does_not_crash(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Blueprint with count >> available spans must degrade gracefully."""
        sparse_map = [
            CourseMapNode(
                node_id="sparse_n01",
                path=["Ch 1", "1.1"],
                source_file="test.pdf",
                page_span=(1, 2),
                token_count=100,
                chunk_ids=["sp_chunk_a"],   # only 1 span
                key_terms=[],
                flags=NodeFlags(),
                instructional_mass=1.0,
            )
        ]
        blueprint = Blueprint(
            blueprint_id="overloaded",
            title="Overloaded Test",
            total_marks=10,
            duration_minutes=15,
            sections=[
                SectionSpec(
                    section_id="A",
                    item_type="mcq",
                    title="MCQ",
                    count=5,          # 5 items but only 1 span available
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                )
            ],
        )
        items, report = solve(sparse_map, blueprint)
        assert len(items) == 1               # only 1 span → only 1 item
        assert len(report.unfilled_slots) == 4
        assert len(report.warnings) > 0

    def test_no_matching_flags_produces_unfilled(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Section requiring has_code when no nodes have it → all slots unfilled."""
        blueprint = Blueprint(
            blueprint_id="flag_test",
            title="Flag Test",
            total_marks=6,        # 3 * 2 — must match the section below
            duration_minutes=15,
            sections=[
                SectionSpec(
                    section_id="A",
                    item_type="mcq",
                    title="Code MCQ",
                    count=3,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    requires_flags_any=["has_code"],  # no nodes have this flag
                )
            ],
        )
        items, report = solve(course_map, blueprint)
        assert items == []
        assert len(report.unfilled_slots) == 3
        assert any("has_code" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Flag filtering
# ---------------------------------------------------------------------------

class TestFlagFiltering:
    def test_only_flagged_nodes_selected(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """Section with requires_flags_any=has_figure must not touch non-figure nodes."""
        blueprint = Blueprint(
            blueprint_id="flag_filter_test",
            title="Flag Filter",
            total_marks=8,        # 4 * 2 — must match the section below
            duration_minutes=15,
            sections=[
                SectionSpec(
                    section_id="A",
                    item_type="mcq",
                    title="Figure MCQ",
                    count=4,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    requires_flags_any=["has_figure"],
                )
            ],
        )
        items, _ = solve(course_map, blueprint)
        figure_nodes = {n.node_id for n in course_map if n.flags.has_figure}
        for item in items:
            assert item.node_id in figure_nodes, (
                f"Item {item.slot_id} uses node {item.node_id} which has no figure flag"
            )


# ---------------------------------------------------------------------------
# Hare apportionment unit tests
# ---------------------------------------------------------------------------

class TestHareApportionment:
    def test_sums_to_total(self) -> None:
        masses = {"a": 0.5, "b": 0.3, "c": 0.2}
        result = _hare_apportionment(masses, 10)
        assert sum(result.values()) == 10

    def test_deterministic_tie_break(self) -> None:
        masses = {"b": 0.5, "a": 0.5}
        r1 = _hare_apportionment(masses, 3)
        r2 = _hare_apportionment(masses, 3)
        assert r1 == r2
        # "a" < "b" alphabetically → "a" gets the extra seat
        assert r1["a"] == 2
        assert r1["b"] == 1

    def test_zero_total(self) -> None:
        result = _hare_apportionment({"a": 0.5, "b": 0.5}, 0)
        assert all(v == 0 for v in result.values())

    def test_single_node(self) -> None:
        result = _hare_apportionment({"only": 1.0}, 7)
        assert result["only"] == 7


# ---------------------------------------------------------------------------
# Regression: flag-constrained sections must not be span-starved
# ---------------------------------------------------------------------------

def _starvation_course_map() -> list[CourseMapNode]:
    """The pathological shape: the flag-bearing nodes are ALSO the highest-mass
    nodes, and every node has few spans. An unconstrained section apportioned by
    mass therefore drains exactly the spans the constrained section needs.
    """
    nodes: list[CourseMapNode] = []
    for i in range(8):
        nodes.append(
            CourseMapNode(
                node_id=f"plain_{i:02d}",
                path=["Ch 1", f"1.{i}"],
                source_file="deck.pdf",
                page_span=(i + 1, i + 2),
                token_count=100 + i,
                chunk_ids=[f"plain_{i:02d}_c{j}" for j in range(3)],
                key_terms=[],
                flags=NodeFlags(),
                instructional_mass=0.05,
            )
        )
    for i in range(2):
        nodes.append(
            CourseMapNode(
                node_id=f"fig_{i:02d}",
                path=["Ch 2", f"2.{i}"],
                source_file="deck.pdf",
                page_span=(20 + i, 21 + i),
                token_count=500 + i,
                chunk_ids=[f"fig_{i:02d}_c{j}" for j in range(3)],
                key_terms=[],
                flags=NodeFlags(has_figure=True),
                instructional_mass=0.30,
            )
        )
    return nodes


def _starvation_blueprint() -> Blueprint:
    """Section A is unconstrained and authored FIRST; section C needs has_figure."""
    return Blueprint(
        blueprint_id="starvation_test",
        title="Starvation Test",
        total_marks=56,               # 18*2 + 4*5
        duration_minutes=90,
        sections=[
            SectionSpec(
                section_id="A",
                title="Multiple Choice",
                item_type="mcq",
                count=18,
                marks_each=2,
                bloom=["remember", "understand"],
                options_count=4,
            ),
            SectionSpec(
                section_id="C",
                title="Diagram Questions",
                item_type="long",
                count=4,
                marks_each=5,
                bloom=["evaluate"],
                requires_flags_any=["has_figure"],
            ),
        ],
    )


class TestConstrainedSectionStarvation:
    def test_constrained_section_has_no_unfilled_slots(self) -> None:
        """The regression that motivated most-constrained-first solving.

        Solved in blueprint order, section A drains both figure nodes' spans and
        section C ends up with 4 unfilled slots (18 items total). Solved
        most-constrained-first, C is served first and every slot fills (22 items).
        """
        items, report = solve(_starvation_course_map(), _starvation_blueprint())

        c_unfilled = [s for s in report.unfilled_slots if s.startswith("C-")]
        assert c_unfilled == [], (
            f"Flag-constrained section C was span-starved: {c_unfilled}"
        )
        assert len(items) == 22
        assert report.unfilled_slots == []

    def test_constrained_section_still_only_uses_flagged_nodes(self) -> None:
        """Solving C first must not relax its flag filter."""
        course_map = _starvation_course_map()
        items, _ = solve(course_map, _starvation_blueprint())
        figure_nodes = {n.node_id for n in course_map if n.flags.has_figure}
        for item in items:
            if item.slot_id.startswith("C-"):
                assert item.node_id in figure_nodes

    def test_starvation_case_is_deterministic(self) -> None:
        """Reordered solving must not cost determinism."""
        course_map = _starvation_course_map()
        blueprint = _starvation_blueprint()
        j1 = json.dumps(
            [i.model_dump() for i in solve(course_map, blueprint)[0]], sort_keys=True
        )
        j2 = json.dumps(
            [i.model_dump() for i in solve(course_map, blueprint)[0]], sort_keys=True
        )
        assert j1 == j2


# ---------------------------------------------------------------------------
# Emission order: solving order and emission order are different things
# ---------------------------------------------------------------------------

class TestEmissionOrder:
    def test_constrained_section_authored_last_is_emitted_last(self) -> None:
        """Section C is constrained (solved first) but authored last (emitted last)."""
        items, _ = solve(_starvation_course_map(), _starvation_blueprint())
        section_order: list[str] = []
        for item in items:
            sid = item.slot_id.split("-")[0]
            if sid not in section_order:
                section_order.append(sid)
        assert section_order == ["A", "C"]

    def test_final_blueprint_emits_in_blueprint_order(
        self, course_map: list[CourseMapNode], final_blueprint: Blueprint
    ) -> None:
        """final_default.json authors the flag-constrained section (C) last.

        The paper must still read A, B, C, with slot numbers ascending inside
        each section and no interleaving between sections.
        """
        items, _ = solve(course_map, final_blueprint)

        section_runs: list[str] = []
        for item in items:
            sid = item.slot_id.split("-")[0]
            if not section_runs or section_runs[-1] != sid:
                section_runs.append(sid)
        # Each section appears as exactly one contiguous run, in blueprint order.
        assert section_runs == [s.section_id for s in final_blueprint.sections]

        for section in final_blueprint.sections:
            nums = [
                int(i.slot_id.split("-")[1])
                for i in items
                if i.slot_id.startswith(f"{section.section_id}-")
            ]
            assert nums == list(range(1, len(nums) + 1))


# ---------------------------------------------------------------------------
# fill_ratio / slots_filled must expose a shortfall
# ---------------------------------------------------------------------------

class TestFillRatio:
    def test_shortfall_is_visible_in_fill_ratio(self) -> None:
        """A paper missing questions must not report a clean fill_ratio.

        coverage_ratio counts nodes touched, so it reads 1.00 here even though
        4 of 5 slots are empty. fill_ratio is the number that tells the truth.
        """
        sparse_map = [
            CourseMapNode(
                node_id="sparse_n01",
                path=["Ch 1", "1.1"],
                source_file="test.pdf",
                page_span=(1, 2),
                token_count=100,
                chunk_ids=["sp_chunk_a"],   # only 1 span for 5 slots
                key_terms=[],
                flags=NodeFlags(),
                instructional_mass=1.0,
            )
        ]
        blueprint = Blueprint(
            blueprint_id="shortfall",
            title="Shortfall Test",
            total_marks=10,               # 5 * 2
            duration_minutes=15,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="MCQ",
                    item_type="mcq",
                    count=5,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                )
            ],
        )
        items, report = solve(sparse_map, blueprint)

        assert len(items) == 1
        assert report.slots_total == 5
        assert report.slots_filled == 1
        assert report.fill_ratio == pytest.approx(0.2)
        assert report.fill_ratio < 1.0
        assert len(report.unfilled_slots) == 4
        # The bug this guards: coverage_ratio alone would have said "all good".
        assert report.coverage_ratio == 1.0

    def test_complete_paper_reports_full_fill_ratio(
        self, course_map: list[CourseMapNode], midterm_blueprint: Blueprint
    ) -> None:
        items, report = solve(course_map, midterm_blueprint)
        assert report.slots_total == sum(s.count for s in midterm_blueprint.sections)
        assert report.slots_filled == len(items)
        assert report.fill_ratio == 1.0
        assert report.unfilled_slots == []

    def test_slots_filled_always_equals_items_emitted(
        self, course_map: list[CourseMapNode], final_blueprint: Blueprint
    ) -> None:
        items, report = solve(course_map, final_blueprint)
        assert report.slots_filled == len(items)
        assert report.slots_filled == report.slots_total - len(report.unfilled_slots)


# ---------------------------------------------------------------------------
# spec_hash is the cross-paper LLM cache key — marks must be part of it
# ---------------------------------------------------------------------------

class TestSpecHash:
    def test_marks_change_the_hash(self) -> None:
        """A 4-mark and a 5-mark short question drawn from the same node, span and
        bloom must not share a cache entry."""
        four = _spec_hash("node-1", ["span-1"], "short", "apply", 4, None)
        five = _spec_hash("node-1", ["span-1"], "short", "apply", 5, None)
        assert four != five

    def test_options_count_changes_the_hash(self) -> None:
        four_opt = _spec_hash("node-1", ["span-1"], "mcq", "remember", 2, 4)
        five_opt = _spec_hash("node-1", ["span-1"], "mcq", "remember", 2, 5)
        assert four_opt != five_opt

    def test_none_options_count_encodes_stably(self) -> None:
        a = _spec_hash("node-1", ["span-1"], "short", "apply", 4, None)
        b = _spec_hash("node-1", ["span-1"], "short", "apply", 4, None)
        assert a == b

    def test_identical_specs_share_a_hash(self) -> None:
        a = _spec_hash("node-1", ["span-1"], "mcq", "remember", 2, 4)
        b = _spec_hash("node-1", ["span-1"], "mcq", "remember", 2, 4)
        assert a == b

    def test_solver_hashes_differ_across_papers_at_same_node(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """End-to-end: same node, same span, same item_type, same bloom, but the
        midterm asks for 4 marks and the final for 5."""
        def one_short_blueprint(marks: int) -> Blueprint:
            return Blueprint(
                blueprint_id=f"short_{marks}",
                title=f"Short {marks}",
                total_marks=marks,
                duration_minutes=15,
                sections=[
                    SectionSpec(
                        section_id="B",
                        title="Short Answer",
                        item_type="short",
                        count=1,
                        marks_each=marks,
                        bloom=["apply"],
                    )
                ],
            )

        items4, _ = solve(course_map, one_short_blueprint(4))
        items5, _ = solve(course_map, one_short_blueprint(5))
        assert items4[0].node_id == items5[0].node_id
        assert items4[0].span_ids == items5[0].span_ids
        assert items4[0].bloom == items5[0].bloom
        assert items4[0].spec_hash != items5[0].spec_hash
