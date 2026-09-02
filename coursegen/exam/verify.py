"""Gate 5 — factuality. The instrument gate 2 was never able to be.

Gate 2 was disproven on 2026-09-01: `ms-marco-MiniLM` scores topical relevance,
and on real spans true claims scored −0.33..+4.21 while false claims scored
−8.73..+4.84, the top scorer being false. The classes overlap; no threshold
exists. It was demoted to a relevance floor and the system has had NO factuality
check since.

This is that check: the LLM as verifier, asked a narrower question than the one it
answers when generating. Rather than "is this true", which invites it to consult
its own knowledge, it is asked "does THIS PASSAGE state this" — a reading task
with the evidence in front of it.

## Validated before shipping (R9)

Same discipline that disproved gate 2: prove the signal separates the classes
BEFORE wiring it to anything. Four real spans, 20 labelled claims:

    accepted true   8      rejected true   0
    accepted false  0      rejected false 12

**No false claim was ever marked SUPPORTED.** That is the property a gate needs;
a verifier that occasionally endorses a falsehood is worse than none, because it
launders it.

## What that validation does NOT establish

- **The true claims were lifted verbatim from the passage.** Real model answers
  paraphrase, compress and infer. Verbatim support is the easy case, and the
  retention figure above is therefore an upper bound.
- **Same model, same provider.** It verifies output from the family that produced
  it. Independent failure is not guaranteed and correlated blind spots are the
  obvious risk.
- **n=20, one corpus, one subject.**

So this is a real instrument where gate 2 was not, and it is not proof of
correctness. `SUPPORTED` means *this passage says so*, which is exactly the claim
Prepify can honestly make about a span-grounded item — and nothing more.

## Cost, and why it is opt-in

One extra call per batch, roughly doubling generation cost. Off by default;
`run_manifest.json` records whether it ran, so a paper is never presented as
verified when the gate was skipped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from coursegen.contracts.item import GeneratedItem, ItemSpec

SUPPORTED = "SUPPORTED"
CONTRADICTED = "CONTRADICTED"
NOT_STATED = "NOT_STATED"
VERDICTS = (SUPPORTED, CONTRADICTED, NOT_STATED)

_SYSTEM = (
    "You check exam answers against the source passage they were written from. "
    "For each numbered claim reply with exactly one verdict:\n"
    "  SUPPORTED    - the passage states this, directly or by clear paraphrase\n"
    "  CONTRADICTED - the passage states the opposite\n"
    "  NOT_STATED   - the passage does not address it either way\n"
    "Judge ONLY against the passage supplied. Do not use your own knowledge of the "
    "subject: a claim you believe is true is still NOT_STATED if this passage does "
    "not say it."
)


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "quote": {"type": "string"},
                    },
                    "required": ["index", "verdict", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Verdict:
    slot_id: str
    verdict: str
    quote: str = ""

    @property
    def is_supported(self) -> bool:
        return self.verdict == SUPPORTED


def _claim(item: GeneratedItem) -> str:
    """The assertion an item makes, as one sentence a reader could check.

    For an MCQ this must resolve `correct_option` to the option's TEXT. Adding the
    stem is not enough: an MCQ's `model_answer` is the bare label "A", so the claim
    reads "...what does the agent state represent? Answer: A", which states
    nothing and is correctly judged NOT_STATED.

    Caught on the first real end-to-end run of this gate (2026-09-02): 5 of 7 items
    came back NOT_STATED, and every one was an MCQ whose claim was a letter. That
    looked like the model drifting from its source and was entirely an artefact of
    this function. It is the same mistake that destroyed a whole TRUE_FALSE section
    under gate 2 — scoring a label instead of a claim — made a second time, one
    layer further in.

    Resolution is by label AFTER `_shuffle_options` has relabelled by position and
    remapped `correct_option`, so the two always agree.
    """
    answer = item.model_answer.strip()
    if item.options and item.correct_option:
        answer = next(
            (o.text.strip() for o in item.options if o.label == item.correct_option),
            answer,   # correct_option naming no option: MCQ hygiene rejects that
        )
    return f"{item.stem.strip()} Answer: {answer}"


def verify_items(
    pairs: list[tuple[ItemSpec, GeneratedItem]],
    span_text_by_id: dict[str, str],
    llm_client: Any,
) -> dict[str, Verdict]:
    """Check each item against its own span. Returns {slot_id: Verdict}.

    Synthesis items are omitted entirely rather than marked NOT_STATED: they are
    written to INVENT an artifact with the span as context, so "the passage does
    not state this" is true of them by design and would read as a failure. The
    caller reports them as not-applicable, exactly as gate 2 does.

    Items are grouped by span so one call covers every claim against a passage,
    and the passage is sent once rather than per item.
    """
    # Keyed by the span TUPLE, not a joined string. Joining and re-splitting a
    # key is a fragile round-trip that has to agree with itself about a
    # separator; a tuple simply cannot disagree.
    by_span: dict[tuple[str, ...], list[tuple[ItemSpec, GeneratedItem]]] = {}
    for spec, item in pairs:
        if spec.grounding == "synthesis" or not spec.span_ids:
            continue
        by_span.setdefault(tuple(spec.span_ids), []).append((spec, item))

    verdicts: dict[str, Verdict] = {}
    for span_key, group in by_span.items():
        span = "\n".join(span_text_by_id.get(sid, "") for sid in span_key)
        if not span.strip():
            continue
        claims = [_claim(item) for _spec, item in group]
        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
        response = llm_client.call(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"PASSAGE:\n{span}\n\nCLAIMS:\n{numbered}"},
            ],
            response_schema=_schema(),
        )
        parsed = _extract_verdicts(response)
        for i, (_spec, item) in enumerate(group):
            row = parsed.get(i)
            verdicts[item.slot_id] = Verdict(
                slot_id=item.slot_id,
                # A missing verdict is NOT_STATED, never SUPPORTED. An item the
                # verifier failed to answer for must not inherit a pass.
                verdict=(row or {}).get("verdict", NOT_STATED),
                quote=(row or {}).get("quote", ""),
            )
    return verdicts


def _extract_verdicts(response: dict[str, Any]) -> dict[int, dict[str, str]]:
    try:
        content = response["choices"][0]["message"]["content"]
        rows = json.loads(content).get("verdicts", [])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}
    out: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            idx = int(row["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if row.get("verdict") in VERDICTS:
            out[idx] = {"verdict": row["verdict"], "quote": str(row.get("quote", ""))}
    return out


def summarise(verdicts: dict[str, Verdict], items_total: int) -> dict[str, Any]:
    """Manifest block. Names what was checked and what was not."""
    counts = {v: 0 for v in VERDICTS}
    for verdict in verdicts.values():
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    checked = len(verdicts)
    return {
        "checked": checked,
        "not_applicable": items_total - checked,   # synthesis items and spanless items
        "supported": counts[SUPPORTED],
        "contradicted": counts[CONTRADICTED],
        "not_stated": counts[NOT_STATED],
        "supported_ratio": counts[SUPPORTED] / checked if checked else None,
        "caveat": (
            "SUPPORTED means the cited passage states the claim. It is not proof of "
            "correctness: the verifier shares a model family with the generator, and "
            "was validated on verbatim-supported claims, which are the easy case."
        ),
    }
