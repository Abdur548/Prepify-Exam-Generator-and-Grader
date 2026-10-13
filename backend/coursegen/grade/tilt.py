"""Turning a student's history into the next paper (§8).

Two separate jobs, both deterministic:

- **`mass_tilt`** — a per-node multiplier for `solve()`. Failed material pulls
  harder, mastered material lets go, and material never tested is left exactly as
  heavy as it was: untested is not weak.
- **`avoid_repeats`** — the solver is deterministic and generation is cached, so
  the same blueprint yields the same paper word for word. Any question the
  student has already been shown is re-specified to ask for new wording.
  Identical re-asking is the review set's job, never the next paper's.
"""
from __future__ import annotations

import math

from coursegen import config
from coursegen.contracts.item import ItemSpec
from coursegen.exam.allocate import avoid_spec_hash
from coursegen.grade.mastery import NodeMastery


def tilt_weight(mastery: float) -> float:
    """`TILT_MAX` at mastery 0, falling linearly to `TILT_MIN` at mastery 1."""
    if not (isinstance(mastery, (int, float)) and math.isfinite(mastery) and 0.0 <= mastery <= 1.0):
        raise ValueError(f"mastery must be in [0, 1], got {mastery!r}.")
    return config.TILT_MAX - (config.TILT_MAX - config.TILT_MIN) * mastery


def mass_tilt(mastery: dict[str, NodeMastery], node_ids: set[str]) -> dict[str, float]:
    """A tilt for every TESTED node still in the course map. Every other node is
    absent, which `solve()` reads as 1.0."""
    return {
        node_id: tilt_weight(m.mastery)
        for node_id, m in sorted(mastery.items())
        if node_id in node_ids
    }


def avoid_repeats(
    specs: list[ItemSpec],
    seen_hashes: set[str],
    seen_stems_by_node: dict[str, list[str]],
) -> tuple[list[ItemSpec], int]:
    """Re-specify every already-seen question to ask for new wording.

    Returns the specs, and how many were reworded. A seen spec carries the stems
    the student has been shown for its node, and a cache key that includes them —
    so a new question is generated, and the next time it too is avoided.
    """
    out: list[ItemSpec] = []
    reworded = 0
    for spec in specs:
        if spec.spec_hash not in seen_hashes:
            out.append(spec)
            continue
        stems = sorted(set(seen_stems_by_node.get(spec.node_id, [])))
        if not stems:
            # A submitted sheet always shows its stems. None on record means the
            # history is inconsistent, and guessing would repeat the question.
            raise ValueError(
                f"{spec.slot_id}: node {spec.node_id!r} was seen but has no stems on record."
            )
        out.append(spec.model_copy(update={
            "avoid_stems": stems,
            "spec_hash": avoid_spec_hash(spec.spec_hash, stems),
        }))
        reworded += 1
    return out, reworded
