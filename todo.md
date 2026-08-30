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

## Corrective pass on P0 / P2′  [COMPLETE — shipped as `9b6bc9c`]

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

## P1 — Ingest  [COMPLETE — gate PASS 2026-08-27, re-verified after the corrective pass below]

- [x] **Add `.gitattributes` pinning `*.json` to `text eol=lf` (or mark `course_map.json`
      `-text`).** `core.autocrlf=true` is set on this machine and the repo has no
      `.gitattributes`, so committed JSON is LF→CRLF converted on checkout. P1's gate is
      "identical `course_map.json` hash" and §14.6 commits a pre-baked demo collection — a hash
      computed before commit will not match one computed after a fresh clone on the demo
      machine, which would present as a random P7 cold-start failure. Found during the P2′
      corrective pass.

### Build
- [x] `ingest/parse.py` — PyMuPDF + python-pptx, retain image blocks
- [x] `ingest/structure.py` — relative-font heading tree
- [x] `ingest/chunk.py` — leaf-section chunks, uuid5 IDs
- [x] `ingest/embed.py` — BGE-M3 dense+sparse single forward pass
- [x] `ingest/index.py` — Qdrant named vectors
- [x] `ingest/coursemap.py` — build + persist course_map.json, using
      `COURSE_MAP_FLOAT_PRECISION` and `JSON_SORT_KEYS` (the identical-hash gate below is
      unachievable without a fixed rounding and key-order policy)

### Tests
- [x] Ingest twice → identical point count, identical node IDs, identical `course_map.json` hash
      — the **point count** half was missing until the corrective pass below
      (`TestIngestPointCount`, real local-mode Qdrant on `tmp_path`; the pre-existing
      idempotency tests mocked `upsert`, so nothing exercised the claim)
- [x] Slide-exported-PDF fixture: <30% of blocks classified as headings
- [x] Malformed PDF fails that file only; batch completes
- [x] Figure flags set on a document containing images
- [x] OCR NOT triggered on native-text PDF
- [x] Two documents sharing a heading produce two distinct nodes with correct `source_file`

---

## Corrective pass on P1  [COMPLETE — shipped, gates independently re-verified]

From a senior review of `029b504`, whose PASS was premature. Full write-up in `progress.md`.

- [x] `has_table` detected properly — `page.find_tables()` (PDF, block bbox ∩ table bbox) and
      `shape.has_table` (PPTX). No new dependency.
- [x] `has_code` implemented as a documented monospace-font-name heuristic;
      `MONOSPACE_FONT_SUBSTRINGS` lives in `config.py`
- [x] `has_equation` **implemented, not stubbed** — a math font name **or** ≥
      `EQUATION_MIN_MATH_CHARS` characters from `MATH_UNICODE_RANGES`. `final_default.json` was
      therefore NOT edited and still requires `["has_figure", "has_equation"]`.
- [x] `PARSE_TIMEOUT_SECONDS` actually applied — thread watchdog in `parse_directory`. Batch
      isolation, not preemption; the limitation is stated in the docstring.
- [x] `shape_type == 13` → `MSO_SHAPE_TYPE.PICTURE`; `len(text) // 4` →
      `config.CHARS_PER_TOKEN_ESTIMATE`
- [x] `numpy` declared in `pyproject.toml` (declaring an existing transitive dependency, not
      adding one)
- [x] `df_other` docstring corrected to describe what the code actually does — see the
      confirmation request below
- [x] Ingest → solver integration test on a REAL course map (`TestIngestSolverIntegration`)
- [x] `progress.md` point-count gate line restored; `pipeline.md` Ingest stage written
- [x] 31 new tests; 122 pass; both gates green; zero-network re-verified with sockets blocked

### RESOLVED — `df_other` narrowing CONFIRMED by the human 2026-08-27

The plan and the old docstring both said:

> `df_other(t)` = number of OTHER source files **containing** term t

The code has always measured something narrower. `term_docs` is built only from each node's
YAKE `key_terms`, so `df_other(t)` counts the other source files in which t was itself
**extracted as a key term**. A term present in another document's body but outside that
document's top-N key terms contributes 0. It measures **salience**, not presence.

- [x] **CONFIRMED 2026-08-27: keep the narrowing.** `df_other` measures cross-document
      *salience* — the other source files in which the term was itself extracted as a YAKE key
      term — not raw text presence. This is the intended semantic. The docstring describes the
      implemented behaviour and the computation stands as written. Do not "fix" it toward the
      plan's original prose in a later phase; the plan's prose was the imprecise half.
      Note the direction of the effect: salience is a stricter test than presence, so `df_other`
      runs lower than the plan implied, the `(1 + ln(1 + mean_df))` multiplier stays nearer 1.0,
      and `instructional_mass` sits closer to normalised `token_count` than the formula suggests
      at a glance. Relevant when P6 compares this weighting against flat `token_count` — the two
      arms are closer together than the formula makes them look.

### Surfaced by the corrective pass, deliberately NOT done in it

- [x] **PPTX table cell text is now extracted** (done 2026-08-27, human-directed). A table
      shape has no `text_frame`, so it used to contribute a `has_table` block with **empty
      text** — the flag propagated while the table's content was never chunked or embedded, so
      a node could be flagged `has_table` with nothing available to ground a question.
      `_parse_pptx` now walks `shape.table` row-major, joining cells with `_TABLE_CELL_SEP`
      (`" | "`) and rows with `_TABLE_ROW_SEP` so row associations survive into the grounding
      span. Cells covered by a merge are skipped via `cell.is_spanned` — python-pptx puts the
      merged text on the origin and returns `""` at every spanned position, so a naive
      rows/cells loop emits a stray blank cell per merge. Cell runs also feed the `has_code` /
      `has_equation` heuristics, and an empty table now raises a warning rather than passing
      silently. Both PPTX fixtures were carrying *empty* tables, which is why the gap survived
      review; they now carry real cell text.
- [x] **Flaky timeout test fixed** (found 2026-08-27 while adding the table tests).
      `TestParseTimeout` installed `PARSE_TIMEOUT_SECONDS = 0.2`, but a real `parse_file` on
      the native fixture measures **50–96 ms** (`find_tables()` dominates) — only ~2x headroom.
      Under full-suite CPU contention the *healthy* file timed out as well, `parse_directory`
      returned nothing, and the assertion failed intermittently: it passed in isolation and on
      3 of 4 full-suite runs. Now `_TIMEOUT_TEST_SECONDS = 1.0`, ~10x the observed worst case,
      with the measurement recorded in a comment. The blocked file waits on an `Event` so it
      trips the watchdog at any threshold — raising this cannot mask a real failure. Verified
      5 consecutive green full-suite runs.
      **Worth remembering:** a flaky gate is worse than a failing one here. The whole review
      protocol rests on pasted test output meaning something, and a test that passes on retry
      trains everyone to retry.
- [ ] **`ingest/chunk.py` keeps its own `_CHARS_PER_TOKEN = 4`**, a second copy of what is now
      `config.CHARS_PER_TOKEN_ESTIMATE`. It governs chunk sizing rather than
      `instructional_mass`, so unifying it would move chunk boundaries and change every
      `chunk_id` — gate-visible, and it needs its own decision.
- [ ] **P6: measure the flag heuristics on real course material.** `has_code` sees only
      monospace font names; `has_equation` sees only math font names and math Unicode and is
      blind to mathematics typeset as an image (no OCR in MVP1). On PPTX both font-name prongs
      are blind whenever `run.font.name` is `None`, which is the common inherited-theme case.
      Precision and recall are currently unmeasured.
- [ ] **P2: check section C's candidate headroom on the real deck before trusting
      `final_default`.** On the synthetic corpus, real ingest produced 32 nodes / 32 spans with
      `has_figure: 3, has_table: 2, has_equation: 3, has_code: 2` — 6 candidate spans for
      section C's 4 slots (1.5× headroom), `fill_ratio = 1.0`, nothing unfilled. A course deck
      with two figures and no typed mathematics would starve section C on **material**, not on
      scheduling.
- [ ] **P6: the eval harness must report `allocation_fidelity` for the solver arm alongside
      coverage.** The whole evaluation is solver vs `baseline_naive` on coverage. If a large
      share of the solver's paper is placed by span exhaustion rather than by mass — on the real
      course map that is 29% / 32% / 38% for quiz / midterm / final — then reporting coverage
      alone **credits the mass-allocation thesis for work that span-exhaustion is doing**. The
      solver's claim is that code decides *what* to ask, by `instructional_mass`; an eval that
      cannot separate mass-placed slots from exhaustion-placed ones cannot support that claim.
      Report `allocation_fidelity` per blueprint, per arm.

---

## P2 review — `allocation_fidelity` instrumentation  [COMPLETE — shipped 2026-08-28]

- [x] **Invariant raised, not asserted.** The slot-accounting check was a bare `assert`, which
      `python -O` strips — silently disabling the one guard between a broken count and a
      believable-looking coverage table. Converted to an explicit `raise ValueError` and
      covered by `TestSlotAccountingInvariant`, which proves it *fires* rather than only that
      it holds on good input. **General rule for this codebase: any check whose failure would
      produce a plausible-but-wrong artifact must raise, not assert.** R5's caps
      (2 retries, 1 regeneration pass, 20 calls per exam) are in that category — check them
      when P3 lands.

Full write-up with pasted gate output in `progress.md`; the three-ratio contract is documented
in `pipeline.md`.

- [x] **`CoverageReport` gains `slots_by_mass` / `slots_by_fallthrough` / `allocation_fidelity`.**
      On real ingest output every node has exactly one span (32 nodes, 32 spans, max 1), because
      a lecture-slide section holds far less than `MAX_CHUNK_TOKENS = 512`. With the
      span-uniqueness invariant that caps each node at **one question**, so when Hare
      apportionment awards a node three slots, two are unsatisfiable and fall through to
      whatever node still has a span. `final_default` was reporting `coverage_ratio 1.000` and
      `fill_ratio 1.000` while 38% of the allocation mechanism did not operate — both headline
      numbers perfect over a paper more than a third of which was placed by exhaustion.
      Measured fidelity: `quiz_default` 0.714, `midterm_default` 0.682, `final_default` 0.625.
      Counted at the two branches that already existed in `_solve_section`, never by parsing
      the warning strings. `build_report()` asserts
      `slots_by_mass + slots_by_fallthrough == slots_filled` in code, not in a comment.
      **Measurement only — allocation behaviour is unchanged**, proved by diffing `solve()`
      output against the pre-change solver loaded from `d73a610` into the same process: byte
      identical on the fixture map, on the real ingest map, and on two synthetic shapes.
      This is a **spec-level tension, not a bug in `allocate.py`.** Nothing in the solver is
      wrong. Fidelity below 1.0 on lecture-slide corpora is structural and stays that way until
      either a node can yield more than one span or span-uniqueness is relaxed — both spec
      changes, neither attempted here.

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

## P3 — Generation + validation  [COMPLETE — gate PASS 2026-08-28]

- [ ] **Calibrate `GROUNDEDNESS_TAU` against measured cross-encoder logits.** The current value
      0.45 was chosen as though it were a 0–1 similarity, but
      `cross-encoder/ms-marco-MiniLM-L-6-v2` emits unbounded raw logits (roughly −11 to +11).
      Until it is set from measurement it does not mean anything. Do not guess a new number.
- [x] Confirm or replace the invented `eligibility` definition before `generate.py` consumes it
      — P3 does not consume `eligibility`; generation passes the full `ItemSpec` through the prompt
      as solver output, and validation uses only `slot_id`, `item_type`, `span_ids`, and `spec_hash`.
- [x] `llm/prompts.py` — stable generation prompt; source spans delimited and labelled as data,
      never instructions.
- [x] `exam/generate.py` — 6-spec batching, `spec_hash` cache, one regeneration pass,
      `run_manifest.json`.
- [x] `exam/validate.py` — schema, groundedness, duplication, MCQ hygiene gates.
- [x] Mocked end-to-end generation tests — `tests/test_p3_generation_validation.py`.
- [ ] Implement/calibrate MCQ "exactly one option high-similarity to the answer span" hygiene
      check. Current P3 hygiene covers all/none-of-above, length band, duplicate normalized
      options, and fixed-seed shuffle.

---

## P3 review — reviewer corrections  [COMPLETE — shipped with P3, 2026-08-28]

Four defects found reviewing P3 before it was committed. Full write-up in `progress.md`.

- [x] **Skipped gate was indistinguishable from a passed gate.** With `groundedness_scorer` or
      `embedding_fn` absent the gate silently did not run, and the manifest recorded zero
      failures either way. Now a per-gate `{evaluated, passed, failed, skipped}` record — which
      is what R6 asks for.
- [x] **`blueprint_id` added to the manifest.** R7 needs the manifest to be sufficient to
      regenerate the same `ItemSpec[]`; that is a function of the course map *and* the
      blueprint, so `course_map_hash` alone cannot tell a midterm run from a final one.
- [x] **MCQ shuffle seed now varies per item.** One seed for every item gave the whole paper the
      same permutation; models emit the correct answer first, so the answer landed in the same
      position on every question.
- [x] **Options relabelled by position and `correct_option` remapped.** Shuffling while leaving
      labels attached to their text produced `C,B,D,A` with the key still on "A" — a wrong
      answer key on every shuffled MCQ once P4 renders by position.

### Open decision for the human

- [ ] **Should `generate_exam` require the scorer and embedder in production?** Skipping is now
      *recorded* rather than silent, which fixes the reported defect. But §9.5 defines
      validation as four gates, and a production run arguably should not be able to skip two of
      them at all. The counter-argument is test ergonomics — injection is what keeps the default
      suite network-free. Left as a design call rather than decided unilaterally.

### Carried into P4

- [x] **The renderer must use the option labels as authoritative**, not re-derive them from list
      position. They now agree, so either works — but only because the shuffle relabels. If that
      ever changes, position-based labelling silently corrupts the answer key.
- [x] **`allocation_fidelity` must appear in the rendered coverage table** alongside
      `coverage_ratio` and `fill_ratio` (carried from the P2 review).

---

## P4 — Render + chat  [PARTIAL — code tests PASS 2026-08-28; PDF gate blocked by GTK]

- [x] **`RERANKER_THRESHOLD` CALIBRATED 2026-08-29: 0.5 → −2.0.** Measured against the real
      model, not reasoned about. `CrossEncoder.default_activation_function` is `Identity` — no
      sigmoid — so the scores are raw unbounded logits: a perfect match scored **+9.48**,
      nonsense **−11.23**. Over a lecture-slide-shaped corpus, genuinely answerable queries
      scored **+1.68 … +7.97** and near-misses (same field, adjacent vocabulary, absent from the
      corpus) **−11.40 … −5.99**. Far-irrelevant queries all sat below −10.9 and never mattered;
      **near-misses are the real boundary.** −2.0 sits near the midpoint of (−5.99, +1.68) with
      ~4.0 margin above and ~3.7 below. 0.5 also classified the set perfectly, but was badly
      placed — 6.5 of margin on one side and 1.2 on the other, so a paraphrased or
      lightly-covered question scoring +0.3 would have been wrongly told "not from your
      material". `TestRerankerThresholdCalibration` pins the measured bounds; verified that
      restoring 0.5 makes it fail.
- [ ] **Re-check the threshold against the real AI deck.** Near-miss scores rise as a corpus
      covers more adjacent topics, and that ceiling is what the threshold must clear. The
      measurement above used representative but synthetic passages.
- [ ] **`GROUNDEDNESS_TAU` is still uncalibrated — and must NOT copy −2.0.** Same model, but a
      question-vs-chunk score and an answer-vs-its-own-source-span score have different
      distributions. A generated answer derived from its span is far more similar than a
      question is to a passage, so grounded pairs cluster much higher; borrowing the retrieval
      threshold would pass essentially everything and the gate would stop gating. Calibrate it
      the same way: grounded answers vs their spans, hallucinated answers vs the same spans,
      value between them. The warning is recorded in `config.py` next to the constant.
- [x] **Both pinned models now present on disk.** `BAAI/bge-m3` was already cached;
      `cross-encoder/ms-marco-MiniLM-L-6-v2` (~90 MB) was **not** — meaning the groundedness
      gate and the chat reranker had only ever run against injected stubs, and R8's "models
      present on disk" preflight would have failed. Downloaded 2026-08-29 with the human's
      approval. **P7: the demo machine needs both — do not discover this cold on the day.**
- [x] **Verify `weasyprint` imports on Windows** before relying on it. It fails on this machine:
      missing `libgobject-2.0-0` / GTK/Pango native runtime. README prerequisite added.
- [x] Renderer and coverage view display `fill_ratio` alongside `coverage_ratio` and surface
      `unfilled_slots`.
- [x] **The rendered coverage table shows `allocation_fidelity` next to `coverage_ratio`
      and `fill_ratio`**, plus `slots_by_fallthrough`.
- [x] Server single-worker note remains in README (`--workers 1`) for S8.
- [x] Jinja2 autoescape protects model output; `<script>` renders as text, not markup.
- [x] Answer key uses MCQ option labels as authoritative.
- [x] Hybrid retrieval issues dense+sparse prefetch with RRF and `RETRIEVE_TOP_K = 10`.
- [x] Chat sends top `SEND_TOP_K = 4` contexts, cites file+page, and caps history to last
      `CHAT_MAX_TURNS = 6` turns.
- [x] Low reranker score returns explicit "not from your material" path.
- [ ] Install/verify WeasyPrint GTK/Pango runtime on the demo machine, then rerun the real PDF
      artifact gate. Until then P4 is PARTIAL, not PASS.

---

## P4 review — reviewer corrections  [COMPLETE — shipped with P4, 2026-08-28]

- [x] **Chat cited one of the four contexts it sent.** `citations = _unique_citations(contexts[:1])`
      while all `SEND_TOP_K = 4` chunks went to the model, so an answer drawing on chunk 2, 3 or
      4 displayed a citation pointing at material that does not contain the claim. Now cites
      `contexts`; dedupe by `(file, page)` keeps the list short. Fifth instance in this project
      of an artifact that reads as verified when it was not.
- [x] **Personal path re-leaked into a pasted traceback (S9).** The WeasyPrint failure was
      pasted verbatim with nine `C:\Users\<name>\...` occurrences. Same leak was redacted in
      `002e2c6`; the repo is now public, so it would have shipped on the next push. Redacted
      with the diagnostic text preserved.

- [ ] **Redacting pasted output needs to be a habit, not a catch.** Tracebacks and pytest
      headers carry machine paths by default, and §2.4 requires pasting output verbatim — the
      two pull against each other. Twice now the leak arrived via honest evidence-pasting.
      Worth a pre-commit grep for `C:\\Users\\` and `/home/` before P7.
- [ ] **`SEND_TOP_K` guard is unreachable** — `contexts = reranked[:SEND_TOP_K]` cannot exceed
      `SEND_TOP_K`, so `if len(contexts) > SEND_TOP_K: raise` never fires. Not a defect, but it
      does not assert what L9 asks for. The meaningful guard is on what
      `_messages_with_material` actually embeds in the prompt. Low priority.

---

## Ingest fixes — `.ppt`, `.docx`, chunk page precision  [SHIPPED — uncommitted 2026-08-28]

- [x] **`.ppt` rejected by name, not by accident.** It was in the scanned set and dispatched to
      `_parse_pptx`, where python-pptx threw an opaque zip error that `parse_directory` logged
      as a generic parse failure — it read as a corrupt file rather than an unsupported format.
      `.ppt` is still scanned (so a legacy deck never vanishes silently) and `parse_file` now
      raises `ValueError` naming the format and the remedy: "legacy .ppt is not supported (it is
      a binary format, not OOXML...); convert it to .pptx and re-run."
- [x] **`.docx` ingestion.** `python-docx` added to `pyproject.toml` (authorised). `_parse_docx`
      emits the same `TextBlock` list, sets all four content flags, and extracts table cell text
      with the same `_TABLE_CELL_SEP` / `_TABLE_ROW_SEP` as the PPTX path.
- [x] **`is_slide_heading` → `is_explicit_heading`**, driven by DOCX becoming the second format
      to set it. Means "the format told us this is a heading" (PPTX placeholder type / DOCX
      paragraph style) as opposed to PDF's inferred font-size path.
- [x] **`chunk.page` is now the page of the first block that contributed to that chunk**, not
      `section.page_start`. Chunk IDs — and therefore Qdrant point IDs — changed by design; a
      previously built index is stale and must be re-ingested.

### Open — carried into P4/P5

- [ ] **The renderer must label DOCX locators differently.** A `.docx` has no pages, so
      `chunk.page` for a DOCX is a **1-based block ordinal**: a citation reading "page 12"
      means the twelfth block, not the twelfth printed page. The renderer and the chat citation
      path should print `¶12` for a DOCX and `p.12` for a PDF/PPTX, which needs the **source
      type available at citation time** — `Chunk` / the Qdrant payload / `RetrievedChunk` carry
      `file` and `page` but not `source_type`. No page-estimation heuristic is to be added:
      counting explicit page breaks is wrong for the majority of documents that contain none,
      and a plausible-but-wrong page number is worse than an honest ordinal.
- [ ] **DOCX heading nesting is unresolved, deliberately.** Word style names carry a level
      ("Heading 1" vs "Heading 2") and could produce a nested heading path the way the PDF
      font-size path does. `TextBlock` carries no level and nothing in the change asked for
      nesting, so DOCX heading paths are **flat**, one entry per section — the same shape the
      PPTX path produces. Recorded rather than invented; needs a human decision plus a new
      block field if nesting is wanted.

### Discovered while fixing chunk pages — NOT fixed, out of scope

- [x] **`_extract_pdf_sections` never assigned `page_start` — FIXED 2026-08-28 by the reviewer.**
      In `ingest/structure.py` the closure `flush()` read a `page_start` variable initialised to
      `0` and never reassigned, while the loop maintained a separate `current_page_start` that
      nothing read. Every PDF leaf section reported `page_start = 0`, so every PDF node carried
      `CourseMapNode.page_span = (0, page_end)`. Found by the ingest-fix agent and deferred as
      out of scope; fixed here because it is the same defect class as the chunk-page work that
      authorised the change. Verified: a three-chapter PDF now yields `page_start` 0/1/2 where
      it previously yielded 0/0/0. `TestPDFSectionPageStart` covers it.
      **Worth remembering why review missed this:** the wrong value was perfectly
      *deterministic*, so the P1 idempotency gate passed on it every time. Determinism tests
      prove a value is stable, never that it is right.
- [ ] **Repeated heading text inside ONE document collides on `node_id`.**
      `node_id = sha1(source_file + "|" + "/".join(path))` and both explicit-heading paths emit
      FLAT single-entry paths, so two sections in the same file under headings with identical
      text ("Summary", "Exercises") produce the same `node_id` and appear twice in the course
      map. Pre-existing — two PPTX slides with the same title already do this — but a DOCX makes
      it likelier, since repeated section headings are ordinary in prose documents. Not
      introduced by this change and not fixed in it; the fix (an ordinal or the parent path in
      the hash) changes every node ID and needs its own decision.

---

## Spec Amendment 01 — stage 1 (authored topics)  [SHIPPED — uncommitted 2026-08-29]

- [x] `SectionSpec.topic: str | None = None`. `None` **is** the derived path: no topic filter,
      candidates are the whole course map, allocation mass-proportional across everything. All
      three shipped blueprints stay topic-free and `solve()` returns byte-identical `ItemSpec[]`
      on them; all 213 pre-existing tests pass unmodified.
- [x] Topic→node matching in `exam/allocate.py` (`_tokenise` / `_topic_scores`) — no new module,
      no new dependency. Candidate filter added beside the flag filter; **no fallback to the
      unfiltered course map**, unmatched slots stay unfilled.
- [x] `TopicCoverage` + `CoverageReport.per_topic`; `allocation_fidelity`'s narrowed meaning
      under an authored blueprint documented in `pipeline.md`.
- [ ] **🔴 BLOCKS STAGE 4 — the scoring rule punishes specificity. Fix the rule, not the
      threshold.** Measured on the fixture course map:
      ```
      "Search"                                              best = 1.000  -> matches
      "Uninformed and Informed Search (BFS, DFS, A*, ...)"   best = 0.286  -> matches NOTHING
      ```
      The richer, more precise topic scores **3.5x worse than the bare word for the same
      topic**, and falls below the floor. Cause: `score = |topic ∩ node| / |topic tokens|`
      puts every enumerated term in the denominator, so each extra specific term a topic names
      *lowers* its score unless the node happens to contain it. That is backwards — and **every
      topic in `template_ai_fundamentals_v1` is a long parenthetical string of exactly this
      shape**, so essentially all of them would match nothing.
      This is a design error in the rule the reviewer specified, not an implementation fault:
      the matcher ranks the right node first and the scoring then throws it away. Lowering the
      floor is not the fix — it would admit noise everywhere else while leaving the dilution
      intact. Candidate directions, needing a decision: score against the topic's *distinctive*
      tokens only (IDF-weight against the course map, so "search" outweighs "and"); or take the
      best-matching sub-phrase rather than whole-string coverage; or normalise by matched
      tokens rather than topic length. **Settle this before stage 4; stages 2 and 3 are
      unaffected.**
- [ ] **Calibrate `TOPIC_MATCH_MIN_SCORE` against the real AI course deck when it arrives.**
      0.3 was chosen by inspection, not measured — same class of defect as `GROUNDEDNESS_TAU`
      and `RERANKER_THRESHOLD`. **Do not tune it against `tests/fixtures/course_map_sample.json`:**
      that fixture is a generic CS syllabus, not representative, and a number fitted to it would
      look measured while meaning nothing.
      Evidence it needs calibration, on the fixture: Amendment 01's own example topic
      `"Uninformed and Informed Search (BFS, DFS, A*, Heuristics)"` scores **2/7 ≈ 0.286** on
      `n11` ("3.3 Graph Traversal", key_terms BFS/DFS/visited/adjacency) — the one node that is
      genuinely about search — and is therefore rejected by a floor of 0.3. The matcher ranks
      the right node first; the floor is what excludes it.

### Open for the human — recorded, deliberately NOT resolved in stage 1

- [ ] **The amendment's own "raises" example does not raise.** Stage 1 specifies "drop tokens
      shorter than `TOPIC_MATCH_MIN_TOKEN_LEN` (set it to 3)" and separately gives `"of and the"`
      as a topic that must raise. Those conflict: `"and"` and `"the"` are exactly 3 characters,
      so they survive `len(tok) >= 3`. The rule was implemented **as written**; `"of and the"`
      therefore tokenises to `{and, the}` and matches `n03` ("1.3 Variables and Scope") at 0.50
      on the strength of the word "and". Pinned by
      `TestMalformedTopicRaises::test_three_letter_stopwords_survive_the_threshold`, which
      documents the gap rather than blessing it. Fix is a decision, not a guess: raise the
      threshold to 4, add a real stopword list (new dependency — currently prohibited), or
      accept that 3-letter connectives dilute the denominator.
- [ ] **A near-miss below the floor is invisible.** `TopicCoverage.best_score` is specified as
      "highest score among **matched** nodes; 0.0 if none matched", so a topic whose best node
      scored 0.29 reports `best_score = 0.0` — indistinguishable from a topic with no overlap at
      all. Amendment §7 calls the half-match the *more* dangerous case. Recording the best score
      over all candidates (matched or not) would make it inspectable; that changes the field's
      specified meaning, so it needs a decision.
- [ ] **Solve order still keys only on `requires_flags_any`.** A topic-restricted section is at
      least as constrained as a flag-restricted one, so in a MIXED blueprint (some sections
      topical, some not) an unconstrained section solved first can drain the spans a topical
      section needs — reported as span exhaustion, not as missing material. Not changed: it is
      outside stage 1's brief, and every authored blueprint envisaged so far carries a topic on
      every section. Fix, if wanted, is one key: `(0 if (requires_flags_any or topic) else 1, i)`
      — provably a no-op for topic-free blueprints.
- [ ] **`spec_hash` deliberately excludes `topic`.** The topic decides *which node* is chosen,
      not how an item is phrased from a span, so two papers hitting the same node+span+bloom+
      marks+type still share a cache entry. Correct as far as stage 1 goes; revisit at stage 3
      when `generation_instructions` starts reaching the prompt, because that text **does**
      change the output for identical inputs.

---

## Spec Amendment 01 — stage 2 (authored structure)  [SHIPPED 2026-08-29]

- [x] `format_requirement` on `SectionSpec` / `ItemSpec`, validated against
      `config.KNOWN_FORMAT_REQUIREMENTS`, **raising** on an unknown value. Joins `spec_hash`
      **only when set** — encoding `None` as `""` would have added a trailing separator and
      silently invalidated the generation cache for all three shipped blueprints.
- [x] `group_id` on both — sub-questions expand into N specs, not one nested composite.
      Deliberately **excluded** from `spec_hash`: presentational, so regrouping must not
      invalidate a cached generation.
- [x] `bloom_mix` — proportional Bloom within a section, via the existing `_hare_apportionment`.
      Absent ⇒ previous even round-robin. Levels assigned grouped, in `section.bloom` order.
- [x] `cognitive_balance` — declared exam-level target; `CoverageReport` carries the realised
      distribution beside it and warns past `COGNITIVE_BALANCE_TOLERANCE = 0.10`. Warning, never
      a raise. **This is the only signal that would catch a paper drifting to easy recall
      questions while the blueprint asked for 70% apply/analyse.**
- [x] 54 new tests; 314 pass; 12/12 mutations caught; shipped blueprints byte-identical,
      verified against a `git archive` of `53b9392` in a separate process.

### Open, recorded not resolved

- [ ] **`bloom_mix` value policy.** `{0.6, 0.4}` and `{6, 4}` behave identically; an **all-zero**
      mix falls into `_hare_apportionment`'s zero-mass branch and splits evenly — it fails
      quietly rather than loudly, which is the wrong direction for this codebase. Decide whether
      to require sum-to-1.0, reject non-positive values, or accept the current behaviour.
- [x] **`group_id` reaches the LLM prompt** via `spec.model_dump()`, so prompt text can vary
      while `spec_hash` does not. **RESOLVED in stage 3** — excluded via
      `spec.model_dump(exclude=_PROMPT_EXCLUDED_SPEC_FIELDS)`. Human decision 2026-08-29.
- [ ] **`cognitive_balance` has no parse-time validation**, not even sum-to-1.0. A declared
      target summing to 1.3 is arguably malformed on its face.
- [ ] **Short-fill trade-off:** with a grouped Bloom order, a section filling 5 of 10 slots at
      60/40 emits only the first level. Interleaving would degrade more gracefully but changes
      the documented order. The `cognitive_balance` warning surfaces it either way.
- [ ] **`format_requirement` is validated on `SectionSpec` only**, not on `ItemSpec`. Fine while
      the blueprint is the only entry point; a later stage constructing specs from another source
      would not be covered.

---

## Spec Amendment 01 — stage 3 (grounding + generation instructions)  [SHIPPED 2026-08-30]

- [x] `grounding: Literal["span", "synthesis"] = "span"` on `SectionSpec` / `ItemSpec`. `"span"`
      is current behaviour and the default. `"synthesis"` means the model invents the artifact
      and the span is context, not the thing being reproduced.
- [x] `grounding` **joins `spec_hash`, appended only when it is not the default** — encoding
      `"span"` would have added a trailing separator, changed every hash in the product and
      silently invalidated the on-disk generation cache for all three shipped blueprints. Same
      trap `format_requirement` avoided in stage 2; checked as mutation M1, and caught by the
      stage-2 test.
- [x] Gate 2 records a synthesis item as **`not_applicable`** — a new per-gate int beside the
      existing `skipped` bool. It does not fail the item and does not silently pass it.
      **"We could not check" and "there is nothing to check against" are different facts** and
      collapsing them would hide the more important one.
- [x] `generation_instructions` on `SectionSpec` / `ItemSpec`, into the **user** message only.
      Joins `spec_hash`, appended only when set.
- [x] `run_manifest.json` carries `grounding: {synthesis_items, items_total, synthesis_ratio}`
      on every run, plus `warnings` past `SYNTHESIS_ITEM_WARN_RATIO = 0.25` (a starting value,
      commented as such, not a measured one).
- [x] `group_id` excluded from the LLM prompt — the stage-2 open item above.
- [x] 36 new tests; 350 pass; 8/8 mutations caught, each asserting the mutation applied;
      shipped blueprints byte-identical on every pre-existing field, `spec_hash` included,
      verified in a separate process with `os.path.normcase` tree identity.

### Owed by a LATER stage — recorded here so it is not lost

- [ ] **The rendered paper must MARK synthesis items for the student.** `exam/render.py` was out
      of scope for stage 3, so the count is currently visible only in `run_manifest.json` — which
      a student never reads. A synthesis item is a question **not backed by their own uploaded
      material**: they cannot revise a novel game tree from their own slides (§7). Marking it on
      the paper (and in the answer key) is what turns a buried number into an honest one. Decide
      the marking: a per-item badge, a section note, or a line in the coverage table.
- [ ] **S2 — `generation_instructions` is a prompt-injection surface the moment users can author
      blueprints.** It is author-supplied text that reaches the model **as instructions**, not as
      delimited data — the one thing `SYSTEM_PROMPT` tells the model that source spans are not.
      Safe today because blueprints are authored by the project and shipped in the repo. Before
      any user-supplied blueprint path exists it needs the same treatment source spans already
      get: delimited, labelled as data, and the system prompt told not to obey it. Recorded in
      the `SectionSpec.generation_instructions` docstring as well as here.

### Open, recorded not resolved

- [ ] **`not_applicable` was added to gate 2 only.** The brief states the invariant "for any
      gate", but the work item is explicitly about gate 2. Gate 4 (MCQ hygiene) has the same
      shape — a `short` item reaches it and is neither evaluated nor counted — so
      `evaluated + not_applicable == items that reached it` is literally true for gate 2 and not
      for gate 4. Widening it was not authorised, so it was not invented. **Decide whether gate 4
      should count non-MCQ items as not-applicable.**
- [ ] **`not_applicable` is counted even when the gate is `skipped`.** Applicability is a
      property of the item, not of what the caller injected, so a run with no scorer AND
      synthesis items reports both facts. The cost: on a skipped gate `evaluated` is 0 by
      construction, so the invariant above is short of what reached the gate. The alternative
      (check the scorer first) makes the invariant unconditional but re-collapses the
      distinction in exactly the case this stage exists to prevent. Confirm the call.
- [ ] **`spec_hash`'s optional fields are positional and untagged.** `format_requirement`,
      `grounding` and `generation_instructions` are each appended only when present, so a
      `generation_instructions` whose entire text is the word `"synthesis"` hashes identically to
      a synthesis section carrying no instructions. Contrived, and tagging the fields would
      change every existing hash — which is the one thing that function must not do.
- [ ] **`slot_id` and `eligibility` reach the prompt while staying out of `spec_hash`** — the
      same class of defect `group_id` was just fixed for. `slot_id` is load-bearing (the model
      must label each output), so it cannot simply be dropped; `eligibility` probably can.
      `_PROMPT_EXCLUDED_SPEC_FIELDS` in `llm/prompts.py` is where the decision goes.
- [ ] **`spec_hash` (64 hex chars) and `node_id` are still sent to the model** and it can use
      neither. Dropping them was explicitly out of scope for stage 3 — reported, not acted on.
      Together they are roughly 90 characters per spec, ~135 tokens per full 6-spec batch.
- [ ] **`generation_instructions` is unbounded free text, repeated once per spec in a batch.**
      Measured: a 220-char instruction costs +56 estimated tokens at 1 spec and +335 at
      `BATCH_SIZE = 6` (6.0x); at the 20-call cap that is ~6,700 of a 60,000-token budget.
      Bounded today, but linear in instruction length — a ~2,000-char instruction alone would
      approach the whole per-exam cap. **Decide whether to cap its length** (no limit was
      specified, so none was invented) **or hoist it out of the per-spec payload.**
- [ ] **`_estimate_tokens` under-counts what is actually sent — pre-existing, not from this
      stage.** It sums `len(m["content"])` only, so it misses (a) the JSON envelope, ~965
      estimated vs ~1059 wire tokens on a full batch, and (b) the `response_format` JSON schema,
      **~347 tokens on every generation call**. Together roughly a 35–45% under-count. It is
      corrected after the fact by `record_call` from the provider's real `usage.total_tokens`,
      but `check_call` — the actual budget gate — decides on the under-count, so
      `PER_EXAM_TOKEN_CAP` can be overrun by a call it approved.
- [ ] **`--dry-run` does not exercise `build_generation_messages`.** It prints a hand-written
      sample that is not `SYSTEM_PROMPT`, so the gate command verifies the budget path but proves
      nothing about the real prompt. L10 byte-identity is covered by tests instead.
- [ ] **`SYNTHESIS_ITEM_WARN_RATIO = 0.25` is uncalibrated.** A starting value, picked so one
      trace question in a section of four does not cry wolf. Measure it against real papers.

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

---

## P5 — UI + resilience  [PARTIAL — code tests PASS 2026-08-30; pipeline wiring pending]

- [x] `app/preflight.py` — R8 pre-flight check for API key, output dir, WeasyPrint, Qdrant.
- [x] `app/main.py` — FastAPI app skeleton; S7 disclosure gate; degraded mode for
      `BudgetExceeded` and `TimeoutException`; no stack traces in responses.
- [x] `tests/test_p5_app.py` — 14 TDD tests covering preflight, disclosure, degraded mode, chat.
- [ ] Wire `_run_exam_pipeline` to real ingest → allocate → generate → render stages.
- [ ] Wire `_run_chat_query` to real retrieve → rerank → answer_question path.
- [ ] Build static HTML UI: upload form, progress view, download links, chat tab.
- [ ] Browser end-to-end gate: upload → ingest → generate → download → chat.
- [ ] Integration test: network killed mid-generation → degraded JSON shown in browser.
- [ ] Fix `@app.on_event("startup")` deprecation to `lifespan` pattern.
