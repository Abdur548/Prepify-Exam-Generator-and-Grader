"""P0 gate tests: all five Pydantic contracts instantiate and round-trip correctly."""
from __future__ import annotations

import pytest

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.contracts.coverage import CoverageReport, NodeCoverage
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption, SourceRef


class TestCourseMapNode:
    def test_roundtrip(self) -> None:
        node = CourseMapNode(
            node_id="abc123",
            path=["Ch 3 Optimization", "3.2 Momentum", "Nesterov"],
            source_file="lecture.pdf",
            page_span=(10, 14),
            token_count=320,
            chunk_ids=["uuid5-aaa"],
            key_terms=["gradient descent", "momentum"],
            flags=NodeFlags(has_equation=True),
            instructional_mass=0.15,
        )
        assert node.node_id == "abc123"
        assert node.flags.has_equation is True
        assert node.flags.has_figure is False
        assert node.flags.has_table is False

    def test_instructional_mass_accepts_float(self) -> None:
        node = CourseMapNode(
            node_id="n1",
            path=["Ch 1"],
            source_file="f.pdf",
            page_span=(1, 2),
            token_count=100,
            chunk_ids=[],
            key_terms=[],
            flags=NodeFlags(),
            instructional_mass=0.0,
        )
        assert node.instructional_mass == 0.0


class TestBlueprint:
    def test_roundtrip(self) -> None:
        bp = Blueprint(
            blueprint_id="midterm_default",
            title="Midterm Exam",
            total_marks=100,
            duration_minutes=120,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Multiple Choice",
                    item_type="mcq",
                    count=20,
                    marks_each=2,
                    bloom=["remember", "understand"],
                    options_count=4,
                ),
                SectionSpec(
                    section_id="B",
                    title="Short Answer",
                    item_type="short",
                    count=5,
                    marks_each=4,
                    bloom=["apply", "analyse"],
                ),
            ],
        )
        assert bp.sections[0].item_type == "mcq"
        assert bp.sections[0].options_count == 4
        assert bp.sections[1].options_count is None

    def test_requires_flags_any_optional(self) -> None:
        sec = SectionSpec(
            section_id="C",
            title="Long Answer",
            item_type="long",
            count=2,
            marks_each=10,
            bloom=["evaluate"],
            requires_flags_any=["has_figure", "has_equation"],
        )
        assert sec.requires_flags_any == ["has_figure", "has_equation"]


class TestItemSpec:
    def test_roundtrip(self) -> None:
        spec = ItemSpec(
            slot_id="A-01",
            item_type="mcq",
            marks=2,
            bloom="remember",
            node_id="node-xyz",
            span_ids=["span-1"],
            eligibility=[],
            spec_hash="a" * 64,
        )
        assert spec.slot_id == "A-01"
        assert len(spec.spec_hash) == 64


class TestGeneratedItem:
    def test_mcq_roundtrip(self) -> None:
        item = GeneratedItem(
            slot_id="A-01",
            stem="Which of the following best describes X?",
            options=[
                MCQOption(label="A", text="Option one"),
                MCQOption(label="B", text="Option two"),
                MCQOption(label="C", text="Option three"),
                MCQOption(label="D", text="Option four"),
            ],
            correct_option="A",
            model_answer="Option one is correct because...",
            explanation="X is defined as ...",
            source_ref=SourceRef(file="lecture.pdf", pages=[3, 4]),
        )
        assert item.correct_option == "A"
        assert len(item.options) == 4

    def test_short_answer_no_options(self) -> None:
        item = GeneratedItem(
            slot_id="B-01",
            stem="Explain the concept of X.",
            options=None,
            correct_option=None,
            model_answer="X is ...",
            explanation="See page 5.",
            source_ref=SourceRef(file="notes.pdf", pages=[5]),
        )
        assert item.options is None
        assert item.correct_option is None


class TestCoverageReport:
    def test_roundtrip(self) -> None:
        report = CoverageReport(
            blueprint_id="midterm_default",
            nodes_total=20,
            nodes_covered=18,
            coverage_ratio=0.9,
            mass_covered=0.87,
            per_node=[
                NodeCoverage(
                    node_id="n1",
                    path=["Ch 1"],
                    instructional_mass=0.1,
                    marks_allocated=5,
                    slots=["A-01", "B-01"],
                )
            ],
            unfilled_slots=[],
            warnings=[],
        )
        assert report.coverage_ratio == 0.9
        assert report.nodes_covered == 18

    def test_unfilled_slots_and_warnings(self) -> None:
        report = CoverageReport(
            blueprint_id="quiz_default",
            nodes_total=5,
            nodes_covered=3,
            coverage_ratio=0.6,
            mass_covered=0.55,
            per_node=[],
            unfilled_slots=["A-04", "A-05"],
            warnings=["Node n4 exhausted — fell through to next-deficit node"],
        )
        assert len(report.unfilled_slots) == 2
        assert len(report.warnings) == 1
