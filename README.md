# Prepify

Course Exam Generator and Chatbot. Upload your lecture material; get a structured
exam paper with real citations back, and a chat window over the same corpus.

**Code decides *what* to ask** — which topic, which source span, which Bloom level —
by `instructional_mass`. The model only writes the words. That claim is measured, not
asserted: the solver covers **2.4–4.0× the instructional mass** of a random paper of
identical shape, beating every individual draw on all four blueprints
(`docs/P6-EVALUATION.md`).

## Layout

```
prepify/
├── backend/
│   ├── coursegen/          the package
│   │   ├── ingest/         parse → chunk → embed → index → course map
│   │   ├── exam/           allocate → generate → validate → verify → render
│   │   ├── retrieve/       hybrid search + reranking
│   │   ├── chat/           question answering over the corpus
│   │   ├── eval/           P6 allocation + E8 reliability harnesses
│   │   ├── app/            FastAPI service and static UI
│   │   ├── pipeline.py     the one orchestration, shared by API and CLI
│   │   └── generate.py     CLI front end
│   ├── tests/
│   ├── data/               course material + Qdrant index (gitignored, S7)
│   └── output/             generated papers (gitignored)
├── docs/
├── the working rules               working rules — read before changing anything
└── STATE.md                where the project stands — read first
```

## Running it

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env          # add GEMINI_API_KEY

python -m coursegen.generate --ingest "data/My Course"     # ~10 min, once
python -m coursegen.generate --blueprint quiz_default --verify

uvicorn coursegen.app.main:app --workers 1                 # single worker only (S8)
```

```bash
python -m pytest                 # 477 tests, no network
python -m pytest --live -m live  # real models + real API calls
python -m coursegen.eval         # P6 allocation evaluation, no LLM calls
```

## What it does not do

**Prepify does not know whether a question is true.** The relevance gate rejects
off-topic text; it cannot tell a true claim from a false one. Gate 5
(`--verify`) checks whether an item's cited passage *states* its answer, which is a
narrower and honest claim — not proof of correctness. See `docs/P6-EVALUATION.md` §E8.

Some blueprints ask for **synthesis** items, deliberately not answerable from the
uploaded material. The run manifest reports how many.
