# Current Pipeline

_Last updated: 2026-08-27 · phase: P2' (post-review corrective pass)_

## Flow

```
[fixture] CourseMapNode[] + Blueprint → Allocation Solver → ItemSpec[] + CoverageReport
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
- **Status:** not started (P1)

### Generation
- **Status:** not started (P3)

### Render + Chat
- **Status:** not started (P4)

### UI
- **Status:** not started (P5)

## Data contracts in use

| Contract | Module | Produced by | Consumed by |
|---|---|---|---|
| `CourseMapNode` | `contracts/course_map.py` | `ingest/coursemap.py` (P1) | `exam/allocate.py` (P2) |
| `Blueprint` | `contracts/blueprint.py` | hand-authored JSON | `exam/allocate.py` (P2) |
| `ItemSpec` | `contracts/item.py` | `exam/allocate.py` (P2) | `exam/generate.py` (P3) |
| `GeneratedItem` | `contracts/item.py` | `exam/generate.py` (P3) | `exam/validate.py`, `exam/render.py` (P4) |
| `CoverageReport` | `contracts/coverage.py` | `exam/coverage.py` (P2) | `exam/render.py` (P4), `app/` (P5) |

### `CoverageReport` — two different ratios, do not confuse them

| Field | Meaning |
|---|---|
| `nodes_total` / `nodes_covered` / `coverage_ratio` | How much of the **syllabus** the paper touches. `coverage_ratio = nodes_covered / nodes_total`. |
| `slots_total` / `slots_filled` / `fill_ratio` | How much of the **paper** actually exists. `slots_total = sum(section.count)`, `slots_filled = slots_total - len(unfilled_slots)`, `fill_ratio = slots_filled / slots_total` (0.0 when `slots_total == 0`). |

`coverage_ratio` counts nodes *touched*, not slots *filled*: a paper missing 4 of 22
questions can still report `coverage_ratio = 1.00`. **`fill_ratio` is the field that proves
the paper is complete**, and it is the one the P4 renderer and P5 UI must display alongside
coverage. `slots_filled` always equals the number of `ItemSpec` emitted.

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
| 2026-08-27 | (uncommitted) | `CoverageReport` gains `slots_total` / `slots_filled` / `fill_ratio` | `coverage_ratio` reported 1.00 on a paper missing 4 of 22 questions | P2' fix |
| 2026-08-27 | (uncommitted) | Sections solved most-constrained-first, emitted in blueprint order | Flag-filtered sections were span-starved by unconstrained sections solved earlier | P2' fix |
| 2026-08-27 | (uncommitted) | `spec_hash` includes `marks` and `options_count` | Cross-paper cache collision between a 4-mark and a 5-mark item on the same node+span | P2' fix |
| 2026-08-27 | (uncommitted) | `SectionSpec.bloom` min_length=1; `Blueprint` marks-sum validator; `GeneratedItem` options/correct_option validator | Contracts that parsed invalid data cleanly | P2' fix |
| 2026-08-27 | (uncommitted) | `COURSE_MAP_FLOAT_PRECISION`, `JSON_SORT_KEYS`; unit comments on `RERANKER_THRESHOLD` / `GROUNDEDNESS_TAU` | P1's identical-hash gate needs a rounding + key-order policy; both thresholds are raw logits, not similarities | P2' fix |
