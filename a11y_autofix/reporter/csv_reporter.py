"""
Gerador de CSV com uma linha por issue de acessibilidade.

Pensado para triagem e resolução manual: cada linha traz criticidade, critério
WCAG (+ princípio), explicação, regra do scanner, seletor, contexto de código e
demais metadados úteis para corrigir o problema. Consome o mesmo `report_data`
do JSONReporter (estrutura do report.json).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Princípio WCAG pelo 1º dígito do critério (1.x.x → Perceivable, etc.)
_WCAG_PRINCIPLE = {
    "1": "P1 Perceivable",
    "2": "P2 Operable",
    "3": "P3 Understandable",
    "4": "P4 Robust",
}

# Ordem de criticidade para ordenar as linhas (mais grave primeiro).
_IMPACT_RANK = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

# Colunas do CSV (ordem estável). Cabeçalho legível em português.
_COLUMNS: list[tuple[str, str]] = [
    ("file", "arquivo"),
    ("scan_mode", "modo_scan"),
    ("impact", "criticidade"),
    ("confidence", "confianca"),
    ("tool_consensus", "consenso_ferramentas"),
    ("issue_type", "tipo"),
    ("wcag_criteria", "wcag"),
    ("wcag_principle", "wcag_principio"),
    ("complexity", "complexidade_correcao"),
    ("message", "explicacao"),
    ("rule_ids", "regras_scanner"),
    ("selector", "seletor"),
    ("context", "contexto_codigo"),
    ("found_by", "detectado_por"),
    ("screenshot_path", "screenshot"),
    ("issue_id", "issue_id"),
    ("resolved", "resolvido"),
]


class CSVReporter:
    """Escreve `issues.csv` (uma linha por issue) a partir do report_data."""

    def generate(
        self,
        report_data: dict[str, Any],
        output_dir: Path,
        filename: str = "issues.csv",
    ) -> Path:
        """
        Gera o CSV de issues e salva em disco.

        Args:
            report_data: Estrutura do report.json (JSONReporter).
            output_dir:  Diretório de saída.
            filename:    Nome do arquivo CSV.

        Returns:
            Caminho do CSV gerado.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        rows = self._collect_rows(report_data)

        # utf-8-sig (BOM) p/ acentos abrirem corretos no Excel; newline="" exigido pelo csv.
        with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
            writer.writerow([header for _, header in _COLUMNS])
            for row in rows:
                writer.writerow([row.get(key, "") for key, _ in _COLUMNS])

        log.info("csv_report_generated", path=str(output_path), issues=len(rows))
        return output_path

    def _collect_rows(self, report_data: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for file_entry in report_data.get("files", []):
            fname = Path(str(file_entry.get("file", ""))).name
            scan_mode = file_entry.get("scan_mode", "desktop")
            for issue in file_entry.get("issues", []):
                rows.append(self._issue_row(issue, fname, scan_mode))

        # Ordena por criticidade → confiança → consenso (decrescente) para
        # que as linhas mais urgentes apareçam primeiro.
        rows.sort(key=lambda r: (
            _IMPACT_RANK.get(str(r.get("impact", "")).lower(), 9),
            _CONFIDENCE_RANK.get(str(r.get("confidence", "")).lower(), 9),
            -int(r.get("tool_consensus") or 0),
        ))
        return rows

    @staticmethod
    def _issue_row(issue: dict[str, Any], fname: str, scan_mode: str) -> dict[str, Any]:
        wcag = issue.get("wcag_criteria") or ""
        principle = _WCAG_PRINCIPLE.get(wcag[:1], "") if wcag else ""

        # Regras dos scanners (ex.: "image-alt", "button-name") — essenciais p/
        # saber exatamente o que cada ferramenta apontou.
        rule_ids = sorted({
            f.get("rule_id", "") for f in issue.get("findings", []) if f.get("rule_id")
        })

        context = (issue.get("context") or "").replace("\r", " ").replace("\n", " ").strip()

        return {
            "file": fname,
            "scan_mode": scan_mode,
            "impact": issue.get("impact", ""),
            "confidence": issue.get("confidence", ""),
            "tool_consensus": issue.get("tool_consensus", ""),
            "issue_type": issue.get("type", ""),
            "wcag_criteria": wcag,
            "wcag_principle": principle,
            "complexity": issue.get("complexity", ""),
            "message": (issue.get("message") or "").replace("\n", " ").strip(),
            "rule_ids": "; ".join(rule_ids),
            "selector": issue.get("selector", ""),
            "context": context[:500],
            "found_by": ", ".join(issue.get("found_by", [])),
            "screenshot_path": issue.get("screenshot_path") or "",
            "issue_id": issue.get("issue_id", ""),
            "resolved": "sim" if issue.get("resolved") else "nao",
        }
