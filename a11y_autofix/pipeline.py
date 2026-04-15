"""
Pipeline principal: orquestra scan → route → fix → report.

O pipeline é o ponto central que coordena todas as etapas do processo
de detecção e correção de acessibilidade para um conjunto de arquivos.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from a11y_autofix.agents.direct_llm import DirectLLMAgent
from a11y_autofix.agents.openhands import OpenHandsAgent
from a11y_autofix.agents.swe import SWEAgent
from a11y_autofix.config import (
    AgentTask,
    AgentType,
    FixAttempt,
    FixResult,
    ModelConfig,
    Settings,
)
from a11y_autofix.llm.client import LocalLLMClient
from a11y_autofix.router.engine import Router
from a11y_autofix.scanner.orchestrator import MultiToolScanner
from a11y_autofix.utils.files import find_react_files

if TYPE_CHECKING:
    from a11y_autofix.agents.base import BaseAgent

log = structlog.get_logger(__name__)


class Pipeline:
    """
    Orquestrador principal do sistema a11y-autofix.

    Coordena:
    1. Descoberta de arquivos
    2. Scan multi-ferramenta paralelo
    3. Routing automático para o agente correto
    4. Tentativas de correção com retry
    5. Geração de relatórios
    """

    def __init__(
        self,
        settings: Settings,
        model_config: ModelConfig,
        agent_preference: AgentType = AgentType.AUTO,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            settings: Configuração global.
            model_config: Configuração do modelo LLM.
            agent_preference: Preferência de agente (AUTO = router decide).
            dry_run: Se True, não aplica correções.
        """
        self.settings = settings
        self.model_config = model_config
        self.agent_preference = agent_preference
        self.dry_run = dry_run

        self.scanner = MultiToolScanner(settings)
        self.router = Router(settings)
        self.llm_client = LocalLLMClient(model_config)

    async def run(
        self,
        targets: list[Path] | list[str],
        wcag_level: str = "WCAG2AA",
        output_dir: Path | None = None,
        on_file_done: Callable | None = None,
        scan_cache: "dict[str, object] | None" = None,
    ) -> list[FixResult]:
        """
        Pipeline em streaming: scan e fix acontecem CONCORRENTEMENTE.

        Arquitetura produtora-consumidora:
          • scan_sem controla quantos scanners rodam em paralelo
          • fix_sem  controla quantos agentes LLM rodam em paralelo
          • Cada arquivo entra no fix assim que seu scan termina —
            o LLM começa a trabalhar nos primeiros arquivos enquanto
            o scan ainda processa os demais.
          • scan_cache é lido E escrito: cache hits evitam re-scan
            em runs subsequentes (modelos 2 e 3 reusam resultados do 1).

        Args:
            targets:    Arquivos, diretórios ou padrões glob.
            wcag_level: Nível WCAG alvo.
            output_dir: Diretório de saída para relatórios.
            on_file_done: Callback async chamado após cada arquivo.
            scan_cache: Dict mutável {str(path) → ScanResult}. Pipeline
                        lê hits e escreve misses para uso pelos modelos
                        seguintes. Persistência em disco é responsabilidade
                        do runner (ScanResultCache.save()).
        """
        from a11y_autofix.config import ScanResult

        files = self._discover_files(targets)
        if not files:
            log.warning("no_files_found", targets=[str(t) for t in targets])
            return []

        # ── Pre-flight: verificar endpoint de chat ANTES de processar arquivos ──
        # health_check() só testa /v1/models — não detecta 404 em /v1/chat/completions.
        # test_chat() envia inferência real para garantir que o modelo responde.
        if not self.dry_run:
            ok, msg = await self.llm_client.test_chat()
            if not ok:
                raise RuntimeError(
                    f"LLM pre-flight failed — aborting experiment to avoid wasting scan time.\n"
                    f"Error: {msg}\n"
                    f"Fix the LLM endpoint, then restart the experiment "
                    f"(checkpoints already saved will be reused automatically)."
                )
            log.info("preflight_ok", model=self.model_config.model_id, result=msg)

        cached_count = sum(
            1 for f in files
            if scan_cache is not None and isinstance(
                scan_cache.get(str(f)) or scan_cache.get(str(f.resolve())), ScanResult
            )
        )
        log.info(
            "pipeline_start",
            files=len(files),
            cache_hits=cached_count,
            to_scan=len(files) - cached_count,
            model=self.model_config.model_id,
        )

        # Semáforos independentes: scan e fix rodam ao mesmo tempo
        scan_sem = asyncio.Semaphore(self.settings.max_concurrent_scans)
        fix_sem  = asyncio.Semaphore(self.settings.max_concurrent_agents)

        all_results: list[FixResult] = []
        results_lock = asyncio.Lock()
        scanned_count = 0
        fixed_count   = 0

        async def process_file(file: Path) -> FixResult:
            nonlocal scanned_count, fixed_count

            # ── Fase 1: Scan (ou hit no cache) ────────────────────────────────
            cached_sr = None
            if scan_cache is not None:
                # Tentar str(file) primeiro, depois str(file.resolve()) como fallback
                cached_sr = scan_cache.get(str(file))
                if cached_sr is None:
                    cached_sr = scan_cache.get(str(file.resolve()))

            if isinstance(cached_sr, ScanResult):
                scan_result = cached_sr
            else:
                async with scan_sem:
                    scan_result = await self.scanner.scan_file(file, wcag_level)
                # Escrever no cache com ambas as chaves para lookup consistente
                if scan_cache is not None:
                    scan_cache[str(file)] = scan_result
                    scan_cache[str(file.resolve())] = scan_result

            async with results_lock:
                scanned_count += 1
                if scanned_count % 100 == 0 or scanned_count == len(files):
                    log.info(
                        "scan_progress",
                        scanned=scanned_count,
                        total=len(files),
                        fixed=fixed_count,
                    )

            # ── Fase 2: Fix (imediatamente após o scan) ────────────────────────
            if self.dry_run:
                result = FixResult(
                    file=file,
                    scan_result=scan_result,
                    final_success=False,
                    issues_fixed=0,
                    issues_pending=len(scan_result.issues),
                    total_time=0.0,
                )
            elif not scan_result.has_issues:
                result = FixResult(
                    file=file,
                    scan_result=scan_result,
                    final_success=True,
                    issues_fixed=0,
                    issues_pending=0,
                    total_time=0.0,
                )
            else:
                async with fix_sem:
                    result = await self._fix_file(scan_result, wcag_level)
                async with results_lock:
                    fixed_count += 1

            async with results_lock:
                all_results.append(result)

            if on_file_done is not None:
                cb = on_file_done(result)
                if asyncio.iscoroutine(cb):
                    await cb

            return result

        await asyncio.gather(*[process_file(f) for f in files])

        # Reordenar resultados para corresponder à ordem de entrada dos arquivos.
        # asyncio.gather não garante ordem de inserção em all_results porque
        # process_file é concorrente — arquivos mais rápidos chegam primeiro.
        file_order = {f: i for i, f in enumerate(files)}
        all_results.sort(key=lambda r: file_order.get(r.file, len(files)))

        # Relatórios
        if output_dir:
            scan_results_typed = [
                r.scan_result for r in all_results
                if isinstance(r.scan_result, ScanResult)
            ]
            await self._generate_reports(
                scan_results=scan_results_typed,
                fix_results=all_results,
                output_dir=output_dir,
                wcag_level=wcag_level,
            )

        total_fixed  = sum(r.issues_fixed for r in all_results)
        total_issues = sum(len(r.scan_result.issues) for r in all_results)
        log.info(
            "pipeline_complete",
            fixed=total_fixed,
            total=total_issues,
            rate=f"{total_fixed/total_issues*100:.1f}%" if total_issues > 0 else "0%",
        )

        return all_results

    async def _fix_file(self, scan: object, wcag_level: str) -> FixResult:
        """
        Tenta corrigir um arquivo com retry automático.

        Args:
            scan: ScanResult do arquivo.
            wcag_level: Nível WCAG.

        Returns:
            FixResult com todas as tentativas.
        """
        from a11y_autofix.config import ScanResult
        if not isinstance(scan, ScanResult):
            raise TypeError("Expected ScanResult")

        t0 = time.perf_counter()
        attempts: list[FixAttempt] = []

        # Router decide o agente
        decision = self.router.decide(scan, self.agent_preference)
        agent = self._create_agent(decision.agent)

        task = AgentTask(
            file=scan.file,
            file_content=scan.file.read_text(encoding="utf-8"),
            issues=scan.issues,
            wcag_level=wcag_level,
        )

        current_content = task.file_content
        # Rastrear issues resolvidas por tentativa (baseado em diff aplicado)
        resolved_issue_ids: set[str] = set()

        for attempt_num in range(1, self.settings.max_retries_per_agent + 1):
            pending_issues = [i for i in task.issues if i.issue_id not in resolved_issue_ids]
            attempt_task = AgentTask(
                file=task.file,
                file_content=current_content,
                issues=pending_issues,
                wcag_level=task.wcag_level,
            )

            if not attempt_task.issues:
                break

            log.info(
                "fix_attempt",
                file=scan.file.name,
                attempt=attempt_num,
                agent=decision.agent,
                issues=len(attempt_task.issues),
            )

            patch = await agent.run(attempt_task)

            attempt = FixAttempt(
                attempt_number=attempt_num,
                agent=decision.agent,
                model=self.model_config.model_id,
                timestamp=datetime.now(tz=timezone.utc),
                success=patch.success,
                diff=patch.diff,
                new_content=patch.new_content,
                tokens_used=patch.tokens_used,
                time_seconds=patch.time_seconds,
                error=patch.error,
            )
            attempts.append(attempt)

            if patch.success and patch.new_content:
                scan.file.write_text(patch.new_content, encoding="utf-8")
                current_content = patch.new_content
                # Marcar as issues desta tentativa como resolvidas.
                # O agente recebeu apenas pending_issues, então todas as que
                # ele tinha como alvo são consideradas corrigidas no patch.
                for issue in pending_issues:
                    resolved_issue_ids.add(issue.issue_id)
                # Continuar o loop: pode haver issues remanescentes de tentativas
                # anteriores que o agente não incluiu nesta rodada.

        total_time = time.perf_counter() - t0
        issues_fixed = len(resolved_issue_ids)
        issues_pending = len(scan.issues) - issues_fixed
        final_success = issues_fixed > 0

        return FixResult(
            file=scan.file,
            scan_result=scan,
            attempts=attempts,
            final_success=final_success,
            issues_fixed=issues_fixed,
            issues_pending=issues_pending,
            total_time=total_time,
        )

    def _create_agent(self, agent_name: str) -> "BaseAgent":
        """Instancia o agente pelo nome."""
        if agent_name == "openhands":
            return OpenHandsAgent(self.llm_client)
        elif agent_name == "swe-agent":
            return SWEAgent(self.llm_client)
        else:
            return DirectLLMAgent(self.llm_client)

    def _discover_files(self, targets: list[Path] | list[str]) -> list[Path]:
        """Descobre arquivos React/TypeScript a partir de targets."""
        files: list[Path] = []
        for target in targets:
            path = Path(target) if isinstance(target, str) else target
            found = find_react_files(path)
            files.extend(found)

        # Deduplicar mantendo ordem
        seen: set[Path] = set()
        unique: list[Path] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique

    async def _generate_reports(
        self,
        scan_results: list[object],
        fix_results: list[FixResult],
        output_dir: Path,
        wcag_level: str,
    ) -> None:
        """Gera relatórios JSON e HTML."""
        from a11y_autofix.config import ScanResult
        from a11y_autofix.reporter.html_reporter import HTMLReporter
        from a11y_autofix.reporter.json_reporter import JSONReporter

        typed_scans = [s for s in scan_results if isinstance(s, ScanResult)]

        json_reporter = JSONReporter(self.settings)
        json_path = json_reporter.generate(
            scan_results=typed_scans,
            fix_results=fix_results,
            output_dir=output_dir,
            wcag_level=wcag_level,
            model_name=self.model_config.model_id,
        )

        import json
        report_data = json.loads(json_path.read_text(encoding="utf-8"))

        html_reporter = HTMLReporter()
        html_reporter.generate(report_data=report_data, output_dir=output_dir)
