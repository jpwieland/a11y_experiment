"""Testes do CSVReporter — uma linha por issue com metadados de resolução."""

from __future__ import annotations

import csv
from pathlib import Path

from a11y_autofix.reporter.csv_reporter import CSVReporter


def _report() -> dict:
    return {
        "files": [{
            "file": "src/Button.tsx",
            "scan_mode": "desktop",
            "issues": [
                {  # moderado, deve vir DEPOIS do serious
                    "issue_id": "id-mod", "type": "other", "wcag_criteria": "2.5.5",
                    "impact": "moderate", "confidence": "low", "tool_consensus": 1,
                    "complexity": "moderate", "message": "Touch target too small",
                    "selector": "button", "context": "<button>x</button>",
                    "found_by": ["lighthouse"],
                    "findings": [{"rule_id": "target-size"}],
                },
                {  # serious com vírgula/quote na mensagem e > no seletor
                    "issue_id": "id-ser", "type": "aria", "wcag_criteria": "4.1.2",
                    "impact": "serious", "confidence": "high", "tool_consensus": 2,
                    "complexity": "simple",
                    "message": 'Button has no name, screen reader says "button"',
                    "selector": "div > button", "context": "<button/>",
                    "found_by": ["axe-core", "pa11y"],
                    "findings": [{"rule_id": "button-name"}, {"rule_id": "H91"}],
                },
            ],
        }],
    }


def test_csv_has_expected_columns_and_rows(tmp_path: Path):
    out = CSVReporter().generate(_report(), tmp_path)
    assert out.name == "issues.csv" and out.exists()
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    for col in ("criticidade", "wcag", "wcag_principio", "explicacao",
                "regras_scanner", "seletor", "contexto_codigo", "complexidade_correcao"):
        assert col in rows[0]


def test_csv_sorted_by_criticality_and_metadata(tmp_path: Path):
    out = CSVReporter().generate(_report(), tmp_path)
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # serious antes de moderate
    assert rows[0]["criticidade"] == "serious"
    assert rows[1]["criticidade"] == "moderate"
    # WCAG → princípio derivado do 1º dígito
    assert rows[0]["wcag_principio"] == "P4 Robust"
    # rule_ids agregados das findings
    assert "button-name" in rows[0]["regras_scanner"]
    # mensagem com vírgula/aspas e seletor com '>' preservados (round-trip do csv)
    assert "screen reader" in rows[0]["explicacao"]
    assert ">" in rows[0]["seletor"]
