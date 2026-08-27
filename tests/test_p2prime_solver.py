"""P2' gate tests: solver determinism, span uniqueness, coverage reporting, edge cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.exam.allocate import _hare_apportionment, solve

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
            total_marks=10,
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
            total_marks=10,
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
