"""Generate one exam from the command line.

    python -m coursegen.generate --blueprint ai_fundamentals_v1 --title "AI Final"
    python -m coursegen.generate --ingest "data/Artificial Intelligence"
    python -m coursegen.generate --blueprint quiz_default --verify

Replaces `tests/run_exam.py`, which was a second hand-rolled copy of the stage
sequence living in the test directory. Both copies had drifted: the CLI wired no
duplication gate, the route wired no progress output, and for a week the CLI was
the only holder of the Qdrant span-read path. The sequence now lives in
`coursegen/pipeline.py` and this is a thin front end onto it.

That extraction fixed the SEQUENCE and left the COLLABORATORS duplicated, so the
drift moved rather than stopped: this file still passed no `embedding_fn`, and
went on reporting `duplication.skipped: true` on every run for another five days
while the paragraph above said otherwise (F12, 2026-09-06). Both front ends now
build their gate collaborators from the same functions.

Two steps, deliberately separate. `--ingest` embeds a directory of course material
(~10 minutes on CPU for 14 decks) and stops. Generation then runs against the
persisted course map, so the expensive step is not repeated for every paper.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coursegen import config
from coursegen.pipeline import available_blueprints, generate_paper


def _ingest(source: Path) -> int:
    from coursegen.ingest.coursemap import ingest

    if not source.is_dir():
        print(f"ERROR: {source.resolve()} is not a directory")
        return 1
    print(f"Ingesting {source} …  (BGE-M3 on CPU; ~10 min for a 14-deck course)")
    nodes = ingest(source, data_dir=config.DATA_DIR)
    if not nodes:
        print("ERROR: no course map produced. Check the files are .pdf/.pptx/.docx.")
        return 1
    print(f"  {len(nodes)} nodes, {sum(len(n.chunk_ids) for n in nodes)} spans, "
          f"from {len({n.source_file for n in nodes})} file(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ingest", type=Path, metavar="DIR",
                    help="embed a directory of course material, then stop")
    ap.add_argument("--blueprint", default="ai_fundamentals_v1")
    ap.add_argument("--title", default="Practice Exam")
    ap.add_argument("--verify", action="store_true",
                    help="run the factuality gate (gate 5) — roughly doubles API cost")
    ap.add_argument("--no-gates", action="store_true",
                    help="skip loading the cross-encoder and BGE-M3; the relevance "
                         "and duplication gates are then reported as skipped "
                         "rather than passed")
    args = ap.parse_args(argv)

    if args.ingest is not None:
        return _ingest(args.ingest)

    if not config.COURSE_MAP_PATH.exists():
        print(f"ERROR: no course map at {config.COURSE_MAP_PATH}.\n"
              f"       Run --ingest <dir> first.")
        return 1
    if args.blueprint not in available_blueprints():
        print(f"ERROR: no blueprint {args.blueprint!r}. "
              f"Available: {available_blueprints()}")
        return 1

    scorer = None
    embedding_fn = None
    if not args.no_gates:
        from sentence_transformers import CrossEncoder
        print("Loading cross-encoder for the relevance gate …  (~40 s cold)")
        enc = CrossEncoder(config.RERANKER_MODEL)
        scorer = lambda answer, src: float(enc.predict([(answer, src)])[0])  # noqa: E731

        # The duplication gate needs an embedder, and this front end never passed
        # one — so every CLI run reported `duplication.skipped: true`, including
        # the paper STATE.md cites as the system's one real end-to-end result. The
        # module docstring above has claimed this was fixed since the pipeline
        # extraction; it was not, until 2026-09-06 (F12).
        from coursegen.app.preflight import _check_memory_headroom
        from coursegen.ingest.embed import dense_embedding_fn, load_model

        # Checked BEFORE the load, because running out of committable memory here
        # is not an exception: BGE-M3's weights are memory-mapped, so loading
        # succeeds and the process dies on the first forward pass with a Windows
        # access violation. Nothing downstream can catch that, which is why the
        # HTTP route checks it in preflight and why this path — new as of the F12
        # fix — must not be the one place that skips it.
        #
        # It reads AFTER the cross-encoder load and that is the point, not an
        # oversight. The cross-encoder takes ~1.3 GB, so a machine reading 4.7 GB
        # free before it reads 3.4 GB after — and 3.4 GB is the number that decides
        # whether BGE-M3 survives. Measured on this machine 2026-09-06: checking
        # first would have PASSED at 4.73 GB and then hard-killed the process.
        # Moving it earlier trades a 40-second wait for an uncatchable crash.
        try:
            _check_memory_headroom()
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            print("       Re-run with --no-gates to generate without the "
                  "relevance and duplication gates.")
            return 1

        print("Loading BGE-M3 for the duplication gate …  (~40 s cold, ~4 GB)")
        embedding_fn = dense_embedding_fn(load_model())

    try:
        result = generate_paper(
            blueprint_id=args.blueprint,
            title=args.title,
            groundedness_scorer=scorer,
            embedding_fn=embedding_fn,
            verify=args.verify,
            progress=lambda m: print(f"  {m}"),
        )
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"ERROR: generation failed — {type(exc).__name__}: {exc}")
        return 1

    m = result.manifest
    print(f"\n{result.status.upper()}: {len(result.items)} items, "
          f"fill {result.coverage.fill_ratio:.0%}, "
          f"fidelity {result.coverage.allocation_fidelity:.2f}")
    for warning in result.coverage.warnings:
        print(f"  ! {warning}")
    if m.get("flagged_slots"):
        print(f"  flagged after regeneration: {m['flagged_slots']}")

    fact = m.get("factuality", {})
    if fact.get("skipped"):
        print("  factuality: NOT CHECKED (pass --verify)")
    else:
        print(f"  factuality: {fact.get('supported')}/{fact.get('checked')} supported")

    print(f"\nWrote to {config.OUTPUT_DIR}")
    for path in (result.artifacts.exam_html, result.artifacts.exam_pdf,
                 result.artifacts.answer_key_html, result.artifacts.answer_key_pdf,
                 result.artifacts.coverage_html):
        if Path(path).exists():
            print(f"  {Path(path).name:<24}{Path(path).stat().st_size:>10,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
