"""
Deterministic allocation solver — THE differentiator. Zero LLM calls.

Implements largest-remainder (Hare quota) apportionment per §9.3.

Coverage is a PROVABLE precondition here: every slot is assigned before
any LLM call is made. The solver emits ItemSpec[] that code has decided,
and the LLM only phrases what code specifies.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Optional

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.coverage import CoverageReport, TopicCoverage
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
    all_by_mass = 0
    all_by_fallthrough = 0

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

    solved: dict[
        int,
        tuple[list[ItemSpec], list[str], list[str], int, int, Optional[TopicCoverage]],
    ] = {}
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
    all_per_topic: list[TopicCoverage] = []
    for i in range(len(blueprint.sections)):
        items, warnings, unfilled, by_mass, by_fallthrough, topic_coverage = solved[i]
        all_items.extend(items)
        all_warnings.extend(warnings)
        all_unfilled.extend(unfilled)
        all_by_mass += by_mass
        all_by_fallthrough += by_fallthrough
        # None on the derived path, so per_topic stays empty for topic-free blueprints.
        if topic_coverage is not None:
            all_per_topic.append(topic_coverage)

    report = build_report(
        blueprint_id=blueprint.blueprint_id,
        course_map=course_map,
        node_slot_map=node_slot_map,
        node_marks_map=node_marks_map,
        unfilled_slots=all_unfilled,
        warnings=all_warnings,
        slots_total=sum(s.count for s in blueprint.sections),
        slots_by_mass=all_by_mass,
        slots_by_fallthrough=all_by_fallthrough,
        per_topic=all_per_topic,
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
) -> tuple[
    list[ItemSpec], list[str], list[str], int, int, Optional[TopicCoverage]
]:
    """Returns (items, warnings, unfilled, slots_by_mass, slots_by_fallthrough,
    topic_coverage).

    slots_by_mass / slots_by_fallthrough are measurement only. They are counted
    where the fill decision is already made and change nothing about how it is
    made.

    topic_coverage is None unless the section carries an authored `topic`.
    """
    warnings: list[str] = []
    unfilled: list[str] = []
    by_mass = 0
    by_fallthrough = 0

    # Topic bookkeeping. Stays at these values unless the section authors a topic,
    # and these are exactly what an empty candidate set must report.
    topic_matched_ids: list[str] = []
    topic_best_score: float = 0.0

    def _topic_row(slots_filled: int) -> Optional[TopicCoverage]:
        """None on the derived path; one row per authored topic otherwise."""
        if section.topic is None:
            return None
        return TopicCoverage(
            topic=section.topic,
            section_id=section.section_id,
            matched_node_ids=topic_matched_ids,
            matched_node_count=len(topic_matched_ids),
            best_score=topic_best_score,
            slots_requested=section.count,
            slots_filled=slots_filled,
        )

    def _unfilled_section() -> tuple[
        list[ItemSpec], list[str], list[str], int, int, Optional[TopicCoverage]
    ]:
        """No candidates: every slot is reported unfilled, nothing is invented.

        Deliberately NO fallback to the unfiltered course map. Refilling from
        nodes the filters excluded would produce questions about material the
        section did not ask for — silently, and looking exactly like a complete
        paper. That is the failure this design exists to prevent.
        """
        unfilled.extend(
            f"{section.section_id}-{i + 1:02d}" for i in range(section.count)
        )
        return [], warnings, unfilled, by_mass, by_fallthrough, _topic_row(0)

    # Scored before either filter runs, so a malformed topic raises whatever the
    # flags do — the failure must not depend on which filter empties the set first.
    # Empty dict on the derived path; nothing downstream reads it.
    scores: dict[str, float] = (
        {} if section.topic is None else _topic_scores(section.topic, sorted_nodes)
    )

    # Step 1a: filter candidates by required flags.
    candidates = [
        n for n in sorted_nodes
        if _node_matches_flags(n, section.requires_flags_any)
    ]

    if not candidates:
        # The flag filter emptied the set. Reported BEFORE the topic filter runs,
        # so the warning names the filter that actually did it: "no nodes carry
        # flag Y" and "no nodes matched topic X" are different problems with
        # different fixes, and a merged message helps nobody.
        warnings.append(
            f"Section {section.section_id!r}: no nodes match "
            f"requires_flags_any={section.requires_flags_any!r}; section skipped."
        )
        return _unfilled_section()

    # Step 1b: filter the survivors by topic, when the blueprint authored one.
    # section.topic is None on the derived path — no filter, candidates stay the
    # whole (flag-filtered) course map, allocation mass-proportional across
    # everything, exactly as before this field existed.
    if section.topic is not None:
        # Applied to the flag survivors only, so matched_node_ids reports the
        # candidate set the allocation actually drew from — not nodes that score
        # well on the topic but were already excluded by the flags.
        candidates = [
            n for n in candidates
            if scores[n.node_id] >= config.TOPIC_MATCH_MIN_SCORE
        ]
        topic_matched_ids = [n.node_id for n in candidates]
        topic_best_score = max(
            (scores[nid] for nid in topic_matched_ids), default=0.0
        )

        if not candidates:
            warnings.append(
                f"Section {section.section_id!r}: no nodes matched topic "
                f"{section.topic!r} at score >= {config.TOPIC_MATCH_MIN_SCORE}; "
                f"section skipped. The uploaded material does not appear to cover "
                f"this topic — its slots are left unfilled rather than filled from "
                f"elsewhere in the course map."
            )
            return _unfilled_section()

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
            by_mass += 1
        else:
            # Fallback node (deficit already zero).
            by_fallthrough += 1
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

    return items, warnings, unfilled, by_mass, by_fallthrough, _topic_row(filled)


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


# Alphanumeric runs, underscore excluded. Unicode-aware, so accented headings
# survive tokenisation instead of being shredded into single letters.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokenise(text: str) -> set[str]:
    """Case-folded alphanumeric tokens, short ones dropped.

    The length threshold is doing the work a stopword list would — the pinned
    stack has no stopword list and this must not add a dependency for one. It
    also drops heading numbering ("3.2" → "3", "2") and one-letter algorithm
    names ("A*" → "a"), which is the intended cost.
    """
    return {
        tok for tok in _TOKEN_RE.findall(text.casefold())
        if len(tok) >= config.TOPIC_MATCH_MIN_TOKEN_LEN
    }


def _topic_scores(topic: str, nodes: list[CourseMapNode]) -> dict[str, float]:
    """node_id → fraction of the TOPIC's tokens that the node carries.

    A node's tokens come from its heading `path` plus its YAKE `key_terms`,
    tokenised the same way as the topic.

    score = |topic_tokens ∩ node_tokens| / |topic_tokens|

    Deliberately NOT Jaccard. The question is "does this node cover the topic",
    not "are these two the same size", so a long node must not be penalised for
    holding many tokens.

    An empty topic token set RAISES: a topic made only of short words cannot
    discriminate anything, and dividing by zero — or treating it as a match on
    everything — would hand the section the whole course map while looking like
    a successful topic match. That is a malformed blueprint and it must be fixed
    by its author, not papered over here. Raised rather than asserted because
    `python -O` strips asserts.
    """
    topic_tokens = _tokenise(topic)
    if not topic_tokens:
        raise ValueError(
            f"Blueprint topic {topic!r} contains no usable tokens: every token is "
            f"shorter than TOPIC_MATCH_MIN_TOKEN_LEN="
            f"{config.TOPIC_MATCH_MIN_TOKEN_LEN}. A topic that cannot be tokenised "
            f"cannot select candidate nodes; fix the topic in the blueprint."
        )
    return {
        n.node_id: len(
            topic_tokens & _tokenise(" ".join(list(n.path) + list(n.key_terms)))
        ) / len(topic_tokens)
        for n in nodes
    }


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
