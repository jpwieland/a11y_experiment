"""Orquestrador multi-ferramenta: executa todos os runners em paralelo."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from a11y_autofix.config import ScanMode, ScanResult, ScanTool, Settings, ToolFinding
from a11y_autofix.scanner.axe import AxeRunner
from a11y_autofix.scanner.base import BaseRunner
from a11y_autofix.scanner.eslint import EslintRunner
from a11y_autofix.scanner.lighthouse import LighthouseRunner
from a11y_autofix.scanner.pa11y import Pa11yRunner
from a11y_autofix.scanner.playwright_axe import PlaywrightAxeRunner

# Ordem de preferência para captura de screenshot
# Playwright primeiro: é o único runner com captura estilo Lighthouse
# (full-page com destaques + crop por elemento violador). Lighthouse
# fica como fallback (screenshot simples, sem crops por elemento).
_SCREENSHOT_PRIORITY = (PlaywrightAxeRunner, LighthouseRunner)


def _build_runner_kwargs(
    runner: "BaseRunner",
    framework: str,
    screenshot_path: "Path | None",
    screenshot_harness_url: "str | None",
    out_meta: "dict | None" = None,
) -> dict:
    """
    Monta kwargs extras para safe_run() dependendo do tipo de runner.

    Centraliza a lógica de quais parâmetros cada runner aceita para
    evitar repetição no loop do orchestrator.
    """
    if isinstance(runner, PlaywrightAxeRunner):
        kwargs: dict = {"framework": framework}
        if screenshot_path:
            kwargs["screenshot_path"] = screenshot_path
        if screenshot_harness_url:
            kwargs["screenshot_harness_url"] = screenshot_harness_url
        if out_meta is not None:
            kwargs["out_meta"] = out_meta
        return kwargs

    if isinstance(runner, LighthouseRunner):
        kwargs = {}
        if screenshot_path:
            kwargs["screenshot_path"] = screenshot_path
        return kwargs

    return {}
from a11y_autofix.utils.angular_template import detect_framework, extract_angular_template
from a11y_autofix.utils.files import (
    build_angular_harness,
    build_angular_screenshot_harness,
    build_html_harness,
    extract_project_css_vars,
    find_angular_assets_dir,
    find_angular_project_root,
    load_bootstrap_utilities,
    load_component_css,
)
from a11y_autofix.utils.http_server import HarnessServer

log = structlog.get_logger(__name__)


@dataclass
class MultiToolScanResult:
    """
    Extended scan result that includes both the consensus output and
    the raw per-scanner findings before deduplication.

    Used to support individual-scanner baseline comparison
    (methodology Section 3.7.3 — Baselines).
    """

    consensus: ScanResult
    """Deduplicated, post-consensus scan result (the primary artefact)."""

    raw_pa11y: list[ToolFinding] = field(default_factory=list)
    """Raw findings from Pa11y before deduplication."""

    raw_axe_core: list[ToolFinding] = field(default_factory=list)
    """Raw findings from axe-core before deduplication."""

    raw_playwright_axe: list[ToolFinding] = field(default_factory=list)
    """Raw findings from Playwright+axe before deduplication."""

    raw_lighthouse: list[ToolFinding] = field(default_factory=list)
    """Raw findings from Lighthouse before deduplication."""

    mobile: ScanResult | None = field(default=None)
    """Scan result for mobile viewport (375×667). None if not applicable."""

    high_contrast: ScanResult | None = field(default=None)
    """Scan result in forced-colors: active (high contrast) mode. None if not applicable."""

    def raw_for_tool(self, tool: ScanTool) -> list[ToolFinding]:
        """Return the raw findings list for the given tool."""
        mapping = {
            ScanTool.PA11Y: self.raw_pa11y,
            ScanTool.AXE: self.raw_axe_core,
            ScanTool.PLAYWRIGHT: self.raw_playwright_axe,
            ScanTool.LIGHTHOUSE: self.raw_lighthouse,
        }
        return mapping.get(tool, [])


class MultiToolScanner:
    """
    Orquestra múltiplas ferramentas de acessibilidade em paralelo.

    Para cada arquivo:
    1. Gera HTML harness temporário (produção min builds para CDN rápido)
    2. Inicia servidor HTTP local para servir o harness (evita file:// issues)
    3. Executa todos os runners disponíveis em paralelo (asyncio.gather)
    4. ESLint roda diretamente no fonte (não precisa do harness)
    5. Coleta findings de cada runner
    6. Aplica o protocolo científico de detecção (deduplicação + confiança)
    7. Retorna ScanResult com metadados completos

    Arquitetura HTTP:
        O harness é servido via http://127.0.0.1:PORT/ em vez de file://.
        Isso resolve os timeouts de CDN: os scripts externos (React, Babel)
        carregam normalmente via HTTP, sem as restrições de segurança do
        protocolo file://.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Inicializa o orquestrador com os runners habilitados nas settings.

        Args:
            settings: Configuração global do sistema.
        """
        self.settings = settings
        self._runners: list[BaseRunner] = []
        self._eslint_runner: EslintRunner | None = None

        if settings.use_pa11y:
            self._runners.append(Pa11yRunner())
        if settings.use_axe:
            self._runners.append(AxeRunner())
        if settings.use_lighthouse:
            self._runners.append(LighthouseRunner())
        if settings.use_playwright:
            self._runners.append(PlaywrightAxeRunner())
        if settings.use_eslint:
            self._eslint_runner = EslintRunner()

    async def scan_file(
        self,
        file: Path,
        wcag: str,
        screenshot_dir: Path | None = None,
    ) -> ScanResult:
        """
        Escaneia um único arquivo com todas as ferramentas disponíveis.

        Args:
            file: Caminho do arquivo .tsx/.jsx a escanear.
            wcag: Nível WCAG alvo, ex: 'WCAG2AA'.
            screenshot_dir: Se fornecido, salva screenshot PNG do componente
                            renderizado nesse diretório.

        Returns:
            ScanResult com todos os issues deduplificados e metadados.
        """
        from a11y_autofix.protocol.detection import DetectionProtocol

        t0 = time.perf_counter()

        # Ler arquivo
        content, read_error = self._read_file(file)
        if read_error:
            return ScanResult(
                file=file,
                file_hash="sha256:error",
                issues=[],
                scan_time=0.0,
                tools_used=[],
                tool_versions={},
                error=read_error,
            )

        file_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()

        # Criar diretório temporário dedicado para este scan
        harness_dir = Path(tempfile.mkdtemp(prefix="a11y_harness_"))

        try:
            # Detectar framework e gerar o harness adequado
            framework = detect_framework(file, content)
            template_html_for_screenshot: str | None = None
            if framework == "angular":
                template_html = extract_angular_template(content, file)
                if template_html is None:
                    log.debug("angular_no_template", file=file.name)
                    return ScanResult(
                        file=file,
                        file_hash=file_hash,
                        issues=[],
                        scan_time=time.perf_counter() - t0,
                        tools_used=[],
                        tool_versions={},
                        error="Angular component has no detectable template",
                    )
                harness_html = build_angular_harness(template_html, file.name)
                template_html_for_screenshot = template_html
            else:
                harness_html = build_html_harness(content, file.name)
            harness_path = harness_dir / "harness.html"
            harness_path.write_text(harness_html, encoding="utf-8")

            # Gerar harness de screenshot para Angular (pré-processado + CSS/ícones)
            if screenshot_dir is not None and template_html_for_screenshot is not None:
                # Detectar projeto Angular e assets para carregar CSS/fonts/imagens reais
                project_root = find_angular_project_root(file)
                assets_dir_path = (
                    find_angular_assets_dir(project_root) if project_root else None
                )

                # Criar symlink "assets" no diretório do harness apontando para
                # os assets reais do projeto. Isso permite que src="assets/..."
                # resolva para os arquivos reais via HTTP local.
                if assets_dir_path is not None:
                    import os as _os
                    assets_link = harness_dir / "assets"
                    # Remover symlink quebrado ou desatualizado antes de criar
                    if assets_link.is_symlink():
                        # Symlink existe — verificar se aponta para o destino correto
                        try:
                            target = Path(_os.readlink(assets_link))
                            if not target.is_absolute():
                                target = assets_link.parent / target
                            if target.resolve() != assets_dir_path.resolve():
                                _os.unlink(assets_link)  # aponta para lugar errado
                        except OSError:
                            try:
                                _os.unlink(assets_link)  # symlink corrompido
                            except OSError:
                                pass
                    if not assets_link.exists() and not assets_link.is_symlink():
                        try:
                            _os.symlink(assets_dir_path, assets_link)
                            log.debug(
                                "assets_symlink_created",
                                src=str(assets_dir_path),
                                dst=str(assets_link),
                            )
                        except OSError as sym_err:
                            log.debug("assets_symlink_failed", error=str(sym_err))
                            assets_dir_path = None  # fallback offline

                # Build extra CSS: CSS vars + Bootstrap utils + component SCSS
                extra_css_parts: list[str] = []
                if project_root is not None:
                    css_vars = extract_project_css_vars(project_root)
                    if css_vars:
                        extra_css_parts.append(f'/* project CSS variables */\n{css_vars}')
                    bootstrap_css = load_bootstrap_utilities(project_root)
                    if bootstrap_css:
                        extra_css_parts.append(f'/* bootstrap utilities */\n{bootstrap_css}')
                component_css = load_component_css(file, project_root)
                if component_css:
                    extra_css_parts.append(f'/* component styles */\n{component_css}')
                extra_css = '\n'.join(extra_css_parts)

                screenshot_harness_html = build_angular_screenshot_harness(
                    template_html_for_screenshot,
                    file.name,
                    assets_dir=assets_dir_path,
                    extra_css=extra_css,
                )
                screenshot_harness_path = harness_dir / "screenshot_harness.html"
                screenshot_harness_path.write_text(screenshot_harness_html, encoding="utf-8")

            # Descobrir runners disponíveis (harness-based)
            available: list[BaseRunner] = []
            for runner in self._runners:
                try:
                    if await runner.available():
                        available.append(runner)
                    else:
                        log.debug("runner_unavailable", tool=runner.tool.value)
                except Exception as e:
                    log.warning(
                        "runner_availability_check_failed",
                        tool=runner.tool.value,
                        error=str(e),
                    )

            # Verificar disponibilidade do ESLint (source-based, não precisa de harness)
            eslint_available = False
            if self._eslint_runner:
                try:
                    eslint_available = await self._eslint_runner.available()
                    if not eslint_available:
                        log.debug("runner_unavailable", tool=ScanTool.ESLINT.value)
                except Exception as e:
                    log.warning(
                        "runner_availability_check_failed",
                        tool=ScanTool.ESLINT.value,
                        error=str(e),
                    )

            if not available and not eslint_available:
                log.warning("no_runners_available", file=str(file))
                return ScanResult(
                    file=file,
                    file_hash=file_hash,
                    issues=[],
                    scan_time=time.perf_counter() - t0,
                    tools_used=[],
                    tool_versions={},
                    error="No scan tools available. Install pa11y, axe-core, playwright or eslint.",
                )

            # Coletar versões
            versions: dict[str, str] = {}
            for runner in available:
                try:
                    versions[runner.tool.value] = await runner.version()
                except Exception:
                    versions[runner.tool.value] = "unknown"
            if eslint_available and self._eslint_runner:
                try:
                    versions[ScanTool.ESLINT.value] = await self._eslint_runner.version()
                except Exception:
                    versions[ScanTool.ESLINT.value] = "unknown"

            # Metadados de saída do runner Playwright (render_success).
            # Declarado fora do branch: o caminho só-ESLint também o consulta.
            scan_meta: dict = {}

            # Iniciar servidor HTTP local para servir o harness
            # Isso evita timeouts de CDN em contexto file://
            # O servidor é iniciado apenas se houver runners harness-based
            if available:
                with HarnessServer(harness_dir) as http_server:
                    http_url = http_server.url_for("harness.html")
                    log.debug("harness_server_started", url=http_url, port=http_server.port)

                    # Executar harness runners em paralelo via HTTP
                    # Prioridade de screenshot depende do framework:
                    #   Angular  → Playwright (usa harness pré-processado; LH capturaria
                    #               o harness axe com sintaxe crua Angular)
                    #   Outros   → Lighthouse > Playwright (LH tem rendering completo JS+CSS)
                    angular_screenshot_harness_ready = (
                        framework == "angular"
                        and screenshot_dir is not None
                        and (harness_dir / "screenshot_harness.html").exists()
                    )
                    # Runner designado para capturar o screenshot.
                    # Playwright tem prioridade em todos os frameworks: é o
                    # único com captura estilo Lighthouse (crops por elemento).
                    screenshot_runner: BaseRunner | None = None
                    if screenshot_dir is not None:
                        for runner_type in _SCREENSHOT_PRIORITY:
                            for r in available:
                                if isinstance(r, runner_type):
                                    screenshot_runner = r
                                    break
                            if screenshot_runner:
                                break
                    # Extensão por runner: Playwright→.png, Lighthouse→.jpg
                    screenshot_path = (
                        screenshot_dir / (
                            f"{file.stem}.png"
                            if isinstance(screenshot_runner, PlaywrightAxeRunner)
                            else f"{file.stem}.jpg"
                        )
                        if screenshot_dir is not None and screenshot_runner is not None
                        else None
                    )

                    # URL do harness de display pré-processado (Playwright + Angular)
                    screenshot_harness_url = (
                        http_server.url_for("screenshot_harness.html")
                        if isinstance(screenshot_runner, PlaywrightAxeRunner)
                        and angular_screenshot_harness_ready
                        else None
                    )

                    harness_tasks = [
                        runner.safe_run(
                            harness_path,
                            wcag,
                            harness_url=http_url,
                            **_build_runner_kwargs(
                                runner,
                                framework=framework,
                                screenshot_path=screenshot_path if runner is screenshot_runner else None,
                                screenshot_harness_url=screenshot_harness_url,
                                out_meta=scan_meta,
                            ),
                        )
                        for runner in available
                    ]

                    async def _no_eslint() -> list[ToolFinding]:
                        return []

                    eslint_task = (
                        self._eslint_runner.safe_run_on_source(file, wcag)
                        if eslint_available and self._eslint_runner
                        else _no_eslint()
                    )

                    all_results = await asyncio.gather(
                        *harness_tasks,
                        eslint_task,
                        return_exceptions=True,
                    )
            else:
                # Só ESLint disponível (não precisa de servidor HTTP)
                async def _no_eslint() -> list[ToolFinding]:
                    return []

                eslint_task = (
                    self._eslint_runner.safe_run_on_source(file, wcag)
                    if eslint_available and self._eslint_runner
                    else _no_eslint()
                )
                all_results = await asyncio.gather(eslint_task, return_exceptions=True)

            # Separar resultados harness vs eslint
            harness_results = all_results[:-1] if available else []
            eslint_result = all_results[-1]

            # Mapear findings por tool
            findings_by_tool: dict[ScanTool, list[ToolFinding]] = {}
            for runner, result in zip(available, harness_results):
                if isinstance(result, Exception):
                    log.warning(
                        "runner_exception",
                        tool=runner.tool.value,
                        error=str(result),
                    )
                    findings_by_tool[runner.tool] = []
                else:
                    findings_by_tool[runner.tool] = result  # type: ignore[assignment]

            if eslint_available:
                if isinstance(eslint_result, Exception):
                    log.warning(
                        "runner_exception",
                        tool=ScanTool.ESLINT.value,
                        error=str(eslint_result),
                    )
                    findings_by_tool[ScanTool.ESLINT] = []
                else:
                    findings_by_tool[ScanTool.ESLINT] = eslint_result or []  # type: ignore[assignment]

            all_tools = [r.tool for r in available] + (
                [ScanTool.ESLINT] if eslint_available else []
            )

            # Aplicar protocolo científico de detecção
            protocol = DetectionProtocol(self.settings)
            scan_result = protocol.run(
                file=file,
                file_content=content,
                findings_by_tool=findings_by_tool,
                tools_used=all_tools,
                tool_versions=versions,
            )
            scan_result.file_hash = file_hash
            scan_result.scan_time = time.perf_counter() - t0

            # Covariável de validade: o componente renderizou no harness?
            # (preenchido pelo PlaywrightAxeRunner via out_meta; None se o
            # Playwright não rodou neste scan)
            scan_result.render_success = (
                scan_meta.get("render_success") if available else None
            )

            # Propagar crops de elemento entre issues que apontam para o
            # mesmo elemento via seletores diferentes (pa11y usa
            # '#root > div > img' enquanto Playwright usa 'img')
            self._propagate_element_screenshots(scan_result.issues)

            # Registrar caminho do screenshot se foi gerado.
            # Angular usa Playwright → .png; outros usam Lighthouse → .jpg (fallback .png).
            if screenshot_dir is not None:
                for ext in (".png", ".jpg"):
                    shot = screenshot_dir / f"{file.stem}{ext}"
                    if shot.exists():
                        scan_result.screenshot_path = shot
                        break

            log.info(
                "scan_complete",
                file=file.name,
                issues=len(scan_result.issues),
                high_conf=len(scan_result.high_confidence_issues()),
                tools=len(all_tools),
                time_s=f"{scan_result.scan_time:.2f}",
            )
            return scan_result

        finally:
            # Limpar diretório temporário em qualquer caso
            shutil.rmtree(harness_dir, ignore_errors=True)

    async def scan_file_extended(self, file: Path, wcag: str) -> MultiToolScanResult:
        """
        Scan a file and return both the consensus result and the raw
        per-scanner findings for individual-scanner baseline comparison.

        Methodology reference: Section 3.7.3 (Baselines) — individual-scanner
        baselines compare the raw detection outputs of each scanner against
        the multi-tool consensus.

        Args:
            file: Path to the .tsx/.jsx component to scan.
            wcag: Target WCAG level (e.g. 'WCAG2AA').

        Returns:
            MultiToolScanResult containing consensus + raw per-scanner findings.
        """
        from a11y_autofix.protocol.detection import DetectionProtocol

        t0 = time.perf_counter()
        content, read_error = self._read_file(file)
        if read_error:
            consensus = ScanResult(
                file=file,
                file_hash="sha256:error",
                issues=[],
                scan_time=0.0,
                tools_used=[],
                tool_versions={},
                error=read_error,
            )
            return MultiToolScanResult(consensus=consensus)

        file_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()

        harness_dir = Path(tempfile.mkdtemp(prefix="a11y_harness_ext_"))

        try:
            framework = detect_framework(file, content)
            if framework == "angular":
                template_html = extract_angular_template(content, file)
                if template_html is None:
                    consensus = ScanResult(
                        file=file,
                        file_hash=file_hash,
                        issues=[],
                        scan_time=time.perf_counter() - t0,
                        tools_used=[],
                        tool_versions={},
                        error="Angular component has no detectable template",
                    )
                    return MultiToolScanResult(consensus=consensus)
                harness_html = build_angular_harness(template_html, file.name)
            else:
                harness_html = build_html_harness(content, file.name)
            harness_path = harness_dir / "harness.html"
            harness_path.write_text(harness_html, encoding="utf-8")

            available: list[BaseRunner] = []
            for runner in self._runners:
                try:
                    if await runner.available():
                        available.append(runner)
                except Exception:
                    pass

            if not available:
                consensus = ScanResult(
                    file=file,
                    file_hash=file_hash,
                    issues=[],
                    scan_time=time.perf_counter() - t0,
                    tools_used=[],
                    tool_versions={},
                    error="No scan tools available.",
                )
                return MultiToolScanResult(consensus=consensus)

            versions: dict[str, str] = {}
            for runner in available:
                try:
                    versions[runner.tool.value] = await runner.version()
                except Exception:
                    versions[runner.tool.value] = "unknown"

            with HarnessServer(harness_dir) as http_server:
                http_url = http_server.url_for("harness.html")

                raw_results = await asyncio.gather(
                    *[
                        runner.safe_run(
                            harness_path,
                            wcag,
                            harness_url=http_url,
                            **({"framework": framework} if isinstance(runner, PlaywrightAxeRunner) else {}),
                        )
                        for runner in available
                    ],
                    return_exceptions=True,
                )

            findings_by_tool: dict[ScanTool, list[ToolFinding]] = {}
            for runner, result in zip(available, raw_results):
                if isinstance(result, Exception):
                    findings_by_tool[runner.tool] = []
                else:
                    findings_by_tool[runner.tool] = result  # type: ignore[assignment]

            protocol = DetectionProtocol(self.settings)
            scan_result = protocol.run(
                file=file,
                file_content=content,
                findings_by_tool=findings_by_tool,
                tools_used=[r.tool for r in available],
                tool_versions=versions,
            )
            scan_result.file_hash = file_hash
            scan_result.scan_time = time.perf_counter() - t0

            return MultiToolScanResult(
                consensus=scan_result,
                raw_pa11y=findings_by_tool.get(ScanTool.PA11Y, []),
                raw_axe_core=findings_by_tool.get(ScanTool.AXE, []),
                raw_playwright_axe=findings_by_tool.get(ScanTool.PLAYWRIGHT, []),
                raw_lighthouse=findings_by_tool.get(ScanTool.LIGHTHOUSE, []),
            )

        finally:
            shutil.rmtree(harness_dir, ignore_errors=True)

    async def scan_files(
        self,
        files: list[Path],
        wcag: str,
        on_file_done: Callable[[ScanResult], None | object] | None = None,
        screenshot_dir: Path | None = None,
    ) -> list[ScanResult]:
        """
        Escaneia múltiplos arquivos com controle de concorrência.

        Args:
            files: Lista de arquivos a escanear.
            wcag: Nível WCAG alvo.
            on_file_done: Callback opcional chamado após cada arquivo ser
                          escaneado, recebendo o ScanResult. Útil para
                          progresso em tempo real.
            screenshot_dir: Se fornecido, salva screenshots PNG dos componentes.

        Returns:
            Lista de ScanResult na mesma ordem dos arquivos.
        """
        sem = asyncio.Semaphore(self.settings.max_concurrent_scans)

        async def scan_with_sem(f: Path) -> ScanResult:
            async with sem:
                result = await self.scan_file(f, wcag, screenshot_dir=screenshot_dir)
                if on_file_done is not None:
                    # Mirror the same async-safe pattern used in Pipeline.run():
                    # on_file_done may be a regular callable OR an async def.
                    cb = on_file_done(result)
                    if asyncio.iscoroutine(cb):
                        await cb
                return result

        results = await asyncio.gather(*[scan_with_sem(f) for f in files])
        return list(results)

    # ─── Métodos de scan adicionais ───────────────────────────────────────────

    async def scan_file_mobile(self, file: Path, wcag: str) -> ScanResult | None:
        """
        Escaneia um arquivo com viewport mobile (375×667).

        Só executa se o componente exibir padrões responsivos no código-fonte;
        retorna None caso contrário para evitar scans desnecessários.

        Args:
            file: Arquivo .tsx/.jsx a escanear.
            wcag: Nível WCAG alvo.

        Returns:
            ScanResult com scan_mode=MOBILE, ou None se não responsivo.
        """
        from a11y_autofix.protocol.detection import DetectionProtocol

        content, read_error = self._read_file(file)
        if read_error:
            return None

        if not self._detect_responsive_content(content):
            log.debug("mobile_scan_skipped_not_responsive", file=file.name)
            return None

        viewport = {
            "width": self.settings.mobile_viewport_width,
            "height": self.settings.mobile_viewport_height,
        }
        return await self._run_playwright_scan(
            file, content, wcag, viewport=viewport, scan_mode=ScanMode.MOBILE
        )

    async def scan_file_high_contrast(self, file: Path, wcag: str) -> ScanResult | None:
        """
        Escaneia um arquivo com forced-colors: active (alto contraste).

        Só executa se o componente tiver styling dependente de cor;
        retorna None caso contrário.

        Args:
            file: Arquivo .tsx/.jsx a escanear.
            wcag: Nível WCAG alvo.

        Returns:
            ScanResult com scan_mode=HIGH_CONTRAST, ou None se não aplicável.
        """
        content, read_error = self._read_file(file)
        if read_error:
            return None

        if not self._detect_high_contrast_content(content):
            log.debug("high_contrast_scan_skipped_no_color_styling", file=file.name)
            return None

        return await self._run_playwright_scan(
            file, content, wcag, forced_colors=True, scan_mode=ScanMode.HIGH_CONTRAST
        )

    async def _run_playwright_scan(
        self,
        file: Path,
        content: str,
        wcag: str,
        viewport: "dict[str, int] | None" = None,
        forced_colors: bool = False,
        scan_mode: ScanMode = ScanMode.DESKTOP,
    ) -> ScanResult | None:
        """
        Executa scan usando somente PlaywrightAxeRunner com parâmetros customizados.

        Usado pelos métodos mobile e high_contrast para evitar duplicar
        a lógica de harness, servidor HTTP e protocolo de detecção.
        """
        from a11y_autofix.protocol.detection import DetectionProtocol

        playwright_runner: PlaywrightAxeRunner | None = None
        for runner in self._runners:
            if isinstance(runner, PlaywrightAxeRunner):
                playwright_runner = runner
                break

        if playwright_runner is None:
            log.debug("playwright_runner_not_configured", scan_mode=scan_mode.value)
            return None

        if not await playwright_runner.available():
            log.debug("playwright_runner_unavailable", scan_mode=scan_mode.value)
            return None

        t0 = time.perf_counter()
        file_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        harness_dir = Path(tempfile.mkdtemp(prefix=f"a11y_{scan_mode.value}_"))

        try:
            framework = detect_framework(file, content)
            if framework == "angular":
                template_html = extract_angular_template(content, file)
                if template_html is None:
                    return None
                harness_html = build_angular_harness(template_html, file.name)
            else:
                harness_html = build_html_harness(content, file.name)

            harness_path = harness_dir / "harness.html"
            harness_path.write_text(harness_html, encoding="utf-8")

            version = await playwright_runner.version()

            with HarnessServer(harness_dir) as http_server:
                http_url = http_server.url_for("harness.html")
                findings = await playwright_runner.safe_run(
                    harness_path,
                    wcag,
                    harness_url=http_url,
                    framework=framework,
                    viewport=viewport,
                    forced_colors=forced_colors,
                )

            protocol = DetectionProtocol(self.settings)
            scan_result = protocol.run(
                file=file,
                file_content=content,
                findings_by_tool={ScanTool.PLAYWRIGHT: findings},
                tools_used=[ScanTool.PLAYWRIGHT],
                tool_versions={ScanTool.PLAYWRIGHT.value: version},
            )
            scan_result.file_hash = file_hash
            scan_result.scan_time = time.perf_counter() - t0
            scan_result.scan_mode = scan_mode

            log.info(
                "extra_scan_complete",
                file=file.name,
                mode=scan_mode.value,
                issues=len(scan_result.issues),
                time_s=f"{scan_result.scan_time:.2f}",
            )
            return scan_result

        except Exception as e:
            log.warning("extra_scan_failed", mode=scan_mode.value, error=str(e)[:200])
            return None
        finally:
            shutil.rmtree(harness_dir, ignore_errors=True)

    # ─── Screenshots ─────────────────────────────────────────────────────────

    @staticmethod
    def _propagate_element_screenshots(issues: list) -> None:
        """
        Propaga crops de elemento entre issues que referenciam o mesmo
        elemento com seletores sintaticamente diferentes.

        Ferramentas diferentes geram seletores diferentes para o mesmo
        elemento (axe: 'img'; pa11y: '#root > div > img'). Os crops são
        capturados apenas pelo Playwright com os seletores dele; este
        passo preenche os demais quando o último segmento do seletor
        (tag/classe simples) tem correspondência ÚNICA — ambiguidade
        (dois imgs distintos) não propaga, evitando crop errado.
        """
        def last_segment(selector: str) -> str:
            seg = selector.split(">")[-1].strip()
            # Normalizar pseudo-classes: 'li:nth-child(2)' → 'li'
            return seg.split(":")[0].strip().lower()

        with_crop = [i for i in issues if i.screenshot_path]
        if not with_crop:
            return

        for issue in issues:
            if issue.screenshot_path:
                continue
            seg = last_segment(issue.selector)
            if not seg:
                continue
            candidates = {
                c.screenshot_path: c
                for c in with_crop
                if last_segment(c.selector) == seg
            }
            if len(candidates) == 1:
                src = next(iter(candidates.values()))
                issue.screenshot_path = src.screenshot_path
                issue.bounding_rect = src.bounding_rect

    # ─── Detecção de conteúdo ─────────────────────────────────────────────────

    @staticmethod
    def _detect_responsive_content(content: str) -> bool:
        """
        Retorna True se o componente tem padrões de renderização responsiva.

        Verifica hooks, media queries, e APIs DOM relacionados a viewport.
        """
        patterns = [
            r'@media\s*[\(\[]',           # CSS media queries (inline ou styled-components)
            r'useWindowSize\b',           # hook popular de dimensão de janela
            r'useMediaQuery\b',           # MUI / custom hooks
            r'useBreakpoint\b',           # hooks de breakpoint comuns
            r'window\.innerWidth\b',      # leitura direta de largura de janela
            r'window\.matchMedia\s*\(',   # Web API de media query
            r'matchMedia\s*\(',           # idem sem qualificação
            r'\bisMobile\b',              # variável/prop comum
            r'\bisTablet\b',
            r'\bisDesktop\b',
            r'screen\.width\b',           # API de tela
            r'viewport',                  # props/variáveis com nome viewport
            r'breakpoints?\s*[=:\.{]',   # configuração de breakpoints
            r'ResponsiveContainer\b',     # componente Recharts/similar
        ]
        return any(re.search(p, content) for p in patterns)

    @staticmethod
    def _detect_high_contrast_content(content: str) -> bool:
        """
        Retorna True se o componente tem styling dependente de cor.

        Componentes com cores explícitas podem ter problemas em
        forced-colors mode (alto contraste do Windows).
        """
        patterns = [
            r'color\s*:\s*["\']?(?:#|rgb|hsl|var\()',   # propriedades CSS de cor
            r'backgroundColor\s*:\s*["\']?(?:#|rgb|hsl|var\()',
            r'background\s*:\s*["\']?(?:#|rgb|hsl|var\()',
            r'borderColor\s*:',
            r'outlineColor\s*:',
            r'forced-colors\b',           # já trata forced-colors explicitamente
            r'prefers-contrast\b',        # media query de preferência de contraste
            r'#[0-9a-fA-F]{3,8}\b',       # cores hexadecimais literais
            r'rgba?\s*\(\s*\d',           # funções de cor
            r'hsla?\s*\(',
            r'color-scheme\b',
            r'currentColor\b',
            r'\.color\b',                 # props de cor em styled-components
        ]
        return any(re.search(p, content, re.IGNORECASE) for p in patterns)

    def _read_file(self, file: Path) -> tuple[str, str | None]:
        """Lê arquivo com tratamento de erros de encoding."""
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return file.read_text(encoding=encoding), None
            except UnicodeDecodeError:
                continue
        return "", f"Cannot decode: {file}"
