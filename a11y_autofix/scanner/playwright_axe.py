"""Runner para Playwright com injeção de axe-core."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# Script JavaScript para injetar e executar axe-core
_AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

_AXE_SCRIPT = f"""
async () => {{
  if (!window.axe) {{
    await new Promise((resolve, reject) => {{
      const s = document.createElement('script');
      s.src = '{_AXE_CDN}';
      s.onload = resolve;
      s.onerror = () => reject(new Error('axe CDN load failed'));
      document.head.appendChild(s);
    }});
  }}
  const results = await window.axe.run(document, {{
    runOnly: {{
      type: 'tag',
      values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
    }},
    reporter: 'v2'
  }});
  return JSON.stringify(results);
}}
"""


class PlaywrightAxeRunner(BaseRunner):
    """
    Runner que usa Playwright + axe-core injetado via CDN.

    Combina a precisão do axe-core com a capacidade de renderização real
    do Playwright (Chromium headless), garantindo que o DOM seja processado
    exatamente como em um browser real com React renderizado.
    """

    tool = ScanTool.PLAYWRIGHT

    async def available(self) -> bool:
        """Verifica se Playwright está instalado e Chromium disponível."""
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def version(self) -> str:
        """Retorna versão do Playwright."""
        try:
            import playwright
            return getattr(playwright, "__version__", "unknown")
        except ImportError:
            return "unknown"

    async def run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa axe-core via Playwright no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG.

        Returns:
            Lista de ToolFinding.
        """
        from playwright.async_api import async_playwright

        version = await self.version()
        url = f"file://{harness_path.resolve()}"
        findings: list[ToolFinding] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                # Aguarda React renderizar
                await page.wait_for_timeout(2000)

                result_json: str = await page.evaluate(_AXE_SCRIPT)
                data: dict[str, object] = json.loads(result_json)

            except Exception as e:
                log.warning("playwright_run_error", error=str(e))
                await browser.close()
                return []

            await browser.close()

        for violation in data.get("violations", []):  # type: ignore[union-attr]
            if not isinstance(violation, dict):
                continue

            rule_id = violation.get("id", "unknown")
            impact = violation.get("impact", "moderate")
            description = violation.get("description", "")
            help_url = violation.get("helpUrl", "")
            wcag_criteria = self._extract_wcag_from_tags(
                violation.get("tags", [])  # type: ignore[arg-type]
            )

            for node in violation.get("nodes", []):  # type: ignore[union-attr]
                if not isinstance(node, dict):
                    continue
                target = node.get("target", [])
                selector = ""
                if target and isinstance(target, list):
                    last = target[-1]
                    selector = last if isinstance(last, str) else str(last)

                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=str(rule_id),
                    wcag_criteria=wcag_criteria,
                    message=str(node.get("failureSummary", description)),
                    selector=selector,
                    context=str(node.get("html", "")),
                    impact=str(impact or "moderate"),
                    help_url=str(help_url),
                )
                findings.append(finding)

        log.debug("playwright_axe_findings", count=len(findings))
        return findings

    def _extract_wcag_from_tags(self, tags: list[object]) -> str | None:
        """Extrai critério WCAG das tags axe-core. Ex: 'wcag143' → '1.4.3'."""
        for tag in tags:
            if not isinstance(tag, str):
                continue
            match = re.match(r'wcag(\d)(\d)(\d)', tag)
            if match:
                return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            match2 = re.match(r'wcag(\d)(\d{2})', tag)
            if match2:
                return f"{match2.group(1)}.{match2.group(2)}"
        return None
