# TODO

## P0 — Foundation  [ACTIVE]

### Build
- [x] `config.py` — all tunables
- [x] `contracts/course_map.py`
- [x] `contracts/blueprint.py`
- [x] `contracts/item.py`
- [x] `contracts/coverage.py`
- [x] `llm/client.py` — budget guard, retry, dry-run
- [x] `tests/conftest.py` — mock client as default
- [x] `tests/test_p0_contracts.py`
- [x] `tests/test_p0_client.py`
- [x] `pipeline.md`, `progress.md`, `todo.md`
- [ ] Install dependencies: `pip install -e ".[dev]"`
- [ ] Run gate: `pytest -q`
- [ ] Run gate: `python -m coursegen --dry-run`

### Tests to run after P0
- [ ] `pytest -q` — expected: all tests PASS, zero network calls
- [ ] `python -m coursegen --dry-run` — expected: prints model name, endpoint, estimated tokens, "No network calls were made"

### Features to verify after P0
- [ ] `BudgetExceeded` raised (not a silent loop) when call cap hit — verified by: `test_exceeded_on_call_cap`
- [ ] API key absent in non-dry-run raises `EnvironmentError` — verified by: `test_raises_without_key_in_non_dry_run`
- [ ] `AIza*`/`sk-*` patterns scrubbed from redacted text — verified by: `TestKeyRedaction`

### Pre-P1 prerequisite (from implementation plan)
- [ ] Create separate Gemini dev cloud project + standby key before any live call

---

## P2′ — Solver on fixture  [BLOCKED on P0 gate]

### Build
- [ ] `tests/fixtures/course_map_sample.json` — hand-written, 20 nodes
- [ ] `exam/blueprints/midterm_default.json`
- [ ] `exam/blueprints/final_default.json`
- [ ] `exam/blueprints/quiz_default.json`
- [ ] `exam/allocate.py` — largest-remainder apportionment, deficit-ordered fill, span uniqueness
- [ ] `exam/coverage.py` — `CoverageReport` builder

### Tests
- [ ] Determinism: allocate twice → byte-identical `ItemSpec[]`
- [ ] No span appears in two items
- [ ] Coverage ratio reported and plausible
- [ ] Over-full blueprint (count > spans) produces warnings + unfilled slots, not a crash
- [ ] Difficulty ladder documented as heuristic in `pipeline.md`

---

## P1 — Ingest  [BLOCKED on P2′ gate]

### Build
- [ ] `ingest/parse.py` — PyMuPDF + python-pptx, retain image blocks
- [ ] `ingest/structure.py` — relative-font heading tree
- [ ] `ingest/chunk.py` — leaf-section chunks, uuid5 IDs
- [ ] `ingest/embed.py` — BGE-M3 dense+sparse single forward pass
- [ ] `ingest/index.py` — Qdrant named vectors
- [ ] `ingest/coursemap.py` — build + persist course_map.json

### Tests
- [ ] Ingest twice → identical point count, identical node IDs, identical `course_map.json` hash
- [ ] Slide-exported-PDF fixture: <30% of blocks classified as headings
- [ ] Malformed PDF fails that file only; batch completes
- [ ] Figure flags set on a document containing images
- [ ] OCR NOT triggered on native-text PDF

---

## Discovered mid-phase (do NOT do now)

_(none yet)_

---

## Blocked

_(none yet)_

---

## Backlog (post-project)

- [ ] Blueprint fitting from uploaded past paper (PRD §15 — out of MVP1 scope)
- [ ] Late chunking (PRD §15 — out of MVP1 scope)
- [ ] Multi-provider abstraction (PRD §15 — explicitly prohibited)
