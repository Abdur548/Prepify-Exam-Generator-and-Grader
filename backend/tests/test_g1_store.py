"""G1 — the practice store: sheets, attempts and append-only marks.

These pin what the rest of MVP 2 stands on. Mastery, re-test cards and the
adaptive paper are all DERIVED from the rows this store keeps, so a store that
loses a row, overwrites a mark, or accepts an impossible one corrupts every
number downstream without any of them looking wrong.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from coursegen import config
from coursegen.grade.records import (
    InvalidSubmission,
    SheetItem,
    SheetRecord,
    UnknownAttempt,
    UnknownSheet,
)
from coursegen.grade.store import SCHEMA_VERSION, Store

NOW = "2026-10-13T10:00:00"


def _sheet(
    sheet_id: str = "s1",
    kind: str = "paper",
    slots=(("A-01", "mcq", 2, "B", "n1"), ("B-01", "short", 4, None, "n2")),
) -> SheetRecord:
    items = [
        SheetItem(
            slot_id=slot, node_id=node, span_ids=[f"c-{slot}"],
            spec_hash=f"h-{sheet_id}-{slot}", item_type=item_type, bloom="remember",
            marks=marks, correct_option=correct, from_material=True,
            spec={"slot_id": slot, "node_id": node},
        )
        for slot, item_type, marks, correct, node in slots
    ]
    document = {
        "title": "T",
        "sheet_id": sheet_id,
        "sections": [{
            "section_id": "A",
            "items": [
                {"slot_id": slot, "filled": True, "stem": f"Stem {sheet_id} {slot}?"}
                for slot, *_ in slots
            ] + [{"slot_id": "Z-99", "filled": False, "marks": 1, "item_type": "mcq"}],
        }],
    }
    return SheetRecord(
        sheet_id=sheet_id, kind=kind, blueprint_id="bp" if kind == "paper" else None,
        title="T", created_at=NOW, document=document, items=items,
    )


@pytest.fixture
def store():
    with Store() as s:
        yield s


class TestIsolation:

    def test_the_suite_never_touches_the_real_database(self, tmp_path: Path) -> None:
        """Autouse: a test that forgets to redirect must not write a student's answers."""
        assert config.db_path().parent == tmp_path


class TestSchema:

    def test_a_new_database_is_created_at_the_current_version(self, store) -> None:
        version = sqlite3.connect(config.db_path()).execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION == 1

    def test_reopening_keeps_what_was_written(self) -> None:
        with Store() as s:
            s.put_sheet(_sheet())
        with Store() as s:
            assert s.get_sheet("s1").title == "T"

    def test_a_database_from_newer_code_is_refused(self) -> None:
        """Opening it would mean reading columns this code does not know about."""
        with Store():
            pass
        conn = sqlite3.connect(config.db_path())
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="newer"):
            Store()


class TestSheets:

    def test_round_trip_is_exact(self, store) -> None:
        sheet = _sheet()
        store.put_sheet(sheet)
        assert store.get_sheet("s1") == sheet

    def test_archiving_the_same_sheet_twice_is_a_no_op(self, store) -> None:
        store.put_sheet(_sheet())
        later = _sheet()
        later = SheetRecord(**{**later.__dict__, "created_at": "2027-01-01T00:00:00"})
        store.put_sheet(later)
        assert store.get_sheet("s1").created_at == NOW

    def test_an_unknown_sheet_raises(self, store) -> None:
        with pytest.raises(UnknownSheet):
            store.get_sheet("nope")

    def test_an_older_paper_stays_answerable_after_a_newer_one(self, store) -> None:
        """Review Focus 2. Answers resolve against the archive, never paper.json."""
        store.put_sheet(_sheet("old"))
        store.put_sheet(_sheet("new"))
        attempt_id, _ = store.add_attempt("old", {"A-01": "B"}, NOW)
        assert store.attempt_sheet_id(attempt_id) == "old"


class TestAttempts:

    def test_seq_counts_submissions_in_order(self, store) -> None:
        store.put_sheet(_sheet())
        seqs = [store.add_attempt("s1", {}, NOW)[1] for _ in range(3)]
        assert seqs == [1, 2, 3]
        assert store.latest_seq() == 3

    def test_retaking_a_paper_is_a_separate_attempt(self, store) -> None:
        """Review Focus 1."""
        store.put_sheet(_sheet())
        first, _ = store.add_attempt("s1", {"A-01": "A"}, NOW)
        second, seq = store.add_attempt("s1", {"A-01": "B"}, NOW)
        assert first != second and seq == 2
        assert [r.response for r in store.attempt_rows(first) if r.slot_id == "A-01"] == ["A"]
        assert [r.response for r in store.attempt_rows(second) if r.slot_id == "A-01"] == ["B"]

    def test_every_answerable_slot_gets_a_row(self, store) -> None:
        """A slot the student skipped is still a fact: they did not answer it."""
        store.put_sheet(_sheet())
        attempt_id, _ = store.add_attempt("s1", {"A-01": "B"}, NOW)
        rows = {r.slot_id: r for r in store.attempt_rows(attempt_id)}
        assert set(rows) == {"A-01", "B-01"}          # the unfilled Z-99 is not answerable
        assert rows["B-01"].response == ""
        assert rows["B-01"].status == "unanswered"

    def test_an_unknown_slot_is_rejected_and_nothing_is_written(self, store) -> None:
        store.put_sheet(_sheet())
        with pytest.raises(InvalidSubmission, match="Z-99"):
            store.add_attempt("s1", {"Z-99": "A"}, NOW)
        assert store.latest_seq() == 0

    def test_an_unknown_sheet_is_rejected(self, store) -> None:
        with pytest.raises(UnknownSheet):
            store.add_attempt("nope", {}, NOW)

    def test_text_round_trips_byte_for_byte(self, store) -> None:
        store.put_sheet(_sheet())
        text = "λ → x\nsecond line ✓  \t"
        attempt_id, _ = store.add_attempt("s1", {"B-01": text}, NOW)
        assert [r.response for r in store.attempt_rows(attempt_id) if r.slot_id == "B-01"] == [text]


class TestMarks:

    def _attempt(self, store, responses=None) -> str:
        store.put_sheet(_sheet())
        return store.add_attempt("s1", responses or {"A-01": "B", "B-01": "an answer"}, NOW)[0]

    def test_the_latest_mark_is_the_one_that_counts(self, store) -> None:
        attempt_id = self._attempt(store)
        store.add_mark(attempt_id, "B-01", 1, "model", confidence="low", reason="thin", at=NOW)
        store.add_mark(attempt_id, "B-01", 3, "student", at=NOW)
        row = next(r for r in store.attempt_rows(attempt_id) if r.slot_id == "B-01")
        assert (row.marks_awarded, row.source, row.status) == (3, "student", "marked")
        assert row.original == (1, "model")

    def test_an_uncontested_mark_has_no_original(self, store) -> None:
        attempt_id = self._attempt(store)
        store.add_mark(attempt_id, "A-01", 2, "key", at=NOW)
        row = next(r for r in store.attempt_rows(attempt_id) if r.slot_id == "A-01")
        assert row.original is None

    def test_an_answer_without_a_mark_is_unmarked(self, store) -> None:
        attempt_id = self._attempt(store)
        row = next(r for r in store.attempt_rows(attempt_id) if r.slot_id == "B-01")
        assert (row.status, row.marks_awarded) == ("unmarked", None)

    def test_whitespace_alone_is_unanswered(self, store) -> None:
        attempt_id = self._attempt(store, {"B-01": "  \n "})
        row = next(r for r in store.attempt_rows(attempt_id) if r.slot_id == "B-01")
        assert row.status == "unanswered"

    @pytest.mark.parametrize("marks", [5, -1])
    def test_an_impossible_mark_is_refused(self, store, marks: int) -> None:
        attempt_id = self._attempt(store)
        with pytest.raises(InvalidSubmission):
            store.add_mark(attempt_id, "B-01", marks, "model", at=NOW)

    def test_a_bool_is_not_a_mark(self, store) -> None:
        attempt_id = self._attempt(store)
        with pytest.raises(InvalidSubmission):
            store.add_mark(attempt_id, "A-01", True, "key", at=NOW)

    def test_the_database_itself_refuses_an_over_bound_mark(self, store) -> None:
        """Defence in depth: a direct insert bypassing add_mark still cannot store it."""
        attempt_id = self._attempt(store)
        conn = sqlite3.connect(config.db_path())
        with pytest.raises(sqlite3.IntegrityError, match="exceeds"):
            conn.execute(
                "INSERT INTO marks (attempt_id, slot_id, marks_awarded, source, at) "
                "VALUES (?, 'B-01', 9, 'model', ?)", (attempt_id, NOW),
            )
        conn.close()

    def test_a_mark_for_an_unknown_attempt_raises(self, store) -> None:
        with pytest.raises(UnknownAttempt):
            store.add_mark("nope", "A-01", 1, "key", at=NOW)

    def test_an_unknown_source_is_refused(self, store) -> None:
        attempt_id = self._attempt(store)
        with pytest.raises(InvalidSubmission):
            store.add_mark(attempt_id, "A-01", 1, "teacher", at=NOW)


class TestHistory:

    def test_all_rows_come_back_in_submission_order(self, store) -> None:
        store.put_sheet(_sheet())
        store.add_attempt("s1", {"A-01": "B"}, NOW)
        store.add_attempt("s1", {"A-01": "C"}, NOW)
        rows = store.all_answer_rows()
        assert [(r.seq, r.slot_id) for r in rows] == [(1, "A-01"), (1, "B-01"), (2, "A-01"), (2, "B-01")]
        assert rows[0].sheet_kind == "paper" and rows[0].node_id == "n1"

    def test_review_rows_carry_their_origin(self, store) -> None:
        store.put_sheet(_sheet())
        review = _sheet("r1", kind="review", slots=(("R-01", "mcq", 2, "A", "n1"),))
        item = SheetItem(**{**review.items[0].__dict__, "retest_of": ("s1", "A-01"), "form": "identical"})
        store.put_sheet(SheetRecord(**{**review.__dict__, "items": [item]}))
        store.add_attempt("r1", {"R-01": "A"}, NOW)
        (row,) = store.all_answer_rows()
        assert row.retest_of == ("s1", "A-01") and row.sheet_kind == "review"

    def test_only_submitted_sheets_count_as_seen(self, store) -> None:
        store.put_sheet(_sheet("taken"))
        store.put_sheet(_sheet("untaken"))
        store.add_attempt("taken", {}, NOW)
        assert store.submitted_spec_hashes() == {"h-taken-A-01", "h-taken-B-01"}
        assert store.seen_stems_by_node() == {
            "n1": ["Stem taken A-01?"], "n2": ["Stem taken B-01?"],
        }
