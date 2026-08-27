"""P0 gate tests: LLM client — budget guard, dry-run, retry cap, key redaction."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import httpx

from coursegen import config
from coursegen.llm.client import BudgetExceeded, LLMClient, TokenBudget, _redact


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
