"""P0 gate tests: LLM client — budget guard, dry-run, retry cap, key redaction."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

import httpx

from coursegen import config
from coursegen.llm.client import (
    BudgetExceeded,
    DailyCallLedger,
    LLMClient,
    TokenBudget,
    _redact,
)


class TestTokenBudget:
    def test_exceeded_on_call_cap(self) -> None:
        budget = TokenBudget(
            per_exam_call_cap=1,
            per_exam_token_cap=100_000,
            per_day_call_cap=1000,
        )
        client = LLMClient(dry_run=True, budget=budget)
        client.call([{"role": "user", "content": "first call"}])
        with pytest.raises(BudgetExceeded, match="Call cap reached"):
            client.call([{"role": "user", "content": "second call"}])

    def test_exceeded_on_token_cap(self) -> None:
        budget = TokenBudget(
            per_exam_call_cap=20,
            per_exam_token_cap=5,   # impossibly small — any call exceeds it
            per_day_call_cap=1000,
        )
        client = LLMClient(dry_run=True, budget=budget)
        with pytest.raises(BudgetExceeded, match="Token cap would be exceeded"):
            client.call([{"role": "user", "content": "a" * 100}])

    def test_budget_tracks_calls_and_tokens(self) -> None:
        budget = TokenBudget(per_exam_call_cap=5, per_exam_token_cap=10_000, per_day_call_cap=100)
        client = LLMClient(dry_run=True, budget=budget)
        client.call([{"role": "user", "content": "hello"}])
        assert budget.calls_used == 1
        assert budget.tokens_used > 0


class TestDryRun:
    def test_makes_no_network_calls(self) -> None:
        client = LLMClient(dry_run=True)
        with patch("httpx.Client") as mock_http:
            result = client.call([{"role": "user", "content": "test prompt"}])
            mock_http.assert_not_called()
        assert result["dry_run"] is True

    def test_returns_estimated_tokens(self) -> None:
        client = LLMClient(dry_run=True)
        msg = "a" * 400  # ~100 tokens
        result = client.call([{"role": "user", "content": msg}])
        assert result["estimated_tokens"] == 100


class TestRetry:
    def test_retry_cap_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaFakeKeyForTesting1234567890123456789")

        budget = TokenBudget(
            per_exam_call_cap=20,
            per_exam_token_cap=100_000,
            per_day_call_cap=1000,
        )
        client = LLMClient(dry_run=False, budget=budget)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.headers = {}

        call_count = 0

        def raise_server_error(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=MagicMock(),
                response=mock_response,
            )

        with patch.object(client, "_send", side_effect=raise_server_error), \
             patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client._call_with_retry(
                    [{"role": "user", "content": "test"}], None, 10
                )

        # 1 original attempt + MAX_RETRIES retries
        assert call_count == config.MAX_RETRIES + 1

    def test_non_retriable_4xx_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaFakeKeyForTesting1234567890123456789")

        budget = TokenBudget(
            per_exam_call_cap=20,
            per_exam_token_cap=100_000,
            per_day_call_cap=1000,
        )
        client = LLMClient(dry_run=False, budget=budget)

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {}

        call_count = 0

        def raise_bad_request(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError(
                "400 Bad Request",
                request=MagicMock(),
                response=mock_response,
            )

        with patch.object(client, "_send", side_effect=raise_bad_request), \
             patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client._call_with_retry(
                    [{"role": "user", "content": "test"}], None, 10
                )

        # 400 is not retriable — should fail on first attempt only
        assert call_count == 1


class TestKeyRedaction:
    def test_google_key_redacted(self) -> None:
        key = "AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"  # 35 chars after AIza
        msg = f"Authorization: Bearer {key} failed"
        redacted = _redact(msg)
        assert key not in redacted
        assert "[REDACTED]" in redacted

    def test_openai_key_redacted(self) -> None:
        key = "sk-" + "x" * 40
        msg = f"key={key} in request"
        redacted = _redact(msg)
        assert key not in redacted
        assert "[REDACTED]" in redacted

    def test_safe_text_unchanged(self) -> None:
        msg = "Normal log message with no keys"
        assert _redact(msg) == msg


class TestClientInit:
    def test_raises_without_key_in_non_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="GEMINI_API_KEY"):
            LLMClient(dry_run=False)

    def test_dry_run_does_not_require_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        client = LLMClient(dry_run=True)
        assert client._dry_run is True


class TestTheDailyCallCapIsEnforced:
    """F4: `PER_DAY_CALL_CAP` was defined, printed, assigned — and read by nothing.

    The counter cannot live in memory. Every request builds a fresh `LLMClient()`
    (`app/main.py`, `pipeline.py`), so `_calls_used` starts at 0 each time; the real
    ceiling was 20 calls per generation and unbounded in aggregate. Meanwhile
    `/api/chat` already told students they had reached "today's request limit".
    """

    @pytest.fixture(autouse=True)
    def _ledger(self, isolated_call_ledger):
        """`conftest.isolated_call_ledger` is autouse; this just names its path.

        Isolation lives there rather than here so it protects every test, not only
        the ones whose author remembered the real ledger exists.
        """
        self.ledger_path = isolated_call_ledger

    def _billable_call(self, cap: int) -> None:
        """One call through a BRAND NEW client, as a request would."""
        budget = TokenBudget(per_day_call_cap=cap)
        budget.check_call(10)
        budget.record_call(10)

    def test_the_cap_survives_a_fresh_client(self) -> None:
        """The property F4 says could not work."""
        for _ in range(3):
            self._billable_call(cap=3)
        with pytest.raises(BudgetExceeded, match="Daily call cap reached"):
            self._billable_call(cap=3)

    def test_the_count_is_on_disk_not_in_the_object(self) -> None:
        self._billable_call(cap=10)
        self._billable_call(cap=10)
        assert json.loads(self.ledger_path.read_text())["calls"] == 2

    def test_a_dry_run_does_not_spend_the_day(self) -> None:
        """No request leaves the process, so no provider quota is consumed.

        It still charges the per-exam counters — that is what makes a dry run a
        rehearsal of the budget, and it is unchanged from before the day cap.
        """
        budget = TokenBudget(per_exam_call_cap=5, per_day_call_cap=5)
        client = LLMClient(dry_run=True, budget=budget)
        client.call([{"role": "user", "content": "hello"}])
        assert budget.calls_used == 1, "the per-exam counter should still move"
        assert not self.ledger_path.exists(), "a dry run wrote to the daily ledger"

    def test_yesterdays_calls_are_not_spent_today(self) -> None:
        self.ledger_path.write_text(json.dumps(
            {"date": (date.today() - timedelta(days=1)).isoformat(), "calls": 9999}
        ))
        assert DailyCallLedger(self.ledger_path).calls_today() == 0
        self._billable_call(cap=1)  # must not raise

    def test_todays_calls_are_counted_today(self) -> None:
        self.ledger_path.write_text(json.dumps(
            {"date": date.today().isoformat(), "calls": 4}
        ))
        assert DailyCallLedger(self.ledger_path).calls_today() == 4

    def test_a_corrupt_ledger_fails_open_rather_than_breaking_the_app(self) -> None:
        """A disk problem must not take down a working application.

        Failing open is the deliberate choice and it is logged, because a silently
        inert cap is the defect being fixed here.
        """
        self.ledger_path.write_text("{ not json at all")
        assert DailyCallLedger(self.ledger_path).calls_today() == 0
        self._billable_call(cap=1)  # must not raise

    def test_an_unwritable_ledger_does_not_lose_a_paid_call(self) -> None:
        """The write happens AFTER a call that has already been paid for.

        Raising there would throw away work the student is waiting on.
        """
        budget = TokenBudget(per_day_call_cap=100)
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            budget.record_call(10)          # must not raise
        assert budget.calls_used == 1

    def test_the_missing_ledger_is_not_an_error(self) -> None:
        assert not self.ledger_path.exists()
        assert DailyCallLedger(self.ledger_path).calls_today() == 0

    def test_the_per_exam_cap_still_fires_first(self) -> None:
        """The day cap is added, not substituted. R10: the old guard is unchanged."""
        budget = TokenBudget(per_exam_call_cap=1, per_day_call_cap=1000)
        client = LLMClient(dry_run=True, budget=budget)
        client.call([{"role": "user", "content": "first"}])
        with pytest.raises(BudgetExceeded, match="Call cap reached"):
            client.call([{"role": "user", "content": "second"}])
