"""Render generated exams, answer keys and coverage tables.

Jinja2 autoescape is mandatory: model output is untrusted (S5).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jinja2 import Environment, select_autoescape

from coursegen.contracts.coverage import CoverageReport
from coursegen.contracts.item import GeneratedItem

PdfWriter = Callable[[str, Path], None]


@dataclass(frozen=True)
class RenderArtifacts:
    exam_html: Path
    answer_key_html: Path
    coverage_html: Path
    exam_pdf: Path
    answer_key_pdf: Path


def check_weasyprint_available() -> None:
    try:
        import weasyprint  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "WeasyPrint is installed but its native GTK/Pango runtime is unavailable. "
            "Install the WeasyPrint Windows prerequisites before PDF generation."
        ) from exc


def render_exam_artifacts(
    items: list[GeneratedItem],
    coverage_report: CoverageReport,
    output_dir: Path,
    title: str,
    pdf_writer: PdfWriter | None = None,
) -> RenderArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_writer = pdf_writer or _write_pdf_with_weasyprint

    exam_html = output_dir / "exam.html"
    key_html = output_dir / "answer_key.html"
    coverage_html = output_dir / "coverage.html"
    exam_pdf = output_dir / "exam.pdf"
    key_pdf = output_dir / "answer_key.pdf"

    exam_html_text = _render_exam_html(items, title)
    key_html_text = _render_answer_key_html(items, title)
    coverage_html_text = _render_coverage_html(coverage_report, title)

    exam_html.write_text(exam_html_text, encoding="utf-8")
    key_html.write_text(key_html_text, encoding="utf-8")
    coverage_html.write_text(coverage_html_text, encoding="utf-8")

    pdf_writer(exam_html_text, exam_pdf)
    pdf_writer(key_html_text, key_pdf)

    return RenderArtifacts(
        exam_html=exam_html,
        answer_key_html=key_html,
        coverage_html=coverage_html,
        exam_pdf=exam_pdf,
        answer_key_pdf=key_pdf,
    )


_ENV = Environment(autoescape=select_autoescape(["html", "xml"]))


_EXAM_TEMPLATE = _ENV.from_string("""
<!doctype html>
<html><head><meta charset="utf-8"><title>{{ title }}</title>
<style>@media print { body { font-family: serif; } .question { break-inside: avoid; } }</style>
</head><body>
<h1>{{ title }}</h1>
{% for item in items %}
<section class="question">
<h2>{{ item.slot_id }}</h2>
<p>{{ item.stem }}</p>
{% if item.options %}
<ol class="options">
{% for option in item.options %}<li><strong>{{ option.label }}.</strong> {{ option.text }}</li>{% endfor %}
</ol>
{% endif %}
<p class="source">Source: {{ item.source_ref.file }}, pages {{ item.source_ref.pages|join(', ') }}</p>
</section>
{% endfor %}
</body></html>
""")

_KEY_TEMPLATE = _ENV.from_string("""
<!doctype html>
<html><head><meta charset="utf-8"><title>{{ title }} Answer Key</title></head><body>
<h1>{{ title }} Answer Key</h1>
{% for item in items %}
<section>
<h2>{{ item.slot_id }}</h2>
{% if item.correct_option %}<p>Correct option: {{ item.correct_option }}</p>{% endif %}
<p>{{ item.model_answer }}</p>
<p>{{ item.explanation }}</p>
<p>Source: {{ item.source_ref.file }}, pages {{ item.source_ref.pages|join(', ') }}</p>
</section>
{% endfor %}
</body></html>
""")

_COVERAGE_TEMPLATE = _ENV.from_string("""
<!doctype html>
<html><head><meta charset="utf-8"><title>{{ title }} Coverage</title></head><body>
<h1>{{ title }} Coverage</h1>
<table>
<tr><th>coverage_ratio</th><th>fill_ratio</th><th>allocation_fidelity</th><th>slots_by_fallthrough</th></tr>
<tr><td>{{ report.coverage_ratio }}</td><td>{{ report.fill_ratio }}</td><td>{{ report.allocation_fidelity }}</td><td>{{ report.slots_by_fallthrough }}</td></tr>
</table>
<h2>Per-node allocation</h2>
<table>
<tr><th>Path</th><th>Mass</th><th>Marks</th><th>Slots</th></tr>
{% for node in report.per_node %}
<tr><td>{{ node.path|join(' > ') }}</td><td>{{ node.instructional_mass }}</td><td>{{ node.marks_allocated }}</td><td>{{ node.slots|join(', ') }}</td></tr>
{% endfor %}
</table>
<h2>Unfilled slots</h2>
<ul>{% for slot in report.unfilled_slots %}<li>{{ slot }}</li>{% endfor %}</ul>
<h2>Warnings</h2>
<ul>{% for warning in report.warnings %}<li>{{ warning }}</li>{% endfor %}</ul>
</body></html>
""")


def _render_exam_html(items: list[GeneratedItem], title: str) -> str:
    return _EXAM_TEMPLATE.render(items=items, title=title)


def _render_answer_key_html(items: list[GeneratedItem], title: str) -> str:
    return _KEY_TEMPLATE.render(items=items, title=title)


def _render_coverage_html(report: CoverageReport, title: str) -> str:
    return _COVERAGE_TEMPLATE.render(report=report, title=title)


def _write_pdf_with_weasyprint(html: str, path: Path) -> None:
    check_weasyprint_available()
    from weasyprint import HTML

    HTML(string=html).write_pdf(str(path))
