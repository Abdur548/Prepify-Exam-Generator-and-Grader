"""P4 gate tests — render + chat.

TDD RED first: these tests define the P4 APIs before implementation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from coursegen.contracts.coverage import CoverageReport, NodeCoverage
from coursegen.contracts.item import GeneratedItem, MCQOption, SourceRef


def _import_render():
    try:
        from coursegen.exam.render import check_weasyprint_available, render_exam_artifacts
    except ModuleNotFoundError as exc:
        pytest.fail(f"P4 render module missing: {exc}")
    return check_weasyprint_available, render_exam_artifacts


def _import_retrieve():
    try:
        from coursegen.retrieve.hybrid import RetrievedChunk, hybrid_search
        from coursegen.retrieve.rerank import RerankedChunk, rerank_chunks, should_use_material
    except ModuleNotFoundError as exc:
        pytest.fail(f"P4 retrieve modules missing: {exc}")
    return RetrievedChunk, hybrid_search, RerankedChunk, rerank_chunks, should_use_material


def _import_chat():
    try:
        from coursegen.chat.answer import answer_question
    except ModuleNotFoundError as exc:
        pytest.fail(f"P4 chat module missing: {exc}")
    return answer_question


def _item(slot_id: str = "A-01", stem: str = "What does <script>alert(1)</script> mean?") -> GeneratedItem:
    return GeneratedItem(
        slot_id=slot_id,
        stem=stem,
        options=[
            MCQOption(label="C", text="Third option"),
            MCQOption(label="A", text="First option"),
        ],
        correct_option="C",
        model_answer="The script tag is text here, not markup.",
        explanation="Escaping makes model output safe.",
        source_ref=SourceRef(file="lecture.pdf", pages=[3]),
    )


def _coverage() -> CoverageReport:
    return CoverageReport(
        blueprint_id="midterm_default",
        nodes_total=2,
        nodes_covered=1,
        coverage_ratio=0.5,
        slots_total=2,
        slots_filled=1,
        fill_ratio=0.5,
        slots_by_mass=1,
        slots_by_fallthrough=0,
        allocation_fidelity=1.0,
        mass_covered=0.75,
        per_node=[
            NodeCoverage(
                node_id="n1",
                path=["Module 1", "Topic A"],
                instructional_mass=0.75,
                marks_allocated=2,
                slots=["A-01"],
            ),
            NodeCoverage(
                node_id="n2",
                path=["Module 2"],
                instructional_mass=0.25,
                marks_allocated=0,
                slots=[],
            ),
        ],
        unfilled_slots=["A-02"],
        warnings=["A-02 could not be filled"],
    )


class TestRenderArtifacts:
    def test_render_writes_three_artifacts_and_escapes_model_output(self, tmp_path: Path) -> None:
        _, render_exam_artifacts = _import_render()

        def fake_pdf_writer(html: str, path: Path) -> None:
            path.write_bytes(b"%PDF-FAKE\n" + html.encode("utf-8"))

        result = render_exam_artifacts(
            items=[_item()],
            coverage_report=_coverage(),
            output_dir=tmp_path,
            title="Midterm",
            pdf_writer=fake_pdf_writer,
        )

        assert result.exam_pdf.exists()
        assert result.answer_key_pdf.exists()
        assert result.coverage_html.exists()
        html = result.exam_html.read_text(encoding="utf-8")
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "<script>alert(1)</script>" not in html

    def test_coverage_table_shows_all_three_ratios_and_fallthrough(self, tmp_path: Path) -> None:
        _, render_exam_artifacts = _import_render()
        result = render_exam_artifacts(
            items=[_item()],
            coverage_report=_coverage(),
            output_dir=tmp_path,
            title="Midterm",
            pdf_writer=lambda html, path: path.write_bytes(b"%PDF-FAKE"),
        )
        html = result.coverage_html.read_text(encoding="utf-8")
        assert "coverage_ratio" in html
        assert "fill_ratio" in html
        assert "allocation_fidelity" in html
        assert "slots_by_fallthrough" in html
        assert "A-02" in html

    def test_answer_key_uses_mcq_labels_as_authoritative(self, tmp_path: Path) -> None:
        _, render_exam_artifacts = _import_render()
        result = render_exam_artifacts(
            items=[_item()],
            coverage_report=_coverage(),
            output_dir=tmp_path,
            title="Midterm",
            pdf_writer=lambda html, path: path.write_bytes(b"%PDF-FAKE"),
        )
        key_html = result.answer_key_html.read_text(encoding="utf-8")
        assert "Correct option: C" in key_html
        assert "Correct option: A" not in key_html

    def test_weasyprint_preflight_reports_missing_native_runtime(self) -> None:
        check_weasyprint_available, _ = _import_render()
        try:
            check_weasyprint_available()
        except RuntimeError as exc:
            assert "WeasyPrint" in str(exc)
            assert "GTK" in str(exc) or "native" in str(exc)


class FakeQdrantClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        points = [
            type("Point", (), {
                "id": "tcp",
                "score": 1.0,
                "payload": {"text": "TCP is a transport protocol acronym.", "source_file": "slides.pptx", "page": 7},
            })(),
            type("Point", (), {
                "id": "other",
                "score": 0.2,
                "payload": {"text": "Unrelated chunk.", "source_file": "notes.pdf", "page": 2},
            })(),
        ]
        return type("Result", (), {"points": points})()


class TestRetrievePipeline:
    def test_hybrid_search_uses_dense_and_sparse_prefetch_and_top_k(self) -> None:
        RetrievedChunk, hybrid_search, *_ = _import_retrieve()
        client = FakeQdrantClient()
        chunks = hybrid_search(
            client=client,
            dense_vector=[0.1, 0.2],
            sparse_indices=[42],
            sparse_values=[1.0],
        )
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["limit"] == 10
        assert len(call["prefetch"]) == 2
        assert chunks[0].chunk_id == "tcp"
        assert chunks[0].file == "slides.pptx"
        assert chunks[0].page == 7

    def test_rerank_sorts_to_top_five_and_threshold_decides_skip(self) -> None:
        RetrievedChunk, _, _, rerank_chunks, should_use_material = _import_retrieve()
        candidates = [
            RetrievedChunk(chunk_id=f"c{i}", text=f"chunk {i}", file="f.pdf", page=i, score=0.0)
            for i in range(8)
        ]
        scores = {f"chunk {i}": float(i) for i in range(8)}
        reranked = rerank_chunks("query", candidates, scorer=lambda q, text: scores[text])
        assert [c.chunk_id for c in reranked] == ["c7", "c6", "c5", "c4", "c3"]
        assert should_use_material(reranked, threshold=3.5) is True
        assert should_use_material(reranked, threshold=9.0) is False


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def call(self, messages: list[dict[str, str]], response_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(messages)
        return {"answer": "TCP is explained in the uploaded slides."}


class TestChatAnswer:
    def test_relevant_query_answers_with_file_and_page_citation(self) -> None:
        RetrievedChunk, *_ = _import_retrieve()
        answer_question = _import_chat()
        llm = FakeLLM()
        candidates = [
            RetrievedChunk(chunk_id=f"c{i}", text=f"TCP acronym detail {i}", file="slides.pptx", page=i + 1, score=1.0)
            for i in range(6)
        ]

        result = answer_question(
            query="What is TCP?",
            history=[("old", "turn")] * 10,
            retrieve=lambda q: candidates,
            rerank=lambda q, cs: [c.with_score(10.0 - i) for i, c in enumerate(cs)],
            llm_client=llm,
            threshold=0.5,
        )

        assert result.from_material is True
        # Every sent context is cited, not just the top-ranked one: the model
        # answers from all four, so citing one names a source the claim may not
        # have come from.
        assert result.citations == [
            {"file": "slides.pptx", "page": 1},
            {"file": "slides.pptx", "page": 2},
            {"file": "slides.pptx", "page": 3},
            {"file": "slides.pptx", "page": 4},
        ]
        assert len(result.citations) == result.sent_context_count
        assert result.sent_context_count == 4
        assert result.history_turns_used == 6
        assert len(llm.calls) == 1

    def test_citations_cover_every_sent_context_and_dedupe(self) -> None:
        """
        Citations must span what was sent, and repeats collapse: four chunks from
        two pages cite two sources, not four identical lines.
        """
        RetrievedChunk, *_ = _import_retrieve()
        answer_question = _import_chat()
        candidates = [
            RetrievedChunk(
                chunk_id=f"c{i}",
                text=f"detail {i}",
                file="notes.pdf",
                page=1 if i < 2 else 2,   # two chunks per page
                score=1.0,
            )
            for i in range(4)
        ]

        result = answer_question(
            query="q",
            history=[],
            retrieve=lambda q: candidates,
            rerank=lambda q, cs: [c.with_score(10.0 - i) for i, c in enumerate(cs)],
            llm_client=FakeLLM(),
            threshold=0.5,
        )

        assert result.sent_context_count == 4
        assert result.citations == [
            {"file": "notes.pdf", "page": 1},
            {"file": "notes.pdf", "page": 2},
        ]

    def test_low_score_uses_not_from_material_path(self) -> None:
        RetrievedChunk, *_ = _import_retrieve()
        answer_question = _import_chat()
        llm = FakeLLM()
        candidates = [RetrievedChunk(chunk_id="c1", text="unrelated", file="x.pdf", page=1, score=1.0)]

        result = answer_question(
            query="Who won the world cup?",
            history=[],
            retrieve=lambda q: candidates,
            rerank=lambda q, cs: [cs[0].with_score(-5.0)],
            llm_client=llm,
            threshold=0.5,
        )

        assert result.from_material is False
        assert result.citations == []
        assert "not from your material" in result.answer.lower()
        assert len(llm.calls) == 1
