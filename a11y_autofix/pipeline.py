"""
Pipeline principal: orquestra scan → route → fix → report.

O pipeline é o ponto central que coordena todas as etapas do processo
de detecção e correção de acessibilidade para um conjunto de arquivos.
"""

from __future__ import annotations

import asyncio
import time
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
    ) -> list[FixResult]:
        """
        Executa o pipeline completo para uma lista de targets.

        Args:
            targets: Arquivos, diretórios ou padrões glob.
            wcag_level: Nível WCAG alvo.
            output_dir: Diretório de saída para relatórios.

        Returns:
            Lista de FixResult para cada arquivo processado.
        """
        # 1. Descobrir arquivos
        files = self._discover_files(targets)
        if not files:
            log.warning("no_files_found", targets=[str(t) for t in targets])
            return []

        log.info("pipeline_start", files=len(files), model=self.model_config.model_id)

        # 2. Scan paralelo
        scan_results = await self.scanner.scan_files(files, wcag_level)

        files_with_issues = [s for s in scan_results if s.has_issues]
        log.info(
            "scan_complete",
            total=len(scan_results),
            with_issues=len(files_with_issues),
        )

        if self.dry_run:
            log.info("dry_run_mode", skipping_fixes=True)
            return [
                FixResult(
                    file=s.file,
                    scan_result=s,
                    final_success=False,
                    issues_fixed=0,
                    issues_pending=len(s.issues),
                    total_time=0.0,
                )
                for s in scan_results
            ]

        # 3. Corrigir arquivos com issues
        sem = asyncio.Semaphore(self.settings.max_concurrent_agents)

        async def fix_with_sem(scan: object) -> FixResult:
            from a11y_autofix.config import ScanResult
            if not isinstance(scan, ScanResult):
                raise TypeError
            if not scan.has_issues:
                return FixResult(
                    file=scan.file,
                    scan_result=scan,
                    final_success=True,
                    issues_fixed=0,
                    issues_pending=0,
                    total_time=0.0,
                )
            async with sem:
                return await self._fix_file(scan, wcag_level)

        fix_results = await asyncio.gather(*[fix_with_sem(s) for s in scan_results])

        # 4. Gerar relatórios
        if output_dir:
            await self._generate_reports(
                scan_results=scan_results,
                fix_results=list(fix_results),
                output_dir=output_dir,
                wcag_level=wcag_level,
            )

        total_fixed = sum(r.issues_fixed for r in fix_results)
        total_issues = sum(len(r.scan_result.issues) for r in fix_results)
        log.info(
            "pipeline_complete",
            fixed=total_fixed,
            total=total_issues,
            rate=f"{total_fixed/total_issues*100:.1f}%" if total_issues > 0 else "0%",
        )

        return list(fix_results)

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

        for attempt_num in range(1, self.settings.max_retries_per_agent + 1):
            attempt_task = AgentTask(
                file=task.file,
                file_content=current_content,
                issues=[i for i in task.issues if not i.resolved],
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
                # Aplicar correção ao arquivo
                scan.file.write_text(patch.new_content, encoding="utf-8")
                current_content = patch.new_content
                break

        total_time = time.perf_counter() - t0
        final_success = any(a.success for a in attempts)
        issues_fixed = len(scan.issues) if final_success else 0
        issues_pending = 0 if final_success else len(scan.issues)

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
