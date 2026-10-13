"""G6 — the written-marking agreement harness (§13.1), aggregation only.

The live run spends quota and needs the team's hand marks; these tests pin the
arithmetic it reports and the two verdicts it can reach, so the numbers mean the
same thing whenever someone finally runs it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from coursegen.eval.marking import (
    SHIPPED_SET,
    HandMarked,
    aggregate,
    format_report,
    load_set,
)
from coursegen.grade.records import WrittenEntry, WrittenMark


def _hm(slot: str, hand: int, marks: int = 4) -> HandMarked:
    return HandMarked(
        entry=WrittenEntry(slot_id=slot, item_type="short", marks_available=marks, stem="Q?",
                           model_answer="A.", explanation="E.", passage="P.",
                           from_material=True, response="R."),
        hand_mark=hand, label="full",
    )


def _m(slot: str, marks, conf="high") -> WrittenMark:
    return WrittenMark(slot, marks, conf if marks is not None else None, "r")


class TestTheShippedSet:

    def test_it_parses_and_holds_thirty_answers(self) -> None:
        rows = load_set(SHIPPED_SET, require_hand_marks=False)
        assert len(rows) == 30
        assert {r.label for r in rows} == {"full", "partial", "wrong", "vague", "adversarial"}
        assert all(0 < r.entry.marks_available for r in rows)

    def test_it_refuses_to_run_without_the_teams_marks(self) -> None:
        """The measurement is only as meaningful as the judgement behind it."""
        with pytest.raises(ValueError, match="hand mark"):
            load_set(SHIPPED_SET)

    def test_a_hand_mark_outside_the_question_is_refused(self, tmp_path: Path) -> None:
        text = SHIPPED_SET.read_text(encoding="utf-8").splitlines()
        header, first = text[0], text[1]
        bad = tmp_path / "bad.csv"
        bad.write_text(header + "\n" + first.rsplit(",", 1)[0] + ",99\n", encoding="utf-8")
        with pytest.raises(ValueError, match="99"):
            load_set(bad)


class TestAggregate:

    def test_within_one_and_mean_absolute_difference(self) -> None:
        rows = [_hm("a", 4), _hm("b", 0), _hm("c", 2), _hm("d", 4)]
        runs = [[_m("a", 4), _m("b", 2), _m("c", 3), _m("d", 1)]]
        out = aggregate(rows, runs)
        assert out["within_one"] == pytest.approx(0.5)       # a (0), c (1) within; b (2), d (3) not
        assert out["mean_abs_diff"] == pytest.approx(1.5)
        assert out["agreement_verdict"] == "FALSIFIED"

    def test_agreement_holds_at_eighty_percent(self) -> None:
        rows = [_hm(s, 2) for s in "abcde"]
        runs = [[_m("a", 2), _m("b", 3), _m("c", 1), _m("d", 2), _m("e", 4)]]
        assert aggregate(rows, runs)["agreement_verdict"] == "HOLDS"

    def test_spread_is_measured_across_runs(self) -> None:
        rows = [_hm("a", 2), _hm("b", 2)]
        runs = [[_m("a", 2), _m("b", 1)], [_m("a", 2), _m("b", 4)], [_m("a", 2), _m("b", 2)]]
        out = aggregate(rows, runs)
        assert out["max_spread"] == 3 and out["mean_spread"] == pytest.approx(1.5)

    def test_unmarked_answers_are_counted_not_scored(self) -> None:
        rows = [_hm("a", 2), _hm("b", 2)]
        out = aggregate(rows, [[_m("a", 2), _m("b", None)]])
        assert out["unmarked"] == 1 and out["within_one"] == 1.0

    def test_the_badge_must_carry_information(self) -> None:
        """Low-confidence marks must disagree MORE often than high-confidence ones."""
        rows = [_hm(s, 2) for s in "abcd"]
        informative = [[_m("a", 2, "high"), _m("b", 2, "high"), _m("c", 4, "low"), _m("d", 0, "low")]]
        useless = [[_m("a", 4, "high"), _m("b", 0, "high"), _m("c", 2, "low"), _m("d", 2, "low")]]
        assert aggregate(rows, informative)["badge_verdict"] == "INFORMATIVE"
        assert aggregate(rows, useless)["badge_verdict"] == "REMOVE THE BADGE"

    def test_a_badge_never_used_cannot_be_judged(self) -> None:
        rows = [_hm("a", 2)]
        assert aggregate(rows, [[_m("a", 2, "high")]])["badge_verdict"] == "NOT JUDGEABLE"

    def test_the_report_names_both_verdicts(self) -> None:
        rows = [_hm("a", 2)]
        text = format_report(aggregate(rows, [[_m("a", 2)]]), runs=1)
        assert "agreement" in text.lower() and "badge" in text.lower()
