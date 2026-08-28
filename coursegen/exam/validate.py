"""Validation gates for generated exam items. Zero LLM calls."""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Any

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
    """
    return {
        gate: {"evaluated": 0, "passed": 0, "failed": 0, "skipped": False}
        for gate in ("schema", "groundedness", "duplication", "mcq_hygiene")
    }


def validate_generated_items(
    specs: list[ItemSpec],
    raw_items: list[dict[str, Any]],
    span_text_by_id: dict[str, str],
    groundedness_scorer: GroundednessScorer | None = None,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[list[GeneratedItem], list[ValidationIssue], dict[str, dict[str, Any]]]:
    """
    Run the four validation gates. Returns accepted items, the issues raised, and a
    per-gate execution record (see `new_gate_report`).

    `groundedness_scorer` and `embedding_fn` are injected so the default test run
    stays fast and network-free. When either is absent its gate is marked `skipped`
    in the report rather than silently contributing zero failures.
    """
    spec_by_slot = {s.slot_id: s for s in specs}
    issues: list[ValidationIssue] = []
    parsed: list[tuple[ItemSpec, GeneratedItem]] = []

    gates = new_gate_report()
    gates["groundedness"]["skipped"] = groundedness_scorer is None
    gates["duplication"]["skipped"] = embedding_fn is None

    def record(gate: str, ok: bool) -> None:
        gates[gate]["evaluated"] += 1
        gates[gate]["passed" if ok else "failed"] += 1

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

    for spec, item in parsed:
        source = "\n".join(span_text_by_id[sid] for sid in spec.span_ids)

        # Gate 2: groundedness.
        if groundedness_scorer is not None:
            score = groundedness_scorer(item.model_answer, source)
            if score < config.GROUNDEDNESS_TAU:
                record("groundedness", False)
                issues.append(ValidationIssue(
                    slot_id=item.slot_id,
                    gate="groundedness",
                    message=f"score={score:.4f} < GROUNDEDNESS_TAU={config.GROUNDEDNESS_TAU}",
                ))
                continue
            record("groundedness", True)

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

def _mcq_hygiene_issue(item: GeneratedItem) -> str | None:
    if item.options is None or item.correct_option is None:
        return "MCQ requires options and correct_option"

    option_texts = [o.text.strip() for o in item.options]
    lowered = [t.lower() for t in option_texts]
    if any("all of the above" in t or "none of the above" in t for t in lowered):
        return "all/none of the above is not allowed"

    lengths = [len(t) for t in option_texts if t]
    if not lengths:
        return "options are empty"
    avg = sum(lengths) / len(lengths)
    min_allowed = avg * (1.0 - config.OPTION_LENGTH_BAND)
    max_allowed = avg * (1.0 + config.OPTION_LENGTH_BAND)
    if any(length < min_allowed or length > max_allowed for length in lengths):
        return "option lengths outside configured band"

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
