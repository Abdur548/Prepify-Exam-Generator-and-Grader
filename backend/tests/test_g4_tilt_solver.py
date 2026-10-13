"""G4 — the solver takes an optional per-node mass tilt.

The 2.5–6.4x evaluation was measured on `solve()` as it stood. Absent a tilt it
must stay exactly that solver, on every shipped blueprint, or every number in
`P6-EVALUATION.md` describes code that no longer exists. Byte-identity against
the PRE-CHANGE source is proved separately (run once, recorded in the commit);
these tests pin that None, {} and a tilt of 1.0 are all the same solver, and that
a tilt actually steers.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from coursegen.contracts.course_map import CourseMapNode
from coursegen.exam.allocate import solve
from coursegen.pipeline import available_blueprints, load_blueprint

FIXTURES = Path(__file__).parent / "fixtures"
MAPS = ["course_map_sample.json", "course_map_ai_fundamentals.json"]


def _nodes(name: str) -> list[CourseMapNode]:
    return [CourseMapNode(**n) for n in json.loads((FIXTURES / name).read_text(encoding="utf-8"))]


def _dump(result) -> tuple[list[dict], dict]:
    specs, coverage = result
    return [s.model_dump() for s in specs], coverage.model_dump()


@pytest.mark.parametrize("map_name", MAPS)
@pytest.mark.parametrize("blueprint_id", available_blueprints())
class TestNoTiltIsTheSameSolver:

    def test_none_and_empty_and_unit_tilts_agree(self, map_name, blueprint_id) -> None:
        nodes, bp = _nodes(map_name), load_blueprint(blueprint_id)
        base = _dump(solve(nodes, bp))
        assert _dump(solve(nodes, bp, mass_tilt=None)) == base
        assert _dump(solve(nodes, bp, mass_tilt={})) == base
        assert _dump(solve(nodes, bp, mass_tilt={n.node_id: 1.0 for n in nodes})) == base


class TestTiltSteers:

    def test_a_heavy_tilt_pulls_an_unused_node_into_the_paper(self) -> None:
        nodes, bp = _nodes("course_map_sample.json"), load_blueprint("quiz_default")
        used = {s.node_id for s in solve(nodes, bp)[0]}
        unused = sorted(n.node_id for n in nodes if n.node_id not in used)
        assert unused, "fixture should leave some node out of a quiz"
        tilted = {s.node_id for s in solve(nodes, bp, mass_tilt={unused[0]: 1000.0})[0]}
        assert unused[0] in tilted

    def test_a_tilt_for_a_node_not_in_the_map_changes_nothing(self) -> None:
        nodes, bp = _nodes("course_map_sample.json"), load_blueprint("quiz_default")
        assert _dump(solve(nodes, bp, mass_tilt={"no-such-node": 5.0})) == _dump(solve(nodes, bp))

    def test_the_tilted_paper_is_deterministic(self) -> None:
        nodes, bp = _nodes("course_map_sample.json"), load_blueprint("midterm_default")
        tilt = {n.node_id: 0.25 + (i % 3) for i, n in enumerate(nodes)}
        assert _dump(solve(nodes, bp, mass_tilt=tilt)) == _dump(solve(nodes, bp, mass_tilt=tilt))


class TestTiltIsValidated:

    @pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
    def test_a_tilt_that_is_not_a_positive_finite_number_raises(self, bad) -> None:
        """A zero tilt would silently delete a node from the paper; NaN would poison
        every share it touched. Neither may pass for a weighting."""
        nodes, bp = _nodes("course_map_sample.json"), load_blueprint("quiz_default")
        with pytest.raises(ValueError, match=nodes[0].node_id):
            solve(nodes, bp, mass_tilt={nodes[0].node_id: bad})
