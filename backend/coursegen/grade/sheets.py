"""Turn a generated paper into an archived, answerable sheet.

`paper.json` means "the latest paper" and is overwritten by every run. An answer
must resolve against the exact questions the student was shown, so every paper is
archived under a content hash the moment it is built, and attempts point at that.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import sqlite3
from typing import Any

from coursegen.contracts.item import ItemSpec
from coursegen.grade.records import SheetItem, SheetRecord, document_items
from coursegen.grade.store import Store

logger = logging.getLogger(__name__)

__all__ = ["archive_paper", "document_items", "paper_sheet", "sheet_id_for"]

ARCHIVE_FAILED_WARNING = (
    "This paper could not be saved for answering; generate it again to take it."
)


def sheet_id_for(document: dict[str, Any]) -> str:
    """First 16 hex characters of sha256 over the canonical document.

    The `sheet_id` key itself is excluded, so the id of a document that already
    carries one is the same as the id it was given. The same paper therefore gets
    the same id however many times it is generated, and archiving it again is a
    no-op rather than a duplicate.
    """
    body = {k: v for k, v in document.items() if k != "sheet_id"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def paper_sheet(
    paper: dict[str, Any], specs: list[ItemSpec], created_at: str
) -> SheetRecord:
    """One `SheetItem` per FILLED question. An unfilled slot has nothing to answer."""
    spec_by_slot = {s.slot_id: s for s in specs}
    items = []
    for slot_id, doc_item in sorted(document_items(paper).items()):
        spec = spec_by_slot[slot_id]
        items.append(SheetItem(
            slot_id=slot_id,
            node_id=spec.node_id,
            span_ids=list(spec.span_ids),
            spec_hash=spec.spec_hash,
            item_type=spec.item_type,
            bloom=spec.bloom,
            marks=spec.marks,
            correct_option=doc_item.get("correct_option"),
            from_material=bool(doc_item.get("from_material", True)),
            spec=spec.model_dump(),
        ))
    return SheetRecord(
        sheet_id=paper.get("sheet_id") or sheet_id_for(paper),
        kind="paper",
        blueprint_id=paper.get("blueprint_id"),
        title=paper.get("title", ""),
        created_at=created_at,
        document=paper,
        items=items,
    )


def archive_paper(
    paper: dict[str, Any], specs: list[ItemSpec], *, now: str
) -> dict[str, Any]:
    """Archive the paper and return a copy carrying its `sheet_id`.

    **Never raises.** By the time this runs the model calls are spent; failing the
    whole run over a storage problem would throw away a paper the student is
    waiting for. Instead the paper comes back with `sheet_id: None` and a warning
    saying it cannot be taken, which the UI can show and act on.
    """
    archived = copy.deepcopy(paper)
    archived["sheet_id"] = sheet_id_for(paper)
    try:
        with Store() as store:
            store.put_sheet(paper_sheet(archived, specs, now))
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        logger.warning("Paper not archived for practice: %s", exc)
        archived["sheet_id"] = None
        summary = archived.setdefault("summary", {})
        summary["warnings"] = list(summary.get("warnings", [])) + [ARCHIVE_FAILED_WARNING]
    return archived
