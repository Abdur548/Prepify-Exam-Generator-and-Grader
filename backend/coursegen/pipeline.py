"""The one orchestration of load → solve → generate → verify → render.

Extracted 2026-09-02. Two callers were sequencing the same five stages by hand —
`app/main.py::_run_exam_pipeline` behind the HTTP route, and a CLI script under
`tests/` — and they had already drifted: the CLI wired no duplication gate and the
route wired no CLI-style progress. Two copies of an orchestration means a fix
lands in one of them, which is how the CLI ended up as the only holder of the
Qdrant span-read path for a week.

This module owns the sequence. `app/main.py` maps its result onto the API
contract; `coursegen.generate` is the CLI. Neither re-implements the middle.

## Gate 5 is opt-in and reported either way

`verify=True` runs the factuality gate (`exam/verify.py`), which costs roughly one
extra call per span group. The manifest records `factuality` whether or not it
ran, so a paper is never presented as checked when the gate was skipped — the same
`skipped` / `not_applicable` distinction `new_gate_report` draws.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem

BLUEPRINT_DIR = Path(__file__).resolve().parent / "exam" / "blueprints"


@dataclass(frozen=True)
class PaperResult:
    items: list[GeneratedItem]
    coverage: CoverageReport
    manifest: dict[str, Any]
    artifacts: Any
    blueprint: Blueprint
    # The assembled paper, so the HTTP route reports the same delivered counts the
    # document itself prints. Recomputing them beside `build_paper` is how the API
    # came to answer `fill_ratio: 1.0, unfilled_slots: []` for a paper missing two
    # of seven questions.
    paper: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """Derived, never asserted.

        A run that rendered zero items produced a document with no questions in
        it; calling that "ok" makes the field decorative.
        """
        return "ok" if self.items else "empty"


def load_blueprint(blueprint_id: str) -> Blueprint:
    path = BLUEPRINT_DIR / f"{blueprint_id}.json"
    if not path.exists():
        available = sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))
        raise ValueError(f"no blueprint {blueprint_id!r}. Available: {available}")
    return Blueprint(**json.loads(path.read_text(encoding="utf-8")))


def available_blueprints() -> list[str]:
    return sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))


def generate_paper(
    blueprint_id: str,
    title: str,
    *,
    nodes: Optional[list[CourseMapNode]] = None,
    data_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    groundedness_scorer: Optional[Callable[[str, str], float]] = None,
    embedding_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    llm_client: Any = None,
    verify: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> PaperResult:
    """Produce one paper. The only place these stages are sequenced.

    Every collaborator is injectable so the caller decides what to pay for: the
    HTTP route supplies real models, the CLI supplies the same, and a test can
    supply none and still exercise the sequence. Passing `None` for a scorer
    leaves its gate SKIPPED in the manifest rather than silently passing — the
    distinction that stops an unchecked paper reading as a clean one.

    ## Two telemetry channels, deliberately

    `progress` takes a formatted line and is what the CLI prints. `on_event` takes
    a structured dict and is what the HTTP stream forwards to a browser. They are
    separate rather than one channel with a formatter because a UI needs to know
    *which* stage started — a string that a client has to parse to find that out is
    a contract nobody wrote down. `progress`'s lines are unchanged by `on_event`
    existing; the CLI's output is byte-identical either way.

    ## Why stages fire on start as well as on completion

    Every `say()` line here fires *after* its stage finishes, which is the right
    shape for a log and the wrong shape for a progress display: writing the items
    is ~40 of the ~51 seconds of a first run, so a client told only about completed
    stages sits on "choosing" for forty seconds with nothing to show. Each stage
    therefore emits `state: "start"` before the work and `state: "done"` after.
    """
    from coursegen.exam.allocate import solve
    from coursegen.exam.generate import generate_exam
    from coursegen.exam.render import render_exam_artifacts
    from coursegen.ingest.coursemap import load_course_map
    from coursegen.ingest.index import read_spans
    from coursegen.llm.client import LLMClient

    emit = progress or (lambda _m: None)

    def say(build: Callable[[], str]) -> None:
        """Emit a progress line, and never let it fail the run it narrates.

        Twice in one refactor a progress message broke generation: once by
        subscripting an absent manifest key, once by applying a percent format to
        a value that was not a number. Both surfaced as HTTP 500 from a pipeline
        that had otherwise worked. Telemetry that can kill the operation is worse
        than no telemetry, so the message is built lazily inside the guard and any
        failure to build it is swallowed.
        """
        try:
            emit(build())
        except Exception:  # noqa: BLE001 - a progress line is never worth a failure
            pass

    fire = on_event or (lambda _e: None)

    def event(kind: str, **fields: Any) -> None:
        """Emit a structured event, under the same guarantee `say` gives.

        A client that has disconnected mid-run makes the forwarding callback raise
        on the next send. That must not become the reason a paper the student has
        already paid for never gets written to disk.
        """
        try:
            fire({"event": kind, **fields})
        except Exception:  # noqa: BLE001 - see say()
            pass

    def stage(name: str, state: str, **fields: Any) -> None:
        event("stage", stage=name, state=state, **fields)

    data_dir = data_dir or config.DATA_DIR
    output_dir = output_dir or config.OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- reading -----------------------------------------------------------
    stage("reading", "start")
    blueprint = load_blueprint(blueprint_id)
    if nodes is None:
        nodes = load_course_map(config.COURSE_MAP_PATH)
    say(lambda: f"course map: {len(nodes)} nodes")
    stage("reading", "done", topics=len(nodes))

    # ---- choosing ----------------------------------------------------------
    # `read_spans` sits inside this stage rather than in its own. It fetches the
    # passages for the slots `solve` just chose, so the stage ends when we know
    # both what to ask about and have the text in hand — which is what the label
    # claims, and the only grouping that keeps the order honest.
    stage("choosing", "start")
    specs, coverage = solve(nodes, blueprint)
    say(lambda: f"allocated {len(specs)}/{coverage.slots_total} slots "
              f"(fill {coverage.fill_ratio:.0%}, "
              f"fidelity {coverage.allocation_fidelity:.2f})")

    chunk_ids = sorted({sid for spec in specs for sid in spec.span_ids})
    span_text, span_source = read_spans(chunk_ids, data_dir)
    stage("choosing", "done", slots=len(specs), slots_total=coverage.slots_total,
          sections=len(blueprint.sections))

    # ---- writing -----------------------------------------------------------
    stage("writing", "start", total=len(specs))
    client = llm_client or LLMClient()
    result = generate_exam(
        specs=specs,
        course_map=nodes,
        span_text_by_id=span_text,
        span_source_by_id=span_source,
        llm_client=client,
        output_dir=output_dir,
        cache_dir=output_dir / "cache",
        blueprint_id=blueprint.blueprint_id,
        groundedness_scorer=groundedness_scorer,
        embedding_fn=embedding_fn,
        on_progress=lambda phase, done, total: event(
            "batch", phase=phase, done=done, total=total
        ),
    )
    # .get, not [] — a progress message must never be able to fail the run it is
    # narrating. Subscripting here turned an absent manifest key into a KeyError
    # that killed generation.
    say(lambda: f"generated {len(result.items)} items, "
              f"{result.manifest.get('call_count', '?')} calls")
    stage("writing", "done", items=len(result.items),
          calls=result.manifest.get("call_count"))

    # ---- assembling --------------------------------------------------------
    # Named for what happens: the factuality gate (when it is on), then rendering
    # the documents. UI-DESIGN.md called this stage "Checking sources", which R6
    # forbids — it would tell a student their questions had been checked against
    # the material, and nothing here establishes that a question is TRUE. The
    # gates that do run (schema, relevance, duplication, MCQ hygiene) run inside
    # `writing`, and they check relevance, not correctness.
    stage("assembling", "start")
    manifest = dict(result.manifest)
    factuality_summary, verdict_by_slot = _run_factuality_gate(
        result.items, specs, span_text, client, verify, say
    )
    manifest["factuality"] = factuality_summary

    artifacts = render_exam_artifacts(
        # The printed paper must not cite a page for an item written to be
        # answered by building something new — the disclosure the student accepted
        # says, in as many words, that those "carry no source".
        synthesis_slots={s.slot_id for s in specs if s.grounding == "synthesis"},
        items=result.items,
        coverage_report=coverage,
        output_dir=output_dir,
        title=title,
    )

    # The same paper as structured data, beside the rendered documents. A client
    # cannot badge provenance, filter synthesis items or reveal a source passage
    # from exam.html, and until this was written the API exposed only a count.
    from coursegen.exam.paper import build_paper

    paper = build_paper(
        items=result.items,
        specs=specs,
        blueprint=blueprint,
        coverage=coverage,
        title=title,
        span_text_by_id=span_text,
        factuality=verdict_by_slot,
    )
    (output_dir / "paper.json").write_text(
        json.dumps(paper, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    stage("assembling", "done")
    return PaperResult(
        items=result.items, coverage=coverage, manifest=manifest,
        artifacts=artifacts, blueprint=blueprint, paper=paper,
    )


def _run_factuality_gate(
    items, specs, span_text, client, verify: bool, say
) -> tuple[dict[str, Any], dict[str, str]]:
    """Gate 5, and an honest record when it did not run.

    `{"skipped": True}` rather than an absent key or a zero count: a caller that
    reads `supported == 0` must be able to tell "nothing was supported" from
    "nothing was checked". That conflation is what made gate 2's reporting
    misleading for a week.
    """
    if not verify:
        return {"skipped": True}, {}

    from coursegen.exam.verify import summarise, verify_items

    spec_by_slot = {s.slot_id: s for s in specs}
    pairs = [(spec_by_slot[i.slot_id], i) for i in items if i.slot_id in spec_by_slot]
    verdicts = verify_items(pairs, span_text, client)
    summary = summarise(verdicts, items_total=len(items))
    summary["skipped"] = False
    say(lambda: f"factuality: {summary['supported']}/{summary['checked']} "
              f"supported ({summary['not_applicable']} not applicable)")
    return summary, {slot: v.verdict for slot, v in verdicts.items()}
