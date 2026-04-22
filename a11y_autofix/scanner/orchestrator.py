"""Orquestrador multi-ferramenta: executa todos os runners em paralelo."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from a11y_autofix.config import ScanResult, ScanTool, Settings, ToolFinding
from a11y_autofix.scanner.axe import AxeRunner
from a11y_autofix.scanner.base import BaseRunner
from a11y_autofix.scanner.eslint import EslintRunner
from a11y_autofix.scanner.lighthouse import LighthouseRunner
from a11y_autofix.scanner.pa11y import Pa11yRunner
from a11y_autofix.scanner.playwright_axe import PlaywrightAxeRunner
from a11y_autofix.utils.files import build_html_harness
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

    async def scan_file(self, file: Path, wcag: str) -> ScanResult:
        """
        Escaneia um único arquivo com todas as ferramentas disponíveis.

        Args:
            file: Caminho do arquivo .tsx/.jsx a escanear.
            wcag: Nível WCAG alvo, ex: 'WCAG2AA'.

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
            # Gerar harness HTML e escrevê-lo no diretório temporário
            harness_html = build_html_harness(content, file.name)
            harness_path = harness_dir / "harness.html"
            harness_path.write_text(harness_html, encoding="utf-8")

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

            # Iniciar servidor HTTP local para servir o harness
            # Isso evita timeouts de CDN em contexto file://
            # O servidor é iniciado apenas se houver runners harness-based
            if available:
                with HarnessServer(harness_dir) as http_server:
                    http_url = http_server.url_for("harness.html")
                    log.debug("harness_server_started", url=http_url, port=http_server.port)

                    # Executar harness runners em paralelo via HTTP
                    harness_tasks = [
                        runner.safe_run(harness_path, wcag, harness_url=http_url)
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
                        runner.safe_run(harness_path, wcag, harness_url=http_url)
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
    ) -> list[ScanResult]:
        """
        Escaneia múltiplos arquivos com controle de concorrência.

        Args:
            files: Lista de arquivos a escanear.
            wcag: Nível WCAG alvo.
            on_file_done: Callback opcional chamado após cada arquivo ser
                          escaneado, recebendo o ScanResult. Útil para
                          progresso em tempo real.

        Returns:
            Lista de ScanResult na mesma ordem dos arquivos.
        """
        sem = asyncio.Semaphore(self.settings.max_concurrent_scans)

        async def scan_with_sem(f: Path) -> ScanResult:
            async with sem:
                result = await self.scan_file(f, wcag)
                if on_file_done is not None:
                    # Mirror the same async-safe pattern used in Pipeline.run():
                    # on_file_done may be a regular callable OR an async def.
                    cb = on_file_done(result)
                    if asyncio.iscoroutine(cb):
                        await cb
                return result

        results = await asyncio.gather(*[scan_with_sem(f) for f in files])
        return list(results)

    def _read_file(self, file: Path) -> tuple[str, str | None]:
        """Lê arquivo com tratamento de erros de encoding."""
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return file.read_text(encoding=encoding), None
            except UnicodeDecodeError:
                continue
        return "", f"Cannot decode: {file}"
