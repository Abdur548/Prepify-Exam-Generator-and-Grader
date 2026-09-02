"""
Heading tree extraction.

Two paths, chosen by source type:

  INFERRED   PDF — relative font-size threshold, never an absolute cutoff
             (PRD §9.1 step 2). Nothing in a PDF declares what a heading is.
  EXPLICIT   PPTX and DOCX — the format says so, and `TextBlock.is_explicit_heading`
             carries that declaration (PPTX placeholder TITLE / CENTER_TITLE, DOCX
             paragraph style "Heading …"). No font size is consulted.

The relative threshold is the key design invariant for the inferred path: it must
remain correct for slide-exported PDFs whose body text sits at 18–28 pt. An absolute
threshold near 14 pt would classify every block in such a document as a heading.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from coursegen import config
from coursegen.ingest.parse import ParsedDocument, TextBlock


@dataclass
class LeafSection:
    path: list[str]          # full heading path, e.g. ["Ch 1", "1.1 Intro"]
    blocks: list[TextBlock]  # content blocks (non-heading text + figures)
    page_start: int
    page_end: int
    source_file: str


def extract_sections(doc: ParsedDocument) -> list[LeafSection]:
    """
    Build the heading tree for *doc* and return its leaf sections.
    Leaf sections are the units that become CourseMapNodes in P1.
    """
    if doc.source_type == "pptx":
        return _extract_pptx_sections(doc)
    if doc.source_type == "docx":
        return _extract_docx_sections(doc)
    return _extract_pdf_sections(doc)


def heading_fraction(doc: ParsedDocument) -> float:
    """
    Fraction of text blocks classified as headings.
    Used by the P1 regression test: must be < HEADING_MAX_BLOCK_PCT for a
    slide-exported PDF so that an absolute-threshold bug is caught immediately.
    """
    text_blocks = [b for b in doc.blocks if b.block_type == "text" and b.span_font_sizes]
    if not text_blocks:
        return 0.0
    _, _, _, heading_sizes = _compute_thresholds(text_blocks)
    heading_count = sum(1 for b in text_blocks if b.max_font_size in heading_sizes)
    return heading_count / len(text_blocks)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _extract_pdf_sections(doc: ParsedDocument) -> list[LeafSection]:
    text_blocks = [b for b in doc.blocks if b.block_type == "text" and b.span_font_sizes]

    if not text_blocks:
        pages = [b.page for b in doc.blocks] or [0]
        return [LeafSection(
            path=[doc.source_file],
            blocks=list(doc.blocks),
            page_start=min(pages),
            page_end=max(pages),
            source_file=doc.source_file,
        )]

    modal, std, threshold, heading_sizes = _compute_thresholds(text_blocks)

    # Assign heading levels: largest font → level 1, etc.
    sorted_sizes = sorted(heading_sizes, reverse=True)
    size_to_level: dict[float, int] = {sz: i + 1 for i, sz in enumerate(sorted_sizes)}

    sections: list[LeafSection] = []
    heading_stack: list[str] = []
    content_blocks: list[TextBlock] = []
    # Page on which the current section's content begins. Maintained by the loop
    # below and read by flush().
    #
    # This previously coexisted with a separate `page_start` that was initialised to
    # 0 and never reassigned — flush() read that one, so EVERY PDF leaf section
    # reported page_start=0 and every PDF node carried page_span=(0, page_end). The
    # value was deterministic, so the idempotency gate passed on it; it was simply
    # wrong, on every node, in a field that feeds citations.
    current_page_start: int = 0

    def flush(page_end: int) -> None:
        if content_blocks:
            path = list(heading_stack) if heading_stack else [doc.source_file]
            sections.append(LeafSection(
                path=path,
                blocks=list(content_blocks),
                page_start=current_page_start,
                page_end=page_end,
                source_file=doc.source_file,
            ))
            content_blocks.clear()

    for block in doc.blocks:
        if block.block_type == "text" and block.max_font_size in size_to_level:
            level = size_to_level[block.max_font_size]
            flush(block.page)
            # Truncate stack to parent level, then push this heading.
            del heading_stack[level - 1:]
            heading_stack.append(block.text)
            current_page_start = block.page
        else:
            if not content_blocks:
                current_page_start = block.page
            content_blocks.append(block)

    flush(doc.page_count - 1 if doc.page_count > 0 else 0)

    return sections if sections else [LeafSection(
        path=[doc.source_file],
        blocks=list(doc.blocks),
        page_start=0,
        page_end=doc.page_count - 1 if doc.page_count > 0 else 0,
        source_file=doc.source_file,
    )]


def _compute_thresholds(
    text_blocks: list[TextBlock],
) -> tuple[float, float, float, set[float]]:
    """Returns (modal, std, threshold, heading_sizes_set)."""
    all_sizes: list[float] = []
    for b in text_blocks:
        all_sizes.extend(b.span_font_sizes)

    modal = _modal_font_size(all_sizes)
    std = statistics.pstdev(all_sizes) if len(all_sizes) > 1 else 0.0
    threshold = modal + config.HEADING_STD_FACTOR * std

    heading_sizes = {
        b.max_font_size for b in text_blocks if b.max_font_size > threshold
    }
    return modal, std, threshold, heading_sizes


def _modal_font_size(sizes: list[float]) -> float:
    """Most common font size, rounded to 0.5 pt for stable grouping."""
    rounded = [round(s * 2) / 2 for s in sizes]
    try:
        return statistics.mode(rounded)
    except statistics.StatisticsError:
        return statistics.mean(rounded) if rounded else 0.0


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------

def _extract_pptx_sections(doc: ParsedDocument) -> list[LeafSection]:
    """Each slide → one leaf section. Heading placeholder text → path."""
    slides: dict[int, list[TextBlock]] = {}
    for block in doc.blocks:
        slides.setdefault(block.page, []).append(block)

    sections: list[LeafSection] = []
    for slide_idx in range(doc.page_count):
        slide_blocks = slides.get(slide_idx, [])
        heading_texts = [
            b.text for b in slide_blocks if b.is_explicit_heading and b.text.strip()
        ]
        body_blocks = [b for b in slide_blocks if not b.is_explicit_heading]
        path = heading_texts if heading_texts else [f"Slide {slide_idx + 1}"]
        sections.append(LeafSection(
            path=path,
            blocks=body_blocks,
            page_start=slide_idx,
            page_end=slide_idx,
            source_file=doc.source_file,
        ))

    return sections


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _extract_docx_sections(doc: ParsedDocument) -> list[LeafSection]:
    """
    Explicit-heading path, same signal as PPTX: `is_explicit_heading` comes from the
    paragraph STYLE, so no font-size inference ever runs over a DOCX (its blocks
    carry no span font sizes at all, and the PDF path would collapse the whole
    document into one fallback section).

    It cannot simply reuse `_extract_pptx_sections`, which groups blocks by `page`.
    For a DOCX `page` is a per-block ordinal, so that grouping would make every
    single block its own section. A DOCX is a linear document: a heading opens a
    section and the blocks after it are its content — the same walk
    `_extract_pdf_sections` performs, with the format's own heading flag replacing
    the font-size threshold.

    Heading paths are FLAT (one entry, the current heading), exactly as the PPTX path
    produces flat slide-title paths. DOCX style names do carry a level ("Heading 1"
    vs "Heading 2") and could nest, but TextBlock carries no level and nothing in
    this change asks for nesting — recorded in todo.md rather than invented here.
    """
    sections: list[LeafSection] = []
    heading: Optional[str] = None
    content: list[TextBlock] = []

    def flush() -> None:
        if not content:
            return
        pages = [b.page for b in content]
        sections.append(LeafSection(
            path=[heading] if heading else [doc.source_file],
            blocks=list(content),
            page_start=min(pages),
            page_end=max(pages),
            source_file=doc.source_file,
        ))
        content.clear()

    for block in doc.blocks:
        if (
            block.block_type == "text"
            and block.is_explicit_heading
            and block.text.strip()
        ):
            flush()                 # closes the PREVIOUS heading's section
            heading = block.text
        else:
            content.append(block)

    flush()

    if not sections:
        # Headings only, or no content at all: keep every block rather than returning
        # nothing, so no text can vanish between parse and course map.
        pages = [b.page for b in doc.blocks] or [0]
        return [LeafSection(
            path=[doc.source_file],
            blocks=list(doc.blocks),
            page_start=min(pages),
            page_end=max(pages),
            source_file=doc.source_file,
        )]

    return sections
