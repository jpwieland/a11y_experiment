"""
Gerador de relatórios JSON científicos com audit trail completo.

O relatório JSON contém toda a informação necessária para:
- Reproduzir exatamente os mesmos resultados
- Auditar cada decisão do sistema
- Comparar runs entre si
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from a11y_autofix.config import FixResult, ScanResult, Settings

log = structlog.get_logger(__name__)


class JSONReporter:
    """
    Gera relatório JSON com audit trail completo.

    Estrutura do relatório:
    - schema_version: versão do schema para compatibilidade futura
    - execution_id: UUID único da execução
    - timestamp: ISO 8601 UTC
    - environment: versões Python, OS, ferramentas
    - configuration: parâmetros usados
    - summary: métricas agregadas
    - files: resultados detalhados por arquivo
    """

    SCHEMA_VERSION = "2.0"
    PROTOCOL_VERSION = "1.0"

    def __init__(self, settings: Settings) -> None:
        """
        Args:
            settings: Configuração global do sistema.
        """
        self.settings = settings
        self._execution_id = str(uuid4())

    def generate(
        self,
        scan_results: list[ScanResult],
        fix_results: list[FixResult],
        output_dir: Path,
        wcag_level: str = "WCAG2AA",
        model_name: str = "unknown",
    ) -> Path:
        """
        Gera relatório JSON e salva em disco.

        Args:
            scan_results: Resultados de scan.
            fix_results: Resultados de correção.
            output_dir: Diretório de saída.
            wcag_level: Nível WCAG utilizado.
            model_name: Modelo LLM utilizado.

        Returns:
            Caminho do arquivo JSON gerado.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        report = self._build_report(scan_results, fix_results, wcag_level, model_name)

        output_path = output_dir / "report.json"
        import json
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        log.info("json_report_generated", path=str(output_path))
        return output_path

    def _build_report(
        self,
        scan_results: list[ScanResult],
        fix_results: list[FixResult],
        wcag_level: str,
        model_name: str,
    ) -> dict[str, Any]:
        """Constrói a estrutura completa do relatório."""
        fix_by_file = {str(r.file): r for r in fix_results}

        total_issues = sum(len(s.issues) for s in scan_results)
        total_fixed = sum(r.issues_fixed for r in fix_results)
        total_high_conf = sum(len(s.high_confidence_issues()) for s in scan_results)

        tool_versions: dict[str, str] = {}
        for s in scan_results:
            tool_versions.update(s.tool_versions)

        openhands_used = sum(
            1 for r in fix_results
            if r.best_attempt and r.best_attempt.agent == "openhands"
        )
        swe_used = sum(
            1 for r in fix_results
            if r.best_attempt and r.best_attempt.agent == "swe-agent"
        )
        total_time = sum(r.total_time for r in fix_results)

        return {
            "schema_version": self.SCHEMA_VERSION,
            "protocol_version": self.PROTOCOL_VERSION,
            "execution_id": self._execution_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "wcag_level": wcag_level,
            "environment": {
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "os": f"{platform.system()} {platform.release()}",
                "llm_model": model_name,
                "tool_versions": tool_versions,
            },
            "configuration": {
                "min_tool_consensus": self.settings.min_tool_consensus,
                "swe_max_issues": self.settings.swe_max_issues,
                "max_retries": self.settings.max_retries_per_agent,
            },
            "summary": {
                "total_files": len(scan_results),
                "files_with_issues": sum(1 for s in scan_results if s.has_issues),
                "total_issues": total_issues,
                "high_confidence_issues": total_high_conf,
                "issues_fixed": total_fixed,
                "issues_pending": total_issues - total_fixed,
                "success_rate": round(total_fixed / total_issues * 100, 1) if total_issues > 0 else 0.0,
                "openhands_used": openhands_used,
                "swe_agent_used": swe_used,
                "total_time_seconds": round(total_time, 2),
            },
            "files": [
                self._build_file_entry(scan, fix_by_file.get(str(scan.file)))
                for scan in scan_results
            ],
        }

    def _build_file_entry(
        self,
        scan: ScanResult,
        fix: FixResult | None,
    ) -> dict[str, Any]:
        """Constrói entrada de arquivo no relatório."""
        entry: dict[str, Any] = {
            "file": str(scan.file),
            "file_hash": scan.file_hash,
            "scan_time_seconds": round(scan.scan_time, 3),
            "tools_used": [t.value for t in scan.tools_used],
            "tool_versions": scan.tool_versions,
            "error": scan.error,
            "issues": [self._build_issue_entry(issue) for issue in scan.issues],
        }

        if fix:
            entry["fix"] = {
                "success": fix.final_success,
                "issues_fixed": fix.issues_fixed,
                "issues_pending": fix.issues_pending,
                "total_time_seconds": round(fix.total_time, 3),
                "attempts": [
                    {
                        "attempt_number": a.attempt_number,
                        "agent": a.agent,
                        "model": a.model,
                        "timestamp": a.timestamp.isoformat(),
                        "success": a.success,
                        "time_seconds": round(a.time_seconds, 3),
                        "tokens_used": a.tokens_used,
                        "diff": a.diff,
                        "error": a.error,
                    }
                    for a in fix.attempts
                ],
            }

        return entry

    def _build_issue_entry(self, issue: object) -> dict[str, Any]:
        """Constrói entrada de issue no relatório."""
        from a11y_autofix.config import A11yIssue
        if not isinstance(issue, A11yIssue):
            return {}
        return {
            "issue_id": issue.issue_id,
            "type": issue.issue_type.value,
            "wcag_criteria": issue.wcag_criteria,
            "complexity": issue.complexity.value,
            "confidence": issue.confidence.value,
            "tool_consensus": issue.tool_consensus,
            "found_by": [t.value for t in issue.found_by],
            "impact": issue.impact,
            "selector": issue.selector,
            "message": issue.message,
            "context": issue.context[:300] if issue.context else "",
            "resolved": issue.resolved,
            "findings": [
                {
                    "tool": f.tool.value,
                    "tool_version": f.tool_version,
                    "rule_id": f.rule_id,
                    "message": f.message,
                    "selector": f.selector,
                }
                for f in issue.findings
            ],
        }
