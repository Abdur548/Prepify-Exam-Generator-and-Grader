"""P4 gate tests — render + chat.

TDD RED first: these tests define the P4 APIs before implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from coursegen import config
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

    def test_a_synthesis_item_is_not_given_a_source_line(self, tmp_path: Path) -> None:
        """F3. The disclosure a student must accept before generating says, in as
        many words, that synthesis questions "carry no source".

        `_EXAM_TEMPLATE` printed `Source: <file>, pages <n>` for every item
        unconditionally, so on the shipped 20-item paper all 8 synthesis items
        carried a citation and were indistinguishable from the 12 grounded ones. A
        student turning to `06_CSP.pdf` page 1 for C-02 finds nothing, and the
        honest conclusion available to them is that their own notes are wrong.

        The span is real — it was the CONTEXT the item was written against — which
        is why the item has a `source_ref` at all and why suppressing it needs the
        slot list rather than a null check.
        """
        _, render_exam_artifacts = _import_render()

        def fake_pdf_writer(html: str, path: Path) -> None:
            path.write_bytes(b"%PDF")

        sourced, synthesis = _item(), _item()
        sourced.slot_id, synthesis.slot_id = "A-01", "B-01"

        result = render_exam_artifacts(
            items=[sourced, synthesis], coverage_report=_coverage(),
            output_dir=tmp_path, title="T", pdf_writer=fake_pdf_writer,
            synthesis_slots={"B-01"},
        )
        exam = result.exam_html.read_text(encoding="utf-8")
        key = result.answer_key_html.read_text(encoding="utf-8")

        # One citation, for the one grounded item — not two.
        assert exam.count("Source:") == 1, exam
        assert key.count("Source:") == 1
        # And the synthesis item says what the app says, rather than nothing at
        # all: a silently missing line reads as a rendering bug.
        assert "asks you to build something new" in exam
        assert "asks you to build something new" in key

    def test_every_item_is_cited_when_none_is_synthesis(self, tmp_path: Path) -> None:
        """The counterpart. Suppressing the line for grounded items would remove
        the provenance the whole product is built on."""
        _, render_exam_artifacts = _import_render()
        a, b = _item(), _item()
        a.slot_id, b.slot_id = "A-01", "A-02"

        result = render_exam_artifacts(
            items=[a, b], coverage_report=_coverage(), output_dir=tmp_path,
            title="T", pdf_writer=lambda html, path: path.write_bytes(b"%PDF"),
            synthesis_slots=set(),
        )
        exam = result.exam_html.read_text(encoding="utf-8")
        assert exam.count("Source:") == 2
        assert "asks you to build something new" not in exam

    def test_weasyprint_preflight_raises_with_an_actionable_message(self) -> None:
        """
        The message is the whole point of the preflight: R8 wants a legible startup
        failure instead of a crash mid-generation, so it must name the library and
        the native runtime.

        The import is forced to fail rather than being left to the machine's actual
        state. The previous version of this test asserted only inside an
        `except RuntimeError`, so once GTK was installed the call succeeded, the
        except never fired, and the test passed having executed ZERO assertions —
        green in both worlds, therefore evidence in neither.
        """
        check_weasyprint_available, _ = _import_render()

        # sys.modules[name] = None makes `import name` raise ImportError.
        with patch.dict(sys.modules, {"weasyprint": None}):
            with pytest.raises(RuntimeError) as excinfo:
                check_weasyprint_available()

        message = str(excinfo.value)
        assert "WeasyPrint" in message
        assert "GTK" in message or "native" in message

    def test_weasyprint_preflight_is_silent_when_the_runtime_is_present(self) -> None:
        """The other half: with the import succeeding, the preflight must not raise."""
        check_weasyprint_available, _ = _import_render()

        with patch.dict(sys.modules, {"weasyprint": MagicMock()}):
            check_weasyprint_available()   # must not raise


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


class TestRerankerThresholdCalibration:
    """
    Every other test in this file injects an explicit `threshold=`, which is right —
    they test the mechanism, not the value. The consequence is that nothing exercises
    the CONFIGURED default, and that default is the one a reader is most likely to
    "correct" back to something in [0, 1] on the assumption that it is a similarity.

    It is not. `ms-marco-MiniLM-L-6-v2` runs with an Identity activation and emits
    unbounded logits; measured 2026-08-29, a perfect match scored +9.48 and nonsense
    -11.23. These bounds pin the calibration recorded in config.py.
    """

    # Measured over a lecture-slide-shaped corpus — see config.RERANKER_THRESHOLD.
    WEAKEST_GENUINE_MATCH = 1.68
    STRONGEST_NEAR_MISS = -5.99

    def test_default_threshold_is_a_logit_not_a_probability(self) -> None:
        assert not (0.0 < config.RERANKER_THRESHOLD <= 1.0), (
            f"RERANKER_THRESHOLD={config.RERANKER_THRESHOLD} looks like a probability. "
            "The reranker emits raw logits (~-11..+11), not a 0-1 similarity."
        )

    def test_default_threshold_separates_the_measured_distributions(self) -> None:
        """
        It must admit the weakest genuinely-relevant query and still reject the
        strongest near-miss. Near-misses — same field, adjacent vocabulary, absent
        from the corpus — are the real boundary; far-irrelevant queries all sat below
        -10.9 and never came close to mattering.
        """
        assert self.STRONGEST_NEAR_MISS < config.RERANKER_THRESHOLD < self.WEAKEST_GENUINE_MATCH, (
            f"RERANKER_THRESHOLD={config.RERANKER_THRESHOLD} falls outside the measured "
            f"boundary ({self.STRONGEST_NEAR_MISS}, {self.WEAKEST_GENUINE_MATCH}); "
            "recalibrate rather than nudging it."
        )

    def test_threshold_has_margin_on_both_sides(self) -> None:
        """
        A value inside the gap but hard against one edge is fragile: 0.5 classified the
        measured set perfectly while sitting 1.2 from the relevant floor and 6.5 from
        the near-miss ceiling, so a lightly-covered question would have been wrongly
        routed to "not from your material".
        """
        below = config.RERANKER_THRESHOLD - self.STRONGEST_NEAR_MISS
        above = self.WEAKEST_GENUINE_MATCH - config.RERANKER_THRESHOLD
        assert min(below, above) >= 2.0, (
            f"margins are lopsided: {below:.2f} above the near-miss ceiling, "
            f"{above:.2f} below the relevant floor"
        )


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


class TestThePipelineTellsTheRendererWhichItemsAreSynthesis:
    """The template can be correct and still print the wrong thing.

    `render_exam_artifacts` cannot see `grounding` — it receives `GeneratedItem`s,
    which do not carry it — so suppressing the citation depends entirely on
    `pipeline.generate_paper` passing the slot ids. Mutating that call to `set()`
    left every template test green while the printed paper went back to citing a
    page for every synthesis item. This is the same shape as the audit's "a guard
    is reported as present and is not", so it gets its own test rather than
    trusting the wiring.
    """

    def test_a_synthesis_item_reaches_the_printed_paper_uncited(self, tmp_path, monkeypatch) -> None:
        from unittest.mock import patch

        from coursegen import config
        from coursegen.contracts.blueprint import Blueprint, SectionSpec
        from coursegen.contracts.coverage import CoverageReport
        from coursegen.contracts.item import GeneratedItem, ItemSpec, SourceRef
        from coursegen.exam.generate import GenerationResult
        from coursegen.pipeline import generate_paper

        blueprint = Blueprint(
            blueprint_id="bp", title="T", total_marks=10, duration_minutes=30,
            sections=[
                SectionSpec(section_id="A", title="Recall", item_type="short",
                            count=1, marks_each=5, bloom=["remember"]),
                SectionSpec(section_id="B", title="Build", item_type="long",
                            count=1, marks_each=5, bloom=["create"],
                            grounding="synthesis"),
            ],
        )
        specs = [
            ItemSpec(slot_id="A-01", item_type="short", marks=5, bloom="remember",
                     node_id="n1", span_ids=["c1"], eligibility=[], spec_hash="h1",
                     grounding="span"),
            ItemSpec(slot_id="B-01", item_type="long", marks=5, bloom="create",
                     node_id="n1", span_ids=["c1"], eligibility=[], spec_hash="h2",
                     grounding="synthesis"),
        ]
        items = [
            GeneratedItem(slot_id=s.slot_id, stem=f"Q {s.slot_id}?",
                          model_answer="answer", explanation="because",
                          source_ref=SourceRef(file="deck.pdf", pages=[7]))
            for s in specs
        ]
        coverage = CoverageReport(
            blueprint_id="bp", nodes_total=1, nodes_covered=1, coverage_ratio=1.0,
            slots_total=2, slots_filled=2, fill_ratio=1.0, slots_by_mass=2,
            slots_by_fallthrough=0, allocation_fidelity=1.0, mass_covered=1.0,
            unfilled_slots=[], warnings=[], per_node=[],
        )

        # OUTPUT_DIR redirected: generate_paper writes paper.json itself, and a
        # test must not write into the product's own output directory.
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
        with patch("coursegen.pipeline.load_blueprint", return_value=blueprint), \
             patch("coursegen.ingest.coursemap.load_course_map", return_value=[]), \
             patch("coursegen.exam.allocate.solve", return_value=(specs, coverage)), \
             patch("coursegen.ingest.index.read_spans", return_value=({"c1": "ctx"}, {})), \
             patch("coursegen.exam.generate.generate_exam",
                   return_value=GenerationResult(items=items, manifest={})), \
             patch("coursegen.exam.render._write_pdf_with_weasyprint",
                   side_effect=lambda html, path: path.write_bytes(b"%PDF")):
            generate_paper(blueprint_id="bp", title="T", llm_client=object())

        exam = (tmp_path / "exam.html").read_text(encoding="utf-8")
        assert exam.count("Source:") == 1, (
            "the synthesis item is cited on the printed paper, which the "
            "disclosure told the student would not happen"
        )
        assert "asks you to build something new" in exam
