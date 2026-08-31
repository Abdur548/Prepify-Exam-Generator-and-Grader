"""
End-to-end runner: ingest → solve → (stop and look) → generate → render.

NOT a pytest module. It has no `test_` prefix so pytest never collects it; it is a
script that lives here because it exercises the whole pipeline the way a test
would, against real course material rather than fixtures.

WHY IT EXISTS — and when to delete it. `app/main.py::_run_exam_pipeline` still
raises NotImplementedError and `python -m coursegen` offers only --dry-run, so
nothing in the product joins the four stages. This does. **When P5 is wired, this
script should go** — if it is still here after P5 ships, it has quietly become the
interface, which is not what it is for.

It also fills a real gap in the product, not just in the plumbing:
`ingest()` returns CourseMapNodes carrying chunk IDs but not chunk TEXT, while
`generate_exam()` needs {chunk_id: text}. The text is in the Qdrant payload and
nothing reads it back — generation has only ever run against hand-built span
dictionaries in tests. `_span_text_from_qdrant` below is that missing read path,
and it belongs in `ingest/index.py` as a proper helper rather than here.

USAGE — two steps, deliberately separate.

    cd E:\\Qoder\\prepify

    # 1. Ingest and read the coverage table. Calls no model, spends nothing.
    python tests/run_exam.py --source "data/Artificial Intelligence" \\
                             --blueprint ai_fundamentals_v1

    # 2. Only once the coverage table looks right.
    python tests/run_exam.py --source "data/Artificial Intelligence" \\
                             --blueprint ai_fundamentals_v1 --generate

Step 1 is not a formality. If the lecture headings do not match the blueprint's
topic strings, the report says so and the paper comes out half empty — better to
learn that before paying for it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import coursegen
from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.exam.allocate import solve
from coursegen.exam.generate import generate_exam
from coursegen.exam.render import render_exam_artifacts
from coursegen.ingest.coursemap import ingest, load_course_map
from coursegen.ingest.index import get_client
from coursegen.llm.client import LLMClient

PACKAGE_DIR = Path(coursegen.__file__).resolve().parent
BLUEPRINT_DIR = PACKAGE_DIR / "exam" / "blueprints"


def _span_text_from_qdrant(chunk_ids: list[str], data_dir: Path) -> dict[str, str]:
    """Read chunk text back out of Qdrant by point id.

    `ingest()` persists the text as a payload field but returns only the course
    map, so this is the only way to recover the spans an item is generated from.
    Qdrant local mode holds an exclusive file lock (S8), so the client is closed
    before anything else opens it.
    """
    client = get_client(data_dir)
    try:
        records = client.retrieve(
            collection_name=config.QDRANT_COLLECTION_NAME,
            ids=chunk_ids,
            with_payload=True,
        )
    finally:
        client.close()
    return {str(r.id): (r.payload or {}).get("text", "") for r in records}


def _span_source_from_qdrant(chunk_ids: list[str], data_dir: Path) -> dict[str, tuple[str, int]]:
    """Real file and page per span, so `source_ref` is built by code not invented.

    The model only ever sees `<source_span id="...">`, so asked for a citation it
    can only echo the id — which is exactly what happened on the first real run.
    """
    client = get_client(data_dir)
    try:
        records = client.retrieve(
            collection_name=config.QDRANT_COLLECTION_NAME,
            ids=chunk_ids,
            with_payload=True,
        )
    finally:
        client.close()
    return {
        str(r.id): ((r.payload or {}).get("source_file", "unknown"),
                    int((r.payload or {}).get("page", 0)))
        for r in records
    }


def _print_coverage(report, blueprint: Blueprint) -> None:
    print(f"\n{'=' * 72}\nCOVERAGE — decide here, before spending anything\n{'=' * 72}")
    print(f"  slots filled     {report.slots_filled}/{report.slots_total}"
          f"   fill_ratio {report.fill_ratio:.2f}")
    print(f"  syllabus touched {report.nodes_covered}/{report.nodes_total}"
          f"   coverage_ratio {report.coverage_ratio:.2f}")
    print(f"  placed by mass   {report.allocation_fidelity:.2f}"
          f"   (remainder by span exhaustion)")

    if report.per_topic:
        print(f"\n  {'topic':46s} {'nodes':>6s} {'best':>7s} {'filled':>8s}")
        for t in report.per_topic:
            flag = "  <-- NO MATERIAL" if t.matched_node_count == 0 else ""
            print(f"  {t.topic[:44]:46s} {t.matched_node_count:6d} {t.best_score:7.2f} "
                  f"{t.slots_filled:3d}/{t.slots_requested:<3d}{flag}")

    if report.bloom_realised:
        print(f"\n  bloom realised (by marks): "
              f"{ {k: round(v, 2) for k, v in report.bloom_realised.items()} }")
        if blueprint.cognitive_balance:
            print(f"  bloom declared           : {blueprint.cognitive_balance}")

    if report.warnings:
        print(f"\n  warnings ({len(report.warnings)}):")
        for w in report.warnings[:10]:
            print(f"    ! {w}")
        if len(report.warnings) > 10:
            print(f"    … and {len(report.warnings) - 10} more")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--source", default=None,
                    help="directory of .pdf / .pptx / .docx, relative to cwd or absolute")
    ap.add_argument("--blueprint", default="midterm_default",
                    help=f"blueprint id; files live in {BLUEPRINT_DIR}")
    ap.add_argument("--out", default=None, help="default: config.OUTPUT_DIR")
    ap.add_argument("--generate", action="store_true",
                    help="actually call the model. Without it, stops after coverage.")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="reuse the existing course_map.json and Qdrant index instead of "
                         "re-ingesting. See the note in main() for why this matters.")
    ap.add_argument("--title", default="Exam")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else config.OUTPUT_DIR
    data_dir = config.DATA_DIR

    # ingest() is IDEMPOTENT but not INCREMENTAL: re-running yields identical node
    # IDs and upserts rather than duplicates, but it still re-parses and re-embeds
    # everything, ~10 minutes on CPU for a 14-deck course. Without this flag every
    # --generate attempt pays that before making a single API call, and pays it
    # again after any failure. L14 already says to pre-bake the demo collection
    # because live ingest is too slow; this is the code path that actually uses one.
    if args.skip_ingest:
        if not config.COURSE_MAP_PATH.exists():
            print(f"ERROR: --skip-ingest needs {config.COURSE_MAP_PATH}, which does not "
                  f"exist. Run once without the flag first.")
            return 1
        nodes = load_course_map(config.COURSE_MAP_PATH)
        print(f"Reusing existing course map ({config.COURSE_MAP_PATH})")
    else:
        source = Path(args.source).expanduser()
        if not source.is_dir():
            print(f"ERROR: {source.resolve()} is not a directory")
            return 1
        print(f"Ingesting {source} …  (BGE-M3 on CPU; ~10 min for a 14-deck course)")
        nodes = ingest(source, data_dir=data_dir)

    if not nodes:
        print("ERROR: no course map. Check the files are .pdf/.pptx/.docx and non-empty.")
        return 1
    print(f"  {len(nodes)} nodes, {sum(len(n.chunk_ids) for n in nodes)} spans, "
          f"from {len({n.source_file for n in nodes})} file(s)")

    bp_path = BLUEPRINT_DIR / f"{args.blueprint}.json"
    if not bp_path.exists():
        available = sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))
        print(f"ERROR: no blueprint {args.blueprint!r}. Available: {available}")
        return 1
    blueprint = Blueprint(**json.loads(bp_path.read_text(encoding="utf-8")))
    print(f"  blueprint: {blueprint.blueprint_id}  "
          f"{blueprint.total_marks} marks, {blueprint.duration_minutes} min")

    specs, report = solve(nodes, blueprint)
    _print_coverage(report, blueprint)

    if not args.generate:
        print("\nStopped before generation. Re-run with --generate when this looks right.")
        return 0
    if not specs:
        print("\nNothing to generate — the solver produced no items.")
        return 1

    needed = sorted({sid for s in specs for sid in s.span_ids})
    span_text = _span_text_from_qdrant(needed, data_dir)
    span_source = _span_source_from_qdrant(needed, data_dir)
    missing = [sid for sid in needed if not span_text.get(sid)]
    if missing:
        print(f"ERROR: {len(missing)} span(s) had no text in Qdrant, e.g. {missing[:3]}")
        return 1

    # The groundedness gate needs a scorer, and it is the gate that catches a
    # question the source does not actually support. The dedup gate additionally
    # needs BGE-M3 resident; left unwired for a first run, and the manifest records
    # it as skipped rather than passed.
    from sentence_transformers import CrossEncoder
    encoder = CrossEncoder(config.RERANKER_MODEL)

    def groundedness(answer: str, source_text: str) -> float:
        return float(encoder.predict([(answer, source_text)])[0])

    print(f"\nGenerating {len(specs)} items …  (cap {config.PER_EXAM_CALL_CAP} calls)")
    result = generate_exam(
        specs=specs,
        course_map=nodes,
        span_text_by_id=span_text,
        llm_client=LLMClient(),
        output_dir=out_dir,
        cache_dir=out_dir / "cache",
        blueprint_id=blueprint.blueprint_id,
        groundedness_scorer=groundedness,
        embedding_fn=None,
        span_source_by_id=span_source,
    )

    m = result.manifest
    print(f"  {len(result.items)} items · {m['call_count']} calls · "
          f"{m['token_count']} tokens · {m['cache_hits']} cache hits")
    print(f"  validation: {json.dumps(m['validation'])}")
    print(f"  grounding : {json.dumps(m['grounding'])}")
    for w in m.get("warnings", []):
        print(f"    ! {w}")
    if m["flagged_slots"]:
        print(f"  flagged after regeneration: {m['flagged_slots']}")

    render_exam_artifacts(
        items=result.items,
        coverage_report=report,
        output_dir=out_dir,
        title=args.title,
    )
    print(f"\nWrote to {out_dir}:")
    for f in sorted(out_dir.iterdir()):
        if f.is_file():
            print(f"  {f.name:24s} {f.stat().st_size:>9,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
