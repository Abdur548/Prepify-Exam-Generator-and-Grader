# TODO

## P0 — Foundation  [COMPLETE — gate PASS 2026-08-27]

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
- [x] Install dependencies: `pip install -e ".[dev]"`
- [x] Run gate: `pytest -q`
- [x] Run gate: `python -m coursegen --dry-run`

### Tests to run after P0
- [x] `pytest -q` — all tests PASS, zero network calls
- [x] `python -m coursegen --dry-run` — prints model name, endpoint, estimated tokens, "No network calls were made"

### Features to verify after P0
- [x] `BudgetExceeded` raised (not a silent loop) when call cap hit — verified by: `test_exceeded_on_call_cap`
- [x] API key absent in non-dry-run raises `EnvironmentError` — verified by: `test_raises_without_key_in_non_dry_run`
- [x] `AIza*`/`sk-*` patterns scrubbed from redacted text — verified by: `TestKeyRedaction`

---

## P2′ — Solver on fixture  [COMPLETE — gate PASS 2026-08-27]

### Build
- [x] `tests/fixtures/course_map_sample.json` — hand-written, 20 nodes
- [x] `exam/blueprints/midterm_default.json`
- [x] `exam/blueprints/final_default.json`
- [x] `exam/blueprints/quiz_default.json`
- [x] `exam/allocate.py` — largest-remainder apportionment, deficit-ordered fill, span uniqueness
- [x] `exam/coverage.py` — `CoverageReport` builder

### Tests
- [x] Determinism: allocate twice → byte-identical `ItemSpec[]`
- [x] No span appears in two items
- [x] Coverage ratio reported and plausible
- [x] Over-full blueprint (count > spans) produces warnings + unfilled slots, not a crash
- [x] Difficulty ladder documented as heuristic in `pipeline.md`

---

## Corrective pass on P0 / P2′  [COMPLETE — uncommitted, awaiting human review of the diff]

From a senior review of `3eb0da9`. Full write-up in `progress.md`.

- [x] `CoverageReport` gains `slots_total` / `slots_filled` / `fill_ratio` — `coverage_ratio` was reporting 1.00 on a paper missing 4 of 22 questions
- [x] Sections solved most-constrained-first, emitted in blueprint order — flag-filtered sections were being span-starved
- [x] `spec_hash` includes `marks` and `options_count` — cross-paper cache collision
- [x] `SectionSpec.bloom` `min_length=1`; `Blueprint` marks-sum validator
- [x] `GeneratedItem` options/correct_option validator — P3's first gate now gates something
- [x] `node_id` definition corrected in the `course_map.py` comment (no ingest code written)
- [x] `COURSE_MAP_FLOAT_PRECISION`, `JSON_SORT_KEYS` added; logit-unit comments on `RERANKER_THRESHOLD` / `GROUNDEDNESS_TAU` (values unchanged — calibration is a P3/P4 task)
- [x] `README.md` created — including security requirement S8 (single-worker uvicorn)
- [x] 25 new tests; 64 pass; both gates green

---

## Pre-P1 prerequisites  [DO BEFORE ANY LIVE CALL]

- [ ] Create a **separate Gemini dev cloud project and a standby API key** before any live
      call. Quotas are per-project, so development traffic must not draw down the quota the
      demo depends on. Having a second key ready also makes a quota trip on demo day a config
      change rather than a dead demo.

---

## RESOLVED — `instructional_mass` formula, decided by the human 2026-08-27

The spec gave `normalise(token_count × (1 + log(1 + cross_document_term_repetition)))` but never
defined `cross_document_term_repetition`. **Decided: bounded document frequency.** Implement in
P1 exactly as follows, and write it into `pipeline.md` as prose, not just as code:

```
df_other(t)  = number of OTHER source files in the corpus containing term t
               (case-folded, whole-token match)
mean_df_i    = mean of df_other(t) over node i's YAKE key_terms   # [] -> 0.0
raw_i        = token_count_i * (1 + ln(1 + mean_df_i))
mass_i       = raw_i / sum(raw)                                   # sums to 1.0
```

- [ ] Terms are the node's YAKE `key_terms` (already a contract field, CPU-only).
- [ ] Counted over **documents**, not occurrences — df is bounded by the file count (typically
      3–20), so the multiplier stays in ~[1, 3.4] and `token_count` remains the primary signal
      with repetition as a genuine but bounded correction. Occurrence counts would reach ~7x and
      let a short section using common vocabulary outrank a long substantive one.
- [ ] **Mean** across the node's terms, not sum — sum would reward nodes that happened to yield
      more YAKE terms, which is an artifact of extraction, not of pedagogy.
- [ ] **Natural log.**
- [ ] Degrades correctly: with one source document every `df_other` is 0, `1 + ln(1) = 1`, and
      mass collapses to normalised `token_count`. This is the demo path (L14, single deck), so
      **this term is invisible in the demo** — do not tune it by watching the demo.
- [ ] Round with `COURSE_MAP_FLOAT_PRECISION` before serialising. Masses will then not sum to
      exactly 1.0 — assert with a tolerance, or apply a largest-remainder correction to the final
      node. Do not assert exact equality.
- [ ] Test: masses sum to 1.0 within tolerance; a term-repeated node outranks an equal-
      `token_count` node that is not repeated.
- [ ] P6: report coverage under this weighting **and** under flat `token_count`, so §16's
      falsification condition can actually be tested — if the repetition term changes nothing
      measurable, say so plainly rather than claiming it matters.

---

## P1 decisions — CONFIRMED by the human 2026-08-27 — implement during P1

- [ ] **Drop OCR from MVP1.** The spec requires OCR for pages with no text layer, but no OCR
      engine is in the pinned dependency list and new dependencies are prohibited. Decision:
      when a page has no text layer, log it and flag the file in the ingest report, so the gap
      is visible rather than silent. `OCR_DPI` stays in `config.py` unused.
      **CONFIRMED 2026-08-27 — implement in P1.**
- [ ] **`node_id = sha1(source_file + "|" + "/".join(path))`** per the corrected comment in
      `contracts/course_map.py`. Hashing the heading path alone collides across documents
      ("Introduction" in two decks → one node carrying one wrong `source_file` and
      `page_span`), and P1's "identical node IDs" gate would still pass because the result is
      deterministically wrong. **CONFIRMED 2026-08-27 — implement in P1.**

---

## P1 — Ingest  [NEXT — UNBLOCKED 2026-08-27, all decisions made]

- [ ] **Add `.gitattributes` pinning `*.json` to `text eol=lf` (or mark `course_map.json`
      `-text`).** `core.autocrlf=true` is set on this machine and the repo has no
      `.gitattributes`, so committed JSON is LF→CRLF converted on checkout. P1's gate is
      "identical `course_map.json` hash" and §14.6 commits a pre-baked demo collection — a hash
      computed before commit will not match one computed after a fresh clone on the demo
      machine, which would present as a random P7 cold-start failure. Found during the P2′
      corrective pass.

### Build
- [ ] `ingest/parse.py` — PyMuPDF + python-pptx, retain image blocks
- [ ] `ingest/structure.py` — relative-font heading tree
- [ ] `ingest/chunk.py` — leaf-section chunks, uuid5 IDs
- [ ] `ingest/embed.py` — BGE-M3 dense+sparse single forward pass
- [ ] `ingest/index.py` — Qdrant named vectors
- [ ] `ingest/coursemap.py` — build + persist course_map.json, using
      `COURSE_MAP_FLOAT_PRECISION` and `JSON_SORT_KEYS` (the identical-hash gate below is
      unachievable without a fixed rounding and key-order policy)

### Tests
- [ ] Ingest twice → identical point count, identical node IDs, identical `course_map.json` hash
- [ ] Slide-exported-PDF fixture: <30% of blocks classified as headings
- [ ] Malformed PDF fails that file only; batch completes
- [ ] Figure flags set on a document containing images
- [ ] OCR NOT triggered on native-text PDF
- [ ] Two documents sharing a heading produce two distinct nodes with correct `source_file`

---

## Blueprint strategy — human research 2026-08-27 — PARTIALLY ACCEPTED

Source: human-supplied "Dynamic LLM Blueprint Schema" (Table of Specifications, Bloom's
cognitive distribution per module, course-level skew). Logged per implementation plan §2.3.

### Accepted — deterministic, no LLM, no prohibition breach
- [ ] **P2: proportional Bloom distribution.** `SectionSpec.bloom: list[str]` is cycled
      round-robin (`allocate.py:183`), so `["remember","understand"]` can only ever mean an even
      50/50 split. Replace with an explicit integer mix (e.g. `bloom_mix: dict[str, int]` summing
      to `count`) so "60% remembering / 40% understanding" becomes expressible. Apportion using
      the same largest-remainder function already used for nodes.
      **Contract change — needs approval before code.**
- [ ] **P2: course-level skew as hand-authored variants.** "Introductory skews lower-order,
      advanced skews higher-order" needs no dynamic logic — it is two more hand-authored files
      (`midterm_intro.json`, `midterm_advanced.json`). PRD §8.2 already anticipates these.
- [ ] **P2: blueprint summary invariant.** Add lower-order vs higher-order totals and an
      alignment check to the `Blueprint` validator, extending the marks-sum validator added in
      the corrective pass. Fails loud at load time, costs nothing.
- [ ] **P4: render the coverage table as a Table of Specifications.** The solver already produces
      one — `CoverageReport.per_node` carries module path, `instructional_mass`,
      `marks_allocated` and slot list. Name it correctly and add the per-module Bloom breakdown
      once the item above lands.

### REJECTED — CONFIRMED by the human 2026-08-27: the LLM stays out of blueprinting
The proposal has the LLM derive `module_name` and `module_weight_percentage` from a retrieved
syllabus. That is topic allocation and weighting, which PRD §3 assigns to code ("Code decides
WHAT to ask about … the LLM decides only HOW to phrase it"). Three specific conflicts:
- **C2 / §9.1** — weights would come from an LLM call, but weighting is derived in `ingest/`,
  where zero LLM calls are permitted.
- **§2 / §9.6** — retrieving via fixed template queries ("Course Schedule", "Learning
  Objectives", "Grading Policy") is the generic-query-template failure mode the PRD names, and
  is the definition of `baseline_naive` — the control arm this architecture must beat.
- **§9.4** — the proposed per-module second pass is a two-pass design, explicitly rejected
  because single-pass makes key/question drift structurally impossible and halves call count
  against the 20-calls-per-exam cap (L1).

The pipeline already meets the proposal's stated goals by other means: module names come from the
heading tree (`CourseMapNode.path`), weights from `instructional_mass`, and "auditable before
spending tokens" is the existing coverage-report-before-generation gate.

### Bearing on the BLOCKED `instructional_mass` decision above
The proposal's `weighting_logic: "Time_spent_equals_exam_weight"` is independent corroboration
that instructional *time* is the intended weighting semantic — which supports reading
`token_count` as the primary signal and cross-document repetition as a bounded correction to it,
rather than the other way round. Still needs the human's explicit call.

### DROPPED — human declined 2026-08-27
- [x] ~~**Blueprint fitting from a syllabus's prose grading policy**~~ (reading "Midterm 30%,
      Final 40%" to set section weights). This is the one part that genuinely needs a model, and
      the human declined it on 2026-08-27 on both architectural and cost grounds. PRD §15 already
      lists blueprint fitting as out of scope. **Not in MVP1, not in Q7. Do not scaffold for it**
      (PRD §15: "Do not build these. Do not scaffold for these. Do not add interfaces 'in case'
      these arrive later.") Every blueprint in this system is hand-authored JSON.

### Human-authored course-specific blueprints
- [ ] Human is researching and authoring course-specific blueprint JSONs separately. These drop
      into `exam/blueprints/` with no code change — the `Blueprint` contract and its validators
      already cover them.

---

## P3 — Generation + validation

- [ ] **Calibrate `GROUNDEDNESS_TAU` against measured cross-encoder logits.** The current value
      0.45 was chosen as though it were a 0–1 similarity, but
      `cross-encoder/ms-marco-MiniLM-L-6-v2` emits unbounded raw logits (roughly −11 to +11).
      Until it is set from measurement it does not mean anything. Do not guess a new number.
- [ ] Confirm or replace the invented `eligibility` definition before `generate.py` consumes it
      — currently the intersection of `requires_flags_any` and the node's flags, so always `[]`
      for unconstrained sections (see `progress.md`, P2′ deviations)

---

## P4 — Render + chat

- [ ] **Calibrate `RERANKER_THRESHOLD` against measured cross-encoder logits** — same issue and
      same model as `GROUNDEDNESS_TAU` above.
- [ ] **Verify `weasyprint` imports on Windows** before relying on it. It needs GTK/Pango
      native libraries, a known Windows install cliff. Check this early — discovering it during
      the P7 rehearsal would be too late.
- [ ] Renderer and coverage view must display `fill_ratio` alongside `coverage_ratio` and must
      surface `unfilled_slots` — `coverage_ratio` alone can read 1.00 on an incomplete paper.
- [ ] Server must run single-worker (`--workers 1`) — `QdrantClient(path=...)` takes an
      exclusive file lock (security requirement S8, see `README.md`)

---

## Discovered mid-phase (do NOT do now)

- `coursegen/contracts/course_map.py` imports `Field` from pydantic without using it. Harmless;
  left alone as out of scope for the corrective pass.

---

## Backlog (post-project)

- [ ] Blueprint fitting from uploaded past paper (PRD §15 — out of MVP1 scope)
- [ ] Late chunking (PRD §15 — out of MVP1 scope)
- [ ] Multi-provider abstraction (PRD §15 — explicitly prohibited)
- [ ] 2-span `long` items — the contract allows 1–2 spans, the solver emits 1
