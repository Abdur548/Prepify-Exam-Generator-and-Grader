"""Gate 5 — factuality verifier. Offline behaviour, plus one live validation.

The offline tests pin the decisions that make this gate safe rather than
decorative: a missing verdict must never become a pass, synthesis items must be
omitted rather than failed, and the summary must distinguish "not checked" from
"checked and clean".
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from coursegen.contracts.item import GeneratedItem, ItemSpec, SourceRef
from coursegen.exam.verify import (
    CONTRADICTED,
    NOT_STATED,
    SUPPORTED,
    Verdict,
    summarise,
    verify_items,
)


def _spec(slot: str, grounding: str = "span", spans=("c1",)) -> ItemSpec:
    return ItemSpec(
        slot_id=slot, item_type="short", marks=5, bloom="remember",
        node_id="n1", span_ids=list(spans), eligibility=[], spec_hash=f"h{slot}",
        grounding=grounding,
    )


def _item(slot: str, stem: str = "What does BFS explore first?", answer: str = "Shallowest nodes.") -> GeneratedItem:
    return GeneratedItem(
        slot_id=slot, stem=stem, model_answer=answer, explanation="because",
        source_ref=SourceRef(file="deck.pdf", pages=[1]),
    )


def _client(verdicts: list[dict]) -> MagicMock:
    client = MagicMock()
    client.call.return_value = {
        "choices": [{"message": {"content": json.dumps({"verdicts": verdicts})}}]
    }
    return client


class TestVerdictHandling:

    def test_supported_and_contradicted_are_returned(self) -> None:
        client = _client([
            {"index": 0, "verdict": SUPPORTED, "quote": "BFS expands the shallowest node."},
            {"index": 1, "verdict": CONTRADICTED, "quote": "BFS is complete."},
        ])
        out = verify_items(
            [(_spec("A-01"), _item("A-01")), (_spec("A-02"), _item("A-02"))],
            {"c1": "BFS expands the shallowest node. BFS is complete."},
            client,
        )
        assert out["A-01"].verdict == SUPPORTED
        assert out["A-01"].is_supported
        assert out["A-02"].verdict == CONTRADICTED
        assert not out["A-02"].is_supported

    def test_a_missing_verdict_is_not_stated_never_supported(self) -> None:
        """An item the verifier did not answer for must not inherit a pass.

        The failure mode this prevents: a truncated or malformed response silently
        promoting every unanswered item to SUPPORTED, which would make the gate
        report a clean paper precisely when it failed.
        """
        client = _client([{"index": 0, "verdict": SUPPORTED, "quote": "q"}])
        out = verify_items(
            [(_spec("A-01"), _item("A-01")), (_spec("A-02"), _item("A-02"))],
            {"c1": "some passage"}, client,
        )
        assert out["A-02"].verdict == NOT_STATED
        assert not out["A-02"].is_supported

    def test_a_malformed_response_fails_closed(self) -> None:
        client = MagicMock()
        client.call.return_value = {"choices": [{"message": {"content": "not json"}}]}
        out = verify_items([(_spec("A-01"), _item("A-01"))], {"c1": "passage"}, client)
        assert out["A-01"].verdict == NOT_STATED

    def test_an_unrecognised_verdict_string_is_discarded(self) -> None:
        client = _client([{"index": 0, "verdict": "PROBABLY_FINE", "quote": ""}])
        out = verify_items([(_spec("A-01"), _item("A-01"))], {"c1": "passage"}, client)
        assert out["A-01"].verdict == NOT_STATED


class TestApplicability:

    def test_synthesis_items_are_omitted_not_failed(self) -> None:
        """A synthesis item is written to invent its artifact.

        "The passage does not state this" is true of it by design, so marking it
        NOT_STATED would read as a failure of the item rather than of the question.
        It is excluded, and the caller reports it as not-applicable — the same
        distinction `new_gate_report` draws.
        """
        client = _client([{"index": 0, "verdict": SUPPORTED, "quote": "q"}])
        out = verify_items(
            [(_spec("B-01", grounding="synthesis"), _item("B-01"))],
            {"c1": "passage"}, client,
        )
        assert out == {}
        client.call.assert_not_called()

    def test_items_without_spans_are_omitted(self) -> None:
        client = MagicMock()
        out = verify_items([(_spec("A-01", spans=()), _item("A-01"))], {}, client)
        assert out == {}
        client.call.assert_not_called()

    def test_items_sharing_a_span_cost_one_call(self) -> None:
        """The passage is sent once, not once per item."""
        client = _client([
            {"index": 0, "verdict": SUPPORTED, "quote": "q"},
            {"index": 1, "verdict": SUPPORTED, "quote": "q"},
        ])
        verify_items(
            [(_spec("A-01"), _item("A-01")), (_spec("A-02"), _item("A-02"))],
            {"c1": "shared passage"}, client,
        )
        assert client.call.call_count == 1

    def test_the_claim_includes_the_stem(self) -> None:
        """A selection item's answer is a label carrying no content on its own.

        Scoring "True" against a lecture slide is what destroyed an entire
        TRUE_FALSE section under gate 2.
        """
        client = _client([{"index": 0, "verdict": SUPPORTED, "quote": "q"}])
        verify_items([(_spec("A-01"), _item("A-01", stem="Is BFS complete?", answer="True"))],
                     {"c1": "passage"}, client)
        sent = client.call.call_args[0][0][1]["content"]
        assert "Is BFS complete?" in sent and "True" in sent


class TestSummary:

    def test_not_applicable_is_distinct_from_checked(self) -> None:
        verdicts = {"A-01": Verdict("A-01", SUPPORTED), "A-02": Verdict("A-02", NOT_STATED)}
        s = summarise(verdicts, items_total=5)
        assert s["checked"] == 2
        assert s["not_applicable"] == 3     # 5 items, 2 checked
        assert s["supported"] == 1
        assert s["supported_ratio"] == 0.5

    def test_ratio_is_none_when_nothing_was_checked(self) -> None:
        """Not 1.0. A gate that checked nothing has no pass rate."""
        s = summarise({}, items_total=4)
        assert s["supported_ratio"] is None
        assert s["checked"] == 0

    def test_the_summary_carries_its_own_caveat(self) -> None:
        """The manifest must not let SUPPORTED be read as proof of correctness."""
        s = summarise({"A-01": Verdict("A-01", SUPPORTED)}, items_total=1)
        assert "not proof of correctness" in s["caveat"]


@pytest.mark.live
class TestLiveSeparation:
    """The validation that decides whether this gate is worth having.

    Gate 2 shipped twice on a signal that could not separate true from false. This
    asserts the property directly, against a real span, before anyone relies on it.
    """

    def test_no_false_claim_is_ever_marked_supported(self) -> None:
        from coursegen.llm.client import LLMClient

        passage = (
            "Iterative deepening search combines the space efficiency of "
            "depth-first search with the completeness of breadth-first search. "
            "It is optimal when every step costs the same, and uses O(bd) space, "
            "which is linear in the depth of the solution."
        )
        true_claims = [
            ("T-01", "Does IDS use linear space?", "Yes, O(bd), linear in the depth."),
            ("T-02", "When is IDS optimal?", "When every step costs the same."),
        ]
        false_claims = [
            ("F-01", "How much space does IDS use?", "Exponential space, worse than DFS."),
            ("F-02", "What is this passage about?", "The Krebs cycle in cellular respiration."),
            ("F-03", "Is IDS optimal?", "No, IDS is never optimal under any cost model."),
        ]
        pairs = [(_spec(sid), _item(sid, stem, ans))
                 for sid, stem, ans in true_claims + false_claims]
        out = verify_items(pairs, {"c1": passage}, LLMClient())

        accepted_false = [sid for sid, _s, _a in false_claims
                          if out.get(sid) and out[sid].is_supported]
        assert not accepted_false, (
            f"the verifier endorsed false claims {accepted_false} — it must not ship "
            "as a factuality gate; this is exactly how gate 2 failed"
        )
        retained = [sid for sid, _s, _a in true_claims
                    if out.get(sid) and out[sid].is_supported]
        assert retained, "the verifier rejected every true claim; it would empty every paper"
