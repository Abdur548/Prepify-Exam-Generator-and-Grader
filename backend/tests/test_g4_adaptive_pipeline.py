"""G4 — the adaptive next paper, through the pipeline and the API.

`adaptive=False` must be exactly MVP 1. `adaptive=True` tilts the solver by the
student's mastery and asks for new wording wherever they have already seen a
question — and with no history at all, it says the paper is not adapted rather
than implying it was.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.item import GeneratedItem, MCQOption, SourceRef
from coursegen.exam.allocate import solve
from coursegen.exam.paper import build_paper
from coursegen.grade.adaptive import adapt
from coursegen.grade.service import contest, submit_attempt
from coursegen.grade.sheets import archive_paper
from coursegen.grade.store import Store
from coursegen.ingest.coursemap import save_course_map
from coursegen.pipeline import load_blueprint

NOW = "2026-10-13T10:00:00"
FIXTURE = Path(__file__).parent / "fixtures" / "course_map_sample.json"
_TEST_API_KEY = "test-key-not-a-real-credential"


def _nodes() -> list[CourseMapNode]:
    return [CourseMapNode(**n) for n in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def _items_for(specs) -> list[GeneratedItem]:
    out = []
    for s in specs:
        mcq = s.item_type == "mcq"
        out.append(GeneratedItem(
            slot_id=s.slot_id, stem=f"Question about {s.node_id} ({s.slot_id})?",
            options=[MCQOption(label="A", text="no"), MCQOption(label="B", text="yes")] if mcq else None,
            correct_option="B" if mcq else None, model_answer="B. yes" if mcq else "An answer.",
            explanation="because", source_ref=SourceRef(file="deck.pdf", pages=[1]),
        ))
    return out


def _take_base_paper(store, nodes, blueprint_id="quiz_default", right=True) -> list:
    """Generate (offline) and answer the untilted paper. `right`: full marks on
    everything, else zero on everything."""
    bp = load_blueprint(blueprint_id)
    specs, coverage = solve(nodes, bp)
    paper = build_paper(items=_items_for(specs), specs=specs, blueprint=bp,
                        coverage=coverage, title="Base", span_text_by_id={})
    sheet_id = archive_paper(paper, specs, now=NOW)["sheet_id"]
    answers = {s.slot_id: ("B" if right else "A") if s.item_type == "mcq" else "an answer"
               for s in specs}
    result = submit_attempt(store, sheet_id, answers, now=NOW)
    for s in specs:
        if s.item_type != "mcq":
            contest(store, result["attempt_id"], s.slot_id, s.marks if right else 0, now=NOW)
    return specs


class TestAdapt:

    def test_with_no_history_it_is_the_ordinary_paper_and_says_so(self) -> None:
        nodes, bp = _nodes(), load_blueprint("quiz_default")
        with Store() as store:
            result = adapt(nodes, bp, store)
        assert result.applied is False and result.reworded == 0 and result.tilted_nodes == 0
        assert [s.model_dump() for s in result.specs] == [s.model_dump() for s in solve(nodes, bp)[0]]

    def test_mastered_material_makes_room_for_untested_material(self) -> None:
        nodes, bp = _nodes(), load_blueprint("quiz_default")
        with Store() as store:
            base = _take_base_paper(store, nodes, right=True)
            result = adapt(nodes, bp, store)
        assert result.applied and result.tilted_nodes == len({s.node_id for s in base})
        moved = sum(1 for a, b in zip(result.base_specs, result.specs) if a.node_id != b.node_id)
        assert moved > 0

    def test_no_question_the_student_has_seen_comes_back_unchanged(self) -> None:
        nodes, bp = _nodes(), load_blueprint("quiz_default")
        with Store() as store:
            base = _take_base_paper(store, nodes, right=False)
            seen = store.submitted_spec_hashes()
            result = adapt(nodes, bp, store)
        assert seen == {s.spec_hash for s in base}
        assert not {s.spec_hash for s in result.specs} & seen
        assert result.reworded == sum(1 for s in result.specs if s.avoid_stems)


def _pipeline(tmp_path, monkeypatch, adaptive: bool):
    """Run generate_paper offline; return (specs sent to generation, result)."""
    from coursegen.exam.generate import GenerationResult
    from coursegen.pipeline import generate_paper

    sent = {}

    def fake_generate_exam(**kwargs):
        sent["specs"] = kwargs["specs"]
        return GenerationResult(items=_items_for(kwargs["specs"]), manifest={})

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    with patch("coursegen.ingest.coursemap.load_course_map", return_value=_nodes()), \
         patch("coursegen.ingest.index.read_spans", return_value=({}, {})), \
         patch("coursegen.exam.generate.generate_exam", side_effect=fake_generate_exam), \
         patch("coursegen.exam.render._write_pdf_with_weasyprint",
               side_effect=lambda html, path: path.write_bytes(b"%PDF")):
        result = generate_paper(blueprint_id="quiz_default", title="Next", llm_client=object(),
                                adaptive=adaptive)
    return sent["specs"], result


class TestPipeline:

    def test_not_adaptive_is_the_ordinary_solver(self, tmp_path, monkeypatch) -> None:
        with Store() as store:
            _take_base_paper(store, _nodes(), right=False)
        specs, result = _pipeline(tmp_path, monkeypatch, adaptive=False)
        assert [s.model_dump() for s in specs] == [s.model_dump() for s in solve(_nodes(), load_blueprint("quiz_default"))[0]]
        assert "adaptive" not in result.manifest

    def test_adaptive_records_what_it_did(self, tmp_path, monkeypatch) -> None:
        with Store() as store:
            _take_base_paper(store, _nodes(), right=False)
            seen = store.submitted_spec_hashes()
        specs, result = _pipeline(tmp_path, monkeypatch, adaptive=True)
        assert result.manifest["adaptive"]["applied"] is True
        assert result.manifest["adaptive"]["reworded"] > 0
        assert not {s.spec_hash for s in specs} & seen

    def test_adaptive_with_no_history_warns_on_the_paper(self, tmp_path, monkeypatch) -> None:
        _specs, result = _pipeline(tmp_path, monkeypatch, adaptive=True)
        assert result.manifest["adaptive"]["applied"] is False
        assert "No answers yet — this paper is not adapted." in result.paper["summary"]["warnings"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from coursegen.app.main import app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    monkeypatch.setattr(config, "COURSE_MAP_PATH", tmp_path / "course_map.json")
    save_course_map(_nodes(), config.COURSE_MAP_PATH)
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            c.post("/api/internal/reset-disclosure")
            c.post("/api/disclosure/accept")
            yield c


class TestRoutes:

    def test_the_plan_preview_shows_what_the_tilt_changed(self, client) -> None:
        with Store() as store:
            _take_base_paper(store, _nodes(), right=True)
        plain = client.post("/api/plan", json={"blueprint_id": "quiz_default"}).json()
        tilted = client.post("/api/plan", json={"blueprint_id": "quiz_default", "adaptive": True}).json()
        assert "adaptive" not in plain
        assert tilted["adaptive"]["applied"] is True and tilted["adaptive"]["changed_slots"] > 0

    def test_the_plan_preview_says_when_there_is_nothing_to_adapt_to(self, client) -> None:
        tilted = client.post("/api/plan", json={"blueprint_id": "quiz_default", "adaptive": True}).json()
        assert tilted["adaptive"] == {"applied": False, "tilted_nodes": 0, "changed_slots": 0,
                                      "reworded": 0, "orphans": 0}

    @pytest.mark.parametrize("route", ["/api/exam", "/api/exam/stream"])
    def test_the_flag_reaches_generation(self, client, route) -> None:
        seen = {}

        def fake(blueprint_id, title, on_event=None, adaptive=False):
            seen["adaptive"] = adaptive
            return {"status": "ok", "fill_ratio": 1.0, "allocation_fidelity": 1.0,
                    "unfilled_slots": [], "items": [], "warnings": []}

        with patch("coursegen.app.main._run_exam_pipeline", side_effect=fake):
            res = client.post(route, json={"blueprint_id": "quiz_default", "title": "T", "adaptive": True})
            _ = res.text
        assert seen == {"adaptive": True}

    def test_the_flag_defaults_to_off(self, client) -> None:
        seen = {}

        def fake(blueprint_id, title, on_event=None, adaptive=False):
            seen["adaptive"] = adaptive
            return {"status": "ok", "fill_ratio": 1.0, "allocation_fidelity": 1.0,
                    "unfilled_slots": [], "items": [], "warnings": []}

        with patch("coursegen.app.main._run_exam_pipeline", side_effect=fake):
            client.post("/api/exam", json={"blueprint_id": "quiz_default", "title": "T"})
        assert seen == {"adaptive": False}
