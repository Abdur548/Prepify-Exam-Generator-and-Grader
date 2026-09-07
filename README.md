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
    ├── API-CONTRACT.md     the frozen HTTP surface the frontend depends on
    └── P6-EVALUATION.md    measured results, with falsification conditions
```

## Running it

Two processes. The API takes an exclusive lock on the Qdrant index, so it must run
single-worker and only one copy at a time.

**Backend**

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env          # add GEMINI_API_KEY

python -m coursegen.generate --ingest "data/My Course"     # ~10 min, once
uvicorn coursegen.app.main:app --workers 1                 # single worker only
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

## Testing

```bash
cd backend  && python -m pytest        # 650 tests, no network
cd frontend && npm test               # 18 tests
cd frontend && npm run build          # typecheck + production build
```

```bash
cd backend && python -m pytest --live -m live   # real models + real API calls
cd backend && python -m coursegen.eval          # allocation evaluation, no LLM calls
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
