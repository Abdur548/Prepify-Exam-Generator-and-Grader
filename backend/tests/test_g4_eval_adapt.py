"""G4 — measuring what the tilt does to the next paper (§13.2).

The claim "the next paper targets your weak topics" is only allowed if this
measurement supports it. These tests pin the measurement itself: that it is
deterministic, that it counts marks honestly, and that its verdict is computed
from its own numbers rather than asserted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coursegen.contracts.course_map import CourseMapNode
from coursegen.eval.adapt import PROFILES, evaluate, format_report, history, to_json
from coursegen.exam.allocate import solve
from coursegen.grade.mastery import node_mastery
from coursegen.pipeline import load_blueprint

FIXTURE = Path(__file__).parent / "fixtures" / "course_map_sample.json"


@pytest.fixture(scope="module")
def nodes() -> list[CourseMapNode]:
    return [CourseMapNode(**n) for n in json.loads(FIXTURE.read_text(encoding="utf-8"))]


@pytest.fixture(scope="module")
def rows(nodes):
    return evaluate(nodes, load_blueprint("midterm_default"))


class TestHistories:

    def test_every_profile_answers_every_question_on_the_base_paper(self, nodes) -> None:
        specs, _ = solve(nodes, load_blueprint("quiz_default"))
        for profile in PROFILES:
            answered = history(profile, specs, nodes)
            assert [r.slot_id for r in answered] == [s.slot_id for s in specs]

    def test_all_correct_masters_everything_it_touched(self, nodes) -> None:
        specs, _ = solve(nodes, load_blueprint("quiz_default"))
        mastery = node_mastery(history("all_correct", specs, nodes))
        assert all(m.mastery == 1.0 for m in mastery.values())

    def test_weak_two_lectures_fails_exactly_the_two_heaviest_lectures(self, nodes) -> None:
        specs, _ = solve(nodes, load_blueprint("midterm_default"))
        lecture = {n.node_id: n.source_file for n in nodes}
        marks: dict[str, int] = {}
        for s in specs:
            marks[lecture[s.node_id]] = marks.get(lecture[s.node_id], 0) + s.marks
        weak = set(sorted(marks, key=lambda l: (-marks[l], l))[:2])
        for r in history("weak_two_lectures", specs, nodes):
            assert (r.marks_awarded == 0) == (lecture[r.node_id] in weak)


class TestMeasurement:

    def test_one_row_per_profile(self, rows) -> None:
        assert [r.profile for r in rows] == list(PROFILES)

    def test_no_question_the_student_saw_is_repeated(self, rows) -> None:
        assert all(r.repeats == 0 for r in rows)

    def test_the_measurement_is_deterministic(self, rows) -> None:
        assert all(r.deterministic for r in rows)

    def test_shares_are_shares(self, rows) -> None:
        for r in rows:
            for side in (r.untilted, r.tilted):
                assert sum(side.values()) == pytest.approx(1.0)

    def test_the_verdict_follows_from_the_numbers(self, rows) -> None:
        for r in rows:
            before = r.untilted["weak"] + r.untilted["untested"]
            after = r.tilted["weak"] + r.tilted["untested"]
            expected = "N/A" if before >= 1.0 - 1e-9 else ("PASS" if after > before else "FAIL")
            assert r.verdict == expected

    def test_a_student_who_got_everything_right_is_moved_onto_new_material(self, rows) -> None:
        r = next(r for r in rows if r.profile == "all_correct")
        assert r.untilted["untested"] == 0.0 and r.tilted["untested"] > 0.0


class TestReport:

    def test_the_report_names_the_map_and_every_verdict(self, rows) -> None:
        text = format_report({"midterm_default": rows}, map_label="fixture.json (20 nodes)")
        assert "fixture.json (20 nodes)" in text
        assert all(r.verdict in text for r in rows)

    def test_json_round_trips(self, rows) -> None:
        data = json.loads(json.dumps(to_json({"midterm_default": rows}, map_label="m")))
        assert data["map"] == "m" and len(data["blueprints"]["midterm_default"]) == len(PROFILES)
