"""Run a Prepify evaluation.

    python -m coursegen.eval                      # P6 allocation, no LLM calls
    python -m coursegen.eval --blueprint final_default
    python -m coursegen.eval --seeds 30

    python -m coursegen.eval reliability --runs 10   # E8, SPENDS LIVE QUOTA

The allocation evaluation is free and deterministic. The reliability harness makes
real API calls — roughly `runs x calls_per_paper` of them — so it takes an explicit
subcommand rather than running by accident.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.eval.harness import run_comparison, format_report, write_report
from coursegen.ingest.coursemap import load_course_map

BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "exam" / "blueprints"


def _load(name: str) -> Blueprint | None:
    path = BLUEPRINT_DIR / f"{name}.json"
    if not path.exists():
        print(f"  skipping {name!r}: no such blueprint")
        return None
    return Blueprint(**json.loads(path.read_text(encoding="utf-8")))


def _nodes() -> list | None:
    if not config.COURSE_MAP_PATH.exists():
        print(f"ERROR: no course map at {config.COURSE_MAP_PATH}. Ingest a course first.")
        return None
    nodes = load_course_map(config.COURSE_MAP_PATH)
    print(f"Course map: {len(nodes)} nodes, "
          f"{len({n.source_file for n in nodes})} source file(s)\n")
    return nodes


def _run_allocation(args: argparse.Namespace) -> int:
    nodes = _nodes()
    if nodes is None:
        return 1
    names = args.blueprint or sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))
    comparisons = [c for c in (
        (lambda bp: run_comparison(nodes, bp, seeds=tuple(range(1, args.seeds + 1))) if bp else None)(_load(n))
        for n in names
    ) if c is not None]
    if not comparisons:
        print("ERROR: no blueprints evaluated.")
        return 1
    print(format_report(comparisons))
    txt, js = write_report(comparisons, args.output)
    print(f"\nWrote {txt}\nWrote {js}")
    return 0


def _run_reliability(args: argparse.Namespace) -> int:
    from coursegen.eval.reliability import run_reliability, format_report as fmt, write_report as write

    nodes = _nodes()
    if nodes is None:
        return 1
    bp = _load(args.blueprint_id)
    if bp is None:
        return 1

    slots = sum(s.count for s in bp.sections)
    est_calls = args.runs * max(1, -(-slots // config.BATCH_SIZE))
    print(f"E8 reliability: {args.runs} runs of {bp.blueprint_id} ({slots} slots)")
    print(f"  estimated ~{est_calls} live API calls "
          f"(PER_DAY_CALL_CAP={config.PER_DAY_CALL_CAP})\n")

    # The relevance gate needs the cross-encoder. Loaded once and reused: a fresh
    # CrossEncoder per run would add ~40s of model load to every iteration and
    # measure the disk, not the model.
    scorer = None
    if not args.no_gates:
        from sentence_transformers import CrossEncoder
        print("  loading cross-encoder for the relevance gate ...")
        enc = CrossEncoder(config.RERANKER_MODEL)
        scorer = lambda answer, src: float(enc.predict([(answer, src)])[0])  # noqa: E731

    rep = run_reliability(
        course_map=nodes, blueprint=bp, data_dir=config.DATA_DIR,
        n_runs=args.runs, groundedness_scorer=scorer, progress=print,
    )
    print()
    print(fmt(rep))
    txt, js = write(rep, args.output)
    print(f"\nWrote {txt}\nWrote {js}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, default=config.OUTPUT_DIR)
    sub = ap.add_subparsers(dest="command")

    ap.add_argument("--blueprint", action="append",
                    help="blueprint id; repeatable. Default: all shipped blueprints.")
    ap.add_argument("--seeds", type=int, default=10,
                    help="random draws for baseline_naive (default 10)")

    rel = sub.add_parser("reliability", help="E8 N-run variance harness (SPENDS QUOTA)")
    rel.add_argument("--runs", type=int, default=10)
    rel.add_argument("--blueprint-id", default="quiz_default",
                     help="default quiz_default: fewest slots, so fewest calls per run")
    rel.add_argument("--no-gates", action="store_true",
                     help="skip loading the cross-encoder (relevance gate reported as skipped)")
    rel.add_argument("--output", type=Path, default=config.OUTPUT_DIR)

    args = ap.parse_args()
    return _run_reliability(args) if args.command == "reliability" else _run_allocation(args)


if __name__ == "__main__":
    sys.exit(main())
