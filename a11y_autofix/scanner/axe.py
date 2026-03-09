"""Runner para axe-core CLI."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

_PROCESS_TIMEOUT_S = 90


class AxeRunner(BaseRunner):
    """
    Runner para axe-core CLI (https://github.com/dequelabs/axe-core-npm).

    Usa @axe-core/cli via npx para escanear páginas e mapeia resultados
    para o formato interno ToolFinding.

    Quando harness_url é fornecida (URL HTTP local), usa-a diretamente
    em vez de file:// para evitar problemas de CDN e timeouts.
    """

    tool = ScanTool.AXE

    async def available(self) -> bool:
        """Verifica se axe-core CLI está disponível via npx."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--yes", "@axe-core/cli", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    async def version(self) -> str:
        """Retorna versão do axe-core CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--yes", "@axe-core/cli", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
            if proc.returncode == 0:
                return stdout.decode().strip()
        except (FileNotFoundError, asyncio.TimeoutError):
            pass
        return "unknown"

    async def run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
    ) -> list[ToolFinding]:
        """
        Executa axe-core CLI no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (ex: 'WCAG2AA').
            harness_url: URL HTTP para acessar o harness (preferido).

        Returns:
            Lista de ToolFinding.
        """
        version = await self.version()

        # Preferir URL HTTP local; fallback para file://
        url = harness_url or f"file://{harness_path.resolve()}"
        log.debug("axe_scanning", url=url[:80])

        tags = self._wcag_to_axe_tags(wcag)

        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "@axe-core/cli",
            url,
            "--stdout",
            "--tags", ",".join(tags),
            "--chromium-args='--no-sandbox --disable-dev-shm-usage --disable-gpu "
            "--disable-web-security --allow-file-access-from-files'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_PROCESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            log.warning("axe_process_timeout", timeout_s=_PROCESS_TIMEOUT_S)
            return []

        output = stdout.decode(errors="replace")
        if not output.strip():
            log.debug("axe_empty_output", stderr=stderr.decode(errors="replace")[:200])
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            log.warning("axe_json_parse_error", output=output[:300])
            return []

        findings: list[ToolFinding] = []
        # Estrutura axe: array de resultados por URL
        results = data if isinstance(data, list) else [data]

        for result in results:
            violations = result.get("violations", []) if isinstance(result, dict) else []
            for violation in violations:
                if not isinstance(violation, dict):
                    continue

                rule_id = violation.get("id", "unknown")
                impact = violation.get("impact", "moderate")
                description = violation.get("description", "")
                help_url = violation.get("helpUrl", "")
                wcag_criteria = self._extract_wcag_from_tags(violation.get("tags", []))

                for node in violation.get("nodes", []):
                    target = node.get("target", [])
                    selector = ""
                    if target:
                        last = target[-1]
                        selector = last if isinstance(last, str) else str(last)

                    finding = ToolFinding(
                        tool=self.tool,
                        tool_version=version,
                        rule_id=rule_id,
                        wcag_criteria=wcag_criteria,
                        message=node.get("failureSummary", description),
                        selector=selector,
                        context=node.get("html", "")[:500],
                        impact=impact or "moderate",
                        help_url=help_url,
                    )
                    findings.append(finding)

        log.debug("axe_findings", count=len(findings))
        return findings

    def _wcag_to_axe_tags(self, wcag: str) -> list[str]:
        """Converte nível WCAG para tags do axe-core."""
        mapping = {
            "WCAG2A": ["wcag2a", "wcag21a", "wcag22a"],
            "WCAG2AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
            "WCAG2AAA": [
                "wcag2a", "wcag2aa", "wcag2aaa",
                "wcag21a", "wcag21aa", "wcag21aaa",
                "wcag22aa",
            ],
        }
        return mapping.get(wcag, ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])

    def _extract_wcag_from_tags(self, tags: list[str]) -> str | None:
        """Extrai critério WCAG das tags do axe-core. Ex: 'wcag143' → '1.4.3'."""
        for tag in tags:
            if not isinstance(tag, str):
                continue
            match = re.match(r"wcag(\d)(\d)(\d)$", tag)
            if match:
                return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            match2 = re.match(r"wcag(\d)(\d{1,2})$", tag)
            if match2:
                return f"{match2.group(1)}.{match2.group(2)}"
        return None
