"""
FastAPI application — P5.

Single worker only (S8): QdrantClient(path=...) takes an exclusive file lock.
Run with: uvicorn coursegen.app.main:app --workers 1
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from coursegen import config
from coursegen.app.preflight import preflight_status, run_preflight_checks
from coursegen.llm.client import BudgetExceeded

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model singletons — lazy-loaded on first chat/generation call (not at import).
# Both are module-level so they survive across requests; tests patch these
# helper functions to prevent real model loading.
# ---------------------------------------------------------------------------
_embed_model = None
_reranker_model = None

# One generation at a time. Every run writes the SAME files - output/exam.pdf,
# output/run_manifest.json, output/cache/ - because /api/files/{name} serves them
# from one fixed directory. Two overlapping runs interleave into that directory
# and the loser silently downloads the winner's paper.
#
# Not hypothetical: on 2026-09-01 a run_manifest was observed on disk describing a
# different paper than the artifacts beside it - claiming mcq_hygiene 8 and
# flagging a slot that the PDFs themselves contained - because a second process
# wrote into output/ mid-run. Behind an HTTP endpoint that stops being an accident
# and becomes one user double-clicking Generate.
#
# A lock rather than per-request directories because S8 already pins this to a
# single worker (Qdrant takes an exclusive file lock), so a process-level lock is
# sufficient and leaves the /api/files/{name} contract unchanged. The second
# caller waits instead of corrupting the first. If this ever has to serve
# concurrent users, the fix is per-run output directories with run-scoped download
# URLs - not a bigger lock.
_PIPELINE_LOCK = threading.Lock()

# What currently holds it, for the sake of anyone waiting. A queued generation can
# now be blocked by an ingest as well as by another generation, and those are a
# minute apart in expected duration - telling a student "another paper is being
# generated" while a ten-minute index runs is a false statement about how long
# they are about to wait. Written under the lock, read without it: it feeds one
# sentence of copy, never a decision.
_pipeline_holder: str = ""


@contextmanager
def _hold_pipeline(label: str):
    global _pipeline_holder
    _PIPELINE_LOCK.acquire()
    _pipeline_holder = label
    try:
        yield
    finally:
        _pipeline_holder = ""
        _PIPELINE_LOCK.release()


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from coursegen.ingest.embed import load_model
        _embed_model = load_model()
    return _embed_model


def _get_reranker():
    global _reranker_model
    if _reranker_model is None:
        from sentence_transformers import CrossEncoder
        _reranker_model = CrossEncoder(config.RERANKER_MODEL)
    return _reranker_model


# ---------------------------------------------------------------------------
# Blueprint directory (ships with the package)
# ---------------------------------------------------------------------------
_BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "exam" / "blueprints"




# ---------------------------------------------------------------------------
# App lifecycle (lifespan replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        run_preflight_checks()
    except RuntimeError as exc:
        logger.error("Pre-flight check failed at startup: %s", exc)
    yield


app = FastAPI(title="Prepify", lifespan=_lifespan)

# Disclosure state — single-user, single-process (see S7).
_disclosure_accepted: bool = False


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_UI_PATH = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def ui() -> HTMLResponse:
    return HTMLResponse(_UI_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/preflight")
def preflight() -> dict[str, str]:
    return preflight_status()


@app.get("/api/blueprints")
def list_blueprints() -> list[str]:
    return sorted(p.stem for p in _BLUEPRINT_DIR.glob("*.json"))


@app.get("/api/limits")
def limits() -> dict[str, Any]:
    """What the server will accept, so the client can stop asking it to guess.

    `UploadScreen` hardcoded `100 * 1024 * 1024` with a comment pointing here, and
    hardcoded "100 MB" again in its rejection message — three copies of one
    number, two of them in the client. Change the config and the client either
    rejects files the server would take or accepts files it will refuse, and the
    message it shows is wrong either way.

    Extensions come from the parser's own scanned-suffix set rather than a second
    literal, so a format added there appears here without anyone remembering to.
    `.ppt` is scanned but never parseable, and is excluded deliberately: offering
    it would invite an upload that always fails.
    """
    from coursegen.ingest.parse import _SCANNED_SUFFIXES

    return {
        "max_file_bytes": config.MAX_FILE_SIZE_BYTES,
        "max_upload_bytes": config.MAX_UPLOAD_TOTAL_BYTES,
        "extensions": sorted(_SCANNED_SUFFIXES - {".ppt"}),
    }


@app.get("/api/topics")
def list_topics(limit: int = 6) -> list[str]:
    """The densest topics in the student's own material, by name.

    Exists so screens can offer examples drawn from the corpus that is actually
    loaded. The chat empty state used to hardcode three questions about A* search
    and alpha-beta pruning — fine against the AI deck it was built on, nonsense to
    anyone who uploaded organic chemistry, and the same defect as the hardcoded
    blueprint presets: the UI asserting facts about content it cannot know.

    The **last path element** is the topic, not `key_terms`. Key terms come from
    YAKE and on this corpus include author names and sentence fragments
    ("Michael Hahsler based", "Advanced Step"); the path's last element is the
    slide's own heading, written by whoever made the deck.

    Costs no quota and loads no model — the same course map `/api/plan` reads.
    Returns `[]` rather than raising when nothing is ingested: an empty corpus is
    a normal state for a new install, not an error.
    """
    from coursegen.ingest.coursemap import load_course_map

    try:
        nodes = load_course_map(config.COURSE_MAP_PATH)
    except Exception:  # noqa: BLE001 - no corpus yet is not a failure
        return []

    seen: set[str] = set()
    topics: list[str] = []
    for node in sorted(nodes, key=lambda n: -n.instructional_mass):
        if len(node.path) < 2:
            continue  # the bare filename, not a heading
        name = node.path[-1].strip()
        key = name.casefold()
        if key in seen:
            continue
        # A heading that is a filename, a fragment, or a paragraph is not a topic
        # anyone would recognise as one.
        if not (3 <= len(name) <= 60) or name.lower().endswith(
            (".pdf", ".pptx", ".docx")
        ):
            continue
        seen.add(key)
        topics.append(name)
        if len(topics) >= max(1, min(limit, 20)):
            break
    return topics


def _test_seams_enabled() -> bool:
    """Whether the `/api/internal/*` routes will answer.

    Read per-request rather than captured at import, so a test can turn it on with
    `monkeypatch.setenv` after the app object already exists.
    """
    return os.getenv("PREPIFY_TEST_SEAMS") == "1"


@app.post("/api/internal/reset-disclosure")
def _reset_disclosure() -> dict[str, bool]:
    """Test helper — resets the disclosure flag.

    `API-CONTRACT.md` has always said "Not for the UI", and it was nonetheless a
    live unauthenticated POST on every running server: anything that could reach
    the port could clear the consent gate. The impact was small — it re-gates
    rather than un-gates, so it makes the system more conservative — but a route
    documented as not-for-production should not be reachable in production.

    404, not 403, when the seam is off. A 403 confirms the route exists; a 404 is
    what an endpoint that is not there looks like, and this endpoint should look
    like it is not there.
    """
    if not _test_seams_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    global _disclosure_accepted
    _disclosure_accepted = False
    return {"reset": True}


# ---------------------------------------------------------------------------
# Disclosure (S7)
# ---------------------------------------------------------------------------

@app.post("/api/disclosure/accept")
def accept_disclosure() -> dict[str, bool]:
    global _disclosure_accepted
    _disclosure_accepted = True
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _safe_upload_name(raw: str | None) -> str:
    """Reduce a client-supplied filename to a bare name inside our directory.

    `Path(raw).name` strips every directory component, which is the same defence
    `/api/files/{filename}` already applies on the way out. It was missing on the
    way IN: `source_dir / "../../../Windows/Temp/pwned.pdf"` resolves outside the
    temp directory, and the next line writes bytes to it — an unauthenticated
    write-anywhere primitive from a multipart field. Verified 2026-09-04, fixed
    the same day.

    Backslashes are translated first because `PurePosixPath` semantics do not
    treat them as separators, and a Windows client can legitimately send one.
    """
    name = Path((raw or "").replace("\\", "/")).name.strip()
    # "..", "." and "" all reduce to nothing usable; a fixed fallback keeps the
    # file inside the directory rather than rejecting the whole upload.
    return name if name and name not in {".", ".."} else "upload"


def _unique_name(name: str, taken: set[str]) -> str:
    """`notes.pdf`, `notes-2.pdf`, `notes-3.pdf` — never the same file twice.

    Reducing an upload to its basename (see `_safe_upload_name`) makes two files
    that differ only by folder collide, and the collision was silent: each write
    overwrote the last, so three decks became one indexed file.

    Silent is the operative word. `_save_uploads` returned the duplicated list, so
    the upload screen listed three rows — and `_apply_ingest_event` keys file rows
    by name, so they merged back into one as parsing began. A student saw their
    own file list shrink, with nothing saying anything had been dropped.

    The suffix goes before the extension so the file still parses: `parse_file`
    dispatches on `path.suffix`, and `notes.pdf-2` is not a PDF to it.
    """
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:  # no extension at all
        stem, ext = name, ""
    n = 2
    while f"{stem}-{n}{dot}{ext}" in taken:
        n += 1
    return f"{stem}-{n}{dot}{ext}"


async def _save_uploads(files: list[UploadFile], dest_dir: Path) -> list[str]:
    """Write the uploads and return the names they were actually saved under.

    The returned list is what the client is shown, so it has to be the names on
    disk rather than the names sent — otherwise the file list is a description of
    a directory that does not exist.
    """
    names: list[str] = []
    taken: set[str] = set()
    total = 0

    for f in files:
        name = _unique_name(_safe_upload_name(f.filename), taken)
        taken.add(name)
        written = 0

        # Streamed and counted, rather than `await f.read()`.
        #
        # Reading whole files put the entire upload in memory before anything
        # checked it, and `_check_file_size` does not run until `parse_file` —
        # long after the bytes are resident AND on disk. This process is killed
        # by the OS at ~4 GB of committable memory with an access violation no
        # handler can catch; `preflight`'s `memory` check exists for precisely
        # that failure. An unbounded upload could therefore kill the server in
        # the one way the rest of the system is arranged to prevent.
        with (dest_dir / name).open("wb") as out:
            while chunk := await f.read(config.UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                total += len(chunk)
                if written > config.MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{name} is larger than the "
                               f"{config.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB "
                               f"limit for a single file.",
                    )
                if total > config.MAX_UPLOAD_TOTAL_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"That upload is over the "
                               f"{config.MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)} MB "
                               f"total limit. Send fewer files at a time.",
                    )
                out.write(chunk)

        names.append(name)
    return names


def _ingest_under_lock(
    source_dir: Path,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_wait: Callable[[str], None] | None = None,
) -> list[Any]:
    """Ingest, serialised against generation.

    Both touch Qdrant, which takes an exclusive file lock (S8): an ingest running
    while a paper is generated makes the generation's span read fail. That was
    unlikely while ingest was a request the caller sat and waited on. It stops
    being unlikely the moment ingest becomes a background job the student can
    walk away from and press Generate during.
    """
    from coursegen.ingest.coursemap import ingest

    global _pipeline_holder

    # Probed before blocking, so the wait can be REPORTED. Taking the lock
    # straight away left the job announcing `running` with no stage while it sat
    # behind a chat request or a generation — a climbing clock, three pending
    # stages, and nothing to distinguish that from a hang. Chat can hold this for
    # ~40 s on a cold model load.
    if not _PIPELINE_LOCK.acquire(blocking=False):
        if on_wait is not None:
            try:
                on_wait(_pipeline_holder or "another job")
            except Exception:  # noqa: BLE001 - telemetry never fails the run
                pass
        _PIPELINE_LOCK.acquire()

    _pipeline_holder = "indexing your uploads"
    try:
        return ingest(source_dir, on_event=on_event)
    finally:
        _pipeline_holder = ""
        _PIPELINE_LOCK.release()


@app.post("/api/ingest")
async def ingest_files(files: list[UploadFile]) -> dict[str, Any]:
    """Upload course material files and run the ingest pipeline.

    Blocking: the caller waits out the whole run, which is minutes. Kept because
    its shape is frozen in `API-CONTRACT.md` and the static UI calls it.
    `/api/ingest/start` is the one a browser should use.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = Path(tmpdir)
        await _save_uploads(files, source_dir)
        # `to_thread`, because `ingest` blocks for minutes and this coroutine runs
        # ON the event loop. Before this, an ingest froze every other request for
        # its whole duration - including /api/health, which is what a watchdog
        # would be reading to decide the server had died.
        nodes = await asyncio.to_thread(_ingest_under_lock, source_dir, None)

    return {"nodes_ingested": len(nodes)}


# ---------------------------------------------------------------------------
# Ingest as a job
# ---------------------------------------------------------------------------
#
# A job with a status endpoint rather than a stream, which is the opposite of the
# choice `/api/exam/stream` made, for one reason: **it has to survive a refresh.**
#
# Generation costs ~51 s and a student watches it. Ingest costs ~10 minutes and a
# student does not — they switch tabs, close the laptop, come back. A streamed
# response dies with the connection, so a reload at minute seven would leave them
# with no way to find out whether their upload was still going, finished, or had
# failed. State on the server can be re-read by whoever asks. That is the whole
# argument, and it is why these two long waits are built differently.
#
# One job at a time. There is no job id because there cannot be two: ingest writes
# one Qdrant collection and one course_map.json.

_INGEST_STATE_LOCK = threading.Lock()

_IDLE_INGEST: dict[str, Any] = {
    "status": "idle",
    "stage": None,
    # What the run is queued behind, while `status` is "waiting". None otherwise.
    "waiting_for": None,
    "files": [],
    "topics": None,
    "passages": None,
    "nodes_ingested": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_ingest_state: dict[str, Any] = dict(_IDLE_INGEST)


def _update_ingest(**fields: Any) -> None:
    with _INGEST_STATE_LOCK:
        _ingest_state.update(fields)


def _apply_ingest_event(event: dict[str, Any]) -> None:
    """Fold one pipeline event into the status record.

    Per-file state is keyed by name and overwritten in place, so `reading` becomes
    `read` or `failed` on the same row rather than appending a second one — the
    client renders the list, not a log.
    """
    kind = event.get("event")
    if kind == "stage":
        if event.get("state") == "start":
            _update_ingest(stage=event.get("stage"))
        else:
            patch = {k: event[k] for k in ("topics", "passages") if k in event}
            if patch:
                _update_ingest(**patch)
        return
    if kind == "file":
        with _INGEST_STATE_LOCK:
            rows: list[dict[str, Any]] = list(_ingest_state["files"])
            row = {k: v for k, v in event.items() if k != "event"}
            for i, existing in enumerate(rows):
                if existing.get("file") == row.get("file"):
                    rows[i] = {**existing, **row}
                    break
            else:
                rows.append(row)
            _ingest_state["files"] = rows


def _ingest_job(source_dir: Path, names: list[str]) -> None:
    """Run one ingest to a terminal status, then publish it.

    The terminal status is written **after** the uploads are deleted, not before,
    which makes the ordering something a caller can rely on: once `/api/ingest/status`
    says `done` or `failed`, the student's files are already off this machine (S7).
    Published the other way round it was merely probable, and a poller that acted
    on `done` could observe them still sitting in the temp directory.
    """
    try:
        _update_ingest(status="running")
        nodes = _ingest_under_lock(
            source_dir,
            on_event=_apply_ingest_event,
            on_wait=lambda holder: _update_ingest(
                status="waiting", waiting_for=holder
            ),
        )
        outcome: dict[str, Any] = {
            "status": "done",
            "nodes_ingested": len(nodes),
        }
    except Exception:
        # Same rule as generation: the traceback goes to the log, never to the
        # client (R4). A failed ingest is a terminal status, not an exception the
        # poller has to infer from a dropped connection.
        logger.exception("Ingest job failed")
        outcome = {
            "status": "failed",
            "error": "Indexing failed. Check the server logs.",
        }
    finally:
        # `ignore_errors` so a locked handle cannot strand the job in `running`
        # forever — a status nobody can clear is worse than a file left behind.
        shutil.rmtree(source_dir, ignore_errors=True)

    _update_ingest(stage=None, finished_at=time.time(), **outcome)


@app.post("/api/ingest/start")
async def ingest_start(files: list[UploadFile]) -> dict[str, Any]:
    """Begin an ingest and return immediately. Poll `/api/ingest/status`."""
    with _INGEST_STATE_LOCK:
        # `waiting` is alive, not finished. Omitted here, a job queued behind a
        # chat request would let a second upload start and overwrite the course
        # map the first one is about to write.
        if _ingest_state["status"] in ("queued", "waiting", "running"):
            raise HTTPException(
                status_code=409,
                detail="An upload is already being indexed. Wait for it to finish.",
            )
        _ingest_state.clear()
        _ingest_state.update(_IDLE_INGEST)
        _ingest_state.update(
            status="queued", files=[], started_at=time.time(), finished_at=None
        )

    # Not a TemporaryDirectory context: the directory has to outlive this request
    # by the length of the job. `_ingest_job` owns it and removes it in a finally.
    source_dir = Path(tempfile.mkdtemp(prefix="prepify-ingest-"))
    try:
        saved = await _save_uploads(files, source_dir)
    except Exception:
        shutil.rmtree(source_dir, ignore_errors=True)
        _update_ingest(status="idle")
        raise

    _update_ingest(files=[{"file": n, "state": "queued"} for n in saved])
    threading.Thread(
        target=_ingest_job, args=(source_dir, saved), name="ingest", daemon=True
    ).start()
    return {"started": True, "files": saved}


@app.get("/api/ingest/status")
def ingest_status() -> dict[str, Any]:
    """Where the current or most recent ingest got to.

    Safe to poll, and safe to call from a page that has just loaded knowing
    nothing — which is the point. `elapsed_seconds` is derived here rather than in
    the client so a reloaded tab shows the true age of the run, not the age of its
    own connection.
    """
    with _INGEST_STATE_LOCK:
        state = dict(_ingest_state)
        state["files"] = [dict(f) for f in state["files"]]

    started, finished = state["started_at"], state["finished_at"]
    state["elapsed_seconds"] = (
        round((finished or time.time()) - started, 1) if started else None
    )
    return state


# ---------------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    blueprint_id: str


@app.post("/api/plan")
def plan_paper(request: PlanRequest) -> dict[str, Any]:
    """The dry run: what this paper would be, costing nothing.

    Deliberately NOT behind the disclosure gate that /api/exam sits behind. The
    disclosure is about sending the student's material to a model; a plan sends
    nothing anywhere. Gating a free, local, read-only preview behind a consent
    step the student has not yet had a reason to give would be theatre.

    Runs the solver only - no LLM calls, no quota, 5-125 ms - so the UI can offer
    it on every keystroke if it wants to.
    """
    from coursegen.exam.allocate import solve
    from coursegen.exam.plan import build_plan
    from coursegen.ingest.coursemap import load_course_map
    from coursegen.pipeline import load_blueprint

    if not config.COURSE_MAP_PATH.exists():
        raise HTTPException(
            status_code=409,
            detail="No course material has been ingested yet. Upload your notes first.",
        )
    try:
        blueprint = load_blueprint(request.blueprint_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    nodes = load_course_map(config.COURSE_MAP_PATH)
    specs, coverage = solve(nodes, blueprint)
    return build_plan(specs, coverage, blueprint, nodes)


@app.get("/api/paper")
def get_paper() -> dict[str, Any]:
    """The most recently generated paper, as structured data.

    Separate from POST /api/exam rather than folded into its response, for two
    reasons. Generation takes the better part of a minute, so a refresh mid-wait
    must not lose the paper. And the generation response is a summary a caller
    polls; the paper is a document a client renders, and the two have different
    lifetimes.

    404 when nothing has been generated yet — an empty paper would be
    indistinguishable from a real one that produced no questions.
    """
    path = config.OUTPUT_DIR / "paper.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="No paper has been generated yet.",
        )
    return _json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/files/{filename}")
def get_file(filename: str) -> FileResponse:
    # Prevent directory traversal — only allow the bare filename.
    safe_name = Path(filename).name
    path = config.OUTPUT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
    return FileResponse(str(path))


# ---------------------------------------------------------------------------
# Exam generation
# ---------------------------------------------------------------------------

class ExamRequest(BaseModel):
    blueprint_id: str
    title: str


def _require_disclosure() -> None:
    if not _disclosure_accepted:
        raise HTTPException(
            status_code=403,
            detail="Disclosure must be accepted before generating exams. "
                   "POST /api/disclosure/accept first.",
        )


@app.post("/api/exam")
def generate_exam(request: ExamRequest) -> dict[str, Any]:
    _require_disclosure()
    # Serialised: see _PIPELINE_LOCK. Held across the whole run because the
    # artifacts only agree with the manifest once render has finished.
    with _hold_pipeline("generating a paper"):
        result = _exam_outcome(request.blueprint_id, request.title)
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result["detail"])
    return result


def _exam_outcome(
    blueprint_id: str,
    title: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one generation and map it onto the outcomes the contract freezes.

    Shared by `/api/exam` and `/api/exam/stream` so the outcome space has exactly
    one implementation. Two copies of `ok / empty / degraded / failed` is how the
    route originally came to report every unexpected exception as `degraded` with
    `fill_ratio: 0.0` — an HTTP 200 claiming a paper had been generated that
    filled nothing, indistinguishable from a paper that genuinely filled nothing.

    **Never raises.** Failure is the value `{"status": "failed", "detail": ...}`.
    The JSON route turns that into a 500; the stream turns it into an `error`
    event, because a stream cannot answer 500 once its headers are on the wire.
    Making failure a value on both paths is what keeps them from diverging.

    Does not take the pipeline lock — the caller does, because the stream needs to
    know whether it had to wait in order to say so.
    """
    try:
        return _run_exam_pipeline(blueprint_id, title, on_event=on_event)
    except BudgetExceeded:
        logger.warning("Generation degraded: quota limit reached")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation stopped early: quota limit reached. The exam may be incomplete."],
        }
    except httpx.RequestError as exc:
        logger.warning(f"Generation degraded: network error ({type(exc).__name__})")
        return {
            "status": "degraded",
            "fill_ratio": 0.0,
            "allocation_fidelity": 0.0,
            "unfilled_slots": [],
            "items": [],
            "warnings": ["Generation degraded by a network error. The exam may be incomplete."],
        }
    except Exception:
        # An unexpected exception is a FAILURE, not a degraded result.
        #
        # "degraded" is an operational state with a meaning: we ran, we hit a
        # known limit (quota, timeout), and what came back is real but partial.
        # Reporting a KeyError or a missing course map as degraded - with
        # fill_ratio 0.0 beside it - produces an HTTP 200 claiming a paper was
        # generated that filled nothing, which a caller cannot tell apart from a
        # paper that genuinely filled nothing. Before this change, a POST to
        # /api/exam with no pipeline at all returned 200 "degraded".
        #
        # Same distinction new_gate_report() draws between a gate that passed
        # everything and a gate that never ran, and why `skipped` is a bool there.
        # The two states R8 actually specifies - BudgetExceeded and
        # TimeoutException - are still degraded 200s, handled above.
        #
        # 500 on the JSON route, an `error` event on the stream. Traceback is
        # logged, never sent to the client (R4).
        logger.exception("Unexpected generation error")
        return {
            "status": "failed",
            "detail": "Generation failed unexpectedly. Check the server logs.",
        }


def _run_exam_pipeline(
    blueprint_id: str,
    title: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Adapt `pipeline.generate_paper` to the API contract.

    The stage sequence lives in `coursegen/pipeline.py` and is shared with the
    CLI. This function's only job is supplying the models and mapping the result
    onto the shape `API-CONTRACT.md` freezes.
    """
    from coursegen.pipeline import generate_paper

    emit = on_event or (lambda _e: None)

    # Model load is its own stage because on a cold process it is the single
    # largest slice of the first request (E3.1) and it happens BEFORE the pipeline
    # emits anything of its own. Without this the stream opens and then says
    # nothing for tens of seconds, which is the exact failure a streamed progress
    # display exists to prevent. On a warm process it passes in milliseconds.
    try:
        emit({"event": "stage", "stage": "loading", "state": "start"})
    except Exception:  # noqa: BLE001 - telemetry never fails the run it narrates
        pass
    reranker = _get_reranker()
    embed = _get_embed_model()
    try:
        emit({"event": "stage", "stage": "loading", "state": "done"})
    except Exception:  # noqa: BLE001
        pass

    # Shared with the CLI rather than defined here. This closure was the only
    # `embedding_fn` in the codebase, which is why only the route ran the
    # duplication gate (F12).
    from coursegen.ingest.embed import dense_embedding_fn

    embedding_fn = dense_embedding_fn(embed)

    result = generate_paper(
        blueprint_id=blueprint_id,
        title=title,
        groundedness_scorer=lambda answer, src: float(reranker.predict([(answer, src)])[0]),
        embedding_fn=embedding_fn,
        on_event=on_event,
    )

    # `coverage_ratio` is deliberately NOT returned (disconnected 2026-09-01). It
    # counts matched nodes against the WHOLE corpus, so under an authored
    # blueprint it read 0.04 on a paper that was 20/20 items and 100/100 marks.
    # `fill_ratio` and `allocation_fidelity` are the honest headline numbers.
    # Read off the assembled paper, not off the coverage report. These used to
    # come from the allocator, so a run that placed all seven slots and then lost
    # two at the gates answered `fill_ratio: 1.0` and `unfilled_slots: []` beside
    # `items_count: 5` — a caller trusting the contract showed a clean paper.
    # `paper["summary"]` now carries delivery, with the allocator's own view kept
    # under `allocation_*` for anyone who wants the difference.
    summary = result.paper.get("summary", {})
    return {
        "status": result.status,
        "fill_ratio": summary.get("fill_ratio", result.coverage.fill_ratio),
        "allocation_fidelity": result.coverage.allocation_fidelity,
        "unfilled_slots": summary.get("unfilled_slots", result.coverage.unfilled_slots),
        "items_count": len(result.items),
        "warnings": result.coverage.warnings,
        "downloads": {
            "exam_html": "/api/files/exam.html",
            "answer_key_html": "/api/files/answer_key.html",
            "coverage_html": "/api/files/coverage.html",
            "exam_pdf": "/api/files/exam.pdf",
            "answer_key_pdf": "/api/files/answer_key.pdf",
        },
    }


# ---------------------------------------------------------------------------
# Streamed generation
# ---------------------------------------------------------------------------

_STREAM_DONE = object()


@app.post("/api/exam/stream")
def generate_exam_streamed(request: ExamRequest) -> StreamingResponse:
    """The same generation as `/api/exam`, narrated while it runs.

    NDJSON — one JSON object per line — rather than SSE, because this is a POST
    with a body and `EventSource` is GET-only. A browser reads it with
    `response.body.getReader()`; there is no client library to add.

    A sibling endpoint rather than a change to `/api/exam`: that shape is frozen in
    `API-CONTRACT.md` and the static UI depends on it. This one is additive, and
    the terminal `result` event carries **exactly** the `/api/exam` body so a
    client has one result-handling path, not two that can drift.

    ### The one place the two endpoints genuinely differ

    A stream cannot answer HTTP 500: by the time anything goes wrong the status
    line is long gone. Failure arrives as `{"event": "error", ...}` on a 200
    response, and a client must treat that event exactly as it treats a 500 from
    `/api/exam`. This is a real asymmetry, not an oversight — see `_exam_outcome`.
    """
    _require_disclosure()
    blueprint_id, title = request.blueprint_id, request.title
    events: "queue.Queue[Any]" = queue.Queue()

    def run() -> None:
        try:
            # Non-blocking first, so a caller that has to queue behind another
            # generation is TOLD it is queueing. The lock is otherwise a silent
            # wait - API-CONTRACT.md warns that double-clicking Generate produces
            # "a long silent wait" - and silence is what a stream exists to fix.
            global _pipeline_holder
            waited = not _PIPELINE_LOCK.acquire(blocking=False)
            if waited:
                events.put({
                    "event": "stage", "stage": "waiting", "state": "start",
                    # Read before we hold the lock, so it names the run we are
                    # actually queued behind.
                    "holder": _pipeline_holder or "another job",
                })
                _PIPELINE_LOCK.acquire()
                events.put({"event": "stage", "stage": "waiting", "state": "done"})
            _pipeline_holder = "generating a paper"
            try:
                body = _exam_outcome(blueprint_id, title, on_event=events.put)
            finally:
                _pipeline_holder = ""
                _PIPELINE_LOCK.release()
            if body.get("status") == "failed":
                events.put({"event": "error", "detail": body["detail"]})
            else:
                events.put({"event": "result", **body})
        except BaseException:  # noqa: BLE001
            # _exam_outcome does not raise, so reaching here means the harness
            # around it broke. Still has to produce a terminal event: a consumer
            # blocked on the queue would otherwise wait forever.
            logger.exception("Exam stream failed outside the pipeline")
            events.put({
                "event": "error",
                "detail": "Generation failed unexpectedly. Check the server logs.",
            })
        finally:
            events.put(_STREAM_DONE)

    def body_iter():
        threading.Thread(target=run, name="exam-stream", daemon=True).start()
        while True:
            item = events.get()
            if item is _STREAM_DONE:
                return
            yield _json.dumps(item) + "\n"

    return StreamingResponse(
        body_iter(),
        media_type="application/x-ndjson",
        # Reverse proxies that buffer would defeat the entire point of this
        # endpoint, and would do it silently - the client just sees one late burst.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    history: list[list[str]] = []


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    history = [(h[0], h[1]) for h in request.history if len(h) == 2]

    # Fail fast rather than queue. Chat reads Qdrant, ingest writes it, and Qdrant
    # takes an exclusive file lock (S8) - so a question asked during an indexing
    # run used to reach the lock and raise, which surfaced as a bare 500. Waiting
    # would be no better: a chat request held open for the ten minutes an ingest
    # takes is indistinguishable from a hang. Told what is happening, a student can
    # come back; told nothing, they refresh and ask again.
    global _pipeline_holder
    if not _PIPELINE_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=f"Busy {_pipeline_holder or 'with another job'}. "
                   "Your material is not available for questions until it finishes.",
        )
    _pipeline_holder = "answering a question"
    try:
        return _run_chat_query(request.query, history)
    except BudgetExceeded:
        # A known limit, not a fault - the same distinction /api/exam draws. A 500
        # here would tell a student their notes were broken when they had simply
        # run out of quota for the day.
        logger.warning("Chat degraded: quota limit reached")
        return {
            "answer": "You have reached today's request limit, so I can't answer "
                      "this one. Your material is still indexed and questions will "
                      "work again once the limit resets.",
            "citations": [],
            "from_material": False,
            "status": "degraded",
        }
    except httpx.RequestError as exc:
        logger.warning(f"Chat degraded: network error ({type(exc).__name__})")
        return {
            "answer": "I couldn't reach the model just now. Nothing is wrong with "
                      "your material — try the question again.",
            "citations": [],
            "from_material": False,
            "status": "degraded",
        }
    except Exception:
        # R4: the traceback goes to the log, never to the client.
        logger.exception("Unexpected chat error")
        raise HTTPException(
            status_code=500,
            detail="That question could not be answered. Check the server logs.",
        )
    finally:
        _pipeline_holder = ""
        _PIPELINE_LOCK.release()


def _run_chat_query(query: str, history: list[tuple[str, str]]) -> dict[str, Any]:
    """Wire query → embed → hybrid_search → rerank → answer_question (P4 chat path)."""
    from coursegen.ingest.index import get_client
    from coursegen.retrieve.hybrid import hybrid_search
    from coursegen.retrieve.rerank import rerank_chunks
    from coursegen.chat.answer import answer_question
    from coursegen.llm.client import LLMClient

    embed = _get_embed_model()
    reranker = _get_reranker()

    def retrieve(q: str):
        out = embed.encode(
            [q], return_dense=True, return_sparse=True, return_colbert_vecs=False
        )
        dense = out["dense_vecs"][0].tolist()
        sparse_dict = dict(out["lexical_weights"][0])
        sorted_items = sorted(sparse_dict.items())
        sparse_indices = [int(k) for k, _ in sorted_items]
        sparse_values = [float(v) for _, v in sorted_items]
        client = get_client(config.DATA_DIR)
        try:
            return hybrid_search(client, dense, sparse_indices, sparse_values)
        finally:
            client.close()

    def rerank(q: str, candidates):
        scorer = lambda q2, text: float(reranker.predict([(q2, text)])[0])
        return rerank_chunks(q, candidates, scorer)

    llm = LLMClient()
    answer = answer_question(query, history, retrieve, rerank, llm)

    return {
        "answer": answer.answer,
        "citations": answer.citations,
        "from_material": answer.from_material,
    }
