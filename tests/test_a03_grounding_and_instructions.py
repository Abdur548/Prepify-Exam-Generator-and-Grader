"""
Spec Amendment 01, stage 3 — `grounding`, `generation_instructions`, and making
ungrounded questions VISIBLE.

Stages 1 and 2 were pure allocation. This one touches the prompt, so it is also
the first place where a field can change what the model is asked while leaving
the cache key alone — the defect `group_id` was quietly carrying.

Four properties these gates exist to protect:

    - a SYNTHESIS item is neither failed nor silently passed by gate 2. "We could
      not check" (`skipped`) and "there is nothing to check against"
      (`not_applicable`) are different facts and the report keeps them apart;
    - the count of ungrounded questions reaches `run_manifest.json` on EVERY run
      and warns past `config.SYNTHESIS_ITEM_WARN_RATIO`, because a student cannot
      revise a novel game tree from their own slides (§7);
    - `grounding` and `generation_instructions` JOIN `spec_hash`, appended only
      when set, so two items that will be prompted differently cannot share a
      cache entry AND the three shipped blueprints keep every hash they had;
    - `group_id` LEAVES the prompt. It is excluded from `spec_hash`, so leaving
      it in let the prompt vary while the cache key did not.

`generation_instructions` goes in the USER message and never the system prompt:
L10 requires SYSTEM_PROMPT stay byte-identical across calls so provider-side
prompt caching applies.

Zero LLM calls — the mocked client only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.contracts.item import ItemSpec
from coursegen.exam.allocate import _spec_hash, solve
from coursegen.exam.generate import generate_exam
from coursegen.exam.validate import new_gate_report, validate_generated_items
from coursegen.llm.prompts import SYSTEM_PROMPT, build_generation_messages

FIXTURES = Path(__file__).parent / "fixtures"
BLUEPRINT_DIR = Path(__file__).parent.parent / "coursegen" / "exam" / "blueprints"
SHIPPED = ["quiz_default", "midterm_default", "final_default"]

INSTRUCTIONS = (
    "Generate a novel adversarial game tree with terminal leaf values; require "
    "the student to show pruned branches."
)


@pytest.fixture
def course_map() -> list[CourseMapNode]:
    raw = json.loads((FIXTURES / "course_map_sample.json").read_text(encoding="utf-8"))
    return [CourseMapNode.model_validate(n) for n in raw]


def _load_shipped(name: str) -> Blueprint:
    return Blueprint.model_validate(
        json.loads((BLUEPRINT_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


def _roomy_course_map(spans_per_node: int = 10) -> list[CourseMapNode]:
    """Every node holds more spans than apportionment can ask for, so nothing
    falls through and no slot goes unfilled."""
    return [
        CourseMapNode(
            node_id=f"roomy_{i:02d}",
            path=["Ch 1", f"1.{i}"],
            source_file="deck.pdf",
            page_span=(i + 1, i + 2),
            token_count=100 + i,
            chunk_ids=[f"roomy_{i:02d}_c{j}" for j in range(spans_per_node)],
            key_terms=[],
            flags=NodeFlags(),
            instructional_mass=0.2,
        )
        for i in range(5)
    ]


def _section(**overrides: object) -> SectionSpec:
    defaults: dict[str, object] = {
        "section_id": "A",
        "title": "Authored Section",
        "item_type": "short",
        "count": 4,
        "marks_each": 5,
        "bloom": ["apply"],
    }
    defaults.update(overrides)
    return SectionSpec(**defaults)          # type: ignore[arg-type]


def _blueprint(*sections: SectionSpec) -> Blueprint:
    return Blueprint(
        blueprint_id="authored",
        title="Authored",
        total_marks=sum(s.count * s.marks_each for s in sections),
        duration_minutes=60,
        sections=list(sections),
    )


def _spec(
    slot_id: str = "A-01",
    node_id: str = "n1",
    span_id: str = "s1",
    **overrides: object,
) -> ItemSpec:
    defaults: dict[str, object] = {
        "slot_id": slot_id,
        "item_type": "short",
        "marks": 5,
        "bloom": "apply",
        "node_id": node_id,
        "span_ids": [span_id],
        "eligibility": [],
        "spec_hash": f"hash-{slot_id}",
    }
    defaults.update(overrides)
    return ItemSpec(**defaults)             # type: ignore[arg-type]


def _node(node_id: str, chunk_id: str) -> CourseMapNode:
    return CourseMapNode(
        node_id=node_id,
        path=["Module", node_id],
        source_file="lecture.pdf",
        page_span=(1, 2),
        token_count=100,
        chunk_ids=[chunk_id],
        key_terms=["concept"],
        flags=NodeFlags(),
        instructional_mass=0.1,
    )


def _raw_item(slot_id: str, answer: str = "Grounded answer") -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "stem": f"Explain {slot_id}.",
        "options": None,
        "correct_option": None,
        "model_answer": answer,
        "explanation": "Because the source span states it.",
        "source_ref": {"file": "lecture.pdf", "pages": [1]},
    }


class FakeLLMClient:
    """Mocked client — zero network calls in the default run."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any] | None]] = []
        self.budget = type("Budget", (), {"calls_used": 0, "tokens_used": 0})()

    def call(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((messages, response_schema))
        self.budget.calls_used += 1
        self.budget.tokens_used += sum(len(m["content"]) for m in messages) // 4
        if not self._batches:
            raise AssertionError("FakeLLMClient called more times than expected")
        return {"items": self._batches.pop(0)}


# ---------------------------------------------------------------------------
# Gate 1 — the three shipped blueprints are unchanged
#
# The overriding regression guard. `_pre_stage_3_spec_hash` is the stage-2
# encoding transcribed by hand: calling the live `_spec_hash` would prove
# nothing, since that is the function under suspicion.
# ---------------------------------------------------------------------------

def _pre_stage_3_spec_hash(
    node_id: str,
    span_ids: list[str],
    item_type: str,
    bloom: str,
    marks: int,
    options_count: int | None,
    format_requirement: str | None = None,
) -> str:
    """The exact encoding that shipped before `grounding` existed."""
    parts = [
        node_id,
        ",".join(span_ids),
        item_type,
        bloom,
        str(marks),
        "" if options_count is None else str(options_count),
    ]
    if format_requirement is not None:
        parts.append(format_requirement)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class TestShippedBlueprintsUnchanged:
    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_no_shipped_section_authors_a_stage_3_field(
        self, blueprint_name: str
    ) -> None:
        """Absence is the regression guard, so absence itself is asserted."""
        for section in _load_shipped(blueprint_name).sections:
            assert section.grounding == "span"
            assert section.generation_instructions is None

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_spec_hash_matches_the_pre_stage_3_encoding(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """grounding joins the hash — but ONLY when it is not the default.

        Encoding "span" the way options_count encodes None would append a
        trailing separator to every hash in the product and silently invalidate
        the on-disk generation cache for all three shipped papers. That is the
        trap `format_requirement` already avoided in stage 2.
        """
        blueprint = _load_shipped(blueprint_name)
        by_section = {s.section_id: s for s in blueprint.sections}
        items, _ = solve(course_map, blueprint)
        assert items, "fixture must actually produce items for this to prove anything"

        for item in items:
            section = by_section[item.slot_id.split("-")[0]]
            assert item.spec_hash == _pre_stage_3_spec_hash(
                item.node_id,
                item.span_ids,
                item.item_type,
                item.bloom,
                item.marks,
                section.options_count,
                section.format_requirement,
            )

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_every_shipped_item_is_span_grounded(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        items, _ = solve(course_map, _load_shipped(blueprint_name))
        assert all(i.grounding == "span" for i in items)
        assert all(i.generation_instructions is None for i in items)


# ---------------------------------------------------------------------------
# Gate 2 — the grounding contract
# ---------------------------------------------------------------------------

class TestGroundingContract:
    def test_default_is_span_on_both_contracts(self) -> None:
        """The default is current behaviour, and an ItemSpec built without the
        field cannot accidentally become ungrounded."""
        assert _section().grounding == "span"
        assert _spec().grounding == "span"

    @pytest.mark.parametrize("value", ["span", "synthesis"])
    def test_both_values_are_accepted_and_carried_to_every_item(
        self, value: str
    ) -> None:
        items, _ = solve(
            _roomy_course_map(), _blueprint(_section(grounding=value))
        )
        assert len(items) == 4
        assert all(i.grounding == value for i in items)

    def test_an_unknown_grounding_mode_raises(self) -> None:
        """A third mode needs code in validate.py, not a data change — so an
        unrecognised value must not parse into a paper that looks honoured."""
        with pytest.raises(ValidationError):
            _section(grounding="invented")
        with pytest.raises(ValidationError):
            _spec(grounding="ungrounded")

    def test_grounding_joins_spec_hash_only_when_not_the_default(self) -> None:
        base = ("n01", ["c1"], "short", "apply", 5, None)
        assert _spec_hash(*base) == _spec_hash(*base, None, "span")
        assert _spec_hash(*base) != _spec_hash(*base, None, "synthesis")

    def test_two_sections_differing_only_in_grounding_do_not_share_a_cache_entry(
        self,
    ) -> None:
        """`grounding` changes the prompt more fundamentally than anything else
        in the key — whether the item is drawn from the span at all."""
        span_items, _ = solve(_roomy_course_map(), _blueprint(_section()))
        synth_items, _ = solve(
            _roomy_course_map(), _blueprint(_section(grounding="synthesis"))
        )

        # Same nodes, same spans, same bloom, same marks — only grounding moved.
        assert [i.node_id for i in span_items] == [i.node_id for i in synth_items]
        assert [i.span_ids for i in span_items] == [i.span_ids for i in synth_items]
        assert [i.bloom for i in span_items] == [i.bloom for i in synth_items]
        assert all(
            a.spec_hash != b.spec_hash for a, b in zip(span_items, synth_items)
        )


# ---------------------------------------------------------------------------
# Gate 3 — generation_instructions: user message only, and in the cache key
# ---------------------------------------------------------------------------

class TestGenerationInstructions:
    def test_carried_from_section_to_every_item(self) -> None:
        items, _ = solve(
            _roomy_course_map(),
            _blueprint(_section(generation_instructions=INSTRUCTIONS)),
        )
        assert len(items) == 4
        assert all(i.generation_instructions == INSTRUCTIONS for i in items)

    def test_reaches_the_user_message(self) -> None:
        messages = build_generation_messages(
            [_spec(generation_instructions=INSTRUCTIONS)], {"s1": "Source text."}
        )
        user = [m for m in messages if m["role"] == "user"]
        assert len(user) == 1
        assert "novel adversarial game tree" in user[0]["content"]

    def test_never_reaches_the_system_prompt(self) -> None:
        """L10 — SYSTEM_PROMPT must stay byte-identical across calls or
        provider-side prompt caching stops applying on every single call."""
        plain = build_generation_messages([_spec()], {"s1": "Source text."})
        instructed = build_generation_messages(
            [_spec(generation_instructions=INSTRUCTIONS)], {"s1": "Source text."}
        )
        systems = [
            m["content"] for m in plain + instructed if m["role"] == "system"
        ]
        assert len(systems) == 2
        assert systems[0] == systems[1] == SYSTEM_PROMPT
        assert INSTRUCTIONS not in systems[0]

    def test_system_prompt_is_byte_identical_across_every_grounding_mode(
        self,
    ) -> None:
        variants = [
            _spec(),
            _spec(grounding="synthesis"),
            _spec(generation_instructions=INSTRUCTIONS),
            _spec(grounding="synthesis", generation_instructions=INSTRUCTIONS),
        ]
        systems = {
            build_generation_messages([v], {"s1": "Source text."})[0]["content"]
            for v in variants
        }
        assert systems == {SYSTEM_PROMPT}

    def test_joins_spec_hash_only_when_set(self) -> None:
        base = ("n01", ["c1"], "short", "apply", 5, None)
        assert _spec_hash(*base) == _spec_hash(*base, None, "span", None)
        assert _spec_hash(*base) != _spec_hash(*base, None, "span", INSTRUCTIONS)

    def test_different_instructions_do_not_collide(self) -> None:
        base = ("n01", ["c1"], "short", "apply", 5, None)
        hashes = {
            _spec_hash(*base, None, "span", text)
            for text in ("trace alpha-beta", "trace minimax", "trace A*")
        }
        assert len(hashes) == 3
        assert _spec_hash(*base) not in hashes


# ---------------------------------------------------------------------------
# Gate 4 — group_id leaves the prompt
#
# It is deliberately excluded from `spec_hash`, so while it stayed in the prompt
# the prompt text could vary while the cache key did not — and a cached
# generation would be reused across a prompt difference.
# ---------------------------------------------------------------------------

class TestGroupIdExcludedFromPrompt:
    def test_group_id_is_absent_from_the_prompt(self) -> None:
        messages = build_generation_messages(
            [_spec(group_id="Q3")], {"s1": "Source text."}
        )
        joined = "\n".join(m["content"] for m in messages)
        assert "group_id" not in joined
        assert "Q3" not in joined

    def test_two_specs_differing_only_in_group_id_produce_identical_prompts(
        self,
    ) -> None:
        """The actual defect: group_id is excluded from spec_hash, so a prompt
        that varies with it means one cache entry serving two prompts."""
        a = build_generation_messages([_spec(group_id="Q1")], {"s1": "Source."})
        b = build_generation_messages([_spec(group_id="Q2")], {"s1": "Source."})
        none = build_generation_messages([_spec()], {"s1": "Source."})
        assert a == b == none

        # ... and the specs really did differ, so the equality above means
        # exclusion rather than two identical inputs.
        assert _spec(group_id="Q1").group_id != _spec(group_id="Q2").group_id

    def test_the_fields_the_writer_needs_are_still_there(self) -> None:
        """Excluding one field must not have emptied the payload."""
        messages = build_generation_messages(
            [
                _spec(
                    group_id="Q3",
                    format_requirement="ALGORITHMIC_TRACE_PROBLEM",
                    grounding="synthesis",
                    generation_instructions=INSTRUCTIONS,
                )
            ],
            {"s1": "Source text."},
        )
        joined = "\n".join(m["content"] for m in messages)
        for needed in (
            "slot_id", "A-01", "item_type", "short", "bloom", "apply",
            "marks", "ALGORITHMIC_TRACE_PROBLEM", "synthesis",
            "novel adversarial game tree",
        ):
            assert needed in joined, f"{needed!r} missing from the prompt"


# ---------------------------------------------------------------------------
# Gate 5 — gate 2 records synthesis items as NOT APPLICABLE
#
# "We could not check" and "there is nothing to check against" are not the same
# thing, and collapsing them into one `skipped` flag hides the more important one.
# ---------------------------------------------------------------------------

class TestGateTwoNotApplicable:
    def test_every_gate_starts_with_a_not_applicable_counter(self) -> None:
        report = new_gate_report()
        assert set(report) == {
            "schema", "groundedness", "duplication", "mcq_hygiene"
        }
        for gate, record in report.items():
            assert record["not_applicable"] == 0, gate
            # `skipped` stays a BOOL about the gate; `not_applicable` is an INT
            # about items. They answer different questions.
            assert isinstance(record["skipped"], bool)
            assert isinstance(record["not_applicable"], int)

    def test_a_synthesis_item_is_not_applicable_rather_than_evaluated(
        self,
    ) -> None:
        spec = _spec(grounding="synthesis")
        valid, issues, gates = validate_generated_items(
            specs=[spec],
            raw_items=[_raw_item("A-01")],
            span_text_by_id={"s1": "Source content."},
            groundedness_scorer=lambda answer, source: 10.0,
            embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        )
        assert [i.slot_id for i in valid] == ["A-01"]
        assert not [i for i in issues if i.gate == "groundedness"]

        g = gates["groundedness"]
        assert g["not_applicable"] == 1
        # Not evaluated, and above all NOT passed: nothing was checked.
        assert g["evaluated"] == 0
        assert g["passed"] == 0
        assert g["failed"] == 0

    def test_a_synthesis_item_is_never_failed_by_gate_two(self) -> None:
        """The blueprint asked for an invented artifact. Scoring it against a
        span it was never meant to reproduce would fail every such item."""
        valid, issues, gates = validate_generated_items(
            specs=[_spec(grounding="synthesis")],
            raw_items=[_raw_item("A-01", answer="A wholly novel game tree.")],
            span_text_by_id={"s1": "Unrelated source content."},
            groundedness_scorer=lambda answer, source: -999.0,
            embedding_fn=None,
        )
        assert len(valid) == 1
        assert issues == []
        assert gates["groundedness"]["failed"] == 0
        assert gates["groundedness"]["not_applicable"] == 1

    def test_a_span_item_is_still_evaluated_normally(self) -> None:
        _, issues, gates = validate_generated_items(
            specs=[_spec()],
            raw_items=[_raw_item("A-01")],
            span_text_by_id={"s1": "Source content."},
            groundedness_scorer=lambda answer, source: -999.0,
            embedding_fn=None,
        )
        assert [i.gate for i in issues] == ["groundedness"]
        assert gates["groundedness"]["evaluated"] == 1
        assert gates["groundedness"]["failed"] == 1
        assert gates["groundedness"]["not_applicable"] == 0

    def test_evaluated_plus_not_applicable_accounts_for_every_item(self) -> None:
        """The invariant the counter exists for: for a gate that ran,
        evaluated + not_applicable == the items that reached it."""
        specs = [
            _spec("A-01", "n1", "s1"),
            _spec("A-02", "n2", "s2", grounding="synthesis"),
            _spec("A-03", "n3", "s3", grounding="synthesis"),
            _spec("A-04", "n4", "s4"),
        ]
        _, _, gates = validate_generated_items(
            specs=specs,
            raw_items=[_raw_item(s.slot_id) for s in specs],
            span_text_by_id={f"s{i}": f"Source {i}." for i in range(1, 5)},
            groundedness_scorer=lambda answer, source: 10.0,
            embedding_fn=None,
        )
        g = gates["groundedness"]
        assert gates["schema"]["evaluated"] == 4, "all four reached gate 2"
        assert g["evaluated"] == 2
        assert g["not_applicable"] == 2
        assert g["evaluated"] + g["not_applicable"] == 4

    def test_not_applicable_is_recorded_even_with_no_scorer_injected(self) -> None:
        """`skipped` and `not_applicable` are independent facts. A run with no
        scorer AND synthesis items must report both, not let the louder one
        swallow the quieter."""
        _, _, gates = validate_generated_items(
            specs=[_spec(grounding="synthesis")],
            raw_items=[_raw_item("A-01")],
            span_text_by_id={"s1": "Source content."},
            groundedness_scorer=None,
            embedding_fn=None,
        )
        g = gates["groundedness"]
        assert g["skipped"] is True
        assert g["not_applicable"] == 1
        assert g["evaluated"] == 0


# ---------------------------------------------------------------------------
# Gate 6 — the paper says how many of its questions are ungrounded
#
# The point of the stage. A synthesis item is a question NOT backed by the
# student's own material, and that has to be as visible as fill_ratio and
# allocation_fidelity made earlier degradations visible.
# ---------------------------------------------------------------------------

class TestManifestReportsSynthesisCount:
    def _run(
        self, tmp_path: Path, specs: list[ItemSpec], **kwargs: Any
    ) -> dict[str, Any]:
        return generate_exam(
            specs=specs,
            course_map=[_node(s.node_id, s.span_ids[0]) for s in specs],
            span_text_by_id={s.span_ids[0]: "Source content." for s in specs},
            llm_client=FakeLLMClient([[_raw_item(s.slot_id) for s in specs]]),
            output_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            blueprint_id="authored",
            **kwargs,
        ).manifest

    def test_an_all_span_paper_reports_zero_rather_than_nothing(
        self, tmp_path: Path
    ) -> None:
        """Reported at every value. A missing field would be indistinguishable
        from an old manifest, and silence is what this stage exists to end."""
        manifest = self._run(
            tmp_path, [_spec(f"A-{i:02d}", f"n{i}", f"s{i}") for i in range(4)]
        )
        assert manifest["grounding"]["synthesis_items"] == 0
        assert manifest["grounding"]["items_total"] == 4
        assert manifest["grounding"]["synthesis_ratio"] == 0.0
        assert manifest["warnings"] == []

    def test_the_count_and_the_total_are_both_on_disk(
        self, tmp_path: Path
    ) -> None:
        specs = [
            _spec("A-01", "n1", "s1", grounding="synthesis"),
            _spec("A-02", "n2", "s2"),
            _spec("A-03", "n3", "s3"),
            _spec("A-04", "n4", "s4"),
        ]
        manifest = self._run(tmp_path, specs)
        on_disk = json.loads(
            (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert on_disk == manifest
        assert on_disk["grounding"]["synthesis_items"] == 1
        assert on_disk["grounding"]["items_total"] == 4
        assert on_disk["grounding"]["synthesis_ratio"] == 0.25

    def test_a_paper_over_the_ratio_warns(self, tmp_path: Path) -> None:
        specs = [
            _spec("A-01", "n1", "s1", grounding="synthesis"),
            _spec("A-02", "n2", "s2", grounding="synthesis"),
            _spec("A-03", "n3", "s3"),
            _spec("A-04", "n4", "s4"),
        ]
        manifest = self._run(tmp_path, specs)
        assert manifest["grounding"]["synthesis_ratio"] == 0.5
        assert len(manifest["warnings"]) == 1
        warning = manifest["warnings"][0]
        # The warning must SAY what is wrong, not just that something is.
        assert "2 of 4" in warning
        assert "synthesis" in warning
        assert "SYNTHESIS_ITEM_WARN_RATIO" in warning
        assert "revise" in warning, "the student-facing consequence must be named"

    def test_exactly_at_the_ratio_does_not_warn(self, tmp_path: Path) -> None:
        """The threshold is a boundary, and which side it falls on is a decision
        rather than an accident: at the ratio is within budget."""
        specs = [
            _spec("A-01", "n1", "s1", grounding="synthesis"),
            _spec("A-02", "n2", "s2"),
            _spec("A-03", "n3", "s3"),
            _spec("A-04", "n4", "s4"),
        ]
        manifest = self._run(tmp_path, specs)
        assert manifest["grounding"]["synthesis_ratio"] == pytest.approx(
            config.SYNTHESIS_ITEM_WARN_RATIO
        )
        assert manifest["warnings"] == []

    def test_the_count_is_over_the_specs_not_the_surviving_items(
        self, tmp_path: Path
    ) -> None:
        """"How much of this paper was invented" is a property of what the
        blueprint asked for. Counting only items that survived validation would
        move the number for reasons unrelated to grounding."""
        specs = [
            _spec("A-01", "n1", "s1", grounding="synthesis"),
            _spec("A-02", "n2", "s2", grounding="synthesis"),
            _spec("A-03", "n3", "s3"),
            _spec("A-04", "n4", "s4"),
        ]
        result = generate_exam(
            specs=specs,
            course_map=[_node(s.node_id, s.span_ids[0]) for s in specs],
            span_text_by_id={s.span_ids[0]: "Source content." for s in specs},
            # Only two items ever come back; the other two never parse.
            llm_client=FakeLLMClient([
                [_raw_item("A-01"), _raw_item("A-03")],
                [_raw_item("A-02"), _raw_item("A-04")],
            ]),
            output_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            blueprint_id="authored",
            groundedness_scorer=lambda answer, source: -999.0,
        )
        # A-03 failed gate 2 and A-01 was not applicable to it.
        assert result.manifest["grounding"]["synthesis_items"] == 2
        assert result.manifest["grounding"]["items_total"] == 4

    def test_the_gate_report_reaches_the_manifest_with_not_applicable(
        self, tmp_path: Path
    ) -> None:
        specs = [
            _spec("A-01", "n1", "s1", grounding="synthesis"),
            _spec("A-02", "n2", "s2"),
        ]
        manifest = self._run(
            tmp_path, specs, groundedness_scorer=lambda answer, source: 10.0
        )
        g = manifest["validation"]["groundedness"]
        assert g["not_applicable"] == 1
        assert g["evaluated"] == 1
        assert g["passed"] == 1
        assert g["skipped"] is False
