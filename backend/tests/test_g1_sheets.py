"""G1 — every generated paper becomes an answerable sheet.

Before this, `paper.json` was overwritten by every run. An attempt pointing at it
would have pointed at whatever paper happened to be newest, so a student who
generated a second paper and then submitted answers to the first would have had
them marked against the wrong questions.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption, SourceRef
from coursegen.exam.paper import build_paper
from coursegen.grade.sheets import archive_paper, paper_sheet, sheet_id_for
from coursegen.grade.store import Store

NOW = "2026-10-13T10:00:00"


def _blueprint() -> Blueprint:
    return Blueprint(
        blueprint_id="bp", title="Test Paper", total_marks=14, duration_minutes=60,
        sections=[
            SectionSpec(section_id="A", title="Recall", item_type="mcq",
                        count=3, marks_each=2, bloom=["remember"]),
            SectionSpec(section_id="B", title="Applied", item_type="short",
                        count=1, marks_each=8, bloom=["apply"]),
        ],
    )


def _spec(slot: str, node: str, item_type: str = "mcq", marks: int = 2) -> ItemSpec:
    return ItemSpec(
        slot_id=slot, item_type=item_type, marks=marks, bloom="remember",
        node_id=node, span_ids=[f"c-{slot}"], eligibility=[], spec_hash=f"h-{slot}",
    )


def _item(slot: str, mcq: bool = True, stem: str | None = None) -> GeneratedItem:
    return GeneratedItem(
        slot_id=slot, stem=stem or f"Question {slot}?",
        options=[MCQOption(label="A", text="one"), MCQOption(label="B", text="two")] if mcq else None,
        correct_option="B" if mcq else None,
        model_answer="B. two" if mcq else "A written answer.",
        explanation="because", source_ref=SourceRef(file="deck.pdf", pages=[7]),
    )


def _coverage() -> CoverageReport:
    return CoverageReport(
        blueprint_id="bp", nodes_total=3, nodes_covered=3, coverage_ratio=1.0,
        slots_total=4, slots_filled=4, fill_ratio=1.0, slots_by_mass=4,
        slots_by_fallthrough=0, allocation_fidelity=1.0, mass_covered=1.0,
        unfilled_slots=[], warnings=[], per_node=[],
    )


def _paper(stem_a01: str | None = None) -> tuple[dict, list[ItemSpec]]:
    specs = [_spec("A-01", "n1"), _spec("A-02", "n2"), _spec("A-03", "n3"),
             _spec("B-01", "n1", "short", 8)]
    # A-03 is placed but has no item: lost at a gate, so the slot is unfilled.
    items = [_item("A-01", stem=stem_a01), _item("A-02"), _item("B-01", mcq=False)]
    paper = build_paper(
        items=items, specs=specs, blueprint=_blueprint(), coverage=_coverage(),
        title="T", span_text_by_id={},
        node_path_by_id={"n1": ["Lecture 3", "3.2 Search"], "n2": ["Lecture 4"]},
    )
    return paper, specs


class TestPaperItemsKnowTheirNode:

    def test_a_filled_item_carries_its_node_and_topic(self) -> None:
        paper, _ = _paper()
        item = paper["sections"][0]["items"][0]
        assert item["node_id"] == "n1"
        assert item["topic"] == "Lecture 3 › 3.2 Search"

    def test_a_node_missing_from_the_path_map_has_no_topic(self) -> None:
        paper, _ = _paper()
        b01 = paper["sections"][1]["items"][0]
        assert b01["node_id"] == "n1"
        a02_map_missing = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01", "n9")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )["sections"][0]["items"][0]
        assert a02_map_missing["node_id"] == "n9" and a02_map_missing["topic"] is None

    def test_an_unfilled_slot_carries_neither(self) -> None:
        paper, _ = _paper()
        gap = paper["sections"][0]["items"][2]
        assert gap["filled"] is False
        assert "node_id" not in gap and "topic" not in gap


class TestSheetId:

    def test_stable_for_the_same_paper(self) -> None:
        assert sheet_id_for(_paper()[0]) == sheet_id_for(_paper()[0])

    def test_ignores_an_existing_sheet_id(self) -> None:
        paper, _ = _paper()
        assert sheet_id_for({**paper, "sheet_id": "anything"}) == sheet_id_for(paper)

    def test_changes_when_a_question_changes(self) -> None:
        assert sheet_id_for(_paper()[0]) != sheet_id_for(_paper("Different?")[0])

    def test_is_sixteen_hex_characters(self) -> None:
        sid = sheet_id_for(_paper()[0])
        assert len(sid) == 16 and int(sid, 16) >= 0


class TestPaperSheet:

    def test_one_item_per_filled_question(self) -> None:
        paper, specs = _paper()
        sheet = paper_sheet(paper, specs, NOW)
        assert [i.slot_id for i in sheet.items] == ["A-01", "A-02", "B-01"]
        assert sheet.kind == "paper" and sheet.blueprint_id == "bp"

    def test_items_carry_the_key_and_the_full_spec(self) -> None:
        paper, specs = _paper()
        a01 = paper_sheet(paper, specs, NOW).items[0]
        assert a01.correct_option == "B" and a01.from_material is True
        assert a01.spec == specs[0].model_dump()
        assert (a01.node_id, a01.spec_hash, a01.marks) == ("n1", "h-A-01", 2)

    def test_a_written_item_has_no_key(self) -> None:
        paper, specs = _paper()
        b01 = paper_sheet(paper, specs, NOW).items[2]
        assert b01.correct_option is None and b01.item_type == "short"


class TestArchive:

    def test_archiving_stores_the_paper_and_returns_its_id(self) -> None:
        paper, specs = _paper()
        archived = archive_paper(paper, specs, now=NOW)
        assert archived["sheet_id"] == sheet_id_for(paper)
        with Store() as s:
            assert s.get_sheet(archived["sheet_id"]).document == archived

    def test_the_input_paper_is_not_mutated(self) -> None:
        paper, specs = _paper()
        archive_paper(paper, specs, now=NOW)
        assert "sheet_id" not in paper

    def test_an_older_paper_survives_a_newer_one(self) -> None:
        """Review Focus 2, at the level the pipeline uses."""
        old = archive_paper(*_paper("Old?"), now=NOW)
        archive_paper(*_paper("New?"), now=NOW)
        with Store() as s:
            assert s.get_sheet(old["sheet_id"]).document["sections"][0]["items"][0]["stem"] == "Old?"

    def test_a_storage_failure_never_fails_the_paper(self) -> None:
        """The model calls are already spent. Losing the paper over a disk error
        would throw away work the student is waiting on."""
        paper, specs = _paper()
        with patch.object(Store, "put_sheet", side_effect=sqlite3.OperationalError("disk I/O error")):
            archived = archive_paper(paper, specs, now=NOW)
        assert archived["sheet_id"] is None
        assert any("could not be saved for answering" in w for w in archived["summary"]["warnings"])


class TestPipelineArchives:

    def test_paper_json_carries_a_sheet_id_the_store_can_resolve(self, tmp_path, monkeypatch) -> None:
        from coursegen.contracts.course_map import CourseMapNode, NodeFlags
        from coursegen.exam.generate import GenerationResult
        from coursegen.pipeline import generate_paper

        nodes = [CourseMapNode(
            node_id="n1", path=["Lecture 3", "3.2 Search"], source_file="deck.pdf",
            page_span=(1, 5), key_terms=[], token_count=100, instructional_mass=1.0,
            chunk_ids=["c-A-01"], flags=NodeFlags(),
        )]
        blueprint = Blueprint(
            blueprint_id="bp", title="Quiz", total_marks=2, duration_minutes=10,
            sections=[SectionSpec(section_id="A", title="MCQ", item_type="mcq",
                                  count=1, marks_each=2, bloom=["remember"])],
        )
        specs = [_spec("A-01", "n1")]
        coverage = CoverageReport(
            blueprint_id="bp", nodes_total=1, nodes_covered=1, coverage_ratio=1.0,
            slots_total=1, slots_filled=1, fill_ratio=1.0, slots_by_mass=1,
            slots_by_fallthrough=0, allocation_fidelity=1.0, mass_covered=1.0,
            unfilled_slots=[], warnings=[], per_node=[],
        )
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
        with patch("coursegen.pipeline.load_blueprint", return_value=blueprint), \
             patch("coursegen.ingest.coursemap.load_course_map", return_value=nodes), \
             patch("coursegen.exam.allocate.solve", return_value=(specs, coverage)), \
             patch("coursegen.ingest.index.read_spans", return_value=({"c-A-01": "ctx"}, {})), \
             patch("coursegen.exam.generate.generate_exam",
                   return_value=GenerationResult(items=[_item("A-01")], manifest={})), \
             patch("coursegen.exam.render._write_pdf_with_weasyprint",
                   side_effect=lambda html, path: path.write_bytes(b"%PDF")):
            result = generate_paper(blueprint_id="bp", title="T", llm_client=object())

        written = json.loads((tmp_path / "paper.json").read_text(encoding="utf-8"))
        assert written["sheet_id"] and written == result.paper
        assert written["sections"][0]["items"][0]["topic"] == "Lecture 3 › 3.2 Search"
        with Store() as s:
            sheet = s.get_sheet(written["sheet_id"])
        assert [i.slot_id for i in sheet.items] == ["A-01"]
