"""Batched exam item generation with cache, validation and run manifest."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.item import GeneratedItem, ItemSpec
from coursegen.exam.validate import GroundednessScorer, validate_generated_items
from coursegen.llm.prompts import build_generation_messages


@dataclass(frozen=True)
class GenerationResult:
    items: list[GeneratedItem]
    manifest: dict[str, Any]


def generate_exam(
    specs: list[ItemSpec],
    course_map: list[CourseMapNode],
    span_text_by_id: dict[str, str],
    llm_client: Any,
    output_dir: Path,
    cache_dir: Path,
    blueprint_id: str,
    groundedness_scorer: GroundednessScorer | None = None,
    embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
    span_source_by_id: dict[str, tuple[str, int]] | None = None,
) -> GenerationResult:
    """
    `blueprint_id` is required, not optional: R7 says a run_manifest must be enough
    to regenerate the exact same ItemSpec[], and ItemSpec[] is a function of the
    course map AND the blueprint. A manifest carrying only course_map_hash cannot
    tell a midterm run from a final one, so the run is not reproducible from it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    cache_hits = 0
    call_count_before = getattr(llm_client.budget, "calls_used", 0)
    token_count_before = getattr(llm_client.budget, "tokens_used", 0)

    items_by_slot: dict[str, GeneratedItem] = {}
    uncached_specs: list[ItemSpec] = []

    for spec in specs:
        cached = _read_cache(cache_dir, spec.spec_hash)
        if cached is None:
            uncached_specs.append(spec)
        else:
            cache_hits += 1
            items_by_slot[spec.slot_id] = cached

    raw_items = _call_batches(uncached_specs, span_text_by_id, llm_client)
    _stamp_source_refs(raw_items, uncached_specs, span_source_by_id)
    valid, issues, gates = validate_generated_items(
        specs=uncached_specs,
        raw_items=raw_items,
        span_text_by_id=span_text_by_id,
        groundedness_scorer=groundedness_scorer,
        embedding_fn=embedding_fn,
    )
    for item in valid:
        spec = _spec_for_slot(uncached_specs, item.slot_id)
        _write_cache(cache_dir, spec.spec_hash, item)
        items_by_slot[item.slot_id] = item

    regeneration_passes = 0
    flagged_slots = sorted({issue.slot_id for issue in issues if issue.slot_id in {s.slot_id for s in uncached_specs}})
    if flagged_slots and config.MAX_REGENERATION_PASSES > 0:
        regeneration_passes = 1
        regen_specs = [s for s in uncached_specs if s.slot_id in set(flagged_slots)]
        regen_raw = _call_batches(regen_specs, span_text_by_id, llm_client)
        _stamp_source_refs(regen_raw, regen_specs, span_source_by_id)
        regen_valid, regen_issues, regen_gates = validate_generated_items(
            specs=regen_specs,
            raw_items=regen_raw,
            span_text_by_id=span_text_by_id,
            groundedness_scorer=groundedness_scorer,
            embedding_fn=embedding_fn,
        )
        gates = _merge_gate_reports(gates, regen_gates)
        for item in regen_valid:
            spec = _spec_for_slot(regen_specs, item.slot_id)
            _write_cache(cache_dir, spec.spec_hash, item)
            items_by_slot[item.slot_id] = item
        fixed = {item.slot_id for item in regen_valid}
        flagged_slots = sorted((set(flagged_slots) - fixed) | {i.slot_id for i in regen_issues})
        issues.extend(regen_issues)

    ordered_items = [items_by_slot[s.slot_id] for s in specs if s.slot_id in items_by_slot]
    call_count_after = getattr(llm_client.budget, "calls_used", 0)
    token_count_after = getattr(llm_client.budget, "tokens_used", 0)

    grounding_summary, grounding_warnings = _grounding_summary(specs)

    manifest = {
        "course_map_hash": _course_map_hash(course_map),
        "blueprint_id": blueprint_id,
        "model_id": config.GEMINI_MODEL,
        "seed": config.MCQ_SHUFFLE_SEED,
        "spec_hashes": [s.spec_hash for s in specs],
        "call_count": call_count_after - call_count_before,
        "token_count": token_count_after - token_count_before,
        "cache_hits": cache_hits,
        # Per-gate {evaluated, passed, failed, skipped, not_applicable} rather
        # than a bare failure count: zero failures because a gate cleared
        # everything, zero because it never ran, and zero because it did not
        # apply are three different facts, and only one of them means the paper
        # was validated.
        "validation": gates,
        # How many of this paper's questions are NOT backed by the student's own
        # material (§6.4, §7). Reported on every run, at whatever value —
        # exactly as fill_ratio and allocation_fidelity report degradations that
        # were previously invisible. A student cannot revise a novel game tree
        # from their own slides, so this number has to be visible rather than
        # inferable.
        "grounding": grounding_summary,
        # Warnings ABOUT THE PAPER, distinct from per-item validation issues:
        # nothing here failed a gate. Currently only the synthesis-ratio warning.
        "warnings": grounding_warnings,
        "regeneration_passes": regeneration_passes,
        "flagged_slots": flagged_slots,
        # WHY each slot was dropped, not just which. `flagged_slots` names the
        # casualties; without the gate and the measured score there is no way to
        # tell a correctly-rejected hallucination from a good item lost to a
        # threshold set too high. This is the field that exposed the gate-2
        # disproof (see config): it recorded the score behind a dropped slot
        # whose claim was verbatim in its own span. A run that silently loses a
        # question should say what it measured, in the same artifact that
        # claims the paper is valid.
        "validation_issues": [
            {"slot_id": i.slot_id, "gate": i.gate, "message": i.message[:config.ISSUE_MESSAGE_MAX_CHARS]}
            for i in issues
        ],
        "wall_clock_seconds": round(time.perf_counter() - start, 6),
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    return GenerationResult(items=ordered_items, manifest=manifest)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_batches(
    specs: list[ItemSpec],
    span_text_by_id: dict[str, str],
    llm_client: Any,
) -> list[dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    for start in range(0, len(specs), config.BATCH_SIZE):
        batch = specs[start: start + config.BATCH_SIZE]
        if not batch:
            continue
        messages = build_generation_messages(batch, span_text_by_id)
        response = llm_client.call(messages, response_schema=_response_schema())
        raw_items.extend(_extract_items(response))
    return raw_items


def _stamp_source_refs(
    raw_items: list[dict[str, Any]],
    specs: list[ItemSpec],
    span_source_by_id: dict[str, tuple[str, int]] | None,
) -> None:
    """Overwrite `source_ref` with the real file and pages, from code not the model.

    The prompt shows the model `<source_span id="chunk_abc">` and nothing else
    about provenance, so a model asked to fill `source_ref` can only echo the
    delimiter id. On the first real run every one of the ten surviving items cited
    a file that does not exist — 'source_span', 'source_span_c3f352fc' — with page
    [1] throughout, while the real sources were 03_search.pdf, 06_CSP.pdf and the
    rest. A citation that looks authoritative and is false is worse than none, and
    in a study tool it is the difference between a student finding the material and
    concluding their notes are wrong.

    The system already holds this: the span id maps to the chunk's own source file
    and page. Never ask a model to invent data you are holding.

    Silently leaves the model's value alone when no map is supplied, so existing
    callers and tests are unaffected — but production callers should always pass it.
    """
    if not span_source_by_id:
        return
    spec_by_slot = {s.slot_id: s for s in specs}
    for raw in raw_items:
        spec = spec_by_slot.get(str(raw.get("slot_id", "")))
        if spec is None:
            continue
        refs = [span_source_by_id[sid] for sid in spec.span_ids if sid in span_source_by_id]
        if not refs:
            continue
        # Spans of one item come from one node, hence one file; pages may differ.
        raw["source_ref"] = {
            "file": refs[0][0],
            "pages": sorted({page for _, page in refs}),
        }


def _extract_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    if "items" in response:
        return list(response["items"])
    content = response["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return list(parsed["items"])


def _response_schema() -> dict[str, Any]:
    """JSON schema for a batch of GeneratedItems.

    `$defs` must be HOISTED to the root of the schema we send.

    Pydantic does not inline nested models: `GeneratedItem` contains `MCQOption`
    and `SourceRef`, so `model_json_schema()` factors them into `$defs` and points
    at them with `{"$ref": "#/$defs/MCQOption"}`. The `#/` prefix means "from the
    root of this schema document". Nesting that schema under
    `properties.items.items` leaves its `$defs` two levels down while the refs
    still resolve from the root, so every pointer dangles. The provider rejected
    it with:

        400 INVALID_ARGUMENT — reference to undefined schema at
        properties.items.items.properties.options.anyOf.0.items

    (`anyOf.0` because `options` is Optional, hence `array | null`.)

    This was never caught because the mocked client accepts any `response_schema`
    — it records the argument and returns canned data, so the schema had never
    been validated by anything until the first live call.
    """
    item_schema = GeneratedItem.model_json_schema()
    defs = item_schema.pop("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": item_schema,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    if defs:
        schema["$defs"] = defs
    return schema


def _read_cache(cache_dir: Path, spec_hash: str) -> GeneratedItem | None:
    path = cache_dir / f"{spec_hash}.json"
    if not path.exists():
        return None
    return GeneratedItem.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _write_cache(cache_dir: Path, spec_hash: str, item: GeneratedItem) -> None:
    path = cache_dir / f"{spec_hash}.json"
    path.write_text(json.dumps(item.model_dump(), sort_keys=True, indent=2), encoding="utf-8")


def _spec_for_slot(specs: list[ItemSpec], slot_id: str) -> ItemSpec:
    for spec in specs:
        if spec.slot_id == slot_id:
            return spec
    raise KeyError(slot_id)


def _grounding_summary(
    specs: list[ItemSpec],
) -> tuple[dict[str, Any], list[str]]:
    """How much of this paper is not backed by the student's own material.

    Counted over the SPECS, not over the accepted items: the question "how much
    of this paper was invented" is a property of what the blueprint asked for,
    and counting only the items that survived validation would let the number
    move for reasons that have nothing to do with grounding.

    Returns the manifest block and any warnings. A WARNING and never a raise —
    a trace-heavy blueprint is a legitimate authoring choice (§4), and an exam
    that is largely synthesis is information rather than an error. The threshold
    lives in config.SYNTHESIS_ITEM_WARN_RATIO and is a starting value, not a
    measured one.
    """
    total = len(specs)
    synthesis = sum(1 for s in specs if s.grounding == "synthesis")
    ratio = synthesis / total if total > 0 else 0.0

    summary: dict[str, Any] = {
        "synthesis_items": synthesis,
        "items_total": total,
        "synthesis_ratio": ratio,
    }

    warnings: list[str] = []
    if ratio > config.SYNTHESIS_ITEM_WARN_RATIO:
        warnings.append(
            f"{synthesis} of {total} items ({ratio:.0%}) are synthesis items, "
            f"above SYNTHESIS_ITEM_WARN_RATIO="
            f"{config.SYNTHESIS_ITEM_WARN_RATIO:.0%}. These questions are NOT "
            f"drawn from the uploaded material — a student cannot revise them "
            f"from their own slides."
        )
    return summary, warnings


def _course_map_hash(course_map: list[CourseMapNode]) -> str:
    payload = json.dumps([n.model_dump() for n in course_map], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_gate_reports(
    first: dict[str, dict[str, Any]],
    second: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Combine the initial pass and the regeneration pass into one record.

    Counts add — `not_applicable` included, because an item re-offered to a gate
    that still does not apply to it was legitimately not checked twice, exactly
    as `evaluated` counts two evaluations of the same slot. `skipped` is AND-ed,
    because a gate only counts as skipped for the run as a whole if it ran in
    neither pass.
    """
    merged: dict[str, dict[str, Any]] = {}
    for gate in first:
        a, b = first[gate], second.get(gate, {})
        merged[gate] = {
            "evaluated": a["evaluated"] + b.get("evaluated", 0),
            "passed": a["passed"] + b.get("passed", 0),
            "failed": a["failed"] + b.get("failed", 0),
            "skipped": bool(a["skipped"] and b.get("skipped", True)),
            "not_applicable": a["not_applicable"] + b.get("not_applicable", 0),
        }
    return merged
