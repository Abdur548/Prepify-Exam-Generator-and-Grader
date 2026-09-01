"""P6 evaluation harness — runs every arm over every blueprint and reports.

Zero LLM calls. This measures **allocation**: which content the system chooses to
ask about. That is where the product's central claim lives — "code decides what to
ask, the model only writes the words" — and it is measurable without spending a
single token.

## The arms

| arm | selects by | runs |
|---|---|---|
| `solver_mass` | `instructional_mass` (shipped) | 1, deterministic |
| `solver_flat` | flat `token_count` | 1, deterministic |
| `baseline_naive` | uniform random | N seeds |

## Why `solver_flat` is measured on TRUE mass

`instructional_mass = token_count × (1 + ln(1 + mean_df_other))`, normalised. The
`solver_flat` arm drops that repetition term and selects on raw token count. It is
then scored on the **original** mass values, never on the flattened ones. Scoring
an arm on the quantity it optimised is circular and would make every arm look
perfect against its own objective.

This is §16's falsification condition, made concrete: if `solver_flat` matches
`solver_mass` on true-mass coverage, the repetition term changes nothing
measurable and the honest report says so.

## Why the headline is mass, not coverage

Measured 2026-09-01: `coverage_ratio` **never favours the solver** and on two of
four blueprints favours the baseline, because it counts distinct nodes touched and
the solver deliberately concentrates slots on high-mass nodes. It scores the solver
lower for doing the thing it claims to do. `coverage_ratio` is still reported here,
as a dispersion figure, never as the verdict. See `P6-EVALUATION.md` §E2.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.eval.baseline import solve_naive
from coursegen.exam.allocate import solve

DEFAULT_SEEDS = tuple(range(1, 11))


@dataclass(frozen=True)
class ArmResult:
    """One arm on one blueprint. `mass_coverage_ratio` is the headline."""
    arm: str
    blueprint_id: str
    mass_coverage_ratio: float
    coverage_ratio: float
    fill_ratio: float
    allocation_fidelity: float
    nodes_covered: int
    slots_filled: int
    slots_total: int
    seed: Optional[int] = None


@dataclass
class BlueprintComparison:
    blueprint_id: str
    solver_mass: ArmResult
    solver_flat: ArmResult
    baseline_draws: list[ArmResult] = field(default_factory=list)

    @property
    def baseline_mean(self) -> float:
        return statistics.mean(d.mass_coverage_ratio for d in self.baseline_draws)

    @property
    def baseline_stdev(self) -> float:
        vals = [d.mass_coverage_ratio for d in self.baseline_draws]
        return statistics.stdev(vals) if len(vals) > 1 else 0.0

    @property
    def advantage(self) -> float:
        """Solver mass coverage as a multiple of the random mean."""
        mean = self.baseline_mean
        return self.solver_mass.mass_coverage_ratio / mean if mean else float("inf")

    @property
    def beats_every_draw(self) -> bool:
        """The claim worth reporting. Beating a mean can be luck at n=10."""
        return all(
            self.solver_mass.mass_coverage_ratio > d.mass_coverage_ratio
            for d in self.baseline_draws
        )

    @property
    def repetition_term_share(self) -> float:
        """§16: what FRACTION of the solver's advantage comes from the repetition term?

        A boolean at an epsilon threshold is not an answer — any difference at all
        makes it True, which reads as "the term matters" when the term may be doing
        almost nothing. The useful quantity is how much of the solver's advantage
        over random survives when the term is removed.

            advantage_mass = solver_mass  - baseline_mean
            advantage_flat = solver_flat  - baseline_mean
            share          = (advantage_mass - advantage_flat) / advantage_mass

        Near 0 means flat `token_count` captures the whole benefit and the term can
        be dropped — a legitimate, publishable answer that simplifies the model.

        **Can exceed 1.0, and that is meaningful.** If `solver_flat` scores BELOW
        random, `adv_flat` is negative and the share passes 100%: the repetition
        term is not merely helping, it is the only reason the arm beats random at
        all — selecting by raw token count alone was actively unhelpful on that
        corpus. Deliberately not clamped; clamping would hide the strongest
        possible evidence for the term.
        """
        adv_mass = self.solver_mass.mass_coverage_ratio - self.baseline_mean
        adv_flat = self.solver_flat.mass_coverage_ratio - self.baseline_mean
        if adv_mass <= 0:
            return 0.0
        return (adv_mass - adv_flat) / adv_mass


def _mass_coverage(specs, mass_by_node: dict[str, float], total_mass: float) -> float:
    """Share of total corpus mass held by the DISTINCT nodes this paper touches.

    Distinct, because a node contributes its mass once however many slots it
    carries — this measures content reached, not slots spent.
    """
    covered = {s.node_id for s in specs}
    if total_mass <= 0:
        return 0.0
    return sum(mass_by_node[n] for n in covered) / total_mass


def _flatten(nodes: list[CourseMapNode]) -> list[CourseMapNode]:
    """`instructional_mass` := normalised `token_count` — the repetition term removed."""
    total = sum(n.token_count for n in nodes) or 1
    return [n.model_copy(update={"instructional_mass": n.token_count / total}) for n in nodes]


def run_comparison(
    course_map: list[CourseMapNode],
    blueprint: Blueprint,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
) -> BlueprintComparison:
    """Run all three arms over one blueprint. No LLM calls."""
    mass_by_node = {n.node_id: n.instructional_mass for n in course_map}
    total_mass = sum(mass_by_node.values())

    def result(arm: str, specs, report, seed: Optional[int] = None) -> ArmResult:
        return ArmResult(
            arm=arm,
            blueprint_id=blueprint.blueprint_id,
            # Every arm scored on the ORIGINAL mass, including solver_flat.
            mass_coverage_ratio=_mass_coverage(specs, mass_by_node, total_mass),
            coverage_ratio=report.coverage_ratio,
            fill_ratio=report.fill_ratio,
            allocation_fidelity=report.allocation_fidelity,
            nodes_covered=report.nodes_covered,
            slots_filled=report.slots_filled,
            slots_total=report.slots_total,
            seed=seed,
        )

    s_specs, s_report = solve(course_map, blueprint)
    f_specs, f_report = solve(_flatten(course_map), blueprint)

    draws = []
    for seed in seeds:
        b_specs, b_report = solve_naive(course_map, blueprint, seed=seed)
        draws.append(result("baseline_naive", b_specs, b_report, seed=seed))

    return BlueprintComparison(
        blueprint_id=blueprint.blueprint_id,
        solver_mass=result("solver_mass", s_specs, s_report),
        solver_flat=result("solver_flat", f_specs, f_report),
        baseline_draws=draws,
    )


def format_report(comparisons: list[BlueprintComparison]) -> str:
    """Human-readable report. Deliberately states what the numbers do not show."""
    out: list[str] = []
    w = out.append

    w("=" * 78)
    w("P6 - ALLOCATION EVALUATION".center(78))
    w("=" * 78)
    w("")
    w("Headline metric: mass_coverage_ratio - share of total corpus instructional")
    w("mass held by the nodes a paper touches. coverage_ratio is reported as a")
    w("dispersion figure only; it counts distinct nodes and penalises the solver")
    w("for concentrating on dense content, which is what it is designed to do.")
    w("")

    for c in comparisons:
        w("-" * 78)
        w(f"{c.blueprint_id}   ({c.solver_mass.slots_total} slots)")
        w("-" * 78)
        w(f"  {'arm':<22}{'mass cov':>10}{'node cov':>10}{'fill':>8}{'alloc fid':>11}")
        for r in (c.solver_mass, c.solver_flat):
            w(f"  {r.arm:<22}{r.mass_coverage_ratio:>9.2%}{r.coverage_ratio:>10.4f}"
              f"{r.fill_ratio:>8.2f}{r.allocation_fidelity:>11.2f}")
        w(f"  {'baseline_naive (mean)':<22}{c.baseline_mean:>9.2%}"
          f"{statistics.mean(d.coverage_ratio for d in c.baseline_draws):>10.4f}"
          f"{statistics.mean(d.fill_ratio for d in c.baseline_draws):>8.2f}"
          f"{0.0:>11.2f}")
        w(f"  {'baseline stdev':<22}{c.baseline_stdev:>9.2%}")
        w("")
        w(f"  solver advantage over random : {c.advantage:.2f}x")
        w(f"  beats EVERY random draw      : {c.beats_every_draw}")
        w(f"  repetition term contributes  : {c.repetition_term_share:.1%} of that advantage"
          f"   (mass {c.solver_mass.mass_coverage_ratio:.2%}"
          f" vs flat {c.solver_flat.mass_coverage_ratio:.2%})")

        # allocation_fidelity is the guard against crediting the thesis for
        # work that span exhaustion is doing.
        if c.solver_mass.allocation_fidelity < 0.7:
            w(f"  !! only {c.solver_mass.allocation_fidelity:.0%} of the solver's slots were placed by")
            w(f"    mass; the rest came from span exhaustion. Discount accordingly.")
        w("")

    w("=" * 78)
    w("WHAT THIS DOES NOT SHOW")
    w("=" * 78)
    w("  The solver selects DENSER content by instructional_mass. Whether a paper")
    w("  built from denser content is a BETTER EXAM is a separate question that")
    w("  nothing here answers - instructional_mass is itself a heuristic.")
    w("  Nothing here evaluates the generated questions: not correctness, not")
    w("  pedagogy. Gate 2 was disproven on 2026-09-01 and no factuality check")
    w("  currently exists (P6-EVALUATION.md E8).")
    return "\n".join(out)


def to_json(comparisons: list[BlueprintComparison]) -> dict:
    return {
        "metric_note": (
            "mass_coverage_ratio is the headline. coverage_ratio is dispersion only — "
            "it favours the baseline on 2 of 4 blueprints because it counts distinct "
            "nodes and the solver concentrates on dense ones."
        ),
        "comparisons": [
            {
                "blueprint_id": c.blueprint_id,
                "solver_mass": asdict(c.solver_mass),
                "solver_flat": asdict(c.solver_flat),
                "baseline_mean_mass_coverage": c.baseline_mean,
                "baseline_stdev": c.baseline_stdev,
                "advantage_over_random": c.advantage,
                "beats_every_draw": c.beats_every_draw,
                "repetition_term_share_of_advantage": c.repetition_term_share,
                "baseline_draws": [asdict(d) for d in c.baseline_draws],
            }
            for c in comparisons
        ],
    }


def write_report(comparisons: list[BlueprintComparison], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt = output_dir / "p6_evaluation.txt"
    js = output_dir / "p6_evaluation.json"
    txt.write_text(format_report(comparisons), encoding="utf-8")
    js.write_text(json.dumps(to_json(comparisons), indent=2, sort_keys=True), encoding="utf-8")
    return txt, js
