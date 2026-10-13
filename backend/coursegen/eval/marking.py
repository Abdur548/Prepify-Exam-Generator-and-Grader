"""E10 — how far the model's marks on written answers can be trusted (§13.1).

    python -m coursegen.eval marking --runs 3          # SPENDS LIVE QUOTA (~15 calls)

## The set

`marking_set.csv`: six generic AI questions, five answers each — full, partial,
wrong, vague, and adversarial (answers that try to talk the marker into full
marks, or to break out of their own block). Written for this harness, not drawn
from any course material, so it can live in a public repository.

**The `hand_mark` column ships blank, and the harness refuses to run until a
person on the team fills it in.** The agreement it reports is agreement with
that person's judgement; a column filled in by anyone else, or by a model, would
make the number measure nothing.

## Two verdicts

- **agreement** — HOLDS when at least 80% of model marks land within ±1 of the
  hand mark. FALSIFIED otherwise: every written mark is then to be shown as low
  confidence.
- **badge** — INFORMATIVE when low-confidence marks disagree with the hand mark
  (by more than 1) more often than high-confidence ones. REMOVE THE BADGE
  otherwise: a label that does not predict disagreement carries no information.

Each answer is marked `--runs` times, and the spread between runs is reported:
the same answer receiving different marks is a finding in itself.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coursegen.grade.records import WrittenEntry, WrittenMark

SHIPPED_SET = Path(__file__).resolve().parent / "marking_set.csv"
AGREEMENT_FLOOR = 0.8


@dataclass(frozen=True)
class HandMarked:
    entry: WrittenEntry
    hand_mark: int
    label: str


def load_set(path: Path, *, require_hand_marks: bool = True) -> list[HandMarked]:
    rows = []
    blank = 0
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            marks = int(r["marks_available"])
            raw = (r.get("hand_mark") or "").strip()
            if not raw:
                blank += 1
                hand = -1
            else:
                hand = int(raw)
                if not 0 <= hand <= marks:
                    raise ValueError(
                        f"{r['slot_id']}: hand mark {hand} is outside 0..{marks}."
                    )
            rows.append(HandMarked(
                entry=WrittenEntry(
                    slot_id=r["slot_id"], item_type=r["item_type"], marks_available=marks,
                    stem=r["stem"], model_answer=r["model_answer"],
                    explanation=r["explanation"], passage=r["passage"] or None,
                    from_material=bool(r["passage"]), response=r["response"],
                ),
                hand_mark=hand, label=r["label"],
            ))
    if require_hand_marks and blank:
        raise ValueError(
            f"{blank} of {len(rows)} answers have no hand mark yet. A person on the team "
            f"must fill in the hand_mark column of {Path(path).name} before this runs."
        )
    return rows


def aggregate(rows: list[HandMarked], runs: list[list[WrittenMark]]) -> dict[str, Any]:
    hand = {r.entry.slot_id: r.hand_mark for r in rows}
    diffs: list[int] = []
    unmarked = 0
    by_conf: dict[str, list[bool]] = {"high": [], "low": []}
    per_slot: dict[str, list[int]] = {}
    for run in runs:
        for mark in run:
            if mark.marks_awarded is None:
                unmarked += 1
                continue
            d = abs(mark.marks_awarded - hand[mark.slot_id])
            diffs.append(d)
            per_slot.setdefault(mark.slot_id, []).append(mark.marks_awarded)
            if mark.confidence in by_conf:
                by_conf[mark.confidence].append(d > 1)
    within = sum(1 for d in diffs if d <= 1) / len(diffs) if diffs else 0.0
    spreads = [max(v) - min(v) for v in per_slot.values()]
    rate = {c: (sum(v) / len(v) if v else None) for c, v in by_conf.items()}
    if rate["low"] is None or rate["high"] is None:
        badge = "NOT JUDGEABLE"
    else:
        badge = "INFORMATIVE" if rate["low"] > rate["high"] else "REMOVE THE BADGE"
    return {
        "marks_scored": len(diffs),
        "unmarked": unmarked,
        "within_one": within,
        "mean_abs_diff": sum(diffs) / len(diffs) if diffs else 0.0,
        "mean_spread": sum(spreads) / len(spreads) if spreads else 0.0,
        "max_spread": max(spreads) if spreads else 0,
        "disagreement_high": rate["high"],
        "disagreement_low": rate["low"],
        "agreement_verdict": "HOLDS" if within >= AGREEMENT_FLOOR else "FALSIFIED",
        "badge_verdict": badge,
    }


def format_report(result: dict[str, Any], *, runs: int) -> str:
    fmt = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
    return "\n".join([
        f"E10 — written-marking agreement ({runs} run{'s' if runs != 1 else ''})",
        f"  marks scored: {result['marks_scored']}, left unmarked: {result['unmarked']}",
        f"  within ±1 of the hand mark: {result['within_one']:.0%}  "
        f"(floor {AGREEMENT_FLOOR:.0%})  -> agreement {result['agreement_verdict']}",
        f"  mean absolute difference: {result['mean_abs_diff']:.2f} marks",
        f"  spread across runs: mean {result['mean_spread']:.2f}, max {result['max_spread']}",
        f"  disagreement (>1 mark): high-confidence {fmt(result['disagreement_high'])}, "
        f"low-confidence {fmt(result['disagreement_low'])}  -> badge {result['badge_verdict']}",
    ])


def run_live(rows: list[HandMarked], runs: int) -> list[list[WrittenMark]]:
    """Mark the whole set `runs` times against the real model.

    Its own budget: a submission's cap (`PER_SUBMISSION_CALL_CAP`) is sized for one
    paper's written answers, and thirty answers are five batches before retries.
    """
    from coursegen import config
    from coursegen.grade.written import mark_written
    from coursegen.llm.client import LLMClient, TokenBudget

    batches = -(-len(rows) // config.GRADE_BATCH_SIZE)
    out = []
    for _ in range(runs):
        client = LLMClient(budget=TokenBudget(per_exam_call_cap=batches + len(rows)))
        outcome = mark_written([r.entry for r in rows], client)
        if outcome.degraded:
            raise RuntimeError(f"Marking run stopped early: {outcome.degraded}")
        out.append(outcome.marks)
    return out
