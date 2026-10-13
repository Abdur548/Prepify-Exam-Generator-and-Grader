"""G5 — review sets built from the questions a student missed.

The rules under test, all from §7.4: the same question until it has been passed
once, then a new question on the same passage; options reordered so position
cannot be memorised, with the key following its text; a new question that cannot
be written, or that comes back as one the student has seen, falls back to the
original and says so.
"""
from __future__ import annotations

from coursegen import config
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.contracts.item import GeneratedItem, MCQOption, SourceRef
from coursegen.exam.validate import _shuffle_options
from coursegen.grade.mastery import cards
from coursegen.grade.review import build_review, needs_model
from coursegen.grade.service import submit_attempt
from coursegen.grade.store import Store
from tests.test_g1_service import NOW, _paper_sheet_id

LECTURE = {"n1": "03_search.pdf", "n2": "03_search.pdf", "n3": "06_csp.pdf", "n4": "06_csp.pdf"}


def _nodes(*ids) -> list[CourseMapNode]:
    return [
        CourseMapNode(
            node_id=n, path=[LECTURE[n], f"Section {n}"], source_file=LECTURE[n],
            page_span=(1, 2), token_count=100, chunk_ids=[f"c-{n}"], key_terms=[],
            flags=NodeFlags(), instructional_mass=0.25,
        )
        for n in (ids or ("n1", "n2", "n3", "n4"))
    ]


def _items(review: dict) -> list[dict]:
    return review["sheet"]["sections"][0]["items"]


def _miss_a02(store) -> str:
    """One attempt: A-01 right, A-02 (node n2) wrong, the written ones right by contest."""
    sheet_id = _paper_sheet_id()
    r = submit_attempt(store, sheet_id, {"A-01": "B", "A-02": "A", "B-01": "q", "C-01": "t"}, now=NOW)
    from coursegen.grade.service import contest
    contest(store, r["attempt_id"], "B-01", 4, now=NOW)
    contest(store, r["attempt_id"], "C-01", 10, now=NOW)
    return sheet_id


def _make_fresh_due(store) -> str:
    """Miss A-02, pass its identical re-test, then miss it again on a re-take:
    the card is back in box 1 and, having been passed once, asks for new wording."""
    sheet_id = _miss_a02(store)
    review = build_review(store, _nodes(), now=NOW, generate_fresh=None)
    (item,) = _items(review)
    submit_attempt(store, review["sheet"]["sheet_id"], {item["slot_id"]: item["correct_option"]}, now=NOW)
    # A-02 left blank: a miss. The written answers are given (and stay unmarked, so
    # they are no evidence either way) — leaving them blank would be two more misses.
    submit_attempt(store, sheet_id, {"A-01": "B", "B-01": "q", "C-01": "t"}, now=NOW)
    return sheet_id


class StubGenerator:
    def __init__(self, stem: str | None = "A brand-new question about n2?") -> None:
        self.stem, self.specs = stem, []

    def __call__(self, specs):
        self.specs.extend(specs)
        if self.stem is None:
            return {}
        return {
            s.slot_id: GeneratedItem(
                slot_id=s.slot_id, stem=self.stem,
                options=[MCQOption(label="A", text="new wrong"), MCQOption(label="B", text="new right")],
                correct_option="B", model_answer="B. new right", explanation="Fresh.",
                source_ref=SourceRef(file="03_search.pdf", pages=[30]),
            )
            for s in specs
        }


class TestShuffleSeed:

    def test_the_default_seed_is_unchanged(self) -> None:
        item = GeneratedItem(
            slot_id="A-01", stem="Q?", options=[MCQOption(label=l, text=t) for l, t in zip("ABCD", "wxyz")],
            correct_option="A", model_answer="A. w", explanation="e",
            source_ref=SourceRef(file="f", pages=[1]),
        )
        assert _shuffle_options(item) == _shuffle_options(item, seed=f"{config.MCQ_SHUFFLE_SEED}:A-01")

    def test_another_seed_keeps_the_key_on_its_text(self) -> None:
        item = GeneratedItem(
            slot_id="A-01", stem="Q?", options=[MCQOption(label=l, text=t) for l, t in zip("ABCD", "wxyz")],
            correct_option="C", model_answer="C. y", explanation="e",
            source_ref=SourceRef(file="f", pages=[1]),
        )
        for seed in ("one", "two", "three"):
            out = _shuffle_options(item, seed=seed)
            keyed = next(o for o in out.options if o.label == out.correct_option)
            assert keyed.text == "y" and out.model_answer == f"{out.correct_option}. y"


class TestNothingDue:

    def test_no_history_means_no_review(self) -> None:
        with Store() as store:
            review = build_review(store, _nodes(), now=NOW, generate_fresh=None)
        assert review == {"due": 0, "sheet": None, "fallbacks": 0, "orphans": 0, "warnings": []}


class TestIdentical:

    def test_a_miss_comes_back_as_the_same_question(self) -> None:
        with Store() as store:
            sheet_id = _miss_a02(store)
            review = build_review(store, _nodes(), now=NOW, generate_fresh=None)
            stored = store.get_sheet(review["sheet"]["sheet_id"])
        (item,) = _items(review)
        assert review["due"] == 1 and review["sheet"]["kind"] == "review"
        assert item["slot_id"] == "R-01" and item["form"] == "identical"
        assert item["retest_of"] == {"sheet_id": sheet_id, "slot_id": "A-02"}
        assert item["stem"] == "Which is right (A-02)?"
        assert stored.kind == "review" and stored.items[0].retest_of == (sheet_id, "A-02")

    def test_its_options_are_reordered_with_the_key_on_the_same_text(self) -> None:
        with Store() as store:
            _miss_a02(store)
            (item,) = _items(build_review(store, _nodes(), now=NOW, generate_fresh=None))
        assert sorted(o["text"] for o in item["options"]) == ["right", "wrong"]
        assert [o["label"] for o in item["options"]] == ["A", "B"]
        keyed = next(o for o in item["options"] if o["label"] == item["correct_option"])
        assert keyed["text"] == "right"
        assert item["model_answer"] == f"{item['correct_option']}. right"

    def test_it_carries_the_origins_source(self) -> None:
        with Store() as store:
            _miss_a02(store)
            (item,) = _items(build_review(store, _nodes(), now=NOW, generate_fresh=None))
        assert item["source"] == {"file": "03_search.pdf", "pages": [30]} and item["node_id"] == "n2"

    def test_building_it_twice_gives_the_same_sheet(self) -> None:
        with Store() as store:
            _miss_a02(store)
            a = build_review(store, _nodes(), now=NOW, generate_fresh=None)
            b = build_review(store, _nodes(), now="2027-01-01T00:00:00", generate_fresh=None)
        assert a["sheet"]["sheet_id"] == b["sheet"]["sheet_id"]

    def test_needs_no_model(self) -> None:
        with Store() as store:
            _miss_a02(store)
            assert needs_model(store, _nodes()) is False

    def test_answering_it_moves_the_card_up(self) -> None:
        with Store() as store:
            sheet_id = _miss_a02(store)
            review = build_review(store, _nodes(), now=NOW, generate_fresh=None)
            (item,) = _items(review)
            submit_attempt(store, review["sheet"]["sheet_id"], {"R-01": item["correct_option"]}, now=NOW)
            card = cards(store.all_answer_rows())[(sheet_id, "A-02")]
        assert (card.box, card.passed_once) == (2, True)


class TestFresh:

    def test_once_passed_a_miss_asks_for_new_wording(self) -> None:
        with Store() as store:
            _make_fresh_due(store)
            assert needs_model(store, _nodes()) is True
            gen = StubGenerator()
            review = build_review(store, _nodes(), now=NOW, generate_fresh=gen)
        (spec,) = gen.specs
        assert spec.avoid_stems == ["Which is right (A-02)?"]
        assert spec.spec_hash != "h2" and spec.node_id == "n2" and spec.group_id is None
        (item,) = _items(review)
        assert item["form"] == "fresh" and item["stem"] == "A brand-new question about n2?"
        assert item["source"] == {"file": "03_search.pdf", "pages": [30]}

    def test_nothing_written_falls_back_to_the_original(self) -> None:
        with Store() as store:
            _make_fresh_due(store)
            review = build_review(store, _nodes(), now=NOW, generate_fresh=StubGenerator(stem=None))
        (item,) = _items(review)
        assert item["form"] == "fallback" and item["stem"] == "Which is right (A-02)?"
        assert review["fallbacks"] == 1

    def test_a_new_question_that_repeats_an_old_one_falls_back(self) -> None:
        with Store() as store:
            _make_fresh_due(store)
            review = build_review(store, _nodes(), now=NOW,
                                  generate_fresh=StubGenerator(stem="  which is RIGHT (A-02)? "))
        assert _items(review)[0]["form"] == "fallback"

    def test_no_generator_falls_back_and_says_so(self) -> None:
        with Store() as store:
            _make_fresh_due(store)
            review = build_review(store, _nodes(), now=NOW, generate_fresh=None)
        assert _items(review)[0]["form"] == "fallback"
        assert any("new question" in w for w in review["warnings"])


class TestOrphans:

    def test_cards_for_material_no_longer_ingested_are_left_out_and_counted(self) -> None:
        """Review Focus 5."""
        with Store() as store:
            _miss_a02(store)
            review = build_review(store, _nodes("n1", "n3", "n4"), now=NOW, generate_fresh=None)
        assert review["due"] == 0 and review["sheet"] is None and review["orphans"] > 0
