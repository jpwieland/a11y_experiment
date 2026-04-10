"""Agente DirectLLM — fallback minimalista usando apenas o LLM."""

from __future__ import annotations

import structlog

from a11y_autofix.agents.base import BaseAgent
from a11y_autofix.agents.prompts import build_direct_llm_prompt, system_prompt_direct
from a11y_autofix.config import AgentTask, PatchResult
from a11y_autofix.utils.git import get_unified_diff

log = structlog.get_logger(__name__)


class DirectLLMAgent(BaseAgent):
    """
    Agente minimalista que usa apenas o LLM sem ferramentas externas.

    Estratégia:
    1. Envia prompt com código e issues
    2. Extrai bloco de código da resposta
    3. Calcula diff e retorna PatchResult

    Usado como fallback quando OpenHands e SWE-agent não estão disponíveis.
    """

    def name(self) -> str:
        """Retorna nome do agente."""
        return "direct-llm"

    async def run(self, task: AgentTask) -> PatchResult:
        """
        Executa correção via LLM diretamente.

        Args:
            task: Tarefa com arquivo e issues.

        Returns:
            PatchResult com resultado da correção.
        """
        log.info(
            "direct_llm_start",
            file=task.file.name,
            issues=len(task.issues),
            model=self.llm.config.model_id,
        )

        prompt = build_direct_llm_prompt(task)

        try:
            response, metrics = await self.llm.complete_with_metrics(
                system=system_prompt_direct(),
                user=prompt,
            )
        except Exception as e:
            log.error("direct_llm_failed", error=str(e))
            return PatchResult(success=False, error=str(e))

        # Extrair código da resposta
        new_content = self.extract_code_block(response)

        if not new_content or not self.validate_tsx_basic(new_content):
            log.warning(
                "direct_llm_no_code",
                model=self.llm.config.model_id,
                response_len=len(response),
            )
            return PatchResult(
                success=False,
                error="LLM did not return a valid code block",
                tokens_used=metrics.get("tokens_total"),
                tokens_prompt=metrics.get("tokens_prompt"),
                tokens_completion=metrics.get("tokens_completion"),
                time_seconds=metrics.get("time_seconds", 0.0),
            )

        diff = get_unified_diff(task.file_content, new_content, task.file.name)

        log.info(
            "direct_llm_success",
            file=task.file.name,
            diff_lines=len(diff.splitlines()),
            time_s=metrics.get("time_seconds", 0.0),
            tokens_prompt=metrics.get("tokens_prompt"),
            tokens_completion=metrics.get("tokens_completion"),
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
