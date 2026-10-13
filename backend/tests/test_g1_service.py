"""G1 — submitting answers, marking objective questions, results, contests.

The service is what the API calls. Every rule a student can observe lives here,
so these tests are written against what the results screen will show: which mark
counts, what the totals claim, and what is never claimed.
"""
from __future__ import annotations

import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption, SourceRef
from coursegen.exam.paper import build_paper
from coursegen.grade.mark import mark_objective, normalise_label
from coursegen.grade.records import InvalidSubmission, MarkingOutcome, UnknownSheet, WrittenMark
from coursegen.grade.service import contest, results, submit_attempt, written_entries
from coursegen.grade.sheets import archive_paper
from coursegen.grade.store import Store

NOW = "2026-10-13T10:00:00"


def _paper_sheet_id() -> str:
    """A1, A2: MCQ (key B), 2 marks. B1: short, 4 marks. C1: synthesis long, 10 marks."""
    blueprint = Blueprint(
        blueprint_id="bp", title="Paper", total_marks=18, duration_minutes=60,
        sections=[
            SectionSpec(section_id="A", title="MCQ", item_type="mcq", count=2,
                        marks_each=2, bloom=["remember"]),
            SectionSpec(section_id="B", title="Short", item_type="short", count=1,
                        marks_each=4, bloom=["understand"]),
            SectionSpec(section_id="C", title="Build", item_type="long", count=1,
                        marks_each=10, bloom=["create"], grounding="synthesis"),
        ],
    )
    specs = [
        ItemSpec(slot_id="A-01", item_type="mcq", marks=2, bloom="remember", node_id="n1",
                 span_ids=["c1"], eligibility=[], spec_hash="h1"),
        ItemSpec(slot_id="A-02", item_type="mcq", marks=2, bloom="remember", node_id="n2",
                 span_ids=["c2"], eligibility=[], spec_hash="h2"),
        ItemSpec(slot_id="B-01", item_type="short", marks=4, bloom="understand", node_id="n3",
                 span_ids=["c3"], eligibility=[], spec_hash="h3"),
        ItemSpec(slot_id="C-01", item_type="long", marks=10, bloom="create", node_id="n4",
                 span_ids=["c4"], eligibility=[], spec_hash="h4", grounding="synthesis"),
    ]
    mcq = lambda slot: GeneratedItem(  # noqa: E731
        slot_id=slot, stem=f"Which is right ({slot})?",
        options=[MCQOption(label="A", text="wrong"), MCQOption(label="B", text="right")],
        correct_option="B", model_answer="B. right", explanation="B is right.",
        source_ref=SourceRef(file="03_search.pdf", pages=[30]),
    )
    written = lambda slot, ans: GeneratedItem(  # noqa: E731
        slot_id=slot, stem=f"Explain ({slot}).", options=None, correct_option=None,
        model_answer=ans, explanation="Why.", source_ref=SourceRef(file="03_search.pdf", pages=[31]),
    )
    coverage = CoverageReport(
        blueprint_id="bp", nodes_total=4, nodes_covered=4, coverage_ratio=1.0,
        slots_total=4, slots_filled=4, fill_ratio=1.0, slots_by_mass=4,
        slots_by_fallthrough=0, allocation_fidelity=1.0, mass_covered=1.0,
        unfilled_slots=[], warnings=[], per_node=[],
    )
    paper = build_paper(
        items=[mcq("A-01"), mcq("A-02"), written("B-01", "BFS uses a queue."),
               written("C-01", "A game tree.")],
        specs=specs, blueprint=blueprint, coverage=coverage, title="Paper",
        span_text_by_id={"c3": "Breadth-first search expands nodes using a FIFO queue."},
    )
    return archive_paper(paper, specs, now=NOW)["sheet_id"]


@pytest.fixture
def store():
    with Store() as s:
        yield s


def _item(result: dict, slot: str) -> dict:
    return next(i for i in result["items"] if i["slot_id"] == slot)


class TestObjectiveMarking:

    def test_labels_are_compared_after_trimming_and_upper_casing(self) -> None:
        assert normalise_label("  b ") == "B"

    def test_the_right_label_earns_full_marks(self, store) -> None:
        sheet = store.get_sheet(_paper_sheet_id())
        a01 = sheet.items[0]
        assert mark_objective(a01, "B") == 2
        assert mark_objective(a01, " b\n") == 2
        assert mark_objective(a01, "A") == 0
        assert mark_objective(a01, "") is None

    def test_a_written_item_is_not_marked_by_key(self, store) -> None:
        sheet = store.get_sheet(_paper_sheet_id())
        with pytest.raises(ValueError):
            mark_objective(sheet.items[2], "anything")


class TestSubmit:

    def test_objective_answers_are_marked_by_the_key(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "B", "A-02": "A"}, now=NOW)
        assert (_item(r, "A-01")["marks_awarded"], _item(r, "A-01")["source"]) == (2, "key")
        assert _item(r, "A-02")["marks_awarded"] == 0

    def test_a_blank_objective_answer_is_unanswered_not_wrong(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "B"}, now=NOW)
        a02 = _item(r, "A-02")
        assert a02["status"] == "unanswered" and a02["marks_awarded"] is None

    def test_without_a_marker_written_answers_wait_for_the_student(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "a queue"}, now=NOW)
        assert _item(r, "B-01")["status"] == "unmarked"
        assert r["status"] == "ok"
        assert "Written answers are not marked yet — mark them yourself." in r["warnings"]

    def test_an_unavailable_marker_is_a_degraded_result(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "a queue"}, now=NOW,
                           unavailable="Busy generating a paper.")
        assert r["status"] == "degraded" and r["warnings"] == ["Busy generating a paper."]

    def test_marks_from_a_marker_are_recorded_as_the_models(self, store) -> None:
        def marker(entries):
            return MarkingOutcome([WrittenMark(e.slot_id, 3, "high", "Names the queue.")
                                   for e in entries])
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "a queue"}, now=NOW, marker=marker)
        b01 = _item(r, "B-01")
        assert (b01["marks_awarded"], b01["source"], b01["confidence"], b01["reason"]) == (
            3, "model", "high", "Names the queue.")

    def test_a_marker_is_only_sent_non_blank_written_answers(self, store) -> None:
        seen = []
        def marker(entries):
            seen.extend(entries)
            return MarkingOutcome([])
        submit_attempt(store, _paper_sheet_id(), {"A-01": "B", "B-01": "q", "C-01": " "},
                       now=NOW, marker=marker)
        assert [e.slot_id for e in seen] == ["B-01"]

    def test_the_marker_sees_the_passage_only_for_material_questions(self, store) -> None:
        seen = []
        def marker(entries):
            seen.extend(entries)
            return MarkingOutcome([])
        submit_attempt(store, _paper_sheet_id(), {"B-01": "q", "C-01": "a tree"}, now=NOW, marker=marker)
        b01, c01 = seen
        assert b01.passage == "Breadth-first search expands nodes using a FIFO queue."
        assert b01.model_answer == "BFS uses a queue." and b01.marks_available == 4
        assert c01.passage is None and c01.from_material is False

    def test_an_unmarked_verdict_is_explained_not_stored(self, store) -> None:
        def marker(entries):
            return MarkingOutcome([WrittenMark("B-01", None, None, "The marker returned an invalid mark.")])
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW, marker=marker)
        assert _item(r, "B-01")["status"] == "unmarked"
        assert "B-01: The marker returned an invalid mark." in r["warnings"]

    def test_a_degraded_marking_run_is_reported(self, store) -> None:
        def marker(entries):
            return MarkingOutcome([], degraded="Marking stopped: today's limit was reached.")
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW, marker=marker)
        assert r["status"] == "degraded"
        assert "Marking stopped: today's limit was reached." in r["warnings"]

    def test_the_longest_allowed_answer_is_accepted(self, store) -> None:
        """Review Focus 4: the boundary itself is allowed."""
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "x" * config.MAX_ANSWER_CHARS}, now=NOW)
        assert len(_item(r, "B-01")["response"]) == config.MAX_ANSWER_CHARS

    def test_one_character_more_is_refused_and_nothing_is_saved(self, store) -> None:
        sheet_id = _paper_sheet_id()
        with pytest.raises(InvalidSubmission, match=str(config.MAX_ANSWER_CHARS)):
            submit_attempt(store, sheet_id, {"B-01": "x" * (config.MAX_ANSWER_CHARS + 1)}, now=NOW)
        assert store.latest_seq() == 0

    def test_an_unknown_sheet_raises(self, store) -> None:
        with pytest.raises(UnknownSheet):
            submit_attempt(store, "nope", {}, now=NOW)


class TestResults:

    def test_totals_never_count_an_unmarked_answer_as_zero(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "B", "B-01": "q"}, now=NOW)
        assert r["totals"] == {
            "marks_awarded": 2,
            "marks_marked": 2 + 2 + 10,     # A-01 marked; A-02, C-01 unanswered (0 of theirs)
            "marks_available": 18,
            "unmarked": 1,
            "unanswered": 2,
        }

    def test_feedback_points_at_the_page_to_revise(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "A"}, now=NOW)
        fb = _item(r, "A-01")["feedback"]
        assert fb["revise"] == {"file": "03_search.pdf", "pages": [30]}
        assert fb["correct_option"] == "B" and fb["model_answer"] == "B. right"
        assert fb["stem"] == "Which is right (A-01)?"

    def test_a_synthesis_question_points_at_no_page(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {}, now=NOW)
        fb = _item(r, "C-01")["feedback"]
        assert fb["from_material"] is False and fb["revise"] is None
        assert fb["source_excerpt"] is None

    def test_results_can_be_read_back_unchanged(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "B"}, now=NOW)
        assert results(store, r["attempt_id"])["items"] == r["items"]


class TestContest:

    def test_a_contest_becomes_the_mark_and_keeps_the_original(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"A-01": "A"}, now=NOW)
        item = contest(store, r["attempt_id"], "A-01", 2, now=NOW)
        assert (item["marks_awarded"], item["source"]) == (2, "student")
        assert item["original"] == {"marks_awarded": 0, "source": "key"}
        assert item["objective_contest"] is True

    def test_marking_your_own_unmarked_answer_has_no_original(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW)
        item = contest(store, r["attempt_id"], "B-01", 3, now=NOW)
        assert item["original"] is None and item["objective_contest"] is False

    def test_an_unanswered_question_cannot_be_contested(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {}, now=NOW)
        with pytest.raises(InvalidSubmission, match="no answer"):
            contest(store, r["attempt_id"], "B-01", 1, now=NOW)

    def test_a_contest_out_of_range_is_refused(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW)
        with pytest.raises(InvalidSubmission):
            contest(store, r["attempt_id"], "B-01", 5, now=NOW)

    def test_a_contest_on_an_unknown_slot_is_refused(self, store) -> None:
        r = submit_attempt(store, _paper_sheet_id(), {}, now=NOW)
        with pytest.raises(InvalidSubmission):
            contest(store, r["attempt_id"], "Z-99", 1, now=NOW)


class TestWrittenEntries:

    def test_only_unmarked_skips_any_answer_that_has_a_mark(self, store) -> None:
        """Review Focus 3, at the service level: a self-mark must not be re-sent."""
        r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q", "C-01": "t"}, now=NOW)
        contest(store, r["attempt_id"], "B-01", 2, now=NOW)
        entries = written_entries(store, r["attempt_id"], only_unmarked=True)
        assert [e.slot_id for e in entries] == ["C-01"]
