"""
P1 gate tests — ingest pipeline.

Gates (all must pass before P2):
  1. Ingest twice → identical POINT COUNT, identical node IDs, identical
     course_map.json hash
  2. Slide-exported PDF: heading_fraction < HEADING_MAX_BLOCK_PCT (0.30)
  3. Malformed PDF fails that file only; parse_directory returns remaining docs
  4. Content flags (figure / table / code / equation) set on documents that carry
     the corresponding content
  5. OCR not triggered on a native-text PDF (no warning about missing text layer)
  6. Two documents with the same heading produce distinct node_ids
  7. A course map produced by REAL ingest can be solved by exam.allocate.solve()
     without the flag-filtered section starving

The point-count gate (1) and the ingest→solver seam (7) use a REAL local-mode
Qdrant on tmp_path. `QdrantClient(path=...)` is pure filesystem — no network — so
this does not breach the zero-network-calls rule for the default test run. Local
mode takes an EXCLUSIVE FILE LOCK, so every client opened here is closed in a
`finally` before the next one opens, and each test gets a fresh tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.exam.allocate import solve
from coursegen.ingest.chunk import Chunk, _chunk_id, chunk_section
from coursegen.ingest.coursemap import (
    _compute_node_id,
    _compute_instructional_mass,
    _derive_flags,
    ingest,
    load_course_map,
    save_course_map,
    _RawNode,
)
from coursegen.ingest.index import get_client
from coursegen.ingest.parse import (
    TextBlock,
    count_math_chars,
    is_code_font,
    is_equation_content,
    is_math_font,
    parse_directory,
    parse_file,
)
from coursegen.ingest.structure import LeafSection, extract_sections, heading_fraction
from coursegen.contracts.course_map import NodeFlags


# ---------------------------------------------------------------------------
# Parse — PDF
# ---------------------------------------------------------------------------

class TestParseNativePDF:
    def test_text_blocks_extracted(self, native_pdf: Path) -> None:
        doc = parse_file(native_pdf)
        text_blocks = [b for b in doc.blocks if b.block_type == "text"]
        assert len(text_blocks) >= 8

    def test_font_sizes_present(self, native_pdf: Path) -> None:
        doc = parse_file(native_pdf)
        text_blocks = [b for b in doc.blocks if b.block_type == "text"]
        assert all(b.span_font_sizes for b in text_blocks)
        assert all(b.max_font_size > 0 for b in text_blocks)

    def test_no_missing_text_layer_warning(self, native_pdf: Path) -> None:
        """OCR must NOT be triggered on a PDF with a proper text layer (L13)."""
        doc = parse_file(native_pdf)
        assert not doc.warnings, f"Unexpected warnings: {doc.warnings}"

    def test_source_file_is_filename_only(self, native_pdf: Path) -> None:
        """source_file must be just the filename — never the full path (S4, stability)."""
        doc = parse_file(native_pdf)
        assert doc.source_file == native_pdf.name
        assert "/" not in doc.source_file
        assert "\\" not in doc.source_file


class TestParseFigurePDF:
    def test_has_figure_block(self, figure_pdf: Path) -> None:
        doc = parse_file(figure_pdf)
        figure_blocks = [b for b in doc.blocks if b.block_type == "figure"]
        assert len(figure_blocks) >= 1, "Expected at least one figure block"


class TestParseMalformedPDF:
    def test_parse_file_raises(self, malformed_pdf: Path) -> None:
        with pytest.raises(ValueError):
            parse_file(malformed_pdf)

    def test_parse_directory_isolates_failure(
        self, malformed_pdf: Path, native_pdf: Path, tmp_path: Path
    ) -> None:
        """One bad file must not stop the batch (R3)."""
        source_dir = tmp_path / "mixed"
        source_dir.mkdir()
        shutil.copy(malformed_pdf, source_dir / "bad.pdf")
        shutil.copy(native_pdf, source_dir / "good.pdf")

        docs = parse_directory(source_dir)
        assert len(docs) == 1
        assert docs[0].source_file == "good.pdf"


# ---------------------------------------------------------------------------
# Parse — PPTX
# ---------------------------------------------------------------------------

class TestParsePPTX:
    def test_two_slides_parsed(self, minimal_pptx: Path) -> None:
        doc = parse_file(minimal_pptx)
        assert doc.page_count == 2

    def test_title_marked_as_slide_heading(self, minimal_pptx: Path) -> None:
        doc = parse_file(minimal_pptx)
        heading_blocks = [b for b in doc.blocks if b.is_explicit_heading]
        assert len(heading_blocks) == 2

    def test_body_not_slide_heading(self, minimal_pptx: Path) -> None:
        doc = parse_file(minimal_pptx)
        body_blocks = [
            b for b in doc.blocks
            if not b.is_explicit_heading and b.block_type == "text"
        ]
        assert len(body_blocks) >= 1


# ---------------------------------------------------------------------------
# Parse — legacy .ppt
#
# `.ppt` is scanned but cannot be parsed. It stays in the scanned set so a legacy
# deck is never silently dropped from the corpus, and parse_file rejects it by name.
# ---------------------------------------------------------------------------

class TestLegacyPPT:
    def test_parse_file_raises(self, legacy_ppt: Path) -> None:
        with pytest.raises(ValueError):
            parse_file(legacy_ppt)

    def test_error_names_the_format_and_the_remedy(self, legacy_ppt: Path) -> None:
        """
        A generic parse failure reads as "your file is broken". `.ppt` is not broken,
        it is the wrong container — a binary format rather than OOXML — and the
        message has to say so and say what to do about it.
        """
        with pytest.raises(ValueError) as excinfo:
            parse_file(legacy_ppt)
        message = str(excinfo.value)
        assert "old_deck.ppt" in message
        assert "not supported" in message.lower()
        assert "not OOXML" in message
        assert "convert" in message.lower() and ".pptx" in message

    def test_ppt_is_scanned_and_reported_not_silently_ignored(
        self, legacy_ppt: Path, native_pdf: Path, tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        The file must reach the parser and be reported, and the batch must survive it
        (R3). Dropping `.ppt` from the scanned set would make an unreadable deck
        vanish from the corpus without a word — worse than an error.
        """
        source_dir = tmp_path / "mixed"
        source_dir.mkdir()
        shutil.copy(legacy_ppt, source_dir / "old_deck.ppt")
        shutil.copy(native_pdf, source_dir / "good.pdf")

        with caplog.at_level(logging.ERROR, logger="coursegen.ingest.parse"):
            docs = parse_directory(source_dir)

        assert [d.source_file for d in docs] == ["good.pdf"], (
            "the .ppt was either skipped silently or took the batch down with it"
        )
        messages = [r.getMessage() for r in caplog.records]
        assert any("old_deck.ppt" in m for m in messages), (
            f"the .ppt was never reported: {messages}"
        )
        assert any(".pptx" in m for m in messages), (
            f"the logged failure does not carry the remedy: {messages}"
        )


# ---------------------------------------------------------------------------
# Parse — DOCX
# ---------------------------------------------------------------------------

class TestParseDOCX:
    def test_source_type_is_docx(self, structured_docx: Path) -> None:
        assert parse_file(structured_docx).source_type == "docx"

    def test_headings_detected_by_paragraph_style(self, structured_docx: Path) -> None:
        """
        EXACT, not inferred: `paragraph.style.name` starts with
        config.DOCX_HEADING_STYLE_PREFIX. Every heading and only the headings.
        """
        doc = parse_file(structured_docx)
        headings = [b.text for b in doc.blocks if b.is_explicit_heading]
        assert headings == [
            "Chapter 1: Sorting Algorithms",
            "1.1 QuickSort",
            "1.2 Architecture Diagram",
        ]

    def test_body_paragraphs_are_not_headings(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        body = [
            b for b in doc.blocks
            if b.block_type == "text" and not b.is_explicit_heading
        ]
        assert body, "no body blocks parsed"
        assert any("Sorting arranges elements" in b.text for b in body)
        assert not any(b.text.startswith("Chapter 1:") for b in body), (
            "a styled heading was also emitted as a body block"
        )

    def test_heading_prefix_comes_from_config(self, structured_docx: Path) -> None:
        """The prefix is a config constant, not a literal typed into parse.py."""
        assert config.DOCX_HEADING_STYLE_PREFIX == "Heading"
        doc = parse_file(structured_docx)
        assert any(b.is_explicit_heading for b in doc.blocks)

    def test_table_cell_text_extracted(self, structured_docx: Path) -> None:
        """
        A has_table node with empty text can be allocated a question it has no
        content to ground — the same defect the PPTX path shipped with. Cell text
        must reach the block.
        """
        doc = parse_file(structured_docx)
        table_blocks = [b for b in doc.blocks if b.has_table]
        assert table_blocks, "no has_table block emitted for a w:tbl element"
        text = table_blocks[0].text
        for expected in ("Algorithm", "Complexity", "QuickSort", "O(n log n)"):
            assert expected in text, f"{expected!r} missing from table text: {text!r}"

    def test_table_text_preserves_cell_boundaries(self, structured_docx: Path) -> None:
        """Row associations survive: 'QuickSort | O(n log n)', not run together."""
        doc = parse_file(structured_docx)
        text = next(b.text for b in doc.blocks if b.has_table)
        assert " | " in text, f"cell boundaries flattened away: {text!r}"

    def test_merged_cells_are_not_duplicated(self, tmp_path: Path) -> None:
        """
        python-docx behaves the OPPOSITE way to python-pptx on merges: it resolves
        every spanned position to the same `w:tc` and REPEATS the text there, where
        python-pptx returns "". A naive rows x columns walk prints the merged cell
        once per spanned position.
        """
        from docx import Document

        document = Document()
        table = document.add_table(rows=2, cols=3)
        table.cell(0, 0).text = "Algorithm"
        table.cell(0, 1).text = "Complexity"
        table.cell(1, 0).text = "QuickSort"
        table.cell(1, 1).text = "O(n log n)"
        table.cell(0, 1).merge(table.cell(0, 2))

        path = tmp_path / "merged.docx"
        document.save(str(path))

        doc = parse_file(path)
        text = next(b.text for b in doc.blocks if b.has_table)
        assert "Algorithm" in text and "QuickSort" in text
        assert text.count("Complexity") == 1, (
            f"merged cell text repeated once per spanned position: {text!r}"
        )

    def test_monospace_run_flagged(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        assert any(b.has_code for b in doc.blocks), (
            "run.font.name='Consolas' was not flagged has_code"
        )

    def test_math_unicode_flagged(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        assert any(b.has_equation for b in doc.blocks), (
            "Unicode mathematics in a paragraph was not flagged has_equation"
        )

    def test_inline_image_becomes_a_figure_block(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        assert any(b.block_type == "figure" for b in doc.blocks), (
            "document.inline_shapes produced no figure block"
        )

    def test_prose_paragraph_trips_no_flags(self, structured_docx: Path) -> None:
        """False positives matter as much as false negatives."""
        doc = parse_file(structured_docx)
        prose = next(b for b in doc.blocks if "Sorting arranges elements" in b.text)
        assert not (prose.has_table or prose.has_code or prose.has_equation)

    def test_no_warnings_on_a_well_formed_document(
        self, structured_docx: Path
    ) -> None:
        """
        In particular the inline-shape cross-check must balance: every shape reported
        by python-docx was placed as a figure block.
        """
        doc = parse_file(structured_docx)
        assert not doc.warnings, f"unexpected warnings: {doc.warnings}"

    def test_page_is_a_one_based_block_ordinal(self, structured_docx: Path) -> None:
        """
        A .docx has NO pages — pagination does not exist until Word renders the
        document. `page` is therefore the 1-based ordinal of the block within the
        document: a disclosed limitation recorded in pipeline.md, not a page estimate.
        No page-counting heuristic is applied, deliberately.
        """
        doc = parse_file(structured_docx)
        assert [b.page for b in doc.blocks] == list(range(1, len(doc.blocks) + 1))
        assert doc.page_count == len(doc.blocks)


# ---------------------------------------------------------------------------
# Structure — DOCX takes the explicit-heading path
# ---------------------------------------------------------------------------

class TestDOCXStructure:
    def test_sections_open_at_headings(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        paths = [s.path for s in extract_sections(doc)]
        assert ["Chapter 1: Sorting Algorithms"] in paths
        assert ["1.1 QuickSort"] in paths
        assert ["1.2 Architecture Diagram"] in paths

    def test_heading_blocks_are_not_also_content(self, structured_docx: Path) -> None:
        doc = parse_file(structured_docx)
        for section in extract_sections(doc):
            assert not any(b.is_explicit_heading for b in section.blocks), (
                f"heading block duplicated into the content of {section.path!r}"
            )

    def test_font_size_path_is_not_used_for_docx(self, structured_docx: Path) -> None:
        """
        DOCX blocks carry no span font sizes at all, so the PDF relative-threshold
        path would find no headings and collapse the whole document into one
        fallback section. The explicit-heading path must be taken instead.
        """
        doc = parse_file(structured_docx)
        assert all(not b.span_font_sizes for b in doc.blocks)
        assert len(extract_sections(doc)) >= 3

    def test_table_and_figure_reach_their_own_sections(
        self, structured_docx: Path
    ) -> None:
        sections = extract_sections(parse_file(structured_docx))
        flags = {tuple(s.path): _derive_flags(s) for s in sections}
        assert flags[("1.1 QuickSort",)].has_table
        assert flags[("1.1 QuickSort",)].has_code
        assert flags[("1.1 QuickSort",)].has_equation
        assert flags[("1.2 Architecture Diagram",)].has_figure


# ---------------------------------------------------------------------------
# Structure — heading detection regression (gate test)
# ---------------------------------------------------------------------------

class TestHeadingDetectionSlide:
    def test_slide_heading_fraction_below_threshold(self, slide_pdf: Path) -> None:
        """
        REGRESSION GATE: an absolute font-size threshold would flag every 24 pt
        body block as a heading, giving fraction ≈ 1.0.  The relative threshold
        must keep it below HEADING_MAX_BLOCK_PCT (0.30).
        """
        doc = parse_file(slide_pdf)
        frac = heading_fraction(doc)
        assert frac < config.HEADING_MAX_BLOCK_PCT, (
            f"heading_fraction={frac:.2f} ≥ {config.HEADING_MAX_BLOCK_PCT} — "
            "absolute threshold bug detected"
        )

    def test_slide_sections_created(self, slide_pdf: Path) -> None:
        """One section per slide, not one section per block."""
        doc = parse_file(slide_pdf)
        sections = extract_sections(doc)
        # 5 slides × 1 heading each → 5 sections
        assert len(sections) == 5

    def test_native_pdf_headings_detected(self, native_pdf: Path) -> None:
        """Two chapter headings in the native PDF must produce ≥ 2 sections."""
        doc = parse_file(native_pdf)
        sections = extract_sections(doc)
        assert len(sections) >= 2


# ---------------------------------------------------------------------------
# Chunk — determinism and ID stability
# ---------------------------------------------------------------------------

class TestChunkDeterminism:
    def test_same_section_same_ids(self, native_pdf: Path) -> None:
        doc = parse_file(native_pdf)
        sections = extract_sections(doc)
        assert sections
        chunks1 = chunk_section(sections[0])
        chunks2 = chunk_section(sections[0])
        ids1 = [c.chunk_id for c in chunks1]
        ids2 = [c.chunk_id for c in chunks2]
        assert ids1 == ids2

    def test_chunk_id_is_uuid_format(self, native_pdf: Path) -> None:
        import re
        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        doc = parse_file(native_pdf)
        sections = extract_sections(doc)
        for section in sections:
            for chunk in chunk_section(section):
                assert uuid_re.match(chunk.chunk_id), f"Not UUID: {chunk.chunk_id}"

    def test_heading_path_prepended(self, native_pdf: Path) -> None:
        doc = parse_file(native_pdf)
        sections = extract_sections(doc)
        for section in sections:
            if section.path:
                for chunk in chunk_section(section):
                    assert section.path[-1] in chunk.text, (
                        "Heading path must be prepended to chunk text"
                    )

    def test_different_source_file_different_id(self) -> None:
        id1 = _chunk_id("same text", "file_a.pdf", 0)
        id2 = _chunk_id("same text", "file_b.pdf", 0)
        assert id1 != id2


# ---------------------------------------------------------------------------
# Chunk — page precision
#
# Every chunk of a section used to be stamped with `section.page_start`, so a chunk
# drawn from page 7 of a section spanning pages 5-9 was cited as page 5. Chunk pages
# now come from the blocks that actually produced them.
# ---------------------------------------------------------------------------

def _paged_block(page: int, source_file: str = "multi.pdf", words: int = 260) -> TextBlock:
    """A text block whose every word names the page it came from."""
    return TextBlock(
        block_type="text",
        text=" ".join(f"p{page:02d}" for _ in range(words)),
        page=page,
        max_font_size=11.0,
        span_font_sizes=[11.0],
        source_file=source_file,
    )


class TestChunkPagePrecision:
    @staticmethod
    def _multi_page_section() -> LeafSection:
        """One block per page, pages 5-9, long enough to split into several chunks."""
        blocks = [_paged_block(p) for p in range(5, 10)]
        return LeafSection(
            path=["Chapter 1"],
            blocks=blocks,
            page_start=5,
            page_end=9,
            source_file="multi.pdf",
        )

    def test_section_fixture_actually_splits(self) -> None:
        """Guard on the fixture itself: a single chunk would prove nothing."""
        assert len(chunk_section(self._multi_page_section())) > 1

    def test_chunk_pages_are_not_all_page_start(self) -> None:
        chunks = chunk_section(self._multi_page_section())
        pages = [c.page for c in chunks]
        assert pages != [5] * len(chunks), (
            "every chunk is still stamped with section.page_start"
        )
        assert len(set(pages)) > 1, f"all chunks share one page: {pages}"

    def test_every_chunk_page_is_a_page_the_chunk_draws_on(self) -> None:
        """A citation must point at a page whose text is actually in the chunk."""
        for chunk in chunk_section(self._multi_page_section()):
            assert f"p{chunk.page:02d}" in chunk.raw_text, (
                f"chunk cites page {chunk.page} but carries no text from it: "
                f"{chunk.raw_text[:60]!r}"
            )

    def test_page_is_the_first_contributing_block(self) -> None:
        """The documented tie-break, checked against the chunk's own content."""
        for chunk in chunk_section(self._multi_page_section()):
            present = sorted({int(m) for m in re.findall(r"p(\d\d)", chunk.raw_text)})
            assert chunk.page == present[0], (
                f"chunk page {chunk.page} is not the first page it draws on "
                f"({present})"
            )

    def test_pages_are_non_decreasing_and_within_the_section(self) -> None:
        pages = [c.page for c in chunk_section(self._multi_page_section())]
        assert pages == sorted(pages), f"chunk pages went backwards: {pages}"
        assert set(pages) <= {5, 6, 7, 8, 9}

    def test_first_contributing_block_wins_when_a_chunk_spans_two(self) -> None:
        """
        A section short enough to be one chunk, drawn from two blocks on different
        pages. page_start is set to an impossible value so the assertion cannot pass
        by accident.
        """
        blocks = [
            TextBlock("text", "alpha beta gamma", 3, 11.0, [11.0], "two.pdf"),
            TextBlock("text", "delta epsilon zeta", 4, 11.0, [11.0], "two.pdf"),
        ]
        section = LeafSection(
            path=["S"], blocks=blocks, page_start=99, page_end=99,
            source_file="two.pdf",
        )
        chunks = chunk_section(section)
        assert len(chunks) == 1
        assert chunks[0].page == 3, (
            "a chunk spanning two blocks must take the FIRST block's page"
        )

    def test_figure_only_section_uses_its_figure_block_page(self) -> None:
        """The empty-text chunk is produced by the figure block, so it cites its page."""
        blocks = [TextBlock("figure", "", 12, 0.0, [], "fig.pdf")]
        section = LeafSection(
            path=["Fig"], blocks=blocks, page_start=0, page_end=12,
            source_file="fig.pdf",
        )
        chunks = chunk_section(section)
        assert len(chunks) == 1
        assert chunks[0].page == 12

    def test_precise_pages_are_still_deterministic(self) -> None:
        """
        Chunk IDs changed by design (page is hashed into `_chunk_id`), but two runs
        over the same input must still agree — that is what makes upsert a no-op.
        """
        section = self._multi_page_section()
        first = [(c.chunk_id, c.page) for c in chunk_section(section)]
        second = [(c.chunk_id, c.page) for c in chunk_section(section)]
        assert first == second

    def test_real_pdf_chunks_are_not_all_page_zero(self, native_pdf: Path) -> None:
        """
        End-to-end regression over a real two-page PDF. `_extract_pdf_sections` hands
        every section `page_start=0`, so before this change every chunk of every PDF
        was cited as page 0 regardless of where its text came from.
        """
        doc = parse_file(native_pdf)
        pages = {
            c.page for s in extract_sections(doc) for c in chunk_section(s)
        }
        assert pages != {0}, (
            "every chunk of a two-page PDF is on page 0 — section page_start is "
            "still being stamped onto chunks"
        )


# ---------------------------------------------------------------------------
# PDF section page_start — must reflect where the section's content actually is
# ---------------------------------------------------------------------------

class TestPDFSectionPageStart:
    """
    `flush()` used to read a `page_start` variable that was initialised to 0 and
    never reassigned, while the maintained `current_page_start` went unread. Every
    PDF leaf section therefore reported page_start=0 and every PDF node carried
    page_span=(0, page_end). The value was deterministic, so the idempotency gate
    passed on it happily — it was simply wrong, on every node, in a field that
    feeds citations.
    """

    def test_sections_report_the_page_their_content_starts_on(
        self, tmp_path: Path
    ) -> None:
        import fitz

        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 100), f"Chapter {i + 1}: Topic", fontsize=24)
            for y in range(4):
                page.insert_text((72, 160 + y * 40), f"Body line {y} on page {i}.", fontsize=11)
        path = tmp_path / "multi.pdf"
        doc.save(str(path))
        doc.close()

        sections = extract_sections(parse_file(path))
        assert len(sections) == 3

        starts = [s.page_start for s in sections]
        assert starts == [0, 1, 2], (
            f"expected each chapter to start on its own page, got {starts}"
        )
        assert len(set(starts)) > 1, "every section claims the same page_start"

    def test_page_start_never_exceeds_page_end(self, native_pdf: Path) -> None:
        for section in extract_sections(parse_file(native_pdf)):
            assert section.page_start <= section.page_end, (
                f"{section.path}: page_start={section.page_start} > "
                f"page_end={section.page_end}"
            )


# ---------------------------------------------------------------------------
# node_id — stability across re-ingest
# ---------------------------------------------------------------------------

class TestNodeID:
    def test_deterministic(self) -> None:
        id1 = _compute_node_id("lecture.pdf", ["Ch 1", "1.1 Intro"])
        id2 = _compute_node_id("lecture.pdf", ["Ch 1", "1.1 Intro"])
        assert id1 == id2

    def test_different_source_file_different_id(self) -> None:
        """Two documents with the same heading path → distinct node_ids."""
        id1 = _compute_node_id("doc_a.pdf", ["Introduction"])
        id2 = _compute_node_id("doc_b.pdf", ["Introduction"])
        assert id1 != id2, (
            "Hashing heading path alone collides across documents — "
            "source_file must be part of the hash"
        )

    def test_node_id_is_sha1_hex(self) -> None:
        nid = _compute_node_id("lecture.pdf", ["Ch 1"])
        assert len(nid) == 40
        assert all(c in "0123456789abcdef" for c in nid)


# ---------------------------------------------------------------------------
# instructional_mass — formula
# ---------------------------------------------------------------------------

class TestInstructionalMass:
    def _make_node(
        self, node_id: str, source_file: str, token_count: int, key_terms: list[str]
    ) -> _RawNode:
        return _RawNode(
            node_id=node_id,
            path=[node_id],
            source_file=source_file,
            page_span=(0, 1),
            token_count=token_count,
            chunk_ids=[],
            key_terms=key_terms,
            flags=NodeFlags(),
        )

    def test_masses_sum_to_one(self) -> None:
        nodes = [
            self._make_node("n1", "a.pdf", 200, ["algorithm"]),
            self._make_node("n2", "a.pdf", 300, ["data structure"]),
            self._make_node("n3", "a.pdf", 100, ["complexity"]),
        ]
        masses = _compute_instructional_mass(nodes)
        assert abs(sum(masses) - 1.0) < 0.001

    def test_repeated_term_outranks_equal_token_unrepeated(self) -> None:
        """
        Node A (doc_a.pdf, 100 tokens): key_term also appears in doc_b.pdf
          → df_other = 1 → raw_A = 100 * (1 + ln(2)) ≈ 169.3
        Node B (doc_b.pdf, 100 tokens): key_term only in doc_b.pdf
          → df_other = 0 → raw_B = 100 * (1 + ln(1)) = 100
        mass_A > mass_B
        """
        nodes = [
            self._make_node("n_a", "doc_a.pdf", 100, ["shared_term"]),
            self._make_node("n_b", "doc_b.pdf", 100, ["shared_term"]),  # same term
        ]
        masses = _compute_instructional_mass(nodes)
        # Both see the other document → df_other = 1 for each → equal masses
        assert abs(masses[0] - masses[1]) < 0.001  # symmetric

        # Now: node C has a unique term (df_other=0), same token count as A
        nodes2 = [
            self._make_node("n_a", "doc_a.pdf", 100, ["shared_term"]),
            self._make_node("n_b", "doc_b.pdf", 100, ["shared_term"]),
            self._make_node("n_c", "doc_c.pdf", 100, ["unique_term_xyz"]),
        ]
        masses2 = _compute_instructional_mass(nodes2)
        # n_a and n_b see each other: df_other=1; n_c sees nothing: df_other=0
        # raw_a = raw_b > raw_c → mass_a > mass_c and mass_b > mass_c
        assert masses2[0] > masses2[2], "Term-repeated node must outrank equal-token unrepeated"
        assert masses2[1] > masses2[2]

    def test_single_doc_collapses_to_token_count(self) -> None:
        """With one source file, every df_other = 0, mass = normalised token_count."""
        nodes = [
            self._make_node("n1", "only.pdf", 200, ["term_a"]),
            self._make_node("n2", "only.pdf", 400, ["term_b"]),
        ]
        masses = _compute_instructional_mass(nodes)
        # Expected: 200/600 = 0.333..., 400/600 = 0.666...
        assert abs(masses[0] - 200 / 600) < 0.001
        assert abs(masses[1] - 400 / 600) < 0.001


# ---------------------------------------------------------------------------
# Full ingest — idempotency gate (embed + index mocked)
# ---------------------------------------------------------------------------

def _fake_embed_result(chunks: list, model: object) -> object:
    """Deterministic fake embeddings for tests (no model download required)."""
    from coursegen.ingest.embed import EmbedResult
    n = len(chunks)
    return EmbedResult(
        dense=np.zeros((n, config.DENSE_VECTOR_SIZE), dtype=np.float32),
        sparse=[{0: 1.0} for _ in range(n)],
    )


class TestIngestIdempotency:
    def test_identical_node_ids_on_double_ingest(
        self, native_pdf: Path, tmp_path: Path
    ) -> None:
        """Ingest the same file twice → identical node IDs (C1)."""
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(native_pdf, source_dir / native_pdf.name)
        data_dir = tmp_path / "data"

        with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
             patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed_result), \
             patch("coursegen.ingest.coursemap.get_client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_client.return_value.close = MagicMock()
            mock_client.return_value.get_collections.return_value.collections = []
            mock_client.return_value.create_collection = MagicMock()
            mock_client.return_value.upsert = MagicMock()

            nodes1 = ingest(source_dir, data_dir)
            nodes2 = ingest(source_dir, data_dir)

        ids1 = sorted(n.node_id for n in nodes1)
        ids2 = sorted(n.node_id for n in nodes2)
        assert ids1 == ids2, "Node IDs differ between two ingests of identical input"

    def test_identical_course_map_hash_on_double_ingest(
        self, native_pdf: Path, tmp_path: Path
    ) -> None:
        """Ingest the same file twice → byte-identical course_map.json (C1)."""
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(native_pdf, source_dir / native_pdf.name)
        data_dir = tmp_path / "data"

        with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
             patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed_result), \
             patch("coursegen.ingest.coursemap.get_client") as mock_client:
            mock_client.return_value.close = MagicMock()
            mock_client.return_value.get_collections.return_value.collections = []
            mock_client.return_value.create_collection = MagicMock()
            mock_client.return_value.upsert = MagicMock()

            ingest(source_dir, data_dir)
            h1 = hashlib.md5((data_dir / "course_map.json").read_bytes()).hexdigest()
            ingest(source_dir, data_dir)
            h2 = hashlib.md5((data_dir / "course_map.json").read_bytes()).hexdigest()

        assert h1 == h2, "course_map.json hash differs between two ingests of identical input"


# ---------------------------------------------------------------------------
# Figure flag
# ---------------------------------------------------------------------------

class TestFigureFlag:
    def test_figure_flag_set_on_pdf_with_image(
        self, figure_pdf: Path, tmp_path: Path
    ) -> None:
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(figure_pdf, source_dir / figure_pdf.name)
        data_dir = tmp_path / "data"

        with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
             patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed_result), \
             patch("coursegen.ingest.coursemap.get_client") as mock_client:
            mock_client.return_value.close = MagicMock()
            mock_client.return_value.get_collections.return_value.collections = []
            mock_client.return_value.create_collection = MagicMock()
            mock_client.return_value.upsert = MagicMock()

            nodes = ingest(source_dir, data_dir)

        assert any(n.flags.has_figure for n in nodes), (
            "No node has has_figure=True for a PDF containing an embedded image"
        )


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------

class TestCourseMapPersistence:
    def test_save_load_roundtrip(self, native_pdf: Path, tmp_path: Path) -> None:
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(native_pdf, source_dir / native_pdf.name)
        data_dir = tmp_path / "data"

        with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
             patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed_result), \
             patch("coursegen.ingest.coursemap.get_client") as mock_client:
            mock_client.return_value.close = MagicMock()
            mock_client.return_value.get_collections.return_value.collections = []
            mock_client.return_value.create_collection = MagicMock()
            mock_client.return_value.upsert = MagicMock()

            original_nodes = ingest(source_dir, data_dir)

        loaded_nodes = load_course_map(data_dir / "course_map.json")
        assert len(loaded_nodes) == len(original_nodes)
        orig_ids = sorted(n.node_id for n in original_nodes)
        load_ids = sorted(n.node_id for n in loaded_nodes)
        assert orig_ids == load_ids


# ---------------------------------------------------------------------------
# Content flag heuristics — unit level
#
# `has_equation`'s font-name prong cannot be driven through a synthetic PDF:
# PyMuPDF's base-14 fonts substitute U+00B7 for every mathematical glyph and no
# CMMI / Cambria Math face may be embedded (no new dependencies). The unit is
# therefore the seam that gets tested; the Unicode prong is additionally covered
# end-to-end through PPTX below.
# ---------------------------------------------------------------------------

class TestCodeFontHeuristic:
    def test_courier_detected(self) -> None:
        assert is_code_font(["Courier"])

    def test_common_monospace_families_detected(self) -> None:
        for name in [
            "Consolas", "DejaVuSansMono", "Menlo-Regular",
            "Inconsolata", "CascadiaCode", "ABCDEF+LiberationMono",
        ]:
            assert is_code_font([name]), name

    def test_case_insensitive(self) -> None:
        assert is_code_font(["COURIERNEW-BOLD"])

    def test_proportional_fonts_not_detected(self) -> None:
        for name in ["Helvetica", "TimesNewRomanPSMT", "Calibri", "Arial-Black"]:
            assert not is_code_font([name]), name

    def test_empty_font_list_is_false(self) -> None:
        assert not is_code_font([])

    def test_one_monospace_span_flags_the_block(self) -> None:
        assert is_code_font(["Helvetica", "Courier", "Helvetica"])


class TestEquationHeuristic:
    def test_tex_math_fonts_detected(self) -> None:
        for name in ["CMMI10", "CMSY7", "CMEX10", "ABCDEF+CMMI12"]:
            assert is_math_font([name]), name

    def test_word_equation_font_detected(self) -> None:
        assert is_math_font(["Cambria Math"])
        assert is_math_font(["CambriaMath-Regular"])

    def test_symbol_font_deliberately_not_math(self) -> None:
        """
        Word sets its default list bullet (U+F0B7) in the Symbol face, so treating
        "Symbol" as a mathematics font would flag every bulleted deck as containing
        equations. The exclusion is a decision, not an oversight — see config.py.
        """
        assert not is_math_font(["Symbol"])
        assert not is_equation_content(["Symbol"], "First bullet point")

    def test_body_fonts_not_math(self) -> None:
        for name in ["Helvetica", "Calibri", "TimesNewRomanPSMT"]:
            assert not is_math_font([name]), name

    def test_math_unicode_counted(self) -> None:
        assert count_math_chars("∀x ∈ S: ∑ y ≤ ∞") == 5   # ∀ ∈ ∑ ≤ ∞

    def test_prose_counts_zero(self) -> None:
        assert count_math_chars("Plain prose with no mathematics at all.") == 0

    def test_excluded_ranges_count_zero(self) -> None:
        """Greek, arrows, letterlike and ± × ÷ are deliberately NOT counted."""
        assert count_math_chars("alpha α beta β Input → Output ™ 1920×1080 ±5%") == 0

    def test_single_inline_symbol_is_not_an_equation(self) -> None:
        """One comparison operator in prose must not flag a whole section."""
        text = "The algorithm terminates when the error ≤ the tolerance."
        assert count_math_chars(text) == 1
        assert not is_equation_content(["Helvetica"], text)

    def test_dense_math_text_is_an_equation(self) -> None:
        text = "∀x ∈ D, ∂J/∂θ ≠ 0 and ∑ w ≤ ∞"
        assert count_math_chars(text) >= config.EQUATION_MIN_MATH_CHARS
        assert is_equation_content(["Helvetica"], text)

    def test_math_font_wins_without_math_unicode(self) -> None:
        """A CMMI span carries no U+2200-range codepoints, but is still an equation."""
        assert is_equation_content(["CMMI10"], "x y z")


# ---------------------------------------------------------------------------
# Content flags — end to end through the parsers
# ---------------------------------------------------------------------------

class TestPDFContentFlags:
    def test_table_blocks_marked(self, table_code_pdf: Path) -> None:
        doc = parse_file(table_code_pdf)
        table_blocks = [b for b in doc.blocks if b.has_table]
        assert table_blocks, "page.find_tables() found no table in a ruled-grid PDF"
        assert all(b.page == 0 for b in table_blocks), (
            "has_table leaked onto a page with no table"
        )

    def test_code_blocks_marked(self, table_code_pdf: Path) -> None:
        doc = parse_file(table_code_pdf)
        code_blocks = [b for b in doc.blocks if b.has_code]
        assert code_blocks, "Courier listing was not flagged has_code"
        assert all(b.page == 1 for b in code_blocks)

    def test_flags_reach_the_section(self, table_code_pdf: Path) -> None:
        doc = parse_file(table_code_pdf)
        sections = extract_sections(doc)
        flags = [_derive_flags(s) for s in sections]
        assert any(f.has_table for f in flags), "has_table did not reach any section"
        assert any(f.has_code for f in flags), "has_code did not reach any section"

    def test_prose_pdf_sets_no_content_flags(self, native_pdf: Path) -> None:
        """A plain prose PDF must not trip any heuristic — false positives matter."""
        doc = parse_file(native_pdf)
        assert not any(b.has_table for b in doc.blocks)
        assert not any(b.has_code for b in doc.blocks)
        assert not any(b.has_equation for b in doc.blocks)


class TestPPTXContentFlags:
    def test_table_shape_flagged(self, math_pptx: Path) -> None:
        doc = parse_file(math_pptx)
        assert any(b.has_table for b in doc.blocks), (
            "shape.has_table did not produce a has_table block"
        )

    def test_table_cell_text_extracted(self, math_pptx: Path) -> None:
        """
        A has_table node with empty text can be allocated a question it has no
        content to ground — the flag says "there is a table here" while the span
        hands the model nothing. Cell text must reach the block.
        """
        doc = parse_file(math_pptx)
        table_blocks = [b for b in doc.blocks if b.has_table]
        assert table_blocks, "no has_table block emitted"
        text = table_blocks[0].text
        for expected in ("Model", "Accuracy", "Baseline", "0.71"):
            assert expected in text, f"{expected!r} missing from table text: {text!r}"

    def test_table_text_preserves_cell_boundaries(self, math_pptx: Path) -> None:
        """Row associations survive: 'Baseline | 0.71', not 'Baseline 0.71'."""
        doc = parse_file(math_pptx)
        text = next(b.text for b in doc.blocks if b.has_table)
        assert " | " in text, f"cell boundaries flattened away: {text!r}"

    def test_merged_cells_do_not_emit_blank_cells(self, tmp_path: Path) -> None:
        """
        python-pptx puts merged text on the origin cell and returns "" at every
        spanned position, so iterating rows/cells naively emits a stray blank cell
        per merge — surfacing as an empty segment between two separators.
        """
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        table = slide.shapes.add_table(
            2, 3, Inches(1), Inches(1), Inches(6), Inches(1)
        ).table
        table.cell(0, 0).text = "Algorithm"
        table.cell(0, 1).text = "Complexity"
        table.cell(1, 0).text = "QuickSort"
        table.cell(1, 1).text = "O(n log n)"
        table.cell(0, 1).merge(table.cell(0, 2))

        path = tmp_path / "merged.pptx"
        prs.save(str(path))

        doc = parse_file(path)
        text = next(b.text for b in doc.blocks if b.has_table)
        assert "Algorithm" in text and "QuickSort" in text
        assert "|  |" not in text, f"blank cell from a merged position: {text!r}"
        for line in text.splitlines():
            assert not line.strip().endswith("|"), (
                f"dangling separator from a merged position: {line!r}"
            )

    def test_math_unicode_slide_flagged(self, math_pptx: Path) -> None:
        doc = parse_file(math_pptx)
        assert any(b.has_equation for b in doc.blocks), (
            "Unicode mathematics in a slide body was not flagged has_equation"
        )

    def test_monospace_run_flagged(self, math_pptx: Path) -> None:
        doc = parse_file(math_pptx)
        assert any(b.has_code for b in doc.blocks), (
            "run.font.name='Consolas' was not flagged has_code"
        )

    def test_picture_shape_still_detected(self, math_pptx: Path) -> None:
        """
        Regression for the `shape_type == 13` magic number replaced by
        MSO_SHAPE_TYPE.PICTURE: figure blocks must still be emitted.
        """
        doc = parse_file(math_pptx)
        assert any(b.block_type == "figure" for b in doc.blocks)

    def test_titles_do_not_trip_flags(self, minimal_pptx: Path) -> None:
        doc = parse_file(minimal_pptx)
        assert not any(b.has_table or b.has_code or b.has_equation for b in doc.blocks)


# ---------------------------------------------------------------------------
# S3 — per-file parse timeout (PARSE_TIMEOUT_SECONDS)
# ---------------------------------------------------------------------------

# The timeout these tests install must be comfortably longer than a real parse,
# because only the deliberately-blocked file is supposed to trip it. A real
# parse_file on the native fixture measures 50–96 ms (find_tables() dominates), so
# the original 0.2 s left ~2x headroom — under full-suite CPU contention the *good*
# file timed out as well, parse_directory returned nothing, and the assertion failed
# intermittently. 1.0 s is ~10x the observed worst case. The blocked file waits on an
# Event, so it trips the watchdog at any threshold; raising this cannot mask a real
# failure, it only stops the healthy file from racing the clock.
_TIMEOUT_TEST_SECONDS = 1.0


class TestParseTimeout:
    def test_slow_file_is_skipped_and_batch_completes(
        self, native_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A file that overruns PARSE_TIMEOUT_SECONDS is logged as failed and skipped;
        the rest of the batch still parses (R3 batch isolation, S3 timeout).

        The stand-in slow parse blocks on an Event rather than sleeping, so the
        abandoned worker thread is released the moment the assertion is done — a
        Python thread cannot be killed, and a bare sleep would linger for the rest of
        the session.
        """
        from coursegen.ingest import parse as parse_mod

        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(native_pdf, source_dir / "good.pdf")
        shutil.copy(native_pdf, source_dir / "slow.pdf")

        monkeypatch.setattr(config, "PARSE_TIMEOUT_SECONDS", _TIMEOUT_TEST_SECONDS)
        real_parse_file = parse_mod.parse_file
        release = threading.Event()

        def slow_for_one_file(path: Path):
            if path.name == "slow.pdf":
                release.wait(timeout=30)
                raise ValueError("released after the batch moved on")
            return real_parse_file(path)

        monkeypatch.setattr(parse_mod, "parse_file", slow_for_one_file)
        try:
            docs = parse_directory(source_dir)
        finally:
            release.set()

        assert [d.source_file for d in docs] == ["good.pdf"], (
            "the timed-out file was not skipped, or it took the batch down with it"
        )

    def test_timeout_error_names_the_config_constant(
        self, native_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure must be legible in the log, not an anonymous timeout."""
        from coursegen.ingest import parse as parse_mod

        monkeypatch.setattr(config, "PARSE_TIMEOUT_SECONDS", _TIMEOUT_TEST_SECONDS)
        release = threading.Event()

        def never_finishes(path: Path):
            release.wait(timeout=30)
            raise ValueError("released")

        monkeypatch.setattr(parse_mod, "parse_file", never_finishes)
        try:
            with pytest.raises(TimeoutError, match="PARSE_TIMEOUT_SECONDS"):
                parse_mod._parse_file_with_timeout(native_pdf)
        finally:
            release.set()


# ---------------------------------------------------------------------------
# Real-Qdrant helpers — no mocked upsert, no network
# ---------------------------------------------------------------------------

def _ingest_with_real_qdrant(source_dir: Path, data_dir: Path) -> list:
    """
    Run the full ingest with the fake embedder but a REAL local-mode Qdrant.

    Only `load_model` / `embed_chunks` are patched — the model download is the one
    thing that would need the network. Index writes are genuine, which is the whole
    point: a MagicMock upsert cannot demonstrate idempotency.
    """
    with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
         patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed_result):
        return ingest(source_dir, data_dir)


def _point_count(data_dir: Path) -> int:
    """Points currently in the collection. Closes the client — local mode locks."""
    client = get_client(data_dir)
    try:
        return client.count(collection_name=config.QDRANT_COLLECTION_NAME).count
    finally:
        client.close()


# ---------------------------------------------------------------------------
# P1 gate condition 3 — identical POINT COUNT across two ingests
# ---------------------------------------------------------------------------

class TestIngestPointCount:
    """
    The third P1 gate condition. `TestIngestIdempotency` above mocks the Qdrant
    client, so `upsert` is a MagicMock and nothing there exercises the actual claim:
    that re-ingesting UPSERTS BY uuid5 KEY instead of duplicating points (C1, L12).
    """

    def test_point_count_identical_on_double_ingest(
        self, native_pdf: Path, tmp_path: Path
    ) -> None:
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(native_pdf, source_dir / native_pdf.name)
        data_dir = tmp_path / "data"

        nodes1 = _ingest_with_real_qdrant(source_dir, data_dir)
        count1 = _point_count(data_dir)

        nodes2 = _ingest_with_real_qdrant(source_dir, data_dir)
        count2 = _point_count(data_dir)

        chunk_ids = [cid for n in nodes1 for cid in n.chunk_ids]
        assert count1 == len(chunk_ids), (
            f"first ingest wrote {count1} points for {len(chunk_ids)} chunks"
        )
        assert count2 == count1, (
            f"re-ingest duplicated points: {count1} → {count2}. "
            "uuid5 chunk IDs are supposed to make upsert a no-op (C1)."
        )
        assert sorted(cid for n in nodes2 for cid in n.chunk_ids) == sorted(chunk_ids)

    def test_docx_ingests_end_to_end_with_every_flag(
        self, structured_docx: Path, tmp_path: Path
    ) -> None:
        """
        The whole DOCX seam through a real local-mode Qdrant: parse -> structure ->
        chunk -> course map. All four NodeFlags must be reachable from a DOCX, or a
        blueprint's `requires_flags_any` would starve on a DOCX-only corpus.
        """
        source_dir = tmp_path / "docs"
        source_dir.mkdir()
        shutil.copy(structured_docx, source_dir / structured_docx.name)
        data_dir = tmp_path / "data"

        nodes1 = _ingest_with_real_qdrant(source_dir, data_dir)
        count1 = _point_count(data_dir)
        nodes2 = _ingest_with_real_qdrant(source_dir, data_dir)
        count2 = _point_count(data_dir)

        assert nodes1, "ingest produced no nodes from a DOCX"
        counts = {
            flag: sum(1 for n in nodes1 if getattr(n.flags, flag))
            for flag in ("has_figure", "has_table", "has_equation", "has_code")
        }
        for flag, count in counts.items():
            assert count > 0, f"{flag} unreachable from a DOCX: {counts}"

        # Idempotency holds for the new format too.
        assert count2 == count1
        assert sorted(c for n in nodes1 for c in n.chunk_ids) == sorted(
            c for n in nodes2 for c in n.chunk_ids
        )

    def test_multi_document_corpus_point_count_stable(
        self, rich_source_dir: Path, tmp_path: Path
    ) -> None:
        """Same gate over a mixed PDF + PPTX corpus, not a single file."""
        data_dir = tmp_path / "data"

        nodes1 = _ingest_with_real_qdrant(rich_source_dir, data_dir)
        count1 = _point_count(data_dir)
        _ingest_with_real_qdrant(rich_source_dir, data_dir)
        count2 = _point_count(data_dir)

        chunk_ids = [cid for n in nodes1 for cid in n.chunk_ids]
        assert count1 == len(chunk_ids)
        assert count2 == count1


# ---------------------------------------------------------------------------
# Ingest → solver integration (the seam nothing tested)
#
# tests/fixtures/course_map_sample.json gave the P2' solver has_table=5,
# has_figure=7, has_equation=9. Real ingest could never produce a non-zero
# has_table or has_equation, so the solver was validated against a candidate-pool
# shape that did not exist. This runs the solver on a course map real ingest built.
# ---------------------------------------------------------------------------

class TestIngestSolverIntegration:
    @staticmethod
    def _final_default() -> Blueprint:
        path = (
            Path(__file__).parent.parent
            / "coursegen" / "exam" / "blueprints" / "final_default.json"
        )
        return Blueprint.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def test_real_course_map_solves_final_default(
        self, rich_source_dir: Path, tmp_path: Path
    ) -> None:
        blueprint = self._final_default()
        course_map = _ingest_with_real_qdrant(rich_source_dir, tmp_path / "data")
        assert course_map, "ingest produced no nodes from the rich corpus"

        items, report = solve(course_map, blueprint)

        # The flag-filtered section is the one the fixture could not have exercised.
        section_c = next(s for s in blueprint.sections if s.section_id == "C")
        assert section_c.requires_flags_any, "final_default section C lost its flag filter"
        c_items = [i for i in items if i.slot_id.startswith("C-")]
        assert c_items, (
            "Section C is entirely unfilled on a real ingest course map. "
            f"requires_flags_any={section_c.requires_flags_any}; "
            "flag counts = " + repr({
                flag: sum(1 for n in course_map if getattr(n.flags, flag))
                for flag in ("has_figure", "has_table", "has_equation", "has_code")
            })
        )

        # Every item in a flag-filtered section must come from a node that matches.
        by_id = {n.node_id: n for n in course_map}
        for item in c_items:
            node = by_id[item.node_id]
            assert any(
                getattr(node.flags, flag) for flag in section_c.requires_flags_any
            ), f"{item.slot_id} was filled from a node matching no required flag"

        # fill_ratio must be reported — coverage_ratio alone can read 1.00 on an
        # incomplete paper.
        assert report.slots_total == sum(s.count for s in blueprint.sections)
        assert report.slots_filled == len(items)
        assert 0.0 <= report.fill_ratio <= 1.0
        assert report.fill_ratio == pytest.approx(
            report.slots_filled / report.slots_total
        )

    def test_real_ingest_produces_every_detectable_flag(
        self, rich_source_dir: Path, tmp_path: Path
    ) -> None:
        """
        The regression that item 1 of this pass existed to prevent: three of the four
        NodeFlags were hardcoded False, so a blueprint could require a flag no real
        document could ever set.
        """
        course_map = _ingest_with_real_qdrant(rich_source_dir, tmp_path / "data")
        counts = {
            flag: sum(1 for n in course_map if getattr(n.flags, flag))
            for flag in ("has_figure", "has_table", "has_equation", "has_code")
        }
        for flag, count in counts.items():
            assert count > 0, f"{flag} is never True on real ingest output: {counts}"
