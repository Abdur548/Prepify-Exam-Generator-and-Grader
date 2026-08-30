"""
Spec Amendment 01 stage 4 — the first real authored blueprint, end to end.

`template_ai_fundamentals_v1` is the first blueprint that names its own topics
and marks rather than deriving them, so this file is where option C stops being
a mechanism and starts being a product. Four things are proved here:

  1. the blueprint loads and validates against the shipped contract
  2. against a course map that DOES cover AI, every section fills and per-topic
     coverage names the nodes it matched
  3. against the WRONG subject, the topics with no material report zero and
     leave their slots unfilled — never a fallback to unrelated nodes. This is
     the property option C exists for and the most important test in the file.
  4. an end-to-end generation run reports how much of the paper is ungrounded

Zero network calls: the LLM is a stand-in that replays canned batches.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coursegen import config
from coursegen.contracts.blueprint import Blueprint
from coursegen.contracts.course_map import CourseMapNode
from coursegen.exam.allocate import solve
from coursegen.exam.generate import generate_exam

BLUEPRINT_PATH = Path(__file__).parent.parent / "coursegen/exam/blueprints/ai_fundamentals_v1.json"
AI_MAP_PATH = Path(__file__).parent / "fixtures/course_map_ai_fundamentals.json"
GENERIC_MAP_PATH = Path(__file__).parent / "fixtures/course_map_sample.json"


def _load_blueprint() -> Blueprint:
    return Blueprint(**json.loads(BLUEPRINT_PATH.read_text(encoding="utf-8")))


def _load_map(path: Path) -> list[CourseMapNode]:
    return [CourseMapNode(**n) for n in json.loads(path.read_text(encoding="utf-8"))]


@pytest.fixture
def blueprint() -> Blueprint:
    return _load_blueprint()


@pytest.fixture
def ai_map() -> list[CourseMapNode]:
    return _load_map(AI_MAP_PATH)


@pytest.fixture
def generic_map() -> list[CourseMapNode]:
    """A generic CS syllabus — the WRONG subject for this blueprint."""
    return _load_map(GENERIC_MAP_PATH)


def _fake_item(spec: Any) -> dict[str, Any]:
    """
    A canned model response shaped to the spec's item type.

    MCQ items must carry options and a correct_option or gate 4 rejects them,
    which triggers the regeneration pass and exhausts the stand-in client — the
    first draft of this file got that wrong, which is the validation working.
    Section A is TRUE_FALSE_SERIES with options_count 2: "True"/"False" sit
    inside the ±40% length band and neither is an "all of the above".
    """
    item: dict[str, Any] = {
        "slot_id": spec.slot_id,
        "stem": f"Question for {spec.slot_id}?",
        "model_answer": "An answer grounded in the span.",
        "explanation": "Because the source says so.",
        "source_ref": {"file": "ai_lectures.pdf", "pages": [1]},
    }
    if spec.item_type == "mcq":
        item["options"] = [{"label": "A", "text": "True"}, {"label": "B", "text": "False"}]
        item["correct_option"] = "A"
    return item


def _batches_for(specs: list[Any]) -> list[list[dict[str, Any]]]:
    return [
        [_fake_item(s) for s in specs[i: i + config.BATCH_SIZE]]
        for i in range(0, len(specs), config.BATCH_SIZE)
    ]


class FakeLLMClient:
    """Replays canned batches. Never touches the network."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self.calls: list[Any] = []
        self.budget = type("Budget", (), {"calls_used": 0, "tokens_used": 0})()

    def call(self, messages, response_schema=None):
        self.calls.append((messages, response_schema))
        self.budget.calls_used += 1
        self.budget.tokens_used += sum(len(m["content"]) for m in messages) // 4
        if not self._batches:
            raise AssertionError("FakeLLMClient called more times than expected")
        return {"items": self._batches.pop(0)}


# ---------------------------------------------------------------------------
# 1 — the blueprint is a valid blueprint
# ---------------------------------------------------------------------------

class TestBlueprintValidates:
    def test_loads_against_the_shipped_contract(self, blueprint: Blueprint) -> None:
        assert blueprint.blueprint_id == "template_ai_fundamentals_v1"
        assert blueprint.total_marks == 100
        assert len(blueprint.sections) == 5

    def test_marks_sum_to_the_declared_total(self, blueprint: Blueprint) -> None:
        """The Blueprint validator enforces this, so loading at all proves it —
        asserted explicitly because a paper printing 'Total: 100' over 95 marks
        of questions is the kind of error nobody notices until an exam hall."""
        computed = sum(s.count * s.marks_each for s in blueprint.sections)
        assert computed == blueprint.total_marks == 100

    def test_every_section_names_a_topic(self, blueprint: Blueprint) -> None:
        """An authored blueprint whose sections had no topics would silently be
        the derived path wearing a costume."""
        assert all(s.topic for s in blueprint.sections)

    def test_formats_are_known_values(self, blueprint: Blueprint) -> None:
        for s in blueprint.sections:
            assert s.format_requirement in config.KNOWN_FORMAT_REQUIREMENTS

    def test_bloom_labels_match_the_declared_balance_vocabulary(
        self, blueprint: Blueprint
    ) -> None:
        """
        The source uses compound labels (REMEMBER_UNDERSTAND) where the shipped
        blueprints use single lowercase levels. They are carried through
        unchanged deliberately: if the section blooms and `cognitive_balance`
        used different vocabularies, the declared-vs-realised comparison would
        compare nothing and silently never fire.
        """
        assert blueprint.cognitive_balance is not None
        section_levels = {lv for s in blueprint.sections for lv in s.bloom}
        assert section_levels == set(blueprint.cognitive_balance)


# ---------------------------------------------------------------------------
# 2 — against material that DOES cover the subject
# ---------------------------------------------------------------------------

class TestAgainstAIMaterial:
    def test_every_slot_fills(self, blueprint: Blueprint, ai_map) -> None:
        items, report = solve(ai_map, blueprint)
        assert report.slots_filled == report.slots_total == 20
        assert report.fill_ratio == pytest.approx(1.0)
        assert report.unfilled_slots == []

    def test_every_topic_matched_material(self, blueprint: Blueprint, ai_map) -> None:
        items, report = solve(ai_map, blueprint)
        assert len(report.per_topic) == 5
        for t in report.per_topic:
            assert t.matched_node_count > 0, f"{t.topic!r} matched nothing"
            assert t.slots_filled == t.slots_requested

    def test_items_come_only_from_nodes_that_matched_their_topic(
        self, blueprint: Blueprint, ai_map
    ) -> None:
        """The whole point of grounding an authored topic: a question about
        Markov Decision Processes must be drawn from a node about them."""
        items, report = solve(ai_map, blueprint)
        matched_by_section = {t.section_id: set(t.matched_node_ids) for t in report.per_topic}
        for item in items:
            section_id = item.slot_id.split("-")[0]
            assert item.node_id in matched_by_section[section_id], (
                f"{item.slot_id} drawn from {item.node_id}, "
                f"which did not match section {section_id}'s topic"
            )


# ---------------------------------------------------------------------------
# 3 — against the WRONG subject: the property option C exists for
# ---------------------------------------------------------------------------

class TestAgainstTheWrongSubject:
    """
    A student uploads a generic CS syllabus and asks for an AI final. The paper
    must come out visibly incomplete, naming what it could not cover — never
    quietly filled with questions drawn from unrelated material.
    """

    def test_the_paper_is_visibly_incomplete(self, blueprint: Blueprint, generic_map) -> None:
        items, report = solve(generic_map, blueprint)
        assert report.fill_ratio < 1.0
        assert report.unfilled_slots, "an impossible paper reported as complete"

    def test_topics_with_no_material_report_zero(self, blueprint: Blueprint, generic_map) -> None:
        items, report = solve(generic_map, blueprint)
        unmatched = [t for t in report.per_topic if t.matched_node_count == 0]
        assert unmatched, "expected at least one AI topic absent from a generic CS map"
        for t in unmatched:
            assert t.best_score == 0.0
            assert t.slots_filled == 0

    def test_a_missing_topic_is_named_in_the_warnings(
        self, blueprint: Blueprint, generic_map
    ) -> None:
        """
        Silence is the failure mode. If the material does not cover a topic the
        paper asks for, the report has to say which one — that sentence is what
        tells a student their slides are the wrong slides.
        """
        items, report = solve(generic_map, blueprint)
        unmatched = [t for t in report.per_topic if t.matched_node_count == 0]
        joined = " ".join(report.warnings)
        for t in unmatched:
            assert t.topic in joined, f"{t.topic!r} went missing without a warning"

    def test_no_fallback_to_unrelated_nodes(self, blueprint: Blueprint, generic_map) -> None:
        """
        The single most important assertion in this file. A fallback to the
        unfiltered course map would fill every slot and look like success while
        asking a student about material the topic does not cover.
        """
        items, report = solve(generic_map, blueprint)
        matched_by_section = {t.section_id: set(t.matched_node_ids) for t in report.per_topic}
        for item in items:
            section_id = item.slot_id.split("-")[0]
            assert item.node_id in matched_by_section[section_id], (
                f"{item.slot_id} fell back to {item.node_id}, outside its topic's matches"
            )


# ---------------------------------------------------------------------------
# 4 — cognitive balance is measured by MARKS, not by item count
# ---------------------------------------------------------------------------

class TestCognitiveBalanceIsWeightedByMarks:
    """
    Caught on this blueprint, which is exactly on target by marks and looks 30
    points adrift by item count:

        level                 declared   by ITEMS   by MARKS
        APPLY_ANALYZE             0.70       0.40       0.70
        REMEMBER_UNDERSTAND       0.20       0.50       0.20

    Ten 2-mark true/false items outnumber one 20-mark trace question ten to one
    while carrying a fifth of the weight. A false alarm costs as much as a miss:
    this warning is the only thing that would catch a paper drifting to easy
    recall questions, and one spurious firing teaches everyone to ignore it.
    """

    def test_realised_matches_the_declared_target(self, blueprint: Blueprint, ai_map) -> None:
        items, report = solve(ai_map, blueprint)
        assert blueprint.cognitive_balance is not None
        for level, declared in blueprint.cognitive_balance.items():
            assert report.bloom_realised[level] == pytest.approx(declared, abs=1e-9), (
                f"{level}: declared {declared}, realised {report.bloom_realised.get(level)}"
            )

    def test_an_on_target_paper_raises_no_balance_warning(
        self, blueprint: Blueprint, ai_map
    ) -> None:
        items, report = solve(ai_map, blueprint)
        assert [w for w in report.warnings if "ognitive balance" in w] == []

    def test_the_two_measures_genuinely_differ_on_this_paper(
        self, blueprint: Blueprint, ai_map
    ) -> None:
        """Guards the fix itself: if marks and item counts happened to agree,
        the tests above would pass under the old buggy measure too."""
        items, _ = solve(ai_map, blueprint)
        by_items: dict[str, int] = {}
        for i in items:
            by_items[i.bloom] = by_items.get(i.bloom, 0) + 1
        total = len(items)
        assert by_items["REMEMBER_UNDERSTAND"] / total != pytest.approx(0.20, abs=0.05)


# ---------------------------------------------------------------------------
# 5 — end to end, and the paper says how much of itself is ungrounded
# ---------------------------------------------------------------------------

class TestEndToEndGeneration:
    def test_manifest_reports_the_synthesis_share_and_warns(
        self, blueprint: Blueprint, ai_map, tmp_path: Path
    ) -> None:
        """
        Three of the five tasks ask the model to INVENT their artifact — a novel
        game tree, a novel word problem, a state-space environment. That is 8 of
        20 items, above SYNTHESIS_ITEM_WARN_RATIO, so the warning fires.

        It is meant to. Nearly half this paper is not answerable from the
        student's own slides, and saying so is the entire reason stage 3 exists.
        A test that wanted this warning absent would be the wrong test.
        """
        specs, _ = solve(ai_map, blueprint)
        spans = {sid: f"Course content for {sid}." for s in specs for sid in s.span_ids}

        result = generate_exam(
            specs=specs,
            course_map=ai_map,
            span_text_by_id=spans,
            llm_client=FakeLLMClient(_batches_for(specs)),
            output_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            blueprint_id=blueprint.blueprint_id,
        )

        manifest = result.manifest
        assert manifest["blueprint_id"] == "template_ai_fundamentals_v1"

        grounding = manifest["grounding"]
        assert grounding["items_total"] == 20
        assert grounding["synthesis_items"] == 8
        assert grounding["synthesis_ratio"] == pytest.approx(0.4)
        assert grounding["synthesis_ratio"] > config.SYNTHESIS_ITEM_WARN_RATIO

        assert any("synthesis" in w.lower() for w in manifest["warnings"]), (
            "40% of the paper is ungrounded and nothing said so"
        )

    def test_written_manifest_matches_the_returned_one(
        self, blueprint: Blueprint, ai_map, tmp_path: Path
    ) -> None:
        specs, _ = solve(ai_map, blueprint)
        spans = {sid: "Content." for s in specs for sid in s.span_ids}
        result = generate_exam(
            specs=specs, course_map=ai_map, span_text_by_id=spans,
            llm_client=FakeLLMClient(_batches_for(specs)), output_dir=tmp_path,
            cache_dir=tmp_path / "cache", blueprint_id=blueprint.blueprint_id,
        )
        on_disk = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
        assert on_disk == result.manifest
