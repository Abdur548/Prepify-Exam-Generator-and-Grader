"""Ingest as a background job — `/api/ingest/start` + `/api/ingest/status`.

Ingest costs about ten minutes. A student does not sit and watch that: they
switch tabs, shut the laptop, come back. So unlike generation, which is streamed,
this one keeps its state on the SERVER — the only place a reloaded page can go
looking. Most of what follows is about that property.

The other half is a security fix these tests exist to hold: the upload path had
no filename sanitisation, so a multipart field named `../../../x.pdf` wrote
outside the temp directory. `/api/files/{filename}` had been hardened on the way
out and the way in had been missed.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_TEST_API_KEY = "test-key-not-a-real-credential"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from coursegen.app.main import _IDLE_INGEST, _ingest_state, app

    monkeypatch.setenv("GEMINI_API_KEY", _TEST_API_KEY)
    with patch("coursegen.app.main.run_preflight_checks"):
        with TestClient(app) as c:
            _ingest_state.clear()
            _ingest_state.update(_IDLE_INGEST)
            yield c
    _ingest_state.clear()
    _ingest_state.update(_IDLE_INGEST)


def _upload(name: str, body: bytes = b"%PDF-1.4 fake"):
    return ("files", (name, body, "application/pdf"))


def _settle(client, timeout: float = 5.0) -> dict[str, Any]:
    """Poll status until the job reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/ingest/status").json()
        if state["status"] in ("done", "failed", "idle"):
            return state
        time.sleep(0.02)
    pytest.fail(f"job never settled; last status {state['status']!r}")


# ---------------------------------------------------------------------------
# The upload filename is attacker-controlled
# ---------------------------------------------------------------------------

class TestUploadNameCannotEscape:

    @pytest.mark.parametrize("raw,expected", [
        ("../../../Windows/Temp/pwned.pdf", "pwned.pdf"),
        ("..\\..\\..\\Windows\\Temp\\pwned.pdf", "pwned.pdf"),
        ("/etc/passwd", "passwd"),
        ("C:\\Users\\me\\notes.pdf", "notes.pdf"),
        ("..", "upload"),
        (".", "upload"),
        ("", "upload"),
        (None, "upload"),
        ("  ", "upload"),
        ("lecture 03.pdf", "lecture 03.pdf"),
    ])
    def test_names_reduce_to_a_bare_filename(self, raw, expected) -> None:
        from coursegen.app.main import _safe_upload_name

        assert _safe_upload_name(raw) == expected

    def test_a_traversing_upload_lands_inside_the_temp_directory(self, client) -> None:
        """The end-to-end version, because the unit test above would still pass if
        nothing called it.

        `dest.write_bytes(...)` on an unsanitised name is a write-anywhere
        primitive reachable from one unauthenticated multipart request.
        """
        seen: dict[str, Any] = {}

        def fake_ingest(source_dir, data_dir=None, on_event=None):
            seen["dir"] = Path(source_dir)
            seen["contents"] = sorted(p.name for p in Path(source_dir).iterdir())
            seen["escaped"] = [
                str(p) for p in Path(source_dir).rglob("*") if ".." in str(p)
            ]
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake_ingest):
            resp = client.post(
                "/api/ingest/start",
                files=[_upload("../../../pwned.pdf")],
            )
            assert resp.status_code == 200
            _settle(client)

        assert seen["contents"] == ["pwned.pdf"], seen["contents"]
        assert seen["escaped"] == []
        # And nothing was written above the temp directory.
        assert not (seen["dir"].parent / "pwned.pdf").exists()

    def test_the_blocking_route_is_sanitised_too(self, client) -> None:
        """`/api/ingest` is frozen in the contract and still reachable. A fix that
        only covered the new endpoint would leave the hole open on the old one."""
        seen: dict[str, Any] = {}

        def fake_ingest(source_dir, data_dir=None, on_event=None):
            seen["contents"] = sorted(p.name for p in Path(source_dir).iterdir())
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake_ingest):
            resp = client.post("/api/ingest", files=[_upload("../../../pwned.pdf")])

        assert resp.status_code == 200
        assert seen["contents"] == ["pwned.pdf"]

    def test_sanitisation_is_load_bearing(self) -> None:
        """R5 mutation. Without `Path(...).name` the traversal test above passes a
        path straight through to `write_bytes`."""
        src = Path("coursegen/app/main.py").read_text(encoding="utf-8")
        original = 'name = Path((raw or "").replace("\\\\", "/")).name.strip()'
        assert original in src, "guard moved — this mutation no longer targets it"
        mutated = src.replace(original, 'name = (raw or "").strip()')
        assert mutated != src, "MUTATION DID NOT APPLY — result is meaningless"


# ---------------------------------------------------------------------------
# The property the whole design exists for
# ---------------------------------------------------------------------------

class TestItSurvivesTheClientGoingAway:

    def test_start_returns_before_the_work_finishes(self, client) -> None:
        """A ten-minute request that only answers at the end cannot be shown to
        anyone. This is the difference between a job and a request."""
        release = threading.Event()

        def slow(source_dir, data_dir=None, on_event=None):
            release.wait(timeout=10)
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=slow):
            t0 = time.monotonic()
            resp = client.post("/api/ingest/start", files=[_upload("a.pdf")])
            elapsed = time.monotonic() - t0

            assert resp.status_code == 200
            assert resp.json()["started"] is True
            assert elapsed < 2.0, f"start blocked for {elapsed:.1f}s"
            # Still running, and saying so, while the caller is free.
            assert client.get("/api/ingest/status").json()["status"] == "running"

            release.set()
            _settle(client)

    def test_a_caller_that_knows_nothing_can_read_the_run(self, client) -> None:
        """What "survives a refresh" means in practice: the state is on the server,
        so a page that has just loaded and holds no connection can still find out
        what is happening. A streamed response could not do this."""
        release = threading.Event()

        def slow(source_dir, data_dir=None, on_event=None):
            release.wait(timeout=10)
            return [object(), object()]

        with patch("coursegen.ingest.coursemap.ingest", side_effect=slow):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])

            mid = client.get("/api/ingest/status").json()
            assert mid["status"] == "running"
            assert mid["started_at"] is not None
            assert mid["elapsed_seconds"] >= 0

            release.set()
            after = _settle(client)

        assert after["status"] == "done"
        assert after["nodes_ingested"] == 2
        assert after["finished_at"] is not None

    def test_elapsed_stops_moving_once_the_run_is_over(self, client) -> None:
        """Measured from the run, not from the poll. A finished job whose clock
        keeps climbing reads as one that never ended."""
        with patch("coursegen.ingest.coursemap.ingest", return_value=[]):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            _settle(client)

        first = client.get("/api/ingest/status").json()["elapsed_seconds"]
        time.sleep(0.2)
        assert client.get("/api/ingest/status").json()["elapsed_seconds"] == first

    def test_status_is_answerable_before_anything_has_ever_run(self, client) -> None:
        state = client.get("/api/ingest/status").json()
        assert state["status"] == "idle"
        assert state["files"] == []
        assert state["elapsed_seconds"] is None


class TestOneAtATime:

    def test_a_second_start_is_refused_rather_than_queued(self, client) -> None:
        """Ingest writes one Qdrant collection and one course_map.json. A queued
        second upload would silently overwrite the first one's work."""
        release = threading.Event()

        def slow(source_dir, data_dir=None, on_event=None):
            release.wait(timeout=10)
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=slow):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            second = client.post("/api/ingest/start", files=[_upload("b.pdf")])

            assert second.status_code == 409
            assert "already" in second.json()["detail"].lower()

            release.set()
            _settle(client)

    def test_ingest_holds_the_pipeline_lock(self, client) -> None:
        """Ingest writes Qdrant; generation reads it; Qdrant takes an exclusive
        file lock (S8). Overlapping them fails the generation's span read. That was
        unlikely while ingest was a request someone waited on, and stops being
        unlikely once they can walk away and press Generate."""
        from coursegen.app.main import _PIPELINE_LOCK

        held: list[bool] = []
        release = threading.Event()

        def slow(source_dir, data_dir=None, on_event=None):
            held.append(_PIPELINE_LOCK.locked())
            release.wait(timeout=10)
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=slow):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            deadline = time.monotonic() + 5
            while not held and time.monotonic() < deadline:
                time.sleep(0.02)
            release.set()
            _settle(client)

        assert held == [True], "ingest ran without the pipeline lock"
        assert not _PIPELINE_LOCK.locked(), "lock not released"

    def test_a_queued_generation_is_told_what_it_is_waiting_for(self, client) -> None:
        """"Another paper is being generated" would be a false sentence, and one
        that understates a ten-minute wait as a one-minute one."""
        import json as _json

        import coursegen.app.main as main

        client.post("/api/disclosure/accept")
        events: list[dict[str, Any]] = []

        def read() -> None:
            with client.stream(
                "POST", "/api/exam/stream",
                json={"blueprint_id": "quiz_default", "title": "T"},
            ) as r:
                events.extend(
                    _json.loads(x) for x in r.iter_lines() if x.strip()
                )

        # The patch spans the JOIN, not just the sleep. Scoped to the sleep, it
        # lapsed while the queued thread was still blocked on the lock — so when
        # the lock freed, the thread ran the REAL pipeline and put a request on the
        # network. It failed on the fixture's fake key rather than spending
        # anything, which is luck, not design.
        with patch("coursegen.app.main._run_exam_pipeline",
                   return_value={"status": "ok", "fill_ratio": 1.0,
                                 "allocation_fidelity": 1.0, "unfilled_slots": [],
                                 "items_count": 0, "warnings": []}):
            main._pipeline_holder = "indexing your uploads"
            main._PIPELINE_LOCK.acquire()
            t = threading.Thread(target=read, daemon=True)
            try:
                t.start()
                time.sleep(0.4)
            finally:
                main._pipeline_holder = ""
                main._PIPELINE_LOCK.release()
            t.join(timeout=10)
        waiting = [e for e in events if e.get("stage") == "waiting"]
        assert waiting, "no waiting event"
        assert waiting[0]["holder"] == "indexing your uploads"


class TestWhatTheScreenShows:

    def test_each_file_is_one_row_that_changes_state(self, client) -> None:
        """Not a log. `reading` becomes `read` on the same row, because the screen
        renders a file list and a student looking for their deck wants one entry
        per deck."""
        def fake(source_dir, data_dir=None, on_event=None):
            on_event({"event": "file", "file": "a.pdf", "state": "reading"})
            on_event({"event": "file", "file": "b.pdf", "state": "reading"})
            on_event({"event": "file", "file": "a.pdf", "state": "read", "blocks": 42})
            on_event({"event": "file", "file": "b.pdf", "state": "failed",
                      "reason": "file is encrypted"})
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake):
            client.post("/api/ingest/start",
                        files=[_upload("a.pdf"), _upload("b.pdf")])
            state = _settle(client)

        rows = {f["file"]: f for f in state["files"]}
        assert len(state["files"]) == 2, state["files"]
        assert rows["a.pdf"]["state"] == "read"
        assert rows["a.pdf"]["blocks"] == 42
        assert rows["b.pdf"]["state"] == "failed"
        assert rows["b.pdf"]["reason"] == "file is encrypted"

    def test_stage_and_counts_come_from_the_run(self, client) -> None:
        def fake(source_dir, data_dir=None, on_event=None):
            on_event({"event": "stage", "stage": "mapping", "state": "start"})
            on_event({"event": "stage", "stage": "mapping", "state": "done",
                      "topics": 187, "passages": 572})
            on_event({"event": "stage", "stage": "indexing", "state": "start",
                      "passages": 572})
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            state = _settle(client)

        assert state["topics"] == 187
        assert state["passages"] == 572

    def test_a_failed_ingest_is_a_status_not_a_dropped_connection(self, client) -> None:
        with patch("coursegen.ingest.coursemap.ingest",
                   side_effect=RuntimeError("secret internal detail")):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            state = _settle(client)

        assert state["status"] == "failed"
        assert state["error"]
        # R4: the inside of the process never reaches the client.
        assert "secret internal detail" not in state["error"]
        assert "Traceback" not in state["error"]
        assert "RuntimeError" not in state["error"]

    def test_a_new_run_clears_the_previous_one(self, client) -> None:
        """A second upload showing the first upload's files would be worse than
        showing none."""
        with patch("coursegen.ingest.coursemap.ingest", return_value=[object()]):
            client.post("/api/ingest/start", files=[_upload("old.pdf")])
            _settle(client)
            client.post("/api/ingest/start", files=[_upload("new.pdf")])
            state = _settle(client)

        assert [f["file"] for f in state["files"]] == ["new.pdf"]
        assert state["nodes_ingested"] == 1


class TestCleanup:

    def test_the_upload_directory_is_removed_when_the_job_ends(self, client) -> None:
        """The uploads are a student's course material. They are ours only for the
        length of the run (S7).

        Asserted immediately after the terminal status, with no polling for the
        deletion: the job publishes `done` only once the files are gone, so a
        client that acts on that status is acting on a fact.
        """
        seen: dict[str, Path] = {}

        def fake(source_dir, data_dir=None, on_event=None):
            seen["dir"] = Path(source_dir)
            assert seen["dir"].exists()
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            _settle(client)

        assert not seen["dir"].exists(), "upload directory outlived the job"

    def test_the_directory_is_removed_even_when_the_job_fails(self, client) -> None:
        seen: dict[str, Path] = {}

        def boom(source_dir, data_dir=None, on_event=None):
            seen["dir"] = Path(source_dir)
            raise RuntimeError("boom")

        with patch("coursegen.ingest.coursemap.ingest", side_effect=boom):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            _settle(client)

        assert not seen["dir"].exists()


# ---------------------------------------------------------------------------
# Per-file reporting, against the real parser
# ---------------------------------------------------------------------------

class TestParseReportsEachFile:
    """R1, and a gap a mutation found.

    Everything above stubs `ingest`, so it proves the API folds events correctly
    and nothing about whether the parser emits any. Changing the `failed` report
    to `read` in `parse_directory` left the whole suite green — a file that could
    not be opened would have shown on the student's screen as successfully read.
    These run the real parser over real files.
    """

    def test_a_readable_file_is_reported_read(
        self, native_pdf: Path, tmp_path: Path
    ) -> None:
        import shutil as _shutil

        from coursegen.ingest.parse import parse_directory

        _shutil.copy(native_pdf, tmp_path / "good.pdf")
        seen: list[dict[str, Any]] = []
        docs = parse_directory(tmp_path, on_file=seen.append)

        assert len(docs) == 1
        assert [(e["file"], e["state"]) for e in seen] == [
            ("good.pdf", "reading"),
            ("good.pdf", "read"),
        ]
        assert seen[-1]["blocks"] > 0
        assert seen[-1]["source_type"] == "pdf"

    def test_an_unreadable_file_is_reported_failed_with_a_reason(
        self, malformed_pdf: Path, tmp_path: Path
    ) -> None:
        """The state a student most needs. Reported as `read`, a corrupt deck looks
        indexed and its absence from every paper afterwards looks like our fault."""
        import shutil as _shutil

        from coursegen.ingest.parse import parse_directory

        _shutil.copy(malformed_pdf, tmp_path / "bad.pdf")
        seen: list[dict[str, Any]] = []
        docs = parse_directory(tmp_path, on_file=seen.append)

        assert docs == []
        assert [e["state"] for e in seen] == ["reading", "failed"]
        # A reason the student can act on, not an exception class name.
        assert seen[-1]["reason"]
        assert seen[-1]["reason"] != "Exception"

    def test_one_bad_file_does_not_stop_the_others_being_reported(
        self, native_pdf: Path, malformed_pdf: Path, tmp_path: Path
    ) -> None:
        """R3's batch isolation, now visible to the client rather than only to the
        log."""
        import shutil as _shutil

        from coursegen.ingest.parse import parse_directory

        _shutil.copy(malformed_pdf, tmp_path / "bad.pdf")
        _shutil.copy(native_pdf, tmp_path / "good.pdf")
        seen: list[dict[str, Any]] = []
        docs = parse_directory(tmp_path, on_file=seen.append)

        assert [d.source_file for d in docs] == ["good.pdf"]
        final = {e["file"]: e["state"] for e in seen if e["state"] != "reading"}
        assert final == {"bad.pdf": "failed", "good.pdf": "read"}

    def test_a_reporting_callback_that_raises_cannot_break_the_batch(
        self, native_pdf: Path, tmp_path: Path
    ) -> None:
        """`parse_directory`'s contract is that one bad file cannot stop the others.
        A telemetry callback that could throw would undo exactly that."""
        import shutil as _shutil

        from coursegen.ingest.parse import parse_directory

        _shutil.copy(native_pdf, tmp_path / "good.pdf")

        def boom(_e):
            raise RuntimeError("the client went away")

        docs = parse_directory(tmp_path, on_file=boom)
        assert len(docs) == 1


class TestCollidingNames:
    """Two files that differ only by folder used to become one file, silently.

    Silently is the point: the upload screen listed every row it was sent, and
    `_apply_ingest_event` keys rows by name — so they merged back into one as
    parsing started and the student watched their file list shrink with nothing
    saying why.
    """

    @pytest.mark.parametrize("names,expected", [
        (["notes.pdf", "notes.pdf"], ["notes.pdf", "notes-2.pdf"]),
        (["a/n.pdf", "b/n.pdf", "c/n.pdf"], ["n.pdf", "n-2.pdf", "n-3.pdf"]),
        (["x.pdf", "y.pdf"], ["x.pdf", "y.pdf"]),
        # Already-suffixed names must not collide with a generated suffix.
        (["n.pdf", "n-2.pdf", "n.pdf"], ["n.pdf", "n-2.pdf", "n-3.pdf"]),
        # No extension at all.
        (["README", "README"], ["README", "README-2"]),
    ])
    def test_names_are_made_unique(self, names, expected) -> None:
        from coursegen.app.main import _safe_upload_name, _unique_name

        taken: set[str] = set()
        out = []
        for n in names:
            picked = _unique_name(_safe_upload_name(n), taken)
            taken.add(picked)
            out.append(picked)
        assert out == expected

    def test_the_suffix_goes_before_the_extension(self) -> None:
        """`parse_file` dispatches on `path.suffix`. `notes.pdf-2` is not a PDF to
        it, so a deduplicated file would be skipped as an unsupported type — a
        different silent loss in place of the one being fixed."""
        from pathlib import Path as _P

        from coursegen.app.main import _unique_name

        assert _P(_unique_name("notes.pdf", {"notes.pdf"})).suffix == ".pdf"

    def test_every_uploaded_file_reaches_disk(self, client) -> None:
        seen: dict[str, Any] = {}

        def fake(source_dir, data_dir=None, on_event=None):
            seen["files"] = sorted(p.name for p in Path(source_dir).iterdir())
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake):
            resp = client.post("/api/ingest/start", files=[
                _upload("notes.pdf", b"one"),
                _upload("notes.pdf", b"two"),
                _upload("notes.pdf", b"three"),
            ])
            _settle(client)

        assert len(seen["files"]) == 3, seen["files"]
        # And the client is told the names actually used, or its file list
        # describes a directory that does not exist.
        assert resp.json()["files"] == ["notes.pdf", "notes-2.pdf", "notes-3.pdf"]

    def test_the_status_rows_do_not_merge(self, client) -> None:
        """The visible symptom. Three rows in, three rows out."""
        with patch("coursegen.ingest.coursemap.ingest", return_value=[]):
            client.post("/api/ingest/start", files=[
                _upload("notes.pdf"), _upload("notes.pdf"), _upload("notes.pdf"),
            ])
            state = _settle(client)

        assert len(state["files"]) == 3, state["files"]


class TestUploadsAreCapped:
    """The upload path could kill the process it runs in.

    `await f.read()` put every file in memory before anything checked it, and
    `_check_file_size` does not run until `parse_file` — long after the bytes are
    resident and on disk. This process dies at ~4 GB of committable memory with an
    access violation no handler can catch, which is the exact failure
    `preflight`'s `memory` check exists to warn about. An unbounded upload could
    therefore kill the server in the one way the rest of the system is arranged to
    prevent.

    Limits are shrunk here rather than sending real megabytes: the guard reads
    config at call time, so a small ceiling exercises the same branch.
    """

    @pytest.fixture(autouse=True)
    def _small_limits(self, monkeypatch: pytest.MonkeyPatch):
        from coursegen import config

        monkeypatch.setattr(config, "MAX_FILE_SIZE_BYTES", 1000)
        monkeypatch.setattr(config, "MAX_UPLOAD_TOTAL_BYTES", 2500)
        monkeypatch.setattr(config, "UPLOAD_CHUNK_BYTES", 256)

    def test_a_file_over_the_per_file_limit_is_refused(self, client) -> None:
        resp = client.post(
            "/api/ingest/start", files=[_upload("big.pdf", b"x" * 5000)]
        )
        assert resp.status_code == 413
        assert "single file" in resp.json()["detail"]

    def test_many_small_files_over_the_total_limit_are_refused(self, client) -> None:
        """The per-file limit alone bounds nothing: N files under it are N times
        the memory."""
        resp = client.post("/api/ingest/start", files=[
            _upload(f"f{i}.pdf", b"x" * 900) for i in range(6)
        ])
        assert resp.status_code == 413
        assert "total limit" in resp.json()["detail"]

    def test_a_refused_upload_leaves_nothing_behind(self, client) -> None:
        """A rejected 400 MB request that left its partial writes on disk would
        turn a refusal into a slower way to fill the disk."""
        import tempfile as _tf

        before = set(Path(_tf.gettempdir()).glob("prepify-ingest-*"))
        client.post("/api/ingest/start", files=[_upload("big.pdf", b"x" * 5000)])
        after = set(Path(_tf.gettempdir()).glob("prepify-ingest-*"))
        assert after == before, f"leftover upload dirs: {after - before}"

    def test_a_refused_upload_does_not_strand_the_job_state(self, client) -> None:
        """Reserved on entry and never released, the state would sit in `queued`
        and 409 every later upload — one oversized file locking out the feature."""
        client.post("/api/ingest/start", files=[_upload("big.pdf", b"x" * 5000)])
        assert client.get("/api/ingest/status").json()["status"] == "idle"

        with patch("coursegen.ingest.coursemap.ingest", return_value=[]):
            ok = client.post("/api/ingest/start", files=[_upload("a.pdf", b"tiny")])
            assert ok.status_code == 200
            _settle(client)

    def test_an_upload_within_the_limits_still_works(self, client) -> None:
        seen: dict[str, Any] = {}

        def fake(source_dir, data_dir=None, on_event=None):
            seen["sizes"] = sorted(p.stat().st_size for p in Path(source_dir).iterdir())
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=fake):
            resp = client.post("/api/ingest/start", files=[
                _upload("a.pdf", b"x" * 900), _upload("b.pdf", b"y" * 900),
            ])
            assert resp.status_code == 200
            _settle(client)

        # Written whole and correctly, not truncated by the chunking.
        assert seen["sizes"] == [900, 900]

    def test_the_blocking_route_is_capped_too(self, client) -> None:
        resp = client.post("/api/ingest", files=[_upload("big.pdf", b"x" * 5000)])
        assert resp.status_code == 413


class TestQueuedIngestSaysSo:
    """An ingest behind a chat request or a generation used to report `running`
    with no stage — a climbing clock over three pending stages, indistinguishable
    from a hang. Chat can hold that lock for ~40 s on a cold model load.

    The exam stream already announced its wait; the job did not."""

    def test_a_queued_job_reports_waiting_and_names_the_holder(self, client) -> None:
        import coursegen.app.main as main

        started = threading.Event()

        def watched(source_dir, data_dir=None, on_event=None):
            started.set()
            return []

        main._PIPELINE_LOCK.acquire()
        main._pipeline_holder = "answering a question"
        try:
            with patch("coursegen.ingest.coursemap.ingest", side_effect=watched):
                client.post("/api/ingest/start", files=[_upload("a.pdf")])

                deadline = time.monotonic() + 5
                seen = None
                while time.monotonic() < deadline:
                    seen = client.get("/api/ingest/status").json()
                    if seen["status"] == "waiting":
                        break
                    time.sleep(0.02)

                assert seen["status"] == "waiting", seen
                assert seen["waiting_for"] == "answering a question"
                assert not started.is_set(), "work began while the lock was held"
        finally:
            main._pipeline_holder = ""
            main._PIPELINE_LOCK.release()

        assert started.wait(timeout=5), "the job never started after the lock freed"
        _settle(client)

    def test_a_waiting_job_still_blocks_a_second_upload(self, client) -> None:
        """`waiting` is alive. Treated as terminal it would let a second upload
        start and overwrite the first one's course map."""
        import coursegen.app.main as main

        main._PIPELINE_LOCK.acquire()
        try:
            with patch("coursegen.ingest.coursemap.ingest", return_value=[]):
                client.post("/api/ingest/start", files=[_upload("a.pdf")])
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if client.get("/api/ingest/status").json()["status"] == "waiting":
                        break
                    time.sleep(0.02)

                second = client.post("/api/ingest/start", files=[_upload("b.pdf")])
                assert second.status_code == 409
        finally:
            main._PIPELINE_LOCK.release()
        _settle(client)

    def test_an_uncontended_ingest_never_reports_waiting(self, client) -> None:
        """A "waiting" banner on every run would train the student to ignore it."""
        seen: list[str] = []

        def record(source_dir, data_dir=None, on_event=None):
            seen.append(client.get("/api/ingest/status").json()["status"])
            return []

        with patch("coursegen.ingest.coursemap.ingest", side_effect=record):
            client.post("/api/ingest/start", files=[_upload("a.pdf")])
            final = _settle(client)

        assert seen == ["running"], seen
        assert final["waiting_for"] is None
