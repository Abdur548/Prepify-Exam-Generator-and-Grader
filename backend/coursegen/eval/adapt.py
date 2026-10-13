"""E9 — what the adaptive tilt does to the next paper. No model calls, no quota.

    python -m coursegen.eval adapt

## The question

A student takes a paper. Does the next paper put more of its marks on material
they got wrong or have not yet been tested on than the same blueprint would
without their history? That is the whole claim behind "the next paper targets
your weak topics", and this harness is the only thing allowed to support it.

## How

For each blueprint, the untilted paper is solved and a simulated student answers
every question on it, under three profiles:

- `weak_two_lectures` — zero on everything from the two lectures carrying the most
  marks, full marks elsewhere;
- `uniform_50` — alternately zero and full marks, in slot order;
- `all_correct` — full marks throughout.

The solver is deterministic, so the untilted NEXT paper is the paper just taken.
It is compared with the tilted next paper on the share of marks landing on weak
(tested, mastery below `RETEST_BELOW`), untested and strong nodes. Repeats — a
question the student has seen, asked again unchanged — must be 0.

## Falsification

PASS when the tilted paper puts more marks on weak + untested material than the
untilted one; FAIL otherwise; N/A when the untilted paper is already entirely weak
or untested. A FAIL is a finding, not a bug to hide: the claim is then not made.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.item import ItemSpec
from coursegen.exam.allocate import solve
from coursegen.grade.mastery import NodeMastery, node_mastery
from coursegen.grade.records import AnswerRow
from coursegen.grade.tilt import avoid_repeats, mass_tilt

PROFILES = ("weak_two_lectures", "uniform_50", "all_correct")
CLASSES = ("weak", "untested", "strong")


@dataclass(frozen=True)
class AdaptRow:
    blueprint_id: str
    profile: str
    untilted: dict[str, float]
    tilted: dict[str, float]
    changed_slots: int
    reworded: int
    repeats: int
    deterministic: bool
    verdict: str


def history(profile: str, specs: list[ItemSpec], nodes: list[CourseMapNode]) -> list[AnswerRow]:
    """One simulated attempt at the paper `specs`, under `profile`."""
    lecture = {n.node_id: n.source_file for n in nodes}
    if profile == "weak_two_lectures":
        marks: dict[str, int] = {}
        for s in specs:
            marks[lecture[s.node_id]] = marks.get(lecture[s.node_id], 0) + s.marks
        weak = set(sorted(marks, key=lambda l: (-marks[l], l))[:2])
        right = [lecture[s.node_id] not in weak for s in specs]
    elif profile == "uniform_50":
        right = [i % 2 == 1 for i in range(len(specs))]
    elif profile == "all_correct":
        right = [True] * len(specs)
    else:
        raise ValueError(f"unknown profile {profile!r}")
    return [
        AnswerRow(
            attempt_id="simulated", seq=1, sheet_id="simulated-paper", sheet_kind="paper",
            slot_id=s.slot_id, node_id=s.node_id, item_type=s.item_type,
            marks_available=s.marks, response="simulated",
            marks_awarded=s.marks if ok else 0, source="key", confidence=None,
            reason=None, retest_of=None, original=None,
        )
        for s, ok in zip(specs, right)
    ]


def _class(node_id: str, mastery: dict[str, NodeMastery]) -> str:
    if node_id not in mastery:
        return "untested"
    return "weak" if mastery[node_id].mastery < config.RETEST_BELOW else "strong"


def _shares(specs: list[ItemSpec], mastery: dict[str, NodeMastery]) -> dict[str, float]:
    total = sum(s.marks for s in specs)
    out = {c: 0.0 for c in CLASSES}
    for s in specs:
        out[_class(s.node_id, mastery)] += s.marks
    return {c: (v / total if total else 0.0) for c, v in out.items()}


def _next_paper(nodes, blueprint, base_specs, mastery):
    tilt = mass_tilt(mastery, {n.node_id for n in nodes})
    specs, _ = solve(nodes, blueprint, mass_tilt=tilt or None)
    # Synthetic stems: what matters here is that every base question counts as seen.
    seen_stems = {}
    for s in base_specs:
        seen_stems.setdefault(s.node_id, []).append(f"Seen question {s.slot_id}")
    return avoid_repeats(specs, {s.spec_hash for s in base_specs}, seen_stems)


def evaluate(nodes: list[CourseMapNode], blueprint: Blueprint) -> list[AdaptRow]:
    base_specs, _ = solve(nodes, blueprint)
    seen = {s.spec_hash for s in base_specs}
    out = []
    for profile in PROFILES:
        mastery = node_mastery(history(profile, base_specs, nodes))
        tilted, reworded = _next_paper(nodes, blueprint, base_specs, mastery)
        again, _ = _next_paper(nodes, blueprint, base_specs, mastery)
        before, after = _shares(base_specs, mastery), _shares(tilted, mastery)
        b, a = before["weak"] + before["untested"], after["weak"] + after["untested"]
        verdict = "N/A" if b >= 1.0 - 1e-9 else ("PASS" if a > b else "FAIL")
        base_node = {s.slot_id: s.node_id for s in base_specs}
        out.append(AdaptRow(
            blueprint_id=blueprint.blueprint_id, profile=profile,
            untilted=before, tilted=after,
            changed_slots=sum(1 for s in tilted if base_node.get(s.slot_id) != s.node_id),
            reworded=reworded,
            repeats=sum(1 for s in tilted if s.spec_hash in seen),
            deterministic=[s.model_dump() for s in tilted] == [s.model_dump() for s in again],
            verdict=verdict,
        ))
    return out


def format_report(results: dict[str, list[AdaptRow]], *, map_label: str) -> str:
    lines = [
        "E9 — the adaptive next paper (no model calls)",
        f"Course map: {map_label}",
        "",
        "Share of the next paper's marks on weak / untested / strong material.",
        "PASS: tilted puts more on weak+untested than untilted. Repeats must be 0.",
        "",
        f"{'blueprint':20} {'profile':18} {'untilted w/u/s':>17} {'tilted w/u/s':>17} "
        f"{'moved':>5} {'reword':>6} {'rep':>3}  verdict",
    ]
    for blueprint_id, rows in results.items():
        for r in rows:
            fmt = lambda d: "/".join(f"{d[c]:.2f}" for c in CLASSES)  # noqa: E731
            lines.append(
                f"{blueprint_id:20} {r.profile:18} {fmt(r.untilted):>17} {fmt(r.tilted):>17} "
                f"{r.changed_slots:>5} {r.reworded:>6} {r.repeats:>3}  {r.verdict}"
                + ("" if r.deterministic else "  NON-DETERMINISTIC")
            )
    verdicts = [r.verdict for rows in results.values() for r in rows]
    lines += ["", f"PASS {verdicts.count('PASS')}, FAIL {verdicts.count('FAIL')}, "
                  f"N/A {verdicts.count('N/A')} of {len(verdicts)}"]
    return "\n".join(lines)


def to_json(results: dict[str, list[AdaptRow]], *, map_label: str) -> dict[str, Any]:
    return {"map": map_label,
            "blueprints": {bp: [asdict(r) for r in rows] for bp, rows in results.items()}}


def write_report(results: dict[str, list[AdaptRow]], output_dir: Path, *, map_label: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt, js = output_dir / "e9_adapt.txt", output_dir / "e9_adapt.json"
    txt.write_text(format_report(results, map_label=map_label) + "\n", encoding="utf-8")
    js.write_text(json.dumps(to_json(results, map_label=map_label), indent=2, sort_keys=True),
                  encoding="utf-8")
    return txt, js
