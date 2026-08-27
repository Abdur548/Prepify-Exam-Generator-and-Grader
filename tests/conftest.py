"""Pytest configuration. The mocked LLM client is the default for all tests.
Live API tests require --live and are excluded from the default run (L3).
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from coursegen.llm.client import LLMClient, TokenBudget


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests that make real LLM API calls (excluded by default).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--live"):
        skip_live = pytest.mark.skip(reason="Pass --live to run live API tests.")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """Default mocked LLM client. Replays a minimal valid response."""
    client = MagicMock(spec=LLMClient)
    client.budget = TokenBudget()
    client.call.return_value = {"mock": True, "estimated_tokens": 100}
    return client


@pytest.fixture
def token_budget() -> TokenBudget:
    return TokenBudget()
