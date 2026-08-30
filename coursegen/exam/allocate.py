"""
Deterministic allocation solver — THE differentiator. Zero LLM calls.

Implements largest-remainder (Hare quota) apportionment per §9.3.

Coverage is a PROVABLE precondition here: every slot is assigned before
any LLM call is made. The solver emits ItemSpec[] that code has decided,
and the LLM only phrases what code specifies.
"""
from __future__ import annotations

import hashlib
import math
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
        # The REALISED Bloom mix is measured from the items actually emitted and
        # compared against the blueprint's declared target. It cannot be a
        # Blueprint validator: how many slots each section fills is an allocation
        # outcome, unknown at parse time. A None target means no comparison.
        items=all_items,
        cognitive_balance=blueprint.cognitive_balance,
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
        # well on the topic but were already excluded by the flags. `best` is
        # taken over those same survivors for the same reason: ranking against a
        # node the flags already removed would admit on evidence this section can
        # never draw from.
        best = max((scores[n.node_id] for n in candidates), default=0.0)

        if best <= 0.0:
            # Zero shared vocabulary with every candidate. Kept as its own branch
            # rather than folded into the evidence check below, because it stays
            # correct if TOPIC_MATCH_MIN_EVIDENCE is ever calibrated down to 0:
            # `0 >= TOPIC_MATCH_RELATIVE_FLOOR * 0` is true, so a relative-only
            # rule would admit the ENTIRE candidate set on no evidence at all.
            warnings.append(
                f"Section {section.section_id!r}: no nodes matched topic "
                f"{section.topic!r} — zero token overlap with any candidate node; "
                f"section skipped. The uploaded material does not appear to cover "
                f"this topic — its slots are left unfilled rather than filled from "
                f"elsewhere in the course map."
            )
            return _unfilled_section()

        if best < config.TOPIC_MATCH_MIN_EVIDENCE:
            # "Best of a bad lot". A purely relative rule ALWAYS admits the
            # argmax — `mass >= 0.5 * best` is trivially true for it — so without
            # this floor a topic sharing one throwaway term with one node would
            # quietly take that node's spans and print a complete-looking paper.
            warnings.append(
                f"Section {section.section_id!r}: no nodes matched topic "
                f"{section.topic!r} — best matched evidence {best:.3f} is below "
                f"TOPIC_MATCH_MIN_EVIDENCE={config.TOPIC_MATCH_MIN_EVIDENCE}; "
                f"section skipped. The closest node matched only on terms too "
                f"common to carry evidence — its slots are left unfilled rather "
                f"than filled from elsewhere in the course map."
            )
            return _unfilled_section()

        # Ranked against the best match rather than against an absolute score:
        # IDF mass scales with corpus size (idf depends on N) and with topic
        # length, so an absolute-only cut calibrated on one course map would
        # drift on the next. The floor above is what stops the ranking from
        # admitting a whole set of equally-worthless matches.
        candidates = [
            n for n in candidates
            if scores[n.node_id] >= config.TOPIC_MATCH_RELATIVE_FLOOR * best
        ]
        topic_matched_ids = [n.node_id for n in candidates]
        topic_best_score = max(
            (scores[nid] for nid in topic_matched_ids), default=0.0
        )

        # Structural postcondition, not a taste check: `best` is the max over the
        # candidates, so the node achieving it satisfies `mass >= floor * best`
        # for any floor in [0, 1] and the set CANNOT be empty here. If it ever is,
        # the floor has been configured outside that range and the section would
        # silently report "no material for this topic" about material that is
        # there. Raised rather than asserted — `python -O` strips asserts.
        if not candidates:
            raise ValueError(
                f"Section {section.section_id!r}: topic {section.topic!r} scored "
                f"{best:.3f} on its best candidate yet admitted no nodes at "
                f"TOPIC_MATCH_RELATIVE_FLOOR="
                f"{config.TOPIC_MATCH_RELATIVE_FLOOR}. The relative floor must be "
                f"a fraction in [0, 1]."
            )

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

    # Build ItemSpec list with deterministic slot IDs and per-slot bloom levels.
    items: list[ItemSpec] = []
    bloom_by_slot = _bloom_ladder(section, filled)

    for idx, (node, spans) in enumerate(assignments):
        slot_id = f"{section.section_id}-{idx + 1:02d}"
        bloom = bloom_by_slot[idx]
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
            format_requirement=section.format_requirement,
            # Carried through unchanged: the N items a sub-questioned task
            # expands into share the section's group_id, and the renderer will
            # later group them under one question number.
            group_id=section.group_id,
            # Carried through unchanged. "span" on every item the three shipped
            # blueprints produce, which is the default and current behaviour.
            grounding=section.grounding,
            generation_instructions=section.generation_instructions,
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
        )
        items.append(spec)
        node_slot_map[node.node_id].append(slot_id)
        node_marks_map[node.node_id] += section.marks_each

    return items, warnings, unfilled, by_mass, by_fallthrough, _topic_row(filled)


def _bloom_ladder(section: SectionSpec, filled: int) -> list[str]:
    """Bloom level for each filled slot, in slot order.

    Without `bloom_mix` this is EXACTLY the round-robin that shipped before the
    field existed — `section.bloom[idx % len(section.bloom)]`. Absence means
    current behaviour, and that is the regression guard for the three shipped
    blueprints, none of which authors a mix.

    With `bloom_mix` the proportions are apportioned to integer counts by the
    SAME `_hare_apportionment` the solver uses for nodes. Reusing it — rather
    than writing a second apportionment — keeps the tie-breaking rule identical
    and the result deterministic. One inherited consequence worth stating: that
    function breaks ties on ASCENDING KEY, so two levels holding equal
    proportions are separated alphabetically by level name, not by their order
    in `section.bloom`.

    ORDER — the levels are emitted GROUPED, in `section.bloom` order: every slot
    of the first-listed level, then every slot of the second, and so on. Two
    reasons. `section.bloom` is the authored order, so the result does not depend
    on the JSON key order of `bloom_mix`. And `assignments` has already been
    sorted ascending by token_count (the difficulty ladder), so a blueprint that
    lists its levels ascending — as all three shipped ones do — gets its Bloom
    ladder running the same way as its length ladder.

    Apportionment is over `section.count`, per the amendment, NOT over the number
    of slots that actually filled. When a section fills short it is the TAIL of
    the ladder that goes missing: the first `filled` levels are used and the rest
    dropped. A short section has already failed its declared mix — the
    cognitive_balance comparison in the CoverageReport is what reports that, and
    it warns rather than raising.
    """
    if section.bloom_mix is None:
        return [section.bloom[i % len(section.bloom)] for i in range(filled)]

    counts = _hare_apportionment(dict(section.bloom_mix), section.count)
    # Keys are a subset of section.bloom (SectionSpec validates that), so every
    # apportioned slot lands in the ladder and unlisted levels contribute zero.
    ladder = [
        level for level in section.bloom for _ in range(counts.get(level, 0))
    ]
    # Structural postcondition, not a taste check: apportionment must place
    # exactly `count` slots. A shorter ladder would silently hand later slots the
    # wrong level (or IndexError); a longer one would drop levels the mix asked
    # for. Either way the paper would look like the blueprint was honoured.
    # Raised, not asserted — `python -O` strips asserts.
    if len(ladder) != section.count:
        raise ValueError(
            f"Section {section.section_id!r}: bloom_mix {section.bloom_mix!r} "
            f"apportioned to {len(ladder)} slots for a section of "
            f"{section.count}. Proportions must be non-negative and describe a "
            f"usable mix."
        )
    return ladder[:filled]


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

    The length threshold drops heading numbering ("3.2" → "3", "2") and
    one-letter algorithm names ("A*" → "a"), which is the intended cost.

    It is NOT sufficient alone, and raising it is not the fix. Under matched IDF
    mass a function word that happens to be RARE in a small course map is
    weighted UP, not down: measured on the 20-node fixture, "and" occurs in one
    heading, scores idf 3.351, and on that alone admitted "1.3 Variables and
    Scope" for the topic "Adversarial Search and Minimax with Alpha-Beta
    Pruning" — tying the genuinely relevant "3.2 Binary Search". A topic that
    half-matches is worse than one that does not match at all, because it draws
    questions from the wrong nodes while looking like it worked.

    Raising the threshold to 4 would kill "and"/"the"/"for" but also "MDP",
    "CSP", "BFS", "DFS", "ID3" — exactly the tokens a technical syllabus leans
    on. So the function words are named explicitly instead. The list is English
    structure only, carries no subject assumption (C5), and needs no dependency.
    """
    return {
        tok for tok in _TOKEN_RE.findall(text.casefold())
        if len(tok) >= config.TOPIC_MATCH_MIN_TOKEN_LEN
        and tok not in config.TOPIC_STOPWORDS
    }


def _node_tokens(node: CourseMapNode) -> set[str]:
    """A node's vocabulary: its heading `path` plus its YAKE `key_terms`.

    Tokenised exactly as the topic is, so the two sides are comparable.
    """
    return _tokenise(" ".join(list(node.path) + list(node.key_terms)))


def _idf(nodes: list[CourseMapNode]) -> dict[str, float]:
    """token → smoothed inverse document frequency over the course map.

        df(t)  = number of nodes whose (path + key_terms) tokens contain t
        idf(t) = ln((N + 1) / (df(t) + 1)) + 1        # N = node count

    Smoothed on both sides and floored by the +1, so idf is ALWAYS > 0: a token
    every node carries still contributes a little evidence rather than none, and
    no arrangement of the corpus can make a matched term subtract.

    Only tokens the corpus actually contains get an entry. A topic token with
    df = 0 can never appear in an intersection, so it never needs a value.
    """
    n_total = len(nodes)
    df: dict[str, int] = defaultdict(int)
    for node in nodes:
        for tok in _node_tokens(node):
            df[tok] += 1
    return {
        tok: math.log((n_total + 1) / (count + 1)) + 1.0
        for tok, count in df.items()
    }


def _topic_scores(topic: str, nodes: list[CourseMapNode]) -> dict[str, float]:
    """node_id → MATCHED IDF MASS: Σ idf(t) over the tokens the two share.

        mass(topic, node) = Σ idf(t) for t in (topic_tokens ∩ node_tokens)

    There is NO topic-length denominator, and that is the whole point. The rule
    this replaces was `|topic ∩ node| / |topic|`, which PUNISHED SPECIFICITY:
    every enumerated term the node happened not to carry sat in the denominator
    and diluted the score. Measured on tests/fixtures/course_map_sample.json,
    "Search" scored 1.000 and matched, while "Uninformed and Informed Search
    (BFS, DFS, A*, Heuristics)" — a richer, more precise phrasing of the SAME
    topic — scored 0.286 and matched nothing. Every topic in an authored AI
    blueprint is a long parenthetical string of exactly that shape, so
    essentially none of them would have matched.

    IDF-weighting the fraction does NOT fix it — measured 0.286 → 0.262, slightly
    WORSE, because the enumerated terms are rare and therefore weighted UP while
    unmatched. The denominator was the problem, not the weighting. Under this
    rule the same two topics score 3.351 and 6.703: extra enumerated terms can
    only ADD evidence.

    The cost, stated rather than hidden: mass is UNBOUNDED, and grows with both
    topic length and corpus size (idf depends on N). It is therefore NOT a
    confidence in [0, 1] and must not be compared across course maps. Admission
    is ranked against the best match (`TOPIC_MATCH_RELATIVE_FLOOR`) with an
    absolute evidence floor (`TOPIC_MATCH_MIN_EVIDENCE`) beneath it — the two
    conditions are applied in `_solve_section`, not here, because `best` is taken
    over the SECTION's candidate set rather than over the whole course map.

    DETERMINISM: each node's sum runs over SORTED tokens. Floating-point addition
    is not associative and set iteration order depends on PYTHONHASHSEED, so
    summing straight out of the set could differ in the last bit between
    processes — enough to move a node across the relative floor in a near-tie,
    and enough to break "identical inputs → byte-identical output".

    An empty topic token set RAISES: a topic made only of short words cannot
    discriminate anything, and treating it as a match on everything would hand
    the section the whole course map while looking like a successful topic match.
    That is a malformed blueprint and must be fixed by its author, not papered
    over here. Raised rather than asserted because `python -O` strips asserts.
    """
    topic_tokens = _tokenise(topic)
    if not topic_tokens:
        raise ValueError(
            f"Blueprint topic {topic!r} contains no usable tokens: every token is "
            f"shorter than TOPIC_MATCH_MIN_TOKEN_LEN="
            f"{config.TOPIC_MATCH_MIN_TOKEN_LEN}. A topic that cannot be tokenised "
            f"cannot select candidate nodes; fix the topic in the blueprint."
        )
    idf = _idf(nodes)
    return {
        n.node_id: sum(idf[tok] for tok in sorted(topic_tokens & _node_tokens(n)))
        for n in nodes
    }


def _spec_hash(
    node_id: str,
    span_ids: list[str],
    item_type: str,
    bloom: str,
    marks: int,
    options_count: Optional[int],
    format_requirement: Optional[str] = None,
    grounding: str = "span",
    generation_instructions: Optional[str] = None,
) -> str:
    """LLM generation cache key.

    marks and options_count are part of the key because the cache only pays off
    ACROSS papers, and that is exactly where they differ: a 4-mark short question
    (midterm) and a 5-mark short question (final) can be drawn from the same
    node + span + bloom. Span-uniqueness only protects within a single paper.
    options_count is rendered as "" when None, so the encoding is stable.

    format_requirement JOINS the key (§8.1) because it changes the prompt: two
    items differing only in format are different questions and must not share a
    cached generation. It is APPENDED ONLY WHEN SET, rather than encoded as ""
    like options_count, so a section that authors no format produces byte-
    identical hashes to before this field existed — the three shipped blueprints
    keep their cache entries and their ItemSpec[] unchanged.

    grounding and generation_instructions JOIN the key on the same reasoning and
    with the same trailing-separator care (§6.4, §6.6). grounding changes the
    prompt more fundamentally than anything else here — whether the item is
    drawn from the span at all — so two otherwise-identical items must not share
    a cache entry across it. It is appended ONLY WHEN IT IS NOT THE DEFAULT
    "span": encoding the default would change EVERY existing hash and silently
    invalidate the on-disk generation cache for all three shipped blueprints.
    generation_instructions is appended only when set, for the same reason.

    ORDER is fixed — format_requirement, then grounding, then
    generation_instructions — and each is appended only when present, so the
    combinations are unambiguous in practice: a section carrying only
    `grounding="synthesis"` encodes "…|synthesis", and one carrying only
    `format_requirement="MATHEMATICAL_MODELING"` encodes
    "…|MATHEMATICAL_MODELING". These cannot collide because the value spaces are
    disjoint — grounding is the two-value Literal, formats are validated against
    config.KNOWN_FORMAT_REQUIREMENTS, and neither contains the other's values.
    generation_instructions is free text and COULD in principle equal one of
    them; a section whose whole instruction text is the single word "synthesis"
    would hash like a synthesis section. Recorded rather than defended against:
    a positional-tag encoding would change every existing hash, which is the
    thing this function must not do.

    group_id is deliberately absent: it is presentational, changing how items are
    displayed rather than what is asked, so regrouping must not invalidate the
    cache.
    """
    parts = [
        node_id,
        ",".join(span_ids),
        item_type,
        bloom,
        str(marks),
        "" if options_count is None else str(options_count),
    ]
    if format_requirement is not None:
        parts.append(format_requirement)
    if grounding != "span":
        parts.append(grounding)
    if generation_instructions is not None:
        parts.append(generation_instructions)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
