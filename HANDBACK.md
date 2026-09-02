# Handback — Prepify

**For:** Future agents and reviewers
**From:** Antigravity (Current Active Agent)
**Date:** 2026-09-01

This document maintains the active context, action log, and state tracking of the current implementation phase.

---

## 1. Problem Analysis & Plan of Action

### 1.1 Memory Crash Blocker (BGE-M3 / Ingest)
- **Problem:** The system hard-crashes (OSError 1455) during `ingest` and `chat` because the Windows page file was capped, leaving insufficient committable memory for the memory-mapped weights of the 2.2GB BGE-M3 embedding model.
- **Cause/Reason:** The PyTorch/FAISS forward pass touches an unbacked memory page, causing an OS-level access violation that bypasses Python's error handling.
- **Fix (User Action):** Change Windows virtual memory settings to "System-managed" or increase max size to 16-24 GB.
- **Status:** OS configuration verified. Preflight memory tests pass, and `coursegen.eval` completes successfully with a 571-node map. (Completed)

### 1.2 P5 Missing Declared Gates (Browser E2E & Network Resilience)
- **Problem:** P5 (UI + resilience) was prematurely marked PASS based solely on mocked tests. It requires a browser-based end-to-end test and a network failure test.
- **Cause/Reason:** A fully mocked suite proves seams agree, but not that the real components work. Simulated mid-flight network death and real browser parsing were entirely missing.
- **Fix:** 
  1. Write a Playwright/Selenium (or equivalent) test (E7.1) for full-flow automation (upload → ingest → generate → download → chat).
  2. Write a unit test patching `httpx.ConnectError` inside `_run_exam_pipeline` to ensure degraded JSON returns instead of a 500 crash.
- **Status:** Pending implementation.

### 1.3 Fixture Audit for Thresholds
- **Problem:** Test fixtures in `tests/` currently use hardcoded values implicitly tied to `config.py` thresholds (e.g. `RELEVANCE_FLOOR = -2.0`). When thresholds move, tests silently pass while asserting nothing.
- **Cause/Reason:** Hardcoded boundaries lose meaning if configuration changes, violating the rule that a test must be able to fail.
- **Fix:** Audit and rewrite fixtures near `DEDUP_TAU`, `RERANKER_THRESHOLD`, `RELEVANCE_FLOOR`, `OPTION_LENGTH_BAND`, and `TOPIC_MATCH_*` to test relative boundaries dynamically. Apply mutation testing to every guard.
- **Status:** Pending implementation.

### 1.4 Opt-in Live Tests (`--live`)
- **Problem:** Real model execution paths (BGE-M3 embedder, live Gemini calls) are mocked out in CI.
- **Cause/Reason:** Mocked testing allowed pipeline code to crash on its first real line in production previously.
- **Fix:** Add tests marked `@pytest.mark.live` that pass actual text to the real embedder and make a single live API smoke call (excluded from default `pytest` runs).
- **Status:** Pending implementation.

### 1.5 E8 LLM Reliability Variance Harness
- **Problem:** The system has only run one full end-to-end exam. N=1 does not prove reliability.
- **Cause/Reason:** LLM outputs are non-deterministic, requiring statistical sampling to measure failure modes.
- **Fix:** Create a variance harness (`tests/test_p6_harness.py` or similar) that runs the generation pipeline N times and measures JSON validation failure rates, API timeouts, and recovery capabilities.
- **Status:** Pending implementation.

### 1.6 E4.2 Prompt Injection Testing
- **Problem:** Prepify takes arbitrary third-party documents as input but has zero tests for prompt injection attacks.
- **Cause/Reason:** Without testing resilience against malicious slides (e.g., "ignore previous instructions"), live demos on user data carry massive functional risk.
- **Fix:** Author a test fixture mimicking a malicious slide and verify validation gates safely handle it.
- **Status:** Pending implementation.

---

## 2. Action Logs

- **2026-09-01:**
  - Initialized workspace context from `HANDOFF-ANTIGRAVITY.md` and `STATE.md`.
  - Established project invariant understanding: strict verification, rule of measurement over reasoning, and non-negotiable reliance on real output over mocked tests.
  - Drafted comprehensive 3-pointer plan of action for all 6 active problem dimensions.
  - User confirmed the Windows Page File memory settings have been updated to resolve the `BGE-M3` memory crash (Problem 1).
  - Prepared `HANDBACK.md` for consistent state tracking.
  - Verified Problem 1 fix: `pytest tests/test_p5_preflight_memory.py` passes 6/6 tests, and `python -m coursegen.eval` runs to completion cleanly.
  - Transitioned active task focus to Problem 2 (P5 Missing Declared Gates).
  - Implemented `ConnectError` test for mid-generation network death in `test_p5_app.py`. Added proper `httpx.RequestError` handling in `app/main.py`. Test verified passing.
  - Implemented Browser E2E test `test_p5_browser_e2e.py` using `pytest-playwright` simulating upload -> ingest -> generate -> download -> chat. Test verified passing.

---

## 3. Implementation State

### Current Progress
- **Problem 1 (Memory Blocker):** OS configuration verified. Completed.
- **Problem 2 (P5 Missing Gates):** Network kill and Browser E2E tests verified. Completed.
- **Problem 3 (Fixture Audit):** Starting implementation.
- **Problem 4-6:** Not Started.

### Active Task
- Implementing Problem 3 (Fixture Audit). Checking threshold-bound fixtures against `config.py` constants.

### Left to Implement
- Problem 3 (Fixture Audit): Audit and mutation-test threshold-bound fixtures.
- Problem 4 (Opt-in Live Tests): `--live` marker test implementation.
- Problem 5 (LLM Reliability Variance Harness).
- Problem 6 (Prompt Injection testing).
