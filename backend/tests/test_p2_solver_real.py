"""
P2 gate — Solver on real data.

The P2' gates proved determinism and span-uniqueness on a hand-authored fixture.
This file re-proves those same properties on a course map produced by the actual
ingest pipeline, which is the point:

  P2' gate                              → hand-authored JSON
  P2  gate (this file)                  → ingest() output from a real corpus

The _ingest_with_real_qdrant helper mirrors the one in test_p1_ingest.py —
re-implemented here rather than imported from a test module.

Gate conditions (must all hold):
  1. solve() twice on the same real course_map → byte-identical ItemSpec[]
  2. No span appears in two items anywhere in the paper
  3. CoverageReport is populated and internally consistent
  4. Per-node coverage table is non-trivial (inspect output with pytest -s)
  5. allocation_fidelity is reported, in range, and accounts for every filled slot
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.exam.allocate import solve
from coursegen.ingest.coursemap import ingest
from coursegen.ingest.embed import EmbedResult


# ---------------------------------------------------------------------------
# Helper — mirrors test_p1_ingest._ingest_with_real_qdrant
# ---------------------------------------------------------------------------

def _fake_embed(chunks: list, model: object) -> EmbedResult:
    n = len(chunks)
    return EmbedResult(
        dense=np.zeros((n, config.DENSE_VECTOR_SIZE), dtype=np.float32),
        sparse=[{0: 1.0} for _ in range(n)],
    )


def _ingest_real(source_dir: Path, data_dir: Path) -> list:
    """Full ingest with fake embedder + real local Qdrant."""
    with patch("coursegen.ingest.coursemap.load_model", return_value=MagicMock()), \
         patch("coursegen.ingest.coursemap.embed_chunks", side_effect=_fake_embed):
        return ingest(source_dir, data_dir)


def _load_blueprint(name: str) -> Blueprint:
    path = (
        Path(__file__).parent.parent
        / "coursegen" / "exam" / "blueprints" / f"{name}.json"
    )
    return Blueprint.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Shared module-scoped fixture — ingest runs once for all P2 tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_course_map(rich_source_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> list:
    """
    CourseMapNode[] produced by running the live ingest pipeline on the
    rich_source_dir fixture corpus (defined in conftest.py).
    Ingest runs once per test module — not per test.
    """
    data_dir = tmp_path_factory.mktemp("p2_data")
    return _ingest_real(rich_source_dir, data_dir)


# ---------------------------------------------------------------------------
# Gate 1 — determinism on real data
# ---------------------------------------------------------------------------

class TestDeterminismRealData:
    def test_quiz_default_byte_identical(self, real_course_map: list) -> None:
        bp = _load_blueprint("quiz_default")
        items1, _ = solve(real_course_map, bp)
        items2, _ = solve(real_course_map, bp)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2, "Solver output is not deterministic on real ingest data"

    def test_midterm_default_byte_identical(self, real_course_map: list) -> None:
        bp = _load_blueprint("midterm_default")
        items1, _ = solve(real_course_map, bp)
        items2, _ = solve(real_course_map, bp)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2

    def test_final_default_byte_identical(self, real_course_map: list) -> None:
        bp = _load_blueprint("final_default")
        items1, _ = solve(real_course_map, bp)
        items2, _ = solve(real_course_map, bp)
        j1 = json.dumps([i.model_dump() for i in items1], sort_keys=True)
        j2 = json.dumps([i.model_dump() for i in items2], sort_keys=True)
        assert j1 == j2


# ---------------------------------------------------------------------------
# Gate 2 — no span reuse across the entire paper on real data
# ---------------------------------------------------------------------------

class TestSpanUniquenessRealData:
    def test_quiz_default_no_reuse(self, real_course_map: list) -> None:
        items, _ = solve(real_course_map, _load_blueprint("quiz_default"))
        spans = [s for item in items for s in item.span_ids]
        assert len(spans) == len(set(spans)), (
            f"Spans reused on real data: {[s for s in spans if spans.count(s) > 1]}"
        )

    def test_midterm_default_no_reuse(self, real_course_map: list) -> None:
        items, _ = solve(real_course_map, _load_blueprint("midterm_default"))
        spans = [s for item in items for s in item.span_ids]
        assert len(spans) == len(set(spans))

    def test_final_default_no_reuse(self, real_course_map: list) -> None:
        items, _ = solve(real_course_map, _load_blueprint("final_default"))
        spans = [s for item in items for s in item.span_ids]
        assert len(spans) == len(set(spans))


# ---------------------------------------------------------------------------
# Gate 3 — coverage report populated and consistent on real data
# ---------------------------------------------------------------------------

class TestCoverageReportRealData:
    def test_ratios_in_range(self, real_course_map: list) -> None:
        _, report = solve(real_course_map, _load_blueprint("midterm_default"))
        assert 0.0 <= report.coverage_ratio <= 1.0
        assert 0.0 <= report.fill_ratio <= 1.0
        assert report.nodes_total == len(real_course_map)
        assert report.nodes_covered <= report.nodes_total

    def test_fill_ratio_matches_slots(self, real_course_map: list) -> None:
        bp = _load_blueprint("midterm_default")
        items, report = solve(real_course_map, bp)
        assert report.slots_total == sum(s.count for s in bp.sections)
        assert report.slots_filled == len(items)
        assert abs(report.fill_ratio - len(items) / report.slots_total) < 1e-9

    def test_per_node_marks_match_items(self, real_course_map: list) -> None:
        items, report = solve(real_course_map, _load_blueprint("midterm_default"))
        assert sum(i.marks for i in items) == sum(n.marks_allocated for n in report.per_node)

    def test_coverage_table_printed(self, real_course_map: list, capsys) -> None:
        """
        Satisfy 'inspect the per-node table by hand once' from the P2 verify-after.
        Run with pytest -s to see the full table.
        """
        bp = _load_blueprint("midterm_default")
        items, report = solve(real_course_map, bp)

        header = (
            f"\n{'node_id':>12}  {'mass':>8}  {'marks':>6}  {'slots':>24}  path\n"
            + "-" * 80
        )
        rows = "\n".join(
            f"{n.node_id[:12]:>12}  {n.instructional_mass:>8.4f}  "
            f"{n.marks_allocated:>6}  {str(n.slots)[:24]:>24}  {' > '.join(n.path)}"
            for n in sorted(report.per_node, key=lambda x: -x.marks_allocated)
            if n.marks_allocated > 0
        )
        summary = (
            f"\nReal data P2 coverage — {bp.blueprint_id}\n"
            f"  nodes: {report.nodes_covered}/{report.nodes_total}  "
            f"coverage_ratio={report.coverage_ratio:.2f}  "
            f"fill_ratio={report.fill_ratio:.2f}  "
            f"({report.slots_filled}/{report.slots_total} slots)\n"
            f"  mass_covered={report.mass_covered:.4f}\n"
            + header + "\n" + rows + "\n"
        )
        print(summary)

        # Plausibility assertions: non-empty, at least one node has marks.
        assert len(report.per_node) == len(real_course_map)
        assert any(n.marks_allocated > 0 for n in report.per_node), (
            "No node received any marks — solver produced zero items on real data"
        )
        assert report.mass_covered > 0.0


# ---------------------------------------------------------------------------
# Gate 5 — allocation_fidelity on real data
#
# On this corpus every node yields exactly one chunk, so apportionment is
# frequently unsatisfiable and a real share of the paper is placed by falling
# through to whatever node still has a span. coverage_ratio and fill_ratio
# cannot see that. These gates only prove the third ratio is REPORTED and
# COHERENT — the value itself moves with the corpus and is not asserted.
# ---------------------------------------------------------------------------

BLUEPRINTS = ["quiz_default", "midterm_default", "final_default"]


class TestAllocationFidelityRealData:
    @pytest.mark.parametrize("blueprint_name", BLUEPRINTS)
    def test_fidelity_reported_and_in_range(
        self, real_course_map: list, blueprint_name: str
    ) -> None:
        _, report = solve(real_course_map, _load_blueprint(blueprint_name))
        assert 0.0 <= report.allocation_fidelity <= 1.0

    @pytest.mark.parametrize("blueprint_name", BLUEPRINTS)
    def test_slot_accounting_invariant(
        self, real_course_map: list, blueprint_name: str
    ) -> None:
        """Every filled slot came through exactly one of the two fill branches."""
        items, report = solve(real_course_map, _load_blueprint(blueprint_name))
        assert report.slots_by_mass + report.slots_by_fallthrough == report.slots_filled
        assert report.slots_filled == len(items)
        assert report.slots_by_mass >= 0
        assert report.slots_by_fallthrough >= 0
        assert report.allocation_fidelity == pytest.approx(
            report.slots_by_mass / report.slots_filled if report.slots_filled else 0.0
        )

    def test_three_ratios_printed_side_by_side(self, real_course_map: list) -> None:
        """Three questions, three answers, printed together. Run with pytest -s.

        No fidelity value is asserted — the corpus can legitimately change and a
        pinned number would make an honest re-ingest look like a regression.
        """
        lines = [
            "",
            "Real data - three ratios that answer three different questions",
            f"  {'blueprint':<18}{'coverage':>10}{'fill':>8}{'fidelity':>10}"
            f"{'by_mass':>9}{'by_fall':>9}{'slots':>8}",
            "  " + "-" * 72,
        ]
        for name in BLUEPRINTS:
            _, r = solve(real_course_map, _load_blueprint(name))
            lines.append(
                f"  {name:<18}{r.coverage_ratio:>10.3f}{r.fill_ratio:>8.3f}"
                f"{r.allocation_fidelity:>10.3f}{r.slots_by_mass:>9}"
                f"{r.slots_by_fallthrough:>9}{r.slots_filled:>5}/{r.slots_total:<2}"
            )
        spans_per_node = [len(n.chunk_ids) for n in real_course_map]
        lines.append(
            f"\n  nodes={len(real_course_map)}  spans={sum(spans_per_node)}  "
            f"max spans/node={max(spans_per_node)}"
        )
        summary = "\n".join(lines) + "\n"
        print(summary)

        # One row per blueprint, all three ratios present on each.
        for name in BLUEPRINTS:
            assert name in summary
        assert "fidelity" in summary
