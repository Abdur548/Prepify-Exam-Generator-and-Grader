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

- [ ] **Calibrate `RERANKER_THRESHOLD` against measured cross-encoder logits** — same issue and
      same model as `GROUNDEDNESS_TAU` above. Code supports threshold-based routing, but the
      current value is still uncalibrated and must be measured before P4 PASS.
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

## Discovered mid-phase (do NOT do now)

- `coursegen/contracts/course_map.py` imports `Field` from pydantic without using it. Harmless;
  left alone as out of scope for the corrective pass.

---

## Backlog (post-project)

- [ ] Blueprint fitting from uploaded past paper (PRD §15 — out of MVP1 scope)
- [ ] Late chunking (PRD §15 — out of MVP1 scope)
- [ ] Multi-provider abstraction (PRD §15 — explicitly prohibited)
- [ ] 2-span `long` items — the contract allows 1–2 spans, the solver emits 1
