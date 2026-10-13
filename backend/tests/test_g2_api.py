"""G2 — marking written answers on submission and on retry.

Consent and the pipeline lock are the two things that can stand between a
written answer and the model. Neither may lose the answer: without consent
nothing is sent and nothing is stored (the student is asked first); with the
pipeline busy, the attempt is saved, objective questions are marked, and the
written ones wait with a sentence saying why.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from coursegen.grade.records import MarkingOutcome, WrittenMark
from coursegen.grade.service import contest, remark, submit_attempt
from coursegen.grade.store import Store
from tests.test_g1_service import NOW, _paper_sheet_id

_TEST_API_KEY = "test-key-not-a-real-credential"


class StubMarker:
    """Stands in for the model. Records exactly what it was sent."""

    def __init__(self, marks: int = 3, degraded: str | None = None) -> None:
        self.marks, self.degraded, self.seen = marks, degraded, []

    def __call__(self, entries):
        self.seen.append([e.slot_id for e in entries])
        return MarkingOutcome(
            [WrittenMark(e.slot_id, min(self.marks, e.marks_available), "high", "Stub reason.")
             for e in entries],
            degraded=self.degraded,
        )


@pytest.fixture
def marker():
    stub = StubMarker()
    # The route's only way to the model. Patched for every API test here, so no
    # test can reach the provider even with a key set.
    with patch("coursegen.app.practice._new_marker", return_value=stub):
        yield stub


def _client_ctx(monkeypatch, accept: bool):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            c.post("/api/internal/reset-disclosure")
            if accept:
                c.post("/api/disclosure/accept")
            yield c


@pytest.fixture
def client(monkeypatch, marker):
    yield from _client_ctx(monkeypatch, accept=True)


@pytest.fixture
def client_no_consent(monkeypatch, marker):
    yield from _client_ctx(monkeypatch, accept=False)


@pytest.fixture
def busy():
    """Another job holds the pipeline, as a generation or an ingest would."""
    from coursegen.app import main

    main._PIPELINE_LOCK.acquire()
    main._pipeline_holder = "generating a paper"
    yield
    main._pipeline_holder = ""
    main._PIPELINE_LOCK.release()


def _submit(client, sheet_id, answers):
    return client.post("/api/attempts", json={
        "sheet_id": sheet_id,
        "answers": [{"slot_id": s, "response": r} for s, r in answers.items()],
    })


def _item(body, slot):
    return next(i for i in body["items"] if i["slot_id"] == slot)


class TestRemarkService:

    def test_nothing_unmarked_means_no_call(self) -> None:
        with Store() as store:
            r = submit_attempt(store, _paper_sheet_id(), {"A-01": "B"}, now=NOW)
            stub = StubMarker()
            again = remark(store, r["attempt_id"], marker=stub, now=NOW)
        assert stub.seen == [] and again["items"] == r["items"]

    def test_only_answers_with_no_mark_at_all_are_sent(self) -> None:
        """Review Focus 3: the student's own mark must not be replaced by the model's."""
        with Store() as store:
            r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q", "C-01": "t"}, now=NOW)
            contest(store, r["attempt_id"], "B-01", 2, now=NOW)
            stub = StubMarker()
            again = remark(store, r["attempt_id"], marker=stub, now=NOW)
        assert stub.seen == [["C-01"]]
        assert (_item(again, "B-01")["marks_awarded"], _item(again, "B-01")["source"]) == (2, "student")

    def test_a_degraded_retry_says_so(self) -> None:
        with Store() as store:
            r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW)
            again = remark(store, r["attempt_id"], marker=StubMarker(degraded="Stopped."), now=NOW)
        assert again["status"] == "degraded" and again["warnings"][0] == "Stopped."

    def test_no_marker_leaves_the_answer_waiting(self) -> None:
        with Store() as store:
            r = submit_attempt(store, _paper_sheet_id(), {"B-01": "q"}, now=NOW)
            again = remark(store, r["attempt_id"], marker=None, unavailable="Busy.", now=NOW)
        assert again["status"] == "degraded" and again["warnings"] == ["Busy."]


class TestConsent:

    def test_a_written_answer_without_consent_is_refused_and_nothing_is_kept(
        self, client_no_consent, marker
    ) -> None:
        res = _submit(client_no_consent, _paper_sheet_id(), {"A-01": "B", "B-01": "a queue"})
        assert res.status_code == 403 and "consent" in res.json()["detail"].lower()
        with Store() as store:
            assert store.latest_seq() == 0
        assert marker.seen == []

    def test_objective_answers_need_no_consent_and_no_model(self, client_no_consent, marker) -> None:
        res = _submit(client_no_consent, _paper_sheet_id(), {"A-01": "B"})
        assert res.status_code == 200 and marker.seen == []

    def test_a_blank_written_answer_needs_no_consent(self, client_no_consent) -> None:
        res = _submit(client_no_consent, _paper_sheet_id(), {"A-01": "B", "B-01": "   "})
        assert res.status_code == 200

    def test_an_over_long_answer_is_422_before_consent_is_asked(self, client_no_consent) -> None:
        from coursegen import config

        res = _submit(client_no_consent, _paper_sheet_id(),
                      {"B-01": "x" * (config.MAX_ANSWER_CHARS + 1)})
        assert res.status_code == 422


class TestMarkingOnSubmit:

    def test_written_answers_come_back_marked_by_the_model(self, client, marker) -> None:
        body = _submit(client, _paper_sheet_id(), {"A-01": "B", "B-01": "a queue"}).json()
        b01 = _item(body, "B-01")
        assert (b01["marks_awarded"], b01["source"], b01["reason"]) == (3, "model", "Stub reason.")
        assert marker.seen == [["B-01"]]

    def test_a_busy_pipeline_saves_and_marks_what_it_can(self, client, marker, busy) -> None:
        res = _submit(client, _paper_sheet_id(), {"A-01": "B", "B-01": "a queue"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "degraded"
        assert "Busy generating a paper" in body["warnings"][0]
        assert _item(body, "A-01")["marks_awarded"] == 2
        assert _item(body, "B-01")["status"] == "unmarked"
        assert marker.seen == []

    def test_marking_releases_the_lock(self, client) -> None:
        from coursegen.app import main

        _submit(client, _paper_sheet_id(), {"B-01": "a queue"})
        assert main._PIPELINE_LOCK.acquire(blocking=False)
        main._PIPELINE_LOCK.release()
        assert main._pipeline_holder == ""


class TestRemarkRoute:

    def test_retry_marks_what_was_left_waiting(self, client, marker, busy_then_free) -> None:
        body = busy_then_free(lambda: _submit(client, _paper_sheet_id(), {"B-01": "a queue"}).json())
        assert _item(body, "B-01")["status"] == "unmarked"
        res = client.post(f"/api/attempts/{body['attempt_id']}/remark")
        assert res.status_code == 200
        assert _item(res.json(), "B-01")["marks_awarded"] == 3

    def test_retry_keeps_the_students_own_mark(self, client, marker, busy_then_free) -> None:
        """Review Focus 3 over HTTP."""
        body = busy_then_free(lambda: _submit(
            client, _paper_sheet_id(), {"B-01": "a queue", "C-01": "a tree"}).json())
        client.post(f"/api/attempts/{body['attempt_id']}/contest",
                    json={"slot_id": "B-01", "marks_awarded": 2})
        res = client.post(f"/api/attempts/{body['attempt_id']}/remark").json()
        assert marker.seen == [["C-01"]]
        assert (_item(res, "B-01")["marks_awarded"], _item(res, "B-01")["source"]) == (2, "student")

    def test_retry_while_busy_is_409(self, client, busy_then_free) -> None:
        from coursegen.app import main

        body = busy_then_free(lambda: _submit(client, _paper_sheet_id(), {"B-01": "q"}).json())
        main._PIPELINE_LOCK.acquire()
        try:
            res = client.post(f"/api/attempts/{body['attempt_id']}/remark")
        finally:
            main._PIPELINE_LOCK.release()
        assert res.status_code == 409

    def test_retry_with_nothing_to_mark_needs_no_consent(self, client_no_consent) -> None:
        body = _submit(client_no_consent, _paper_sheet_id(), {"A-01": "B"}).json()
        res = client_no_consent.post(f"/api/attempts/{body['attempt_id']}/remark")
        assert res.status_code == 200

    def test_retry_of_an_unknown_attempt_is_404(self, client) -> None:
        assert client.post("/api/attempts/nope/remark").status_code == 404


@pytest.fixture
def busy_then_free():
    """Run one request while the pipeline is held, then let it go."""
    from coursegen.app import main

    def run(fn):
        main._PIPELINE_LOCK.acquire()
        main._pipeline_holder = "generating a paper"
        try:
            return fn()
        finally:
            main._pipeline_holder = ""
            main._PIPELINE_LOCK.release()

    return run
