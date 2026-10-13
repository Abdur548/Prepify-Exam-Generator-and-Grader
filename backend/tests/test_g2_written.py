"""G2 — written answers, marked in validated batches.

The rule this file exists for: **reject, never clamp.** A 9 returned for a
4-mark question is not "4 with enthusiasm" — it is a marker that did not do what
it was asked, and storing 4 would be a plausible wrong mark the student has no
reason to doubt. Rejected marks are retried once, alone, and then left unmarked
with the reason said out loud.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from coursegen import config
from coursegen.grade.records import WrittenEntry
from coursegen.grade.written import (
    INVALID_MARK,
    default_client,
    make_marker,
    mark_written,
)
from coursegen.llm.client import BudgetExceeded, LLMClient, TokenBudget


def _entry(slot: str, marks: int = 4, from_material: bool = True) -> WrittenEntry:
    return WrittenEntry(
        slot_id=slot, item_type="short", marks_available=marks, stem=f"Explain {slot}.",
        model_answer="A queue.", explanation="FIFO.",
        passage="BFS uses a FIFO queue." if from_material else None,
        from_material=from_material, response=f"answer {slot}",
    )


def _mark(slot: str, marks=3, reason="Names the queue.", confidence="high") -> dict:
    return {"slot_id": slot, "marks_awarded": marks, "reason": reason, "confidence": confidence}


def _resp(*marks: dict) -> dict:
    return {"marks": list(marks)}


def _client(*responses) -> MagicMock:
    client = MagicMock(spec=LLMClient)
    client.budget = TokenBudget()
    client.call.side_effect = list(responses)
    return client


def _slots_sent(client: MagicMock, call_index: int) -> list[str]:
    user = client.call.call_args_list[call_index].args[0][1]["content"]
    payload = json.loads(user.split("Items:\n", 1)[1].split("\n\n", 1)[0])
    return [p["slot_id"] for p in payload]


class TestBatching:

    def test_seven_answers_take_two_calls(self) -> None:
        entries = [_entry(f"B-{i:02d}") for i in range(1, 8)]
        client = _client(
            _resp(*[_mark(e.slot_id) for e in entries[:6]]),
            _resp(_mark("B-07")),
        )
        out = mark_written(entries, client)
        assert client.call.call_count == 2
        assert _slots_sent(client, 0) == [e.slot_id for e in entries[:6]]
        assert [m.marks_awarded for m in out.marks] == [3] * 7
        assert out.degraded is None

    def test_the_batch_size_is_the_configured_one(self) -> None:
        assert config.GRADE_BATCH_SIZE == 6

    def test_marks_come_back_in_the_order_the_answers_went_in(self) -> None:
        client = _client(_resp(_mark("B-02", 1), _mark("B-01", 2)))
        out = mark_written([_entry("B-01"), _entry("B-02")], client)
        assert [(m.slot_id, m.marks_awarded) for m in out.marks] == [("B-01", 2), ("B-02", 1)]

    def test_the_content_envelope_is_read_too(self) -> None:
        envelope = {"choices": [{"message": {"content": json.dumps(_resp(_mark("B-01", 2)))}}]}
        out = mark_written([_entry("B-01")], _client(envelope))
        assert out.marks[0].marks_awarded == 2


class TestRejectNeverClamp:

    def test_an_out_of_range_mark_is_retried_alone(self) -> None:
        client = _client(_resp(_mark("B-01", 9), _mark("B-02", 2)), _resp(_mark("B-01", 3)))
        out = mark_written([_entry("B-01"), _entry("B-02")], client)
        assert _slots_sent(client, 1) == ["B-01"]
        assert [m.marks_awarded for m in out.marks] == [3, 2]

    def test_an_out_of_range_mark_twice_is_left_unmarked_never_clamped(self) -> None:
        client = _client(_resp(_mark("B-01", 9)), _resp(_mark("B-01", 9)))
        (mark,) = mark_written([_entry("B-01")], client).marks
        assert mark.marks_awarded is None and mark.reason == INVALID_MARK

    def test_a_negative_mark_is_rejected(self) -> None:
        client = _client(_resp(_mark("B-01", -1)), _resp(_mark("B-01", -1)))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_true_is_not_a_mark(self) -> None:
        client = _client(_resp(_mark("B-01", True)), _resp(_mark("B-01", True)))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_a_fractional_mark_is_rejected(self) -> None:
        client = _client(_resp(_mark("B-01", 2.5)), _resp(_mark("B-01", 2.5)))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_an_empty_reason_is_rejected(self) -> None:
        client = _client(_resp(_mark("B-01", reason="  ")), _resp(_mark("B-01", reason="")))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_an_over_long_reason_is_rejected(self) -> None:
        long = "x" * (config.GRADE_REASON_MAX_CHARS + 1)
        client = _client(_resp(_mark("B-01", reason=long)), _resp(_mark("B-01", reason=long)))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_an_unknown_confidence_is_rejected(self) -> None:
        client = _client(_resp(_mark("B-01", confidence="medium")),
                         _resp(_mark("B-01", confidence="sure")))
        assert mark_written([_entry("B-01")], client).marks[0].marks_awarded is None

    def test_a_slot_marked_twice_in_one_batch_is_retried(self) -> None:
        """Two different marks for one answer: either would be a guess."""
        client = _client(_resp(_mark("B-01", 1), _mark("B-01", 4)), _resp(_mark("B-01", 2)))
        out = mark_written([_entry("B-01")], client)
        assert out.marks[0].marks_awarded == 2 and client.call.call_count == 2

    def test_a_missing_slot_is_retried_and_a_stray_one_ignored(self) -> None:
        client = _client(_resp(_mark("B-99", 4)), _resp(_mark("B-01", 1)))
        out = mark_written([_entry("B-01")], client)
        assert [(m.slot_id, m.marks_awarded) for m in out.marks] == [("B-01", 1)]

    def test_an_unreadable_response_is_retried_not_a_crash(self) -> None:
        """The attempt is already saved; a 500 here would tell the student something
        broke when their answers are safe and simply waiting for a mark."""
        bad = {"choices": [{"message": {"content": "not json"}}]}
        out = mark_written([_entry("B-01")], _client(bad, bad))
        assert out.marks[0].marks_awarded is None and out.marks[0].reason == INVALID_MARK


class TestConfidence:

    def test_a_synthesis_answer_is_always_low_confidence(self) -> None:
        """No passage to anchor the judgement, whatever the model says."""
        client = _client(_resp(_mark("C-01", 7, confidence="high")))
        out = mark_written([_entry("C-01", marks=10, from_material=False)], client)
        assert out.marks[0].confidence == "low"

    def test_a_material_answer_keeps_the_models_confidence(self) -> None:
        out = mark_written([_entry("B-01")], _client(_resp(_mark("B-01", confidence="high"))))
        assert out.marks[0].confidence == "high"


class TestDegraded:

    def test_a_quota_stop_keeps_what_was_already_marked(self) -> None:
        entries = [_entry(f"B-{i:02d}") for i in range(1, 8)]
        client = _client(_resp(*[_mark(e.slot_id) for e in entries[:6]]), BudgetExceeded("cap"))
        out = mark_written(entries, client)
        assert [m.marks_awarded for m in out.marks[:6]] == [3] * 6
        assert out.marks[6].marks_awarded is None
        assert out.degraded and "saved" in out.degraded

    def test_a_network_failure_is_degraded_not_raised(self) -> None:
        client = _client(httpx.ConnectError("refused"))
        out = mark_written([_entry("B-01")], client)
        assert out.marks[0].marks_awarded is None and out.degraded

    def test_a_provider_error_is_degraded_not_raised(self) -> None:
        req = httpx.Request("POST", "https://example.invalid")
        err = httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
        out = mark_written([_entry("B-01")], _client(err))
        assert out.degraded and out.marks[0].marks_awarded is None

    def test_a_programming_error_still_raises(self) -> None:
        with pytest.raises(RuntimeError):
            mark_written([_entry("B-01")], _client(RuntimeError("bug")))

    def test_no_api_key_marks_nothing_and_says_so(self, monkeypatch) -> None:
        def factory():
            raise EnvironmentError("GEMINI_API_KEY not set")
        out = make_marker(factory)([_entry("B-01")])
        assert out.marks[0].marks_awarded is None
        assert out.degraded and "API key" in out.degraded

    def test_the_marker_builds_its_client_only_when_asked(self) -> None:
        built = []
        marker = make_marker(lambda: built.append(1) or _client(_resp(_mark("B-01"))))
        assert built == []
        marker([_entry("B-01")])
        assert built == [1]


class TestBudget:

    def test_a_submission_has_its_own_call_cap(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
        assert default_client().budget.per_exam_call_cap == config.PER_SUBMISSION_CALL_CAP == 6
