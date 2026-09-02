"""Opt-in tests that run the REAL models and make a REAL API call.

    python -m pytest -m live -v

Deselected from the default run (`addopts = -q -m 'not live'`), because they load
multi-gigabyte models and spend live quota.

## Why this file exists

Everything else in this suite mocks the expensive paths, which is correct for
speed and has hidden real breakage four separate times:

- `embed_chunks` passed `show_progress_bar` to a model that rejects it. **The real
  embedding path had never executed** — four days, 375 green tests — and it failed
  on its first line the first time it ran for real.
- Every citation in a generated paper was fabricated; all four validation gates
  passed it.
- The static UI had a `SyntaxError`; 385 tests stayed green.
- BGE-M3 could not run at all on this machine for an afternoon, and the suite was
  green throughout.

"The model actually runs" was proven nowhere. That is what these tests assert, and
almost nothing more. They are smoke tests: shape, dtype, finiteness, a non-empty
answer. Deliberately not accuracy tests — nothing here checks that an embedding is
*good* or an answer is *true*, and the system has no instrument for the latter
(see `P6-EVALUATION.md` §E8).

## What each one costs

| test | cost |
|---|---|
| `test_real_embedder_produces_usable_vectors` | ~10-25 s, BGE-M3 load, no quota |
| `test_real_reranker_scores_relevance_correctly` | ~40 s cold, no quota |
| `test_real_llm_smoke_call` | 1 API call, ~10 tokens |
| `test_real_llm_honours_a_response_schema` | 1 API call, ~100 tokens |

Two calls per full live run. `PER_DAY_CALL_CAP` is 900.

## If these fail

A failure here is almost never a bug in the test. It means the environment cannot
do the thing the product depends on — check `python -m coursegen.app.preflight`
first, and `memory` before `models`: BGE-M3 needs ~4 GB of committable memory and
the OS kills the process outright below that, with no Python exception.
"""
from __future__ import annotations

import math
import os

import pytest

from coursegen import config

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# The real embedder
# ---------------------------------------------------------------------------

class TestRealEmbedder:

    def test_real_embedder_produces_usable_vectors(self) -> None:
        """BGE-M3 actually runs, and returns what the rest of the code assumes.

        This is the test whose absence let `show_progress_bar` sit undetected. It
        asserts the CONTRACT the pipeline relies on — dimensionality, finiteness,
        one vector per input — not that the vectors are semantically good.
        """
        from types import SimpleNamespace
        from coursegen.ingest.embed import embed_chunks, load_model

        chunks = [
            SimpleNamespace(text="A* search uses f(n) = g(n) + h(n) to order the frontier."),
            SimpleNamespace(text="Depth-first search is not optimal; it returns the first solution."),
            SimpleNamespace(text="A constraint satisfaction problem has variables and domains."),
        ]
        result = embed_chunks(chunks, load_model())

        assert result.dense.shape == (len(chunks), config.DENSE_VECTOR_SIZE), (
            f"dense shape {result.dense.shape} != expected "
            f"{(len(chunks), config.DENSE_VECTOR_SIZE)} — the index will reject these"
        )
        assert all(math.isfinite(float(v)) for v in result.dense[0]), "non-finite values in vector 0"
        assert len(result.sparse) == len(chunks)
        # Distinct inputs must not collapse to one point, which would silently
        # destroy retrieval while every shape assertion above still passed.
        assert not (result.dense[0] == result.dense[1]).all(), "distinct texts produced identical vectors"

    def test_real_embedder_is_deterministic(self) -> None:
        """Same text twice, same vector. Retrieval quality depends on this."""
        from types import SimpleNamespace
        from coursegen.ingest.embed import embed_chunks, load_model

        model = load_model()
        text = SimpleNamespace(text="Iterative deepening trades time for space.")
        a = embed_chunks([text], model)
        b = embed_chunks([text], model)
        assert (a.dense[0] == b.dense[0]).all(), "the embedder is not deterministic"


# ---------------------------------------------------------------------------
# The real reranker
# ---------------------------------------------------------------------------

class TestRealReranker:

    def test_real_reranker_scores_relevance_correctly(self) -> None:
        """The cross-encoder ranks an on-topic passage above an unrelated one.

        This is the ONLY thing this model is trusted to do. It was disproven as a
        factuality signal on 2026-09-01 (true and false claims about the same span
        overlap completely), so the assertion here is deliberately narrow: topical
        relevance, which is what it was trained for and what the relevance gate
        relies on.
        """
        from sentence_transformers import CrossEncoder

        enc = CrossEncoder(config.RERANKER_MODEL)
        query = "How does A* search choose which node to expand?"
        on_topic = "A* expands the node with the lowest f(n) = g(n) + h(n)."
        off_topic = "The mitochondrion is the powerhouse of the cell."

        s_on = float(enc.predict([(query, on_topic)])[0])
        s_off = float(enc.predict([(query, off_topic)])[0])

        assert s_on > s_off, f"on-topic {s_on:.2f} did not beat off-topic {s_off:.2f}"
        assert s_off < config.RERANKER_THRESHOLD, (
            f"unrelated text scored {s_off:.2f}, at or above "
            f"RERANKER_THRESHOLD={config.RERANKER_THRESHOLD} — the floor would admit it"
        )


# ---------------------------------------------------------------------------
# The real LLM
# ---------------------------------------------------------------------------

class TestRealLLM:

    def test_real_llm_smoke_call(self) -> None:
        """One live call. Proves the key, endpoint, model id and response shape.

        `gemini-2.0-flash-lite` was retired underneath this project and every call
        404'd; nothing in the suite noticed, because every test mocked the client.
        This is the test that would have caught it on the next run.
        """
        from coursegen.llm.client import LLMClient

        resp = LLMClient().call([{"role": "user", "content": "Reply with the single word: ok"}])

        assert "choices" in resp, f"unexpected response shape: {sorted(resp)}"
        content = resp["choices"][0]["message"]["content"]
        assert content.strip(), "model returned an empty message"
        assert resp.get("usage", {}).get("total_tokens", 0) > 0

    def test_real_llm_honours_a_response_schema(self) -> None:
        """Structured output still works against the live model.

        A dangling `$ref` in the generated schema produced HTTP 400 on the first
        real run — `$defs` were nested where the pointers could not resolve. That
        was a schema-shape bug no mocked test could see, because the mock never
        validated the schema it was handed.
        """
        from coursegen.exam.generate import _response_schema
        from coursegen.llm.client import LLMClient

        schema = _response_schema()
        resp = LLMClient().call(
            [{"role": "user", "content":
              "Produce one multiple-choice item about breadth-first search, "
              "with slot_id 'A-01'."}],
            response_schema=schema,
        )
        import json
        payload = json.loads(resp["choices"][0]["message"]["content"])
        assert "items" in payload, f"schema not honoured; got keys {sorted(payload)}"
        assert isinstance(payload["items"], list) and payload["items"], "no items returned"


# ---------------------------------------------------------------------------
# Credential handling, against the real key
# ---------------------------------------------------------------------------

def test_the_real_key_is_redacted() -> None:
    """S1, checked against the credential actually in use rather than a fixture.

    The pattern originally covered `AIza…` and `sk-…`. The key in use starts
    `AQ.A` and matched neither, so it passed through `_redact()` intact — a
    redaction rule that did not cover the credential being held. Verified here
    against the live value, which a fixture cannot do.
    """
    from coursegen.llm.client import _redact

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    assert key not in _redact(f"Authorization: Bearer {key} failed"), (
        "the live API key survived redaction"
    )
