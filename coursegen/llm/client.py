"""Single LLM chokepoint. Every call in the codebase goes through here."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

from coursegen import config

load_dotenv()

logger = logging.getLogger(__name__)

# Redact API key patterns before any log line or exception message is written (S1).
_KEY_RE = re.compile(r"(AIza[A-Za-z0-9_\-]{35,}|sk-[A-Za-z0-9]{32,})")


def _redact(text: str) -> str:
    return _KEY_RE.sub("[REDACTED]", text)


class BudgetExceeded(Exception):
    """Raised when a call would breach the per-exam call or token cap."""


@dataclass
class TokenBudget:
    per_exam_call_cap: int = config.PER_EXAM_CALL_CAP
    per_exam_token_cap: int = config.PER_EXAM_TOKEN_CAP
    per_day_call_cap: int = config.PER_DAY_CALL_CAP

    _calls_used: int = field(default=0, init=False, repr=False)
    _tokens_used: int = field(default=0, init=False, repr=False)

    def check_call(self, estimated_tokens: int) -> None:
        """Assert budget before sending. Raises BudgetExceeded — never loops silently."""
        if self._calls_used >= self.per_exam_call_cap:
            raise BudgetExceeded(
                f"Call cap reached: {self._calls_used}/{self.per_exam_call_cap}"
            )
        if self._tokens_used + estimated_tokens > self.per_exam_token_cap:
            raise BudgetExceeded(
                f"Token cap would be exceeded: "
                f"{self._tokens_used}+{estimated_tokens} > {self.per_exam_token_cap}"
            )

    def record_call(self, tokens_used: int) -> None:
        self._calls_used += 1
        self._tokens_used += tokens_used

    @property
    def calls_used(self) -> int:
        return self._calls_used

    @property
    def tokens_used(self) -> int:
        return self._tokens_used


def _estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Cheap char-based estimate: 4 chars ≈ 1 token."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, total_chars // 4)


class LLMClient:
    """
    Gemini Flash-Lite via OpenAI-compatible endpoint.
    Not configurable to a different provider — see PRD §6.
    """

    def __init__(
        self,
        dry_run: bool = False,
        budget: TokenBudget | None = None,
    ) -> None:
        self._dry_run = dry_run
        self._budget = budget or TokenBudget()
        self._api_key: str = os.getenv("GEMINI_API_KEY", "")
        if not self._dry_run and not self._api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY not set. Add it to .env (see .env.example)."
            )

    def call(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Single entry point for all LLM calls. Checks budget before sending."""
        estimated = _estimate_tokens(messages)
        self._budget.check_call(estimated)

        if self._dry_run:
            logger.info("[dry-run] estimated_tokens=%d", estimated)
            self._budget.record_call(estimated)
            return {"dry_run": True, "estimated_tokens": estimated}

        return self._call_with_retry(messages, response_schema, estimated)

    def _call_with_retry(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any] | None,
        estimated_tokens: int,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None

        for attempt in range(config.MAX_RETRIES + 1):
            try:
                result = self._send(messages, response_schema)
                actual = result.get("usage", {}).get("total_tokens", estimated_tokens)
                self._budget.record_call(actual)
                return result

            except httpx.TimeoutException as exc:
                # L7: log the abandoned call and count it against the budget.
                logger.warning(
                    "[attempt %d/%d] Request timed out; charging budget: %s",
                    attempt + 1,
                    config.MAX_RETRIES + 1,
                    _redact(str(exc)),
                )
                self._budget.record_call(estimated_tokens)
                last_exc = exc
                if attempt < config.MAX_RETRIES:
                    time.sleep(self._backoff(attempt))

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 or status >= 500:
                    retry_after = float(exc.response.headers.get("retry-after", 0))
                    delay = max(retry_after, self._backoff(attempt))
                    logger.warning(
                        "[attempt %d/%d] HTTP %d; sleeping %.1fs",
                        attempt + 1,
                        config.MAX_RETRIES + 1,
                        status,
                        delay,
                    )
                    last_exc = exc
                    if attempt < config.MAX_RETRIES:
                        time.sleep(delay)
                else:
                    # 4xx (not 429) — not retriable; redact key before re-raising.
                    raise httpx.HTTPStatusError(
                        _redact(str(exc)),
                        request=exc.request,
                        response=exc.response,
                    ) from exc

        raise last_exc or RuntimeError("LLM call failed after retries")

    def _send(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.GEMINI_MODEL,
            "messages": messages,
        }
        if response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output",
                    "schema": response_schema,
                    "strict": True,
                },
            }

        headers = {
            # Key only in Authorization header — never in URL, never logged (S1).
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as http:
            response = http.post(
                f"{config.GEMINI_BASE_URL}chat/completions",
                json=payload,
                headers=headers,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Include the provider's RESPONSE BODY, not just httpx's summary.
            #
            # `str(exc)` is only "Client error '404 Not Found' for url ..." — the
            # reason lives in the body, and dropping it makes every API failure
            # undiagnosable. Measured on the first real run: a 404 whose body said
            # "models/gemini-2.0-flash-lite is no longer available, use
            # models/gemini-3.5-flash-lite" surfaced as a bare 404, and finding the
            # cause needed a separate probe script against the live API.
            #
            # Still redacted (S1), and truncated so a large error page cannot flood
            # a log. A degraded-mode message the user cannot act on is barely better
            # than a crash.
            body = _redact(exc.response.text)[:config.ERROR_BODY_MAX_CHARS]
            raise httpx.HTTPStatusError(
                f"{_redact(str(exc))} — response body: {body}",
                request=exc.request,
                response=exc.response,
            ) from exc

        return response.json()

    @staticmethod
    def _backoff(attempt: int) -> float:
        base = config.RETRY_BASE_DELAY_SECONDS * (2**attempt)
        jitter = random.uniform(0, base * 0.3)
        return min(base + jitter, config.RETRY_MAX_DELAY_SECONDS)

    @property
    def budget(self) -> TokenBudget:
        return self._budget
