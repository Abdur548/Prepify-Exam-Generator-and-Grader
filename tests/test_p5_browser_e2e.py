import threading
import time
import pytest
from unittest.mock import patch
import uvicorn
import requests
from playwright.sync_api import Page, expect
from pathlib import Path

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
    # Wait for server to start
    for _ in range(20):
        try:
            if requests.get(f"{url}/api/health").status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
            
    return url


def test_browser_end_to_end_flow(page: Page, live_server_url: str, native_pdf: Path) -> None:
    """
    Browser E2E gate (E7.1): upload -> ingest -> generate -> download -> chat.
    This test verifies the frontend Vanilla JS UI coordinates correctly with the backend.
    """
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
        expect(page.locator("#ingest-status")).to_contain_text("Ingested", timeout=60000)
        
        # Generate Exam
        page.locator("#blueprint-select").select_option("quiz_default")
        page.locator("#exam-title").fill("My E2E Exam")
        page.locator("#btn-generate").click()
        
        # Wait for generate success
        expect(page.locator("#generate-status")).to_contain_text("Done", timeout=60000)
        expect(page.locator("#dl-exam-html")).to_be_visible()
        
        # Chat
        page.locator(".tab[data-tab='chat']").click()
        expect(page.locator("#tab-chat")).to_be_visible()
        
        page.locator("#chat-input").fill("What is TCP?")
        page.locator("#btn-chat-send").click()
        
        # Wait for assistant response
        expect(page.locator(".chat-msg.assistant .text")).to_contain_text("TCP is a reliable protocol.")
        expect(page.locator(".chat-msg.assistant .citation")).to_contain_text("native.pdf")
