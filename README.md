# Prepify

Course Exam Generator and Chatbot. Upload your lecture material; get a structured
exam paper with real citations back, and a chat window over the same corpus.

**Code decides *what* to ask** — which topic, which source span, which Bloom level —
by `instructional_mass`. The model only writes the words. That claim is measured, not
asserted: the solver covers **2.5–6.4× the instructional mass** of a random paper of
identical shape, on all four blueprints. On the authored blueprint it beat every
individual draw across ten seeds, not merely the mean (`docs/P6-EVALUATION.md`).

What that does *not* show: denser content is not the same as a better exam. The
evaluation says so itself.

## Layout

```
prepify/
├── backend/
│   ├── coursegen/          the package
│   │   ├── ingest/         parse → chunk → embed → index → course map
│   │   ├── exam/           allocate → generate → validate → verify → render
│   │   ├── retrieve/       hybrid search + reranking
│   │   ├── chat/           question answering over the corpus
│   │   ├── eval/           allocation + reliability harnesses
│   │   ├── app/            FastAPI service and static UI
│   │   ├── pipeline.py     the one orchestration, shared by API and CLI
│   │   └── generate.py     CLI front end
│   ├── tests/
│   ├── data/               course material + Qdrant index (gitignored)
│   └── output/             generated papers (gitignored)
├── frontend/               React + TypeScript client (Vite)
│   └── src/
│       ├── upload/         upload and ingest progress
│       ├── plan/           blueprint selection and coverage warnings
│       ├── generate/       streamed generation progress
│       ├── paper/          the rendered paper and downloads
│       ├── provenance/     source reveal for an individual question
│       └── chat/           chat over the same corpus
└── docs/
    ├── API-CONTRACT.md                 the frozen HTTP surface the frontend depends on
    ├── P6-EVALUATION.md                measured results, with falsification conditions
    └── Prepify-Jury-Presentation.pptx  the jury presentation, with speaker notes
```

## Running it

Two processes. The API takes an exclusive lock on the Qdrant index, so it must run
single-worker and only one copy at a time.

**Backend**

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env          # then add your GEMINI_API_KEY to it

# Ingest your own material once — replace the path with your folder of
# .pdf / .pptx / .docx. Skip this if data/course_map.json already exists.
python -m coursegen.generate --ingest "data/Your Course Folder"

# `python -m uvicorn`, not bare `uvicorn`: pip installs console scripts to a
# per-user Scripts directory that is often not on PATH, and the bare form then
# fails with "not recognized". The module form always resolves.
python -m uvicorn coursegen.app.main:app --workers 1        # single worker only
```

**Frontend** — in a second terminal:

```bash
cd frontend
npm install
npm run dev                   # proxies /api to http://127.0.0.1:8000
```

Open the URL Vite prints (`http://localhost:5173` by default). The React client is
the full interface: upload, blueprint, streamed generation, paper, provenance and
chat. Visiting the API's own port directly serves a minimal fallback page instead.

**Generating from the command line**, without the browser:

```bash
python -m coursegen.generate --blueprint quiz_default --verify
python -m coursegen.generate --blueprint quiz_default --no-gates   # skip model loads
```

Generation loads a cross-encoder and BGE-M3 for the relevance and duplication gates
(~4 GB of committable memory). It checks for that headroom first and tells you to use
`--no-gates` rather than dying mid-run.

## Practice: taking a paper (MVP 2)

Every generated paper can be taken in the browser. **Take this paper** turns it into an
answer sheet; submitting marks it and shows, for every question, the mark, the answer key or
model answer, and the page of your own material to revise.

- **Multiple-choice and true/false** are marked against the stored key — no model.
- **Written answers** are marked by the model against the model answer and the source
  passage, one call per six answers. Each mark carries a one-line reason and a confidence;
  low-confidence marks say *check with your teacher*. **Any mark can be contested**, and the
  mark you give is the one that counts.
- **Progress** shows mastery per lecture beside how much of the lecture has been tested.
- **Review** brings back what you missed: the same question first (options reordered), then
  — once you have passed it — a new question on the same passage. Re-tests return at your
  next review, then 2, 4 and 8 attempts later.
- **Next paper, built on my answers** keeps the material you got wrong, swaps out what you
  have mastered for material you have not been tested on, and asks questions you have
  already seen in new words.

History lives in `backend/output/prepify.db` (SQLite, gitignored). Mastery and re-tests are
derived from it every time, never stored, so contesting a mark re-derives everything after
it. Design: `docs/MVP2-GRADER-DESIGN.md`.

## Testing

```bash
cd backend  && python -m pytest        # 913 tests, no network
cd frontend && npm test               # 71 tests
cd frontend && npm run build          # typecheck + production build
```

```bash
cd backend && python -m pytest --live -m live   # real models + real API calls
cd backend && python -m coursegen.eval          # allocation evaluation, no LLM calls
cd backend && python -m coursegen.eval adapt    # E9: what the adaptive next paper does, no LLM calls
cd backend && python -m coursegen.eval marking  # E10: marking agreement — needs hand marks, spends quota
```

## What it does not do

**Prepify does not know whether a question is true.** The relevance gate rejects
off-topic text; it cannot tell a true claim from a false one. Gate 5
(`--verify`) checks whether an item's cited passage *states* its answer, which is a
narrower and honest claim — not proof of correctness. See `docs/P6-EVALUATION.md`.

Some blueprints ask for **synthesis** items, deliberately not answerable from the
uploaded material. The paper marks those as carrying no source, and the run manifest
reports how many.

**Prompt injection is contained structurally, not behaviourally.** Text from an
uploaded document cannot break out of the block that quotes it, in either the
generation or the chat prompt. Whether the model *obeys* an instruction sitting
inside a correctly quoted block is a separate, unsolved problem.

**The daily call cap counts this application's own calls**, on the local calendar
day. It is not the provider's quota and does not know about calls made elsewhere.

**A model's mark on a written answer is a judgement, not a verdict.** Its confidence is
the model's own report, not a calibrated probability, and its agreement with a teacher's
marks has not been measured yet: `python -m coursegen.eval marking` needs the team's own
hand marks in `backend/coursegen/eval/marking_set.csv` first (`docs/P6-EVALUATION.md`, E10).
Until then every model mark should be read as a suggestion — which is why every one can be
contested.

**The next paper does not drill weak topics harder.** Measured on the real course map
(E9): it keeps weak material in and trades mastered material for untested material, 12 of
12 cases — but a passage carries at most one question per paper, so it cannot ask about a
weak passage more than once. Drilling is the review set's job.

**Re-tests are spaced in attempts, not days.** Five attempts in one evening space them
minutes apart.
