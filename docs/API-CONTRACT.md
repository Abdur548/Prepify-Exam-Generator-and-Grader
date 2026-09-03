# Prepify API contract

**Frozen 2026-09-01** against `coursegen/app/main.py`. This is what the frontend builds
against. If a shape here changes, this file changes in the same commit — a contract that
drifts from the code is worse than no contract, because the frontend trusts it.

**Run:** `uvicorn coursegen.app.main:app --workers 1`

**One worker only (S8).** `QdrantClient(path=...)` takes an exclusive file lock. A second
worker does not fail cleanly — it fails at Qdrant open time.

---

## The four outcomes every caller must handle

`/api/exam` is the only endpoint with a non-trivial outcome space, and this is the part
most likely to be got wrong. These are **four different things** and the UI must not
collapse them:

| outcome | HTTP | body | means |
|---|---|---|---|
| success | 200 | `status: "ok"` | a paper was produced with items in it |
| empty | 200 | `status: "empty"` | the run completed and produced **zero items** |
| degraded | 200 | `status: "degraded"` | a known limit was hit (quota, timeout); result is real but partial |
| failure | **500** | `{"detail": "..."}` | something unexpected broke; nothing was produced |

`degraded` is **not** an error and 500 is **not** degraded. Before 2026-09-01 every
unexpected exception was reported as `degraded` with `fill_ratio: 0.0`, which made "the
pipeline crashed" indistinguishable from "we generated a paper that filled nothing." Do
not re-merge them in the UI.

---

## Endpoints

### `GET /` → HTML
The static UI. `text/html`.

### `GET /api/health` → 200
```json
{"ok": true}
```
Liveness only. Says nothing about whether generation will work — use `/api/preflight`.

### `GET /api/preflight` → 200
```json
{
  "api_key": "ok",
  "output_dir": "ok",
  "models": "ok",
  "memory": "ok",
  "weasyprint": "ok",
  "qdrant": "ok"
}
```
Six fixed keys. Each is `"ok"` or a human-readable failure string. **Never raises** — a
failing check is a value, not an exception. Show this before letting anyone press Generate;
**`memory` is the one that will bite.** BGE-M3 needs ~4 GB of committable memory; below
that the process is killed by the OS (access violation), which no error handler can catch
and no degraded response can report. `models: "ok"` beside `memory: "<failure>"` means the
files are present and the machine still cannot run them. Block Generate and Chat on
`memory`, not just on `models`.

### `GET /api/blueprints` → 200
```json
["ai_fundamentals_v1", "final_default", "midterm_default", "quiz_default"]
```
A bare JSON array of blueprint ids (filename stems). Not an object.

### `POST /api/disclosure/accept` → 200
```json
{"accepted": true}
```
**Gate.** `/api/exam` returns **403** until this is called. Process-wide, in memory, and
resets when the server restarts — so the UI must handle a 403 by re-showing the disclosure,
not by treating it as a bug.

### `POST /api/ingest` → 200
`multipart/form-data`, field `files` (repeatable).
```json
{"nodes_ingested": 571}
```
**Slow: minutes, not seconds.** ~10 minutes for 14 decks / 572 chunks on CPU. Uploads land
in a temp directory that is deleted after the call, and the request does not return until
the whole run is over. **A browser should use `/api/ingest/start` instead**; this one is
kept because its shape is frozen and the static UI calls it.

Filenames are reduced to a bare name (`Path(name).name`). Before 2026-09-04 they were not,
and a `files` field named `../../../x.pdf` wrote outside the temp directory.

### `POST /api/ingest/start` → 200 / 409

Added 2026-09-04. Same multipart body. Starts the ingest and **returns immediately**:

```json
{"started": true, "files": ["03_search.pdf", "06_CSP.pdf"]}
```

**409** if one is already being indexed. There is no job id because there cannot be two
jobs: ingest writes one Qdrant collection and one `course_map.json`.

The job takes `_PIPELINE_LOCK`, so a generation started during an ingest waits rather than
failing on Qdrant's exclusive file lock. `/api/exam/stream` reports that wait with
`holder: "indexing your uploads"`.

### `GET /api/ingest/status` → 200

```json
{
  "status": "running",
  "stage": "indexing",
  "files": [
    {"file": "03_search.pdf", "state": "read", "blocks": 142, "source_type": "pdf"},
    {"file": "old.ppt", "state": "failed", "reason": "legacy .ppt is not supported…"}
  ],
  "topics": 187,
  "passages": 572,
  "nodes_ingested": null,
  "started_at": 1788463907.4,
  "finished_at": null,
  "error": null,
  "elapsed_seconds": 22.8
}
```

`status` is `idle` / `queued` / `running` / `done` / `failed`. `stage` is `reading` →
`mapping` → `indexing`, and `null` outside a run.

**Poll this rather than holding a stream.** Generation is streamed because a student
watches ~51 s; ingest is a job because nobody watches ~10 minutes — they switch tabs, close
the laptop, come back. Server-held state is the only kind a reloaded page can read, so a
run started in one tab is visible in the next. Keep polling when idle too: a run may have
been started somewhere else.

**`indexing` reports no sub-progress, and cannot yet.** It is one
`model.encode(texts, batch_size=…)` call and FlagEmbedding batches inside it with no
callback. Slicing that call would buy a progress number at the cost of changing the
embedding path — R10 wants byte-identical vectors proven first. Size the wait from
`passages`: `30 + passages` seconds fits both measured points (2 → 28 s, 572 → ~10 min).

**A terminal status means the uploads are already deleted.** The job removes the temp
directory before publishing `done` or `failed`, so a client acting on that status is acting
on a fact rather than a probability (S7).

### `POST /api/exam` → 200 / 403 / 500
```json
{"blueprint_id": "ai_fundamentals_v1", "title": "AI Final"}
```
Success:
```json
{
  "status": "ok",
  "fill_ratio": 1.0,
  "allocation_fidelity": 0.95,
  "unfilled_slots": [],
  "items_count": 20,
  "warnings": ["..."],
  "downloads": {
    "exam_html": "/api/files/exam.html",
    "answer_key_html": "/api/files/answer_key.html",
    "coverage_html": "/api/files/coverage.html",
    "exam_pdf": "/api/files/exam.pdf",
    "answer_key_pdf": "/api/files/answer_key.pdf"
  }
}
```

**`coverage_ratio` is deliberately absent.** It counts distinct nodes against the whole
corpus and read **0.04 on a paper that was 20/20 items and 100/100 marks**. Do not add it
back, and do not compute an equivalent in the frontend. `fill_ratio` is the completeness
number; `allocation_fidelity` is the share of slots placed by instructional mass rather
than by span exhaustion.

Degraded body carries `status: "degraded"`, a `warnings` array safe to show verbatim, and
no `downloads`. It also carries `items: []` where a successful body carries `items_count`
— these two shapes are **not** the same key set, and a client that reads `items_count`
unconditionally gets `undefined` on a degraded run.

**Serialised.** One generation at a time, process-wide (`_PIPELINE_LOCK`). A second
concurrent request **blocks until the first finishes** — it does not fail and does not
queue visibly. Double-clicking Generate produces a long silent wait. The UI must disable
the control on submit. (`/api/exam/stream` announces the wait instead — see below.)

### `POST /api/exam/stream` → 200 / 403

Added 2026-09-03. The same generation as `/api/exam`, narrated while it runs. Request body
is identical. Response is **NDJSON** — `application/x-ndjson`, one JSON object per line —
read in a browser with `response.body.getReader()`. Not SSE: this is a POST with a body
and `EventSource` is GET-only.

```
{"event":"stage","stage":"loading","state":"start"}
{"event":"stage","stage":"loading","state":"done"}
{"event":"stage","stage":"reading","state":"start"}
{"event":"stage","stage":"reading","state":"done","topics":571}
{"event":"stage","stage":"choosing","state":"start"}
{"event":"stage","stage":"choosing","state":"done","slots":7,"slots_total":7,"sections":2}
{"event":"stage","stage":"writing","state":"start","total":7}
{"event":"batch","phase":"writing","done":6,"total":7}
{"event":"batch","phase":"writing","done":7,"total":7}
{"event":"stage","stage":"writing","state":"done","items":7,"calls":2}
{"event":"stage","stage":"assembling","state":"start"}
{"event":"stage","stage":"assembling","state":"done"}
{"event":"result", ...exactly the /api/exam body...}
```

| event | when |
|---|---|
| `stage` | each pipeline stage, on `start` and again on `done`. Both, because `writing` is ~40 of the ~51 seconds and a client told only about completions sits still through it |
| `batch` | after each LLM batch returns. `phase` is `writing` or `rewriting`; the second is a re-attempt at items the gates rejected, counted against its own total |
| `result` | terminal. **Byte-identical to the `/api/exam` body** — one outcome space, one client code path |
| `error` | terminal. See below |

**Two stages are conditional.** `waiting` fires only when the run had to queue behind
another generation; `loading` only wraps the model load, which is instant on a warm
process and ~24 s cold (measured 2026-09-03). Neither is pipeline work — show them apart
from the four real stages so the stage list does not change length between runs.

**Failure is an `error` event on a 200, never a status code.** By the time generation
breaks, the status line is long gone. A client must treat `{"event":"error","detail":...}`
exactly as it treats a 500 from `/api/exam`. 403 is the exception: the disclosure gate is
checked before streaming starts, so it still arrives as a JSON 403.

**A stream can also just end.** If the connection closes with neither `result` nor
`error`, the server went away mid-run — the documented case is the OS killing the process
during the model load, which is an access violation no handler can catch and therefore
cannot be reported. Treat a terminated stream with no terminal event as a failure, but
**not** as "nothing was written": the pipeline runs in its own thread and does not stop
when the connection does, so the paper may well have landed. Check `/api/paper`.

### `POST /api/chat` → 200
```json
{"query": "What is A* search?", "history": [["prev question", "prev answer"]]}
```
`history` is an array of `[user, assistant]` pairs; malformed pairs are dropped silently.
```json
{
  "answer": "...",
  "citations": [{"file": "03_search.pdf", "page": 48}],
  "from_material": true
}
```
`from_material: false` means the answer was **not** drawn from the uploaded course
material. Surface that distinction — it is the whole point of the field.

### `GET /api/files/{filename}` → 200 / 404
Serves from the output directory. `Path(filename).name` strips every directory component,
so traversal is blocked (verified: 5/5 attempts return 404, including URL-encoded and
backslash forms). Only ever pass names that came from a `downloads` object.

### `POST /api/internal/reset-disclosure` → 200
Test seam. **Not for the UI.**

---

## Rules for anything built on this

1. **Never say a question is verified, accurate, grounded, or fact-checked.** Nothing in
   the system checks whether a generated question is *true*. The relevance gate rejects
   off-topic text; it cannot tell a true claim from a false one. This is measured, not
   suspected — see `P6-EVALUATION.md` §E8.
2. **Never display `coverage_ratio` as a quality figure**, or reconstruct one. It is
   inverted: it favours a random baseline on 2 of 4 blueprints.
3. **Synthesis items are not answerable from the student's material.** On the real paper
   that was 8 of 20 items and 70 of 100 marks. The manifest records it; the exam does not
   yet mark it. A student revising a synthesis question from their slides will conclude
   their notes are incomplete. This needs a UI affordance.
4. **First request is slow.** Models load lazily on first generate/chat. See
   `P6-EVALUATION.md` §E3.1 for the measured number. A spinner that gives no progress will
   read as a hang.
5. **Errors never carry a traceback** (R4 of the PRD). If you need detail, it is in the
   server log, not the response.
