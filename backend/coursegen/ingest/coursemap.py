"""
Ingest orchestrator: parse → structure → chunk → embed → index → course_map.json.

Public entry points:
  ingest(source_dir, data_dir)  — full pipeline, idempotent
  load_course_map(path)         — load previously persisted nodes
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import yake  # type: ignore[import]

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.ingest.chunk import Chunk, chunk_section
from coursegen.ingest.embed import embed_chunks, load_model
from coursegen.ingest.index import get_client, get_or_create_collection, upsert_chunks
from coursegen.ingest.parse import parse_directory
from coursegen.ingest.structure import LeafSection, extract_sections

logger = logging.getLogger(__name__)


def ingest(
    source_dir: Path,
    data_dir: Optional[Path] = None,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> list[CourseMapNode]:
    """
    Run the full ingest pipeline on all PDF/PPTX/DOCX files in *source_dir*.

    Idempotent (C1): re-running on unchanged input produces identical node IDs,
    identical course_map.json, and identical Qdrant point count.

    ## Three stages, wildly unequal

    `on_event` reports `reading` -> `mapping` -> `indexing`, plus per-file events
    forwarded from `parse_directory`. The split is by *cost*, not by the nine
    internal stages, because a progress display that gives equal weight to unequal
    work misleads more than no display at all: on 14 real decks the first two take
    seconds and `indexing` takes the better part of ten minutes.

    **`indexing` reports no sub-progress, and that is a limitation, not an
    oversight.** It is one `model.encode(texts, batch_size=...)` call and
    FlagEmbedding batches inside it with no callback to hook. Slicing the call
    here would produce a progress number at the cost of changing the embedding
    path, and R10 wants byte-identical vectors proven before that ships.
    """
    data_dir = data_dir or config.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    fire = on_event or (lambda _e: None)

    def event(kind: str, **fields: Any) -> None:
        # Swallowed for the reason every other telemetry point here is: ingest
        # costs ten minutes of the student's time and must not be lost narrating
        # itself.
        try:
            fire({"event": kind, **fields})
        except Exception:  # noqa: BLE001
            pass

    def stage(name: str, state: str, **fields: Any) -> None:
        event("stage", stage=name, state=state, **fields)

    # --- Stage 1: parse ---
    stage("reading", "start")
    parsed_docs = parse_directory(
        source_dir, on_file=lambda f: event("file", **f)
    )
    stage("reading", "done", files_read=len(parsed_docs))
    if not parsed_docs:
        logger.warning("No documents parsed from %s — nothing to ingest.", source_dir)
        return []

    # --- Stage 2 + 3: structure → chunk ---
    stage("mapping", "start")
    all_sections: list[LeafSection] = []
    for doc in parsed_docs:
        sections = extract_sections(doc)
        all_sections.extend(sections)
        logger.info(
            "%s → %d leaf sections", doc.source_file, len(sections)
        )

    if not all_sections:
        logger.warning("No sections extracted — nothing to ingest.")
        return []

    section_chunks: list[list[Chunk]] = [chunk_section(s) for s in all_sections]
    all_chunks: list[Chunk] = [c for group in section_chunks for c in group]
    logger.info("Total chunks: %d", len(all_chunks))

    # --- Stage 4: YAKE key-terms (CPU only) ---
    section_key_terms = _extract_key_terms(all_sections)

    # --- Stage 5: build preliminary nodes (mass computed below) ---
    # node_id = sha1(source_file + "|" + "/".join(path)) — stable across re-ingest.
    raw_nodes: list[_RawNode] = []
    for sec, chunks, key_terms in zip(all_sections, section_chunks, section_key_terms):
        section_text = " ".join(
            b.text for b in sec.blocks if b.block_type == "text" and b.text.strip()
        )
        token_count = max(1, len(section_text) // config.CHARS_PER_TOKEN_ESTIMATE)
        flags = _derive_flags(sec)
        raw_nodes.append(_RawNode(
            node_id=_compute_node_id(sec.source_file, sec.path),
            path=sec.path,
            source_file=sec.source_file,
            page_span=(sec.page_start, sec.page_end),
            token_count=token_count,
            chunk_ids=[c.chunk_id for c in chunks],
            key_terms=key_terms,
            flags=flags,
        ))

    # --- Stage 6: instructional_mass ---
    masses = _compute_instructional_mass(raw_nodes)

    # --- Stage 7: build CourseMapNode objects ---
    nodes: list[CourseMapNode] = [
        CourseMapNode(
            node_id=rn.node_id,
            path=rn.path,
            source_file=rn.source_file,
            page_span=rn.page_span,
            token_count=rn.token_count,
            chunk_ids=rn.chunk_ids,
            key_terms=rn.key_terms,
            flags=rn.flags,
            instructional_mass=masses[i],
        )
        for i, rn in enumerate(raw_nodes)
    ]

    # --- Stage 8: persist course_map.json ---
    save_course_map(nodes, data_dir / "course_map.json")
    stage("mapping", "done", topics=len(nodes), passages=len(all_chunks))

    # --- Stage 9: embed + index ---
    # `passages` is sent on start, not just on done: it is the only number the
    # client has to size the wait by, and it is useless once the wait is over.
    stage("indexing", "start", passages=len(all_chunks))
    model = load_model()
    embed_result = embed_chunks(all_chunks, model)
    client = get_client(data_dir)
    get_or_create_collection(client)
    upsert_chunks(client, all_chunks, embed_result)
    client.close()

    stage("indexing", "done", passages=len(all_chunks))

    logger.info("Ingest complete: %d nodes, %d chunks.", len(nodes), len(all_chunks))
    return nodes


def load_course_map(path: Optional[Path] = None) -> list[CourseMapNode]:
    """Load previously persisted course_map.json from disk."""
    path = path or config.COURSE_MAP_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CourseMapNode.model_validate(n) for n in raw]


def save_course_map(nodes: list[CourseMapNode], path: Path) -> None:
    """
    Persist nodes with deterministic float precision and key ordering.
    COURSE_MAP_FLOAT_PRECISION and JSON_SORT_KEYS make the file hash stable
    across two ingests on identical input — a prerequisite for the P1 gate.
    """
    data = []
    for node in nodes:
        d = node.model_dump()
        d["instructional_mass"] = round(
            d["instructional_mass"], config.COURSE_MAP_FLOAT_PRECISION
        )
        data.append(d)

    json_str = json.dumps(
        data,
        sort_keys=config.JSON_SORT_KEYS,
        indent=2,
        ensure_ascii=False,
    )
    path.write_text(json_str, encoding="utf-8")
    logger.debug("Saved course_map.json (%d nodes) to %s", len(nodes), path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_node_id(source_file: str, heading_path: list[str]) -> str:
    """sha1(source_file + "|" + "/".join(path)) — stable across re-ingest."""
    raw = f"{source_file}|{'/'.join(heading_path)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _derive_flags(section: LeafSection) -> NodeFlags:
    """
    OR the per-block content flags set by ingest/parse.py up to the section.

    A section is flagged if ANY of its blocks is: the flag answers "can this node
    anchor a question of that kind?", and one table on one page is enough.

    Only `has_figure` is a hard fact (an image block is an image block). `has_table`
    is a detection (PyMuPDF `find_tables`; python-pptx `shape.has_table` and a DOCX
    `w:tbl` element are both exact), and
    `has_code` / `has_equation` are HEURISTICS whose false-positive and
    false-negative modes are documented in ingest/parse.py's module docstring.
    Blueprints filter candidate nodes on these flags (`requires_flags_any`), so a
    flag that can never be true silently starves a whole exam section.
    """
    return NodeFlags(
        has_figure=any(b.block_type == "figure" for b in section.blocks),
        has_table=any(b.has_table for b in section.blocks),
        has_equation=any(b.has_equation for b in section.blocks),
        has_code=any(b.has_code for b in section.blocks),
    )


def _extract_key_terms(sections: list[LeafSection]) -> list[list[str]]:
    """Run YAKE on each section's text. CPU-only, no LLM calls."""
    extractor = yake.KeywordExtractor(
        n=config.YAKE_NGRAM_MAX,
        dedupLim=config.YAKE_DEDUP_THRESHOLD,
        top=config.YAKE_TOP_N,
        features=None,
    )
    results: list[list[str]] = []
    for sec in sections:
        text = " ".join(
            b.text for b in sec.blocks if b.block_type == "text" and b.text.strip()
        )
        if len(text.split()) < 5:  # YAKE needs some text to work with
            results.append([])
            continue
        try:
            kws = extractor.extract_keywords(text)
            results.append([kw for kw, _ in kws])
        except Exception as exc:
            logger.warning("YAKE failed on section %r: %s", sec.path, exc)
            results.append([])
    return results


def _compute_instructional_mass(nodes: list["_RawNode"]) -> list[float]:
    """
    Instructional mass formula (decided 2026-08-27):

        df_other(t) = number of OTHER source files in which term t was itself
                      extracted as a YAKE key term (case-folded exact match on the
                      extracted term string)
        mean_df_i   = mean of df_other(t) over node i's YAKE key_terms ([] → 0.0)
        raw_i       = token_count_i × (1 + ln(1 + mean_df_i))
        mass_i      = raw_i / sum(raw)

    NOTE — `df_other` measures key-term SALIENCE, not raw text presence. The
    `term_docs` index below is built only from each node's YAKE `key_terms`, so a
    term that appears in another document's body but did not make that document's
    top-N key terms contributes 0. This is narrower than the "number of other source
    files containing term t" that the plan's prose implies, and it is what the code
    has always done. It is arguably the better measure — a term that is prominent
    enough elsewhere to be extracted is stronger evidence of cross-document
    importance than a term that merely occurs — but it IS a narrowing, and it is
    flagged for the human to confirm. Broadening it to raw text presence
    would change every node's weight and therefore every exam allocation, so the
    change is not made unilaterally.

    Degrades correctly with a single document: every df_other = 0,
    mass collapses to normalised token_count. This is the live-demo path.
    """
    # Build {term → set(source_files)} across all nodes.
    term_docs: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        for term in node.key_terms:
            term_docs[term.lower()].add(node.source_file)

    def mean_df_other(node: "_RawNode") -> float:
        if not node.key_terms:
            return 0.0
        df_values = [
            len(term_docs.get(t.lower(), set()) - {node.source_file})
            for t in node.key_terms
        ]
        return sum(df_values) / len(df_values)

    raw = [
        n.token_count * (1.0 + math.log(1.0 + mean_df_other(n)))
        for n in nodes
    ]
    total = sum(raw)
    if total == 0:
        return [1.0 / len(nodes)] * len(nodes)

    masses = [r / total for r in raw]

    # Round and apply a largest-remainder correction to the node with the
    # highest mass so the sum stays within float tolerance.
    prec = config.COURSE_MAP_FLOAT_PRECISION
    rounded = [round(m, prec) for m in masses]
    diff = round(1.0 - sum(rounded), prec + 2)
    if diff != 0.0 and rounded:
        max_idx = masses.index(max(masses))
        rounded[max_idx] = round(rounded[max_idx] + diff, prec)
    return rounded


# ---------------------------------------------------------------------------
# Private data class (not a Pydantic model — only lives within this module)
# ---------------------------------------------------------------------------

class _RawNode:
    __slots__ = (
        "node_id", "path", "source_file", "page_span",
        "token_count", "chunk_ids", "key_terms", "flags",
    )

    def __init__(
        self,
        node_id: str,
        path: list[str],
        source_file: str,
        page_span: tuple[int, int],
        token_count: int,
        chunk_ids: list[str],
        key_terms: list[str],
        flags: NodeFlags,
    ) -> None:
        self.node_id = node_id
        self.path = path
        self.source_file = source_file
        self.page_span = page_span
        self.token_count = token_count
        self.chunk_ids = chunk_ids
        self.key_terms = key_terms
        self.flags = flags
