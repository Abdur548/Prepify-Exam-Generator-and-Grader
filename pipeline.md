# Current Pipeline

_Last updated: 2026-08-30 · phase: P5 partial · Spec Amendment 01 stages 1–3 shipped; stage 1's
topic→node rule replaced with matched IDF mass (stage 4 unblocked)_

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
  `marks`, `options_count`, then `format_requirement`, `grounding` and `generation_instructions`
  **when set**. `marks` and `options_count`
  are in the key because the generation cache only pays off *across* papers, which is exactly
  where a 4-mark midterm question and a 5-mark final question drawn from the same node and span
  would otherwise collide. The last three join it because each changes the prompt — `grounding`
  most fundamentally of all, since it decides whether the item is drawn from the span at all.
  Each is **appended only when present** (`grounding` only when it is not the default `"span"`),
  rather than encoded as `""` the way `options_count` is:
  encoding a default as an empty field would add a trailing separator and change every hash in the
  product, silently invalidating the on-disk generation cache for all three shipped blueprints.
  `group_id` is deliberately **excluded** — it is presentational, so regrouping items under a
  question number must not invalidate a cached generation of the same question. Because it is
  outside the key it is also excluded from the prompt (see Generation).
- **Authored-blueprint structure (Amendment 01 stage 2):**
  - `format_requirement` — the finer generation-facing format (`TRUE_FALSE_SERIES`,
    `ALGORITHMIC_TRACE_PROBLEM`, …), kept separate from `item_type` because `item_type` decides
    which validation gates apply: gate 4 (MCQ hygiene) keys off `item_type == "mcq"`. Validated
    against `config.KNOWN_FORMAT_REQUIREMENTS` and **raises** on an unknown value — a typo that
    fell through to a generic question would leave the blueprint looking honoured while quietly
    not being.
  - `group_id` — sub-questions expand into N `ItemSpec`s sharing an id, rather than one nested
    composite, because one-spec-per-item underpins span uniqueness, the cache, and all four
    gates.
  - `bloom_mix` — proportional Bloom within a section, apportioned by the same largest-remainder
    function used for nodes. Absent, the previous even round-robin applies. Levels are assigned
    **grouped, in `section.bloom` order**, so the result does not depend on JSON key order.
    Note the inherited tie-break: two levels with equal proportions are separated
    alphabetically by level name, not by `section.bloom` order.
  - `cognitive_balance` — an exam-level *declared* target. `CoverageReport` carries the
    **realised** distribution beside it and warns past `COGNITIVE_BALANCE_TOLERANCE`. A warning,
    never a raise: an under-filled paper legitimately misses its target, and that is information.
    It cannot be a parse-time validator because the realised mix is an allocation outcome.
    This is the only signal that would catch a paper drifting to easy recall questions while the
    blueprint asked for 70% apply/analyse — every other metric would call that paper fine.
- **Authored-blueprint generation control (Amendment 01 stage 3):** the solver also carries
  `grounding` and `generation_instructions` from `SectionSpec` to every `ItemSpec` it emits, and
  both join `spec_hash` on the rules above. Neither changes how a slot is *allocated* — they
  change what the model is asked once the slot is filled. See **Generation** for what they do.
- **Status:** implemented (on fixture; real data in P2)

### Ingest
- **Input:** a directory of `.pdf` / `.pptx` / `.docx` files. `.ppt` is **scanned but never
  parsed**: legacy PowerPoint is a binary format, not OOXML, so python-pptx cannot read one
  at all. It stays in the scanned set so a legacy deck never disappears from the corpus
  silently; `parse_file` rejects it by name with the remedy ("convert it to .pptx and
  re-run") and `parse_directory`'s per-file `try/except` carries that message to the log
  while the batch continues (R3).
- **Output:** `list[CourseMapNode]` persisted to `course_map.json`, plus dense+sparse
  points in the Qdrant collection
- **Modules:** `ingest/parse.py` → `structure.py` → `chunk.py` → `embed.py` → `index.py`,
  orchestrated by `ingest/coursemap.py`
- **LLM calls:** none, at any stage (C2, §9.1). Key terms come from YAKE, which is CPU-only.
- **Key parameters:** `MAX_CHUNK_TOKENS = 512`, `CHUNK_OVERLAP_PCT = 0.15`,
  `HEADING_STD_FACTOR = 1.5`, `CHARS_PER_TOKEN_ESTIMATE = 4`, `PARSE_TIMEOUT_SECONDS = 60`,
  `DOCX_HEADING_STYLE_PREFIX = "Heading"`
- **Heading detection — two paths, not one.** PDF is *inferred*: a relative font-size
  threshold (`modal + 1.5σ`), never an absolute cutoff — a slide-exported PDF has 24 pt body
  text and an absolute threshold would call every block a heading. PPTX and DOCX are
  *explicit*: the format itself declares the heading (PPTX placeholder TITLE / CENTER_TITLE;
  DOCX `paragraph.style.name` starting with `DOCX_HEADING_STYLE_PREFIX`, which covers
  "Heading 1"…"Heading 9"). `TextBlock.is_explicit_heading` carries that declaration —
  renamed from `is_slide_heading` when DOCX became the second format to use it. DOCX heading
  paths are flat, one entry per section, exactly like PPTX slide titles; style names do carry
  a level and could nest, which is logged in `todo.md` rather than assumed.
- **Idempotency (C1):** `chunk_id = uuid5(...)` over content, so a re-ingest upserts by key
  instead of appending. `node_id = sha1(source_file + "|" + "/".join(path))`.
  `COURSE_MAP_FLOAT_PRECISION` + `JSON_SORT_KEYS` make the JSON byte-stable.
- **Chunk page precision:** `chunk.page` is the page of the **first block that contributed
  characters to that chunk**, not the section's `page_start`. A chunk drawn from page 7 of a
  section spanning 5–9 used to be cited as page 5. The joining space between two blocks
  belongs to neither and never decides the page; a chunk that overlaps no block raises rather
  than guessing. `page` is hashed into `chunk_id`, so this **changed every chunk ID whose
  page moved** — see the re-ingest note in `progress.md`.
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

| Flag | Status | PDF source | PPTX source | DOCX source |
|---|---|---|---|---|
| `has_figure` | fact | image block (`get_text("dict")` type 1) | shape type `MSO_SHAPE_TYPE.PICTURE` | `document.inline_shapes`, placed at the paragraph that contains each shape |
| `has_table` | detection | `page.find_tables()`; a text block whose bbox intersects a detected table's bbox | `shape.has_table` (exact) | a `w:tbl` body element (exact) |
| `has_code` | **heuristic** | span font name matches `MONOSPACE_FONT_SUBSTRINGS` | `run.font.name` matches the same list | `run.font.name` matches the same list |
| `has_equation` | **heuristic** | font name in `MATH_FONT_SUBSTRINGS`, **or** ≥ `EQUATION_MIN_MATH_CHARS` characters from `MATH_UNICODE_RANGES` | same two prongs | same two prongs |

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
- DOCX table cell text is extracted the same way, with the same two separators, but the merge
  handling is **inverted**: python-docx resolves every spanned position to the *same* `w:tc`
  element and repeats its text there, where python-pptx returns `""`. Cells are therefore
  de-duplicated by `w:tc` identity across the table, covering horizontal and vertical merges.
- A DOCX inline shape anchored somewhere the body walk does not visit (a header, a footnote, a
  table cell) cannot be placed as a figure block. The parser cross-checks the number placed
  against `len(document.inline_shapes)` and records a warning on any mismatch rather than
  under-reporting `has_figure` silently.

#### ⚠ DOCX `page` is a block ordinal, not a printed page number

**A `.docx` has no page numbers.** Pagination does not exist until Word lays the document out
against a printer, the style definitions and the installed font metrics; python-docx cannot
compute it and neither can Prepify. `TextBlock.page` is nonetheless an `int` that flows into
`chunk.page` and from there into the user-visible citation.

For DOCX, `page` is therefore the **1-based ordinal of the block within the document**. A DOCX
citation reading "page 12" means **the 12th block** — the 12th paragraph, table or image — and
not the twelfth printed page. PDF pages and PPTX slides are unaffected and remain real
0-indexed page/slide numbers.

This is a **disclosed limitation, not an approximation waiting to be tightened**. Counting
explicit page breaks was considered and rejected: most real documents contain none, so such a
count would report "page 1" for a forty-page document, and a plausible-looking wrong page
number in a citation is worse than an honestly-labelled ordinal. The fix belongs in the
renderer — label a DOCX locator `¶12` rather than `p.12` — which needs the source type
available at citation time. Logged in `todo.md` for P4/P5.

Consequence for `MAX_PAGES`: it is deliberately **not** applied to DOCX. There are no pages to
count, and capping the block count at 2 000 would both assert the block==page equivalence this
section exists to deny and reject a legitimate hundred-page document. The S3 guards that do
apply to DOCX are `MAX_FILE_SIZE_BYTES`, `MAX_DECOMPRESSED_SIZE_BYTES` (shared OOXML zip-bomb
check) and the per-file `PARSE_TIMEOUT_SECONDS` watchdog.

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
  `{evaluated, passed, failed, skipped, not_applicable}` record, and the manifest carries it
  verbatim. The groundedness and duplication gates depend on an injected scorer and embedder so
  the default test run stays network-free; when either is absent the gate is marked `skipped`. A
  bare failure count cannot tell "cleared every item" from "never ran" — both read zero, and only
  one of them means the paper was validated.
- **`skipped` and `not_applicable` are different facts, deliberately kept apart.** `skipped` is a
  bool about the GATE — no scorer was injected, so it never ran. `not_applicable` is an int about
  ITEMS — how many the gate legitimately does not apply to. For a gate that ran,
  `evaluated + not_applicable == items that reached it`. "We could not check" and "there is
  nothing to check against" are not the same thing, and collapsing them would hide the second,
  which is the more important one.
- **Grounding mode (`ItemSpec.grounding`, Amendment 01 §6.4).** `"span"` is the default and the
  existing product: the item is written FROM its span and gate 2 scores the answer against it.
  `"synthesis"` means the model INVENTS the artifact — a novel game tree, a novel word problem —
  with the span as context. Gate 2 records a synthesis item as `not_applicable`: it must not fail
  it (the blueprint asked for exactly this) and must not pass it either (nothing was checked).
  The check sits **before** the scorer-injection branch, because whether an item is groundable is
  a property of the item, not of what the caller happened to inject.
- **The paper says how many of its questions are ungrounded.** `run_manifest.json` carries
  `grounding: {synthesis_items, items_total, synthesis_ratio}` on **every** run, at whatever
  value, plus a `warnings` list that fires past `SYNTHESIS_ITEM_WARN_RATIO = 0.25` (a starting
  value, not a measured one). A synthesis item is a question not backed by the student's own
  material — a student cannot revise a novel game tree from their own slides — so the count is
  made visible exactly as `fill_ratio` and `allocation_fidelity` made earlier degradations
  visible. Counted over the SPECS, not the surviving items: "how much of this paper was invented"
  is a property of what the blueprint asked for, not of what passed validation. **The renderer
  does not yet mark synthesis items for the student (todo.md).**
- **`generation_instructions` goes in the USER message, never the system prompt.** L10 requires
  `SYSTEM_PROMPT` stay byte-identical across calls so provider-side prompt caching applies;
  templating per-section text into it would defeat that on every single call. It rides in the
  per-spec payload because it is per-SECTION and one batch can mix sections. **S2:** this is
  author-supplied text arriving in the prompt as *instructions* rather than as delimited data —
  safe only while blueprints are authored by the project (todo.md).
- **`group_id` is excluded from the prompt** (`_PROMPT_EXCLUDED_SPEC_FIELDS` in `llm/prompts.py`).
  It is presentational, and it is deliberately excluded from `spec_hash`, so leaving it in let the
  prompt text vary while the cache key did not — one cache entry serving two different prompts.
  Any future field excluded from `spec_hash` belongs in that set for the same reason.
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
- **Input:** `list[GeneratedItem]`, `CoverageReport`, and chat query/history
- **Output:** exam HTML/PDF, answer-key HTML/PDF, coverage HTML; `ChatAnswer` with citations or not-from-material marker
- **Modules:** `exam/render.py`, `retrieve/hybrid.py`, `retrieve/rerank.py`, `chat/answer.py`
- **LLM calls:** chat makes one LLM call per answer; retrieval/skip is decided by reranker score, never an LLM router
- **Key parameters:** `RETRIEVE_TOP_K = 10`, `RERANK_TOP_K = 5`, `SEND_TOP_K = 4`, `CHAT_MAX_TURNS = 6`, `RERANKER_THRESHOLD = 0.5` (raw cross-encoder logit, still uncalibrated)
- **Security:** Jinja2 autoescape enabled; model output is never marked safe. Source contexts in chat prompts are delimited as data.
- **Citations cover every context that was sent**, not only the top-ranked one. The model
  answers from all `SEND_TOP_K` chunks, so citing one names a source the claim may not have
  come from — a citation that reads as authoritative and is wrong. `_unique_citations`
  collapses repeats by `(file, page)`, so the rendered list is usually shorter than
  `SEND_TOP_K`.
- **Status:** partial (HTML/render/chat code implemented and tested; real PDF artifact gate blocked by missing WeasyPrint GTK/Pango native runtime; reranker threshold still needs measured calibration)

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
| `TopicCoverage` | `contracts/coverage.py` | `exam/allocate.py` per authored `SectionSpec.topic` (Amendment 01) | `CoverageReport.per_topic` — empty on the derived path |

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

#### `allocation_fidelity` answers a NARROWER question under an authored blueprint

Spec Amendment 01 (option C, signed 2026-08-28) lets a blueprint section carry a free-text
`topic`. When it does, **the human set the across-topic weights by hand** — those marks were not
placed by `instructional_mass` and are not "placed by mass" in the sense the ratio was defined
in. `allocation_fidelity` then measures **within-topic apportionment only**: of the slots a
topic's own matched nodes received, how many were placed because mass asked for them rather
than because the mass-preferred node had no span left.

Read across a whole authored paper it is a **weighted average of per-topic fidelities**, not a
statement about the paper's topic mix. The product claim narrows with it: under an authored
blueprint it is *"coverage within an authored structure is proved"*, not *"coverage is derived"*.

`topic` is optional and its absence is the derived path — the three shipped blueprints carry no
topics, `per_topic` is empty for them, and `allocation_fidelity` means exactly what it always
meant. **Which question the number is answering is therefore read off `per_topic`: empty means
the old question, non-empty means the narrower one.** It must not be compared across the two
modes without saying so.

#### `per_topic` — what the student's own material could actually answer

| Field | Meaning |
|---|---|
| `topic` / `section_id` | The authored topic and the section that asked for it. |
| `matched_node_ids` / `matched_node_count` | The candidate nodes it selected, after **both** the flag filter and the two topic floors. Ascending `node_id`. |
| `best_score` | Highest **matched IDF mass** among the admitted nodes; `0.0` when none were admitted. **Not a 0–1 fraction — see the scale warning below.** |
| `slots_requested` / `slots_filled` | What the blueprint asked for, and what the material could actually support. |

A row with **`matched_node_count == 0` and `slots_filled == 0` is the most important output of
the whole mechanism**: the paper asked for marks on something the uploaded documents do not
cover, and those slots are deliberately left empty. There is no fallback to the unfiltered
course map — refilling from elsewhere would produce a complete-looking paper about material the
topic never asked for, which is the exact failure option C exists to prevent.

`best_score` is there because **a weak match is more dangerous than no match**: a topic admitted
on thin evidence silently draws from the wrong nodes, and without the number nothing
distinguishes it from a strong match. A near-miss *below* the floors is not visible here — it
reports as `best_score = 0.0`, same as a topic with no overlap at all (todo.md).

> **⚠ SCALE CHANGE, 2026-08-30.** `best_score` used to be a fraction in `[0, 1]`. It is now
> **matched IDF mass**: unbounded, growing with topic length, and dependent on the node count.
> A `4.35` today is not "worse than" a `1.00` yesterday — it is a different quantity. It ranks
> candidates *within one section on one course map* and must not be compared across either.

#### Matching rule — matched IDF mass (`exam/allocate.py::_topic_scores`)

No new module, no new dependency (`math.log` is stdlib). Tokenisation is unchanged: case-fold,
split on non-alphanumeric, drop tokens shorter than `TOPIC_MATCH_MIN_TOKEN_LEN = 3` (the
threshold does the work a stopword list would — the pinned stack has none); a node's tokens come
from its `path` + `key_terms`.

```
df(t)  = number of course-map nodes whose (path + key_terms) tokens contain t
idf(t) = ln((N + 1) / (df(t) + 1)) + 1          # N = node count; smoothed, always > 0
mass(topic, node) = Σ idf(t) for t in (topic_tokens ∩ node_tokens)
```

**Why there is no topic-length denominator.** The rule this replaced was
`|topic ∩ node| / |topic|`, and it **punished specificity**. Measured on the fixture course map:

```
"Search"                                              1.000  → matched
"Uninformed and Informed Search (BFS, DFS, A*, ...)"  0.286  → matched NOTHING
```

A richer, more precise phrasing of the *same* topic scored 3.5× worse than the bare word,
because every enumerated term the node happened not to contain sat in the denominator and
diluted the score — and **every topic in `template_ai_fundamentals_v1` is a long parenthetical
string of exactly this shape**, so essentially none of them would have matched. IDF-weighting
the same fraction was tried and rejected on measurement: `0.286 → 0.262`, slightly *worse*,
because the enumerated terms are rare and are therefore weighted *up* while unmatched. The
denominator was the problem, not the weighting. Under the new rule the same two topics score
**3.351** and **6.703** — extra enumerated terms can only *add* evidence.

A topic that tokenises to nothing still **raises** — it cannot discriminate anything, and
treating it as a match on everything would hand the section the whole course map while looking
like a successful topic match.

**Admission takes two conditions and needs both:**

```
best = max mass over the section's candidate nodes      (the FLAG SURVIVORS, not the whole map)
if best <= 0:                       no match at all
admit node  iff  mass(node) >= TOPIC_MATCH_RELATIVE_FLOOR * best
            and  best      >= TOPIC_MATCH_MIN_EVIDENCE
```

- **Relative floor** (`0.5`) because raw IDF mass scales with corpus size — `idf` depends on `N`,
  so an absolute-only threshold calibrated on a 20-node fixture would drift on a 200-node
  course. Ranking against the best match is scale-free.
- **Absolute evidence floor** (`1.5`) because a purely relative rule always admits the best node,
  however weak: `mass >= 0.5 * best` is trivially true for the argmax. This is the guard against
  *"best of a bad lot"*, and it is the only thing between a topic the upload does not cover and a
  section quietly filled from the wrong nodes.
- `best <= 0` stays its **own branch** rather than being folded into the evidence check. It has
  to: if `TOPIC_MATCH_MIN_EVIDENCE` were ever calibrated down to `0`, then `0 >= 0.5 × 0` holds
  and a relative-only rule would admit the *entire* candidate set on no evidence at all.

Both constants are **UNCALIBRATED** — reasoned, not measured — and must be set against a real
course deck (todo.md). Two things the fixture already shows, recorded there: the evidence floor
**never fires** on it (its most common token still scores 2.099, above 1.5), and a rare 3-letter
connective such as `"and"` is weighted *up* to 3.351 and can admit a node on its own.

### `SectionSpec` / `Blueprint` invariants (validated at parse time)

- `bloom` must be non-empty — the solver cycles it with `bloom_list[idx % len(bloom_list)]`.
- `sum(count * marks_each) == total_marks` — otherwise a hand-authored blueprint can print
  "Total: 100 marks" over a 95-mark paper.
- `grounding` is a `Literal["span", "synthesis"]`, defaulting to `"span"`, on both `SectionSpec`
  and `ItemSpec`. A `Literal` rather than a config set (unlike `KNOWN_FORMAT_REQUIREMENTS`):
  these are not a growing vocabulary but a branch `validate.py` switches on, so a third mode
  needs code, not data. An unrecognised value must not parse into a paper that looks honoured.

### What joins `spec_hash`, and what must not

`spec_hash = sha256(node_id | span_ids | item_type | bloom | marks | options_count`
`[| format_requirement] [| grounding] [| generation_instructions])`.

The three bracketed fields are appended **only when set** — `grounding` only when it is not the
default `"span"` — never encoded as `""` the way `options_count` is. Encoding a default would
append a trailing separator, change every hash in the product, and silently invalidate the
on-disk generation cache for all three shipped blueprints. They join the key because each
changes the prompt, and `grounding` most of all: it decides whether the item is drawn from the
span at all.

`slot_id`, `eligibility` and `group_id` are **not** in the key. `group_id` is therefore also
excluded from the prompt (above); `slot_id` and `eligibility` still reach it, which is a
pre-existing prompt-varies-while-key-does-not seam recorded in todo.md.

Ordering note: because each optional field is appended positionally and only when present, a
free-text `generation_instructions` equal to `"synthesis"` would hash like a synthesis section.
Recorded rather than defended against — a positional-tag encoding would change every existing
hash, which is the one thing this function must not do.

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
| 2026-08-28 | uncommitted | `exam/render.py`, `retrieve/hybrid.py`, `retrieve/rerank.py`, `chat/answer.py`, P4 tests | HTML render, coverage table, hybrid retrieval/rerank and chat path; PDF gate blocked by missing GTK | P4 partial |
| 2026-08-28 | uncommitted | `.ppt` rejected by name in `parse_file` with the conversion remedy, still scanned | It was advertised as supported but python-pptx cannot read a binary OLE2 file; the opaque zip error read as a corrupt document rather than an unsupported format | Ingest fix |
| 2026-08-28 | uncommitted | `.docx` ingestion (`_parse_docx`), `python-docx` dependency, `DOCX_HEADING_STYLE_PREFIX` | Third input format; headings come from real paragraph styles, so they are exact rather than inferred | Ingest fix |
| 2026-08-28 | uncommitted | `TextBlock.is_slide_heading` → `is_explicit_heading` | A second format now uses the flag; the name said "slide" while DOCX paragraph styles set it too | Ingest fix |
| 2026-08-28 | uncommitted | `chunk.page` = page of the first block contributing to that chunk, not `section.page_start` | A chunk drawn from page 7 of a 5–9 section was cited as page 5. **Changes chunk IDs / Qdrant point IDs — a prior index is stale and must be re-ingested** | Ingest fix |
| 2026-08-29 | uncommitted | `SectionSpec.topic` (optional), topic→node matching in `exam/allocate.py`, topic filter beside the flag filter, `TopicCoverage` + `CoverageReport.per_topic`, `TOPIC_MATCH_MIN_TOKEN_LEN` / `TOPIC_MATCH_MIN_SCORE` | Spec Amendment 01 option C, signed 2026-08-28: an authored blueprint sets across-topic weights by hand while the solver still allocates within topic from the student's own material, and says so loudly when the material does not cover a topic. `topic is None` **is** the derived path — `solve()` byte-identical on all three shipped blueprints, all 213 prior tests unmodified | Amendment 01 stage 1 |
| 2026-08-30 | uncommitted | `SectionSpec`/`ItemSpec` gain `grounding` (`span`\|`synthesis`) and `generation_instructions`; both join `spec_hash` **only when non-default**; gate 2 records synthesis items as `not_applicable` rather than passing or failing them; `run_manifest.json` gains `grounding.{synthesis_items, items_total, synthesis_ratio}` + `warnings` past `SYNTHESIS_ITEM_WARN_RATIO = 0.25`; `group_id` removed from the LLM prompt | Amendment 01 §6.4/§6.6. Some exam items legitimately require synthesis (a novel game tree), and gate 2 cannot score what has no source span — but an ungrounded question is not studiable from the upload, so the COUNT has to be visible rather than inferable. `group_id` was excluded from `spec_hash` yet reaching the prompt, so prompt text could vary while the cache key did not. `solve()` byte-identical on all three shipped blueprints, all 314 prior tests unmodified | Amendment 01 stage 3 |
| 2026-08-30 | uncommitted | Topic→node score replaced: `\|topic ∩ node\| / \|topic\|` → **matched IDF mass** `Σ idf(t)` over the shared tokens, `idf(t) = ln((N+1)/(df(t)+1)) + 1`. `TOPIC_MATCH_MIN_SCORE` removed; admission becomes `TOPIC_MATCH_RELATIVE_FLOOR = 0.5` of the section's best match **and** `TOPIC_MATCH_MIN_EVIDENCE = 1.5` absolute. `TopicCoverage.best_score` changes SCALE (unbounded IDF mass, not a 0–1 fraction) | The old rule **punished specificity**: on the fixture `"Search"` scored 1.000 and matched while `"Uninformed and Informed Search (BFS, DFS, A*, Heuristics)"` — the same topic, phrased precisely — scored 0.286 and matched nothing, because every enumerated term the node lacked sat in the denominator. Every topic in `template_ai_fundamentals_v1` has that shape, so stage 4 could not work. IDF-weighting the fraction was measured and rejected (0.286 → 0.262, worse). New scores: 3.351 and 6.703. Relative floor because idf scales with `N`; absolute floor because a relative rule always admits the argmax. **No fallback preserved** — an unmatched topic still leaves its slots unfilled. `solve()` byte-identical on all three shipped blueprints (`ItemSpec[]`, `spec_hash` and `CoverageReport`), verified against a `git archive` of `ebc6c1b` in a separate process | Amendment 01 stage 1 fix |
