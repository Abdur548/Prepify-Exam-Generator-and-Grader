"""Validation gates for generated exam items. Zero LLM calls."""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Callable, Any

from pydantic import ValidationError

from coursegen import config
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption

GroundednessScorer = Callable[[str, str], float]
EmbeddingFn = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class ValidationIssue:
    slot_id: str
    gate: str
    message: str


def new_gate_report() -> dict[str, dict[str, Any]]:
    """
    Per-gate execution record.

    A bare failure count cannot distinguish "the gate cleared every item" from
    "the gate never ran" — both report zero. That is the difference between a
    validated paper and an unvalidated one, and R6 asks for pass/fail counts, not
    just failures. `skipped` records the dependency-injection case explicitly:
    with no scorer or no embedder, gates 2 and 3 do not execute at all.

    `not_applicable` is a SEPARATE fact and deliberately not folded into
    `skipped`. "We could not check this" and "there is nothing here to check
    against" are different, and the second is the more important one: a
    synthesis item (§6.4) has no source span to be grounded in, so gate 2 must
    neither fail it nor silently pass it. Collapsing the two would hide exactly
    the count this stage exists to make visible.

    `skipped` stays a BOOL about the gate (did it run at all) and
    `not_applicable` is an INT about items (how many this gate legitimately does
    not apply to), so for every gate that ran:

        evaluated + not_applicable == items that reached it

    Applicability is a property of the ITEM, not of what the caller injected, so
    `not_applicable` is counted even when `skipped` is True — a run with no
    scorer AND synthesis items reports both facts rather than letting the louder
    one swallow the other. `evaluated` is then 0 by construction, which is the
    one case where the sum above is short of what reached the gate; `skipped`
    is what says why.

    `from_cache` is the third way a gate can fail to see an item, and it was
    invisible until 2026-09-06. Items served from the spec-hash cache never reach
    validation — only `uncached_specs` is passed in — so a re-run of an identical
    generation reported `duplication: {evaluated: 0, skipped: false}`, which reads
    as "the gate ran and found nothing" for a paper where five of seven items
    touched no gate at all (F13). That is precisely the conflation the paragraphs
    above exist to prevent, arriving by a route they did not cover.

    Cached items are not re-gated, deliberately: only items that PASSED are ever
    written to the cache, and re-running the per-item gates would re-shuffle an
    already-shuffled MCQ and change the paper on every run. What they now do is
    seed the duplication gate's comparison set, because that gate is the one that
    is cross-item — see `validate_generated_items`.
    """
    return {
        gate: {
            "evaluated": 0,
            "passed": 0,
            "failed": 0,
            "skipped": False,
            "not_applicable": 0,
            # Items in this paper the gate did not evaluate on THIS run because
            # they came from cache. Never folded into `passed`: they cleared the
            # gate on an earlier run, under whatever thresholds applied then.
            "from_cache": 0,
        }
        for gate in ("schema", "relevance", "duplication", "mcq_hygiene")
    }


def validate_generated_items(
    specs: list[ItemSpec],
    raw_items: list[dict[str, Any]],
    span_text_by_id: dict[str, str],
    groundedness_scorer: GroundednessScorer | None = None,
    embedding_fn: EmbeddingFn | None = None,
    prior_items: list[GeneratedItem] | None = None,
) -> tuple[list[GeneratedItem], list[ValidationIssue], dict[str, dict[str, Any]]]:
    """
    Run the four validation gates. Returns accepted items, the issues raised, and a
    per-gate execution record (see `new_gate_report`).

    `groundedness_scorer` and `embedding_fn` are injected so the default test run
    stays fast and network-free. When either is absent its gate is marked `skipped`
    in the report rather than silently contributing zero failures.

    ## `prior_items` — items already accepted, that this batch must not duplicate

    Items already in the paper: served from the spec-hash cache, or accepted by an
    earlier pass of this same run. They are NOT re-gated and NOT returned; they
    seed the duplication gate's comparison set and are counted as `from_cache`.

    Three of the four gates are per-item, and a cached item passed them when it was
    written — only accepted items are ever cached. Duplication is the exception: it
    is the one CROSS-item gate, so judging a batch against only itself was wrong in
    two ways at once (F13). A newly generated item was never compared against a
    cached one, and a regenerated item was never compared against anything accepted
    in the first pass, because each call started with an empty comparison set.

    Re-gating them instead would be worse than useless: `_shuffle_options` mutates
    the item, so a cached MCQ would be shuffled a second time and the paper would
    change on every run — destroying the byte-identical re-generation the cache
    exists to provide.
    """
    spec_by_slot = {s.slot_id: s for s in specs}
    issues: list[ValidationIssue] = []
    parsed: list[tuple[ItemSpec, GeneratedItem]] = []
    prior_items = prior_items or []

    gates = new_gate_report()
    gates["relevance"]["skipped"] = groundedness_scorer is None
    gates["duplication"]["skipped"] = embedding_fn is None
    # `from_cache` is NOT derived from `prior_items` here. The rewrite pass is also
    # given prior items — everything accepted so far, most of which was generated
    # this run, not restored from cache — so counting them here would report
    # first-pass items as cached. Only `generate_exam` knows the cache count, and
    # it stamps the finished report.

    def record(gate: str, ok: bool) -> None:
        gates[gate]["evaluated"] += 1
        gates[gate]["passed" if ok else "failed"] += 1

    def record_not_applicable(gate: str) -> None:
        """The gate reached this item and legitimately does not apply to it.

        Counted apart from `evaluated` so that
        `evaluated + not_applicable == items that reached the gate` holds, and
        apart from `passed` so the item is never reported as having cleared a
        check that never ran on it.
        """
        gates[gate]["not_applicable"] += 1

    # Gate 1: schema.
    for raw in raw_items:
        slot_id = str(raw.get("slot_id", "<missing>"))
        try:
            item = GeneratedItem.model_validate(raw)
        except ValidationError as exc:
            record("schema", False)
            issues.append(ValidationIssue(slot_id=slot_id, gate="schema", message=str(exc)))
            continue

        spec = spec_by_slot.get(item.slot_id)
        if spec is None:
            record("schema", False)
            issues.append(ValidationIssue(slot_id=item.slot_id, gate="schema", message="No matching ItemSpec"))
            continue
        record("schema", True)
        parsed.append((spec, item))

    accepted: list[GeneratedItem] = []
    accepted_texts: list[str] = []
    accepted_embeddings: list[list[float]] = []

    # Seed the duplication comparison set with what is already in the paper, so a
    # new item is judged against the WHOLE paper rather than against its own batch.
    # Embedding is local (BGE-M3), so this costs CPU and no API quota.
    if embedding_fn is not None and prior_items:
        prior_texts = [f"{i.stem}\n{i.model_answer}" for i in prior_items]
        accepted_texts.extend(prior_texts)
        accepted_embeddings.extend(embedding_fn(prior_texts))

    for spec, item in parsed:
        source = "\n".join(span_text_by_id[sid] for sid in spec.span_ids)

        # Gate 2: relevance. NOT groundedness - see the disproof above
        # RELEVANCE_FLOOR in config. The reranker cannot separate a true claim
        # from a false one about the same span, so this gate only rejects text
        # that is not about the source material at all.
        #
        # A synthesis item (§6.4) is written to INVENT its artifact — a novel
        # game tree, a novel word problem — with the span as context rather than
        # as the thing being reproduced. There is nothing for groundedness to
        # score the answer against, so the gate records the item as
        # not-applicable and lets it through. It must not fail the item (the
        # blueprint asked for exactly this) and must not pass it either (nothing
        # was checked) — the count reaches the run_manifest instead.
        #
        # Checked BEFORE the scorer-injection branch so the fact is recorded
        # whether or not a scorer was supplied: whether an item is groundable is
        # a property of the item, not of what the caller happened to inject.
        if spec.grounding == "synthesis":
            record_not_applicable("relevance")
        elif groundedness_scorer is not None:
            score = groundedness_scorer(_groundedness_claim(spec, item), source)
            if score < config.RELEVANCE_FLOOR:
                record("relevance", False)
                issues.append(ValidationIssue(
                    slot_id=item.slot_id,
                    gate="relevance",
                    message=f"score={score:.4f} < RELEVANCE_FLOOR={config.RELEVANCE_FLOOR}",
                ))
                continue
            record("relevance", True)

        # Gate 4: MCQ hygiene.
        if spec.item_type == "mcq":
            issue = _mcq_hygiene_issue(item)
            if issue is not None:
                record("mcq_hygiene", False)
                issues.append(ValidationIssue(item.slot_id, "mcq_hygiene", issue))
                continue
            record("mcq_hygiene", True)
            item = _shuffle_options(item)

        # Gate 3: duplication.
        text = f"{item.stem}\n{item.model_answer}"
        if embedding_fn is not None:
            emb = embedding_fn([text])[0]
            duplicate = any(_cosine(emb, prev) >= config.DEDUP_TAU for prev in accepted_embeddings)
            if duplicate:
                record("duplication", False)
                issues.append(ValidationIssue(
                    slot_id=item.slot_id,
                    gate="duplication",
                    message=f"cosine >= DEDUP_TAU={config.DEDUP_TAU}",
                ))
                continue
            record("duplication", True)
            accepted_embeddings.append(emb)
        accepted_texts.append(text)
        accepted.append(item)

    return accepted, issues, gates


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------

def _groundedness_claim(spec: ItemSpec, item: GeneratedItem) -> str:
    """The text gate 2 scores against the source span.

    Which text depends on the item type, because "the claim being made" lives in
    a different field for different formats. For a written answer the content IS
    the answer. For a selection item the answer is a LABEL — "True.", "A" — that
    carries no content at all, and scoring it measures nothing.

    Measured on a real 2,047-character span (2026-08-30):

        form                                bare answer   stem + answer
        true/false ("True.")                    -6.48         +5.52
        correct concise prose                   +6.78         +7.18
        HALLUCINATED claim                      -3.21         +2.92
        unrelated topic                        -11.28        -11.28

    This is why the whole TRUE_FALSE_SERIES section was destroyed on the first
    real run: ten correct items scored like nonsense because their answers were
    one word long.

    Note the cost, and why the stem is NOT simply added for everything: it comes
    from the span, so including it injects overlap regardless of whether the
    answer is right, and it rescued the hallucination too (-3.21 → +2.92).
    Bare-answer scoring discriminates roughly twice as well (9.99 apart vs 4.26),
    so it is kept wherever the answer actually carries the content.
    """
    if spec.item_type == "mcq":
        return f"{item.stem} {item.model_answer}"
    return item.model_answer


def _option_length_outlier_ratio(sorted_lengths: list[int]) -> float:
    """Longest option divided by the median of the others. 1.0 means no outlier.

    Replaced a +/-40% band around the mean on 2026-09-01. That rule was
    SCALE-DEPENDENT: 40% of a 5-character option is two characters, so one extra
    word broke it, while 40% of a 40-character sentence is sixteen characters of
    slack. It therefore rejected `Trees / Arrays / Indices / Linked lists` -- a
    good item -- and accepted four padded sentences, penalising the better MCQ
    design. Measured on real output, it failed 2 of 4 items, both short-option
    sets, and drove mcq_hygiene to a 32.5% pass rate over ten runs.

    What the guard is actually for is ONE option standing out far enough that a
    student picks it without reading the stem. That is a property of an outlier
    against its peers, not of absolute spread, so the median of the OTHER options
    is the right reference: it is unmoved by the outlier itself, where the mean is
    dragged toward it and hides the very thing being measured.

    Scale-free by construction -- four one-word options and four sentences with
    the same shape score the same.

    Known limitation: this catches a conspicuously LONG option only. A
    conspicuously short one is a weaker tell and was not measured, so it is not
    guarded (R9 -- do not add a threshold for a signal you have not checked).
    """
    if len(sorted_lengths) < 2:
        return 1.0
    others = sorted_lengths[:-1]
    mid = len(others) // 2
    median_of_others = (
        others[mid] if len(others) % 2
        else (others[mid - 1] + others[mid]) / 2
    )
    if median_of_others <= 0:
        return float("inf")
    return sorted_lengths[-1] / median_of_others


def _mcq_hygiene_issue(item: GeneratedItem) -> str | None:
    if item.options is None or item.correct_option is None:
        return "MCQ requires options and correct_option"

    option_texts = [o.text.strip() for o in item.options]
    lowered = [t.lower() for t in option_texts]
    if any("all of the above" in t or "none of the above" in t for t in lowered):
        return "all/none of the above is not allowed"

    lengths = sorted(len(t) for t in option_texts if t)
    if not lengths:
        return "options are empty"
    ratio = _option_length_outlier_ratio(lengths)
    if ratio > config.OPTION_LENGTH_OUTLIER_RATIO:
        return (
            f"longest option is {ratio:.2f}x the median of the others, above "
            f"OPTION_LENGTH_OUTLIER_RATIO={config.OPTION_LENGTH_OUTLIER_RATIO}"
        )

    normalised = [_normalise_option(t) for t in option_texts]
    if len(set(normalised)) != len(normalised):
        return "duplicate option spelling variant"

    return None


def _shuffle_options(item: GeneratedItem) -> GeneratedItem:
    """
    Shuffle MCQ options deterministically, then relabel by position and remap
    `correct_option` to match.

    Two things this gets right that a naive shuffle does not:

    1. **The seed varies per item.** Seeding a fresh `Random` with the bare
       constant gives every question in the paper the *same* permutation. Models
       tend to emit the correct answer first, so a fixed permutation lands the
       answer in an identical position on every item — a paper answerable without
       reading it. Mixing in `slot_id` keeps the shuffle reproducible (same item,
       same permutation, every run) while varying it across items. `Random` hashes
       a str seed with SHA-512, so this is stable across processes and unaffected
       by PYTHONHASHSEED.

    2. **Labels are reassigned by position and `correct_option` follows.** Shuffling
       the list while leaving labels attached to their text yields options ordered
       C, B, D, A. A renderer printing them in list order with fresh positional
       labels would then disagree with the key — the answer displayed as "D" while
       the key still says "A", on every shuffled MCQ. Relabelling here makes the
       item self-consistent no matter how P4 chooses to render it.

    Indices are permuted rather than the option objects so the correct answer is
    tracked by position, not by matching text — immune to duplicate option text.
    """
    if item.options is None or item.correct_option is None:
        return item

    options = list(item.options)
    correct_positions = [i for i, o in enumerate(options) if o.label == item.correct_option]
    if not correct_positions:
        # correct_option names no existing option. The hygiene gate rejects this
        # before we get here; leave the item untouched rather than inventing a key.
        return item
    correct_old = correct_positions[0]

    order = list(range(len(options)))
    random.Random(f"{config.MCQ_SHUFFLE_SEED}:{item.slot_id}").shuffle(order)

    relabelled = [
        MCQOption(label=config.MCQ_OPTION_LABELS[new_pos], text=options[old_pos].text)
        for new_pos, old_pos in enumerate(order)
    ]
    new_correct = config.MCQ_OPTION_LABELS[order.index(correct_old)]

    data = item.model_dump()
    data["options"] = [o.model_dump() for o in relabelled]
    data["correct_option"] = new_correct
    # `model_answer` is DERIVED, not carried. This is the field the answer key
    # prints and the UI shows, and it is the one the student marks against.
    #
    # It used to survive the shuffle untouched, still naming the pre-shuffle
    # position — so `answer_key.pdf` printed "Correct option: C" and then "B"
    # beneath it, and the explanation named a third option again. Measured on a
    # real run: the model answered 4 of 4 correctly and the shuffle broke 3 of
    # them; the fourth agreed only because its permutation happened to leave the
    # answer in place. A study tool whose key is wrong is worse than one with no
    # key at all.
    #
    # Remapping it would not have been enough. `model_answer` is free text and
    # the model fills it inconsistently — a bare label on one run, "T" for a
    # true/false item, the option's full text on another (6 of 10 items on the
    # shipped paper are not labels at all). A "remap it if it looks like a label"
    # rule silently does nothing for the rest.
    #
    # So it is computed from `correct_option`, which is the field the hygiene
    # gate validates and the renderer trusts. R7: stamp from code what the system
    # already holds, rather than keeping a second copy that can drift.
    data["model_answer"] = f"{new_correct}. {options[correct_old].text}"
    return GeneratedItem.model_validate(data)


def _normalise_option(text: str) -> str:
    return re.sub(r"\W+", "", text).lower()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
