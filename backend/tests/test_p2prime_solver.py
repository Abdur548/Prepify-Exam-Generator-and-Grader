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


# ---------------------------------------------------------------------------
# allocation_fidelity — how much of the paper mass actually placed
#
# coverage_ratio and fill_ratio can BOTH read 1.000 on a paper where a third of
# the slots were placed by span exhaustion rather than by apportionment. These
# gates prove the third ratio sees what the first two cannot.
# ---------------------------------------------------------------------------

def _roomy_course_map() -> list[CourseMapNode]:
    """Every node carries far more spans than apportionment can ask for, so no
    slot can ever fall through. Equal mass keeps the Hare split exact."""
    return [
        CourseMapNode(
            node_id=f"roomy_{i:02d}",
            path=["Ch 1", f"1.{i}"],
            source_file="deck.pdf",
            page_span=(i + 1, i + 2),
            token_count=100 + i,
            chunk_ids=[f"roomy_{i:02d}_c{j}" for j in range(10)],
            key_terms=[],
            flags=NodeFlags(),
            instructional_mass=0.2,
        )
        for i in range(5)
    ]


def _one_span_per_node_course_map() -> list[CourseMapNode]:
    """The real-corpus shape: every node yields exactly one chunk.

    Two heavy nodes carry most of the mass, so Hare hands each of them 3 slots —
    but each holds a single span, and span-uniqueness caps a node at one item.
    The surplus can only be placed by falling through to nodes the mass never
    asked for. Total spans exactly equal total slots, so the paper still fills
    completely and touches every node: coverage_ratio and fill_ratio both read
    1.000 over a paper half of which was placed by exhaustion.
    """
    nodes = [
        CourseMapNode(
            node_id=f"hi_{i:02d}",
            path=["Ch 1", f"1.{i}"],
            source_file="deck.pdf",
            page_span=(i + 1, i + 2),
            token_count=900 + i,
            chunk_ids=[f"hi_{i:02d}_c0"],
            key_terms=[],
            flags=NodeFlags(),
            instructional_mass=40.0,
        )
        for i in range(2)
    ]
    nodes += [
        CourseMapNode(
            node_id=f"lo_{i:02d}",
            path=["Ch 2", f"2.{i}"],
            source_file="deck.pdf",
            page_span=(20 + i, 21 + i),
            token_count=100 + i,
            chunk_ids=[f"lo_{i:02d}_c0"],
            key_terms=[],
            flags=NodeFlags(),
            instructional_mass=5.0,
        )
        for i in range(6)
    ]
    return nodes


def _eight_slot_blueprint(blueprint_id: str) -> Blueprint:
    return Blueprint(
        blueprint_id=blueprint_id,
        title="Fidelity Probe",
        total_marks=16,               # 8 * 2
        duration_minutes=30,
        sections=[
            SectionSpec(
                section_id="A",
                title="Multiple Choice",
                item_type="mcq",
                count=8,
                marks_each=2,
                bloom=["remember", "understand"],
                options_count=4,
            )
        ],
    )


def _ten_slot_blueprint() -> Blueprint:
    return Blueprint(
        blueprint_id="roomy",
        title="Roomy",
        total_marks=20,               # 10 * 2
        duration_minutes=30,
        sections=[
            SectionSpec(
                section_id="A",
                title="Multiple Choice",
                item_type="mcq",
                count=10,
                marks_each=2,
                bloom=["remember", "understand"],
                options_count=4,
            )
        ],
    )


class TestSlotAccountingInvariant:
    """
    The invariant is only worth having if something proves it fires. It is raised
    explicitly rather than asserted, because `python -O` strips `assert` — which
    would silently disable the one check standing between a broken count and a
    believable-looking coverage table.
    """

    def test_broken_accounting_raises(self) -> None:
        from coursegen.exam.coverage import build_report

        with pytest.raises(ValueError, match="Slot accounting broken"):
            build_report(
                blueprint_id="x",
                course_map=[],
                node_slot_map={},
                node_marks_map={},
                unfilled_slots=[],
                warnings=[],
                slots_total=10,
                slots_by_mass=3,          # 3 + 3 != 10
                slots_by_fallthrough=3,
            )

    def test_consistent_accounting_does_not_raise(self) -> None:
        from coursegen.exam.coverage import build_report

        report = build_report(
            blueprint_id="x",
            course_map=[],
            node_slot_map={},
            node_marks_map={},
            unfilled_slots=[],
            warnings=[],
            slots_total=10,
            slots_by_mass=7,
            slots_by_fallthrough=3,
        )
        assert report.slots_filled == 10
        assert report.allocation_fidelity == pytest.approx(0.7)


class TestAllocationFidelity:
    def test_satisfiable_apportionment_reports_full_fidelity(self) -> None:
        """Plenty of spans everywhere → every slot placed because mass said so."""
        items, report = solve(_roomy_course_map(), _ten_slot_blueprint())

        assert len(items) == 10
        assert report.slots_filled == 10
        assert report.slots_by_fallthrough == 0
        assert report.slots_by_mass == 10
        assert report.allocation_fidelity == 1.0

    def test_scarce_spans_force_fallthrough(self) -> None:
        """High-mass nodes with one span each → apportionment cannot be satisfied.

        This is the defect the metric exists to expose: both headline numbers
        read perfect while half the paper was placed by exhaustion.
        """
        items, report = solve(
            _one_span_per_node_course_map(), _eight_slot_blueprint("scarce")
        )

        assert len(items) == 8
        assert report.slots_by_fallthrough > 0
        assert report.allocation_fidelity < 1.0
        assert report.slots_by_mass + report.slots_by_fallthrough == report.slots_filled
        assert report.allocation_fidelity == pytest.approx(
            report.slots_by_mass / report.slots_filled
        )
        # The blindness: neither existing ratio registers the degradation.
        assert report.coverage_ratio == 1.0
        assert report.fill_ratio == 1.0
        assert report.unfilled_slots == []

    def test_fidelity_is_zero_safe_when_nothing_is_filled(
        self, course_map: list[CourseMapNode]
    ) -> None:
        """slots_filled == 0 must yield 0.0, not a ZeroDivisionError."""
        blueprint = Blueprint(
            blueprint_id="nothing_fills",
            title="Nothing Fills",
            total_marks=6,            # 3 * 2
            duration_minutes=15,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Code MCQ",
                    item_type="mcq",
                    count=3,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                    requires_flags_any=["has_code"],   # no fixture node has it
                )
            ],
        )
        items, report = solve(course_map, blueprint)

        assert items == []
        assert report.slots_filled == 0
        assert report.slots_by_mass == 0
        assert report.slots_by_fallthrough == 0
        assert report.allocation_fidelity == 0.0

    @pytest.mark.parametrize(
        "blueprint_name", ["quiz_default", "midterm_default", "final_default"]
    )
    def test_invariant_holds_across_shipped_blueprints(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """slots_by_mass + slots_by_fallthrough == slots_filled == len(items).

        A slot filled through a path neither branch counts would be a real bug.
        """
        raw = json.loads(
            (
                Path(__file__).parent.parent
                / "coursegen/exam/blueprints" / f"{blueprint_name}.json"
            ).read_text(encoding="utf-8")
        )
        items, report = solve(course_map, Blueprint.model_validate(raw))

        assert report.slots_by_mass + report.slots_by_fallthrough == report.slots_filled
        assert report.slots_filled == len(items)
        assert 0.0 <= report.allocation_fidelity <= 1.0
        assert report.slots_by_mass >= 0
        assert report.slots_by_fallthrough >= 0


# ---------------------------------------------------------------------------
# Regression: adding allocation_fidelity is MEASUREMENT ONLY
#
# The expected ItemSpec[] is re-derived here in the test from the course map,
# the blueprint and _hare_apportionment — deliberately without consulting the
# new counters. If counting had moved a single item, this oracle diverges.
# ---------------------------------------------------------------------------

def _expected_items(
    nodes: list[CourseMapNode], section: SectionSpec
) -> list[tuple[str, str, list[str], str]]:
    """Independent re-derivation of one unconstrained section's ItemSpec[].

    Returns (slot_id, node_id, span_ids, spec_hash) in emission order.
    Mirrors §9.3: renormalise mass → Hare quota → fill by descending deficit
    (fall through to the lowest node_id with spans left) → ascending-token_count
    difficulty ladder → cycle bloom.
    """
    candidates = sorted(nodes, key=lambda n: n.node_id)
    total_mass = sum(n.instructional_mass for n in candidates)
    masses = {n.node_id: n.instructional_mass / total_mass for n in candidates}
    deficit = dict(_hare_apportionment(masses, section.count))
    available = {n.node_id: list(n.chunk_ids) for n in candidates}
    by_id = {n.node_id: n for n in candidates}

    picked: list[tuple[str, str]] = []
    for _ in range(section.count):
        primary = sorted(
            [nid for nid in deficit if deficit[nid] > 0 and available[nid]],
            key=lambda nid: (-deficit[nid], nid),
        )
        if primary:
            nid = primary[0]
            deficit[nid] -= 1
        else:
            fallback = sorted(n.node_id for n in candidates if available[n.node_id])
            if not fallback:
                break
            nid = fallback[0]
        picked.append((nid, available[nid].pop(0)))

    picked.sort(key=lambda p: (by_id[p[0]].token_count, p[0]))

    return [
        (
            f"{section.section_id}-{idx + 1:02d}",
            nid,
            [span],
            _spec_hash(
                nid,
                [span],
                section.item_type,
                section.bloom[idx % len(section.bloom)],
                section.marks_each,
                section.options_count,
            ),
        )
        for idx, (nid, span) in enumerate(picked)
    ]


class TestMeasurementOnly:
    @pytest.mark.parametrize(
        "nodes_fn, blueprint_fn",
        [
            (_roomy_course_map, _ten_slot_blueprint),
            (_one_span_per_node_course_map, lambda: _eight_slot_blueprint("scarce")),
        ],
    )
    def test_items_match_independently_derived_allocation(
        self, nodes_fn, blueprint_fn
    ) -> None:
        """Both branches of the fill loop, checked against an oracle written here.

        The roomy map exercises the mass branch only; the one-span map exercises
        both. Neither expectation is a stored golden file — each is computed in
        the test from the same inputs the solver receives.
        """
        nodes = nodes_fn()
        blueprint = blueprint_fn()
        items, _ = solve(nodes, blueprint)

        expected = _expected_items(nodes, blueprint.sections[0])
        actual = [(i.slot_id, i.node_id, i.span_ids, i.spec_hash) for i in items]
        assert actual == expected

    @pytest.mark.parametrize(
        "blueprint_name", ["quiz_default", "midterm_default", "final_default"]
    )
    def test_shipped_blueprint_hashes_recompute_from_item_fields(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """Every spec_hash still derives from exactly its documented inputs.

        The shipped blueprints are multi-section and flag-filtered, so the oracle
        above does not apply — but each emitted item must still hash to
        _spec_hash(its own fields). A counter that perturbed node, span, bloom or
        marks selection would break this.
        """
        raw = json.loads(
            (
                Path(__file__).parent.parent
                / "coursegen/exam/blueprints" / f"{blueprint_name}.json"
            ).read_text(encoding="utf-8")
        )
        blueprint = Blueprint.model_validate(raw)
        items, _ = solve(course_map, blueprint)
        options_by_section = {
            s.section_id: s.options_count for s in blueprint.sections
        }
        item_type_by_section = {s.section_id: s.item_type for s in blueprint.sections}

        assert items, f"{blueprint_name} produced no items"
        for item in items:
            section_id = item.slot_id.split("-")[0]
            assert item.spec_hash == _spec_hash(
                item.node_id,
                item.span_ids,
                item_type_by_section[section_id],
                item.bloom,
                item.marks,
                options_by_section[section_id],
            )

    @pytest.mark.parametrize(
        "blueprint_name", ["quiz_default", "midterm_default", "final_default"]
    )
    def test_item_stream_is_stable_across_solves(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """Slot ids, node ids, span ids and spec hashes, re-solved and compared."""
        raw = json.loads(
            (
                Path(__file__).parent.parent
                / "coursegen/exam/blueprints" / f"{blueprint_name}.json"
            ).read_text(encoding="utf-8")
        )
        blueprint = Blueprint.model_validate(raw)

        def stream(bp: Blueprint) -> list[tuple[str, str, tuple[str, ...], str]]:
            return [
                (i.slot_id, i.node_id, tuple(i.span_ids), i.spec_hash)
                for i in solve(course_map, bp)[0]
            ]

        assert stream(blueprint) == stream(blueprint)
