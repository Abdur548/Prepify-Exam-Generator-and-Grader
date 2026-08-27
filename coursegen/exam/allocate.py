"""
Deterministic allocation solver — THE differentiator. Zero LLM calls.

Implements largest-remainder (Hare quota) apportionment per §9.3.

Coverage is a PROVABLE precondition here: every slot is assigned before
any LLM call is made. The solver emits ItemSpec[] that code has decided,
and the LLM only phrases what code specifies.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Optional

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import ItemSpec
from coursegen.exam.coverage import build_report


def solve(
    course_map: list[CourseMapNode],
    blueprint: Blueprint,
) -> tuple[list[ItemSpec], CoverageReport]:
    """
    Deterministic solver: identical inputs → byte-identical ItemSpec[].

    Hard invariants enforced here:
    - No span appears in more than one item across the entire paper (§9.3 step 5).
    - Span exhaustion produces warnings + unfilled slots, never a crash (R3).
    - Sections are SOLVED most-constrained-first but EMITTED in blueprint order.
    - No LLM calls. No randomness.
    """
    sorted_nodes = sorted(course_map, key=lambda n: n.node_id)

    # Shared span registry across all sections — enforces paper-wide uniqueness.
    used_spans: set[str] = set()

    all_items: list[ItemSpec] = []
    all_warnings: list[str] = []
    all_unfilled: list[str] = []

    node_slot_map: dict[str, list[str]] = defaultdict(list)
    node_marks_map: dict[str, int] = defaultdict(int)

    # Solve order: most-constrained-first. Sections carrying requires_flags_any are
    # solved before unconstrained ones, because an unconstrained section drains spans
    # from the highest-mass nodes and can leave a flag-filtered section span-starved
    # (final_default.json has exactly that shape). Sorting on
    # (0 if constrained else 1, original_index) is stable and deterministic:
    # constrained sections first, blueprint order preserved within each group.
    solve_order = sorted(
        range(len(blueprint.sections)),
        key=lambda i: (0 if blueprint.sections[i].requires_flags_any else 1, i),
    )

    solved: dict[int, tuple[list[ItemSpec], list[str], list[str]]] = {}
    for i in solve_order:
        solved[i] = _solve_section(
            section=blueprint.sections[i],
            sorted_nodes=sorted_nodes,
            used_spans=used_spans,
            node_slot_map=node_slot_map,
            node_marks_map=node_marks_map,
        )

    # Emission order: ORIGINAL blueprint position, then slot number within the section.
    # Solving order and emission order are two different things — the rendered paper
    # (P4) must still read A, B, C.
    for i in range(len(blueprint.sections)):
        items, warnings, unfilled = solved[i]
        all_items.extend(items)
        all_warnings.extend(warnings)
        all_unfilled.extend(unfilled)

    report = build_report(
        blueprint_id=blueprint.blueprint_id,
        course_map=course_map,
        node_slot_map=node_slot_map,
        node_marks_map=node_marks_map,
        unfilled_slots=all_unfilled,
        warnings=all_warnings,
        slots_total=sum(s.count for s in blueprint.sections),
    )
    return all_items, report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _solve_section(
    section: SectionSpec,
    sorted_nodes: list[CourseMapNode],
    used_spans: set[str],
    node_slot_map: dict[str, list[str]],
    node_marks_map: dict[str, int],
) -> tuple[list[ItemSpec], list[str], list[str]]:
    warnings: list[str] = []
    unfilled: list[str] = []

    # Step 1: filter candidates by required flags.
    candidates = [
        n for n in sorted_nodes
        if _node_matches_flags(n, section.requires_flags_any)
    ]

    if not candidates:
        warnings.append(
            f"Section {section.section_id!r}: no nodes match "
            f"requires_flags_any={section.requires_flags_any!r}; section skipped."
        )
        unfilled.extend(
            f"{section.section_id}-{i + 1:02d}" for i in range(section.count)
        )
        return [], warnings, unfilled

    # Step 2: renormalise mass over the candidate set.
    total_mass = sum(n.instructional_mass for n in candidates)
    if total_mass == 0:
        masses = {n.node_id: 1.0 / len(candidates) for n in candidates}
    else:
        masses = {n.node_id: n.instructional_mass / total_mass for n in candidates}

    # Step 3: Hare quota apportionment.
    allocation = _hare_apportionment(masses, section.count)

    # Mutable deficit counter for fill ordering.
    deficit: dict[str, int] = dict(allocation)

    # Available spans per candidate (excluding paper-wide used spans).
    available: dict[str, list[str]] = {
        n.node_id: [s for s in n.chunk_ids if s not in used_spans]
        for n in candidates
    }

    # Step 4: Fill slots by descending allocation deficit.
    assignments: list[tuple[CourseMapNode, list[str]]] = []
    node_by_id = {n.node_id: n for n in candidates}
    slots_remaining = section.count

    while slots_remaining > 0:
        chosen_nid = _pick_node(deficit, available, candidates)

        if chosen_nid is None:
            # Truly span-exhausted — remaining slots are unfilled.
            break

        # Decrement deficit only when filling from a deficit-allocated node.
        if deficit.get(chosen_nid, 0) > 0:
            deficit[chosen_nid] -= 1
        else:
            # Fallback node (deficit already zero).
            warnings.append(
                f"Section {section.section_id!r}: allocated nodes span-exhausted; "
                f"falling through to node {chosen_nid!r}."
            )

        span = available[chosen_nid].pop(0)
        used_spans.add(span)
        assignments.append((node_by_id[chosen_nid], [span]))
        slots_remaining -= 1

    # Record unfilled slots.
    filled = len(assignments)
    for i in range(filled, section.count):
        slot_id = f"{section.section_id}-{i + 1:02d}"
        unfilled.append(slot_id)
        warnings.append(f"Slot {slot_id!r} could not be filled: all spans exhausted.")

    # Step 6: difficulty ladder — ascending token_count within section.
    # This is a documented heuristic, not a pedagogical guarantee.
    assignments.sort(key=lambda a: (a[0].token_count, a[0].node_id))

    # Build ItemSpec list with deterministic slot IDs and bloom cycling.
    items: list[ItemSpec] = []
    bloom_list = section.bloom

    for idx, (node, spans) in enumerate(assignments):
        slot_id = f"{section.section_id}-{idx + 1:02d}"
        bloom = bloom_list[idx % len(bloom_list)]
        eligibility = [
            flag for flag in (section.requires_flags_any or [])
            if getattr(node.flags, flag, False)
        ]
        spec = ItemSpec(
            slot_id=slot_id,
            item_type=section.item_type,
            marks=section.marks_each,
            bloom=bloom,
            node_id=node.node_id,
            span_ids=spans,
            eligibility=eligibility,
            spec_hash=_spec_hash(
                node.node_id,
                spans,
                section.item_type,
                bloom,
                section.marks_each,
                section.options_count,
            ),
        )
        items.append(spec)
        node_slot_map[node.node_id].append(slot_id)
        node_marks_map[node.node_id] += section.marks_each

    return items, warnings, unfilled


def _pick_node(
    deficit: dict[str, int],
    available: dict[str, list[str]],
    candidates: list[CourseMapNode],
) -> Optional[str]:
    """
    Pick the best node to fill the next slot.

    Primary: highest deficit > 0 with available spans.
    Fallback: any node with available spans (if primary exhausted).
    Tie-break: ascending node_id for determinism.
    """
    # Primary: nodes still within their deficit allocation.
    primary = sorted(
        [nid for nid in deficit if deficit[nid] > 0 and available.get(nid)],
        key=lambda nid: (-deficit[nid], nid),
    )
    if primary:
        return primary[0]

    # Fallback: any candidate with remaining spans.
    fallback = sorted(
        [n.node_id for n in candidates if available.get(n.node_id)],
        key=lambda nid: nid,
    )
    return fallback[0] if fallback else None


def _hare_apportionment(masses: dict[str, float], total: int) -> dict[str, int]:
    """
    Largest-remainder (Hare quota) apportionment.
    Deterministic: ties broken by ascending node_id.
    """
    if total == 0:
        return {nid: 0 for nid in masses}

    total_mass = sum(masses.values())
    if total_mass == 0:
        # Uniform distribution over all nodes.
        base = total // len(masses)
        result = {nid: base for nid in masses}
        remainder = total - base * len(masses)
        for nid in sorted(masses.keys())[:remainder]:
            result[nid] += 1
        return result

    raw = {nid: (m / total_mass) * total for nid, m in masses.items()}
    bases = {nid: int(v) for nid, v in raw.items()}
    remainders = {nid: raw[nid] - bases[nid] for nid in masses}

    remaining = total - sum(bases.values())
    sorted_nids = sorted(masses.keys(), key=lambda nid: (-remainders[nid], nid))

    result = dict(bases)
    for nid in sorted_nids[:remaining]:
        result[nid] += 1

    return result


def _node_matches_flags(
    node: CourseMapNode,
    requires_flags_any: Optional[list[str]],
) -> bool:
    if not requires_flags_any:
        return True
    return any(getattr(node.flags, flag, False) for flag in requires_flags_any)


def _spec_hash(
    node_id: str,
    span_ids: list[str],
    item_type: str,
    bloom: str,
    marks: int,
    options_count: Optional[int],
) -> str:
    """LLM generation cache key.

    marks and options_count are part of the key because the cache only pays off
    ACROSS papers, and that is exactly where they differ: a 4-mark short question
    (midterm) and a 5-mark short question (final) can be drawn from the same
    node + span + bloom. Span-uniqueness only protects within a single paper.
    options_count is rendered as "" when None, so the encoding is stable.
    """
    raw = "|".join([
        node_id,
        ",".join(span_ids),
        item_type,
        bloom,
        str(marks),
        "" if options_count is None else str(options_count),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()
