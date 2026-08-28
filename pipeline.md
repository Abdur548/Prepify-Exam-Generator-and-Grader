# Current Pipeline

_Last updated: 2026-08-28 · phase: P3 (generation + validation)_

## Flow

```
source docs (PDF/PPTX)
  → parse → structure → chunk → embed → index (Qdrant)
                              ↘ course_map.json  (CourseMapNode[])

CourseMapNode[] + Blueprint → Allocation Solver → ItemSpec[] + CoverageReport
```

## Stages

### LLM Client
- **Input:** `list[dict]` messages, optional JSON response schema
- **Output:** `dict` — parsed API response, or `{"dry_run": True, "estimated_tokens": N}`
- **Module:** `coursegen/llm/client.py`
- **LLM calls:** 1 per `client.call()` invocation (0 in dry-run)
- **Key parameters:** `GEMINI_MODEL = gemini-2.0-flash-lite`, `LLM_TIMEOUT_SECONDS = 120`, `MAX_RETRIES = 2`, `PER_EXAM_CALL_CAP = 20`, `PER_EXAM_TOKEN_CAP = 60_000`
- **Status:** implemented

### Allocation Solver
- **Input:** `list[CourseMapNode]`, `Blueprint`
- **Output:** `tuple[list[ItemSpec], CoverageReport]`
- **Module:** `coursegen/exam/allocate.py` + `coursegen/exam/coverage.py`
- **LLM calls:** none
- **Key parameters:** Hare quota apportionment; span uniqueness enforced globally across the whole paper
- **Section solve order:** most-constrained-first — sections carrying `requires_flags_any` are
  solved before unconstrained ones, sorted on `(0 if constrained else 1, original_index)`.
  An unconstrained section apportioned by mass drains spans from the highest-mass nodes, and a
  later flag-filtered section then finds its candidates span-exhausted. `final_default.json`
  has exactly that shape.
- **Section emission order:** ORIGINAL blueprint position, then slot number within the section.
  Solve order and emission order are deliberately different: the rendered paper (P4) must read
  A, B, C regardless of which section the solver served first.
- **Difficulty ladder:** ascending `token_count` within a section. This is a **documented
  heuristic, not a pedagogical guarantee** — a longer section is not reliably a harder one.
- **Cache key:** `ItemSpec.spec_hash` = sha256 over `node_id`, `span_ids`, `item_type`, `bloom`,
  `marks`, `options_count`. `marks` and `options_count` are in the key because the generation
  cache only pays off *across* papers, which is exactly where a 4-mark midterm question and a
  5-mark final question drawn from the same node and span would otherwise collide.
- **Status:** implemented (on fixture; real data in P2)

### Ingest
- **Input:** a directory of `.pdf` / `.pptx` / `.ppt` files
- **Output:** `list[CourseMapNode]` persisted to `course_map.json`, plus dense+sparse
  points in the Qdrant collection
- **Modules:** `ingest/parse.py` → `structure.py` → `chunk.py` → `embed.py` → `index.py`,
  orchestrated by `ingest/coursemap.py`
- **LLM calls:** none, at any stage (C2, §9.1). Key terms come from YAKE, which is CPU-only.
- **Key parameters:** `MAX_CHUNK_TOKENS = 512`, `CHUNK_OVERLAP_PCT = 0.15`,
  `HEADING_STD_FACTOR = 1.5`, `CHARS_PER_TOKEN_ESTIMATE = 4`, `PARSE_TIMEOUT_SECONDS = 60`
- **Heading detection:** relative font-size threshold (`modal + 1.5σ`), never an absolute
  cutoff — a slide-exported PDF has 24 pt body text and an absolute threshold would call
  every block a heading. PPTX uses placeholder types TITLE / CENTER_TITLE.
- **Idempotency (C1):** `chunk_id = uuid5(...)` over content, so a re-ingest upserts by key
  instead of appending. `node_id = sha1(source_file + "|" + "/".join(path))`.
  `COURSE_MAP_FLOAT_PRECISION` + `JSON_SORT_KEYS` make the JSON byte-stable.
- **Per-file timeout (S3):** each file is parsed under a
  `ThreadPoolExecutor.result(timeout=PARSE_TIMEOUT_SECONDS)` watchdog; an overrun is logged
  and the file skipped. `signal.alarm` is Unix-only, and a Python thread cannot be killed, so
  this buys **batch isolation, not preemption** — the abandoned parse keeps running until the
  process exits. `multiprocessing` was rejected: it collides with Qdrant's exclusive file
  lock (S8).
- **Status:** implemented

#### Content flags — how `NodeFlags` is derived, and what each flag is worth

`ingest/parse.py` sets flags per block; `ingest/coursemap.py::_derive_flags` ORs them across a
leaf section's blocks. A section is flagged if **any** of its blocks is, because the flag
answers "can this node anchor a question of that kind?".

| Flag | Status | PDF source | PPTX source |
|---|---|---|---|
| `has_figure` | fact | image block (`get_text("dict")` type 1) | shape type `MSO_SHAPE_TYPE.PICTURE` |
| `has_table` | detection | `page.find_tables()`; a text block whose bbox intersects a detected table's bbox | `shape.has_table` (exact) |
| `has_code` | **heuristic** | span font name matches `MONOSPACE_FONT_SUBSTRINGS` | `run.font.name` matches the same list |
| `has_equation` | **heuristic** | font name in `MATH_FONT_SUBSTRINGS`, **or** ≥ `EQUATION_MIN_MATH_CHARS` characters from `MATH_UNICODE_RANGES` | same two prongs |

This matters beyond completeness: blueprints filter candidate nodes with
`requires_flags_any`, so **a flag that can never be true silently starves a whole exam
section**. `final_default.json` section C requires `["has_figure", "has_equation"]`; while
`has_equation` was hardcoded `False` that section degraded to figure-only without saying so.

Known false modes, stated rather than buried:
- `has_code` sees monospace and nothing else. There is no lexical or syntactic analysis.
  Prose in a monospace face is a false positive; code in a proportional face is a false
  negative.
- `has_equation` cannot see mathematics that was typeset as an **image**, and MVP1 has no
  OCR. `"Symbol"` is deliberately excluded from the math-font list even though it looks like
  an obvious candidate: Word sets its default list bullet in the Symbol face, so including it
  would flag every bulleted deck. Greek, arrows, letterlike symbols and `± × ÷` are excluded
  from the Unicode ranges for the same reason — `config.py` records each exclusion.
- Both font-name prongs are blind on PPTX runs that inherit their face from the layout or
  theme, which is the common case (`run.font.name` is then `None`).
- PPTX table **cell text is extracted** row-major: cells joined by `" | "`, rows by newline, so
  a row's associations survive into the grounding span. Cells covered by a merge are skipped
  (`cell.is_spanned`) — python-pptx returns the merged text on the origin cell and `""` at every
  spanned position, so a naive rows/cells walk emits a stray blank cell per merge. Table text
  counts toward `token_count`, so tables now contribute to `instructional_mass`.

#### `instructional_mass`

```
df_other(t) = number of OTHER source files in which term t was itself extracted
              as a YAKE key term (case-folded exact match)
mean_df_i   = mean of df_other(t) over node i's key_terms      # [] -> 0.0
raw_i       = token_count_i * (1 + ln(1 + mean_df_i))
mass_i      = raw_i / sum(raw)                                 # sums to 1.0
```

`df_other` measures key-term **salience**, not raw text presence: the index is built only
from each node's YAKE `key_terms`, so a term occurring in another document's body but outside
its top-N key terms contributes 0. That is narrower than "number of other source files
containing term t" and is flagged in `todo.md` for the human to confirm. `token_count` is
`len(section_text) // CHARS_PER_TOKEN_ESTIMATE` — a cheap divisor, not a tokenizer; it is only
ever used relatively, so the uniform scaling error cancels.

With a single source document every `df_other` is 0 and mass collapses to normalised
`token_count`. That is the live-demo path, so **this term is invisible in the demo** — do not
tune it by watching the demo.

### Generation
- **Input:** `list[ItemSpec]`, `list[CourseMapNode]`, `span_text_by_id`, LLM client
- **Output:** `list[GeneratedItem]`, cache files keyed by `spec_hash`, `run_manifest.json`
- **Modules:** `exam/generate.py`, `exam/validate.py`, `llm/prompts.py`
- **LLM calls:** batches of `BATCH_SIZE = 6` uncached item specs per call; cached `spec_hash` hits cost zero calls
- **Key parameters:** `MAX_REGENERATION_PASSES = 1`, `GROUNDEDNESS_TAU = 0.45` (raw cross-encoder logit, uncalibrated), `DEDUP_TAU = 0.85`, `OPTION_LENGTH_BAND = 0.40`, `MCQ_SHUFFLE_SEED = 42`
- **Prompt-injection control:** every source span is wrapped in `<source_span id="...">...</source_span>` and the stable system prompt says source spans are data, never instructions (S2)
- **Validation gates:** Pydantic schema parse, groundedness score, duplication cosine, MCQ hygiene. Failures regenerate only their slots, capped at one regeneration pass; remaining failures are shipped flagged in `run_manifest.json`.
- **Gate execution is recorded, not inferred.** `validate_generated_items` returns a per-gate
  `{evaluated, passed, failed, skipped}` record, and the manifest carries it verbatim. The
  groundedness and duplication gates depend on an injected scorer and embedder so the default
  test run stays network-free; when either is absent the gate is marked `skipped`. A bare
  failure count cannot tell "cleared every item" from "never ran" — both read zero, and only
  one of them means the paper was validated.
- **MCQ option shuffling** is seeded per item (`MCQ_SHUFFLE_SEED` mixed with `slot_id`), not
  from the bare constant: one seed for all items gives every question the same permutation, and
  since models tend to emit the correct answer first, the answer then sits in an identical
  position on every question. After shuffling, options are **relabelled by position** and
  `correct_option` is remapped to follow, so the rendered order and the answer key cannot
  disagree however P4 chooses to print them.
- **Reproducibility (R7):** the manifest carries `blueprint_id` as well as `course_map_hash`.
  `ItemSpec[]` is a function of both, so a manifest with only the course-map hash cannot tell a
  midterm run from a final one and the run would not be reproducible from it.
- **Status:** implemented with mocked LLM tests (P3)

### Render + Chat
- **Status:** not started (P4)

### UI
- **Status:** not started (P5)

## Data contracts in use

| Contract | Module | Produced by | Consumed by |
|---|---|---|---|
| `CourseMapNode` | `contracts/course_map.py` | `ingest/coursemap.py` (P1) | `exam/allocate.py` (P2) |
| `NodeFlags` | `contracts/course_map.py` | `ingest/parse.py` blocks → `ingest/coursemap.py::_derive_flags` (P1) | `exam/allocate.py` `requires_flags_any` filter (P2) |
| `Blueprint` | `contracts/blueprint.py` | hand-authored JSON | `exam/allocate.py` (P2) |
| `ItemSpec` | `contracts/item.py` | `exam/allocate.py` (P2) | `exam/generate.py` (P3) |
| `GeneratedItem` | `contracts/item.py` | `exam/generate.py` (P3) | `exam/validate.py`, `exam/render.py` (P4) |
| `CoverageReport` | `contracts/coverage.py` | `exam/coverage.py` (P2) | `exam/render.py` (P4), `app/` (P5) |

### `CoverageReport` — three different ratios, do not confuse them

| Field | Question it answers |
|---|---|
| `nodes_total` / `nodes_covered` / `coverage_ratio` | How much of the **syllabus** was touched. `coverage_ratio = nodes_covered / nodes_total`. |
| `slots_total` / `slots_filled` / `fill_ratio` | How much of the **paper** got filled. `slots_total = sum(section.count)`, `slots_filled = slots_total - len(unfilled_slots)`, `fill_ratio = slots_filled / slots_total` (0.0 when `slots_total == 0`). |
| `slots_by_mass` / `slots_by_fallthrough` / `allocation_fidelity` | How much of the paper was placed **by mass** rather than by exhaustion. `allocation_fidelity = slots_by_mass / slots_filled` (0.0 when `slots_filled == 0`). |

These are three different questions and **they can disagree** — they already do.

`coverage_ratio` counts nodes *touched*, not slots *filled*: a paper missing 4 of 22
questions can still report `coverage_ratio = 1.00`. **`fill_ratio` is the field that proves
the paper is complete.** `slots_filled` always equals the number of `ItemSpec` emitted.

`allocation_fidelity` is the field that proves the paper was *allocated*, not merely
completed. The solver fills a slot down one of exactly two branches in `_solve_section`:

- the chosen node still had positive apportionment deficit → **placed by mass** (`slots_by_mass`);
- the chosen node's deficit was already zero, so `_pick_node` fell through to whichever node
  still had a span left → **placed by exhaustion** (`slots_by_fallthrough`).

`slots_by_mass + slots_by_fallthrough == slots_filled` is asserted in
`exam/coverage.py::build_report`, not merely commented. A slot filled through some third path
would be a real bug and should crash rather than report a plausible number.

**On single-span-per-node corpora, fidelity runs well below 1.0 while coverage and fill can
both read 1.000.** Lecture-slide sections hold far less than `MAX_CHUNK_TOKENS = 512`, so each
leaf section yields exactly one chunk — the real ingest course map is 32 nodes, 32 spans, max 1
span per node. Combined with the hard invariant that no span is used by more than one item in
the entire paper, **each node can host at most one question.** When Hare apportionment says a
high-mass node deserves three slots, two are unsatisfiable and fall through to the next node
with any span left. This is the primary use case and the demo path, not an edge case. Measured
on the real course map:

| Blueprint | `coverage_ratio` | `fill_ratio` | `allocation_fidelity` | by mass | by fallthrough |
|---|---|---|---|---|---|
| `quiz_default` | 0.219 | 1.000 | 0.714 | 5 | 2 |
| `midterm_default` | 0.688 | 1.000 | 0.682 | 15 | 7 |
| `final_default` | **1.000** | **1.000** | **0.625** | 20 | 12 |

`final_default` is the case that motivated the metric: both headline numbers read perfect while
38% of the allocation mechanism did not operate. This is a **spec-level tension, not a bug in
`allocate.py`** — nothing in the solver is wrong, and adding the metric did not change its
behaviour (`solve()` returns byte-identical `ItemSpec[]`). It makes the degradation visible.

All three ratios must be displayed together by the P4 renderer, the P5 UI and the P6 eval
harness. Showing only the first two reproduces exactly the blindness this field exists to
remove.

### `SectionSpec` / `Blueprint` invariants (validated at parse time)

- `bloom` must be non-empty — the solver cycles it with `bloom_list[idx % len(bloom_list)]`.
- `sum(count * marks_each) == total_marks` — otherwise a hand-authored blueprint can print
  "Total: 100 marks" over a 95-mark paper.

### `GeneratedItem` invariants (validated at parse time)

`GeneratedItem` has no `item_type` field, so item-type rules are not expressible here. What is
enforced is internal consistency: `options` and `correct_option` are present together, and
`correct_option` must match one of the `options[].label` values. This is what makes P3's first
validation gate ("Pydantic parse of `GeneratedItem`") actually gate something.

### `CourseMapNode.node_id`

`node_id = sha1(source_file + "|" + "/".join(path))`. `source_file` is part of the hash because
a course ingests several documents and the same heading recurs across them; hashing the heading
path alone merges those into one node with a single wrong `source_file` and `page_span`.
Hashing code lands in `ingest/coursemap.py` at P1.

## Change log

| Date | Commit | Change | Reason | Phase |
|---|---|---|---|---|
| 2026-08-27 | P0 initial | Created all five contracts, `config.py`, `llm/client.py`, living docs | P0 foundation | P0 |
| 2026-08-27 | P2' solver | `exam/allocate.py`, `exam/coverage.py`, 3 blueprints, 20-node fixture | Solver before ingest to de-risk schedule | P2' |
| 2026-08-27 | 9b6bc9c | `CoverageReport` gains `slots_total` / `slots_filled` / `fill_ratio` | `coverage_ratio` reported 1.00 on a paper missing 4 of 22 questions | P2' fix |
| 2026-08-27 | 9b6bc9c | Sections solved most-constrained-first, emitted in blueprint order | Flag-filtered sections were span-starved by unconstrained sections solved earlier | P2' fix |
| 2026-08-27 | 9b6bc9c | `spec_hash` includes `marks` and `options_count` | Cross-paper cache collision between a 4-mark and a 5-mark item on the same node+span | P2' fix |
| 2026-08-27 | 9b6bc9c | `SectionSpec.bloom` min_length=1; `Blueprint` marks-sum validator; `GeneratedItem` options/correct_option validator | Contracts that parsed invalid data cleanly | P2' fix |
| 2026-08-27 | 9b6bc9c | `COURSE_MAP_FLOAT_PRECISION`, `JSON_SORT_KEYS`; unit comments on `RERANKER_THRESHOLD` / `GROUNDEDNESS_TAU` | P1's identical-hash gate needs a rounding + key-order policy; both thresholds are raw logits, not similarities | P2' fix |
| 2026-08-27 | 029b504 | Full ingest: `parse` / `structure` / `chunk` / `embed` / `index` / `coursemap` | P1 | P1 |
| 2026-08-27 | 2922d3f | `has_table` detected, `has_code` + `has_equation` implemented heuristically | Three of four `NodeFlags` were hardcoded `False`, so `final_default.json` section C silently degraded to figure-only | P1 fix |
| 2026-08-27 | 2922d3f | `PARSE_TIMEOUT_SECONDS` actually applied, via a thread watchdog in `parse_directory` | The constant was defined and used nowhere; S3 requires a per-file timeout | P1 fix |
| 2026-08-27 | 2922d3f | `CHARS_PER_TOKEN_ESTIMATE` in config; `MSO_SHAPE_TYPE.PICTURE` replaces `shape_type == 13` | Two magic numbers, one of them feeding `instructional_mass` | P1 fix |
| 2026-08-27 | 2922d3f | `df_other` docstring corrected to describe key-term salience | The doc claimed raw text presence; the code has always measured YAKE key-term salience. Code is truth, doc was wrong | P1 fix |
| 2026-08-27 | 2922d3f | Real-Qdrant point-count test; ingest→solver integration test | The P1 gate's point-count condition and the fixture-vs-reality seam were both untested | P1 fix |
| 2026-08-27 | this commit | PPTX table cell text extracted (merge-aware); table text counts toward `token_count` | A `has_table` node carried no table content to ground a question in; both PPTX fixtures held empty tables, so the gap survived review | P1 follow-up |
| 2026-08-27 | this commit | `df_other` key-term-salience narrowing confirmed as intended | Human decision — the code was right and the plan's prose was the imprecise half | P1 follow-up |
| 2026-08-27 | this commit | `TestParseTimeout` threshold 0.2s → 1.0s | Real parse is 50–96 ms, so ~2× headroom; under suite load the healthy file timed out too and the gate failed intermittently | P1 follow-up |
| 2026-08-28 | uncommitted | `CoverageReport` gains `slots_by_mass` / `slots_by_fallthrough` / `allocation_fidelity`; `build_report` asserts `by_mass + by_fallthrough == slots_filled` | `final_default` reported `coverage_ratio 1.000` and `fill_ratio 1.000` while 38% of its slots were placed by span exhaustion, not by mass. Measurement only — allocation behaviour unchanged, `solve()` byte-identical | P2 instrumentation |
| 2026-08-28 | uncommitted | `exam/generate.py`, `exam/validate.py`, `llm/prompts.py`, mocked P3 tests | Batched schema-constrained generation with cache, validation gates and manifest | P3 |