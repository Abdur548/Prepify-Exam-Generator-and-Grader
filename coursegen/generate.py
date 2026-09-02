"""Generate one exam from the command line.

    python -m coursegen.generate --blueprint ai_fundamentals_v1 --title "AI Final"
    python -m coursegen.generate --ingest "data/Artificial Intelligence"
    python -m coursegen.generate --blueprint quiz_default --verify

Replaces `tests/run_exam.py`, which was a second hand-rolled copy of the stage
sequence living in the test directory. Both copies had drifted: the CLI wired no
duplication gate, the route wired no progress output, and for a week the CLI was
the only holder of the Qdrant span-read path. The sequence now lives in
`coursegen/pipeline.py` and this is a thin front end onto it.

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
                    help="skip loading the cross-encoder; the relevance gate is "
                         "then reported as skipped rather than passed")
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
    if not args.no_gates:
        from sentence_transformers import CrossEncoder
        print("Loading cross-encoder for the relevance gate …  (~40 s cold)")
        enc = CrossEncoder(config.RERANKER_MODEL)
        scorer = lambda answer, src: float(enc.predict([(answer, src)])[0])  # noqa: E731

    try:
        result = generate_paper(
            blueprint_id=args.blueprint,
            title=args.title,
            groundedness_scorer=scorer,
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
