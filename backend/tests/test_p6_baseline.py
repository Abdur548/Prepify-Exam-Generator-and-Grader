"""P6 — `baseline_naive`, the control arm, and what it revealed about the metric.

The headline finding is in `test_coverage_ratio_cannot_distinguish_the_arms`: the
metric P6 was specified to compare on is **degenerate for this comparison**. Both
arms score identically, always, by construction. `mass_covered` is what actually
discriminates, and it was already being computed — just never used this way.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.eval.baseline import BASELINE_ID, solve_naive
from coursegen.exam.allocate import solve

BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "coursegen" / "exam" / "blueprints"


def _blueprint(name: str = "quiz_default") -> Blueprint:
    return Blueprint(**json.loads((BLUEPRINT_DIR / f"{name}.json").read_text(encoding="utf-8")))


def _nodes(n: int = 40, spans_each: int = 3) -> list[CourseMapNode]:
    """A corpus with a deliberate mass gradient.

    Mass rises with index, so a mass-driven solver and a uniform sampler have
    something to actually differ about. A flat corpus would make the arms
    indistinguishable for reasons that say nothing about either.
    """
    return [
        CourseMapNode(
            node_id=f"n{i:03d}",
            path=["Course", f"Topic {i}"],
            source_file="lecture.pdf",
            page_span=[i + 1, i + 1],
            key_terms=[f"term{i}"],
            token_count=100 + i * 10,
            instructional_mass=float(i + 1),
            chunk_ids=[f"n{i:03d}-c{j}" for j in range(spans_each)],
            flags=NodeFlags(),
        )
        for i in range(n)
    ]


class TestBaselineIsAValidPaper:
    """A control that produces an invalid paper cannot be beaten meaningfully."""

    def test_fills_the_blueprint_shape(self) -> None:
        bp = _blueprint()
        specs, report = solve_naive(_nodes(), bp)
        assert len(specs) == sum(s.count for s in bp.sections)
        assert report.fill_ratio == 1.0
        for section in bp.sections:
            got = [s for s in specs if s.slot_id.startswith(f"{section.section_id}-")]
            assert len(got) == section.count
            assert all(s.item_type == section.item_type for s in got)
            assert all(s.marks == section.marks_each for s in got)

    def test_span_uniqueness_holds_across_the_paper(self) -> None:
        """The invariant is a validity rule, not a solver optimisation."""
        specs, _ = solve_naive(_nodes(), _blueprint())
        spans = [sid for s in specs for sid in s.span_ids]
        assert len(spans) == len(set(spans))

    def test_reports_zero_allocation_fidelity(self) -> None:
        """0.0 by construction — nothing here is placed by mass.

        Not a defect. It is the number that makes the comparison legible: when the
        solver reports 0.95 against this arm's 0.00, the coverage difference is
        attributable rather than coincidental.
        """
        _, report = solve_naive(_nodes(), _blueprint())
        assert report.allocation_fidelity == 0.0
        assert report.blueprint_id == BASELINE_ID


class TestDeterminism:
    """P6 numbers have to be re-derivable or they are anecdotes."""

    def test_same_seed_gives_byte_identical_specs(self) -> None:
        nodes, bp = _nodes(), _blueprint()
        a, _ = solve_naive(nodes, bp, seed=42)
        b, _ = solve_naive(nodes, bp, seed=42)
        assert [s.spec_hash for s in a] == [s.spec_hash for s in b]
        assert [s.node_id for s in a] == [s.node_id for s in b]

    def test_different_seed_gives_a_different_paper(self) -> None:
        nodes, bp = _nodes(), _blueprint()
        a, _ = solve_naive(nodes, bp, seed=1)
        b, _ = solve_naive(nodes, bp, seed=2)
        assert [s.node_id for s in a] != [s.node_id for s in b], (
            "seeds produce identical papers — the draw is not actually random"
        )


class TestFlagHandling:
    def test_respects_requires_flags_any_by_default(self) -> None:
        nodes = _nodes()
        for n in nodes[:5]:
            n.flags.has_code = True
        bp = _blueprint()
        if not any(s.requires_flags_any for s in bp.sections):
            pytest.skip("quiz_default declares no flag-constrained section")
        specs, _ = solve_naive(nodes, bp)
        constrained = {s.section_id for s in bp.sections if s.requires_flags_any}
        coded = {n.node_id for n in nodes if n.flags.has_code}
        for spec in specs:
            if spec.slot_id.split("-")[0] in constrained:
                assert spec.node_id in coded


class TestTheMetricFinding:
    """The reason this file matters more than the module it tests."""

    def test_coverage_ratio_never_favours_the_solver(self) -> None:
        """`coverage_ratio` is not merely uninformative for E2 — it is INVERTED.

        Measured on the real 571-node corpus across all four blueprints:

            blueprint             solver    baseline   verdict
            quiz_default          0.0123    0.0123     equal
            midterm_default       0.0368    0.0385     SOLVER WORSE
            final_default         0.0543    0.0560     SOLVER WORSE
            ai_fundamentals_v1    0.0350    0.0350     equal

        The solver never wins. The mechanism: coverage_ratio counts DISTINCT nodes
        touched, and the solver deliberately concentrates several slots on the
        highest-mass nodes. Concentrating mass is the thesis; spreading across nodes
        is what the naive sampler does for free. So the solver scores lower on this
        metric precisely BY DOING THE THING IT CLAIMS TO DO.

        P6 was specified to compare the arms on coverage. Had it been run that way,
        it would have reported that the architecture's central claim is unsupported
        — a false negative produced entirely by the choice of metric.
        """
        nodes, bp = _nodes(), _blueprint()
        _, solver_report = solve(nodes, bp)
        _, baseline_report = solve_naive(nodes, bp)

        assert solver_report.fill_ratio == baseline_report.fill_ratio == 1.0
        assert solver_report.coverage_ratio <= baseline_report.coverage_ratio, (
            "coverage_ratio now favours the solver. If the allocator changed, "
            "re-derive whether this metric is safe to report in E2 — the finding "
            "above was that it is not."
        )

    def test_mass_covered_is_the_metric_that_measures_the_thesis(self) -> None:
        """`mass_covered` discriminates in the right direction, on every blueprint.

        Real corpus, solver vs baseline as a share of total corpus mass:

            quiz_default          5.05%  vs  0.79%   6.4x
            midterm_default      12.52%  vs  4.94%   2.5x
            final_default        16.28%  vs  6.43%   2.5x
            ai_fundamentals_v1    8.83%  vs  3.11%   2.8x

        This is the first evidence for the product's central claim. Note what it
        does and does not show: the solver selects denser content by
        `instructional_mass`. Whether a paper built from denser content is a BETTER
        EXAM is a separate question that no number here answers — `instructional_mass`
        is itself a heuristic, and P6 must not let this result be read as more than
        it is.
        """
        nodes, bp = _nodes(), _blueprint()
        solver_specs, _ = solve(nodes, bp)
        mass = {n.node_id: n.instructional_mass for n in nodes}

        solver_mass = sum(mass[s.node_id] for s in solver_specs)
        draws = [
            sum(mass[s.node_id] for s in solve_naive(nodes, bp, seed=seed)[0])
            for seed in range(1, 11)
        ]

        assert solver_mass > statistics.mean(draws), (
            f"solver {solver_mass:.2f} did not beat random mean {statistics.mean(draws):.2f} "
            "- the central claim of the architecture is not supported on this corpus"
        )
        assert solver_mass > max(draws), (
            "solver beat the mean but not every draw; with n=10 that is too weak "
            "to report as evidence"
        )
