"""Single LLM chokepoint. Every call in the codebase goes through here."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from coursegen import config

load_dotenv()

logger = logging.getLogger(__name__)

# Redact API key patterns before any log line or exception message is written (S1).
#
# `AQ\.[\w.-]{20,}` was added 2026-09-01 because the key actually in use starts
# `AQ.A` and is 53 characters — it matched NEITHER existing alternative, so the
# live key passed through _redact() untouched. A redaction rule that does not
# cover the credential you are holding is decoration.
_KEY_RE = re.compile(
    r"(AIza[A-Za-z0-9_\-]{35,}|sk-[A-Za-z0-9]{32,}|AQ\.[A-Za-z0-9_.\-]{20,})"
)


def _redact(text: str) -> str:
    """Strip the API key from anything about to be logged or raised.

    Two layers, deliberately. The literal-value pass is the one that actually
    guarantees the property: whatever format a provider invents next, the process
    knows its own key and can remove exactly that string. The pattern pass is the
    fallback, covering keys that are not this process's own — a key pasted into
    course material, or echoed back inside a provider's error body.

    Relying on the pattern alone is what failed here: it was written against
    `AIza…` when the credential in use was `AQ.A…`.
    """
    key = os.getenv("GEMINI_API_KEY", "")
    # The length floor stops an empty or absurdly short value turning every
    # character of the message into [REDACTED].
    if len(key) >= 12:
        text = text.replace(key, "[REDACTED]")
    return _KEY_RE.sub("[REDACTED]", text)


class BudgetExceeded(Exception):
    """Raised when a call would breach the per-exam or per-day cap."""


def _today() -> str:
    return date.today().isoformat()


@dataclass
class DailyCallLedger:
    """Calls made per calendar day, persisted because the counter cannot live in memory.

    `PER_DAY_CALL_CAP` was defined on 2026-08-30 with a paragraph of rationale and
    enforced nowhere: `check_call` tested the two per-exam caps and nothing read the
    day cap at all (F4). It could not have worked in memory even if it had — every
    request builds a fresh `LLMClient()` (`app/main.py`, `pipeline.py`), so
    `_calls_used` starts at 0 each time. The ceiling was 20 calls per generation,
    unbounded in aggregate, while the chat route was already telling students they
    had reached "today's request limit" and it would reset.

    ## What this counts, and what it does not

    It counts calls THIS APPLICATION recorded, on the local calendar day. It is not
    the provider's quota: it does not know about calls made from another machine,
    another key, or outside this app, and local midnight is not the provider's reset
    window. Treat a breach as "we have made 900 calls today", never as "the provider
    will refuse the next one".

    ## Failure is open, deliberately

    A ledger that cannot be read reports 0 and a ledger that cannot be written logs
    and returns. Failing closed would let a disk problem take down a working app,
    and the write happens AFTER a call that has already been paid for — raising
    there would throw away work the student is waiting on. Both paths log at
    warning, because a silently inert cap is exactly the defect this fixes.

    ## Concurrency

    Read-modify-write with no lock. Safe in the shipped configuration and only
    there: uvicorn runs single-worker (S8) and Qdrant's exclusive file lock stops
    the CLI running beside the server. If either constraint is ever relaxed, two
    writers can lose an increment and this needs a real lock.
    """

    path: Path

    def _read(self) -> tuple[str, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return str(data.get("date", "")), int(data.get("calls", 0))
        except FileNotFoundError:
            return "", 0
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "Daily call ledger unreadable, treating today as 0 calls: %s", exc
            )
            return "", 0

    def calls_today(self) -> int:
        day, calls = self._read()
        return calls if day == _today() else 0

    def record(self) -> None:
        day, calls = self._read()
        calls = calls + 1 if day == _today() else 1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"date": _today(), "calls": calls}), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Daily call ledger not written; the day cap is not being "
                           "enforced for this call: %s", exc)


@dataclass
class TokenBudget:
    per_exam_call_cap: int = config.PER_EXAM_CALL_CAP
    per_exam_token_cap: int = config.PER_EXAM_TOKEN_CAP
    per_day_call_cap: int = config.PER_DAY_CALL_CAP
    # Resolved on first use, not here, so a test redirecting `config.OUTPUT_DIR`
    # gets its own ledger instead of writing the real one.
    ledger: DailyCallLedger | None = None

    _calls_used: int = field(default=0, init=False, repr=False)
    _tokens_used: int = field(default=0, init=False, repr=False)

    def _get_ledger(self) -> DailyCallLedger:
        if self.ledger is None:
            self.ledger = DailyCallLedger(config.call_ledger_path())
        return self.ledger

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
        used_today = self._get_ledger().calls_today()
        if used_today >= self.per_day_call_cap:
            raise BudgetExceeded(
                f"Daily call cap reached: {used_today}/{self.per_day_call_cap}"
            )

    def record_call(self, tokens_used: int, *, billable: bool = True) -> None:
        """Charge one call.

        `billable` is False only for a dry run, which reaches no provider and so
        cannot consume a daily quota. It still charges the per-exam counters, which
        is what makes a dry run a useful rehearsal of the budget — that behaviour is
        unchanged from before the day cap existed.
        """
        self._calls_used += 1
        self._tokens_used += tokens_used
        if billable:
            self._get_ledger().record()

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
            # Not billable: no request leaves the process, so it cannot spend a day
            # of provider quota. It still charges the per-exam counters.
            self._budget.record_call(estimated, billable=False)
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
