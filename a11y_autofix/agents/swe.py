"""Agente SWE — correções cirúrgicas e localizadas."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import structlog

from a11y_autofix.agents.base import BaseAgent
from a11y_autofix.agents.prompts import build_swe_prompt, system_prompt_swe
from a11y_autofix.config import AgentTask, PatchResult
from a11y_autofix.utils.git import get_unified_diff

log = structlog.get_logger(__name__)


class SWEAgent(BaseAgent):
    """
    Agente SWE para correções cirúrgicas (aria-label, alt text, etc.).

    Estratégia chain:
    1. SWE-agent CLI subprocess → LLM local (se instalado)
    2. LLM direto com prompting cirúrgico (FIND/REPLACE blocks)

    Ideal para: issues simples e localizados (ARIA, labels, alt-text).
    """

    def name(self) -> str:
        """Retorna nome do agente."""
        return "swe-agent"

    async def run(self, task: AgentTask) -> PatchResult:
        """
        Executa correção com SWE-agent ou LLM cirúrgico.

        Args:
            task: Tarefa com arquivo e issues.

        Returns:
            PatchResult com resultado.
        """
        log.info(
            "swe_agent_start",
            file=task.file.name,
            issues=len(task.issues),
            model=self.llm.config.model_id,
        )

        # Tenta SWE-agent CLI
        swe_cli = shutil.which("sweagent") or shutil.which("swe-agent")
        if swe_cli:
            try:
                result = await self._via_swe_cli(task, swe_cli)
                if result.success:
                    return result
            except Exception as e:
                log.warning("swe_cli_failed", error=str(e))

        # Fallback: LLM direto com prompting cirúrgico
        return await self._via_llm_direct(task)

    async def _via_swe_cli(self, task: AgentTask, cli_path: str) -> PatchResult:
        """
        Executa via SWE-agent CLI com LLM local configurado.

        Args:
            task: Tarefa de correção.
            cli_path: Caminho para o executável do SWE-agent.

        Returns:
            PatchResult da execução.
        """
        import asyncio

        # Criar arquivo temporário para o SWE-agent trabalhar
        with tempfile.TemporaryDirectory() as tmpdir:
            work_file = Path(tmpdir) / task.file.name
            work_file.write_text(task.file_content, encoding="utf-8")

            # Construir task description
            issues_desc = "\n".join(
                f"- WCAG {i.wcag_criteria or 'N/A'} [{i.issue_type.value}]: "
                f"{i.message} (selector: {i.selector})"
                for i in task.issues
            )

            task_desc = (
                f"Fix accessibility issues in {task.file.name}:\n{issues_desc}"
            )

            env = {
                **os.environ,
                "OPENAI_BASE_URL": self.llm._base_url,  # type: ignore[attr-defined]
                "OPENAI_API_KEY": self.llm.config.api_key,
                "LLM_MODEL": f"openai/{self.llm.config.model_id}",
            }

            proc = await asyncio.create_subprocess_exec(
                cli_path,
                "--task", task_desc,
                "--file", str(work_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("SWE-agent CLI timeout after 120s")

            if proc.returncode != 0:
                raise RuntimeError(f"SWE-agent exited with code {proc.returncode}")

            # Ler arquivo modificado
            if work_file.exists():
                new_content = work_file.read_text(encoding="utf-8")
                diff = get_unified_diff(task.file_content, new_content, task.file.name)
                return PatchResult(success=True, new_content=new_content, diff=diff)

        raise RuntimeError("SWE-agent did not modify the file")

    async def _via_llm_direct(self, task: AgentTask) -> PatchResult:
        """
        LLM direto com formato de PATCH cirúrgico (FIND/REPLACE).

        Args:
            task: Tarefa de correção.

        Returns:
            PatchResult da execução.
        """
        prompt = build_swe_prompt(task)

        try:
            response, metrics = await self.llm.complete_with_metrics(
                system=system_prompt_swe(),
                user=prompt,
            )
        except Exception as e:
            log.error("swe_llm_failed", error=str(e))
            return PatchResult(success=False, error=str(e))

        # Tentar código completo primeiro
        new_content = self.extract_code_block(response)

        # Tentar patches cirúrgicos
        if not new_content:
            new_content = self.apply_surgical_patches(response, task.file_content)

        if not new_content or not self.validate_tsx_basic(new_content):
            return PatchResult(
                success=False,
                error="SWE: no valid code or patches found in response",
                tokens_used=metrics.get("tokens_total"),
                time_seconds=metrics.get("time_seconds", 0.0),
            )

        diff = get_unified_diff(task.file_content, new_content, task.file.name)

        log.info(
            "swe_llm_success",
            file=task.file.name,
            diff_lines=len(diff.splitlines()),
            time_s=f"{metrics.get('time_seconds', 0.0):.2f}",
        )

        return PatchResult(
            success=True,
            new_content=new_content,
            diff=diff,
            tokens_used=metrics.get("tokens_total"),
            time_seconds=metrics.get("time_seconds", 0.0),
        )
