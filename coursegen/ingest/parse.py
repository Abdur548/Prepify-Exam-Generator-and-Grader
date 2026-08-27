"""
Document parsing: PDF (PyMuPDF) and PPTX (python-pptx) → TextBlock list.

Zero LLM calls. Zero heading decisions — that is structure.py's job.
This module only extracts blocks and records their font metadata.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from coursegen import config

logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".ppt"}


@dataclass
class TextBlock:
    block_type: Literal["text", "figure"]
    text: str
    page: int                       # 0-indexed within the document
    max_font_size: float            # max span font size; 0.0 for figure blocks
    span_font_sizes: list[float]    # all individual span sizes; empty for figures
    source_file: str                # filename only — not full path (S4, node_id stability)
    is_slide_heading: bool = False  # True for PPTX placeholder types 1 / 3 only


@dataclass
class ParsedDocument:
    source_file: str                # filename only
    source_type: Literal["pdf", "pptx"]
    blocks: list[TextBlock]
    page_count: int
    warnings: list[str]             # pages with no text layer, size warnings, etc.


def parse_file(path: Path) -> ParsedDocument:
    """
    Parse one PDF or PPTX file. Raises ValueError on any hard failure
    so the caller (parse_directory) can isolate it (R3).
    """
    _check_file_size(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix in (".pptx", ".ppt"):
        return _parse_pptx(path)
    raise ValueError(f"Unsupported file type: {suffix!r} ({path.name})")


def parse_directory(source_dir: Path) -> list[ParsedDocument]:
    """
    Parse all PDF/PPTX files in source_dir.
    One bad file logs an error and is skipped; the batch always completes (R3).
    """
    candidates = sorted(
        p for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES
    )
    if not candidates:
        logger.warning("No supported files found in %s", source_dir)

    docs: list[ParsedDocument] = []
    for path in candidates:
        try:
            doc = parse_file(path)
            docs.append(doc)
            logger.info(
                "Parsed %s: %d blocks on %d pages",
                path.name, len(doc.blocks), doc.page_count,
            )
        except Exception as exc:
            logger.error("Failed to parse %s — skipping: %s", path.name, exc)

    return docs


# ---------------------------------------------------------------------------
# PDF via PyMuPDF
# ---------------------------------------------------------------------------

def _parse_pdf(path: Path) -> ParsedDocument:
    import fitz  # PyMuPDF

    source_file = path.name
    blocks: list[TextBlock] = []
    warnings: list[str] = []

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise ValueError(f"PyMuPDF could not open {path.name}: {exc}") from exc

    if doc.page_count > config.MAX_PAGES:
        doc.close()
        raise ValueError(
            f"{path.name}: {doc.page_count} pages exceeds MAX_PAGES={config.MAX_PAGES}"
        )

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_dict = page.get_text("dict")
        page_has_content = False

        for raw_block in page_dict.get("blocks", []):
            if raw_block["type"] == 1:                   # image block
                blocks.append(TextBlock(
                    block_type="figure",
                    text="",
                    page=page_idx,
                    max_font_size=0.0,
                    span_font_sizes=[],
                    source_file=source_file,
                ))
                page_has_content = True

            elif raw_block["type"] == 0:                 # text block
                span_texts: list[str] = []
                span_sizes: list[float] = []
                for line in raw_block.get("lines", []):
                    for span in line.get("spans", []):
                        t = span.get("text", "").strip()
                        if t:
                            span_texts.append(t)
                            size = span.get("size", 0.0)
                            if size > 0:
                                span_sizes.append(size)

                if not span_texts:
                    continue

                text = " ".join(span_texts)
                max_size = max(span_sizes) if span_sizes else 0.0
                blocks.append(TextBlock(
                    block_type="text",
                    text=text,
                    page=page_idx,
                    max_font_size=max_size,
                    span_font_sizes=span_sizes,
                    source_file=source_file,
                ))
                page_has_content = True

        if not page_has_content:
            # R10: log the cut, never silently drop content.
            msg = (
                f"Page {page_idx + 1} of {path.name} has no text or image layer. "
                "OCR is not available in MVP1 — page skipped."
            )
            logger.warning(msg)
            warnings.append(msg)

    page_count = doc.page_count
    doc.close()
    return ParsedDocument(
        source_file=source_file,
        source_type="pdf",
        blocks=blocks,
        page_count=page_count,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# PPTX via python-pptx
# ---------------------------------------------------------------------------

def _parse_pptx(path: Path) -> ParsedDocument:
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER

    _check_pptx_zip_size(path)

    source_file = path.name
    blocks: list[TextBlock] = []
    warnings: list[str] = []

    _HEADING_TYPES = {
        PP_PLACEHOLDER.TITLE,         # 1
        PP_PLACEHOLDER.CENTER_TITLE,  # 3
    }

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ValueError(f"python-pptx could not open {path.name}: {exc}") from exc

    slide_count = len(prs.slides)
    if slide_count > config.MAX_PAGES:
        raise ValueError(
            f"{path.name}: {slide_count} slides exceeds MAX_PAGES={config.MAX_PAGES}"
        )

    for slide_idx, slide in enumerate(prs.slides):
        slide_has_content = False

        for shape in slide.shapes:
            if not shape.has_text_frame:
                if hasattr(shape, "shape_type") and shape.shape_type == 13:  # picture
                    blocks.append(TextBlock(
                        block_type="figure",
                        text="",
                        page=slide_idx,
                        max_font_size=0.0,
                        span_font_sizes=[],
                        source_file=source_file,
                    ))
                    slide_has_content = True
                continue

            parts: list[str] = []
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    t = run.text.strip()
                    if t:
                        parts.append(t)

            if not parts:
                continue

            is_heading = (
                shape.is_placeholder
                and shape.placeholder_format is not None
                and shape.placeholder_format.type in _HEADING_TYPES
            )
            blocks.append(TextBlock(
                block_type="text",
                text=" ".join(parts),
                page=slide_idx,
                max_font_size=0.0,      # not used for PPTX heading detection
                span_font_sizes=[],
                source_file=source_file,
                is_slide_heading=is_heading,
            ))
            slide_has_content = True

        if not slide_has_content:
            warnings.append(f"Slide {slide_idx + 1} of {path.name} has no content.")

    return ParsedDocument(
        source_file=source_file,
        source_type="pptx",
        blocks=blocks,
        page_count=slide_count,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Security checks (S3)
# ---------------------------------------------------------------------------

def _check_file_size(path: Path) -> None:
    size = path.stat().st_size
    if size > config.MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"{path.name}: {size} bytes exceeds MAX_FILE_SIZE_BYTES={config.MAX_FILE_SIZE_BYTES}"
        )


def _check_pptx_zip_size(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            total = sum(info.file_size for info in zf.infolist())
        if total > config.MAX_DECOMPRESSED_SIZE_BYTES:
            raise ValueError(
                f"{path.name}: decompressed {total} bytes exceeds "
                f"MAX_DECOMPRESSED_SIZE_BYTES={config.MAX_DECOMPRESSED_SIZE_BYTES}"
            )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{path.name} is not a valid ZIP/PPTX: {exc}") from exc
