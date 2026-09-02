import threading
import time
import pytest
from unittest.mock import patch
import uvicorn
import requests
from playwright.sync_api import Page, expect
from pathlib import Path

# How long to wait for the server to answer /api/health before giving up.
#
# Sized from measurement, not guessed. The app's `_lifespan` runs
# `run_preflight_checks()` before uvicorn accepts connections, and that costs
# **4.97 s on a cold process** (0.22 s once warm) because it imports WeasyPrint,
# which drags in GTK. The original budget was 20 attempts x 0.5 s = 10 s, i.e.
# 2x a 5 s startup — too thin under any load, and the observed flake was ~20%
# (1 failure in 5 runs, always the slowest run).
_SERVER_START_TIMEOUT_S = 60.0
_SERVER_POLL_INTERVAL_S = 0.25

# Default for assertions that do not set their own. Playwright's built-in default
# is 5 s, which is generous for a mocked call on an idle machine and marginal for
# one on a loaded one. Every assertion here except the ingest wait is against a
# mocked backend, so this only has to absorb browser scheduling jitter.
_DEFAULT_ASSERTION_TIMEOUT_MS = 15_000

# The ingest wait is the one real backend call in this test. Measured at
# 25.2-25.8 s across three cold runs (dominated by the BGE-M3 load, not by
# embedding two pages), so 90 s is ~3.5x the measured cost.
_INGEST_TIMEOUT_MS = 90_000


# Provide a live server for Playwright to test against
@pytest.fixture(scope="session")
def live_server_url():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()

    from coursegen.app.main import app
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="critical")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"

    # Poll against a wall-clock deadline, and FAIL LOUDLY if the server never
    # comes up. The previous loop had three defects, each of which turns a
    # startup problem into a misleading downstream failure:
    #
    #   1. It counted attempts rather than elapsed time, so its real budget
    #      depended on how fast the failures came back.
    #   2. A non-200 response skipped the sleep entirely, burning every
    #      remaining attempt in microseconds.
    #   3. On exhaustion it returned the URL ANYWAY. The test then failed at
    #      some later locator with "element not found", which reads like a UI
    #      bug and is not one. A test that dies here should say so here.
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_S
    last_error = "no attempt completed"
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{url}/api/health", timeout=5)
            if resp.status_code == 200:
                break
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(_SERVER_POLL_INTERVAL_S)
    else:
        raise RuntimeError(
            f"uvicorn did not answer {url}/api/health within "
            f"{_SERVER_START_TIMEOUT_S:.0f}s (last: {last_error}). Startup runs "
            f"run_preflight_checks(), which imports WeasyPrint/GTK and takes ~5s cold."
        )

    return url



@pytest.fixture(autouse=True)
def _isolate_real_course_data(tmp_path, monkeypatch):
    """Point ingest at a throwaway data dir. THE SUITE MUST NOT TOUCH REAL DATA.

    This test drives a REAL `/api/ingest`, and `ingest()` writes
    `course_map.json` and upserts into the Qdrant collection under
    `config.DATA_DIR`. With the default path that is the user's actual corpus.

    On 2026-09-01 it silently replaced 571 nodes from 14 real lecture decks with
    2 nodes from this test's 2-page synthetic PDF. Nothing failed: the suite went
    green, and the damage only surfaced later when an unrelated probe found the
    course map had two nodes in it. Rebuilding costs ~10 minutes of CPU.

    `ingest()` reads `config.DATA_DIR` at call time and the uvicorn server shares
    this process, so patching the module attribute redirects the server's writes
    too. Autouse, because remembering to opt in is exactly what failed here.
    """
    from coursegen import config as _config
    sandbox = tmp_path / "data"
    sandbox.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_config, "DATA_DIR", sandbox)
    monkeypatch.setattr(_config, "COURSE_MAP_PATH", sandbox / "course_map.json")
    yield


def test_the_e2e_fixture_isolates_real_data() -> None:
    """Guard on the guard: prove the redirect is in force, not merely declared."""
    from coursegen import config as _config
    assert "course_map.json" in str(_config.COURSE_MAP_PATH)
    assert _config.COURSE_MAP_PATH.parent != Path("data").resolve(), (
        "ingest would write to the real corpus"
    )


def test_browser_end_to_end_flow(page: Page, live_server_url: str, native_pdf: Path) -> None:
    """
    Browser E2E gate (E7.1): upload -> ingest -> generate -> download -> chat.
    This test verifies the frontend Vanilla JS UI coordinates correctly with the backend.

    **Scope, stated plainly so nobody reads more into a green run than it earns.**
    `_run_exam_pipeline` and `_run_chat_query` are MOCKED, so generation and chat
    are not exercised here — only the UI's handling of their responses. Ingest is
    real. This does NOT close P5's declared gate, which asks for the real flow;
    P5 stays PARTIAL until generation and chat run unmocked. What it does earn is
    the only coverage of the file a browser actually executes: a regex edit once
    left `index.html` with a SyntaxError while 385 other tests stayed green.

    "Download" below is asserted as *link present*, not as a file fetched.
    """
    page.set_default_timeout(_DEFAULT_ASSERTION_TIMEOUT_MS)
    # Mock the backend heavy lifters so the UI can run quickly without real models or API keys
    with patch("coursegen.app.main._run_exam_pipeline") as mock_generate, \
         patch("coursegen.app.main._run_chat_query") as mock_chat, \
         patch("coursegen.app.main.run_preflight_checks"):
        
        # 1. Setup mocks
        mock_generate.return_value = {
            "status": "ok",
            "items_count": 10,
            "fill_ratio": 1.0,
            "allocation_fidelity": 1.0,
            "downloads": {
                "exam_html": "/api/files/exam.html",
                "answer_key_html": "/api/files/answer_key.html",
                "coverage_html": "/api/files/coverage.html",
                "exam_pdf": "/api/files/exam.pdf",
                "answer_key_pdf": "/api/files/answer_key.pdf",
            },
            "warnings": []
        }
        
        mock_chat.return_value = {
            "answer": "TCP is a reliable protocol.",
            "citations": [{"file": "native.pdf", "page": 1}],
            "from_material": True
        }
        
        # Navigate to the app
        page.goto(live_server_url)
        
        # Accept disclosure
        page.locator("#btn-accept-disclosure").click()
        expect(page.locator("#main-workflow")).to_be_visible()
        
        # Upload & Ingest
        page.locator("#upload-files").set_input_files(str(native_pdf))
        page.locator("#btn-ingest").click()
        expect(page.locator("#ingest-status")).to_contain_text("Ingested", timeout=_INGEST_TIMEOUT_MS)
        
        # Generate Exam
        page.locator("#blueprint-select").select_option("quiz_default")
        page.locator("#exam-title").fill("My E2E Exam")
        page.locator("#btn-generate").click()
        
        # Wait for generate success
        expect(page.locator("#generate-status")).to_contain_text("Done", timeout=_DEFAULT_ASSERTION_TIMEOUT_MS)
        expect(page.locator("#dl-exam-html")).to_be_visible()
        
        # Chat
        page.locator(".tab[data-tab='chat']").click()
        expect(page.locator("#tab-chat")).to_be_visible()
        
        page.locator("#chat-input").fill("What is TCP?")
        page.locator("#btn-chat-send").click()
        
        # Wait for assistant response
        expect(page.locator(".chat-msg.assistant .text")).to_contain_text("TCP is a reliable protocol.")
        expect(page.locator(".chat-msg.assistant .citation")).to_contain_text("native.pdf")
