from __future__ import annotations

from pydantic import BaseModel, Field


class NodeFlags(BaseModel):
    has_figure: bool = False
    has_table: bool = False
    has_equation: bool = False
    has_code: bool = False


class CourseMapNode(BaseModel):
    node_id: str                        # sha1 of the heading path — stable across re-ingest
    path: list[str]                     # e.g. ["Ch 3 Optimization", "3.2 Momentum"]
    source_file: str
    page_span: tuple[int, int]
    token_count: int
    chunk_ids: list[str]               # uuid5, ordered
    key_terms: list[str]               # YAKE, CPU only
    flags: NodeFlags
    instructional_mass: float          # normalised so all nodes sum to 1.0
