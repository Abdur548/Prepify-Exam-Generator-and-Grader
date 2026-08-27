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
    coverage_ratio: float   # nodes TOUCHED / nodes total — says nothing about slots being filled
    slots_total: int        # sum of section.count across the blueprint
    slots_filled: int       # slots_total - len(unfilled_slots)
    fill_ratio: float       # slots_filled / slots_total — the number that proves the paper is complete
    mass_covered: float
    per_node: list[NodeCoverage]
    unfilled_slots: list[str]
    warnings: list[str]
