"""The static UI is a real surface, and until now nothing parsed it.

A regex edit to `index.html` on 2026-09-01 removed half of a `setStatus(...)`
call and left a dangling `+`. The whole suite stayed green — 385 passed — because
every test exercises the API through TestClient and nothing reads the file the
browser actually runs. The generate tab would have thrown a SyntaxError on load
and rendered nothing.

That is the same shape as the `show_progress_bar` defect: a path no test
executes, protected by a passing suite. These tests are deliberately cheap and
structural. They do not test behaviour — a browser end-to-end gate is still owed
for P5 — they only assert the file is not obviously broken.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "coursegen" / "app" / "static"
INDEX = STATIC_DIR / "index.html"


def _inline_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)


def test_index_html_exists() -> None:
    assert INDEX.is_file(), f"static UI missing at {INDEX}"


def test_inline_javascript_parses() -> None:
    """`node --check` on every inline <script>.

    Skipped rather than failed when node is absent: this guard is worth having
    wherever it can run, and not worth making the suite unrunnable where it
    cannot. A skip is visible; a silently-dropped check is not.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; cannot syntax-check inline JavaScript")

    scripts = _inline_scripts(INDEX.read_text(encoding="utf-8"))
    assert scripts, "no inline <script> found — did the UI move to an external file?"

    for i, js in enumerate(scripts):
        proc = subprocess.run(
            [node, "--check", "-"], input=js, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, (
            f"inline <script> #{i} is not valid JavaScript:\n{proc.stderr.strip()}"
        )


def test_ui_does_not_display_coverage_ratio() -> None:
    """coverage_ratio is disconnected from user-facing surfaces (2026-09-01).

    It measures matched nodes against the whole corpus, so under an authored
    blueprint it read 0.04 on a paper that was 20/20 items and 100/100 marks.
    Correct arithmetic, ruinous as a headline. It remains on CoverageReport and in
    the coverage.html audit view, where the denominator is visible.
    """
    html = INDEX.read_text(encoding="utf-8")
    assert "coverage_ratio" not in html, (
        "coverage_ratio is back on the UI; it reads ~4% on a complete paper. "
        "See the note in _run_exam_pipeline before restoring it."
    )


def test_ui_shows_the_honest_headline_metrics() -> None:
    """fill_ratio and items_count are what the paper is actually judged by."""
    html = INDEX.read_text(encoding="utf-8")
    for field in ("fill_ratio", "items_count"):
        assert field in html, f"UI no longer surfaces {field}"
