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
    page: int           # section page_start (approximation for MVP1)
    path: list[str]     # heading path of the parent section


def chunk_section(section: LeafSection) -> list[Chunk]:
    """
    Split one leaf section into ≥1 chunks with intra-section overlap.
    Heading path is prepended to each chunk text before embedding (PRD §9.1 step 3).
    """
    raw_content = " ".join(
        b.text for b in section.blocks if b.block_type == "text" and b.text.strip()
    ).strip()

    if not raw_content:
        # No textual content (e.g., figure-only section) — emit one empty-text chunk
        # so the section still gets a CourseMapNode and can anchor a figure question.
        cid = _chunk_id("", section.source_file, section.page_start)
        prefix = _heading_prefix(section.path)
        return [Chunk(
            chunk_id=cid,
            text=prefix,
            raw_text="",
            source_file=section.source_file,
            page=section.page_start,
            path=section.path,
        )]

    raw_pieces = _split_with_overlap(raw_content, _MAX_CHUNK_CHARS, _OVERLAP_CHARS)
    prefix = _heading_prefix(section.path)
    chunks: list[Chunk] = []

    for piece in raw_pieces:
        full_text = f"{prefix}\n\n{piece}" if prefix else piece
        cid = _chunk_id(full_text, section.source_file, section.page_start)
        chunks.append(Chunk(
            chunk_id=cid,
            text=full_text,
            raw_text=piece,
            source_file=section.source_file,
            page=section.page_start,
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


def _split_with_overlap(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars*, with *overlap_chars*
    of shared content between consecutive chunks. Splitting prefers word
    boundaries. Overlap is intra-section only — never crosses a section boundary.
    """
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    start = 0

    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            pieces.append(text[start:])
            break

        # Prefer to break at a word boundary near the end.
        boundary = text.rfind(" ", start, end)
        if boundary > start:
            end = boundary

        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)

        next_start = end - overlap_chars
        start = max(next_start, start + 1)  # never go backwards

    return pieces if pieces else [text]
