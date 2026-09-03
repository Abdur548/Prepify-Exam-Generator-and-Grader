# Prepify — frontend design

Drafted 2026-09-02 after driving TestMacher, Scholarly and Papersetter.

---

## 1. What the three references actually taught us

### TestMacher — the structural base

Teacher-facing question-paper builder for Indian schools. What to take:

- **Workspace IA**: `My Papers · Editor · Question Bank · Chapters`, then `TOOLS: Auto-gen · Export`.
  Nouns the user recognises, not system parts.
- **Two-pane editor**: controls left, **live paper preview right, auto-saved**. The paper is
  always visible while you configure it. This is the single most important pattern to keep.
- **The Auto Generator is our blueprint, verbatim**: Paper Details (title, exam type, max
  marks, duration) → Class & Subject → Question Types as toggle chips → live counters
  (`15 QUESTIONS · 40 TOTAL MARKS · 3 TYPES`) → Generate. That is `total_marks`,
  `duration_minutes`, `sections[].item_type`, `sections[].count` with a running total.
- **Paper rendering**: `Q3.` in a left gutter, options in a **two-column grid**, marks `[1]`
  in a **right rail** top-aligned to the question, sections as centred rules
  (`SECTION A — Multiple Choice`).
- **General Instructions block** derived from the blueprint ("Q1–Q18 are 1 mark each…").
  We can generate this from our sections for free, and it is what makes a paper read as real.
- **Honest comparison table** (Word vs TestMacher) as a landing-page section.

What to leave: the question-bank model. They assemble from a pre-built CBSE bank. We
generate from the student's own upload — a different product, and our better one.

Their demo also repeated questions (Q4≡Q9, Q5≡Q10). Our duplication gate exists precisely
for that.

### Scholarly — the ingest flow, and one product fork

- **Tabbed source input**: `Upload · Library · Paste text · Google Drive`, dropzone with
  constraints stated inline (`PDF, Word, PowerPoint… up to 8MB each`), and a **persistent
  quota counter** (`0/3 sources · 1 upload slot left`).
- **The fork**: their step 3 is *Practice & Review* — you sit the quiz in-app, timed.
  TestMacher exports a paper you print. **Prepify must decide, and can do both** (see §5).
- What to leave: the twelve-tool sidebar. Prepify does one thing.

### Papersetter — the signature interaction

A **draggable split** on the hero: one exam question, wiped between two renderings.
Left is the printed paper (school header, `Name / Student ID / Date` rules, serif, LaTeX,
ruled writing lines, `(4 marks)` right-aligned). Right is the online sitting (monospace
countdown, `QUESTION 5 OF 20`, `4 MARKS`, autosaving response box with `● SAVED`, symbol
palette, and a 1–20 **question navigator** colouring answered / flagged / current).

It is live — the timer ticks, the answer types itself in LaTeX. One drag communicates
"paper or online" with no copy at all.

**We should build this interaction and change what it reveals.** See §4.

---

## 2. Design thesis

> **Every question on this paper came from a page you uploaded. Drag to see which one.**

Papersetter's split dramatises *delivery*. Ours should dramatise **provenance**, because
that is the thing Prepify has and its competitors do not: `03_search.pdf p.48`, resolving to
real material, on every single item.

The product is confident about its sourcing and modest about its judgement. The design
should feel the same: quiet, precise, evidential. Not a chatbot with a spinner.

---

## 3. Tokens

### Colour — one saturated hue, and it means something

Everything is a cool neutral except one accent, and **that accent is only ever used to mark
provenance**. Not for primary buttons, not for links, not for decoration.

| token | hex | role |
|---|---|---|
| `--paper` | `#FBFBFA` | the exam sheet surface |
| `--ink` | `#16181D` | body and question text |
| `--graphite` | `#5B6270` | secondary text, labels, marks rail |
| `--rule` | `#DFE1E6` | hairlines, section rules, borders |
| `--slate` | `#232936` | app chrome, sidebar, primary buttons |
| `--trace` | `#B8E62E` | **provenance only** — cited line, source badge, "from your material" |

`--trace` is a highlighter yellow-green. Deliberately not a brand colour: it is a semantic.
When a student sees it, it means *this came from your upload*.

**The corollary is the good part.** Synthesis items — the ones deliberately not answerable
from the student's material — simply have **no trace colour**. Their plainness is the
signal. We do not need a warning badge; the absence of the highlight is the honest mark,
and it is impossible to misread as an error.

Anti-default check: the three houses AI design keeps landing in are cream+serif+terracotta,
near-black+acid-green, and broadsheet hairlines. Cream is out. The acid accent here is used
under a strict semantic rule rather than as a general accent, and the ground is cool paper
rather than near-black, which keeps it away from the second cluster.

### Type — three faces, each meaning a world

This is the part to get right, because it does structural work rather than decorative.

| role | face | used for |
|---|---|---|
| **the paper** | a genuine academic serif — *Source Serif 4* or *Spectral* | anything that is part of the exam: stems, options, section headers, instructions |
| **the tool** | a neutral grotesque — *Inter* | app chrome, controls, labels, navigation |
| **the evidence** | monospace — *JetBrains Mono* | filenames, page numbers, marks, timers, counts |

A student can tell at a glance whether they are looking at *the exam*, *the app*, or *the
proof*. When a citation reads `03_search.pdf p.48` in mono beside serif question text, the
difference in face is doing the work a label would otherwise have to.

Scale: 13 / 15 / 17 / 21 / 28 / 44. Paper body at 17/1.65 — exam papers are read slowly.

### Layout

Three surfaces, one grid.

```
┌──────────┬────────────────────────────────────┬──────────┐
│ SIDEBAR  │  PAPER (max 760px, centred)        │  MARKS   │
│  56/240  │  the serif world                   │  RAIL 72 │
│  slate   │  paper white                       │          │
└──────────┴────────────────────────────────────┴──────────┘
```

The **marks rail** is the structural device, and it is real data, not decoration: it carries
`[2]`, `[5]`, `[20]` per question from `sections[].marks_each`, and at the top it carries the
running total against `total_marks`. It is the exam-paper vernacular that separates this from
a generic broadsheet layout — an exam paper is not a newspaper, it is a document with a
margin for marks and space for working.

### Signature

**The provenance drag** (§4), plus the trace highlight. Nothing else in the interface is
allowed to be loud.

---

## 4. The signature interaction: drag to source

Same mechanic as Papersetter, different payload.

```
        ← drag                                    drag →
┌───────────────────────────┬───────────────────────────┐
│  THE QUESTION             │  THE SOURCE               │
│                           │                           │
│  Q7.  Which search        │  03_search.pdf · p.48     │
│       strategy expands    │  ┌─────────────────────┐  │
│       the lowest-cost     │  │ • Complete? Yes     │  │
│       node?          [2]  │  │ • Optimal? Yes, if  │  │
│                           │  │   step cost = 1 ▓▓▓ │  │← trace
│  (a) Depth-first          │  │ • Time? O(b^d)      │  │
│  (b) Uniform-cost         │  └─────────────────────┘  │
└───────────────────────────┴───────────────────────────┘
```

Drag right and the question dissolves into the actual slide it was written from, with the
supporting line marked in `--trace`. Release and it springs back.

Why this and not Papersetter's: their split answers *"how will this be delivered?"*. Ours
answers *"why should I believe this question?"* — which is the only question a student
actually has about an AI-generated exam.

**On the landing page** it runs on a loop with a real example. **Inside the app** the same
component is the per-question provenance control, so the marketing gesture and the product
gesture are the same thing — the demo is not a lie.

For a **synthesis** item the right panel is honest instead: *"Not from your material — this
question asks you to build something new."* No trace colour. That is the affordance the
backend has been asking for since the synthesis ratio first fired at 40%.

---

## 5. Screens

**1 · Upload** — dropzone, formats and limits inline (Scholarly's pattern), file list with
per-file parse state. Ingest takes ~10 minutes, so this screen owns the long wait (§6).

**2 · Blueprint** — TestMacher's Auto Generator, ours. Title, exam type, total marks,
duration; then section rows (`Section A · 10 × MCQ · 2 marks`), then the live counters
`20 QUESTIONS · 100 MARKS · 5 SECTIONS · 180 MIN`. Preview pane shows the empty paper
taking shape as they configure — the paper exists before it is filled.

**3 · Generating** — not a modal spinner. The paper skeleton is on screen and questions
land into it one at a time, each arriving with its citation. Stage line beneath:
`Reading your material → Choosing what to ask → Writing questions → Checking sources`.
Those are the real pipeline stages; the progress is honest.

**4 · Paper** — the artifact. Serif, marks rail, section rules, general instructions.
Per question: the trace highlight and the drag-to-source. Header actions: Download PDF,
Download Word, Answer key, Regenerate this question.

**5 · Sit it** *(the Scholarly fork — recommended as phase 2)* — timer, question navigator
with answered/flagged/current states, autosave. Papersetter's right panel is the blueprint
for this and we should not redesign it.

**6 · Ask** — chat over the corpus. Every answer carries its citations. When
`from_material: false`, the answer is rendered **without** trace colour and labelled
*"Answered from general knowledge — not from your uploads."*

---

## 6. Where the backend forces the design

These are measured, not guesses (`docs/FRONTEND-BRIEF.md`).

| reality | design consequence |
|---|---|
| first request ~51 s | the generating screen must show *stages*, never a bare spinner |
| ingest ~10 min, no progress stream | upload screen owns a long wait: per-file states, honest "this takes about ten minutes", and it must survive a refresh |
| one generation at a time (process lock) | disable Generate on submit; a second attempt blocks silently, so the UI must never imply a queue |
| four outcomes: `ok` / `empty` / `degraded` / 500 | four distinct states. `degraded` is a **partial paper you can still use**, not an error — show what came back plus what did not |
| `fill_ratio` < 1 | show which slots are unfilled, in place, in the marks rail |
| `from_material: false` | no trace colour + explicit label |
| synthesis items | no trace colour + "asks you to build something new" |
| `coverage_ratio` | **never render.** It reads 4% on a perfect paper |

### Copy rules

Never *verified*, *accurate*, *grounded*, *fact-checked*.
Say: **"from your material"**, **"the cited page states this"**, **"not in your uploads"**.

Empty state, upload: *"Add your lecture slides and we'll build a paper from them."*
Degraded: *"We built 14 of 20 questions before hitting your daily limit. The paper below is
complete and usable — the missing six are marked."*

---

## 7. Build order

1. Design tokens + the three type roles.
2. **Paper renderer** — serif, marks rail, sections, instructions. Everything else hangs off it.
3. Blueprint form with live counters, two-pane against the paper.
4. Generating screen with real stages.
5. Provenance drag (landing + in-app, one component).
6. Upload with the long wait.
7. Chat with `from_material`.
8. *Phase 2:* sit-the-exam mode.

---

## 8. Open questions

1. ~~Who is the primary user?~~ **DECIDED 2026-09-02: the student revising.** TestMacher is
   teacher-first and we are not copying that. Consequences, since this decision reaches
   almost every screen:
   - The upload is *"your lecture slides"*, not *"your question bank"*. The corpus belongs
     to the person sitting the exam.
   - Provenance is the core feature rather than a footnote: a teacher who wrote the slides
     gains nothing from a citation back to them, a student revising gains everything.
   - Copy is second person and revision-shaped — "practise", "check your answer", "find this
     in your notes" — not "set", "assign" or "class".
   - Sit-the-exam mode (§5.5) moves from *nice-to-have* to the natural phase 2, because a
     student wants to attempt the paper, not print it for someone else.
   - No class/section/roster concepts anywhere. One person, their own material.
2. **Do they sit the exam in-app?** Recommend shipping export-first, sit-mode second.
3. `--trace` at `#B8E62E` needs a contrast pass on paper white before it is final.
