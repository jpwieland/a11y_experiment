"""Runner para Playwright com injeção de axe-core.

Estratégia de robustez:
1. Serve o harness via HTTP local (sem file://) → CDN carrega sem restrições
2. Usa wait_until='domcontentloaded' → retorna rápido, sem aguardar CDN
3. Depois, espera window.React estar disponível (com timeout gracioso)
4. Injeta axe-core a partir do npm local — sem dependência de rede para axe
5. Fallback para CDN se axe-core não estiver instalado localmente
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# URL CDN de fallback para axe-core (usado só se npm local não encontrar)
_AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

# Timeouts
_GOTO_TIMEOUT_MS = 60_000        # 60s para navegação inicial
_REACT_WAIT_TIMEOUT_MS = 25_000  # 25s esperando React carregar via CDN
_AXE_RUN_TIMEOUT_MS = 30_000     # 30s para execução do axe-core
_RENDER_WAIT_MS = 600            # 600ms após React carregar (renderização)

# Script para executar axe-core e retornar resultados como JSON
_AXE_RUN_SCRIPT = """
async () => {
  const results = await window.axe.run(document, {
    runOnly: {
      type: 'tag',
      values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice']
    },
    reporter: 'v2',
    resultTypes: ['violations', 'incomplete']
  });
  return JSON.stringify({
    violations: results.violations || [],
    incomplete: results.incomplete || []
  });
}
"""

# Candidatos para encontrar axe-core instalado via npm
_AXE_NPM_SUBPATHS = [
    "axe-core/axe.min.js",
    "axe-core/axe.js",
    "@axe-core/cli/node_modules/axe-core/axe.min.js",
]


class PlaywrightAxeRunner(BaseRunner):
    """
    Runner que usa Playwright + axe-core para análise profunda de acessibilidade.

    Carrega o componente React no Chromium headless via HTTP local,
    injeta axe-core (preferencialmente do npm local) e executa análise completa
    incluindo violações WCAG 2.1/2.2 e melhores práticas.
    """

    tool = ScanTool.PLAYWRIGHT

    def __init__(self) -> None:
        # Cache para localização do axe-core
        self._local_axe_path: Path | None = None
        self._axe_resolved = False
        # Cache para versão
        self._playwright_version: str | None = None

    async def _find_local_axe(self) -> Path | None:
        """
        Busca axe-core nos módulos npm globais.

        Evita dependência de CDN durante a análise, tornando o sistema
        robusto para ambientes sem internet ou com CDN lento.
        """
        if self._axe_resolved:
            return self._local_axe_path
        self._axe_resolved = True

        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "root", "-g",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return None

            npm_root = Path(stdout.decode().strip())
            for subpath in _AXE_NPM_SUBPATHS:
                candidate = npm_root / subpath
                if candidate.exists():
                    log.debug("playwright_axe_local_found", path=str(candidate))
                    self._local_axe_path = candidate
                    return candidate

        except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
            log.debug("playwright_axe_local_search_failed", error=str(e))

        log.debug("playwright_axe_using_cdn_fallback")
        return None

    async def available(self) -> bool:
        """Verifica se Playwright está instalado com Chromium disponível."""
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def version(self) -> str:
        """Retorna versão do Playwright."""
        if self._playwright_version is not None:
            return self._playwright_version
        try:
            import playwright
            ver = getattr(playwright, "__version__", "unknown")
            self._playwright_version = ver
            return ver
        except ImportError:
            return "unknown"

    async def run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
    ) -> list[ToolFinding]:
        """
        Executa axe-core via Playwright no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (ex: 'WCAG2AA').
            harness_url: URL HTTP para acessar o harness (preferido sobre file://).
                         Fornecida pelo orquestrador via HarnessServer.

        Returns:
            Lista de ToolFinding com violações encontradas.
        """
        from playwright.async_api import async_playwright
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        version = await self.version()

        # Preferir URL HTTP local; fallback para file://
        url = harness_url or f"file://{harness_path.resolve()}"
        log.debug("playwright_navigating", url=url[:80])

        # Buscar axe-core local antes de abrir o browser (async, com cache)
        local_axe = await self._find_local_axe()

        findings: list[ToolFinding] = []

        async with async_playwright() as p:
            # Chrome flags:
            # --no-sandbox: necessário em ambientes Linux/Docker
            # --disable-dev-shm-usage: evita crashes em /dev/shm pequeno
            # --disable-gpu: headless sem GPU
            # --disable-web-security: permite scripts CDN de file:// se necessário
            # --allow-file-access-from-files: permite leitura de file:// locais
            browser = await p.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--allow-file-access-from-files",
                    "--disable-extensions",
                    "--disable-background-timer-throttling",
                ]
            )

            try:
                context = await browser.new_context(
                    # Viewport padrão desktop para análise realista
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                # Silenciar erros de console esperados (React, Babel, etc.)
                page.on("console", lambda _: None)
                page.on("pageerror", lambda _: None)

                # ── Passo 1: Navegar para o harness ─────────────────────────
                # domcontentloaded: retorna assim que o HTML é parseado,
                # sem esperar scripts CDN externos (que podem demorar)
                try:
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=_GOTO_TIMEOUT_MS,
                    )
                except PlaywrightTimeout:
                    log.warning("playwright_goto_timeout", url=url[:80])
                    await browser.close()
                    return []
                except Exception as e:
                    log.warning("playwright_goto_error", error=str(e)[:200])
                    await browser.close()
                    return []

                # ── Passo 2: Aguardar React/Babel carregarem ─────────────────
                # Quando servido via HTTP local, os scripts CDN carregam
                # normalmente. Aguardamos window.React estar definido.
                react_loaded = False
                try:
                    await page.wait_for_function(
                        "(typeof window.React !== 'undefined') && "
                        "(typeof window.ReactDOM !== 'undefined')",
                        timeout=_REACT_WAIT_TIMEOUT_MS,
                    )
                    react_loaded = True
                    log.debug("playwright_react_loaded")
                    # Aguardar renderização do componente (Babel precisa processar JSX)
                    await page.wait_for_timeout(_RENDER_WAIT_MS)
                except PlaywrightTimeout:
                    log.debug(
                        "playwright_react_not_loaded",
                        hint="Continuando com DOM estático — "
                             "axe ainda detecta issues estruturais",
                    )

                log.debug("playwright_react_status", loaded=react_loaded)

                # ── Passo 3: Injetar axe-core ────────────────────────────────
                try:
                    if local_axe and local_axe.exists():
                        # Injeção local: sem dependência de rede, mais rápida
                        await page.add_script_tag(path=str(local_axe))
                        log.debug("playwright_axe_injected", source="local_npm")
                    else:
                        # Fallback CDN (requer internet)
                        await page.add_script_tag(url=_AXE_CDN)
                        log.debug("playwright_axe_injected", source="cdn")
                except Exception as e:
                    log.warning("playwright_axe_inject_failed", error=str(e)[:200])
                    await browser.close()
                    return []

                # ── Passo 4: Executar análise axe-core ──────────────────────
                # page.evaluate() não aceita timeout= como kwarg no Playwright Python.
                # Usamos set_default_timeout() para configurar o timeout da página
                # antes da chamada; PlaywrightTimeout será levantado se excedido.
                try:
                    page.set_default_timeout(_AXE_RUN_TIMEOUT_MS)
                    result_json: str = await page.evaluate(_AXE_RUN_SCRIPT)
                    data: dict = json.loads(result_json)
                except PlaywrightTimeout:
                    log.warning("playwright_axe_run_timeout")
                    await browser.close()
                    return []
                except Exception as e:
                    log.warning("playwright_axe_run_error", error=str(e)[:200])
                    await browser.close()
                    return []

            except Exception as e:
                log.warning("playwright_run_error", error=str(e)[:200])
                await browser.close()
                return []

            await browser.close()

        # ── Passo 5: Converter resultados para ToolFinding ───────────────────
        all_violations = list(data.get("violations", []))
        # Incluir 'incomplete' como potential findings (moderado)
        for item in data.get("incomplete", []):
            if isinstance(item, dict):
                # Marcar como potencial (não confirmado)
                item = {**item, "_incomplete": True}
                # Só inclui incomplete se tem nodes específicos
                if item.get("nodes"):
                    all_violations.append(item)

        for violation in all_violations:
            if not isinstance(violation, dict):
                continue

            rule_id = violation.get("id", "unknown")
            is_incomplete = violation.get("_incomplete", False)
            # incomplete → impacto menor (moderate)
            impact = "moderate" if is_incomplete else (violation.get("impact") or "moderate")
            description = violation.get("description", "")
            help_url = violation.get("helpUrl", "")
            wcag_criteria = self._extract_wcag_from_tags(
                violation.get("tags", [])
            )

            for node in violation.get("nodes", []):
                if not isinstance(node, dict):
                    continue

                target = node.get("target", [])
                selector = ""
                if target and isinstance(target, list):
                    last = target[-1]
                    selector = last if isinstance(last, str) else str(last)

                failure = node.get("failureSummary", description)
                if is_incomplete:
                    failure = f"[Potential] {failure}"

                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=str(rule_id),
                    wcag_criteria=wcag_criteria,
                    message=str(failure),
                    selector=selector,
                    context=str(node.get("html", ""))[:500],
                    impact=str(impact),
                    help_url=str(help_url),
                )
                findings.append(finding)

        log.debug(
            "playwright_axe_findings",
            count=len(findings),
            react_loaded=react_loaded,
        )
        return findings

    def _extract_wcag_from_tags(self, tags: list[object]) -> str | None:
        """Extrai critério WCAG das tags axe-core. Ex: 'wcag143' → '1.4.3'."""
        for tag in tags:
            if not isinstance(tag, str):
                continue
            # Padrão de 3 dígitos: wcag143 → 1.4.3
            match = re.match(r"wcag(\d)(\d)(\d)$", tag)
            if match:
                return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            # Padrão de 2 dígitos: wcag21 → 2.1
            match2 = re.match(r"wcag(\d)(\d{1,2})$", tag)
            if match2:
                return f"{match2.group(1)}.{match2.group(2)}"
        return None
