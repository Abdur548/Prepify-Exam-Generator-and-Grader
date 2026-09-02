"""E8 reliability harness — the aggregation, tested without spending quota.

The harness itself makes live calls; this file does not. It builds synthetic
`RunOutcome`s and checks the statistics, because the arithmetic is where a
reliability report can quietly lie: reporting a gate that never ran as 100%,
counting a retry that never happened as a success, or averaging over runs that
died.
"""
from __future__ import annotations

import pytest

from coursegen.eval.reliability import ReliabilityReport, RunOutcome, _classify


def _run(idx: int, *, items=5, slots=5, gates=None, issues=(), flagged=(),
         stems=None, regen=0, error=None) -> RunOutcome:
    return RunOutcome(
        run=idx, items_generated=items, slots_total=slots, calls=2, tokens=1000,
        wall_clock=5.0, regeneration_passes=regen, flagged_slots=tuple(flagged),
        gates=gates if gates is not None else {},
        issues=tuple(issues), stems=stems or {}, error=error,
    )


class TestGateRates:
    def test_a_gate_that_never_ran_has_no_rate(self) -> None:
        """`evaluated == 0` must be None, never 1.0.

        Reporting a skipped gate as a perfect pass rate is the same conflation
        `new_gate_report` exists to prevent — "cleared everything" and "never ran"
        are different facts, and only one means the paper was checked.
        """
        o = _run(1, gates={"duplication": {"evaluated": 0, "passed": 0, "failed": 0}})
        assert o.gate_pass_rate("duplication") is None

    def test_a_missing_gate_has_no_rate(self) -> None:
        assert _run(1, gates={}).gate_pass_rate("schema") is None

    def test_rate_is_passed_over_evaluated(self) -> None:
        o = _run(1, gates={"schema": {"evaluated": 4, "passed": 3, "failed": 1}})
        assert o.gate_pass_rate("schema") == 0.75

    def test_skipped_gates_are_excluded_from_the_aggregate(self) -> None:
        """One measured run and one skipped must average the measured one alone."""
        rep = ReliabilityReport("bp", [
            _run(1, gates={"schema": {"evaluated": 2, "passed": 1, "failed": 1}}),
            _run(2, gates={"schema": {"evaluated": 0, "passed": 0, "failed": 0}}),
        ])
        assert rep.gate_rates("schema") == [0.5]


class TestFailedRuns:
    def test_a_died_run_is_kept_but_excluded_from_rates(self) -> None:
        """A crash is data for the taxonomy, not a reason to drop the run.

        It must not contribute to pass rates either — a run that never produced an
        item has no pass rate, and counting it as 0% would understate reliability
        just as dropping it silently would overstate it.
        """
        rep = ReliabilityReport("bp", [
            _run(1, gates={"schema": {"evaluated": 2, "passed": 2, "failed": 0}}),
            _run(2, error="ConnectError: network died"),
        ])
        assert len(rep.runs) == 2
        assert len(rep.completed_runs) == 1
        assert rep.gate_rates("schema") == [1.0]

    def test_all_runs_dead_yields_no_rates(self) -> None:
        rep = ReliabilityReport("bp", [_run(1, error="boom"), _run(2, error="boom")])
        assert rep.gate_rates("schema") == []
        assert rep.item_yield() == []


class TestRegenerationEffectiveness:
    def test_none_when_nothing_was_ever_flagged(self) -> None:
        """No failures means no retry happened; 1.0 would claim a success that never occurred."""
        rep = ReliabilityReport("bp", [_run(1), _run(2)])
        assert rep.regeneration_effectiveness() is None

    def test_rescued_items_are_those_flagged_first_and_clean_after(self) -> None:
        # Two items failed a gate; one still flagged after the retry -> 50% rescued.
        rep = ReliabilityReport("bp", [
            _run(1, regen=1,
                 issues=[("A-01", "mcq_hygiene", "band"), ("A-02", "mcq_hygiene", "band")],
                 flagged=["A-02"]),
        ])
        assert rep.regeneration_effectiveness() == 0.5

    def test_runs_without_a_retry_do_not_count(self) -> None:
        """A run with no regeneration pass cannot report retry effectiveness."""
        rep = ReliabilityReport("bp", [
            _run(1, regen=0, issues=[("A-01", "schema", "bad")], flagged=["A-01"]),
        ])
        assert rep.regeneration_effectiveness() is None


class TestDeterminism:
    def test_identical_stems_across_runs_is_full_determinism(self) -> None:
        rep = ReliabilityReport("bp", [
            _run(1, stems={"A-01": "What is BFS?", "A-02": "Define A*."}),
            _run(2, stems={"A-01": "What is BFS?", "A-02": "Define A*."}),
        ])
        assert rep.determinism() == 1.0

    def test_one_slot_varying_halves_the_score(self) -> None:
        rep = ReliabilityReport("bp", [
            _run(1, stems={"A-01": "What is BFS?", "A-02": "Define A*."}),
            _run(2, stems={"A-01": "What is BFS?", "A-02": "Explain A*."}),
        ])
        assert rep.determinism() == 0.5

    def test_whitespace_only_differences_do_not_count_as_variation(self) -> None:
        rep = ReliabilityReport("bp", [
            _run(1, stems={"A-01": "What is BFS?"}),
            _run(2, stems={"A-01": "  What is BFS?  "}),
        ])
        assert rep.determinism() == 1.0

    def test_none_when_no_items_were_produced(self) -> None:
        assert ReliabilityReport("bp", [_run(1, stems={})]).determinism() is None


class TestFailureTaxonomy:
    def test_failures_are_bucketed_by_gate_and_cause(self) -> None:
        rep = ReliabilityReport("bp", [
            _run(1, issues=[("A-01", "mcq_hygiene", "option lengths outside configured band"),
                            ("A-02", "mcq_hygiene", "option lengths outside configured band")]),
            _run(2, issues=[("A-01", "relevance", "score=-9.1 < RELEVANCE_FLOOR=-2.0")]),
        ])
        tax = rep.failure_taxonomy()
        assert tax["mcq_hygiene: option lengths outside band"] == 2
        assert tax["relevance: below relevance floor"] == 1

    @pytest.mark.parametrize("message,bucket", [
        ("score=-9.1 < RELEVANCE_FLOOR=-2.0", "below relevance floor"),
        ("cosine >= DEDUP_TAU=0.85", "duplicate of an earlier item"),
        ("option lengths outside configured band", "option lengths outside band"),
        ("MCQ requires options and correct_option", "mcq missing options"),
        ("No matching ItemSpec", "unknown slot_id"),
        ("1 validation error for GeneratedItem", "schema / other"),
    ])
    def test_message_classification(self, message: str, bucket: str) -> None:
        assert _classify(message) == bucket


class TestItemYield:
    def test_yield_is_delivered_over_asked(self) -> None:
        rep = ReliabilityReport("bp", [_run(1, items=3, slots=6)])
        assert rep.item_yield() == [0.5]

    def test_zero_slots_does_not_divide_by_zero(self) -> None:
        assert ReliabilityReport("bp", [_run(1, items=0, slots=0)]).item_yield() == []
