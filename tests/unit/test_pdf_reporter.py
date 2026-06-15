"""Testes do PDFReporter — foco na robustez a texto com marcação.

Regressão: mensagens de scanner ("Ensure <img> elements have alt text") e
seletores CSS ("div > button") contêm <, >, & que quebravam o parser de
Paragraph do reportlab e abortavam a geração do PDF (descoberto no scan Angular).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_autofix.reporter.pdf_reporter import _xesc

reportlab = pytest.importorskip("reportlab")

from a11y_autofix.reporter.pdf_reporter import PDFReporter  # noqa: E402


def _report_with_markup_text() -> dict:
    return {
        "schema_version": "2.0",
        "wcag_level": "WCAG2AA",
        "report_label": "desktop",
        "timestamp": "2026-06-14T00:00:00+00:00",
        "environment": {"llm_model": "n/a", "os": "test", "python_version": "3.11",
                        "tool_versions": {}},
        "configuration": {"min_tool_consensus": 2, "swe_max_issues": 4, "max_retries": 3},
        "summary": {
            "total_files": 1, "files_with_issues": 1, "total_issues": 2,
            "high_confidence_issues": 1, "issues_fixed": 0, "issues_pending": 2,
            "success_rate": 0.0, "openhands_used": 0, "swe_agent_used": 0,
            "total_time_seconds": 1.0,
        },
        "files": [{
            "file": "button.component.ts",
            "tools_used": ["axe-core", "pa11y"],
            "scan_mode": "desktop",
            "issues": [
                {  # mensagem com <img> e & — quebrava o reportlab
                    "type": "alt-text", "wcag_criteria": "1.1.1", "impact": "serious",
                    "confidence": "high",
                    "message": "Ensure <img> elements have alt text & a role",
                    "selector": "div.toolbar > img:nth-child(2)",
                    "found_by": ["axe-core"], "tool_consensus": 1,
                },
                {  # seletor com > e atributo
                    "type": "aria", "wcag_criteria": "4.1.2", "impact": "moderate",
                    "confidence": "low",
                    "message": "<button> has no accessible name",
                    "selector": "[aria-label] > svg", "found_by": ["pa11y"],
                    "tool_consensus": 1,
                },
            ],
        }],
    }


def test_xesc_escapes_xml_special_chars():
    assert _xesc("div > button & <img>") == "div &gt; button &amp; &lt;img&gt;"


def test_pdf_generates_with_markup_in_messages(tmp_path: Path):
    out = PDFReporter().generate(_report_with_markup_text(), tmp_path)
    assert out.exists(), "PDF não foi gerado para issues com <, >, & na mensagem"
    assert out.stat().st_size > 1000  # PDF real, não vazio
    assert b"%PDF" == out.read_bytes()[:4]
