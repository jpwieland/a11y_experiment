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
    ScanMode,
    ScanResult,
    Settings,
)
from a11y_autofix.llm.client import LocalLLMClient
from a11y_autofix.router.engine import Router
from a11y_autofix.scanner.orchestrator import MultiToolScanner
from a11y_autofix.utils.angular_template import find_angular_components
from a11y_autofix.utils.files import find_react_files, label_for_path, project_of

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
        framework: str = "auto",
        strategy: str = "few-shot",
    ) -> None:
        """
        Args:
            settings: Configuração global.
            model_config: Configuração do modelo LLM.
            agent_preference: Preferência de agente (AUTO = router decide).
            dry_run: Se True, não aplica correções.
            framework: Framework alvo — 'react', 'angular' ou 'auto' (detecta ambos).
            strategy: Estratégia de prompting (IV2): 'zero-shot', 'few-shot'
                      ou 'chain-of-thought'. Propagada a todos os agentes.
        """
        self.settings = settings
        self.model_config = model_config
        self.agent_preference = agent_preference
        self.dry_run = dry_run
        self.framework = framework
        self.strategy = strategy

        self.scanner = MultiToolScanner(settings)
        self.router = Router(settings)
        self.llm_client = LocalLLMClient(model_config)

        # Pipeline de validação em 4 camadas (methodology Section 3.7.2).
        # Antes de 06/2026 este módulo existia mas NUNCA era executado:
        # patches eram aceitos e creditados sem qualquer verificação.
        from a11y_autofix.validation import ValidationPipeline
        self.validator = ValidationPipeline()

    async def run(
        self,
        targets: list[Path] | list[str],
        wcag_level: str = "WCAG2AA",
        output_dir: Path | None = None,
        on_file_done: Callable | None = None,
        scan_cache: "dict[str, object] | None" = None,
        generate_pdf: bool = False,
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
        files = self._discover_files(targets)
        if not files:
            log.warning("no_files_found", targets=[str(t) for t in targets])
            return []

        # Diretório de screenshots estilo Lighthouse (full-page com destaques
        # + crop por elemento violador). Ativo por padrão quando há output_dir;
        # desativável via settings.capture_screenshots=False.
        screenshots_dir: Path | None = None
        if output_dir and (generate_pdf or self.settings.capture_screenshots):
            screenshots_dir = output_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)

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
        projects = sorted({project_of(f) for f in files})
        log.info(
            "pipeline_start",
            files=len(files),
            projects=len(projects),
            project_ids=projects[:20] + (["…"] if len(projects) > 20 else []),
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
                    scan_result = await self.scanner.scan_file(
                        file, wcag_level, screenshot_dir=screenshots_dir
                    )
                # Escrever no cache com ambas as chaves para lookup consistente
                if scan_cache is not None:
                    scan_cache[str(file)] = scan_result
                    scan_cache[str(file.resolve())] = scan_result

            async with results_lock:
                scanned_count += 1
                # Log por arquivo deixa visível QUAL projeto/arquivo acabou de ser
                # escaneado (vs. só um contador a cada 100). Essencial com dezenas
                # de projetos cheios de index.tsx/Button.tsx homônimos.
                log.info(
                    "file_scanned",
                    target=label_for_path(file),
                    issues=len(scan_result.issues),
                    scanned=scanned_count,
                    total=len(files),
                )
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

        # ── Scans adicionais: mobile e alto contraste ─────────────────────────
        mobile_scan_results: list[ScanResult] = []
        high_contrast_scan_results: list[ScanResult] = []

        if self.settings.scan_mobile or self.settings.scan_high_contrast:
            # Cada scan extra sobe um browser headless + servidor HTTP. Sem
            # limite, asyncio.gather sobre TODOS os arquivos de uma vez abriria
            # centenas de browsers simultâneos — OOM/thrashing num laptop que já
            # roda o LLM na GPU. Reusar scan_sem mantém o mesmo teto do scan
            # principal (max_concurrent_scans).
            async def _extra_scan(coro):
                async with scan_sem:
                    return await coro

            extra_tasks = []
            for r in all_results:
                if self.settings.scan_mobile:
                    extra_tasks.append(
                        _extra_scan(self.scanner.scan_file_mobile(r.file, wcag_level))
                    )
                if self.settings.scan_high_contrast:
                    extra_tasks.append(
                        _extra_scan(self.scanner.scan_file_high_contrast(r.file, wcag_level))
                    )

            if extra_tasks:
                extra_raw = await asyncio.gather(*extra_tasks, return_exceptions=True)
                for item in extra_raw:
                    if isinstance(item, ScanResult):
                        if item.scan_mode == ScanMode.MOBILE:
                            mobile_scan_results.append(item)
                        elif item.scan_mode == ScanMode.HIGH_CONTRAST:
                            high_contrast_scan_results.append(item)

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
                mobile_scan_results=mobile_scan_results,
                high_contrast_scan_results=high_contrast_scan_results,
                generate_pdf=generate_pdf,
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
        # Feedback de auto-correção: rejeição da tentativa anterior repassada ao
        # agente na próxima. Sem isso, a temperatura baixa reproduz o mesmo patch
        # rejeitado e as tentativas 2-3 são desperdiçadas.
        previous_attempt: dict | None = None

        for attempt_num in range(1, self.settings.max_retries_per_agent + 1):
            pending_issues = [i for i in task.issues if i.issue_id not in resolved_issue_ids]
            attempt_task = AgentTask(
                file=task.file,
                file_content=current_content,
                issues=pending_issues,
                wcag_level=task.wcag_level,
                context={"previous_attempt": previous_attempt} if previous_attempt else {},
            )

            if not attempt_task.issues:
                break

            log.info(
                "fix_attempt",
                target=label_for_path(scan.file),
                attempt=attempt_num,
                agent=decision.agent,
                issues=len(attempt_task.issues),
            )

            patch = await agent.run(attempt_task)

            # ── Validação em 4 camadas (Section 3.7.2) ────────────────────
            # Um patch só é aceito se passar nas 4 camadas. Rejeição em
            # qualquer camada descarta o patch (arquivo NÃO é modificado)
            # e a tentativa é registrada como falha — o loop tenta de novo.
            # Rejeição na Camada 2 = regressão funcional → métrica ρ (H5),
            # contada via prefixo "functional_regression:" no error.
            validation_passed: bool | None = None
            validation_layer: int | None = None
            patch_error = patch.error
            patch_accepted = bool(patch.success and patch.new_content)

            if patch_accepted:
                validation = self.validator.validate(
                    patched_content=patch.new_content,
                    original_content=current_content,
                    issues=pending_issues,
                    file_id=scan.file.name,
                    model_id=self.model_config.model_id,
                    strategy=self.strategy,
                )
                validation_passed = validation.passed
                validation_layer = validation.rejected_at_layer
                if not validation.passed:
                    patch_accepted = False
                    patch_error = validation.failure_reason

            attempt = FixAttempt(
                attempt_number=attempt_num,
                agent=decision.agent,
                model=self.model_config.model_id,
                timestamp=datetime.now(tz=timezone.utc),
                success=patch_accepted,
                diff=patch.diff if patch_accepted else "",
                new_content=patch.new_content if patch_accepted else "",
                tokens_used=patch.tokens_used,
                time_seconds=patch.time_seconds,
                error=patch_error,
                validation_passed=validation_passed,
                validation_rejected_layer=validation_layer,
            )
            attempts.append(attempt)

            if not patch_accepted:
                # Guardar a causa da rejeição para o agente se auto-corrigir na
                # próxima tentativa (excerpt = saída rejeitada, se houver).
                excerpt = (patch.new_content or patch.diff or "")[:400]
                previous_attempt = {
                    "error": patch_error,
                    "layer": validation_layer,
                    "excerpt": excerpt,
                }

            if patch_accepted:
                previous_attempt = None  # tentativa aceita: sem feedback de erro
                scan.file.write_text(patch.new_content, encoding="utf-8")
                current_content = patch.new_content

                if self.settings.verify_fixes_by_rescan:
                    # ── Camada 3 real: re-scan browser-based ──────────────
                    # Credita como corrigidos APENAS os issues que de fato
                    # desapareceram do re-scan. issue_id é content-addressed
                    # (file:selector:wcag:type), estável entre scans.
                    #
                    # Guarda net-delta por critério WCAG: se o patch muda a
                    # estrutura do DOM, o seletor muda e o id antigo "some"
                    # mesmo que uma violação equivalente persista com novo
                    # id. Por isso o crédito por critério é limitado ao
                    # desaparecimento LÍQUIDO de issues daquele critério
                    # (antes − depois), nunca apenas ao sumiço do id.
                    try:
                        rescan = await self.scanner.scan_file(scan.file, wcag_level)
                        remaining_ids = {i.issue_id for i in rescan.issues}

                        def _crit(issue) -> str:
                            return issue.wcag_criteria or f"type:{issue.issue_type.value}"

                        before_by_crit: dict[str, int] = {}
                        for i in pending_issues:
                            before_by_crit[_crit(i)] = before_by_crit.get(_crit(i), 0) + 1
                        after_by_crit: dict[str, int] = {}
                        for i in rescan.issues:
                            after_by_crit[_crit(i)] = after_by_crit.get(_crit(i), 0) + 1

                        # Orçamento de crédito por critério = redução líquida
                        credit_budget = {
                            c: max(0, n - after_by_crit.get(c, 0))
                            for c, n in before_by_crit.items()
                        }

                        newly_resolved = []
                        id_vanished_but_uncredited = 0
                        for issue in pending_issues:
                            if issue.issue_id in remaining_ids:
                                continue  # ainda presente: não corrigido
                            c = _crit(issue)
                            if credit_budget.get(c, 0) > 0:
                                credit_budget[c] -= 1
                                newly_resolved.append(issue)
                            else:
                                # id sumiu mas o total do critério não caiu →
                                # violação provavelmente migrou de seletor
                                id_vanished_but_uncredited += 1

                        for issue in newly_resolved:
                            resolved_issue_ids.add(issue.issue_id)
                        log.info(
                            "rescan_verification",
                            file=scan.file.name,
                            targeted=len(pending_issues),
                            verified_fixed=len(newly_resolved),
                            still_present=len(pending_issues) - len(newly_resolved),
                            selector_migrations=id_vanished_but_uncredited,
                        )
                    except Exception as e:
                        # Re-scan falhou: NÃO creditar (conservador) —
                        # o loop tentará os issues novamente
                        log.warning(
                            "rescan_verification_failed",
                            file=scan.file.name,
                            error=str(e)[:200],
                        )
                else:
                    # Crédito otimista (comportamento pré-06/2026):
                    # todos os issues alvo do patch validado
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
        """Instancia o agente pelo nome, propagando a estratégia de prompting."""
        if agent_name == "openhands":
            return OpenHandsAgent(self.llm_client, strategy=self.strategy)
        elif agent_name == "swe-agent":
            return SWEAgent(self.llm_client, strategy=self.strategy)
        else:
            return DirectLLMAgent(self.llm_client, strategy=self.strategy)

    def _discover_files(self, targets: list[Path] | list[str]) -> list[Path]:
        """
        Descobre arquivos a partir de targets, respeitando self.framework.

        - 'react'   → find_react_files() para cada target
        - 'angular' → find_angular_components() para cada target
        - 'auto'    → ambos, deduplicados (útil para projetos híbridos)
        """
        files: list[Path] = []
        for target in targets:
            path = Path(target) if isinstance(target, str) else target
            if self.framework in ("react", "auto"):
                files.extend(find_react_files(path))
            if self.framework in ("angular", "auto"):
                files.extend(find_angular_components(path))

        # Deduplicar mantendo ordem de descoberta
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
        mobile_scan_results: "list[ScanResult] | None" = None,
        high_contrast_scan_results: "list[ScanResult] | None" = None,
        generate_pdf: bool = False,
    ) -> None:
        """Gera relatórios JSON e HTML (desktop + mobile separado + alto contraste).

        O PDF só é gerado quando generate_pdf=True (flag --pdf no CLI).
        """
        from a11y_autofix.reporter.csv_reporter import CSVReporter
        from a11y_autofix.reporter.html_reporter import HTMLReporter
        from a11y_autofix.reporter.json_reporter import JSONReporter
        import json

        typed_scans = [s for s in scan_results if isinstance(s, ScanResult)]

        json_reporter = JSONReporter(self.settings)
        html_reporter = HTMLReporter()
        csv_reporter = CSVReporter()

        def _maybe_pdf(
            data: dict,
            out_dir: Path,
            filename: str = "accessibility_report.pdf",
        ) -> None:
            if not generate_pdf:
                return
            try:
                from a11y_autofix.reporter.pdf_reporter import PDFReporter
                PDFReporter().generate(report_data=data, output_dir=out_dir, filename=filename)
            except Exception as exc:
                log.warning("pdf_report_failed", filename=filename, error=str(exc)[:200])

        # ── Relatório desktop principal ───────────────────────────────────────
        json_path = json_reporter.generate(
            scan_results=typed_scans,
            fix_results=fix_results,
            output_dir=output_dir,
            wcag_level=wcag_level,
            model_name=self.model_config.model_id,
        )
        report_data = json.loads(json_path.read_text(encoding="utf-8"))
        html_reporter.generate(report_data=report_data, output_dir=output_dir)
        csv_reporter.generate(report_data=report_data, output_dir=output_dir)
        _maybe_pdf(report_data, output_dir)

        # ── Relatório mobile separado (se houver issues) ──────────────────────
        mobile_scans = [s for s in (mobile_scan_results or []) if s.has_issues]
        if mobile_scans:
            mobile_dir = output_dir / "mobile"
            mobile_json_path = json_reporter.generate(
                scan_results=mobile_scans,
                fix_results=[],
                output_dir=mobile_dir,
                wcag_level=wcag_level,
                model_name=self.model_config.model_id,
                report_label="mobile",
            )
            mobile_data = json.loads(mobile_json_path.read_text(encoding="utf-8"))
            html_reporter.generate(report_data=mobile_data, output_dir=mobile_dir)
            csv_reporter.generate(report_data=mobile_data, output_dir=mobile_dir)
            _maybe_pdf(mobile_data, mobile_dir, "accessibility_report_mobile.pdf")
            log.info("mobile_report_generated", files=len(mobile_scans), dir=str(mobile_dir))
        else:
            log.info("mobile_report_skipped", reason="no mobile-specific issues found")

        # ── Relatório alto contraste (se houver issues) ───────────────────────
        hc_scans = [s for s in (high_contrast_scan_results or []) if s.has_issues]
        if hc_scans:
            hc_dir = output_dir / "high_contrast"
            hc_json_path = json_reporter.generate(
                scan_results=hc_scans,
                fix_results=[],
                output_dir=hc_dir,
                wcag_level=wcag_level,
                model_name=self.model_config.model_id,
                report_label="high_contrast",
            )
            hc_data = json.loads(hc_json_path.read_text(encoding="utf-8"))
            html_reporter.generate(report_data=hc_data, output_dir=hc_dir)
            csv_reporter.generate(report_data=hc_data, output_dir=hc_dir)
            _maybe_pdf(hc_data, hc_dir, "accessibility_report_high_contrast.pdf")
            log.info("high_contrast_report_generated", files=len(hc_scans), dir=str(hc_dir))
