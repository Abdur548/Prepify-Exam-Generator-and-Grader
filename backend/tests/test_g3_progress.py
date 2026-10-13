"""G3 — where the student stands, per lecture, and what is due.

Assembled from history every time it is asked for. These pin the numbers the
Progress tab prints, and that a course map changed under the history degrades
the report with a warning rather than breaking it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.grade.service import progress, submit_attempt
from coursegen.grade.store import Store
from coursegen.ingest.coursemap import save_course_map
from tests.test_g1_service import NOW, _paper_sheet_id

_TEST_API_KEY = "test-key-not-a-real-credential"


def _nodes(*ids) -> list[CourseMapNode]:
    """The paper fixture's nodes: n1, n2 in 03_search.pdf; n3, n4 in 06_csp.pdf."""
    lecture = {"n1": "03_search.pdf", "n2": "03_search.pdf", "n3": "06_csp.pdf", "n4": "06_csp.pdf"}
    return [
        CourseMapNode(
            node_id=n, path=[lecture[n], f"Section {n}"], source_file=lecture[n],
            page_span=(1, 2), token_count=100, chunk_ids=[f"c-{n}"], key_terms=[],
            flags=NodeFlags(), instructional_mass=0.25,
        )
        for n in (ids or ("n1", "n2", "n3", "n4"))
    ]


def _lecture(report, name):
    return next(l for l in report["lectures"] if l["lecture"] == name)


class TestProgress:

    def test_with_no_history_everything_is_untested(self) -> None:
        with Store() as store:
            report = progress(store, _nodes())
        assert report["attempts"] == 0 and report["due"] == 0 and report["next_review"] == 0
        assert all(l["mastery"] is None and l["tested_share"] == 0.0 for l in report["lectures"])
        assert report["warnings"] == []

    def test_a_miss_shows_up_as_tested_and_due(self) -> None:
        with Store() as store:
            # A-01 (n1) right, A-02 (n2) wrong: both in 03_search.pdf.
            submit_attempt(store, _paper_sheet_id(), {"A-01": "B", "A-02": "A"}, now=NOW)
            report = progress(store, _nodes())
        search = _lecture(report, "03_search.pdf")
        assert search["mastery"] == pytest.approx(0.5) and search["tested_share"] == pytest.approx(1.0)
        assert search["nodes_tested"] == 2 and len(search["nodes"]) == 2
        assert report["attempts"] == 1 and report["due"] >= 1
        assert report["cards"]["box_1"] >= 1 and report["cards"]["retired"] == 0

    def test_blank_answers_count_as_tested(self) -> None:
        """C-01 left blank is evidence of not knowing n4 — it is tested, at zero."""
        with Store() as store:
            submit_attempt(store, _paper_sheet_id(), {}, now=NOW)
            report = progress(store, _nodes())
        assert _lecture(report, "06_csp.pdf")["mastery"] == 0.0

    def test_answers_on_material_no_longer_ingested_are_counted_not_crashed_on(self) -> None:
        """Review Focus 5."""
        with Store() as store:
            submit_attempt(store, _paper_sheet_id(), {"A-01": "A"}, now=NOW)
            report = progress(store, _nodes("n1"))          # n2..n4 gone
        assert any("no longer ingested" in w for w in report["warnings"])
        assert [l["lecture"] for l in report["lectures"]] == ["03_search.pdf"]

    def test_next_review_is_capped_and_one_per_node(self) -> None:
        with Store() as store:
            submit_attempt(store, _paper_sheet_id(), {}, now=NOW)       # every question missed
            report = progress(store, _nodes())
        assert report["next_review"] <= config.REVIEW_MAX_ITEMS
        assert report["next_review"] <= report["due"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    monkeypatch.setattr(config, "COURSE_MAP_PATH", tmp_path / "course_map.json")
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            yield c


class TestProgressRoute:

    def test_no_course_map_is_409(self, client) -> None:
        res = client.get("/api/progress")
        assert res.status_code == 409 and "ingested" in res.json()["detail"]

    def test_the_report_comes_back(self, client) -> None:
        save_course_map(_nodes(), config.COURSE_MAP_PATH)
        _paper = _paper_sheet_id()
        client.post("/api/attempts", json={"sheet_id": _paper,
                                           "answers": [{"slot_id": "A-02", "response": "A"}]})
        res = client.get("/api/progress")
        assert res.status_code == 200
        body = res.json()
        assert body["attempts"] == 1 and body["due"] >= 1
        assert {l["lecture"] for l in body["lectures"]} == {"03_search.pdf", "06_csp.pdf"}
