# Spec Amendment 01 — Authored, Subject-Specific Blueprints

**Status:** ACCEPTED (option C) and **fully implemented**, 2026-08-30. Kept as the
record of *why* option C was chosen over the alternatives in §4 — that reasoning is not
recoverable from the code, which only shows what was built.
Shipped: `ai_fundamentals_v1.json`, topic→node matching by matched IDF mass in
`exam/allocate.py`, `grounding` and `generation_instructions` on `SectionSpec`,
and marks-weighted `cognitive_balance`. Evidence in `docs/progress.md`.
**Raised:** 2026-08-28, by the human, with `template_ai_fundamentals_v1` as the first instance.
**Affects:** PRD §3, §8.2, §8.3, §9.3, §9.4, §9.5, C5, §16 · plan R6, R7 · phases P2′, P2, P3, P6.

---

## 1. What is being proposed

Replace the generic, derived blueprint with a hand-authored blueprint that names its own
topics and their mark weights. First instance: an Artificial Intelligence final — five tasks
(search, adversarial search, CSP, MDP, RL), 20/20/20/30/10 marks, cognitive balance
0.20 remember-understand / 0.70 apply-analyse / 0.10 evaluate.

The marks arithmetic is correct: 20+20+20+30+10 = 100 = `total_marks`.

**As an exam specification this is good.** It reads like a real AI final. The objection below
is not to its quality — it is that the current machinery cannot execute it, and the parts
that would execute it are the parts that make this product distinctive.

---

## 2. What the PRD currently specifies

> **§3** — Code decides *what* to ask about: topic allocation, mark weighting, item type,
> difficulty, source span. The LLM decides only *how to phrase it* — from a span it is handed.

Topics come from the **uploaded documents' own heading tree** (`CourseMapNode.path`). Weights
come from `instructional_mass`. Questions per topic come from largest-remainder apportionment
over that mass (§9.3). Coverage is therefore *derived* and provable before any token is spent
— §3 calls this "the one claim this product makes".

---

## 3. Impact — six consequences, stated plainly

**3.1 The solver is bypassed.** The proposed blueprint hand-specifies both topics and their
weights, so that *is* the allocation. `exam/allocate.py`, `instructional_mass`, Hare
apportionment, `CoverageReport`, `allocation_fidelity` — all of P2′, P2 and both corrective
passes — become inert. The coverage table would describe a course map that nothing consulted.

**3.2 P6 loses its control arm.** The whole evaluation is solver vs `baseline_naive` on
coverage. With no solver arm there is no comparison, and §16's falsification condition
("if assessors rate blueprint-allocated items lower than the naive baseline's, the coverage
thesis is wrong") becomes untestable.

**3.3 Contracts do not support the new shapes.**

| Blueprint requires | Contract today |
|---|---|
| `TRUE_FALSE_SERIES`, `ALGORITHMIC_TRACE_PROBLEM`, `SCENARIO_MODELING_AND_SOLVING`, `MATHEMATICAL_MODELING`, `ANALYTICAL_SHORT_ANSWER` | `item_type: Literal["mcq","short","long"]` |
| `sub_question_count: 4` | one `ItemSpec` = one item; no sub-parts |
| `generation_instructions` (free text, per task) | prompt built from spec + span |
| `cognitive_balance` as fractions | `bloom: list[str]`, cycled round-robin |

**3.4 Grounding is inverted.** "Generate a novel adversarial game tree", "Generate a novel
word problem" ask the model to **invent** content rather than phrase content it was handed.
This is the opposite of §3.

**3.5 Validation gate 2 breaks on invented items.** Groundedness scores the model answer
*against its source span*. A novel game tree has no source span, so every such item either
fails the gate or the gate is switched off — the silent-skip defect fixed in `d88f3ac`.

**3.6 C5 — subject-agnosticism.** C5 forbids hardcoded topic names "anywhere in the codebase".
A blueprint is *data*, not code, and §8.2 already ships hand-authored blueprints, so the letter
of C5 survives. What does not survive is its intent: the system would no longer work on an
arbitrary uploaded course without someone first authoring a blueprint for that subject.

---

## 4. In fairness to the proposal

For an **algorithmic trace** question, inventing a fresh game tree is pedagogically correct —
an exam should not reuse the tree from the slides. The requirement is sound. It simply does
not fit a grounding model built on the assumption that every item derives from a span of the
student's own material.

Some exam items legitimately require synthesis. The architecture currently has no way to say so.

---

## 5. Options

### A — Adopt fully; retire the solver
Coherent and simple. Discards the differentiator, P2′/P2 and both corrective passes, and makes
P6 meaningless. The product becomes "LLM exam generator with a human-authored table of
specifications" — a legitimate product, but not the one the PRD describes.

### B — Support both blueprint modes side by side
`derived` (current) and `authored` (proposed). Keeps P6 intact. Costs two generation paths and
two sets of tests, and §5 warns against speculative abstraction — though with two real
implementations this would not be speculative.

### C — Map authored topics onto the ingested course map  ← **recommended**

The blueprint sets the **across-topic** weights by hand; the system still does **within-topic**
allocation from the student's own material.

Mechanism:

1. Each task's `topic` string is matched against `CourseMapNode.path` and `key_terms`
   (case-folded token overlap) to select a candidate node set.
2. The solver apportions that task's slots across those nodes by `instructional_mass`, exactly
   as today, with span-uniqueness preserved.
3. `CoverageReport` gains per-topic reporting: for each authored topic, how much material was
   found and how many slots it filled.
4. **If a topic matches no nodes, the report says so loudly** and the slots go unfilled.

**Why this one.** If the student's slides do not cover Markov Decision Processes, the system
tells them — instead of the model inventing 30 marks of material they cannot study from. That
failure mode is worth a great deal in a study tool, and A and B both lose it. It also keeps
every item grounded, keeps gate 2 working, and keeps `CoverageReport`, `fill_ratio` and
`allocation_fidelity` meaningful.

**What is given up:** mass-proportional allocation *across* topics. The human sets that. This
should be stated honestly rather than papered over — under C the product claim becomes
"coverage within an authored structure is proved", not "coverage is derived".

---

## 6. Contract changes required under C

Each needs approval before code (plan §1.1.3, §8).

1. `SectionSpec.item_type` — widen beyond `mcq|short|long`. Decide whether the new formats are
   item types or a separate `format_requirement` field.
2. **Sub-questions** — a task with `sub_question_count: 4` worth 20 marks total is one rendered
   question with four parts. Either `ItemSpec` gains sub-parts, or a task expands into N
   `ItemSpec`s that the renderer groups. The second is the smaller change.
3. `topic: str` on the section/task, plus the matching rule in §5 above.
4. `grounding: "span" | "synthesis"` per task.
   - `span` — current behaviour; gate 2 applies.
   - `synthesis` — the model invents (a novel tree, a novel word problem); gate 2 is recorded
     as **skipped for that item**, using the mechanism added in `d88f3ac`, and the item is
     marked as such in the paper and the manifest.
5. `cognitive_balance` — proportional Bloom mix, apportioned by the same largest-remainder
   function used for nodes. (Already flagged as needed independently of this amendment.)
6. `generation_instructions` — per-task free text. Must go in the **user** message, never the
   system prompt: L10 requires the system prompt stay byte-identical across calls for
   provider-side caching.

---

## 7. Risks to record

- **Synthesis items are not studiable from the upload.** A student cannot revise a novel game
  tree from their own slides. This should be visible in the UI, not buried.
- **Prompt-injection surface grows.** `generation_instructions` is author-supplied text that
  reaches the prompt as instructions rather than data. Safe while blueprints are authored by
  the project; it becomes an S2 concern the moment users can supply them.
- **Topic matching is a new failure mode.** A topic that half-matches is worse than one that
  does not match at all, because it silently draws from the wrong nodes. The match rule needs a
  confidence floor and must report what it matched.
- **`allocation_fidelity` changes meaning** under C: it would measure within-topic apportionment
  only. Document that, or the number silently answers a different question than it does today.

---

## 8. Decision — SIGNED 2026-08-28

- [x] **Option chosen: C** — authored topics mapped onto the ingested course map.
- [x] **Contract changes approved:** §6.1–§6.6, with the two sub-decisions below settled by the
      reviewer and open to override.
- [x] **The derived path survives, at no cost.** All three shipped blueprints
      (`quiz_default`, `midterm_default`, `final_default`) contain **zero** `topic` fields.
      So `topic` is optional on a section, and its absence *is* the derived path: no topic
      filter, candidates are the whole course map, allocation is mass-proportional across
      everything, exactly as today. One code path serves both. **P6's solver-vs-baseline
      comparison is therefore intact and §16's falsification condition still runs** — it just
      runs on the topic-free blueprints.

### 8.1 Sub-decision — new formats go in a separate field, not into `item_type`

`item_type` stays `mcq | short | long`. A new `format_requirement` field carries
`TRUE_FALSE_SERIES`, `ALGORITHMIC_TRACE_PROBLEM`, and the rest.

Reason: `item_type` drives two things that must not be confused with prompting —
renderer layout, and **which validation gates apply**. Gate 4 (MCQ hygiene: option-length band,
no "all of the above", single spelling variant) keys off `item_type == "mcq"`. Folding
`TRUE_FALSE_SERIES` into `item_type` would either subject it to MCQ rules that do not fit or
force gate 4 to learn every new format. Keeping them separate means a true/false item is
`item_type="mcq"`, `options_count=2`, `format_requirement="TRUE_FALSE_SERIES"` — hygiene still
applies, and the prompt gets the finer instruction. `format_requirement` joins `spec_hash`.

### 8.2 Sub-decision — sub-questions expand into N `ItemSpec`s with a shared group id

A task with `sub_question_count: 4` becomes four `ItemSpec`s carrying the same `group_id`;
the renderer groups them under one question number.

Reason: everything downstream assumes **one `ItemSpec` = one item** — span uniqueness, the
`spec_hash` cache, per-item validation, the four gates, `slots_filled`, `allocation_fidelity`.
Nesting sub-parts inside a single `ItemSpec` would make the gates operate on composites and
leave span-uniqueness ambiguous. Expansion needs one new field and changes no invariant.

---

## 9. Staging

Built in four reviewable stages rather than one change, mirroring the project's own phase
discipline. Each stage ends green with its tests.

| Stage | Content | LLM? |
|---|---|---|
| **1** | `topic` on `SectionSpec`, topic→node matching with a confidence floor, candidate filtering in the solver, per-topic `CoverageReport` | none |
| **2** | `format_requirement`, `group_id` + sub-question expansion, `cognitive_balance` proportional Bloom | none |
| **3** | `grounding: span\|synthesis`, gate-2 skip recorded for synthesis items, `generation_instructions` into the **user** message (never the system prompt — L10) | yes |
| **4** | `template_ai_fundamentals_v1` itself, end-to-end against a real ingested course map | yes |

Stage 1 is the one that proves C works: it is pure allocation, fully testable without a model,
and it either grounds authored topics in the student's own material or reports loudly that it
could not.

---

`template_ai_fundamentals_v1` cannot be loaded until stages 1–3 land — it does not yet validate
against the `Blueprint` contract.
