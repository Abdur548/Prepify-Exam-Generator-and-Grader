"""
Leaf-section → Chunk list.

uuid5 IDs make ingest idempotent: re-running on the same files produces the same
IDs, so Qdrant upsert is a no-op rather than a duplication.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from coursegen import config
from coursegen.ingest.parse import TextBlock
from coursegen.ingest.structure import LeafSection

# Approximate conversion: 1 token ≈ 4 characters.
_CHARS_PER_TOKEN = 4
_MAX_CHUNK_CHARS: int = config.MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN
_OVERLAP_CHARS: int = int(_MAX_CHUNK_CHARS * config.CHUNK_OVERLAP_PCT)


@dataclass
class Chunk:
    chunk_id: str       # uuid5 — deterministic, content-addressed
    text: str           # heading path prepended — this is what gets embedded
    raw_text: str       # content only (no path prefix) — used for token_count
    source_file: str
    # Page of the FIRST block that contributed characters to THIS chunk — not the
    # section's page_start. Flows into chunk.page → the user-visible citation.
    # For a DOCX source this is a block ordinal, not a printed page (see parse.py).
    page: int
    path: list[str]     # heading path of the parent section


def chunk_section(section: LeafSection) -> list[Chunk]:
    """
    Split one leaf section into ≥1 chunks with intra-section overlap.
    Heading path is prepended to each chunk text before embedding (PRD §9.1 step 3).

    `page` is resolved PER CHUNK, not per section. Stamping every chunk with
    `section.page_start` cited a chunk drawn from page 7 of a 5–9 section as page 5.

    Tie-break when a chunk spans several blocks: the page of the FIRST block that
    contributes at least one character to that chunk. Blocks are visited in document
    order, so this is also the earliest page the chunk draws on — the page a reader
    should turn to first. The single space joining two blocks belongs to neither and
    never decides the page.

    ⚠ `_chunk_id` hashes the page, so precise pages CHANGE chunk IDs, and therefore
    Qdrant point IDs, relative to the page_start version. Any index built before this
    change is stale and must be re-ingested. Determinism is unaffected: the same
    input still yields the same IDs, so upsert stays a no-op on re-ingest (C1).
    """
    text_blocks = [
        b for b in section.blocks
        if b.block_type == "text" and b.text.strip()
    ]
    prefix = _heading_prefix(section.path)

    if not text_blocks:
        # No textual content (e.g., figure-only section) — emit one empty-text chunk
        # so the section still gets a CourseMapNode and can anchor a figure question.
        # The page still comes from a real block wherever there is one: a figure-only
        # section is produced by its figure blocks.
        page = section.blocks[0].page if section.blocks else section.page_start
        return [Chunk(
            chunk_id=_chunk_id("", section.source_file, page),
            text=prefix,
            raw_text="",
            source_file=section.source_file,
            page=page,
            path=section.path,
        )]

    # Built exactly as before so chunk TEXT is unchanged; only the page moves.
    joined = " ".join(b.text for b in text_blocks)
    raw_content = joined.strip()
    spans = _block_spans(text_blocks, len(joined) - len(joined.lstrip()), len(raw_content))

    raw_pieces = _split_with_overlap(raw_content, _MAX_CHUNK_CHARS, _OVERLAP_CHARS)
    chunks: list[Chunk] = []

    for piece, piece_start in raw_pieces:
        page = _page_for_span(spans, piece_start, piece_start + len(piece), section)
        full_text = f"{prefix}\n\n{piece}" if prefix else piece
        chunks.append(Chunk(
            chunk_id=_chunk_id(full_text, section.source_file, page),
            text=full_text,
            raw_text=piece,
            source_file=section.source_file,
            page=page,
            path=section.path,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _chunk_id(text: str, source_file: str, page: int) -> str:
    """
    uuid5(NAMESPACE_URL, sha256(text + "|" + source_file + "|" + str(page)))
    Deterministic: identical inputs → identical ID → upsert is idempotent (C1).
    """
    content = f"{text}|{source_file}|{page}"
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, sha))


def _heading_prefix(path: list[str]) -> str:
    return " > ".join(path) if path else ""


def _block_spans(
    blocks: list[TextBlock], lead: int, length: int
) -> list[tuple[int, int, int]]:
    """
    (start, end, page) for each block's own characters within the joined-and-stripped
    section text.

    Blocks are joined with a single space; that separator belongs to no block and is
    skipped rather than attributed to either neighbour. *lead* is how many leading
    characters the final `.strip()` removed, so the offsets line up with the text the
    splitter actually sees; *length* clamps the trailing strip.
    """
    spans: list[tuple[int, int, int]] = []
    offset = 0
    for i, block in enumerate(blocks):
        if i:
            offset += 1                             # the " " joiner
        start = max(0, offset - lead)
        end = min(length, offset + len(block.text) - lead)
        if end > start:
            spans.append((start, end, block.page))
        offset += len(block.text)
    return spans


def _page_for_span(
    spans: list[tuple[int, int, int]],
    start: int,
    end: int,
    section: LeafSection,
) -> int:
    """
    Page of the first block overlapping [start, end) — the documented tie-break.

    Raises when nothing overlaps. A chunk whose page cannot be traced to a block
    would be cited at a page it has no content from, and a plausible-but-wrong
    citation is worse than a stopped ingest. `raise`, not `assert`: `python -O`
    strips asserts and the wrong page would then ship silently.
    """
    for span_start, span_end, page in spans:
        if span_start < end and start < span_end:
            return page
    raise ValueError(
        f"{section.source_file}: chunk at [{start}, {end}) of section "
        f"{section.path!r} overlaps no source block — its page cannot be traced"
    )


def _split_with_overlap(
    text: str, max_chars: int, overlap_chars: int
) -> list[tuple[str, int]]:
    """
    Split *text* into chunks of at most *max_chars*, with *overlap_chars*
    of shared content between consecutive chunks. Splitting prefers word
    boundaries. Overlap is intra-section only — never crosses a section boundary.

    Returns (piece, start_offset) pairs, where start_offset is the index of the
    piece's first character in *text*. The offset is what lets chunk_section
    attribute each piece to the block that produced it. Piece strings are byte-
    identical to what this function returned before the offsets were added.
    """
    if len(text) <= max_chars:
        return [(text, 0)]

    pieces: list[tuple[str, int]] = []
    start = 0

    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            pieces.append((text[start:], start))
            break

        # Prefer to break at a word boundary near the end.
        boundary = text.rfind(" ", start, end)
        if boundary > start:
            end = boundary

        raw_piece = text[start:end]
        piece = raw_piece.strip()
        if piece:
            # .strip() can move the first character; the offset must follow it.
            pieces.append((piece, start + len(raw_piece) - len(raw_piece.lstrip())))

        next_start = end - overlap_chars
        start = max(next_start, start + 1)  # never go backwards

    return pieces if pieces else [(text, 0)]
