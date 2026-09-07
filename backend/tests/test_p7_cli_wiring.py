"""P7 — what the CLI front end hands the pipeline.

There were no tests over `coursegen/generate.py` at all, which is why F12 survived
five days after the docstring said it was fixed: the CLI passed no `embedding_fn`,
so every CLI run reported `duplication.skipped: true` — including the 20-item paper
that is the project's one real end-to-end result.

These tests are about WIRING, not about the gates themselves. The gates have their
own tests; what was missing is anything asserting the front end actually reaches
them. That is the same failure as F3, where a correct renderer guard was simply not
passed its input and every template test stayed green.

Nothing here loads a model or makes a call.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from coursegen import generate as cli


@pytest.fixture
def captured(tmp_path, monkeypatch):
    """Run `cli.main` with every expensive collaborator replaced, and capture kwargs."""
    seen: dict = {}

    def fake_generate_paper(**kwargs):
        seen.update(kwargs)
        result = MagicMock()
        result.items = []
        result.status = "ok"
        result.manifest = {}
        result.coverage = MagicMock(fill_ratio=0.0, allocation_fidelity=0.0,
                                    unfilled_slots=[], warnings=[])
        result.artifacts = MagicMock()
        return result

    monkeypatch.setattr(cli, "generate_paper", fake_generate_paper)
    monkeypatch.setattr(cli, "available_blueprints", lambda: ["quiz_default"])
    monkeypatch.setattr(cli.config, "COURSE_MAP_PATH", tmp_path / "course_map.json")
    (tmp_path / "course_map.json").write_text("[]", encoding="utf-8")
    return seen


class TestTheDuplicationGateIsWired:

    def test_the_cli_passes_an_embedding_fn(self, captured) -> None:
        """F12. Without this the gate reports `skipped: true` on every CLI run."""
        with patch("sentence_transformers.CrossEncoder"), \
             patch("coursegen.ingest.embed.load_model"), \
             patch("coursegen.app.preflight._check_memory_headroom"):
            cli.main(["--blueprint", "quiz_default"])

        assert "embedding_fn" in captured, "the CLI never passed the argument at all"
        assert captured["embedding_fn"] is not None, \
            "embedding_fn is None, so the duplication gate will report skipped"

    def test_the_cli_still_passes_a_groundedness_scorer(self, captured) -> None:
        """The gate that already worked must keep working (R10)."""
        with patch("sentence_transformers.CrossEncoder"), \
             patch("coursegen.ingest.embed.load_model"), \
             patch("coursegen.app.preflight._check_memory_headroom"):
            cli.main(["--blueprint", "quiz_default"])

        assert captured["groundedness_scorer"] is not None

    def test_no_gates_skips_both_models(self, captured) -> None:
        """`--no-gates` must mean no gates, not one of two."""
        cli.main(["--blueprint", "quiz_default", "--no-gates"])
        assert captured["embedding_fn"] is None
        assert captured["groundedness_scorer"] is None

    def test_no_gates_loads_neither_model(self, captured) -> None:
        """The flag exists to avoid a ~40 s, ~4 GB load. It must actually avoid it."""
        with patch("coursegen.ingest.embed.load_model") as embed_model, \
             patch("sentence_transformers.CrossEncoder") as cross:
            cli.main(["--blueprint", "quiz_default", "--no-gates"])
        assert not embed_model.called, "BGE-M3 was loaded despite --no-gates"
        assert not cross.called, "the cross-encoder was loaded despite --no-gates"


class TestBothFrontEndsBuildTheSameCollaborator:
    """The drift F12 is an instance of, closed structurally rather than by hand.

    `pipeline.py` was extracted so two callers could not sequence the stages
    differently. It left the COLLABORATORS duplicated, so the drift moved instead
    of stopping — the route had an `embedding_fn` closure and the CLI had nothing.
    """

    def test_the_route_and_the_cli_use_one_function(self) -> None:
        import inspect

        from coursegen.app import main as route

        assert "dense_embedding_fn" in inspect.getsource(route._run_exam_pipeline)
        assert "dense_embedding_fn" in inspect.getsource(cli.main)

    def test_there_is_no_second_dense_only_embedding_closure(self) -> None:
        """A second dense-only embedder is the drift returning.

        Deliberately narrower than "no other `dense_vecs` unpack". `_run_chat_query`
        has one and must keep it: hybrid retrieval asks for `return_sparse=True` and
        embeds a single query, so it is a different operation, not a copy of this
        one. Merging them would trade a duplication bug for a coupling bug.
        """
        from pathlib import Path

        root = Path(cli.__file__).resolve().parent
        offenders = [
            p.relative_to(root).as_posix()
            for p in root.rglob("*.py")
            if "return_sparse=False" in p.read_text(encoding="utf-8")
        ]
        assert offenders == ["ingest/embed.py"], \
            f"a dense-only embedder exists outside ingest/embed.py: {offenders}"


class TestTheMemoryGuardRunsBeforeTheLoad:
    """BGE-M3 running out of committable memory is not an exception.

    The weights are memory-mapped, so the load succeeds and the process dies on the
    first forward pass with a Windows access violation — no traceback, nothing
    downstream can catch it. The HTTP route checks this in preflight; the CLI
    generation path only started loading BGE-M3 with the F12 fix, so it must not be
    the one place that skips the check.
    """

    def test_a_memory_failure_is_reported_not_a_crash(self, captured, capsys) -> None:
        with patch("sentence_transformers.CrossEncoder"), \
             patch("coursegen.app.preflight._check_memory_headroom",
                   side_effect=RuntimeError("only 0.50 GB of committable memory free")), \
             patch("coursegen.ingest.embed.load_model") as load:
            code = cli.main(["--blueprint", "quiz_default"])

        assert code == 1
        assert not load.called, "BGE-M3 was loaded after the memory check failed"
        out = capsys.readouterr().out
        assert "0.50 GB" in out, "the CLI swallowed the reason"
        assert "--no-gates" in out, "the CLI did not offer the way forward"
