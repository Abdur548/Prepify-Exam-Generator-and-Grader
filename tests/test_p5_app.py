"""
P5 gate tests — FastAPI app, preflight checks, degraded mode.

TDD RED first: these tests define the P5 API before implementation.

Gate conditions (all must hold):
  1. Pre-flight check raises a clear RuntimeError for each missing condition
  2. POST /api/exam without disclosure accepted → 403
  3. POST /api/exam with BudgetExceeded → 200 degraded (not 500)
  4. POST /api/chat returns answer + citations for grounded query
  5. POST /api/chat returns not-from-material marker for ungrounded query
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _import_preflight():
    try:
        from coursegen.app.preflight import run_preflight_checks
    except ModuleNotFoundError as exc:
        pytest.fail(f"P5 preflight module missing: {exc}")
    return run_preflight_checks


def _import_app():
    try:
        from fastapi.testclient import TestClient
        from coursegen.app.main import app
    except ModuleNotFoundError as exc:
        pytest.fail(f"P5 app module missing: {exc}")
    return TestClient, app


# ---------------------------------------------------------------------------
# Preflight checks — R8
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_raises_on_missing_api_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        run_preflight_checks = _import_preflight()
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch("coursegen.app.preflight.check_weasyprint_available"):
            with patch("coursegen.app.preflight._check_qdrant_openable"):
                with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                    run_preflight_checks(output_dir=tmp_path)

    def test_raises_on_unwritable_output_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        run_preflight_checks = _import_preflight()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.preflight.check_weasyprint_available"):
            with patch("coursegen.app.preflight._check_qdrant_openable"):
                with patch("pathlib.Path.write_text",
                           side_effect=PermissionError("Permission denied")):
                    with pytest.raises(RuntimeError, match="[Oo]utput"):
                        run_preflight_checks(output_dir=tmp_path)

    # These three reach the models check, so it is patched out: otherwise they would
    # pass or fail according to whether THIS machine happens to have the models
    # cached, which is not what any of them is testing. The models check has its own
    # tests below, where the cache state is controlled explicitly.
    def test_raises_on_weasyprint_unavailable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        run_preflight_checks = _import_preflight()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.preflight._check_models_present"):
            with patch("coursegen.app.preflight.check_weasyprint_available",
                       side_effect=RuntimeError("WeasyPrint native runtime unavailable")):
                with patch("coursegen.app.preflight._check_qdrant_openable"):
                    with pytest.raises(RuntimeError, match="[Ww]easy[Pp]rint"):
                        run_preflight_checks(output_dir=tmp_path)

    def test_raises_on_qdrant_not_openable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        run_preflight_checks = _import_preflight()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.preflight._check_models_present"):
            with patch("coursegen.app.preflight.check_weasyprint_available"):
                with patch("coursegen.app.preflight._check_qdrant_openable",
                           side_effect=RuntimeError("Qdrant not openable")):
                    with pytest.raises(RuntimeError, match="[Qq]drant"):
                        run_preflight_checks(output_dir=tmp_path)

    def test_passes_silently_when_all_conditions_met(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        run_preflight_checks = _import_preflight()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.preflight._check_models_present"):
            with patch("coursegen.app.preflight.check_weasyprint_available"):
                with patch("coursegen.app.preflight._check_qdrant_openable"):
                    run_preflight_checks(output_dir=tmp_path)  # must not raise


class TestPreflightModelsPresent:
    """
    R8 lists "models present on disk" among the startup checks and it was the one
    missing. It is also the one with a real incident behind it: the cross-encoder
    was absent from the development machine until 2026-08-29, so the groundedness
    gate and the chat reranker had only ever run against injected stubs — and
    nothing said so, because the server started perfectly.
    """

    def test_raises_naming_the_missing_model(self) -> None:
        from coursegen.app import preflight
        from coursegen import config

        def fake_cache(repo_id: str, filename: str):
            # Embedding model cached, reranker absent — the exact state this machine
            # was in, and the state a cold demo machine is in for both.
            return "/cache/path" if repo_id == config.EMBEDDING_MODEL else None

        with patch("huggingface_hub.try_to_load_from_cache", side_effect=fake_cache):
            with pytest.raises(RuntimeError) as excinfo:
                preflight._check_models_present()

        message = str(excinfo.value)
        assert config.RERANKER_MODEL in message, "the missing model must be named"
        assert config.EMBEDDING_MODEL not in message, "the cached model must not be"

    def test_passes_when_both_models_are_cached(self) -> None:
        from coursegen.app import preflight

        with patch("huggingface_hub.try_to_load_from_cache", return_value="/cache/path"):
            preflight._check_models_present()   # must not raise

    def test_makes_no_network_call(self) -> None:
        """
        A preflight that can block on a 90 MB download is not a preflight. The lookup
        must be cache-only, so an offline machine reports 'missing' rather than hanging.
        """
        from coursegen.app import preflight

        with patch("socket.socket.connect", side_effect=AssertionError("network call")):
            with patch("huggingface_hub.try_to_load_from_cache", return_value="/cache/path"):
                preflight._check_models_present()

    def test_run_preflight_checks_actually_invokes_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """
        The other tests here call `_check_models_present()` directly, so they pass
        whether or not the startup sequence ever reaches it. Mutation-checked:
        deleting the call from `run_preflight_checks` left all of them green. This is
        the test that notices — a check nothing invokes protects nothing.
        """
        from coursegen.app import preflight

        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.preflight._check_models_present") as models_check:
            with patch("coursegen.app.preflight.check_weasyprint_available"):
                with patch("coursegen.app.preflight._check_qdrant_openable"):
                    preflight.run_preflight_checks(output_dir=tmp_path)

        models_check.assert_called_once()

    def test_models_appear_in_preflight_status(self) -> None:
        from coursegen.app import preflight

        with patch("coursegen.app.preflight._check_models_present"):
            with patch("coursegen.app.preflight.check_weasyprint_available"):
                with patch("coursegen.app.preflight._check_qdrant_openable"):
                    status = preflight.preflight_status()
        assert "models" in status, f"models check missing from status: {list(status)}"


class TestQdrantLockMessageNamesWorkers:
    def test_qdrant_failure_points_at_single_worker(self) -> None:
        """
        S8: local-mode Qdrant holds an exclusive file lock, so the usual cause of this
        on a working install is a second uvicorn worker. The PRD warns it "will
        otherwise present as a random failure on the demo machine" — naming the cause
        in the message is what makes it self-diagnosing. README documents --workers 1;
        documentation does not stop anyone typing --workers 4.
        """
        from coursegen.app import preflight

        with patch("qdrant_client.QdrantClient", side_effect=RuntimeError("already locked")):
            with pytest.raises(RuntimeError) as excinfo:
                preflight._check_qdrant_openable()

        message = str(excinfo.value)
        assert "--workers 1" in message
        assert "lock" in message.lower()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

# A value no secret scanner can mistake for a credential.
#
# The literal this replaces was "AIza" + 35 characters, which matches _KEY_RE in
# llm/client.py AND GitHub push protection's Google-key detector. It is fake, but a
# scanner cannot know that: it blocks pushes and teaches the reader to wave through
# key-shaped findings. test_p0_client.py still uses a realistic-shaped key on
# purpose - it asserts that redaction catches one - which is the one place that
# shape earns its keep.
_TEST_API_KEY = "test-key-not-a-real-credential"


class TestAppRoutes:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        TestClient, app = _import_app()
        # Point the app at a fresh data dir so tests are isolated.
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.main.run_preflight_checks"):
            with TestClient(app) as c:
                # Reset disclosure state before each test.
                c.post("/api/internal/reset-disclosure")
                yield c

    def test_health_returns_ok(self, client) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_generate_without_disclosure_returns_403(self, client) -> None:
        resp = client.post("/api/exam", json={"blueprint_id": "quiz_default", "title": "Test"})
        assert resp.status_code == 403
        assert "disclosure" in resp.json()["detail"].lower()

    def test_accept_disclosure_returns_200(self, client) -> None:
        resp = client.post("/api/disclosure/accept")
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    def test_generate_after_disclosure_is_not_403(self, client) -> None:
        client.post("/api/disclosure/accept")
        with patch("coursegen.app.main._run_exam_pipeline",
                   return_value={"status": "done", "items": [], "fill_ratio": 1.0,
                                 "unfilled_slots": [], "coverage_ratio": 0.5,
                                 "allocation_fidelity": 1.0}):
            resp = client.post("/api/exam", json={"blueprint_id": "quiz_default", "title": "T"})
        assert resp.status_code != 403

    def test_preflight_endpoint_reports_each_check(self, client) -> None:
        with patch("coursegen.app.main.run_preflight_checks"):
            resp = client.get("/api/preflight")
        assert resp.status_code == 200
        body = resp.json()
        assert "api_key" in body
        assert "output_dir" in body
        assert "weasyprint" in body
        assert "qdrant" in body


# ---------------------------------------------------------------------------
# Degraded mode — BudgetExceeded must not reach the user as a 500
# ---------------------------------------------------------------------------

class TestDegradedMode:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch):
        TestClient, app = _import_app()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.main.run_preflight_checks"):
            with TestClient(app) as c:
                c.post("/api/internal/reset-disclosure")
                c.post("/api/disclosure/accept")
                yield c

    def test_budget_exceeded_returns_degraded_not_500(self, client) -> None:
        from coursegen.llm.client import BudgetExceeded
        with patch("coursegen.app.main._run_exam_pipeline",
                   side_effect=BudgetExceeded("per-exam call cap reached")):
            resp = client.post("/api/exam", json={"blueprint_id": "quiz_default", "title": "Test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert "fill_ratio" in body
        assert "unfilled_slots" in body
        # No stack trace in the response body
        assert "Traceback" not in json.dumps(body)
        assert "BudgetExceeded" not in json.dumps(body)

    def test_network_error_during_generation_returns_degraded_not_500(self, client) -> None:
        import httpx
        with patch("coursegen.app.main._run_exam_pipeline",
                   side_effect=httpx.TimeoutException("timed out")):
            resp = client.post("/api/exam", json={"blueprint_id": "quiz_default", "title": "Test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert "Traceback" not in json.dumps(body)


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

class TestChatEndpoint:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch):
        TestClient, app = _import_app()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.main.run_preflight_checks"):
            with TestClient(app) as c:
                yield c

    def test_grounded_query_returns_answer_with_citations(self, client) -> None:
        grounded_answer = {
            "answer": "TCP is a transport protocol.",
            "citations": [{"file": "slides.pptx", "page": 7}],
            "from_material": True,
        }
        with patch("coursegen.app.main._run_chat_query", return_value=grounded_answer):
            resp = client.post("/api/chat",
                               json={"query": "What is TCP?", "history": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["from_material"] is True
        assert body["citations"][0]["file"] == "slides.pptx"
        assert body["citations"][0]["page"] == 7

    def test_ungrounded_query_returns_not_from_material_marker(self, client) -> None:
        ungrounded = {
            "answer": "Not from your material: general knowledge answer.",
            "citations": [],
            "from_material": False,
        }
        with patch("coursegen.app.main._run_chat_query", return_value=ungrounded):
            resp = client.post("/api/chat",
                               json={"query": "Who won the world cup?", "history": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["from_material"] is False
        assert body["citations"] == []
        assert "not from your material" in body["answer"].lower()


# ---------------------------------------------------------------------------
# Real pipeline wiring — _run_exam_pipeline calls P1-P4 stages
# ---------------------------------------------------------------------------

class TestExamPipelineWiring:
    """
    _run_exam_pipeline must call the real P1-P4 stages (load_course_map, solve,
    generate_exam, render_exam_artifacts) and return a dict with the expected keys.
    These tests patch each stage so no real I/O or LLM calls occur.
    """

    def _fake_nodes(self):
        from coursegen.contracts.course_map import CourseMapNode, NodeFlags
        return [
            CourseMapNode(
                node_id="n1", path=["Topic"], source_file="slides.pdf",
                page_span=(1, 5), key_terms=[], token_count=100,
                instructional_mass=1.0, chunk_ids=["c1"], flags=NodeFlags(),
            )
        ]

    def _fake_blueprint(self):
        from coursegen.contracts.blueprint import Blueprint, SectionSpec
        return Blueprint(
            blueprint_id="quiz_default", title="Quiz", total_marks=10,
            duration_minutes=30,
            sections=[SectionSpec(
                section_id="A", title="MCQ", item_type="mcq",
                count=2, marks_each=5, bloom=["remember"],
            )],
        )

    def _fake_specs_and_coverage(self, nodes, blueprint):
        from coursegen.exam.allocate import solve
        # Use real solve with real fixtures so we don't need to build ItemSpec manually.
        # This is acceptable: solve is pure deterministic code with no I/O.
        return solve(nodes, blueprint)

    def test_pipeline_reports_empty_when_no_items_were_generated(self, tmp_path, monkeypatch) -> None:
        """A run that produced zero items must not report "ok".

        This test previously asserted `status == "ok"` while handing the pipeline
        `GenerationResult(items=[])` — encoding the position that a paper with no
        questions in it is a success. `status` is now derived from the items, so
        the empty case says so and the populated case below says "ok".
        """
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        from coursegen.app.main import _run_exam_pipeline
        from coursegen.contracts.coverage import CoverageReport
        from coursegen.exam.render import RenderArtifacts
        from coursegen.exam.generate import GenerationResult

        nodes = self._fake_nodes()
        blueprint = self._fake_blueprint()
        specs, coverage = self._fake_specs_and_coverage(nodes, blueprint)

        fake_artifacts = RenderArtifacts(
            exam_html=tmp_path / "exam.html",
            answer_key_html=tmp_path / "answer_key.html",
            coverage_html=tmp_path / "coverage.html",
            exam_pdf=tmp_path / "exam.pdf",
            answer_key_pdf=tmp_path / "answer_key.pdf",
        )
        fake_result = GenerationResult(items=[], manifest={})

        with patch("coursegen.app.main._load_blueprint", return_value=blueprint), \
             patch("coursegen.ingest.coursemap.load_course_map", return_value=nodes), \
             patch("coursegen.exam.allocate.solve", return_value=(specs, coverage)), \
             patch("coursegen.app.main._fetch_spans", return_value=({}, {})), \
             patch("coursegen.exam.generate.generate_exam", return_value=fake_result), \
             patch("coursegen.exam.render.render_exam_artifacts", return_value=fake_artifacts):
            result = _run_exam_pipeline("quiz_default", "My Exam")

        assert result["status"] == "empty"
        assert result["items_count"] == 0
        # coverage_ratio is deliberately absent from the API payload: it measures
        # against the whole corpus and reads ~0.04 on a complete authored-blueprint
        # paper. See the note in _run_exam_pipeline. fill_ratio is the honest
        # headline and is asserted above.
        assert "coverage_ratio" not in result
        assert "fill_ratio" in result
        assert "allocation_fidelity" in result
        assert "downloads" in result
        assert "exam_html" in result["downloads"]

    def test_pipeline_reports_ok_when_items_were_generated(self, tmp_path, monkeypatch) -> None:
        """The populated path. Without this, nothing asserts "ok" is ever reachable."""
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        from coursegen.app.main import _run_exam_pipeline
        from coursegen.exam.render import RenderArtifacts
        from coursegen.exam.generate import GenerationResult
        from coursegen.contracts.item import GeneratedItem, SourceRef

        nodes = self._fake_nodes()
        blueprint = self._fake_blueprint()
        specs, coverage = self._fake_specs_and_coverage(nodes, blueprint)

        item = GeneratedItem(
            slot_id=specs[0].slot_id,
            stem="What is a heuristic?",
            model_answer="An estimate of remaining cost.",
            explanation="Defined in the source span.",
            source_ref=SourceRef(file="slides.pdf", pages=[3]),
        )
        fake_artifacts = RenderArtifacts(
            exam_html=tmp_path / "exam.html",
            answer_key_html=tmp_path / "answer_key.html",
            coverage_html=tmp_path / "coverage.html",
            exam_pdf=tmp_path / "exam.pdf",
            answer_key_pdf=tmp_path / "answer_key.pdf",
        )

        with patch("coursegen.app.main._load_blueprint", return_value=blueprint), \
             patch("coursegen.ingest.coursemap.load_course_map", return_value=nodes), \
             patch("coursegen.exam.allocate.solve", return_value=(specs, coverage)), \
             patch("coursegen.app.main._fetch_spans", return_value=({}, {})), \
             patch("coursegen.app.main._get_reranker"), \
             patch("coursegen.app.main._get_embed_model"), \
             patch("coursegen.exam.generate.generate_exam",
                   return_value=GenerationResult(items=[item], manifest={})), \
             patch("coursegen.exam.render.render_exam_artifacts", return_value=fake_artifacts):
            result = _run_exam_pipeline("quiz_default", "My Exam")

        assert result["status"] == "ok"
        assert result["items_count"] == 1

    def test_unexpected_error_is_500_not_a_degraded_200(self, monkeypatch) -> None:
        """An unexpected exception must not be dressed up as a degraded result.

        `degraded` means "we ran and hit a known limit"; the two states R8
        specifies (BudgetExceeded, TimeoutException) keep their 200s and are
        covered above. Anything else is a failure, and a 200 carrying
        fill_ratio 0.0 is indistinguishable from a real paper that filled nothing.
        """
        from fastapi.testclient import TestClient
        from coursegen.app.main import app

        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with TestClient(app, raise_server_exceptions=False) as c:
            c.post("/api/internal/reset-disclosure")
            c.post("/api/disclosure/accept")
            with patch("coursegen.app.main._run_exam_pipeline",
                       side_effect=KeyError("course_map missing")):
                resp = c.post("/api/exam",
                              json={"blueprint_id": "quiz_default", "title": "T"})

        assert resp.status_code == 500
        body = json.dumps(resp.json())
        assert "Traceback" not in body and "KeyError" not in body  # R4

    def test_unknown_blueprint_raises_value_error(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        from coursegen.app.main import _run_exam_pipeline
        with pytest.raises((ValueError, FileNotFoundError)):
            _run_exam_pipeline("nonexistent_blueprint_xyz", "Title")


# ---------------------------------------------------------------------------
# Real chat wiring — _run_chat_query calls P4 chat path
# ---------------------------------------------------------------------------

class TestChatQueryWiring:
    """
    _run_chat_query must call answer_question with real retrieve/rerank callables
    and return {answer, citations, from_material}.
    """

    def test_wiring_returns_answer_dict(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        from coursegen.app.main import _run_chat_query
        from coursegen.chat.answer import ChatAnswer

        fake_answer = ChatAnswer(
            answer="TCP is a protocol.",
            citations=[{"file": "slides.pptx", "page": 3}],
            from_material=True,
            sent_context_count=2,
            history_turns_used=0,
        )

        with patch("coursegen.app.main._get_embed_model"), \
             patch("coursegen.app.main._get_reranker"), \
             patch("coursegen.chat.answer.answer_question", return_value=fake_answer):
            result = _run_chat_query("What is TCP?", [])

        assert result["answer"] == "TCP is a protocol."
        assert result["from_material"] is True
        assert result["citations"] == [{"file": "slides.pptx", "page": 3}]


# ---------------------------------------------------------------------------
# New UI endpoints
# ---------------------------------------------------------------------------

class TestNewEndpoints:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch):
        TestClient, app = _import_app()
        monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
        with patch("coursegen.app.main.run_preflight_checks"):
            with TestClient(app) as c:
                yield c

    def test_root_returns_html(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<html" in resp.text.lower()

    def test_blueprints_endpoint_returns_list(self, client) -> None:
        resp = client.get("/api/blueprints")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert "quiz_default" in body

    def test_ingest_endpoint_accepts_file_upload(self, client, tmp_path) -> None:
        fake_nodes = []
        with patch("coursegen.ingest.coursemap.ingest", return_value=fake_nodes):
            resp = client.post(
                "/api/ingest",
                files=[("files", ("test.pdf", b"%PDF-1.4 fake content", "application/pdf"))],
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes_ingested" in body

    def test_files_endpoint_serves_output_file(self, client, tmp_path, monkeypatch) -> None:
        from coursegen import config
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
        (tmp_path / "exam.html").write_text("<html>exam</html>", encoding="utf-8")
        resp = client.get("/api/files/exam.html")
        assert resp.status_code == 200

    def test_files_endpoint_404_for_missing_file(self, client, tmp_path, monkeypatch) -> None:
        from coursegen import config
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
        resp = client.get("/api/files/does_not_exist.pdf")
        assert resp.status_code == 404
