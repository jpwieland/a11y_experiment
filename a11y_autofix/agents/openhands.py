"""Agente OpenHands — para correções complexas com contexto amplo."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import structlog

from a11y_autofix.agents.base import BaseAgent
from a11y_autofix.agents.prompts import system_prompt_openhands
from a11y_autofix.config import AgentTask, PatchResult
from a11y_autofix.utils.git import get_unified_diff

log = structlog.get_logger(__name__)


class OpenHandsAgent(BaseAgent):
    """
    Agente de "contexto amplo": prompting de arquivo completo.

    NOTA DE VALIDADE (06/2026): o CLI do OpenHands raramente está
    instalado no ambiente experimental; na prática a execução cai no
    fallback `_via_llm_direct` (template de 6 componentes + system
    prompt de contexto amplo). Nos experimentos, esta condição é
    operacionalizada como "LLM com prompt de contexto amplo" — uma
    diferença de PROMPTING, não de scaffolding agêntico real.
    Reporte-a assim na metodologia.

    Ideal para: issues complexos que requerem contexto amplo do arquivo inteiro.
    OpenHands aceita OPENAI_BASE_URL para apontar para Ollama/vLLM/etc.
    """

    def name(self) -> str:
        """Retorna nome do agente."""
        return "openhands"

    async def run(self, task: AgentTask) -> PatchResult:
        """
        Executa correção com OpenHands ou LLM direto.

        Args:
            task: Tarefa com arquivo e issues.

        Returns:
            PatchResult com resultado.
        """
        log.info(
            "openhands_start",
            file=task.file.name,
            issues=len(task.issues),
            model=self.llm.config.model_id,
        )

        # Tenta OpenHands CLI
        oh_cli = shutil.which("openhands") or shutil.which("oh")
        if oh_cli:
            try:
                result = await self._via_openhands_cli(task, oh_cli)
                if result.success:
                    return result
            except Exception as e:
                log.warning("openhands_cli_failed", error=str(e))

        # Fallback: LLM direto
        return await self._via_llm_direct(task)

    async def _via_openhands_cli(self, task: AgentTask, cli_path: str) -> PatchResult:
        """
        Executa via OpenHands CLI configurado com LLM local.

        OpenHands suporta OPENAI_BASE_URL para redirecionar para Ollama/vLLM.

        Args:
            task: Tarefa de correção.
            cli_path: Caminho para o CLI do OpenHands.

        Returns:
            PatchResult da execução.
        """
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            work_file = Path(tmpdir) / task.file.name
            work_file.write_text(task.file_content, encoding="utf-8")

            issues_desc = "\n".join(
                f"- WCAG {i.wcag_criteria or 'N/A'} [{i.issue_type.value.upper()}]: "
                f"{i.message} (selector: {i.selector})"
                for i in task.issues
            )

            task_desc = (
                f"Fix these accessibility issues in the file {task.file.name}:\n"
                f"{issues_desc}\n\n"
                f"Preserve all existing functionality. Use WCAG {task.wcag_level}."
            )

            # OpenHands usa OPENAI_BASE_URL para backends locais
            base_url = getattr(self.llm, "_base_url", "http://localhost:11434/v1")
            env = {
                **os.environ,
                "OPENAI_BASE_URL": base_url,
                "OPENAI_API_KEY": self.llm.config.api_key,
                "LLM_MODEL": f"openai/{self.llm.config.model_id}",
                "SANDBOX_VOLUMES": f"{tmpdir}:{tmpdir}",
            }

            proc = await asyncio.create_subprocess_exec(
                cli_path,
                "--task", task_desc,
                "--directory", tmpdir,
                "--no-gui",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                _, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("OpenHands CLI timeout after 180s")

            # Ler arquivo modificado
            if work_file.exists():
                new_content = work_file.read_text(encoding="utf-8")
                if new_content != task.file_content:
                    diff = get_unified_diff(task.file_content, new_content, task.file.name)
                    return PatchResult(success=True, new_content=new_content, diff=diff)

        raise RuntimeError("OpenHands did not modify the file")

    async def _via_llm_direct(self, task: AgentTask) -> PatchResult:
        """
        LLM direto com prompt de contexto amplo.

        Args:
            task: Tarefa de correção.

        Returns:
            PatchResult da execução.
        """
        # Template canônico de 6 componentes; a estratégia (IV2) controla
        # Componente 5 (few-shot) e instrução CoT. O system prompt mantém a
        # identidade de contexto amplo do scaffolding OpenHands.
        from a11y_autofix.agents.prompts import PromptBuilder

        system_prompt = system_prompt_openhands()
        prompt = PromptBuilder().build(
            issues=task.issues,
            file=task.file,
            file_content=task.file_content,
            strategy=self.strategy,
            wcag_level=task.wcag_level,
            previous_attempt=task.context.get("previous_attempt"),
        )
        attempt_num = len(task.context.get("previous_attempt") or []) + 1

        try:
            response, metrics = await self.llm.complete_with_metrics(
                system=system_prompt,
                user=prompt,
            )
        except Exception as e:
            log.error("openhands_llm_failed", error=str(e))
            if self.prompt_logger:
                self.prompt_logger.append(
                    file=task.file.name, attempt=attempt_num,
                    system_prompt=system_prompt, user_prompt=prompt,
                    response="", tokens_prompt=None, tokens_completion=None,
                    time_s=None, success=False, extra={"error": str(e)},
                )
            return PatchResult(success=False, error=str(e))

        new_content = self.extract_code_block(response)

        if self.prompt_logger:
            self.prompt_logger.append(
                file=task.file.name, attempt=attempt_num,
                system_prompt=system_prompt, user_prompt=prompt,
                response=response,
                tokens_prompt=metrics.get("tokens_prompt"),
                tokens_completion=metrics.get("tokens_completion"),
                time_s=metrics.get("time_seconds"),
                success=bool(new_content and self.validate_tsx_basic(new_content)),
            )

        if not new_content or not self.validate_tsx_basic(new_content):
            log.warning(
                "openhands_no_code",
                response_preview=response[:300],
                model=self.llm.config.model_id,
            )
            return PatchResult(
                success=False,
                error="OpenHands: no valid code block found in LLM response",
                tokens_used=metrics.get("tokens_total"),
                tokens_prompt=metrics.get("tokens_prompt"),
                tokens_completion=metrics.get("tokens_completion"),
                time_seconds=metrics.get("time_seconds", 0.0),
            )

        diff = get_unified_diff(task.file_content, new_content, task.file.name)

        log.info(
            "openhands_llm_success",
            file=task.file.name,
            diff_lines=len(diff.splitlines()),
            time_s=f"{metrics.get('time_seconds', 0.0):.2f}",
            tokens=metrics.get("tokens_total"),
        )

        return PatchResult(
            success=True,
            new_content=new_content,
            diff=diff,
            tokens_used=metrics.get("tokens_total"),
            tokens_prompt=metrics.get("tokens_prompt"),
            tokens_completion=metrics.get("tokens_completion"),
            time_seconds=metrics.get("time_seconds", 0.0),
        )
