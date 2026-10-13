"""The student's practice history, in SQLite. The only module that writes SQL.

## What is stored, and what never is

Sheets (every answerable paper and review set), attempts, the answers given, and
every mark ever awarded. **Mastery, re-test cards and the next paper's tilt are
never stored** — `grade/mastery.py` derives them from these rows each time, so a
contested mark re-derives history instead of needing a repair step.

## Nothing is overwritten

`answers` rows are written once. Marks arrive at different times and from three
sources — the key at submission, the model at submission or on a retry, the
student when they contest — and each is APPENDED to `marks`. The mark that counts
is the latest row (`effective_marks`). "The latest mark counts" is then one rule,
not an update policy with edge cases.

## Concurrency

One connection per request, one transaction per write. Sufficient because the
API runs single-worker (S8); the same constraint the daily call ledger relies on.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from coursegen import config
from coursegen.grade.records import (
    AnswerRow,
    InvalidSubmission,
    SheetItem,
    SheetRecord,
    UnknownAttempt,
    UnknownSheet,
    document_items,
)

SCHEMA_VERSION = 1

_SOURCES = {"key", "model", "student"}
_CONFIDENCES = {None, "high", "low"}

_DDL_V1 = """
CREATE TABLE sheets (
    sheet_id     TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('paper', 'review')),
    blueprint_id TEXT,
    title        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    document     TEXT NOT NULL
);

CREATE TABLE sheet_items (
    sheet_id        TEXT NOT NULL REFERENCES sheets(sheet_id),
    slot_id         TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    span_ids        TEXT NOT NULL,
    spec_hash       TEXT NOT NULL,
    item_type       TEXT NOT NULL,
    bloom           TEXT NOT NULL,
    marks           INTEGER NOT NULL CHECK (marks > 0),
    correct_option  TEXT,
    from_material   INTEGER NOT NULL CHECK (from_material IN (0, 1)),
    retest_of_sheet TEXT,
    retest_of_slot  TEXT,
    form            TEXT CHECK (form IN ('identical', 'fresh', 'fallback')),
    spec            TEXT NOT NULL,
    PRIMARY KEY (sheet_id, slot_id)
);

CREATE TABLE attempts (
    attempt_id   TEXT PRIMARY KEY,
    seq          INTEGER NOT NULL UNIQUE,
    sheet_id     TEXT NOT NULL REFERENCES sheets(sheet_id),
    submitted_at TEXT NOT NULL
);

CREATE TABLE answers (
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
    slot_id    TEXT NOT NULL,
    response   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (attempt_id, slot_id)
);

CREATE TABLE marks (
    mark_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id    TEXT NOT NULL,
    slot_id       TEXT NOT NULL,
    marks_awarded INTEGER NOT NULL CHECK (marks_awarded >= 0),
    source        TEXT NOT NULL CHECK (source IN ('key', 'model', 'student')),
    confidence    TEXT CHECK (confidence IN ('high', 'low')),
    reason        TEXT,
    at            TEXT NOT NULL,
    FOREIGN KEY (attempt_id, slot_id) REFERENCES answers(attempt_id, slot_id)
);

CREATE INDEX marks_by_answer ON marks (attempt_id, slot_id, mark_id);

CREATE VIEW effective_marks AS
SELECT m.* FROM marks m
WHERE m.mark_id = (SELECT MAX(mark_id) FROM marks
                   WHERE attempt_id = m.attempt_id AND slot_id = m.slot_id);

-- A second line behind add_mark's own check: a direct insert cannot store a
-- mark larger than the question is worth either.
CREATE TRIGGER marks_within_bounds BEFORE INSERT ON marks
WHEN NEW.marks_awarded > (
    SELECT si.marks FROM sheet_items si JOIN attempts a ON a.sheet_id = si.sheet_id
    WHERE a.attempt_id = NEW.attempt_id AND si.slot_id = NEW.slot_id
)
BEGIN
    SELECT RAISE(ABORT, 'marks_awarded exceeds the item''s marks');
END;
"""

_ROW_QUERY = """
SELECT a.attempt_id, a.seq, a.sheet_id, s.kind AS sheet_kind, an.slot_id,
       si.node_id, si.item_type, si.marks AS marks_available, an.response,
       si.retest_of_sheet, si.retest_of_slot,
       em.marks_awarded, em.source, em.confidence, em.reason,
       (SELECT m.marks_awarded FROM marks m
         WHERE m.attempt_id = an.attempt_id AND m.slot_id = an.slot_id
           AND m.source != 'student' ORDER BY m.mark_id DESC LIMIT 1) AS orig_marks,
       (SELECT m.source FROM marks m
         WHERE m.attempt_id = an.attempt_id AND m.slot_id = an.slot_id
           AND m.source != 'student' ORDER BY m.mark_id DESC LIMIT 1) AS orig_source
FROM answers an
JOIN attempts a     ON a.attempt_id = an.attempt_id
JOIN sheets s       ON s.sheet_id = a.sheet_id
JOIN sheet_items si ON si.sheet_id = a.sheet_id AND si.slot_id = an.slot_id
LEFT JOIN effective_marks em ON em.attempt_id = an.attempt_id AND em.slot_id = an.slot_id
"""


class Store:
    """A connection to the practice database, migrated on open."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else config.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except Exception:
            self._conn.close()
            raise

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            # Reading it would mean guessing at columns this code has never seen.
            raise RuntimeError(
                f"The practice database at {self.path.name} was written by newer code "
                f"(schema {version}, this code knows {SCHEMA_VERSION})."
            )
        if version == 0:
            self._conn.executescript(_DDL_V1)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    # -- sheets --------------------------------------------------------------

    def put_sheet(self, sheet: SheetRecord) -> None:
        """Archive a sheet. Archiving the same id again is a no-op.

        The id is a content hash, so the same id means the same document; the
        first `created_at` is kept because that is when it was first produced.
        """
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO sheets VALUES (?, ?, ?, ?, ?, ?)",
                (sheet.sheet_id, sheet.kind, sheet.blueprint_id, sheet.title,
                 sheet.created_at, json.dumps(sheet.document, sort_keys=True)),
            )
            if cur.rowcount == 0:
                return
            self._conn.executemany(
                "INSERT INTO sheet_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (sheet.sheet_id, i.slot_id, i.node_id, json.dumps(i.span_ids),
                     i.spec_hash, i.item_type, i.bloom, i.marks, i.correct_option,
                     int(i.from_material),
                     i.retest_of[0] if i.retest_of else None,
                     i.retest_of[1] if i.retest_of else None,
                     i.form, json.dumps(i.spec, sort_keys=True))
                    for i in sheet.items
                ],
            )

    def get_sheet(self, sheet_id: str) -> SheetRecord:
        row = self._conn.execute(
            "SELECT * FROM sheets WHERE sheet_id = ?", (sheet_id,)
        ).fetchone()
        if row is None:
            raise UnknownSheet(f"No sheet {sheet_id!r}.")
        items = [
            SheetItem(
                slot_id=r["slot_id"], node_id=r["node_id"],
                span_ids=json.loads(r["span_ids"]), spec_hash=r["spec_hash"],
                item_type=r["item_type"], bloom=r["bloom"], marks=r["marks"],
                correct_option=r["correct_option"],
                from_material=bool(r["from_material"]), spec=json.loads(r["spec"]),
                retest_of=(
                    (r["retest_of_sheet"], r["retest_of_slot"])
                    if r["retest_of_sheet"] is not None else None
                ),
                form=r["form"],
            )
            for r in self._conn.execute(
                "SELECT * FROM sheet_items WHERE sheet_id = ? ORDER BY slot_id",
                (sheet_id,),
            )
        ]
        return SheetRecord(
            sheet_id=row["sheet_id"], kind=row["kind"],
            blueprint_id=row["blueprint_id"], title=row["title"],
            created_at=row["created_at"], document=json.loads(row["document"]),
            items=items,
        )

    # -- attempts and marks --------------------------------------------------

    def add_attempt(
        self, sheet_id: str, responses: dict[str, str], submitted_at: str
    ) -> tuple[str, int]:
        """Record one submission. Returns (attempt_id, seq).

        Every answerable slot gets a row, with '' for the ones not answered: a
        skipped question is a fact about the attempt, not an absence of one.
        """
        slots = [
            r["slot_id"] for r in self._conn.execute(
                "SELECT slot_id FROM sheet_items WHERE sheet_id = ? ORDER BY slot_id",
                (sheet_id,),
            )
        ]
        if not slots and self._conn.execute(
            "SELECT 1 FROM sheets WHERE sheet_id = ?", (sheet_id,)
        ).fetchone() is None:
            raise UnknownSheet(f"No sheet {sheet_id!r}.")
        unknown = sorted(set(responses) - set(slots))
        if unknown:
            raise InvalidSubmission(
                f"Not questions on this sheet: {', '.join(unknown)}."
            )
        attempt_id = uuid.uuid4().hex
        with self._conn:
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM attempts"
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?)",
                (attempt_id, seq, sheet_id, submitted_at),
            )
            self._conn.executemany(
                "INSERT INTO answers VALUES (?, ?, ?)",
                [(attempt_id, slot, responses.get(slot, "")) for slot in slots],
            )
        return attempt_id, seq

    def add_mark(
        self,
        attempt_id: str,
        slot_id: str,
        marks_awarded: int,
        source: str,
        *,
        confidence: Optional[str] = None,
        reason: Optional[str] = None,
        at: str,
    ) -> None:
        """Append a mark. The latest mark for an answer is the one that counts."""
        if source not in _SOURCES:
            raise InvalidSubmission(f"Unknown mark source {source!r}.")
        if confidence not in _CONFIDENCES:
            raise InvalidSubmission(f"Unknown confidence {confidence!r}.")
        sheet_id = self.attempt_sheet_id(attempt_id)
        row = self._conn.execute(
            "SELECT marks FROM sheet_items WHERE sheet_id = ? AND slot_id = ?",
            (sheet_id, slot_id),
        ).fetchone()
        if row is None:
            raise InvalidSubmission(f"{slot_id!r} is not a question on this sheet.")
        # bool is an int subclass; True would otherwise be stored as 1 mark.
        if isinstance(marks_awarded, bool) or not isinstance(marks_awarded, int):
            raise InvalidSubmission(f"A mark must be a whole number, not {marks_awarded!r}.")
        if not 0 <= marks_awarded <= row["marks"]:
            raise InvalidSubmission(
                f"{slot_id} is worth {row['marks']} marks; {marks_awarded} is not possible."
            )
        with self._conn:
            self._conn.execute(
                "INSERT INTO marks (attempt_id, slot_id, marks_awarded, source, "
                "confidence, reason, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (attempt_id, slot_id, marks_awarded, source, confidence, reason, at),
            )

    def attempt_sheet_id(self, attempt_id: str) -> str:
        row = self._conn.execute(
            "SELECT sheet_id FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise UnknownAttempt(f"No attempt {attempt_id!r}.")
        return row["sheet_id"]

    def has_marks(self, attempt_id: str, slot_id: str) -> bool:
        """Whether any mark, from any source, exists for this answer."""
        return self._conn.execute(
            "SELECT 1 FROM marks WHERE attempt_id = ? AND slot_id = ? LIMIT 1",
            (attempt_id, slot_id),
        ).fetchone() is not None

    # -- history -------------------------------------------------------------

    def attempt_rows(self, attempt_id: str) -> list[AnswerRow]:
        self.attempt_sheet_id(attempt_id)
        return self._rows("WHERE an.attempt_id = ?", (attempt_id,))

    def all_answer_rows(self) -> list[AnswerRow]:
        """Every answer ever given, in submission order."""
        return self._rows("", ())

    def latest_seq(self) -> int:
        return self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM attempts"
        ).fetchone()[0]

    def submitted_spec_hashes(self) -> set[str]:
        """spec_hash of every question on a sheet the student has submitted."""
        return {
            r[0] for r in self._conn.execute(
                "SELECT DISTINCT si.spec_hash FROM sheet_items si "
                "JOIN attempts a ON a.sheet_id = si.sheet_id"
            )
        }

    def seen_stems_by_node(self) -> dict[str, list[str]]:
        """node_id -> every stem the student has been shown for it, sorted.

        Read from submitted sheets only. A paper generated and never taken was
        never seen, so it must not make the next paper avoid its wording.
        """
        seen: dict[str, set[str]] = {}
        sheet_ids = [
            r[0] for r in self._conn.execute(
                "SELECT DISTINCT sheet_id FROM attempts ORDER BY sheet_id"
            )
        ]
        for sheet_id in sheet_ids:
            sheet = self.get_sheet(sheet_id)
            stems = document_items(sheet.document)
            for item in sheet.items:
                stem = stems.get(item.slot_id, {}).get("stem")
                if stem:
                    seen.setdefault(item.node_id, set()).add(stem)
        return {node: sorted(stems) for node, stems in sorted(seen.items())}

    def _rows(self, where: str, params: tuple[Any, ...]) -> list[AnswerRow]:
        rows = self._conn.execute(
            f"{_ROW_QUERY} {where} ORDER BY a.seq, an.slot_id", params
        ).fetchall()
        return [
            AnswerRow(
                attempt_id=r["attempt_id"], seq=r["seq"], sheet_id=r["sheet_id"],
                sheet_kind=r["sheet_kind"], slot_id=r["slot_id"], node_id=r["node_id"],
                item_type=r["item_type"], marks_available=r["marks_available"],
                response=r["response"], marks_awarded=r["marks_awarded"],
                source=r["source"], confidence=r["confidence"], reason=r["reason"],
                retest_of=(
                    (r["retest_of_sheet"], r["retest_of_slot"])
                    if r["retest_of_sheet"] is not None else None
                ),
                original=(
                    (r["orig_marks"], r["orig_source"])
                    if r["source"] == "student" and r["orig_marks"] is not None
                    else None
                ),
            )
            for r in rows
        ]
