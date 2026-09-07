"""`baseline_naive` — the control arm P6 measures the solver against.

The product's central claim is that **code decides what to ask**, by
`instructional_mass`, and the model only writes the words. That claim is only
worth anything if a system that does NOT do it performs worse. This module is
that system.

## What it ignores, and what it must not

A control that differs from the solver in many ways at once proves nothing: if
this arm loses, you cannot say which difference caused it. So this deliberately
differs in **exactly one variable — how a node is chosen for a slot.**

Ignored (these are the thesis under test):

- `instructional_mass` and largest-remainder apportionment — nodes are drawn
  uniformly at random instead.
- Topic matching — an authored blueprint's `topic` strings are not consulted.
- Most-constrained-first section ordering.
- The ascending-`token_count` difficulty ladder.

Preserved (these are blueprint *requirements*, not solver cleverness):

- Section shape: counts, `item_type`, `marks_each`. A paper of a different shape
  is not a comparable paper.
- Span uniqueness across the whole paper. A paper that asks two questions from
  one span is invalid, not naive.
- `requires_flags_any`. Ignoring it would produce a paper that fails the
  blueprint, and beating an invalid paper proves nothing. See `respect_flags`
  if you want to measure that weaker claim separately.
- The Bloom ladder, which is a function of the section and the number of slots
  filled — not of which node was picked. Varying it would confound
  `bloom_realised` with span selection.

## Determinism

Random, but reproducible: seeded from `(seed, blueprint_id, section_id)`, so the
same inputs give byte-identical output and P6 numbers can be re-derived. `Random`
hashes a str seed with SHA-512, so this is stable across processes and unaffected
by PYTHONHASHSEED — the same property `_shuffle_options` relies on.

## Expected `allocation_fidelity`

**0.0, by construction.** `allocation_fidelity` is the share of slots placed by
mass, and this arm places none that way. That is not a bug to fix; it is the
number that makes the comparison legible. When the solver reports 0.62 and this
reports 0.00, the difference in coverage is attributable.
"""
from __future__ import annotations

import random
from collections import defaultdict

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import ItemSpec
from coursegen.exam.allocate import _bloom_ladder, _spec_hash
from coursegen.exam.coverage import build_report

BASELINE_ID = "baseline_naive"
DEFAULT_SEED = 42


def solve_naive(
    course_map: list[CourseMapNode],
    blueprint: Blueprint,
    seed: int = DEFAULT_SEED,
    respect_flags: bool = True,
) -> tuple[list[ItemSpec], CoverageReport]:
    """Fill the blueprint by uniform random span selection.

    Signature and return type mirror `exam.allocate.solve` exactly, so an eval
    harness can swap arms without special-casing either one.

    `respect_flags=False` additionally ignores `requires_flags_any`, which tests a
    strictly weaker claim ("does flag filtering matter?") and produces a paper
    that does not satisfy its own blueprint. Off by default for that reason.
    """
    sorted_nodes = sorted(course_map, key=lambda n: n.node_id)
    node_by_id = {n.node_id: n for n in sorted_nodes}

    used_spans: set[str] = set()
    all_items: list[ItemSpec] = []
    all_warnings: list[str] = []
    all_unfilled: list[str] = []

    node_slot_map: dict[str, list[str]] = defaultdict(list)
    node_marks_map: dict[str, int] = defaultdict(int)

    # Blueprint order, not most-constrained-first. The solver reorders because an
    # unconstrained section otherwise drains the highest-mass nodes and starves a
    # flag-filtered one; declining to do that is part of being naive, and the
    # starvation it causes is a real cost this arm should be seen to pay.
    for section in blueprint.sections:
        items, warnings, unfilled = _fill_section(
            section=section,
            sorted_nodes=sorted_nodes,
            node_by_id=node_by_id,
            used_spans=used_spans,
            node_slot_map=node_slot_map,
            node_marks_map=node_marks_map,
            rng=random.Random(f"{seed}:{blueprint.blueprint_id}:{section.section_id}"),
            respect_flags=respect_flags,
        )
        all_items.extend(items)
        all_warnings.extend(warnings)
        all_unfilled.extend(unfilled)

    report = build_report(
        blueprint_id=BASELINE_ID,
        course_map=course_map,
        node_slot_map=node_slot_map,
        node_marks_map=node_marks_map,
        unfilled_slots=all_unfilled,
        warnings=all_warnings,
        slots_total=sum(s.count for s in blueprint.sections),
        # Nothing here is placed by mass. Reporting these honestly is what makes
        # allocation_fidelity 0.0 rather than silently undefined.
        slots_by_mass=0,
        slots_by_fallthrough=len(all_items),
        per_topic=None,
        items=all_items,
        cognitive_balance=blueprint.cognitive_balance,
    )
    return all_items, report


def _fill_section(
    section: SectionSpec,
    sorted_nodes: list[CourseMapNode],
    node_by_id: dict[str, CourseMapNode],
    used_spans: set[str],
    node_slot_map: dict[str, list[str]],
    node_marks_map: dict[str, int],
    rng: random.Random,
    respect_flags: bool,
) -> tuple[list[ItemSpec], list[str], list[str]]:
    warnings: list[str] = []
    unfilled: list[str] = []

    eligible = sorted_nodes
    if respect_flags and section.requires_flags_any:
        eligible = [
            n for n in sorted_nodes
            if any(getattr(n.flags, f, False) for f in section.requires_flags_any)
        ]

    # Spans still free, per node. Sorted for determinism before any draw.
    available: dict[str, list[str]] = {
        n.node_id: [c for c in sorted(n.chunk_ids) if c not in used_spans]
        for n in eligible
    }
    available = {k: v for k, v in available.items() if v}

    assignments: list[tuple[CourseMapNode, list[str]]] = []
    for _ in range(section.count):
        candidates = sorted(k for k, v in available.items() if v)
        if not candidates:
            break
        # The one variable under test: uniform over nodes, mass ignored entirely.
        nid = candidates[rng.randrange(len(candidates))]
        span = available[nid].pop(0)
        used_spans.add(span)
        if not available[nid]:
            del available[nid]
        assignments.append((node_by_id[nid], [span]))

    filled = len(assignments)
    for i in range(filled, section.count):
        slot_id = f"{section.section_id}-{i + 1:02d}"
        unfilled.append(slot_id)
        warnings.append(f"Slot {slot_id!r} could not be filled: all spans exhausted.")

    # No ascending-token_count sort: the difficulty ladder is a solver heuristic.
    items: list[ItemSpec] = []
    bloom_by_slot = _bloom_ladder(section, filled)

    for idx, (node, spans) in enumerate(assignments):
        slot_id = f"{section.section_id}-{idx + 1:02d}"
        bloom = bloom_by_slot[idx]
        eligibility = [
            flag for flag in (section.requires_flags_any or [])
            if getattr(node.flags, flag, False)
        ]
        items.append(ItemSpec(
            slot_id=slot_id,
            item_type=section.item_type,
            marks=section.marks_each,
            bloom=bloom,
            node_id=node.node_id,
            span_ids=spans,
            eligibility=eligibility,
            format_requirement=section.format_requirement,
            group_id=section.group_id,
            grounding=section.grounding,
            generation_instructions=section.generation_instructions,
            # Same hash function as the solver, deliberately. A baseline item over
            # the same span with the same shape SHOULD collide with the solver's
            # cache entry — that is a saved call, not a leak, because spec_hash
            # covers everything the prompt is built from.
            spec_hash=_spec_hash(
                node.node_id,
                spans,
                section.item_type,
                bloom,
                section.marks_each,
                section.options_count,
                section.format_requirement,
                section.grounding,
                section.generation_instructions,
            ),
        ))
        node_slot_map[node.node_id].append(slot_id)
        node_marks_map[node.node_id] += section.marks_each

    return items, warnings, unfilled
