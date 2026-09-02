"""E8 — LLM reliability. The N-run variance harness.

    python -m coursegen.eval.reliability --runs 10

The one component that cannot be unit-tested is the one the product depends on.
Everything else in this repo is deterministic and provable; the model is neither.
Before this harness, the only evidence about generation reliability was **one
clean run** — 20/20 items, every gate passing — which is a data point, not a rate,
and must never be reported as one.

## What it measures

| dimension | what it answers |
|---|---|
| E8.1 variance | per-gate pass rate across N runs: mean, min, max, stdev |
| E8.2 taxonomy | what actually goes wrong, and how often per 100 items |
| E8.3 regeneration | of items that fail a gate, what fraction the one retry rescues |
| E8.4 determinism | same `spec_hash`, cache bypassed — how often is the item identical? |
| cost | calls, tokens and wall clock per run, with spread |

## Why the cache is bypassed

Every run gets a fresh cache directory. Without that, run 2 onwards would be
served entirely from run 1's cache — `cache_hits == items`, `call_count == 0` —
and the harness would report perfect reliability having called the model once.
That failure would look exactly like success, which is the property that makes it
worth stating here rather than trusting a reader to notice.

## What it does NOT measure

Whether the questions are **true**. Gate 2 was disproven on 2026-09-01: the
reranker behind it scores topical relevance and cannot separate a true claim from
a false one about the same span. A 100% pass rate here means well-formed and
on-topic, nothing more. Any reliability figure quoted without that sentence beside
it overstates what was measured.
"""
from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.exam.allocate import solve
from coursegen.exam.generate import generate_exam
from coursegen.ingest.index import read_spans
from coursegen.llm.client import LLMClient

GATES = ("schema", "relevance", "duplication", "mcq_hygiene")


@dataclass(frozen=True)
class RunOutcome:
    """One generation run. `error` is set when the run died rather than degraded."""
    run: int
    items_generated: int
    slots_total: int
    calls: int
    tokens: int
    wall_clock: float
    regeneration_passes: int
    flagged_slots: tuple[str, ...]
    gates: dict[str, dict[str, Any]]
    issues: tuple[tuple[str, str, str], ...]   # (slot_id, gate, message)
    stems: dict[str, str]                      # slot_id -> stem, for determinism
    error: Optional[str] = None

    @property
    def completed(self) -> bool:
        return self.error is None

    def gate_pass_rate(self, gate: str) -> Optional[float]:
        """None when the gate did not evaluate anything — not 1.0.

        A gate that never ran has no pass rate. Reporting it as 100% would be the
        same conflation `new_gate_report` exists to prevent.
        """
        g = self.gates.get(gate, {})
        evaluated = g.get("evaluated", 0)
        return g["passed"] / evaluated if evaluated else None


@dataclass
class ReliabilityReport:
    blueprint_id: str
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def completed_runs(self) -> list[RunOutcome]:
        return [r for r in self.runs if r.completed]

    def gate_rates(self, gate: str) -> list[float]:
        return [r for r in (o.gate_pass_rate(gate) for o in self.completed_runs) if r is not None]

    def item_yield(self) -> list[float]:
        """Items actually delivered / slots asked for, per run.

        The number a student would notice. A paper can pass every gate and still
        arrive short.
        """
        return [o.items_generated / o.slots_total for o in self.completed_runs if o.slots_total]

    def failure_taxonomy(self) -> dict[str, int]:
        """Failures by gate and coarse cause, counted across all runs."""
        tally: dict[str, int] = {}
        for o in self.completed_runs:
            for _slot, gate, message in o.issues:
                tally[f"{gate}: {_classify(message)}"] = tally.get(f"{gate}: {_classify(message)}", 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: -kv[1]))

    def regeneration_effectiveness(self) -> Optional[float]:
        """Fraction of flagged items the single permitted retry rescued.

        None when nothing was ever flagged — with no failures there is nothing to
        rescue, and 1.0 would imply a retry that never happened succeeded.
        """
        rescued = attempted = 0
        for o in self.completed_runs:
            if not o.regeneration_passes:
                continue
            failed_first = {slot for slot, _g, _m in o.issues}
            attempted += len(failed_first)
            rescued += len(failed_first - set(o.flagged_slots))
        return rescued / attempted if attempted else None

    def determinism(self) -> Optional[float]:
        """Fraction of slots whose stem was identical on every run that produced it.

        The cache is bypassed, so this is the model's own repeatability at
        temperature. Bears on R7: a manifest is only a reproduction recipe if the
        same spec yields the same item.
        """
        by_slot: dict[str, set[str]] = {}
        for o in self.completed_runs:
            for slot, stem in o.stems.items():
                by_slot.setdefault(slot, set()).add(stem.strip())
        if not by_slot:
            return None
        identical = sum(1 for variants in by_slot.values() if len(variants) == 1)
        return identical / len(by_slot)


def _classify(message: str) -> str:
    """Coarse bucket for a validation message, so the taxonomy is readable."""
    m = message.lower()
    if "score=" in m:
        return "below relevance floor"
    if "cosine" in m:
        return "duplicate of an earlier item"
    if "options and correct_option" in m:
        return "mcq missing options"
    if "band" in m:
        return "option lengths outside band"
    if "above" in m:
        return "all/none of the above"
    if "duplicate option" in m:
        return "duplicate option text"
    if "no matching itemspec" in m:
        return "unknown slot_id"
    return "schema / other"


def run_reliability(
    course_map: list[CourseMapNode],
    blueprint: Blueprint,
    data_dir: Path,
    n_runs: int = 10,
    groundedness_scorer: Optional[Callable[[str, str], float]] = None,
    embedding_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> ReliabilityReport:
    """Generate the same blueprint `n_runs` times, cache bypassed, and tally.

    `embedding_fn` defaults to None, leaving the duplication gate skipped. That is
    deliberate: it requires BGE-M3 resident alongside the cross-encoder for the
    whole run, and this harness exists to measure the MODEL's variance rather than
    to stress the host. The manifest records the gate as skipped, so the report
    says "not measured" instead of implying a clean sheet.
    """
    say = progress or (lambda _m: None)
    specs, _report = solve(course_map, blueprint)
    needed = sorted({sid for s in specs for sid in s.span_ids})
    span_text, span_source = read_spans(needed, data_dir)

    out = ReliabilityReport(blueprint_id=blueprint.blueprint_id)
    for i in range(1, n_runs + 1):
        # A fresh cache dir per run. Sharing one would serve every run after the
        # first from cache and report flawless reliability off a single call.
        with tempfile.TemporaryDirectory(prefix=f"prepify_rel_{i}_") as tmp:
            tmp_path = Path(tmp)
            t0 = time.perf_counter()
            try:
                result = generate_exam(
                    specs=specs,
                    course_map=course_map,
                    span_text_by_id=span_text,
                    span_source_by_id=span_source,
                    llm_client=LLMClient(),   # fresh budget per run
                    output_dir=tmp_path,
                    cache_dir=tmp_path / "cache",
                    blueprint_id=blueprint.blueprint_id,
                    groundedness_scorer=groundedness_scorer,
                    embedding_fn=embedding_fn,
                )
            except Exception as exc:                     # noqa: BLE001
                # A run that dies is data, not an abort. E8.2 wants the failure
                # taxonomy, and provider errors belong in it.
                out.runs.append(RunOutcome(
                    run=i, items_generated=0, slots_total=len(specs), calls=0, tokens=0,
                    wall_clock=time.perf_counter() - t0, regeneration_passes=0,
                    flagged_slots=(), gates={}, issues=(), stems={},
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                ))
                say(f"  run {i}/{n_runs}: FAILED — {type(exc).__name__}")
                continue

            m = result.manifest
            outcome = RunOutcome(
                run=i,
                items_generated=len(result.items),
                slots_total=len(specs),
                calls=m.get("call_count", 0),
                tokens=m.get("token_count", 0),
                wall_clock=time.perf_counter() - t0,
                regeneration_passes=m.get("regeneration_passes", 0),
                flagged_slots=tuple(m.get("flagged_slots", [])),
                gates=m.get("validation", {}),
                issues=tuple(
                    (d.get("slot_id", "?"), d.get("gate", "?"), d.get("message", ""))
                    for d in m.get("validation_issues", [])
                ),
                stems={it.slot_id: it.stem for it in result.items},
            )
            out.runs.append(outcome)
            say(f"  run {i}/{n_runs}: {outcome.items_generated}/{outcome.slots_total} items, "
                f"{outcome.calls} calls, {outcome.wall_clock:.1f}s"
                + (f", flagged {list(outcome.flagged_slots)}" if outcome.flagged_slots else ""))
    return out


def _spread(values: list[float], pct: bool = True) -> str:
    if not values:
        return "not measured"
    fmt = (lambda v: f"{v:.1%}") if pct else (lambda v: f"{v:.1f}")
    if len(values) == 1:
        return f"{fmt(values[0])} (n=1)"
    return (f"{fmt(statistics.mean(values))}  "
            f"[{fmt(min(values))}–{fmt(max(values))}]  sd {fmt(statistics.stdev(values))}")


def format_report(rep: ReliabilityReport) -> str:
    out: list[str] = []
    w = out.append
    n = len(rep.runs)
    ok = len(rep.completed_runs)

    w("=" * 78)
    w("E8 - LLM RELIABILITY".center(78))
    w("=" * 78)
    w(f"  blueprint : {rep.blueprint_id}")
    w(f"  runs      : {ok}/{n} completed"
      + ("" if ok == n else f"   ({n - ok} died - see failures below)"))
    if not ok:
        w("  Nothing completed. No rates can be reported.")
        return "\n".join(out)

    w("")
    w("  E8.1 PER-GATE PASS RATE      mean   [min-max]   sd")
    w("  " + "-" * 62)
    for gate in GATES:
        w(f"    {gate:<24}{_spread(rep.gate_rates(gate))}")
    w(f"    {'items delivered / asked':<24}{_spread(rep.item_yield())}")

    w("")
    w("  E8.2 FAILURE TAXONOMY")
    w("  " + "-" * 62)
    tax = rep.failure_taxonomy()
    total_items = sum(o.items_generated for o in rep.completed_runs) or 1
    if not tax:
        w("    no gate failures across any completed run")
    for cause, count in tax.items():
        w(f"    {cause:<44}{count:>4}   ({100 * count / total_items:.1f} per 100 items)")
    for o in rep.runs:
        if o.error:
            w(f"    run {o.run} DIED: {o.error}")

    w("")
    w("  E8.3 REGENERATION")
    w("  " + "-" * 62)
    eff = rep.regeneration_effectiveness()
    w("    nothing was flagged; retry effectiveness not measured" if eff is None
      else f"    rescued {eff:.0%} of flagged items on the one permitted retry")

    w("")
    w("  E8.4 DETERMINISM  (cache bypassed)")
    w("  " + "-" * 62)
    det = rep.determinism()
    w("    not measured" if det is None
      else f"    {det:.0%} of slots produced an identical stem on every run")

    w("")
    w("  COST PER RUN")
    w("  " + "-" * 62)
    w(f"    calls     {_spread([float(o.calls) for o in rep.completed_runs], pct=False)}")
    w(f"    tokens    {_spread([float(o.tokens) for o in rep.completed_runs], pct=False)}")
    w(f"    seconds   {_spread([o.wall_clock for o in rep.completed_runs], pct=False)}")
    w(f"    total     {sum(o.calls for o in rep.completed_runs)} calls, "
      f"{sum(o.tokens for o in rep.completed_runs)} tokens")

    w("")
    w("=" * 78)
    w("  WHAT THIS DOES NOT MEASURE")
    w("=" * 78)
    w("  Whether any question is TRUE. The relevance gate scores topical overlap")
    w("  and cannot separate a true claim from a false one about the same span")
    w("  (disproven 2026-09-01). A 100% pass rate here means well-formed and")
    w("  on-topic. Quoting any figure above without this sentence overstates it.")
    return "\n".join(out)


def to_json(rep: ReliabilityReport) -> dict[str, Any]:
    return {
        "blueprint_id": rep.blueprint_id,
        "runs_total": len(rep.runs),
        "runs_completed": len(rep.completed_runs),
        "gate_pass_rates": {
            g: {
                "values": rep.gate_rates(g),
                "mean": statistics.mean(rep.gate_rates(g)) if rep.gate_rates(g) else None,
            }
            for g in GATES
        },
        "item_yield": rep.item_yield(),
        "failure_taxonomy": rep.failure_taxonomy(),
        "regeneration_effectiveness": rep.regeneration_effectiveness(),
        "determinism": rep.determinism(),
        "caveat": (
            "Measures well-formedness and topical relevance only. Nothing here "
            "establishes that any generated question is true."
        ),
        "runs": [
            {
                "run": o.run, "items": o.items_generated, "slots": o.slots_total,
                "calls": o.calls, "tokens": o.tokens,
                "wall_clock": round(o.wall_clock, 2),
                "flagged": list(o.flagged_slots), "error": o.error,
            }
            for o in rep.runs
        ],
    }


def write_report(rep: ReliabilityReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt = output_dir / "e8_reliability.txt"
    js = output_dir / "e8_reliability.json"
    txt.write_text(format_report(rep), encoding="utf-8")
    js.write_text(json.dumps(to_json(rep), indent=2, sort_keys=True), encoding="utf-8")
    return txt, js
