from __future__ import annotations

from pydantic import BaseModel


class NodeFlags(BaseModel):
    has_figure: bool = False
    has_table: bool = False
    has_equation: bool = False
    has_code: bool = False


class CourseMapNode(BaseModel):
    # node_id = sha1(source_file + "|" + "/".join(path)) — stable across re-ingest.
    # source_file is part of the hash because a course ingests several documents and the
    # same heading (e.g. "Introduction") recurs across them. Hashing the heading path
    # alone would collapse those into one node, but source_file holds a single string
    # and page_span a single tuple, so the merged node would cite the wrong file and
    # the wrong pages — deterministically, so the P1 "identical node IDs" gate would
    # still pass. Hashing code lives in ingest/coursemap.py (P1).
    node_id: str
    path: list[str]                     # e.g. ["Ch 3 Optimization", "3.2 Momentum"]
    source_file: str
    page_span: tuple[int, int]
    token_count: int
    chunk_ids: list[str]               # uuid5, ordered
    key_terms: list[str]               # YAKE, CPU only
    flags: NodeFlags
    instructional_mass: float          # normalised so all nodes sum to 1.0
