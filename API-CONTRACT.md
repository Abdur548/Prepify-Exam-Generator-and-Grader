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
in a temp directory that is deleted after the call. There is currently **no progress
stream** — the request simply does not return for a long time. Plan the UI around that, or
add streaming to the backend first.

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

Degraded body carries the same keys minus `downloads`, with `status: "degraded"` and a
`warnings` array that is safe to show verbatim.

**Serialised.** One generation at a time, process-wide (`_PIPELINE_LOCK`). A second
concurrent request **blocks until the first finishes** — it does not fail and does not
queue visibly. Double-clicking Generate produces a long silent wait. The UI must disable
the control on submit.

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
