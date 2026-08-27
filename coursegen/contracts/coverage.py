from __future__ import annotations

from pydantic import BaseModel


class NodeCoverage(BaseModel):
    node_id: str
    path: list[str]
    instructional_mass: float
    marks_allocated: int
    slots: list[str]


class CoverageReport(BaseModel):
    blueprint_id: str
    nodes_total: int
    nodes_covered: int
    coverage_ratio: float
    mass_covered: float
    per_node: list[NodeCoverage]
    unfilled_slots: list[str]
    warnings: list[str]
