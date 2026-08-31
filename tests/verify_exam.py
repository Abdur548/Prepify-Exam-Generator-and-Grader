"""Independent post-hoc verification of a generated paper. Zero LLM calls.

Deliberately does NOT import the validation gates. Those gates decide what
reaches the paper; asking them afterwards whether the paper is correct only asks
whether they agree with themselves. This reads the artifacts on disk — the
cached items and the blueprint — and checks them against the course material and
against what the blueprint asked for.

That independence is the point. It is what caught the fabricated citations: all
four gates passed a paper in which every item cited a file that does not exist
('source_span', page [1]), because no gate ever compared `source_ref` to the
corpus. A gate suite reports on what it was told to look at.

Not pytest-collected (no `test_` prefix): it needs a real run's output.

    python tests/verify_exam.py [--output output] [--blueprint ai_fundamentals_v1]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BLUEPRINT_DIR = ROOT / "coursegen" / "exam" / "blueprints"


def _ascii(s: object, n: int = 120) -> str:
    """Windows consoles are cp1252; lecture text is not."""
    return " ".join(str(s).split()).encode("ascii", "replace").decode()[:n]


def verify(output_dir: Path, blueprint_id: str, data_dir: Path) -> int:
    bp = json.loads((BLUEPRINT_DIR / f"{blueprint_id}.json").read_text(encoding="utf-8"))
    items: dict[str, dict] = {}
    for f in sorted((output_dir / "cache").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        items[d["slot_id"]] = d
    if not items:
        print(f"ERROR: no cached items under {output_dir / 'cache'}. Run a generation first.")
        return 1

    failures: list[str] = []

    print("=" * 68); print("1. BLUEPRINT COMPLIANCE"); print("=" * 68)
    marks_exp = marks_got = 0
    for s in bp["sections"]:
        sid, n, per = s["section_id"], s["count"], s["marks_each"]
        got = [k for k in items if k.startswith(f"{sid}-")]
        marks_exp += n * per; marks_got += len(got) * per
        # item_type is a property of the SPEC; on the item it shows up as options.
        want_mcq = s["item_type"] == "mcq"
        bad = [k for k in got if bool(items[k].get("options")) != want_mcq]
        if len(got) != n:
            failures.append(f"section {sid}: {len(got)}/{n} items")
        if bad:
            failures.append(f"section {sid}: wrong item type for {bad}")
        print(f"  [{'OK ' if len(got) == n and not bad else 'BAD'}] {sid}: {len(got)}/{n}"
              f" x {per} marks  type={s['item_type']}")
    print(f"  TOTAL: {len(items)} items, {marks_got}/{marks_exp} marks"
          f" ({100 * marks_got / marks_exp:.0f}% of paper)")

    print("\n" + "=" * 68); print("2. CITATIONS RESOLVE TO REAL COURSE FILES"); print("=" * 68)
    real = {p.name for p in data_dir.rglob("*") if p.suffix.lower() in {".pdf", ".pptx", ".docx"}}
    fabricated = [(k, (v.get("source_ref") or {}).get("file"))
                  for k, v in items.items()
                  if (v.get("source_ref") or {}).get("file") not in real]
    print(f"  course files on disk    : {len(real)}")
    print(f"  items citing a REAL file: {len(items) - len(fabricated)}/{len(items)}")
    if fabricated:
        failures.append(f"{len(fabricated)} fabricated citation(s)")
        for k, f in fabricated[:5]:
            print(f"    FABRICATED {k}: {f!r}")
    pages = {(v["source_ref"]["file"], tuple(v["source_ref"]["pages"])) for v in items.values()}
    print(f"  distinct file+page refs : {len(pages)}"
          f"{'   <-- all identical means fabricated' if len(pages) == 1 else ''}")

    print("\n" + "=" * 68); print("3. MCQ HYGIENE AND SHUFFLE"); print("=" * 68)
    mcqs = {k: v for k, v in items.items() if v.get("options")}
    spread = collections.Counter(v["correct_option"] for v in mcqs.values())
    print(f"  mcq items: {len(mcqs)}   correct-answer spread: {dict(sorted(spread.items()))}")
    for k, v in sorted(mcqs.items()):
        labels = [o["label"] for o in v["options"]]
        if labels != sorted(labels) or v["correct_option"] not in labels:
            failures.append(f"{k}: labels {labels}, correct {v['correct_option']!r}")
            print(f"    BROKEN {k}: labels={labels} correct={v['correct_option']!r}")
    # One permutation reused across the paper puts the answer in the same slot
    # every time — a paper answerable without reading it.
    if len(mcqs) > 3 and len(spread) == 1:
        failures.append("every MCQ has the same correct position — shuffle is not varying")
        print("    BROKEN: identical correct-answer position on every MCQ")

    print("\n" + "=" * 68); print("4. EVERY ITEM CARRIES AN ANSWER"); print("=" * 68)
    empty = [k for k, v in items.items() if not (v.get("model_answer") or "").strip()]
    print(f"  items with a non-empty model_answer: {len(items) - len(empty)}/{len(items)}")
    if empty:
        failures.append(f"empty model_answer: {empty}")

    print("\n" + "=" * 68)
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASSED — {len(items)} items, {marks_got}/{marks_exp} marks, all citations resolve.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="output", type=Path)
    ap.add_argument("--blueprint", default="ai_fundamentals_v1")
    ap.add_argument("--data", default="data", type=Path)
    a = ap.parse_args()
    return verify(a.output.resolve(), a.blueprint, a.data.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
