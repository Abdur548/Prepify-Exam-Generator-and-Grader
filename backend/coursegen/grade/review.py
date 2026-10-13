"""Review sets: the questions a student missed, back on a schedule (§7.4).

A review set is a sheet like any paper — answered, marked and contested the same
way — with one section, `R`, of at most `REVIEW_MAX_ITEMS` questions.

## Three forms, and what each one tells the student

- **identical** — the question they missed, its options reordered so position
  cannot be memorised. It tests whether that specific gap closed. Costs nothing.
- **fresh** — once the card has been passed, a new question on the same passage,
  written with the stems they have already seen quoted as "not this". It tests the
  concept rather than the memory of an answer.
- **fallback** — a fresh question was needed but could not be had: the model was
  unavailable, the gates rejected it, or it came back as a question already seen.
  The original is served and labelled, never passed off as new.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption, SourceRef
from coursegen.exam.allocate import avoid_spec_hash
from coursegen.exam.validate import _shuffle_options
from coursegen.grade.mastery import Card, cards, select_review, split_orphans
from coursegen.grade.records import SheetItem, SheetRecord, document_items
from coursegen.grade.sheets import sheet_id_for
from coursegen.grade.store import Store

FreshGenerator = Callable[[list[ItemSpec]], dict[str, GeneratedItem]]

NO_FRESH = (
    "Couldn't write a new question right now, so the original comes back instead. "
    "You can build a review again later for new wording."
)


def _selected(store: Store, nodes: list[CourseMapNode]) -> tuple[list[Card], int]:
    rows, orphans = split_orphans(store.all_answer_rows(), {n.node_id for n in nodes})
    picked = select_review(
        cards(rows), store.latest_seq(), {n.node_id: n.instructional_mass for n in nodes}
    )
    return picked, orphans


def needs_model(store: Store, nodes: list[CourseMapNode]) -> bool:
    """Whether the next review set would ask the model for new wording."""
    picked, _ = _selected(store, nodes)
    return any(card.form == "fresh" for card in picked)


def build_review(
    store: Store,
    nodes: list[CourseMapNode],
    *,
    now: str,
    generate_fresh: Optional[FreshGenerator],
) -> dict[str, Any]:
    picked, orphans = _selected(store, nodes)
    if not picked:
        return {"due": 0, "sheet": None, "fallbacks": 0, "orphans": orphans, "warnings": []}

    seen_stems = store.seen_stems_by_node()
    sheets: dict[str, SheetRecord] = {}
    plans = []                       # (slot, card, origin item, origin doc item, fresh spec | None)
    for i, card in enumerate(picked):
        sheet_id, origin_slot = card.origin
        if sheet_id not in sheets:
            sheets[sheet_id] = store.get_sheet(sheet_id)
        origin = next(it for it in sheets[sheet_id].items if it.slot_id == origin_slot)
        doc = document_items(sheets[sheet_id].document)[origin_slot]
        slot = f"R-{i + 1:02d}"
        spec = None
        stems = sorted(set(seen_stems.get(card.node_id, [])))
        if card.form == "fresh" and stems:
            spec = ItemSpec(**{
                **origin.spec,
                "slot_id": slot,
                "group_id": None,
                "avoid_stems": stems,
                "spec_hash": avoid_spec_hash(origin.spec["spec_hash"], stems),
            })
        plans.append((slot, card, origin, doc, spec))

    fresh_specs = [p[4] for p in plans if p[4] is not None]
    written = generate_fresh(fresh_specs) if (fresh_specs and generate_fresh) else {}

    items, sheet_items, fallbacks = [], [], 0
    for slot, card, origin, doc, spec in plans:
        new = written.get(slot) if spec is not None else None
        if spec is not None and (new is None or _repeats(new.stem, spec.avoid_stems or [])):
            new, spec = None, None
            form = "fallback"
            fallbacks += 1
        elif spec is not None:
            form = "fresh"
        else:
            form = "fallback" if card.form == "fresh" else "identical"
            fallbacks += form == "fallback"

        item = _identical(slot, card, doc) if new is None else _fresh(slot, new, doc)
        item.update({"form": form,
                     "retest_of": {"sheet_id": card.origin[0], "slot_id": card.origin[1]}})
        items.append(item)
        sheet_items.append(SheetItem(
            slot_id=slot, node_id=origin.node_id, span_ids=list(origin.span_ids),
            spec_hash=spec.spec_hash if spec is not None else origin.spec_hash,
            item_type=origin.item_type, bloom=origin.bloom, marks=origin.marks,
            correct_option=item.get("correct_option"), from_material=origin.from_material,
            spec=spec.model_dump() if spec is not None else origin.spec,
            retest_of=card.origin, form=form,
        ))

    warnings = [NO_FRESH] if fallbacks else []
    total = sum(it["marks"] for it in items)
    n = len(items)
    document: dict[str, Any] = {
        "kind": "review",
        "title": f"Review — {n} question{'s' if n != 1 else ''} you missed",
        "blueprint_id": None,
        "blueprint_title": "Review",
        "total_marks": total,
        "duration_minutes": 0,
        "instructions": [
            f"{n} question{'s' if n != 1 else ''} you got wrong or left blank before.",
            "Questions you have already passed once come back in new words.",
        ],
        "sections": [{
            "section_id": "R", "title": "Review", "item_type": "mixed",
            "marks_each": 0, "count": n,
            "instruction": f"{n} question{'s' if n != 1 else ''}.",
            "items": items,
        }],
        "summary": {
            "items_total": n, "slots_total": n, "fill_ratio": 1.0,
            "allocation_fidelity": 1.0, "unfilled_slots": [], "marks_available": total,
            "synthesis_items": sum(1 for it in items if not it["from_material"]),
            "warnings": warnings,
        },
    }
    document["sheet_id"] = sheet_id_for(document)
    store.put_sheet(SheetRecord(
        sheet_id=document["sheet_id"], kind="review", blueprint_id=None,
        title=document["title"], created_at=now, document=document, items=sheet_items,
    ))
    return {"due": n, "sheet": document, "fallbacks": fallbacks, "orphans": orphans,
            "warnings": warnings}


def _identical(slot: str, card: Card, doc: dict[str, Any]) -> dict[str, Any]:
    """The origin question, its options reordered with the key following its text."""
    item = {k: v for k, v in doc.items() if k not in ("form", "retest_of")}
    item["slot_id"] = slot
    if doc.get("options") and doc.get("correct_option"):
        shuffled = _shuffle_options(
            GeneratedItem(
                slot_id=slot, stem=doc["stem"],
                options=[MCQOption(**o) for o in doc["options"]],
                correct_option=doc["correct_option"], model_answer=doc.get("model_answer", ""),
                explanation=doc.get("explanation", ""),
                source_ref=SourceRef(**(doc.get("source") or {"file": "", "pages": []})),
            ),
            seed=f"{config.MCQ_SHUFFLE_SEED}:retest:{card.origin[0]}:{card.origin[1]}:{card.tests}",
        )
        item["options"] = [o.model_dump() for o in shuffled.options]
        item["correct_option"] = shuffled.correct_option
        item["model_answer"] = shuffled.model_answer
    return item


def _fresh(slot: str, new: GeneratedItem, doc: dict[str, Any]) -> dict[str, Any]:
    """A newly written question, with the origin's provenance — same passage."""
    return {
        "slot_id": slot, "filled": True, "stem": new.stem,
        "options": [o.model_dump() for o in (new.options or [])],
        "correct_option": new.correct_option, "model_answer": new.model_answer,
        "explanation": new.explanation, "marks": doc["marks"], "item_type": doc["item_type"],
        "bloom": doc.get("bloom"), "from_material": doc.get("from_material", True),
        "source": doc.get("source"), "source_excerpt": doc.get("source_excerpt"),
        "factuality": None, "node_id": doc.get("node_id"), "topic": doc.get("topic"),
    }


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def _repeats(stem: str, seen: list[str]) -> bool:
    return _norm(stem) in {_norm(s) for s in seen}
