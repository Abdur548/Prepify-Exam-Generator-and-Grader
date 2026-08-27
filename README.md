# Prepify

Prepify generates university exam papers from a student's own course material. You upload
your lecture slides and notes; Prepify builds a course map of the syllabus, and a
deterministic **solver** allocates every question slot across that map before any language
model is called. Code decides *what* to ask — which topic, which source span, which Bloom
level, how many marks — and the LLM only phrases it. Because allocation happens in code,
coverage is a provable precondition rather than a hope: the coverage report is computed and
displayed before the paper is rendered.

## Security requirement S8 — run the server single-worker

`QdrantClient(path=...)` opens the local vector store in embedded mode and takes an
**exclusive file lock**. A second worker process cannot acquire that lock and crashes on
startup. Always run:

```
uvicorn coursegen.app.main:app --workers 1
```

Do not raise `--workers`, and do not let a process manager (systemd, a Procfile, a Docker
`CMD`) fork additional workers. Without this, the failure surfaces as an apparently random
startup crash — the kind that shows up for the first time on the demo machine.

## Install

```
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in the Gemini API key. The key is never required for
the dry-run gate.

## Gates

```
pytest -q                      # full test suite; makes zero network calls
python -m coursegen --dry-run  # prints prompts and token estimates; zero network calls
```

Both must pass before a phase is considered complete.

## Phase status

| Phase | Name | Status |
|---|---|---|
| P0 | Foundation — contracts, config, LLM client | complete |
| P2′ | Allocation solver on a hand-written fixture | complete |
| P1 | Ingest — parse, structure, chunk, embed, index, course map | **next** |
| P2 | Solver on real ingested data | not started |
| P3 | Generation + validation | not started |
| P4 | Render + chat | not started |
| P5 | UI + resilience | not started |
| P6 | Evaluation + baseline | not started |
| P7 | Hardening + rehearsal | not started |

P1 is blocked on one open question — the `instructional_mass` formula is undefined in the
spec. See `todo.md` under **Blocked**.

## Living documents

- `pipeline.md` — what the system is and how the stages connect
- `progress.md` — what has shipped, with pasted gate output
- `todo.md` — what is next, what is blocked, and what is deliberately deferred
