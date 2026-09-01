"""Run the P6 allocation evaluation.

    python -m coursegen.eval                     # all blueprints, 10 seeds
    python -m coursegen.eval --seeds 30
    python -m coursegen.eval --blueprint final_default

Zero LLM calls, zero network. Reads the ingested course map and reports.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blueprint", action="append",
                    help="blueprint id; repeatable. Default: all shipped blueprints.")
    ap.add_argument("--seeds", type=int, default=10,
                    help="random draws for baseline_naive (default 10)")
    ap.add_argument("--output", type=Path, default=config.OUTPUT_DIR)
    args = ap.parse_args()

    if not config.COURSE_MAP_PATH.exists():
        print(f"ERROR: no course map at {config.COURSE_MAP_PATH}. Ingest a course first.")
        return 1
    nodes = load_course_map(config.COURSE_MAP_PATH)
    print(f"Course map: {len(nodes)} nodes, "
          f"{len({n.source_file for n in nodes})} source file(s)\n")

    names = args.blueprint or sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))
    comparisons = []
    for name in names:
        path = BLUEPRINT_DIR / f"{name}.json"
        if not path.exists():
            print(f"  skipping {name!r}: no such blueprint")
            continue
        bp = Blueprint(**json.loads(path.read_text(encoding="utf-8")))
        comparisons.append(run_comparison(nodes, bp, seeds=tuple(range(1, args.seeds + 1))))

    if not comparisons:
        print("ERROR: no blueprints evaluated.")
        return 1

    print(format_report(comparisons))
    txt, js = write_report(comparisons, args.output)
    print(f"\nWrote {txt}")
    print(f"Wrote {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
