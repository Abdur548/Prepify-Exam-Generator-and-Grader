"""
P1 gate tests — ingest pipeline.

Gates (all must pass before P2):
  1. Ingest twice → identical node IDs and identical course_map.json hash
  2. Slide-exported PDF: heading_fraction < HEADING_MAX_BLOCK_PCT (0.30)
  3. Malformed PDF fails that file only; parse_directory returns remaining docs
  4. Figure flag set on a document containing an embedded image
  5. OCR not triggered on a native-text PDF (no warning about missing text layer)
  6. Two documents with the same heading produce distinct node_ids
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from coursegen import config
from coursegen.ingest.chunk import Chunk, _chunk_id, chunk_section
from coursegen.ingest.coursemap import (
    _compute_node_id,
    _compute_instructional_mass,
    ingest,
    load_course_map,
    save_course_map,
    _RawNode,
)
from coursegen.ingest.parse import parse_directory, parse_file
from coursegen.ingest.structure import extract_sections, heading_fraction
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
        heading_blocks = [b for b in doc.blocks if b.is_slide_heading]
        assert len(heading_blocks) == 2

    def test_body_not_slide_heading(self, minimal_pptx: Path) -> None:
        doc = parse_file(minimal_pptx)
        body_blocks = [b for b in doc.blocks if not b.is_slide_heading and b.block_type == "text"]
        assert len(body_blocks) >= 1


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
