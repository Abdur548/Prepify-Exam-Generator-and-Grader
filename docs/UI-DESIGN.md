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

Built 2026-09-04 on `POST /api/ingest/start` + `GET /api/ingest/status`. **Polled, not
streamed** — the opposite of the generating screen, and deliberately: a student watches a
51-second generation and does not watch a ten-minute index. Server-held state is the only
kind a reloaded tab can read, so the screen says *"this keeps running if you close the
tab"* and means it.

Three stages, split by cost rather than by the pipeline's nine internal ones:
`Reading your files → Finding the topics → Indexing your material`. The third is nearly
all of the time and has **no sub-progress** — it is one `model.encode(...)` call with no
callback — so it shows a passage count and an estimate scaled from that count, never a bar.

**2 · Blueprint** — TestMacher's Auto Generator, ours. Title, exam type, total marks,
duration; then section rows (`Section A · 10 × MCQ · 2 marks`), then the live counters
`20 QUESTIONS · 100 MARKS · 5 SECTIONS · 180 MIN`. Preview pane shows the empty paper
taking shape as they configure — the paper exists before it is filled.

**3 · Generating** — not a modal spinner. The paper skeleton is on screen from the first
frame, drawn from the dry run the student just read, and the stage line beneath it is
streamed from the run itself over `POST /api/exam/stream`:
`Reading your material → Choosing what to ask → Writing questions → Putting the paper together`.

Two corrections to the original sketch, both from building it (2026-09-03):

- **"Checking sources" was cut.** It would tell a student their questions had been checked
  against the material, which is the claim R6 forbids — nothing establishes that a
  generated question is *true*. The gates that do run check relevance and duplication, and
  they run inside *Writing questions*. The stage is named for what it does: laying out the
  paper and the answer key.
- **Questions do not land one at a time.** The duplication gate compares items against each
  other, so nothing is final until every batch is back — an item shown as written at ten
  seconds can still be rejected at forty. The placeholders therefore pulse together rather
  than in sequence, and the honest per-second signal is the batch counter
  (`12 of 20 questions written`), which is real.

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
| first request ~51 s (~24 s of it the model load) | the generating screen must show *stages*, never a bare spinner. Built: `/api/exam/stream` |
| ingest ~10 min, no progress stream | upload screen owns a long wait: per-file states, an estimate scaled from the passage count, and it must survive a refresh. Built: `/api/ingest/start` + `/api/ingest/status` |
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
4. ~~Generating screen with real stages.~~ **Done** 2026-09-03 — needed a streaming endpoint first.
5. Provenance drag (landing + in-app, one component).
6. ~~Upload with the long wait.~~ **Done** 2026-09-04 — needed ingest to become a job first.
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

---

## 9. Addendum — the logged-in TestMacher app

Walked the real app 2026-09-02. The marketing site showed maybe a third of it. Everything
below is from screens a visitor never sees.

### 9.1 The finding that matters most

The question bank carries a **provenance badge on every card**, and a disclosure above it:

> **Heads-up:** 🤖 **AI** questions are auto-checked and may contain errors — please review
> before using. ✓ **Human** questions have been manually reviewed.

Cards show ⚠ **"Not verified"** in amber. There is also a **"Human-verified only"** checkbox
that *filters the bank* by it.

**They warn because they cannot cite. We cite, so we need not warn.**

That is the sharpest framing of Prepify's advantage I have found, and it comes from a
competitor solving the same honesty problem with the weaker instrument available to them.
Their badge says *this might be wrong*; ours says *this came from page 48 of your own
slides*. One creates doubt, the other resolves it — and both are honest.

The transferable mechanic is the **filter**, not the badge: `☐ Human-verified only` becomes
**`☐ From my material only`**, which hides synthesis items. That is one checkbox and it
solves the synthesis-visibility problem §6 has been carrying.

### 9.2 "Live blueprint" — they use our word

The real Auto Generator has a right-hand panel headed **Live blueprint**, with a
`● updating` pill, three big counters (`QUESTIONS · MARKS · TYPES`), a **`Below target 0/80`**
progress bar, and a **TYPE SPREAD** distribution. Empty state:
*"Toggle on at least one question type to see the blueprint."*

Theirs is arithmetic on counts. **Ours can be a real allocation.** Our solver runs with
**zero LLM calls** and returns the complete assignment — which topics matched, which slots
filled, where material ran out, `fill_ratio`, `allocation_fidelity`, realised Bloom mix.

So Prepify can offer something TestMacher structurally cannot: **a free dry run.** Configure
the paper, watch the real skeleton assemble against your actual uploaded material, see
exactly where your notes are too thin to fill Section C — and only then spend tokens.
"Preview the paper's skeleton for free; spend calls only when you like it."

This should be its own screen (§5.2 absorbs it) and it is the strongest thing in this
document after the provenance drag.

### 9.3 Generator structure, worth copying closely

- **Preset chips** in the top bar: `Quick test · Half-yearly · Annual · Custom`.
  Ours: `Quick · Midterm · Final · Custom` — we already ship those blueprints.
- **Numbered steps with a why-line under each heading**: "Paper details — *What goes on the
  cover sheet*", "Class & subject — *We use this to filter your question bank*". The
  microcopy explains the field's purpose, not its name. Adopt this pattern wholesale.
- **Question mix as toggle rows**: icon, name, one plain sentence, a marks chip, a switch.
  *"Long-answer questions worth 6 marks each."* No jargon anywhere.
- **Difficulty as dot chips**: `● Easy ● Medium ● Hard` → our `cognitive_balance`.
- **Sectioning modes**: `Auto-section by type · Flat list · Manual sections`.
- **`Below target 0/80`** is a constraint-satisfaction readout. Ours is better: it can say
  *"Section C wants 20 marks; your material supports 12."*

### 9.4 Editor chrome

- **Top bar**: back · editable title (serif italic) · live counters `0 QUESTIONS · 0 MARKS ·
  0h EST. TIME` with a progress bar · `Preview · Draft · Submit`.
- **Persistent status bar** along the bottom: marks progress, **keyboard shortcuts shown
  inline** (`Alt+A` add · `Alt+B` bank · `?` all shortcuts), primary action, and
  **`● Auto-saves as you type`** — a *permanent* save indicator, not a toast.
  Take this: our generation is slow, and a permanent state line beats an ephemeral one.
- **Left accordion**: Paper Details / Quick Add / Auto-Generate. Collapsed panels become
  **vertical edge tabs** with rotated labels — good use of narrow space.
- **`▶ Replay tour`** pinned at the bottom of the sidebar. Onboarding you can re-enter.

### 9.5 Question card anatomy

Wrapping chip row, then body:

```
[⚠ Not verified] [🎓 Class 12] [🧪 Computer Science] [🔖 Society Law and Ethics] [👥 User]

Q1. Phishing is a type of:
  a)  Email scam          ✓
  b)  Online shopping
  c)  Web hosting
  d)  Coding language

👁 Show answer
```

Options are full-width rows on a tinted ground; the correct one carries a green check at the
right edge; the answer is behind progressive disclosure. Ours becomes:

```
[📄 03_search.pdf · p.48] [Section A] [2 marks] [MCQ] [Remember]
```

Same shape, and the first chip is a citation instead of a warning.

### 9.6 Papers list

Table: `TITLE · SUBJECT/CLASS · STATUS · UPDATED · ACTIONS`, with a paper **thumbnail** per
row, an amber `Draft` pill, and **relative + absolute timestamps together**
("49 minutes ago / 03 Sep 2026"). Right sidebar: **At a glance** (counts by status) and
**Quick actions**. Filter pills carry counts: `All (1) · Submitted (0) · Drafts (1)`.

One detail worth stealing: a paper with no marks set shows **`— marks`**, not `0 marks`.
Unset and zero are different facts — the same distinction our gate reporting draws between
`skipped` and `failed: 0`.

### 9.7 What we now know not to copy

`Draft → Submitted` is a teacher's workflow: you submit a paper *to* someone. A student
revising has no such handoff. Our states are **`Draft → Ready → Attempted`**, and there are
no class, roster or submission concepts anywhere (see §8.1).
