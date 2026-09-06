"""The paper as structured data — the contract the frontend renders against.

These pin the distinctions a renderer cannot recover if the document loses them:
an unfilled slot versus a closed-up gap, a synthesis item versus a sourced one,
and a factuality gate that did not run versus one that returned nothing good.
"""
from __future__ import annotations

import pytest

from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem, ItemSpec, MCQOption, SourceRef
from coursegen.exam.paper import EXCERPT_MAX_CHARS, build_paper


def _blueprint() -> Blueprint:
    return Blueprint(
        blueprint_id="bp", title="Test Paper", total_marks=30, duration_minutes=60,
        sections=[
            SectionSpec(section_id="A", title="Recall", item_type="mcq",
                        count=2, marks_each=5, bloom=["remember"]),
            SectionSpec(section_id="B", title="Applied", item_type="long",
                        count=1, marks_each=20, bloom=["apply"], grounding="synthesis"),
        ],
    )


def _spec(slot: str, grounding: str = "span", marks: int = 5,
          item_type: str = "mcq", spans=("c1",)) -> ItemSpec:
    return ItemSpec(
        slot_id=slot, item_type=item_type, marks=marks, bloom="remember",
        node_id="n1", span_ids=list(spans), eligibility=[], spec_hash=f"h{slot}",
        grounding=grounding,
    )


def _item(slot: str, mcq: bool = True) -> GeneratedItem:
    return GeneratedItem(
        slot_id=slot, stem=f"Question {slot}?",
        options=[MCQOption(label="A", text="first"), MCQOption(label="B", text="second")] if mcq else None,
        correct_option="A" if mcq else None,
        model_answer="A" if mcq else "A written answer.",
        explanation="because", source_ref=SourceRef(file="deck.pdf", pages=[7]),
    )


def _coverage(slots_total=3, filled=3, unfilled=()) -> CoverageReport:
    return CoverageReport(
        blueprint_id="bp", nodes_total=10, nodes_covered=3, coverage_ratio=0.3,
        slots_total=slots_total, slots_filled=filled, fill_ratio=filled / slots_total,
        slots_by_mass=filled, slots_by_fallthrough=0, allocation_fidelity=1.0,
        mass_covered=0.3,
        unfilled_slots=list(unfilled), warnings=[], per_node=[],
    )


class TestProvenance:

    def test_a_sourced_item_carries_file_pages_and_excerpt(self) -> None:
        """The three things the provenance panel needs, in one place."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
            span_text_by_id={"c1": "BFS expands the shallowest node first."},
        )
        item = paper["sections"][0]["items"][0]
        assert item["from_material"] is True
        assert item["source"] == {"file": "deck.pdf", "pages": [7]}
        assert item["source_excerpt"] == "BFS expands the shallowest node first."

    def test_a_synthesis_item_is_marked_and_carries_no_excerpt(self) -> None:
        """A synthesis item's span is CONTEXT, not an answer location.

        Shipping an excerpt for it would invite the UI to present the passage as
        where the answer lives, and a student would hunt for something that was
        never there. The manifest has always counted these; this is the first time
        the individual question can be marked.
        """
        paper = build_paper(
            items=[_item("B-01", mcq=False)],
            specs=[_spec("B-01", grounding="synthesis", marks=20, item_type="long")],
            blueprint=_blueprint(), coverage=_coverage(), title="T",
            span_text_by_id={"c1": "Some context about search."},
        )
        item = paper["sections"][1]["items"][0]
        assert item["from_material"] is False
        assert item["source_excerpt"] is None

    def test_synthesis_items_are_counted_in_the_summary(self) -> None:
        paper = build_paper(
            items=[_item("A-01"), _item("B-01", mcq=False)],
            specs=[_spec("A-01"), _spec("B-01", grounding="synthesis")],
            blueprint=_blueprint(), coverage=_coverage(), title="T",
        )
        assert paper["summary"]["synthesis_items"] == 1

    def test_a_long_excerpt_is_truncated_on_a_word_boundary(self) -> None:
        long_text = "word " * 500
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T", span_text_by_id={"c1": long_text},
        )
        excerpt = paper["sections"][0]["items"][0]["source_excerpt"]
        assert len(excerpt) <= EXCERPT_MAX_CHARS + 1
        assert excerpt.endswith("…")


class TestUnfilledSlots:

    def test_an_unfilled_slot_appears_as_a_gap_not_an_omission(self) -> None:
        """The renderer must be able to show where a question should have been.

        Silently closing up leaves a student wondering why Section A is short, and
        makes a degraded paper indistinguishable from a complete one.
        """
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01"), _spec("A-02")],
            blueprint=_blueprint(),
            coverage=_coverage(slots_total=3, filled=2, unfilled=["A-02"]), title="T",
        )
        items = paper["sections"][0]["items"]
        assert [i["slot_id"] for i in items] == ["A-01", "A-02"]
        assert items[0]["filled"] is True
        assert items[1]["filled"] is False
        assert items[1]["marks"] == 5      # the gap still knows what it was worth

    def test_a_slot_lost_at_a_gate_is_a_gap_even_though_allocation_succeeded(self) -> None:
        """The case every fixture had missed, and a real run produced.

        There are two different reasons a slot ends up empty and they are recorded
        in different places. `coverage.unfilled_slots` is the solver's: it could
        not place a question there. But a slot that WAS placed, WAS written and
        then lost its item at a validation gate never appears there — allocation
        succeeded, so `unfilled_slots` stays empty and `fill_ratio` stays 1.0.

        On 2026-09-03 a live quiz_default run hit exactly this: an MCQ was rejected
        twice for an option-length outlier, and the paper came back 6 items over 7
        allocated slots with `unfilled_slots: []`. A client counting that array
        printed "0 questions could not be built" directly above the gap it had just
        drawn. `filled` is the field that answers "is there a question here"; the
        coverage report answers a different question.

        **Updated 2026-09-06 (F2).** The two assertions below used to require
        `unfilled_slots == []` and `fill_ratio == 1.0` — they were pinning the
        allocator's numbers into the paper's summary, which is the defect, not the
        contract. The summary now reports DELIVERY and keeps the allocator's view
        under `allocation_*`. The lesson is unchanged and is asserted at the end:
        the two counts still disagree, and a renderer must take its gap count from
        the items.
        """
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01"), _spec("A-02")],
            blueprint=_blueprint(),
            # Allocation was perfect. The item is missing anyway.
            coverage=_coverage(slots_total=2, filled=2, unfilled=[]), title="T",
        )
        summary = paper["summary"]
        # Delivery: the slot the gate destroyed is missing, and says so.
        assert summary["unfilled_slots"] == ["A-02"]
        assert summary["fill_ratio"] == 0.5
        # Allocation: still perfect, still reported, just not as the paper's own.
        assert summary["allocation_unfilled_slots"] == []
        assert summary["allocation_fill_ratio"] == 1.0

        items = paper["sections"][0]["items"]
        assert [i["filled"] for i in items] == [True, False]
        # The two counts disagree, and that is the point: a renderer must take the
        # gap count from the items, never from the coverage report.
        assert sum(1 for i in items if not i["filled"]) == 1
        assert summary["marks_available"] < paper["total_marks"]

    def test_the_instructions_count_delivered_questions_not_slots(self) -> None:
        """F2, on a paper that actually has a gap.

        Every other instruction fixture has no missing questions, so "count the
        slots" and "count the delivered items" agree and neither mutation shows.
        This is the shape that was wrong on every real run: 2 slots in Section A,
        1 delivered, and a block that promised 2.
        """
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01"), _spec("A-02")],
            blueprint=_blueprint(),
            coverage=_coverage(slots_total=2, filled=2, unfilled=[]), title="T",
        )
        joined = " ".join(paper["instructions"])

        assert "There are 1 question in this paper." in joined, joined
        assert "2 questions in this paper" not in joined, (
            "the block is counting slots, including the one with no question in it"
        )
        # 5 marks delivered against a 30-mark blueprint.
        assert "carries 5 marks" in joined

        section_a = paper["sections"][0]
        assert len(section_a["items"]) == 2
        assert sum(1 for i in section_a["items"] if i["filled"]) == 1
        assert section_a["instruction"] == "1 question, 5 marks each.", (
            "the section line promises questions the section does not hold"
        )

    def test_a_slot_the_solver_never_placed_is_also_missing(self) -> None:
        """The other half. `unfilled_slots` must name both kinds of loss, or a
        client has to union two fields to answer "what is not here"."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01"), _spec("A-02")],
            blueprint=_blueprint(),
            coverage=_coverage(slots_total=3, filled=2, unfilled=["B-01"]), title="T",
        )
        summary = paper["summary"]
        # A-02 was placed and lost at a gate; B-01 was never placed at all.
        assert summary["unfilled_slots"] == ["A-02", "B-01"]
        assert summary["allocation_unfilled_slots"] == ["B-01"]

    def test_marks_available_counts_only_filled_slots(self) -> None:
        """What the paper is actually worth, not what it was asked to be worth."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01"), _spec("A-02")],
            blueprint=_blueprint(), coverage=_coverage(3, 2, ["A-02"]), title="T",
        )
        assert paper["summary"]["marks_available"] == 5
        assert paper["total_marks"] == 30


class TestFactuality:

    def test_a_verdict_is_carried_through(self) -> None:
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T", factuality={"A-01": "SUPPORTED"},
        )
        assert paper["sections"][0]["items"][0]["factuality"] == "SUPPORTED"

    def test_a_gate_that_did_not_run_reports_none_not_a_negative(self) -> None:
        """None means "not checked". It must not read as "checked and failed"."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )
        assert paper["sections"][0]["items"][0]["factuality"] is None


class TestStructure:

    def test_sections_follow_blueprint_order(self) -> None:
        paper = build_paper(
            items=[_item("A-01"), _item("B-01", mcq=False)],
            specs=[_spec("A-01"), _spec("B-01", grounding="synthesis")],
            blueprint=_blueprint(), coverage=_coverage(), title="T",
        )
        assert [s["section_id"] for s in paper["sections"]] == ["A", "B"]

    def test_section_instruction_uses_the_filled_count(self) -> None:
        """An instruction promising two questions above a section holding one is
        worse than no instruction."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )
        assert paper["sections"][0]["instruction"] == "1 question, 5 marks each."

    def test_general_instructions_describe_the_paper_as_delivered(self) -> None:
        """Renamed from `..._are_derived_from_the_blueprint`, which is what they
        used to be and is exactly the defect (F2).

        The block said "There are 7 questions in this paper. The paper carries 20
        marks" above a paper holding 5 questions and 13 marks — all four lines
        false at once, and sitting two lines above the client's own shortfall
        banner in the same viewport. `duration_minutes` stays blueprint-derived
        because time allowed is not affected by a lost question.
        """
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )
        joined = " ".join(paper["instructions"])
        assert "1 question in this paper" in joined
        # 5, the marks actually on the page — not the blueprint's 30.
        assert "5 marks and allows" in joined
        assert "30 marks" not in joined, "the paper must not promise the blueprint's marks"
        assert "60 minutes" in joined
        assert "Section A" in joined and "Section B" in joined

    def test_a_section_with_nothing_in_it_says_so_readably(self) -> None:
        """"0 questions, 5 marks each" is arithmetic, not a sentence. The section
        still appears on the paper with its gaps rendered, so it needs a line."""
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )
        section_b = paper["sections"][1]
        assert section_b["items"] == [] or all(
            not i["filled"] for i in section_b["items"]
        )
        assert "no questions" in " ".join(paper["instructions"]).lower()

    def test_mcq_options_survive_as_label_text_pairs(self) -> None:
        paper = build_paper(
            items=[_item("A-01")], specs=[_spec("A-01")], blueprint=_blueprint(),
            coverage=_coverage(), title="T",
        )
        assert paper["sections"][0]["items"][0]["options"] == [
            {"label": "A", "text": "first"}, {"label": "B", "text": "second"}
        ]
