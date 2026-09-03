"""The dry run — the free preview the frontend decides against.

The point of these is that a plan must never overstate what the material supports.
A student reads it and commits their quota on the strength of it, so a section
reported as whole had better be whole.
"""
from __future__ import annotations

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import ItemSpec
from coursegen.exam.plan import build_plan


def _nodes() -> list[CourseMapNode]:
    return [
        CourseMapNode(
            node_id=f"n{i}", path=["Course", f"T{i}"], source_file=f"deck{i % 2}.pdf",
            page_span=[i, i], key_terms=[f"t{i}"], token_count=100,
            instructional_mass=1.0, chunk_ids=[f"c{i}"], flags=NodeFlags(),
        )
        for i in range(4)
    ]


def _blueprint(count_a: int = 2) -> Blueprint:
    return Blueprint(
        blueprint_id="bp", title="Test", total_marks=30, duration_minutes=60,
        sections=[
            SectionSpec(section_id="A", title="Recall", item_type="mcq",
                        count=count_a, marks_each=5, bloom=["remember"]),
            SectionSpec(section_id="B", title="Applied", item_type="long",
                        count=1, marks_each=20, bloom=["apply"], grounding="synthesis"),
        ],
    )


def _spec(slot: str, node: str, grounding: str = "span", marks: int = 5) -> ItemSpec:
    return ItemSpec(
        slot_id=slot, item_type="mcq", marks=marks, bloom="remember", node_id=node,
        span_ids=[f"c{node[-1]}"], eligibility=[], spec_hash=f"h{slot}",
        grounding=grounding,
    )


def _coverage(total=3, filled=3, unfilled=()) -> CoverageReport:
    return CoverageReport(
        blueprint_id="bp", nodes_total=4, nodes_covered=filled, coverage_ratio=0.5,
        slots_total=total, slots_filled=filled, fill_ratio=filled / total,
        slots_by_mass=filled, slots_by_fallthrough=0, allocation_fidelity=1.0,
        mass_covered=0.5, unfilled_slots=list(unfilled), warnings=[], per_node=[],
    )


class TestSources:

    def test_each_section_names_the_files_it_draws_on(self) -> None:
        """The line that makes a dry run worth reading.

        Not "10 slots planned" but "from deck0.pdf and deck1.pdf" — the student's
        own uploads, named, before anything is generated.
        """
        specs = [_spec("A-01", "n0"), _spec("A-02", "n1")]
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["sections"][0]["sources"] == ["deck0.pdf", "deck1.pdf"]

    def test_sources_are_deduplicated_and_ordered(self) -> None:
        specs = [_spec("A-01", "n0"), _spec("A-02", "n2")]  # both deck0.pdf
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["sections"][0]["sources"] == ["deck0.pdf"]

    def test_summary_lists_every_file_the_paper_touches(self) -> None:
        specs = [_spec("A-01", "n0"), _spec("A-02", "n1")]
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["summary"]["sources_used"] == ["deck0.pdf", "deck1.pdf"]


class TestShortfall:

    def test_a_section_the_material_cannot_fill_reports_short_by(self) -> None:
        """The most useful thing a dry run can surface.

        Cheap to fix before generating (upload more), expensive to discover after.
        """
        specs = [_spec("A-01", "n0")]  # blueprint asks for 2
        plan = build_plan(specs, _coverage(3, 1, ["A-02"]), _blueprint(2), _nodes())
        section = plan["sections"][0]
        assert section["slots_requested"] == 2
        assert section["slots_planned"] == 1
        assert section["short_by"] == 1

    def test_a_whole_section_reports_no_shortfall(self) -> None:
        specs = [_spec("A-01", "n0"), _spec("A-02", "n1")]
        plan = build_plan(specs, _coverage(), _blueprint(2), _nodes())
        assert plan["sections"][0]["short_by"] == 0

    def test_marks_planned_never_exceeds_what_the_material_supports(self) -> None:
        """A plan that promises the full marks on a short paper is a lie the
        student pays for."""
        specs = [_spec("A-01", "n0")]
        plan = build_plan(specs, _coverage(3, 1, ["A-02"]), _blueprint(2), _nodes())
        assert plan["summary"]["marks_planned"] == 5
        assert plan["summary"]["short_by_marks"] == 25
        assert plan["total_marks"] == 30


class TestSynthesis:

    def test_a_synthesis_section_is_flagged_not_sourced(self) -> None:
        specs = [_spec("B-01", "n0", grounding="synthesis", marks=20)]
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["sections"][1]["from_material"] is False

    def test_synthesis_items_are_counted(self) -> None:
        specs = [_spec("A-01", "n0"), _spec("B-01", "n1", grounding="synthesis")]
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["summary"]["synthesis_items"] == 1


class TestCostPreview:

    def test_estimated_calls_rounds_up(self) -> None:
        """The student is about to spend their own quota. Under-promising the
        cost is worse than over-promising it."""
        specs = [_spec(f"A-{i:02d}", "n0") for i in range(config.BATCH_SIZE + 1)]
        plan = build_plan(specs, _coverage(), _blueprint(), _nodes())
        assert plan["summary"]["estimated_calls"] == 2

    def test_an_empty_plan_costs_nothing(self) -> None:
        plan = build_plan([], _coverage(3, 0, ["A-01"]), _blueprint(), _nodes())
        assert plan["summary"]["estimated_calls"] == 0
        assert plan["summary"]["marks_planned"] == 0
