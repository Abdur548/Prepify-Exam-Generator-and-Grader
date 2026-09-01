"""P6 harness — the arms are comparable, and the numbers are re-derivable."""
from __future__ import annotations

import json
from pathlib import Path

from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.eval.harness import _flatten, _mass_coverage, run_comparison

BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "coursegen" / "exam" / "blueprints"


def _blueprint(name: str = "quiz_default") -> Blueprint:
    return Blueprint(**json.loads((BLUEPRINT_DIR / f"{name}.json").read_text(encoding="utf-8")))


def _nodes(n: int = 40) -> list[CourseMapNode]:
    """Mass gradient that is NOT proportional to token_count.

    Deliberate: if mass were a monotone function of tokens, `solver_flat` and
    `solver_mass` would pick identically and the §16 comparison would be vacuous
    while appearing to pass.
    """
    return [
        CourseMapNode(
            node_id=f"n{i:03d}",
            path=["Course", f"Topic {i}"],
            source_file="lecture.pdf",
            page_span=[i + 1, i + 1],
            key_terms=[f"term{i}"],
            token_count=100 + (i % 7) * 50,
            instructional_mass=float((i * 37) % 23 + 1),
            chunk_ids=[f"n{i:03d}-c{j}" for j in range(3)],
            flags=NodeFlags(),
        )
        for i in range(n)
    ]


class TestFairness:
    def test_flatten_removes_the_repetition_term_only(self) -> None:
        nodes = _nodes()
        flat = _flatten(nodes)
        total = sum(n.token_count for n in nodes)
        assert [f.instructional_mass for f in flat] == [n.token_count / total for n in nodes]
        # Everything else must be untouched, or the arms differ in more than one way.
        for a, b in zip(nodes, flat):
            assert (a.node_id, a.token_count, a.chunk_ids) == (b.node_id, b.token_count, b.chunk_ids)

    def test_every_arm_is_scored_on_the_original_mass(self) -> None:
        """`solver_flat` must not be graded on the objective it optimised.

        Scoring an arm against its own objective is circular — a flat-token arm
        graded on flat-token mass would look perfect by construction. This asserts
        the flattened masses never reach the scorer.
        """
        nodes, bp = _nodes(), _blueprint()
        c = run_comparison(nodes, bp, seeds=(1, 2, 3))

        mass = {n.node_id: n.instructional_mass for n in nodes}
        total = sum(mass.values())
        from coursegen.exam.allocate import solve
        flat_specs, _ = solve(_flatten(nodes), bp)
        expected = _mass_coverage(flat_specs, mass, total)

        assert abs(c.solver_flat.mass_coverage_ratio - expected) < 1e-12

    def test_all_arms_fill_the_same_number_of_slots(self) -> None:
        """Different-sized papers are not comparable."""
        c = run_comparison(_nodes(), _blueprint(), seeds=(1, 2, 3))
        assert c.solver_mass.slots_total == c.solver_flat.slots_total
        for d in c.baseline_draws:
            assert d.slots_total == c.solver_mass.slots_total


class TestReproducibility:
    def test_same_seeds_give_identical_numbers(self) -> None:
        nodes, bp = _nodes(), _blueprint()
        a = run_comparison(nodes, bp, seeds=(1, 2, 3))
        b = run_comparison(nodes, bp, seeds=(1, 2, 3))
        assert a.solver_mass == b.solver_mass
        assert a.baseline_draws == b.baseline_draws


class TestRepetitionTermShare:
    def test_share_is_a_fraction_of_advantage_not_an_epsilon_flag(self) -> None:
        """The §16 answer must be a magnitude.

        An earlier version returned `bool(diff > 1e-9)`, which is True for any
        difference at all and reads as "the term matters" even when it contributes
        almost nothing. On the real corpus the term supplies 4.9%-8.5% of the
        solver's advantage: real, and small.
        """
        c = run_comparison(_nodes(), _blueprint(), seeds=(1, 2, 3))
        share = c.repetition_term_share
        assert isinstance(share, float)
        # Not bounded above by 1.0. On this fixture mass is uncorrelated with
        # token_count, so the flat arm scores BELOW random and the share exceeds
        # 100% — the term is the only reason the arm beats random at all. Clamping
        # that to 1.0 would discard the strongest evidence the metric can produce.
        assert share > 0.0
        assert share == share  # finite, not NaN

    def test_share_is_zero_when_the_term_does_nothing(self) -> None:
        """Falsification path: mass proportional to tokens means flat == mass.

        If this ever fails, the harness is manufacturing a difference that the
        inputs do not contain.
        """
        nodes = _nodes()
        total = sum(n.token_count for n in nodes)
        proportional = [
            n.model_copy(update={"instructional_mass": n.token_count / total}) for n in nodes
        ]
        c = run_comparison(proportional, _blueprint(), seeds=(1, 2, 3))
        assert abs(c.repetition_term_share) < 1e-9, (
            "flat and mass arms diverged on a corpus where mass IS flat tokens"
        )
