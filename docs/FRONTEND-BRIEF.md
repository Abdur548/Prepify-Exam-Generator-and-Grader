# Prepify — backend brief for frontend research

Self-contained. Assumes no prior context on this project.

---

## What it is

A student uploads their own course material — lecture slides, PDFs, DOCX — and gets back a
**structured exam paper**: sections, marks, duration, an answer key, and a coverage report.
There is also a **chat** window over the same material.

Audience: university students revising for a specific course, and secondarily the
instructors who own the material.

Not a quiz app. The output is shaped like a real exam paper — Section A/B/C, marks per
question, total marks, duration — because that is what someone revising for an exam wants
to practise against.

## The thesis the product is built on

> **Code decides *what* to ask. The model only writes the words.**

A deterministic solver reads the uploaded corpus, scores every section of it by
`instructional_mass` (roughly: how much teaching weight it carries), and allocates exam
slots across topics before any AI is involved. The LLM is then handed a specific source
passage and a specific question shape, and asked only to phrase it.

**This is measured, not asserted.** Against a baseline that picks passages at random, the
solver covers **2.4–4.0× the instructional mass** on every blueprint tested, beating every
individual random draw.

The consequence for design: **the sourcing is the product.** Every generated question
points at a real file and page in the student's own upload — `03_search.pdf p.48`. Most AI
study tools cannot do this and hide the fact. Here it is the trust surface and should be
treated as a first-class element, not a footnote.

---

## What actually happens, end to end

1. **Ingest** — parse PDFs/PPTX/DOCX → chunk → embed → index → build a "course map" of
   nodes, each with source file, page span, key terms and an instructional-mass score.
   *Takes ~10 minutes for 14 lecture decks. No progress stream today.*
2. **Allocate** — a deterministic solver fills a blueprint's slots from the course map. No
   AI. Produces a spec per question: topic, source span, question type, marks, Bloom level.
3. **Generate** — batched LLM calls turn specs into questions. ~5 questions per call.
4. **Validate** — four gates: schema, relevance, duplication, MCQ hygiene. Failures get one
   retry, then ship flagged.
5. **Verify (optional)** — gate 5 asks whether each question's cited passage actually
   *states* its answer. Off by default; roughly doubles cost.
6. **Render** — exam HTML + PDF, answer key HTML + PDF, coverage report.

---

## API surface

Full contract in `docs/API-CONTRACT.md`. Summary:

| endpoint | purpose |
|---|---|
| `GET /api/preflight` | six health checks; **gate the UI on this** |
| `GET /api/blueprints` | list of exam templates (quiz, midterm, final, authored) |
| `POST /api/disclosure/accept` | consent gate — `/api/exam` returns 403 until called |
| `POST /api/ingest` | multipart upload; **minutes, not seconds** |
| `POST /api/exam` | generate a paper |
| `POST /api/chat` | question answering over the corpus |
| `GET /api/files/{name}` | download the generated artifacts |

### `/api/exam` returns four outcomes, not two

| outcome | HTTP | means |
|---|---|---|
| `status: "ok"` | 200 | a paper was produced |
| `status: "empty"` | 200 | ran successfully, produced **zero** questions |
| `status: "degraded"` | 200 | hit a known limit (quota, network); result is real but partial |
| — | **500** | something unexpected broke; nothing was produced |

**Do not collapse these into success/error.** "We generated a partial paper because your
quota ran out" is genuinely different from "the server broke", and the user can act on the
first.

### Success payload

```json
{
  "status": "ok",
  "fill_ratio": 1.0,
  "allocation_fidelity": 0.95,
  "unfilled_slots": [],
  "items_count": 20,
  "warnings": ["..."],
  "downloads": { "exam_pdf": "/api/files/exam.pdf", "...": "..." }
}
```

`fill_ratio` = questions delivered ÷ questions asked for. This is the completeness number.
`allocation_fidelity` = share of questions placed by instructional mass rather than by
falling back when a topic ran out of material.

### `/api/chat` returns

```json
{ "answer": "...", "citations": [{"file": "03_search.pdf", "page": 48}], "from_material": true }
```

`from_material: false` means the answer was **not** drawn from the student's upload. That
distinction is the whole point of the field and must be visible.

---

## Timing realities that shape the UI

These are measured, not estimates.

- **First request after startup: ~51 seconds.** Models load lazily — 41 s for the reranker,
  10 s for the embedder. A bare spinner reads as a hang.
- **Ingest: ~10 minutes** for 14 decks, with **no progress stream**. The request simply does
  not return for a long time.
- **One generation at a time, process-wide.** A second request *blocks silently* until the
  first finishes. Double-clicking Generate produces a longer wait, not a queue. The control
  must disable on submit.
- Everything else (health, blueprint list, page load) is ~3 ms.

**Waiting is a designed state here, not an edge case.**

---

## Three things the UI must surface that a generic template won't have a slot for

1. **Citations, per question.** File + page, resolving to real uploaded material. This is
   the product's differentiator.
2. **Synthesis questions.** Some blueprints deliberately ask for questions that are *not*
   answerable from the uploaded material — on one real paper this was 8 of 20 questions and
   70 of 100 marks. The backend reports the count. The exam does not yet mark them, and a
   student revising one from their slides will wrongly conclude their notes are incomplete.
3. **`from_material` on chat answers.**

---

## What the copy may never say

**No UI string may describe a question as verified, accurate, grounded, or fact-checked.**

The system checks that questions are well-formed and on-topic. Gate 5 additionally checks
whether the cited passage *states* the answer — a narrower claim, and the strongest one
available. It is not proof of correctness: the verifier shares a model family with the
generator.

Honest phrasings: "from your material", "cited passage states this", "not in your uploads".
Dishonest: "verified", "guaranteed accurate", "fact-checked".

There is one further number that must never be shown: `coverage_ratio`. It reads ~4% on a
complete, perfect paper because it counts nodes against the whole corpus. It is excluded
from the API for that reason. Do not reconstruct it.

---

## Maturity, stated plainly

**Works and is measured:** ingest, allocation, generation, validation, citation accuracy
(20/20 resolving to real pages), the factuality gate, PDF rendering, chat. 477 tests.

**Not established:** whether the generated questions are *pedagogically good*. Nothing in
the system evaluates difficulty, fairness or teaching value. It has been validated on
**one course** — 14 AI lecture decks — and no instructor has yet reviewed a generated paper
for quality.

The design should reflect a tool that is confident about its sourcing and modest about its
judgement.
