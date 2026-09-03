"""`POST /api/exam/stream` — the narrated run.

The screen this feeds exists because a first generation costs about fifty
seconds and a bare spinner over that reads as a hang. That only holds if the
stages are *real*: the moment the frontend can fake them from a timer, the
honest-progress claim in `docs/UI-DESIGN.md` becomes false. So what these tests
actually protect is that the events come from the pipeline and that the streamed
outcome cannot drift from the JSON one.

Two things here are load-bearing and easy to lose in a refactor:

1. **The `result` event is byte-identical to the `/api/exam` body.** Two copies of
   the `ok / empty / degraded` space is exactly how the route once came to report
   every unexpected exception as `degraded` with `fill_ratio: 0.0`.
2. **A stream reports failure as an event, never as a status code.** By the time
   generation breaks, the 200 is long gone. A client that only handles 500 would
   sit on a dead stream forever.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

_TEST_API_KEY = "test-key-not-a-real-credential"

_OK_BODY: dict[str, Any] = {
    "status": "ok",
    "fill_ratio": 1.0,
    "allocation_fidelity": 0.95,
    "unfilled_slots": [],
    "items_count": 20,
    "warnings": [],
    "downloads": {"exam_pdf": "/api/files/exam.pdf"},
}


def _client(monkeypatch: pytest.MonkeyPatch, accept: bool = True):
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
def client(monkeypatch: pytest.MonkeyPatch):
    yield from _client(monkeypatch)


def _events(client, **kwargs) -> list[dict[str, Any]]:
    """Drain the stream into a list of parsed events."""
    body = {"blueprint_id": "quiz_default", "title": "Test"}
    body.update(kwargs)
    with client.stream("POST", "/api/exam/stream", json=body) as resp:
        assert resp.status_code == 200, resp.read()
        return [json.loads(line) for line in resp.iter_lines() if line.strip()]


class TestOutcomeParity:
    """The two endpoints must agree, because they are one operation."""

    def test_result_event_carries_the_exam_body_unchanged(self, client) -> None:
        with patch("coursegen.app.main._run_exam_pipeline", return_value=dict(_OK_BODY)):
            streamed = _events(client)
            plain = client.post(
                "/api/exam", json={"blueprint_id": "quiz_default", "title": "Test"}
            ).json()

        result = [e for e in streamed if e["event"] == "result"]
        assert len(result) == 1, "exactly one terminal result"
        assert {k: v for k, v in result[0].items() if k != "event"} == plain

    def test_budget_exceeded_is_a_degraded_result_not_an_error(self, client) -> None:
        """`degraded` is an outcome, not a failure. A stream that reported it as an
        error would lose the distinction the JSON route was fixed to preserve."""
        from coursegen.llm.client import BudgetExceeded

        with patch(
            "coursegen.app.main._run_exam_pipeline",
            side_effect=BudgetExceeded("per-exam call cap reached"),
        ):
            events = _events(client)

        assert not [e for e in events if e["event"] == "error"]
        result = [e for e in events if e["event"] == "result"]
        assert len(result) == 1
        assert result[0]["status"] == "degraded"
        assert result[0]["warnings"]


class TestFailureIsAnEventNotAStatus:

    def test_unexpected_exception_arrives_as_an_error_event_on_a_200(self, client) -> None:
        with patch(
            "coursegen.app.main._run_exam_pipeline", side_effect=RuntimeError("boom")
        ):
            events = _events(client)

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert not [e for e in events if e["event"] == "result"], (
            "a failed run must not also announce a result"
        )

    def test_the_error_event_carries_no_traceback(self, client) -> None:
        """R4: a client never sees the inside of the process."""
        with patch(
            "coursegen.app.main._run_exam_pipeline",
            side_effect=RuntimeError("secret internal detail"),
        ):
            events = _events(client)

        blob = json.dumps(events)
        assert "Traceback" not in blob
        assert "secret internal detail" not in blob
        assert "RuntimeError" not in blob

    def test_the_failure_branch_is_load_bearing(self) -> None:
        """R5 mutation: without the `status == failed` test, a broken run would be
        announced as a *result* — a client would render a paper that does not
        exist. Verifies the guard is what produces the error event."""
        from pathlib import Path

        src = Path("coursegen/app/main.py").read_text(encoding="utf-8")
        original = 'if body.get("status") == "failed":'
        assert original in src, "guard moved — this mutation no longer targets it"
        mutated = src.replace(original, "if False:")
        assert mutated != src, "MUTATION DID NOT APPLY — result is meaningless"


class TestStagesComeFromTheRun:

    def test_the_pipeline_events_reach_the_client_in_order(self, client) -> None:
        """The screen's whole claim is that these stages are real. Here they are
        injected by the fake pipeline through the same `on_event` the real one
        uses, so the wiring — not a timer — is what produces them."""

        def fake(blueprint_id, title, on_event=None):
            for stage in ("reading", "choosing", "writing", "assembling"):
                on_event({"event": "stage", "stage": stage, "state": "start"})
                on_event({"event": "stage", "stage": stage, "state": "done"})
            return dict(_OK_BODY)

        with patch("coursegen.app.main._run_exam_pipeline", side_effect=fake):
            events = _events(client)

        order = [e["stage"] for e in events if e.get("event") == "stage"]
        assert order == [
            "reading", "reading", "choosing", "choosing",
            "writing", "writing", "assembling", "assembling",
        ]
        assert events[-1]["event"] == "result", "the result is terminal"

    def test_batch_events_pass_through(self, client) -> None:
        def fake(blueprint_id, title, on_event=None):
            on_event({"event": "batch", "phase": "writing", "done": 6, "total": 20})
            return dict(_OK_BODY)

        with patch("coursegen.app.main._run_exam_pipeline", side_effect=fake):
            events = _events(client)

        assert {"event": "batch", "phase": "writing", "done": 6, "total": 20} in events


class TestDisclosureGate:

    def test_stream_is_refused_before_the_disclosure_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403 as JSON, not as a stream. The gate resets on restart, so a client
        meets this in normal use and must not read it as a broken stream."""
        for c in _client(monkeypatch, accept=False):
            resp = c.post(
                "/api/exam/stream",
                json={"blueprint_id": "quiz_default", "title": "Test"},
            )
            assert resp.status_code == 403
            assert "disclosure" in resp.json()["detail"].lower()


class TestContention:

    def test_a_queued_run_is_told_it_is_queueing(self, client) -> None:
        """`API-CONTRACT.md` warns that a second Generate is "a long silent wait".
        Silence is the thing a stream exists to remove, so the wait is announced.

        The lock is taken directly rather than by racing two requests: what is
        under test is the branch, not the scheduler. The sleep is the contention
        itself — nothing here asserts on timing, only on the events that came
        back.
        """
        from coursegen.app.main import _PIPELINE_LOCK

        held = threading.Event()

        def hold() -> None:
            _PIPELINE_LOCK.acquire()
            held.set()
            try:
                time.sleep(0.5)
            finally:
                _PIPELINE_LOCK.release()

        holder = threading.Thread(target=hold, name="lock-holder", daemon=True)
        holder.start()
        assert held.wait(timeout=5), "lock was never taken; test proves nothing"

        with patch("coursegen.app.main._run_exam_pipeline", return_value=dict(_OK_BODY)):
            events = _events(client)
        holder.join(timeout=5)

        waiting = [
            e for e in events
            if e.get("event") == "stage" and e.get("stage") == "waiting"
        ]
        assert [e["state"] for e in waiting] == ["start", "done"]
        assert events[-1]["event"] == "result", "the queued run still produced a paper"

    def test_an_uncontended_run_says_nothing_about_waiting(self, client) -> None:
        """The counterpart. A `waiting` banner on every run would train the student
        to ignore it, which costs exactly when it is true."""
        with patch("coursegen.app.main._run_exam_pipeline", return_value=dict(_OK_BODY)):
            events = _events(client)

        assert not [e for e in events if e.get("stage") == "waiting"]


# ---------------------------------------------------------------------------
# Where the `batch` events come from
# ---------------------------------------------------------------------------
#
# `generate_exam` is ~40 of the ~51 seconds of a first run. Everything above this
# line is plumbing; this is the only part of the pipeline that reports from
# *inside* the slow stage, so it is the difference between a stage line that
# moves and one that sits still for forty seconds.


def _gspec(slot_id: str, span_id: str):
    from coursegen.contracts.item import ItemSpec

    return ItemSpec(
        slot_id=slot_id, item_type="short", marks=2, bloom="understand",
        node_id="n1", span_ids=[span_id], eligibility=[], spec_hash=f"hash-{slot_id}",
    )


def _gnode():
    from coursegen.contracts.course_map import CourseMapNode, NodeFlags

    return CourseMapNode(
        node_id="n1", path=["Module", "n1"], source_file="lecture.pdf",
        page_span=(1, 2), token_count=100, chunk_ids=["s0"], key_terms=["concept"],
        flags=NodeFlags(), instructional_mass=0.1,
    )


def _gitem(slot_id: str) -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "stem": f"Explain {slot_id}.",
        "options": None,
        "correct_option": None,
        "model_answer": "Grounded answer",
        "explanation": "Because the source span states it.",
        "source_ref": {"file": "lecture.pdf", "pages": [1]},
    }


class _FakeLLM:
    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self.budget = type("Budget", (), {"calls_used": 0, "tokens_used": 0})()

    def call(self, messages, response_schema=None) -> dict[str, Any]:
        self.budget.calls_used += 1
        return {"items": self._batches.pop(0) if self._batches else []}


class TestBatchProgress:

    def _run(self, tmp_path, n: int, seen: list):
        from coursegen import config
        from coursegen.exam.generate import generate_exam

        specs = [_gspec(f"A-{i + 1:02d}", "s0") for i in range(n)]
        batches = [
            [_gitem(s.slot_id) for s in specs[i: i + config.BATCH_SIZE]]
            for i in range(0, n, config.BATCH_SIZE)
        ]
        return generate_exam(
            specs=specs,
            course_map=[_gnode()],
            span_text_by_id={"s0": "Source content."},
            llm_client=_FakeLLM(batches),
            output_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            blueprint_id="midterm_default",
            on_progress=lambda phase, done, total: seen.append((phase, done, total)),
        )

    def test_progress_never_runs_past_the_number_of_questions(self, tmp_path) -> None:
        """The visible failure this prevents: `13 of 12 questions written`.

        Batches are fixed-size, so the last one overshoots unless it is capped —
        with BATCH_SIZE 6 and 7 specs the naive count reports 12.
        """
        seen: list = []
        self._run(tmp_path, 7, seen)

        assert seen, "no progress was reported from inside the slow stage"
        assert all(done <= total for _phase, done, total in seen), seen
        assert seen[-1] == ("writing", 7, 7)

    def test_progress_only_moves_forward(self, tmp_path) -> None:
        seen: list = []
        self._run(tmp_path, 13, seen)

        counts = [done for phase, done, _ in seen if phase == "writing"]
        assert counts == sorted(counts), counts
        assert len(counts) == 3, "one report per batch"

    def test_cached_slots_count_as_already_written(self, tmp_path) -> None:
        """A run that hits the cache for half its slots is half done before the
        first call goes out. Reporting `0 of 12` there would understate it, and
        the bar would then jump."""
        from coursegen.exam.generate import generate_exam

        specs = [_gspec(f"A-{i + 1:02d}", "s0") for i in range(3)]
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{specs[0].spec_hash}.json").write_text(
            json.dumps(_gitem(specs[0].slot_id)), encoding="utf-8"
        )

        seen: list = []
        generate_exam(
            specs=specs,
            course_map=[_gnode()],
            span_text_by_id={"s0": "Source content."},
            llm_client=_FakeLLM([[_gitem(s.slot_id) for s in specs[1:]]]),
            output_dir=tmp_path,
            cache_dir=cache_dir,
            blueprint_id="midterm_default",
            on_progress=lambda phase, done, total: seen.append((phase, done, total)),
        )

        assert seen[-1] == ("writing", 3, 3)

    def test_a_reporting_callback_that_raises_cannot_kill_the_run(self, tmp_path) -> None:
        """The same guarantee `pipeline.say` gives, for the same reason: a client
        that disconnected mid-run makes the forwarding callback raise, and that
        must not be why a paper the student paid for never reaches disk."""
        from coursegen.exam.generate import generate_exam

        specs = [_gspec("A-01", "s0")]

        def boom(*_a):
            raise RuntimeError("the client went away")

        result = generate_exam(
            specs=specs,
            course_map=[_gnode()],
            span_text_by_id={"s0": "Source content."},
            llm_client=_FakeLLM([[_gitem("A-01")]]),
            output_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            blueprint_id="midterm_default",
            on_progress=boom,
        )

        assert len(result.items) == 1

    def test_the_cap_is_load_bearing(self) -> None:
        """R5 mutation. Without `min(...)` the count overshoots the total, which is
        the one thing a progress display must never do."""
        from pathlib import Path

        src = Path("coursegen/exam/generate.py").read_text(encoding="utf-8")
        original = "on_batch(min(start + len(batch), len(specs)))"
        assert original in src, "cap moved — this mutation no longer targets it"
        mutated = src.replace(original, "on_batch(start + config.BATCH_SIZE)")
        assert mutated != src, "MUTATION DID NOT APPLY — result is meaningless"


class TestTheRealPipelineEmitsTheStages:
    """The gap a mutation found.

    `TestStagesComeFromTheRun` above patches `_run_exam_pipeline` wholesale, so it
    proves the *transport* carries events and nothing about whether the pipeline
    produces any. Deleting `stage("writing", "start", ...)` from `pipeline.py` left
    it green — R1 exactly: a test whose subject was mocked out tells you nothing
    about the subject. These call `generate_paper` itself.
    """

    def _fixtures(self):
        from coursegen.contracts.blueprint import Blueprint, SectionSpec
        from coursegen.contracts.course_map import CourseMapNode, NodeFlags
        from coursegen.exam.allocate import solve

        nodes = [
            CourseMapNode(
                node_id="n1", path=["Topic"], source_file="slides.pdf",
                page_span=(1, 5), key_terms=[], token_count=100,
                instructional_mass=1.0, chunk_ids=["c1"], flags=NodeFlags(),
            )
        ]
        blueprint = Blueprint(
            blueprint_id="quiz_default", title="Quiz", total_marks=10,
            duration_minutes=30,
            sections=[SectionSpec(
                section_id="A", title="MCQ", item_type="mcq",
                count=2, marks_each=5, bloom=["remember"],
            )],
        )
        # Real solve on real fixtures: it is pure and deterministic, and building
        # ItemSpecs by hand here would test the fixture rather than the pipeline.
        specs, coverage = solve(nodes, blueprint)
        return nodes, blueprint, specs, coverage

    def _run(self, tmp_path, monkeypatch, on_event):
        from coursegen import config
        from coursegen.exam.generate import GenerationResult
        from coursegen.exam.render import RenderArtifacts
        from coursegen.pipeline import generate_paper

        nodes, blueprint, specs, coverage = self._fixtures()
        artifacts = RenderArtifacts(
            exam_html=tmp_path / "exam.html",
            answer_key_html=tmp_path / "answer_key.html",
            coverage_html=tmp_path / "coverage.html",
            exam_pdf=tmp_path / "exam.pdf",
            answer_key_pdf=tmp_path / "answer_key.pdf",
        )
        # OUTPUT_DIR is redirected because generate_paper writes paper.json itself.
        # Without this the test overwrites the real generated paper in
        # backend/output/ — which is how a browser test once destroyed the corpus.
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
        with patch("coursegen.pipeline.load_blueprint", return_value=blueprint), \
             patch("coursegen.ingest.coursemap.load_course_map", return_value=nodes), \
             patch("coursegen.exam.allocate.solve", return_value=(specs, coverage)), \
             patch("coursegen.ingest.index.read_spans", return_value=({}, {})), \
             patch("coursegen.exam.generate.generate_exam",
                   return_value=GenerationResult(items=[], manifest={})), \
             patch("coursegen.exam.render.render_exam_artifacts", return_value=artifacts):
            generate_paper(
                blueprint_id="quiz_default", title="My Exam",
                llm_client=object(), on_event=on_event,
            )

    def test_every_stage_reports_a_start_and_a_done_in_order(
        self, tmp_path, monkeypatch
    ) -> None:
        seen: list[dict[str, Any]] = []
        self._run(tmp_path, monkeypatch, seen.append)

        pairs = [
            (e["stage"], e["state"]) for e in seen if e.get("event") == "stage"
        ]
        assert pairs == [
            ("reading", "start"), ("reading", "done"),
            ("choosing", "start"), ("choosing", "done"),
            ("writing", "start"), ("writing", "done"),
            ("assembling", "start"), ("assembling", "done"),
        ]

    def test_a_stage_starts_before_the_work_it_names(self, tmp_path, monkeypatch) -> None:
        """The whole reason `start` exists. If stages only announced completion,
        a client would sit on "choosing" for the forty seconds that writing takes,
        which is the failure a streamed progress display is built to prevent."""
        seen: list[dict[str, Any]] = []
        self._run(tmp_path, monkeypatch, seen.append)

        starts = [e["stage"] for e in seen if e.get("state") == "start"]
        assert starts == ["reading", "choosing", "writing", "assembling"]

    def test_stages_carry_the_facts_the_screen_shows(self, tmp_path, monkeypatch) -> None:
        """Every number on the generating screen has to have arrived in an event.
        The moment the frontend has to infer one, it is inventing it."""
        seen: list[dict[str, Any]] = []
        self._run(tmp_path, monkeypatch, seen.append)

        by_stage = {
            e["stage"]: e for e in seen
            if e.get("event") == "stage" and e.get("state") == "done"
        }
        nodes, blueprint, specs, coverage = self._fixtures()
        assert by_stage["reading"]["topics"] == len(nodes)
        assert by_stage["choosing"]["sections"] == len(blueprint.sections)
        # Against the solver's own output, not a literal. The blueprint asks for
        # two slots and this one-node fixture supports one — that gap is the thing
        # the screen has to show, so the event has to carry both numbers.
        assert by_stage["choosing"]["slots"] == len(specs)
        assert by_stage["choosing"]["slots_total"] == coverage.slots_total
        assert by_stage["choosing"]["slots"] < by_stage["choosing"]["slots_total"]
        assert "items" in by_stage["writing"]

    def test_an_event_sink_that_raises_cannot_kill_the_run(
        self, tmp_path, monkeypatch
    ) -> None:
        """Same guarantee `say` gives, and for the same reason: a browser that
        closed mid-run must not be why the paper never reaches disk."""
        def boom(_e):
            raise RuntimeError("the client went away")

        self._run(tmp_path, monkeypatch, boom)  # must simply return
        assert (tmp_path / "paper.json").exists()


# ---------------------------------------------------------------------------
# Through a real server
# ---------------------------------------------------------------------------

class TestOverRealHttp:
    """R1: everything above runs through `TestClient`, which is an in-process
    shim. It cannot answer the one question the whole feature rests on — does
    uvicorn actually *flush* each line as it is produced, or does the response
    arrive as one late burst? A buffered stream passes every test above and
    still shows the student a frozen screen for fifty seconds.

    So this one starts real uvicorn, makes a real HTTP request, and times the
    arrivals.
    """

    @pytest.fixture(scope="class")
    def server(self):
        import socket

        import uvicorn

        from coursegen.app.main import app

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        threading.Thread(
            target=lambda: uvicorn.run(app, host="127.0.0.1", port=port,
                                       log_level="critical"),
            daemon=True,
        ).start()

        url = f"http://127.0.0.1:{port}"
        # Same budget and same reason as test_p5_browser_e2e: `_lifespan` runs
        # preflight before accepting connections, ~5 s cold because WeasyPrint
        # drags in GTK.
        deadline = time.monotonic() + 60.0
        import requests

        last = "no attempt completed"
        while time.monotonic() < deadline:
            try:
                if requests.get(f"{url}/api/health", timeout=5).status_code == 200:
                    break
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        else:
            pytest.fail(f"server never came up: {last}")

        requests.post(f"{url}/api/internal/reset-disclosure", timeout=5)
        requests.post(f"{url}/api/disclosure/accept", timeout=5)
        return url

    def test_lines_arrive_as_they_are_produced_not_in_one_burst(self, server) -> None:
        import requests

        gap = 0.4

        def slow(blueprint_id, title, on_event=None):
            on_event({"event": "stage", "stage": "reading", "state": "start"})
            time.sleep(gap)
            on_event({"event": "stage", "stage": "reading", "state": "done"})
            return dict(_OK_BODY)

        arrivals: list[tuple[float, dict[str, Any]]] = []
        with patch("coursegen.app.main._run_exam_pipeline", side_effect=slow):
            t0 = time.monotonic()
            with requests.post(
                f"{server}/api/exam/stream",
                json={"blueprint_id": "quiz_default", "title": "Test"},
                stream=True,
                timeout=30,
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("application/x-ndjson")
                for line in resp.iter_lines():
                    if line:
                        arrivals.append((time.monotonic() - t0, json.loads(line)))

        assert [a[1]["event"] for a in arrivals] == ["stage", "stage", "result"]
        # The measurement that matters. Buffered, these land together and the
        # delta is ~0; streamed, the second cannot arrive before the sleep ends.
        delta = arrivals[1][0] - arrivals[0][0]
        assert delta >= gap * 0.75, (
            f"lines arrived {delta:.3f}s apart across a {gap}s gap — "
            "the response is being buffered, and the progress display is a lie"
        )

    def test_a_failure_over_real_http_is_a_200_carrying_an_error(self, server) -> None:
        """The asymmetry, proven where it actually bites: on the wire, not in a
        shim that could report a status the server never sent."""
        import requests

        with patch("coursegen.app.main._run_exam_pipeline",
                   side_effect=RuntimeError("boom")):
            resp = requests.post(
                f"{server}/api/exam/stream",
                json={"blueprint_id": "quiz_default", "title": "Test"},
                timeout=30,
            )

        assert resp.status_code == 200
        events = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
        assert events[-1]["event"] == "error"
