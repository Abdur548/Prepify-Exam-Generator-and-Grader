"""G1 — attempts, contests and sheets over HTTP.

The service tests pin the rules; these pin the mapping a client depends on: which
failure is a 404, which a 422, and that an unexpected one is a 500 that says
nothing about the server's internals.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.test_g1_service import _paper_sheet_id

_TEST_API_KEY = "test-key-not-a-real-credential"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            c.post("/api/internal/reset-disclosure")
            yield c


def _submit(client, sheet_id, answers):
    return client.post("/api/attempts", json={
        "sheet_id": sheet_id,
        "answers": [{"slot_id": s, "response": r} for s, r in answers.items()],
    })


class TestAttempts:

    def test_submitting_objective_answers_returns_marks(self, client) -> None:
        res = _submit(client, _paper_sheet_id(), {"A-01": "B", "A-02": "A"})
        assert res.status_code == 200
        body = res.json()
        marks = {i["slot_id"]: i["marks_awarded"] for i in body["items"]}
        assert marks["A-01"] == 2 and marks["A-02"] == 0
        assert body["status"] == "ok"

    def test_results_read_back_by_id(self, client) -> None:
        body = _submit(client, _paper_sheet_id(), {"A-01": "B"}).json()
        again = client.get(f"/api/attempts/{body['attempt_id']}")
        assert again.status_code == 200 and again.json()["items"] == body["items"]

    def test_an_unknown_sheet_is_404(self, client) -> None:
        res = _submit(client, "0000000000000000", {})
        # The detail, not just the code: a missing ROUTE is also a 404.
        assert res.status_code == 404 and "No sheet" in res.json()["detail"]

    def test_an_unknown_attempt_is_404(self, client) -> None:
        res = client.get("/api/attempts/nope")
        assert res.status_code == 404 and "No attempt" in res.json()["detail"]

    def test_an_unknown_slot_is_422(self, client) -> None:
        res = _submit(client, _paper_sheet_id(), {"Z-99": "A"})
        assert res.status_code == 422 and "Z-99" in res.json()["detail"]

    def test_the_same_slot_twice_is_422(self, client) -> None:
        res = client.post("/api/attempts", json={
            "sheet_id": _paper_sheet_id(),
            "answers": [{"slot_id": "A-01", "response": "A"}, {"slot_id": "A-01", "response": "B"}],
        })
        assert res.status_code == 422

    def test_an_over_long_answer_is_422(self, client) -> None:
        from coursegen import config

        res = _submit(client, _paper_sheet_id(), {"B-01": "x" * (config.MAX_ANSWER_CHARS + 1)})
        assert res.status_code == 422

    def test_an_unexpected_failure_is_a_500_that_reveals_nothing(self, client) -> None:
        with patch("coursegen.app.practice.submit_attempt",
                   side_effect=RuntimeError("secret internal detail")):
            res = _submit(client, _paper_sheet_id(), {})
        assert res.status_code == 500
        assert "secret" not in res.text and "Traceback" not in res.text


class TestContest:

    def test_a_contest_returns_the_item_with_its_original(self, client) -> None:
        body = _submit(client, _paper_sheet_id(), {"A-01": "A"}).json()
        res = client.post(f"/api/attempts/{body['attempt_id']}/contest",
                          json={"slot_id": "A-01", "marks_awarded": 2})
        assert res.status_code == 200
        item = res.json()
        assert item["marks_awarded"] == 2 and item["original"]["source"] == "key"

    def test_contesting_an_unanswered_question_is_422(self, client) -> None:
        body = _submit(client, _paper_sheet_id(), {}).json()
        res = client.post(f"/api/attempts/{body['attempt_id']}/contest",
                          json={"slot_id": "B-01", "marks_awarded": 1})
        assert res.status_code == 422

    def test_contesting_out_of_range_is_422(self, client) -> None:
        # An objective question: a written answer would need consent (G2).
        body = _submit(client, _paper_sheet_id(), {"A-01": "A"}).json()
        res = client.post(f"/api/attempts/{body['attempt_id']}/contest",
                          json={"slot_id": "A-01", "marks_awarded": 99})
        assert res.status_code == 422


class TestSheets:

    def test_an_archived_sheet_is_served_verbatim(self, client) -> None:
        sheet_id = _paper_sheet_id()
        res = client.get(f"/api/sheets/{sheet_id}")
        assert res.status_code == 200 and res.json()["sheet_id"] == sheet_id

    def test_an_unknown_sheet_is_404(self, client) -> None:
        res = client.get("/api/sheets/nope")
        assert res.status_code == 404 and "No sheet" in res.json()["detail"]
