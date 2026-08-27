"""P0 gate tests: all five Pydantic contracts instantiate and round-trip correctly."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
            total_marks=60,        # 20*2 + 5*4 — must match the sections below
            title="Midterm Exam",
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
            slots_total=25,
            slots_filled=25,
            fill_ratio=1.0,
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
            slots_total=5,
            slots_filled=3,
            fill_ratio=0.6,
            mass_covered=0.55,
            per_node=[],
            unfilled_slots=["A-04", "A-05"],
            warnings=["Node n4 exhausted — fell through to next-deficit node"],
        )
        assert len(report.unfilled_slots) == 2
        assert len(report.warnings) == 1



class TestSectionSpecValidation:
    def test_empty_bloom_is_rejected(self) -> None:
        """allocate.py cycles bloom with `bloom_list[idx % len(bloom_list)]`,
        so an empty list would be a ZeroDivisionError at solve time."""
        with pytest.raises(ValidationError):
            SectionSpec(
                section_id="A",
                title="Multiple Choice",
                item_type="mcq",
                count=5,
                marks_each=2,
                bloom=[],
                options_count=4,
            )

    def test_single_bloom_is_accepted(self) -> None:
        sec = SectionSpec(
            section_id="A",
            title="Multiple Choice",
            item_type="mcq",
            count=5,
            marks_each=2,
            bloom=["remember"],
            options_count=4,
        )
        assert sec.bloom == ["remember"]


class TestBlueprintMarksValidation:
    def test_marks_mismatch_is_rejected(self) -> None:
        """A blueprint must not print 'Total: 100 marks' over a 95-mark paper."""
        with pytest.raises(ValidationError) as exc:
            Blueprint(
                blueprint_id="bad_marks",
                title="Mismatched",
                total_marks=100,
                duration_minutes=120,
                sections=[
                    SectionSpec(
                        section_id="A",
                        title="Multiple Choice",
                        item_type="mcq",
                        count=20,
                        marks_each=2,      # 40, not 100
                        bloom=["remember"],
                        options_count=4,
                    )
                ],
            )
        message = str(exc.value)
        assert "40" in message and "100" in message

    def test_marks_match_is_accepted(self) -> None:
        bp = Blueprint(
            blueprint_id="good_marks",
            title="Matched",
            total_marks=40,
            duration_minutes=60,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="Multiple Choice",
                    item_type="mcq",
                    count=20,
                    marks_each=2,
                    bloom=["remember"],
                    options_count=4,
                )
            ],
        )
        assert bp.total_marks == 40

    @pytest.mark.parametrize(
        "name", ["midterm_default", "final_default", "quiz_default"]
    )
    def test_shipped_blueprints_validate(self, name: str) -> None:
        """The three shipped blueprints are arithmetically correct and must stay so."""
        path = (
            Path(__file__).parent.parent
            / "coursegen" / "exam" / "blueprints" / f"{name}.json"
        )
        bp = Blueprint.model_validate(json.loads(path.read_text()))
        assert sum(s.count * s.marks_each for s in bp.sections) == bp.total_marks


class TestGeneratedItemValidation:
    """P3's first validation gate is a Pydantic parse of GeneratedItem. It has to
    reject something, or it gates nothing."""

    def test_options_without_correct_option_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedItem(
                slot_id="A-01",
                stem="Which of the following best describes X?",
                options=[
                    MCQOption(label="A", text="Option one"),
                    MCQOption(label="B", text="Option two"),
                ],
                correct_option=None,
                model_answer="Option one is correct.",
                explanation="X is defined as ...",
                source_ref=SourceRef(file="lecture.pdf", pages=[3]),
            )

    def test_correct_option_not_matching_a_label_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            GeneratedItem(
                slot_id="A-02",
                stem="Which of the following best describes X?",
                options=[
                    MCQOption(label="A", text="Option one"),
                    MCQOption(label="B", text="Option two"),
                ],
                correct_option="C",      # no such label
                model_answer="Option one is correct.",
                explanation="X is defined as ...",
                source_ref=SourceRef(file="lecture.pdf", pages=[3]),
            )
        assert "correct_option" in str(exc.value)

    def test_correct_option_without_options_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedItem(
                slot_id="B-01",
                stem="Explain the concept of X.",
                options=None,
                correct_option="A",
                model_answer="X is ...",
                explanation="See page 5.",
                source_ref=SourceRef(file="notes.pdf", pages=[5]),
            )

    def test_well_formed_mcq_is_accepted(self) -> None:
        item = GeneratedItem(
            slot_id="A-03",
            stem="Which of the following best describes X?",
            options=[
                MCQOption(label="A", text="Option one"),
                MCQOption(label="B", text="Option two"),
                MCQOption(label="C", text="Option three"),
                MCQOption(label="D", text="Option four"),
            ],
            correct_option="C",
            model_answer="Option three is correct because ...",
            explanation="X is defined as ...",
            source_ref=SourceRef(file="lecture.pdf", pages=[3, 4]),
        )
        assert item.correct_option == "C"

    def test_well_formed_short_answer_is_accepted(self) -> None:
        item = GeneratedItem(
            slot_id="B-02",
            stem="Explain the concept of X.",
            options=None,
            correct_option=None,
            model_answer="X is ...",
            explanation="See page 5.",
            source_ref=SourceRef(file="notes.pdf", pages=[5]),
        )
        assert item.options is None
