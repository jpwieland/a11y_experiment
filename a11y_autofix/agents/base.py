"""Interface base para agentes de correção de acessibilidade."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from a11y_autofix.config import AgentTask, PatchResult

if TYPE_CHECKING:
    from a11y_autofix.llm.base import BaseLLMClient


class BaseAgent(ABC):
    """
    Interface abstrata para agentes de correção.

    Cada agente (OpenHands, SWE-agent, DirectLLM) implementa uma estratégia
    diferente de geração e aplicação de correções.
    """

    def __init__(self, llm_client: "BaseLLMClient") -> None:
        """
        Args:
            llm_client: Cliente LLM a usar para geração de código.
        """
        self.llm = llm_client

    @abstractmethod
    async def run(self, task: AgentTask) -> PatchResult:
        """
        Executa correção de issues de acessibilidade.

        Args:
            task: Tarefa com arquivo, conteúdo e issues a corrigir.

        Returns:
            PatchResult com success, diff, new_content e métricas.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Retorna nome do agente (para logging e relatórios)."""
        ...

    # ─── Helpers compartilhados ───────────────────────────────────────────────

    def extract_code_block(self, text: str) -> str | None:
        """
        Extrai código de um bloco ```tsx, ```jsx, ```ts, ```js, ou ``` genérico.

        Args:
            text: Resposta do LLM contendo código.

        Returns:
            Código extraído ou None se não encontrado.
        """
        patterns = [
            r'```(?:tsx|jsx|typescript|javascript|ts|js)\n(.*?)```',
            r'```\n(.*?)```',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()
        return None

    def apply_surgical_patches(self, response: str, original: str) -> str | None:
        """
        Aplica patches cirúrgicos no formato FIND/REPLACE.

        Espera blocos no formato:
            PATCH N:
            FIND: `<texto>`
            REPLACE: `<texto>`

        Args:
            response: Resposta do LLM com patches.
            original: Conteúdo original do arquivo.

        Returns:
            Conteúdo patcheado ou None se nenhum patch foi encontrado.
        """
        patch_pattern = re.compile(
            r'PATCH\s+\d+:?\s*\n'
            r'FIND:\s*`(.*?)`\s*\n'
            r'REPLACE:\s*`(.*?)`',
            re.DOTALL | re.IGNORECASE,
        )

        patches = patch_pattern.findall(response)
        if not patches:
            return None

        result = original
        for find_text, replace_text in patches:
            find_clean = find_text.strip()
            replace_clean = replace_text.strip()
            if find_clean and find_clean in result:
                result = result.replace(find_clean, replace_clean, 1)

        return result if result != original else None

    def validate_tsx_basic(self, code: str) -> bool:
        """
        Valida que o código tem estrutura básica de TSX válida.

        Verificação rápida para detectar respostas malformadas.

        Args:
            code: Código a validar.

        Returns:
            True se parece válido.
        """
        if not code or len(code) < 10:
            return False
        # Deve ter pelo menos um JSX element ou função
        has_jsx = bool(re.search(r'<[A-Za-z]', code))
        has_function = bool(re.search(r'(?:function|const|class)\s+\w+', code))
        return has_jsx or has_function
