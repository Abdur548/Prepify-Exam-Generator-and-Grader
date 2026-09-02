"""
Spec Amendment 01, stage 2 — the remaining structural vocabulary of an authored
blueprint: `format_requirement`, `group_id`, `bloom_mix` and the REPORTED
`cognitive_balance`. Zero LLM calls — contracts and allocation only.

Every field added here is OPTIONAL, and its absence is exactly current behaviour.
The three shipped blueprints (`quiz_default`, `midterm_default`, `final_default`)
author none of them, so they must keep producing byte-identical `ItemSpec[]` —
`TestShippedBlueprintsUnchanged` is that guard, and it re-derives the
PRE-AMENDMENT `spec_hash` encoding by hand rather than trusting the live one.

The three properties these gates exist to protect:

    - a typo'd `format_requirement` RAISES, instead of quietly generating a
      generic question under a blueprint that looks honoured;
    - `format_requirement` changes the cache key and `group_id` does NOT, because
      one changes what is asked and the other only how it is displayed;
    - a paper that drifts away from its declared cognitive balance SAYS SO —
      coverage_ratio, fill_ratio and allocation_fidelity would all call it fine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from coursegen import config
from coursegen.contracts.blueprint import Blueprint, SectionSpec
from coursegen.contracts.course_map import CourseMapNode, NodeFlags
from coursegen.exam.allocate import (
    _bloom_ladder,
    _hare_apportionment,
    _spec_hash,
    solve,
)

FIXTURES = Path(__file__).parent / "fixtures"
BLUEPRINT_DIR = Path(__file__).parent.parent / "coursegen" / "exam" / "blueprints"
SHIPPED = ["quiz_default", "midterm_default", "final_default"]


@pytest.fixture
def course_map() -> list[CourseMapNode]:
    raw = json.loads((FIXTURES / "course_map_sample.json").read_text(encoding="utf-8"))
    return [CourseMapNode.model_validate(n) for n in raw]


def _load_shipped(name: str) -> Blueprint:
    return Blueprint.model_validate(
        json.loads((BLUEPRINT_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


def _roomy_course_map(spans_per_node: int = 10) -> list[CourseMapNode]:
    """Every node holds far more spans than apportionment can ask for, so nothing
    falls through and no slot goes unfilled. Equal mass keeps the split exact."""
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
        "item_type": "mcq",
        "count": 10,
        "marks_each": 2,
        "bloom": ["remember", "understand"],
        "options_count": 4,
    }
    defaults.update(overrides)
    return SectionSpec(**defaults)          # type: ignore[arg-type]


def _blueprint(section: SectionSpec, **overrides: object) -> Blueprint:
    return Blueprint(
        blueprint_id="authored",
        title="Authored",
        total_marks=section.count * section.marks_each,
        duration_minutes=60,
        sections=[section],
        **overrides,                        # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Gate 1 — the three shipped blueprints are byte-identical
#
# The overriding regression guard of the whole stage. `_pre_amendment_spec_hash`
# is the HEAD-53b9392 encoding transcribed by hand: a golden file would only
# prove the file did not change, and calling the live `_spec_hash` would prove
# nothing at all, since that is the function under suspicion.
# ---------------------------------------------------------------------------

def _pre_amendment_spec_hash(
    node_id: str,
    span_ids: list[str],
    item_type: str,
    bloom: str,
    marks: int,
    options_count: int | None,
) -> str:
    """The exact six-field encoding that shipped before `format_requirement`."""
    raw = "|".join([
        node_id,
        ",".join(span_ids),
        item_type,
        bloom,
        str(marks),
        "" if options_count is None else str(options_count),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


class TestShippedBlueprintsUnchanged:
    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_no_shipped_section_authors_any_stage_2_field(
        self, blueprint_name: str
    ) -> None:
        """Absence is the regression guard, so absence itself is asserted."""
        blueprint = _load_shipped(blueprint_name)
        assert blueprint.cognitive_balance is None
        for section in blueprint.sections:
            assert section.format_requirement is None
            assert section.group_id is None
            assert section.bloom_mix is None

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_spec_hash_matches_the_pre_amendment_encoding(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """format_requirement joins the hash — but only when it is SET.

        Encoding None as "" the way options_count is would have appended a
        trailing separator and changed every hash in the product, silently
        invalidating the on-disk generation cache for all three shipped papers.
        """
        blueprint = _load_shipped(blueprint_name)
        by_section = {s.section_id: s for s in blueprint.sections}
        items, _ = solve(course_map, blueprint)
        assert items, "fixture must actually produce items for this to prove anything"

        for item in items:
            section = by_section[item.slot_id.split("-")[0]]
            assert item.spec_hash == _pre_amendment_spec_hash(
                item.node_id,
                item.span_ids,
                item.item_type,
                item.bloom,
                item.marks,
                section.options_count,
            )

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_new_item_fields_are_absent_on_every_item(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        items, _ = solve(course_map, _load_shipped(blueprint_name))
        assert all(i.format_requirement is None for i in items)
        assert all(i.group_id is None for i in items)

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_bloom_is_still_round_robin(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """No bloom_mix means `bloom[idx % len(bloom)]`, exactly as before."""
        blueprint = _load_shipped(blueprint_name)
        items, _ = solve(course_map, blueprint)
        for section in blueprint.sections:
            emitted = [
                i.bloom for i in items
                if i.slot_id.startswith(f"{section.section_id}-")
            ]
            assert emitted == [
                section.bloom[idx % len(section.bloom)]
                for idx in range(len(emitted))
            ]

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_no_cognitive_balance_means_no_target_and_no_warning(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        _, report = solve(course_map, _load_shipped(blueprint_name))
        assert report.cognitive_balance_declared is None
        assert not [w for w in report.warnings if "Cognitive balance" in w]

    @pytest.mark.parametrize("blueprint_name", SHIPPED)
    def test_realised_bloom_is_reported_anyway(
        self, course_map: list[CourseMapNode], blueprint_name: str
    ) -> None:
        """Measured with or without a declared target — it is a fact about the
        paper, not a judgement of it."""
        items, report = solve(course_map, _load_shipped(blueprint_name))
        assert report.bloom_realised
        assert sum(report.bloom_realised.values()) == pytest.approx(1.0)
        assert set(report.bloom_realised) == {i.bloom for i in items}


# ---------------------------------------------------------------------------
# Gate 2 — format_requirement: validated, carried, and part of the cache key
# ---------------------------------------------------------------------------

class TestFormatRequirement:
    @pytest.mark.parametrize("value", sorted(config.KNOWN_FORMAT_REQUIREMENTS))
    def test_every_known_value_is_accepted_and_carried(
        self, value: str
    ) -> None:
        items, _ = solve(
            _roomy_course_map(),
            _blueprint(_section(count=4, format_requirement=value)),
        )
        assert len(items) == 4
        assert all(i.format_requirement == value for i in items)

    def test_unknown_value_raises(self) -> None:
        """A typo'd format must not fall through to a generic question.

        The blueprint would look honoured and quietly not be — exactly the
        plausible-but-wrong artifact this check exists to prevent.
        """
        with pytest.raises(ValidationError) as excinfo:
            _section(format_requirement="TRUE_FALSE_SERIE")     # typo
        message = str(excinfo.value)
        assert "TRUE_FALSE_SERIE" in message          # names the offending value
        assert "TRUE_FALSE_SERIES" in message         # names what was meant
        assert "unknown format_requirement" in message

    def test_item_type_is_not_widened(self) -> None:
        """§8.1 — the new formats do NOT become item types.

        item_type drives renderer layout and gate selection: gate 4 (MCQ hygiene)
        keys off item_type == "mcq". A true/false series is an mcq with two
        options that carries the finer instruction alongside.
        """
        with pytest.raises(ValidationError):
            _section(item_type="TRUE_FALSE_SERIES")

        section = _section(
            item_type="mcq", options_count=2,
            format_requirement="TRUE_FALSE_SERIES",
        )
        assert section.item_type == "mcq"
        assert section.options_count == 2

    def test_format_requirement_joins_spec_hash(self) -> None:
        """Two items differing only in format must not share a cached generation."""
        plain, _ = solve(_roomy_course_map(), _blueprint(_section(count=4)))
        formatted, _ = solve(
            _roomy_course_map(),
            _blueprint(
                _section(count=4, format_requirement="ANALYTICAL_SHORT_ANSWER")
            ),
        )

        # Same nodes, same spans, same bloom, same marks — only the format moved.
        assert [i.node_id for i in plain] == [i.node_id for i in formatted]
        assert [i.span_ids for i in plain] == [i.span_ids for i in formatted]
        assert [i.bloom for i in plain] == [i.bloom for i in formatted]
        assert all(
            p.spec_hash != f.spec_hash for p, f in zip(plain, formatted)
        )

    def test_two_different_formats_do_not_collide(self) -> None:
        base = ("n01", ["c1"], "mcq", "remember", 2, 4)
        hashes = {
            _spec_hash(*base, fmt)
            for fmt in sorted(config.KNOWN_FORMAT_REQUIREMENTS)
        }
        assert len(hashes) == len(config.KNOWN_FORMAT_REQUIREMENTS)
        # ... and none of them is the no-format hash.
        assert _spec_hash(*base) not in hashes

    def test_known_values_are_data_not_a_literal(self) -> None:
        """Adding a format must stay a config change, not a contract change."""
        assert isinstance(config.KNOWN_FORMAT_REQUIREMENTS, frozenset)
        assert config.KNOWN_FORMAT_REQUIREMENTS == {
            "TRUE_FALSE_SERIES",
            "ALGORITHMIC_TRACE_PROBLEM",
            "SCENARIO_MODELING_AND_SOLVING",
            "MATHEMATICAL_MODELING",
            "ANALYTICAL_SHORT_ANSWER",
        }


# ---------------------------------------------------------------------------
# Gate 3 — group_id: sub-questions without breaking one-ItemSpec-per-item
# ---------------------------------------------------------------------------

class TestGroupId:
    def test_a_four_part_task_is_four_item_specs_sharing_one_group(self) -> None:
        """§8.2 — expansion, not nesting.

        Everything downstream assumes one ItemSpec = one item: span uniqueness,
        the spec_hash cache, per-item validation, all four gates, slots_filled,
        allocation_fidelity. Nesting would make the gates operate on composites.
        """
        items, report = solve(
            _roomy_course_map(),
            _blueprint(
                _section(
                    count=4, item_type="short", options_count=None,
                    group_id="Q3", marks_each=5,
                )
            ),
        )

        assert len(items) == 4
        assert [i.slot_id for i in items] == ["A-01", "A-02", "A-03", "A-04"]
        assert all(i.group_id == "Q3" for i in items)

        # The invariants that expansion had to preserve, asserted rather than
        # assumed: one span each, no span reused, every slot filled.
        assert all(len(i.span_ids) == 1 for i in items)
        spans = [s for i in items for s in i.span_ids]
        assert len(spans) == len(set(spans))
        assert report.slots_filled == 4
        assert report.unfilled_slots == []

    def test_group_id_does_not_join_spec_hash(self) -> None:
        """It is PRESENTATIONAL — it changes how items are displayed, not what is
        asked — so regrouping must not invalidate cached generations."""
        ungrouped, _ = solve(_roomy_course_map(), _blueprint(_section(count=4)))
        grouped, _ = solve(
            _roomy_course_map(), _blueprint(_section(count=4, group_id="Q3"))
        )

        assert [i.group_id for i in grouped] == ["Q3"] * 4
        assert [i.spec_hash for i in ungrouped] == [i.spec_hash for i in grouped]

    def test_regrouping_does_not_invalidate_the_cache(self) -> None:
        """The same items moved from one group to another keep their keys."""
        first, _ = solve(
            _roomy_course_map(), _blueprint(_section(count=4, group_id="Q3"))
        )
        second, _ = solve(
            _roomy_course_map(), _blueprint(_section(count=4, group_id="Q7"))
        )
        assert [i.spec_hash for i in first] == [i.spec_hash for i in second]

    def test_absent_group_id_is_none_everywhere(self) -> None:
        items, _ = solve(_roomy_course_map(), _blueprint(_section(count=4)))
        assert all(i.group_id is None for i in items)


# ---------------------------------------------------------------------------
# Gate 4 — bloom_mix: a proportional mix, apportioned by the SAME Hare function
# ---------------------------------------------------------------------------

class TestBloomMix:
    def test_absent_mix_is_todays_round_robin(self) -> None:
        """The regression guard, stated as an explicit expected sequence."""
        items, _ = solve(_roomy_course_map(), _blueprint(_section(count=5)))
        assert [i.bloom for i in items] == [
            "remember", "understand", "remember", "understand", "remember",
        ]

    def test_sixty_forty_is_now_expressible(self) -> None:
        """`["remember", "understand"]` alone can only ever mean 50/50."""
        items, _ = solve(
            _roomy_course_map(),
            _blueprint(
                _section(
                    count=10,
                    bloom_mix={"remember": 0.6, "understand": 0.4},
                )
            ),
        )
        emitted = [i.bloom for i in items]
        assert emitted.count("remember") == 6
        assert emitted.count("understand") == 4

    def test_levels_are_grouped_in_section_bloom_order(self) -> None:
        """The documented deterministic order: every slot of the first-listed
        level, then the second, and so on."""
        items, _ = solve(
            _roomy_course_map(),
            _blueprint(
                _section(
                    count=10,
                    bloom_mix={"remember": 0.6, "understand": 0.4},
                )
            ),
        )
        assert [i.bloom for i in items] == ["remember"] * 6 + ["understand"] * 4

    def test_order_does_not_depend_on_mix_key_order(self) -> None:
        """`section.bloom` is the authored order, not the JSON key order."""
        forwards = _bloom_ladder(
            _section(count=10, bloom_mix={"remember": 0.6, "understand": 0.4}), 10
        )
        backwards = _bloom_ladder(
            _section(count=10, bloom_mix={"understand": 0.4, "remember": 0.6}), 10
        )
        assert forwards == backwards
        assert forwards == ["remember"] * 6 + ["understand"] * 4

    def test_apportionment_is_the_existing_hare_function(self) -> None:
        """Not a second apportionment: same tie-breaking, same determinism.

        A 1/3 : 2/3 split over 10 slots is where a naive rounding and
        largest-remainder disagree, so it actually discriminates.
        """
        mix = {"remember": 1 / 3, "understand": 2 / 3}
        expected = _hare_apportionment(dict(mix), 10)
        ladder = _bloom_ladder(_section(count=10, bloom_mix=mix), 10)

        assert len(ladder) == 10
        for level, count in expected.items():
            assert ladder.count(level) == count

    def test_equal_proportions_tie_break_alphabetically(self) -> None:
        """An inherited consequence of reusing _hare_apportionment, pinned so it
        is a known property rather than a surprise: its tie-break is ascending
        KEY, so equal proportions are separated by level NAME, not by the order
        the section lists them in."""
        ladder = _bloom_ladder(
            _section(count=5, bloom_mix={"remember": 0.5, "understand": 0.5}), 5
        )
        assert ladder.count("remember") == 3     # "remember" < "understand"
        assert ladder.count("understand") == 2

    def test_a_level_the_section_does_not_list_raises(self) -> None:
        """A mix naming an unlisted level is a malformed blueprint, not something
        to silently ignore."""
        with pytest.raises(ValidationError) as excinfo:
            _section(
                bloom=["remember", "understand"],
                bloom_mix={"remember": 0.6, "evaluate": 0.4},
            )
        message = str(excinfo.value)
        assert "evaluate" in message              # names the stray level
        assert "bloom_mix" in message             # names the field it broke

    def test_a_subset_of_bloom_is_allowed(self) -> None:
        """Subset, not equality: a section may list a level and give it no share."""
        ladder = _bloom_ladder(
            _section(count=4, bloom_mix={"remember": 1.0}), 4
        )
        assert ladder == ["remember"] * 4

    def test_empty_mix_is_rejected(self) -> None:
        """A mix naming no levels apportions nothing and reaches
        _hare_apportionment as a ZeroDivisionError — the same reason `bloom`
        itself carries min_length=1."""
        with pytest.raises(ValidationError):
            _section(bloom_mix={})

    def test_a_negative_proportion_raises_the_postcondition(self) -> None:
        """Apportionment must place exactly `count` slots.

        A negative proportion sails through the keys-subset check and reaches
        _hare_apportionment, which hands back a negative count for that level; a
        shorter ladder would then give later slots the wrong level, and a longer
        one would drop levels the mix asked for — either way the paper would look
        like the blueprint was honoured. Structural, so it is checked where the
        apportionment happens rather than guessed at parse time.
        """
        section = _section(count=10, bloom_mix={"remember": -0.5, "understand": 1.5})
        with pytest.raises(ValueError) as excinfo:
            _bloom_ladder(section, 10)
        message = str(excinfo.value)
        assert "apportioned to 15 slots for a section of 10" in message
        assert "'A'" in message                    # names the offending section

    def test_an_all_zero_mix_splits_evenly(self) -> None:
        """RECORDED GAP, not an endorsement.

        Nothing in the amendment says whether an all-zero mix is malformed. It
        currently falls into _hare_apportionment's documented zero-mass branch
        and splits evenly — which is what the section would have got with no mix
        at all, so it fails quietly rather than loudly. This pins the ACTUAL
        behaviour so nobody assumes a guard covers it. Whether a mix must sum to
        1.0, or may be all zeros, is the author's call to make, not this test's.
        """
        ladder = _bloom_ladder(
            _section(count=10, bloom_mix={"remember": 0.0, "understand": 0.0}), 10
        )
        assert ladder == ["remember"] * 5 + ["understand"] * 5

    def test_short_fill_takes_the_head_of_the_ladder(self) -> None:
        """Apportionment is over `count`, per the amendment — not over the number
        of slots that filled. A section that fills short loses the TAIL."""
        two_spans = [
            CourseMapNode(
                node_id=f"tiny_{i}",
                path=["Ch 1", f"1.{i}"],
                source_file="deck.pdf",
                page_span=(i + 1, i + 2),
                token_count=100 + i,
                chunk_ids=[f"tiny_{i}_c0"],
                key_terms=[],
                flags=NodeFlags(),
                instructional_mass=0.5,
            )
            for i in range(2)
        ]
        items, report = solve(
            two_spans,
            _blueprint(
                _section(count=5, bloom_mix={"remember": 0.6, "understand": 0.4})
            ),
        )
        assert len(items) == 2
        assert report.unfilled_slots == ["A-03", "A-04", "A-05"]
        assert [i.bloom for i in items] == ["remember", "remember"]


# ---------------------------------------------------------------------------
# Gate 5 — cognitive_balance: a REPORTED aggregate, never a parse-time validator
#
# Easy to get backwards. Each task carries a SINGLE cognitive_level;
# cognitive_balance is the exam-level aggregate those tasks are meant to sum to.
# It is a check on the REALISED distribution, which is an allocation outcome —
# so it cannot be known when the Blueprint is parsed.
# ---------------------------------------------------------------------------

class TestCognitiveBalance:
    def test_a_wildly_wrong_target_still_parses(self) -> None:
        """The realised distribution is unknowable at parse time, so this is NOT
        a pydantic validator. A Blueprint declaring an unreachable target must
        construct without complaint — the report is where it is answered."""
        blueprint = _blueprint(
            _section(count=10, bloom=["remember"]),
            cognitive_balance={"apply": 1.0},
        )
        assert blueprint.cognitive_balance == {"apply": 1.0}

    def test_divergence_warns_and_does_not_raise(self) -> None:
        """An under-filled or skewed paper legitimately misses its target. That
        is information, not a crash."""
        items, report = solve(
            _roomy_course_map(),
            _blueprint(
                _section(count=10, bloom=["remember"]),
                cognitive_balance={"remember": 0.2, "apply": 0.7, "evaluate": 0.1},
            ),
        )

        assert len(items) == 10                    # nothing was aborted
        assert report.bloom_realised == {"remember": 1.0}
        assert report.cognitive_balance_declared == {
            "remember": 0.2, "apply": 0.7, "evaluate": 0.1
        }

        balance = [w for w in report.warnings if "Cognitive balance" in w]
        assert balance, f"no divergence warning. Got: {report.warnings}"

        # The warning must name BOTH numbers — "balance missed" alone tells the
        # author nothing about which way, or by how much.
        remember = [w for w in balance if "'remember'" in w]
        assert len(remember) == 1
        assert "0.20" in remember[0]               # declared
        assert "1.00" in remember[0]               # realised

    def test_a_target_that_is_met_is_silent(self) -> None:
        items, report = solve(
            _roomy_course_map(),
            _blueprint(
                _section(count=10, bloom=["remember", "understand"]),
                cognitive_balance={"remember": 0.5, "understand": 0.5},
            ),
        )
        assert len(items) == 10
        assert report.bloom_realised == {"remember": 0.5, "understand": 0.5}
        assert not [w for w in report.warnings if "Cognitive balance" in w]

    def test_tolerance_separates_a_wobble_from_a_drift(self) -> None:
        """A divergence inside COGNITIVE_BALANCE_TOLERANCE is silent; one outside
        it warns. Both sections realise 0.5 / 0.5.

        The EXACT boundary is deliberately not asserted, because it is not
        assertable: a difference of exactly 0.10 is not representable in binary
        floating point — `abs(0.5 - 0.4)` is 0.09999999999999998 — so no fixture
        can distinguish `>` from `>=` here, and a test claiming to would be
        passing for a reason other than the one it states.
        """
        assert config.COGNITIVE_BALANCE_TOLERANCE == 0.10

        def warnings_for(target: dict[str, float]) -> list[str]:
            _, report = solve(
                _roomy_course_map(),
                _blueprint(
                    _section(count=10, bloom=["remember", "understand"]),
                    cognitive_balance=target,
                ),
            )
            assert report.bloom_realised == {"remember": 0.5, "understand": 0.5}
            return [w for w in report.warnings if "Cognitive balance" in w]

        # 0.05 out on each level — inside tolerance.
        assert warnings_for({"remember": 0.45, "understand": 0.55}) == []
        # 0.30 out on each level — a real drift, and both levels are named.
        drift = warnings_for({"remember": 0.2, "understand": 0.8})
        assert len(drift) == 2
        assert any("'remember'" in w for w in drift)
        assert any("'understand'" in w for w in drift)

    def test_a_level_the_target_never_declared_still_counts(self) -> None:
        """The comparison runs over the UNION of declared and realised levels.

        This is the drift the metric exists to catch: a paper filling up with a
        level the blueprint never asked for. Iterating only the declared keys
        would look straight past it.
        """
        _, report = solve(
            _roomy_course_map(),
            _blueprint(
                _section(count=10, bloom=["remember", "understand"]),
                cognitive_balance={"remember": 1.0},
            ),
        )
        balance = [w for w in report.warnings if "Cognitive balance" in w]
        assert any("'understand'" in w for w in balance), (
            f"a level absent from the target went unreported. Got: {balance}"
        )

    def test_an_underfilled_paper_reports_rather_than_crashes(self) -> None:
        """§4's stated reason for warning instead of raising."""
        one_span = [
            CourseMapNode(
                node_id="solo",
                path=["Ch 1", "1.1"],
                source_file="deck.pdf",
                page_span=(1, 2),
                token_count=100,
                chunk_ids=["solo_c0"],
                key_terms=[],
                flags=NodeFlags(),
                instructional_mass=1.0,
            )
        ]
        items, report = solve(
            one_span,
            _blueprint(
                _section(count=10, bloom=["remember", "understand"]),
                cognitive_balance={"remember": 0.5, "understand": 0.5},
            ),
        )
        assert len(items) == 1
        assert report.fill_ratio == pytest.approx(0.1)
        # Realised over what was EMITTED, not over what was requested.
        assert report.bloom_realised == {"remember": 1.0}
        assert [w for w in report.warnings if "Cognitive balance" in w]

    def test_no_items_reports_an_empty_realised_mix(self) -> None:
        """No paper has no distribution. Inventing one — or dividing by zero —
        would both be worse than saying nothing."""
        _, report = solve(
            _roomy_course_map(),
            _blueprint(
                _section(count=4, requires_flags_any=["has_code"]),  # matches nothing
                cognitive_balance={"remember": 1.0},
            ),
        )
        assert report.slots_filled == 0
        assert report.bloom_realised == {}


# ---------------------------------------------------------------------------
# Gate 6 — the new fields compose, and determinism survives all of them
# ---------------------------------------------------------------------------

class TestEverythingComposes:
    def _authored(self) -> Blueprint:
        return Blueprint(
            blueprint_id="authored_full",
            title="Authored Full",
            total_marks=40,                       # 6*2 + 4*2 + 4*5
            duration_minutes=120,
            sections=[
                SectionSpec(
                    section_id="A",
                    title="True/False",
                    item_type="mcq",
                    count=6,
                    marks_each=2,
                    bloom=["remember", "understand"],
                    bloom_mix={"remember": 0.5, "understand": 0.5},
                    options_count=2,
                    format_requirement="TRUE_FALSE_SERIES",
                ),
                SectionSpec(
                    section_id="B",
                    title="Trace",
                    item_type="short",
                    count=4,
                    marks_each=2,
                    bloom=["apply"],
                    format_requirement="ALGORITHMIC_TRACE_PROBLEM",
                    group_id="Q2",
                ),
                SectionSpec(
                    section_id="C",
                    title="Modelling",
                    item_type="long",
                    count=4,
                    marks_each=5,
                    bloom=["analyse", "evaluate"],
                    bloom_mix={"analyse": 0.75, "evaluate": 0.25},
                    format_requirement="MATHEMATICAL_MODELING",
                ),
            ],
            cognitive_balance={"remember": 0.2, "apply": 0.5, "analyse": 0.3},
        )

    def test_solve_is_byte_identical_across_runs(self) -> None:
        nodes = _roomy_course_map()

        def run() -> str:
            items, report = solve(nodes, self._authored())
            return json.dumps(
                {
                    "items": [i.model_dump() for i in items],
                    "bloom_realised": report.bloom_realised,
                    "declared": report.cognitive_balance_declared,
                    "warnings": report.warnings,
                },
                sort_keys=True,
            )

        assert run() == run()

    def test_each_section_carries_its_own_format_and_group(self) -> None:
        items, _ = solve(_roomy_course_map(), self._authored())
        by_section: dict[str, list[str | None]] = {}
        for item in items:
            by_section.setdefault(item.slot_id.split("-")[0], []).append(
                item.format_requirement
            )

        assert by_section["A"] == ["TRUE_FALSE_SERIES"] * 6
        assert by_section["B"] == ["ALGORITHMIC_TRACE_PROBLEM"] * 4
        assert by_section["C"] == ["MATHEMATICAL_MODELING"] * 4

        groups = {i.slot_id: i.group_id for i in items}
        assert all(groups[f"B-{n:02d}"] == "Q2" for n in range(1, 5))
        assert all(groups[f"A-{n:02d}"] is None for n in range(1, 7))

    def test_span_uniqueness_still_holds_across_the_whole_paper(self) -> None:
        items, report = solve(_roomy_course_map(), self._authored())
        spans = [s for i in items for s in i.span_ids]
        assert len(spans) == len(set(spans))
        assert report.slots_filled == 14
        assert report.slots_by_mass + report.slots_by_fallthrough == 14
