"""G3 — mastery and re-test cards, derived from answer history.

Pure functions over rows: no database, no clock. That is what lets a contest
re-derive history — change one effective mark and every number below follows —
and it is what these tests rely on: rows in, values out.
"""
from __future__ import annotations

import pytest

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.grade.mastery import (
    Card,
    cards,
    lecture_rollup,
    node_mastery,
    score,
    select_review,
    split_orphans,
)
from coursegen.grade.records import AnswerRow


def _row(seq, slot, node, marks, available=2, *, kind="paper", sheet="s1",
         response="x", retest_of=None) -> AnswerRow:
    return AnswerRow(
        attempt_id=f"a{seq}", seq=seq, sheet_id=sheet, sheet_kind=kind, slot_id=slot,
        node_id=node, item_type="mcq", marks_available=available, response=response,
        marks_awarded=marks, source="key" if marks is not None else None,
        confidence=None, reason=None, retest_of=retest_of, original=None,
    )


def _review(seq, origin_slot, node, marks, origin_sheet="s1", slot="R-01"):
    return _row(seq, slot, node, marks, kind="review", sheet=f"r{seq}",
                retest_of=(origin_sheet, origin_slot))


def _node(node_id, lecture, mass, path=None) -> CourseMapNode:
    return CourseMapNode(
        node_id=node_id, path=path or [lecture, node_id], source_file=lecture,
        page_span=(1, 2), token_count=100, chunk_ids=[f"c-{node_id}"], key_terms=[],
        flags=NodeFlags(), instructional_mass=mass,
    )


class TestScore:

    def test_a_blank_answer_scores_zero(self) -> None:
        assert score(_row(1, "A-01", "n1", None, response="")) == 0.0

    def test_an_unmarked_answer_is_no_evidence(self) -> None:
        assert score(_row(1, "B-01", "n1", None, response="something")) is None

    def test_a_mark_is_a_fraction_of_the_marks_available(self) -> None:
        assert score(_row(1, "B-01", "n1", 3, available=4)) == 0.75


class TestNodeMastery:

    def test_the_first_answer_sets_mastery(self) -> None:
        assert node_mastery([_row(1, "B-01", "n1", 1, available=2)])["n1"].mastery == 0.5

    def test_later_answers_move_it_halfway(self) -> None:
        rows = [_row(1, "A-01", "n1", 0), _row(2, "A-01", "n1", 2)]
        assert node_mastery(rows)["n1"].mastery == 0.5

    def test_recent_answers_weigh_more(self) -> None:
        rows = [_row(1, "A-01", "n1", 2), _row(2, "A-01", "n1", 0), _row(3, "A-01", "n1", 0)]
        m = node_mastery(rows)["n1"]
        assert m.mastery == 0.25 and m.answers == 3

    def test_the_rate_is_the_configured_one(self) -> None:
        assert config.MASTERY_ALPHA == 0.5

    def test_unmarked_answers_are_skipped(self) -> None:
        rows = [_row(1, "B-01", "n1", None, response="waiting"), _row(2, "A-01", "n1", 2)]
        m = node_mastery(rows)["n1"]
        assert (m.mastery, m.answers) == (1.0, 1)

    def test_a_blank_answer_counts_as_not_knowing(self) -> None:
        assert node_mastery([_row(1, "A-01", "n1", None, response="")])["n1"].mastery == 0.0

    def test_an_untested_node_has_no_mastery(self) -> None:
        """Untested is not weak. It is absent, never zero."""
        assert "n2" not in node_mastery([_row(1, "A-01", "n1", 2)])

    def test_review_answers_count_toward_the_node(self) -> None:
        rows = [_row(1, "A-01", "n1", 0), _review(2, "A-01", "n1", 2)]
        assert node_mastery(rows)["n1"].mastery == 0.5

    def test_order_follows_submission_not_input(self) -> None:
        rows = [_row(2, "A-01", "n1", 2), _row(1, "A-01", "n1", 0)]
        assert node_mastery(rows)["n1"].mastery == 0.5


class TestLectureRollup:

    NODES = [
        _node("n1", "03_search.pdf", 0.3), _node("n2", "03_search.pdf", 0.1),
        _node("n3", "03_search.pdf", 0.6), _node("n4", "06_csp.pdf", 1.0),
    ]

    def _rollup(self):
        mastery = node_mastery([_row(1, "A-01", "n1", 2), _row(1, "A-02", "n2", 0)])
        return {l.lecture: l for l in lecture_rollup(mastery, self.NODES)}

    def test_mastery_is_mass_weighted_over_tested_nodes(self) -> None:
        assert self._rollup()["03_search.pdf"].mastery == pytest.approx(0.75)

    def test_the_tested_share_is_by_mass(self) -> None:
        search = self._rollup()["03_search.pdf"]
        assert search.tested_share == pytest.approx(0.4)
        assert (search.nodes_total, search.nodes_tested) == (3, 2)

    def test_an_untested_lecture_reports_no_mastery(self) -> None:
        csp = self._rollup()["06_csp.pdf"]
        assert csp.mastery is None and csp.tested_share == 0.0

    def test_lectures_come_back_sorted(self) -> None:
        mastery = node_mastery([])
        assert [l.lecture for l in lecture_rollup(mastery, self.NODES)] == ["03_search.pdf", "06_csp.pdf"]

    def test_nodes_whose_mass_rounded_to_zero_still_report_mastery(self) -> None:
        """Masses are rounded when the course map is written, so a tiny section can
        carry 0.0. Weighting by it would divide by zero; the plain mean is used."""
        nodes = [_node("z1", "tiny.pdf", 0.0), _node("z2", "tiny.pdf", 0.0)]
        mastery = node_mastery([_row(1, "A-01", "z1", 2), _row(1, "A-02", "z2", 0)])
        (tiny,) = lecture_rollup(mastery, nodes)
        assert tiny.mastery == 0.5 and tiny.tested_share == 0.0

    def test_only_tested_nodes_are_listed_with_their_topic(self) -> None:
        nodes = self._rollup()["03_search.pdf"].nodes
        assert [n["node_id"] for n in nodes] == ["n1", "n2"]
        assert nodes[0]["topic"] == "03_search.pdf › n1" and nodes[0]["answers"] == 1


class TestCards:

    def test_a_miss_creates_a_card_due_at_the_next_review(self) -> None:
        (card,) = cards([_row(1, "A-01", "n1", 0)]).values()
        assert (card.origin, card.box, card.form, card.tests) == (("s1", "A-01"), 1, "identical", 1)
        assert card.is_due(latest_seq=1)

    def test_a_pass_creates_no_card(self) -> None:
        assert cards([_row(1, "A-01", "n1", 2)]) == {}

    def test_exactly_the_threshold_is_a_pass(self) -> None:
        """0.6 of the marks is not below RETEST_BELOW = 0.6."""
        assert cards([_row(1, "B-01", "n1", 3, available=5)]) == {}

    def test_a_blank_answer_creates_a_card(self) -> None:
        assert len(cards([_row(1, "A-01", "n1", None, response="")])) == 1

    def test_an_unmarked_answer_creates_nothing(self) -> None:
        assert cards([_row(1, "B-01", "n1", None, response="waiting")]) == {}

    def test_passing_a_retest_moves_up_a_box_and_waits_two_attempts(self) -> None:
        rows = [_row(1, "A-01", "n1", 0), _review(2, "A-01", "n1", 2)]
        card = cards(rows)[("s1", "A-01")]
        assert (card.box, card.passed_once, card.form, card.last_tested_seq) == (2, True, "fresh", 2)
        assert not card.is_due(latest_seq=3)
        assert card.is_due(latest_seq=4)

    def test_passing_box_four_retires_the_card(self) -> None:
        rows = [_row(1, "A-01", "n1", 0)] + [_review(s, "A-01", "n1", 2) for s in (2, 3, 4, 5)]
        card = cards(rows)[("s1", "A-01")]
        assert card.retired and not card.is_due(latest_seq=100)

    def test_a_fail_after_a_pass_returns_to_box_one_with_new_wording(self) -> None:
        rows = [_row(1, "A-01", "n1", 0), _review(2, "A-01", "n1", 2), _review(4, "A-01", "n1", 0)]
        card = cards(rows)[("s1", "A-01")]
        assert (card.box, card.form, card.tests) == (1, "fresh", 3)

    def test_retaking_the_paper_and_passing_counts_as_a_test(self) -> None:
        """Review Focus 1."""
        rows = [_row(1, "A-01", "n1", 0), _row(2, "A-01", "n1", 2)]
        assert cards(rows)[("s1", "A-01")].box == 2

    def test_contesting_a_miss_to_full_marks_means_there_was_no_card(self) -> None:
        """The effective mark is what the rows carry, so history re-derives itself."""
        assert cards([_row(1, "A-01", "n1", 2)]) == {}

    def test_a_retest_of_a_card_that_no_longer_exists_is_ignored(self) -> None:
        assert cards([_review(2, "A-01", "n1", 0)]) == {}

    def test_an_unmarked_retest_changes_nothing(self) -> None:
        rows = [_row(1, "B-01", "n1", 0, available=4),
                _review(2, "B-01", "n1", None) ]
        card = cards(rows)[("s1", "B-01")]
        assert (card.box, card.tests, card.last_tested_seq) == (1, 1, 1)

    def test_failing_a_retired_question_again_revives_it(self) -> None:
        rows = ([_row(1, "A-01", "n1", 0)] + [_review(s, "A-01", "n1", 2) for s in (2, 3, 4, 5)]
                + [_row(6, "A-01", "n1", 0)])
        card = cards(rows)[("s1", "A-01")]
        assert (card.retired, card.box) == (False, 1)

    def test_the_gaps_are_the_configured_ones(self) -> None:
        assert config.LEITNER_GAPS == {1: 0, 2: 2, 3: 4, 4: 8}


def _card(slot, node, box=1, last=1, retired=False) -> Card:
    return Card(origin=("s1", slot), node_id=node, box=box, retired=retired,
                passed_once=box > 1, last_tested_seq=last, tests=box)


class TestSelectReview:

    def test_lower_boxes_come_first(self) -> None:
        picked = select_review({c.origin: c for c in [_card("A", "n1", box=2, last=1), _card("B", "n2", box=1, last=9)]},
                               latest_seq=9, mass_by_node={})
        assert [c.origin[1] for c in picked] == ["B", "A"]

    def test_within_a_box_the_most_overdue_comes_first(self) -> None:
        cs = [_card("A", "n1", last=8), _card("B", "n2", last=2)]
        picked = select_review({c.origin: c for c in cs}, latest_seq=9, mass_by_node={})
        assert [c.origin[1] for c in picked] == ["B", "A"]

    def test_then_the_heavier_node(self) -> None:
        cs = [_card("A", "n1"), _card("B", "n2")]
        picked = select_review({c.origin: c for c in cs}, latest_seq=1, mass_by_node={"n1": 0.1, "n2": 0.9})
        assert [c.origin[1] for c in picked] == ["B", "A"]

    def test_one_question_per_node(self) -> None:
        cs = [_card("A", "n1"), _card("B", "n1"), _card("C", "n2")]
        picked = select_review({c.origin: c for c in cs}, latest_seq=1, mass_by_node={})
        assert [c.node_id for c in picked] == ["n1", "n2"]

    def test_not_due_and_retired_cards_are_left_out(self) -> None:
        cs = [_card("A", "n1", box=2, last=9), _card("B", "n2", retired=True)]
        assert select_review({c.origin: c for c in cs}, latest_seq=9, mass_by_node={}) == []

    def test_the_set_is_capped(self) -> None:
        cs = [_card(f"A{i}", f"n{i}") for i in range(15)]
        picked = select_review({c.origin: c for c in cs}, latest_seq=1, mass_by_node={})
        assert len(picked) == config.REVIEW_MAX_ITEMS == 10


class TestOrphans:

    def test_rows_for_material_no_longer_ingested_are_dropped_and_counted(self) -> None:
        """Review Focus 5."""
        rows = [_row(1, "A-01", "n1", 2), _row(1, "A-02", "gone", 0), _row(2, "A-02", "gone", 0)]
        kept, orphans = split_orphans(rows, {"n1"})
        assert [r.node_id for r in kept] == ["n1"] and orphans == 2
