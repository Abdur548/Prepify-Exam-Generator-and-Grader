"""G5 — review sets over HTTP.

A review of identical questions touches nothing but the student's own history:
no model, no consent, no lock. Only when a question must be re-worded does the
model get involved, and then consent and the pipeline lock apply exactly as they
do to marking.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from coursegen import config
from coursegen.grade.store import Store
from coursegen.ingest.coursemap import save_course_map
from tests.test_g5_review import StubGenerator, _make_fresh_due, _miss_a02, _nodes

_TEST_API_KEY = "test-key-not-a-real-credential"


def _client_ctx(monkeypatch, tmp_path, accept: bool, generator):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    monkeypatch.setattr(config, "COURSE_MAP_PATH", tmp_path / "course_map.json")
    save_course_map(_nodes(), config.COURSE_MAP_PATH)
    with patch("coursegen.app.main.run_preflight_checks"), \
         patch("coursegen.app.practice._new_generator", return_value=generator):
        with TestClient(app) as c:
            c.post("/api/internal/reset-disclosure")
            if accept:
                c.post("/api/disclosure/accept")
            yield c


@pytest.fixture
def generator():
    return StubGenerator()


@pytest.fixture
def client(monkeypatch, tmp_path, generator):
    yield from _client_ctx(monkeypatch, tmp_path, True, generator)


@pytest.fixture
def client_no_consent(monkeypatch, tmp_path, generator):
    yield from _client_ctx(monkeypatch, tmp_path, False, generator)


@pytest.fixture
def busy():
    from coursegen.app import main

    main._PIPELINE_LOCK.acquire()
    main._pipeline_holder = "generating a paper"
    yield
    main._pipeline_holder = ""
    main._PIPELINE_LOCK.release()


class TestReviewRoute:

    def test_without_material_it_is_409(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from coursegen.app.main import app

        monkeypatch.setattr(config, "COURSE_MAP_PATH", tmp_path / "none.json")
        with patch("coursegen.app.main.run_preflight_checks"), TestClient(app) as c:
            res = c.post("/api/review")
        assert res.status_code == 409

    def test_nothing_due_is_an_empty_answer_not_an_error(self, client) -> None:
        res = client.post("/api/review")
        assert res.status_code == 200 and res.json()["due"] == 0 and res.json()["sheet"] is None

    def test_identical_questions_need_no_consent_no_lock_and_no_model(
        self, client_no_consent, generator, busy
    ) -> None:
        with Store() as store:
            _miss_a02(store)
        res = client_no_consent.post("/api/review")
        assert res.status_code == 200 and res.json()["due"] == 1
        assert generator.specs == []

    def test_new_wording_without_consent_is_403(self, client_no_consent) -> None:
        with Store() as store:
            _make_fresh_due(store)
        assert client_no_consent.post("/api/review").status_code == 403

    def test_new_wording_while_busy_is_409(self, client, busy) -> None:
        with Store() as store:
            _make_fresh_due(store)
        res = client.post("/api/review")
        assert res.status_code == 409 and "Busy generating a paper" in res.json()["detail"]

    def test_new_wording_comes_back_written(self, client, generator) -> None:
        with Store() as store:
            _make_fresh_due(store)
        body = client.post("/api/review").json()
        (item,) = body["sheet"]["sections"][0]["items"]
        assert item["form"] == "fresh" and len(generator.specs) == 1

    def test_a_failed_rewrite_falls_back_and_says_so(self, monkeypatch, tmp_path) -> None:
        gen = StubGenerator(stem=None)
        ctx = _client_ctx(monkeypatch, tmp_path, True, gen)
        c = next(ctx)
        try:
            with Store() as store:
                _make_fresh_due(store)
            body = c.post("/api/review").json()
        finally:
            ctx.close()
        assert body["fallbacks"] == 1 and body["warnings"]

    def test_the_review_is_a_sheet_that_can_be_taken(self, client) -> None:
        with Store() as store:
            _miss_a02(store)
        sheet = client.post("/api/review").json()["sheet"]
        assert client.get(f"/api/sheets/{sheet['sheet_id']}").json() == sheet
        (item,) = sheet["sections"][0]["items"]
        res = client.post("/api/attempts", json={
            "sheet_id": sheet["sheet_id"],
            "answers": [{"slot_id": item["slot_id"], "response": item["correct_option"]}],
        })
        assert res.status_code == 200 and res.json()["sheet_kind"] == "review"
        assert res.json()["items"][0]["marks_awarded"] == 2
        assert res.json()["items"][0]["form"] == "identical"
