"""Runner para Google Lighthouse."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# Mapeamento de audits Lighthouse → critérios WCAG
_AUDIT_TO_WCAG: dict[str, str] = {
    "color-contrast": "1.4.3",
    "image-alt": "1.1.1",
    "button-name": "4.1.2",
    "link-name": "4.1.2",
    "label": "1.3.1",
    "aria-required-attr": "4.1.2",
    "aria-valid-attr": "4.1.2",
    "aria-valid-attr-value": "4.1.2",
    "document-title": "2.4.2",
    "html-has-lang": "3.1.1",
    "frame-title": "4.1.2",
    "duplicate-id": "4.1.1",
    "tabindex": "2.4.3",
    "focus-traps": "2.1.2",
    "heading-order": "1.3.1",
    "list": "1.3.1",
    "listitem": "1.3.1",
    "definition-list": "1.3.1",
    "dlitem": "1.3.1",
    "input-image-alt": "1.1.1",
    "object-alt": "1.1.1",
    "video-caption": "1.2.2",
    "audio-caption": "1.2.1",
    "meta-viewport": "1.4.4",
    "aria-hidden-body": "4.1.2",
    "aria-hidden-focus": "4.1.2",
    "aria-input-field-name": "4.1.2",
    "aria-toggle-field-name": "4.1.2",
    "landmark-one-main": "1.3.6",
    "bypass": "2.4.1",
    "skip-link": "2.4.1",
}


class LighthouseRunner(BaseRunner):
    """
    Runner para Google Lighthouse (https://developer.chrome.com/docs/lighthouse/).

    Foca na categoria 'accessibility' do Lighthouse para extrair findings
    compatíveis com WCAG.
    """

    tool = ScanTool.LIGHTHOUSE

    async def available(self) -> bool:
        """Verifica se Lighthouse CLI está disponível."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "lighthouse", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    async def version(self) -> str:
        """Retorna versão do Lighthouse."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "lighthouse", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode().strip()
        except FileNotFoundError:
            pass
        return "unknown"

    async def run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa Lighthouse na categoria accessibility.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (usado para filtrar resultados).

        Returns:
            Lista de ToolFinding.
        """
        version = await self.version()
        url = f"file://{harness_path.resolve()}"

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = Path(tmp.name)

        proc = await asyncio.create_subprocess_exec(
            "lighthouse",
            url,
            "--only-categories=accessibility",
            "--output=json",
            f"--output-path={output_path}",
            "--quiet",
            "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            output_path.unlink(missing_ok=True)
            raise RuntimeError("Lighthouse timeout after 90s")

        try:
            raw = output_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            log.warning("lighthouse_parse_error", error=str(e))
            output_path.unlink(missing_ok=True)
            return []
        finally:
            output_path.unlink(missing_ok=True)

        findings: list[ToolFinding] = []
        all_audits: dict[str, object] = data.get("audits", {})  # type: ignore[assignment]
        audit_refs = (
            data.get("categories", {})
            .get("accessibility", {})
            .get("auditRefs", [])
        )

        for audit_ref in audit_refs:
            audit_id = audit_ref.get("id", "")
            audit = all_audits.get(audit_id, {})
            if not isinstance(audit, dict):
                continue

            # Pular audits que passaram ou não são aplicáveis
            if audit.get("score") in (1, None):
                continue

            details = audit.get("details", {})
            items = details.get("items", []) if isinstance(details, dict) else []

            if not items:
                # Criar finding genérico mesmo sem items
                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=audit_id,
                    wcag_criteria=_AUDIT_TO_WCAG.get(audit_id),
                    message=audit.get("description", ""),
                    selector="",
                    context="",
                    impact=self._score_to_impact(audit.get("score")),
                    help_url=f"https://web.dev/{audit_id}/",
                )
                findings.append(finding)
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                node = item.get("node", {}) if isinstance(item.get("node"), dict) else {}
                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=audit_id,
                    wcag_criteria=_AUDIT_TO_WCAG.get(audit_id),
                    message=audit.get("description", ""),
                    selector=node.get("selector", ""),
                    context=node.get("snippet", ""),
                    impact=self._score_to_impact(audit.get("score")),
                    help_url=f"https://web.dev/{audit_id}/",
                )
                findings.append(finding)

        log.debug("lighthouse_findings", count=len(findings))
        return findings

    def _score_to_impact(self, score: object) -> str:
        """Converte score Lighthouse (0–1) para impacto."""
        if score is None or score == 0:
            return "critical"
        s = float(score)  # type: ignore[arg-type]
        if s < 0.5:
            return "serious"
        if s < 0.9:
            return "moderate"
        return "minor"
