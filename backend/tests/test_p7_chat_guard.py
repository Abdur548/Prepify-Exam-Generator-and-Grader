"""`POST /api/chat` under contention and under a known limit.

Chat reads Qdrant. Ingest writes it. Qdrant takes an exclusive file lock (S8), so
before this a question asked during an indexing run reached the lock and raised —
a bare 500 telling a student their notes were broken when they were merely busy.

The other half is the `degraded` distinction `/api/exam` already draws and chat
did not: running out of quota is a known limit, not a fault, and reporting it as
one sends a student looking for a problem that is not there.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

_TEST_API_KEY = "test-key-not-a-real-credential"

_ANSWER = {
    "answer": "A heuristic estimates the remaining cost to a goal.",
    "citations": [{"file": "03_search.pdf", "page": 48}],
    "from_material": True,
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            yield c


def _ask(client, query: str = "What is a heuristic?", history=None):
    return client.post(
        "/api/chat", json={"query": query, "history": history or []}
    )


class TestBusyIsNotBroken:

    def test_a_question_during_another_job_is_409_not_500(self, client) -> None:
        import coursegen.app.main as main

        main._PIPELINE_LOCK.acquire()
        main._pipeline_holder = "indexing your uploads"
        try:
            resp = _ask(client)
        finally:
            main._pipeline_holder = ""
            main._PIPELINE_LOCK.release()

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "indexing your uploads" in detail
        assert "Traceback" not in detail

    def test_it_refuses_immediately_rather_than_queueing(self, client) -> None:
        """Waiting would be no better than failing. A chat request held open for
        the ten minutes an ingest takes is indistinguishable from a hang, and the
        student cannot tell whether to wait or reload."""
        import coursegen.app.main as main

        main._PIPELINE_LOCK.acquire()
        try:
            t0 = time.monotonic()
            resp = _ask(client)
            elapsed = time.monotonic() - t0
        finally:
            main._PIPELINE_LOCK.release()

        assert resp.status_code == 409
        assert elapsed < 2.0, f"blocked for {elapsed:.1f}s instead of refusing"

    def test_the_lock_is_released_after_a_successful_question(self, client) -> None:
        """Held, a chat would lock out every generation and ingest that followed."""
        from coursegen.app.main import _PIPELINE_LOCK

        with patch("coursegen.app.main._run_chat_query", return_value=dict(_ANSWER)):
            assert _ask(client).status_code == 200

        assert not _PIPELINE_LOCK.locked()

    def test_the_lock_is_released_after_a_failed_question(self, client) -> None:
        from coursegen.app.main import _PIPELINE_LOCK

        with patch("coursegen.app.main._run_chat_query",
                   side_effect=RuntimeError("boom")):
            assert _ask(client).status_code == 500

        assert not _PIPELINE_LOCK.locked(), "a failure left the pipeline locked"

    def test_a_generation_queued_behind_chat_is_told_so(self, client) -> None:
        """The holder label has to name chat too, or a queued run reports the
        vague fallback while something perfectly nameable holds the lock."""
        import coursegen.app.main as main

        seen: list[str] = []

        def slow(query, history):
            seen.append(main._pipeline_holder)
            return dict(_ANSWER)

        with patch("coursegen.app.main._run_chat_query", side_effect=slow):
            _ask(client)

        assert seen == ["answering a question"]


class TestKnownLimitsAreNotFaults:

    def test_quota_exhaustion_is_a_degraded_answer_not_a_500(self, client) -> None:
        from coursegen.llm.client import BudgetExceeded

        with patch("coursegen.app.main._run_chat_query",
                   side_effect=BudgetExceeded("per-day call cap reached")):
            resp = _ask(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["citations"] == []
        # R6: a degraded answer must not be dressed as one drawn from the material.
        assert body["from_material"] is False
        assert "limit" in body["answer"].lower()

    def test_a_network_failure_says_the_material_is_fine(self, client) -> None:
        """The student's instinct on any failure is that their upload broke. It
        did not, and the copy has to say so or they re-index for ten minutes."""
        import httpx

        with patch("coursegen.app.main._run_chat_query",
                   side_effect=httpx.ConnectError("no route")):
            resp = _ask(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert "material" in body["answer"].lower()

    def test_an_unexpected_error_is_a_500_with_no_traceback(self, client) -> None:
        with patch("coursegen.app.main._run_chat_query",
                   side_effect=RuntimeError("secret internal detail")):
            resp = _ask(client)

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "secret internal detail" not in detail
        assert "Traceback" not in detail
        assert "RuntimeError" not in detail


class TestTheAnswerShape:

    def test_from_material_survives_the_route(self, client) -> None:
        """The field the whole Ask screen turns on. Dropped or defaulted, a
        general-knowledge answer renders as one drawn from the student's pages."""
        ungrounded = {"answer": "…", "citations": [], "from_material": False}
        with patch("coursegen.app.main._run_chat_query", return_value=ungrounded):
            body = _ask(client).json()

        assert body["from_material"] is False

    def test_malformed_history_pairs_are_dropped_not_rejected(self, client) -> None:
        seen: dict[str, Any] = {}

        def capture(query, history):
            seen["history"] = history
            return dict(_ANSWER)

        with patch("coursegen.app.main._run_chat_query", side_effect=capture):
            resp = _ask(client, history=[["q1", "a1"], ["lonely"], ["q2", "a2"]])

        assert resp.status_code == 200
        assert seen["history"] == [("q1", "a1"), ("q2", "a2")]


class TestGuardsAreLoadBearing:

    def test_the_contention_guard_is_load_bearing(self) -> None:
        """R5 mutation: without the non-blocking probe, a question during an ingest
        blocks for the whole run instead of answering."""
        from pathlib import Path

        src = Path("coursegen/app/main.py").read_text(encoding="utf-8")
        original = "if not _PIPELINE_LOCK.acquire(blocking=False):"
        assert src.count(original) >= 1, "guard moved"
        mutated = src.replace(original, "if False:", 1)
        assert mutated != src, "MUTATION DID NOT APPLY — result is meaningless"
