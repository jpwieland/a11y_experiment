"""Interface abstrata para clientes LLM."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from a11y_autofix.config import ModelConfig


class BaseLLMClient(ABC):
    """
    Interface abstrata para qualquer cliente LLM.

    Todos os backends (Ollama, LM Studio, vLLM, etc.) implementam esta interface,
    garantindo que os agentes possam trocar de backend de forma transparente.
    """

    def __init__(self, config: ModelConfig) -> None:
        """
        Args:
            config: Configuração do modelo a usar.
        """
        self.config = config

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Completa uma conversa (single-turn).

        Args:
            system: System prompt com instruções ao modelo.
            user: Mensagem do usuário.
            temperature: Override de temperatura (usa config se None).
            max_tokens: Override de max_tokens (usa config se None).

        Returns:
            Texto de resposta do modelo.
        """
        ...

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """
        Verifica se o modelo está acessível e funcionando.

        Returns:
            Tupla (ok, mensagem). ok=True se saudável.
        """
        ...

    @abstractmethod
    async def get_model_info(self) -> dict[str, Any]:
        """
        Obtém informações sobre o modelo (versão, contexto, etc).

        Returns:
            Dicionário com informações do modelo.
        """
        ...

    async def complete_with_metrics(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        Completa uma conversa E retorna métricas de performance.

        O comportamento padrão mede o tempo e retorna métricas de tokens nulas.
        Backends que retornam usage podem sobrescrever este método.

        Args:
            system: System prompt.
            user: Mensagem do usuário.
            temperature: Override de temperatura.
            max_tokens: Override de max_tokens.

        Returns:
            Tupla (resposta, métricas). Métricas incluem: time_seconds,
            tokens_prompt, tokens_completion, tokens_total.
        """
        t0 = time.perf_counter()
        response = await self.complete(system, user, temperature, max_tokens)
        elapsed = time.perf_counter() - t0

        metrics: dict[str, Any] = {
            "time_seconds": elapsed,
            "tokens_prompt": None,
            "tokens_completion": None,
            "tokens_total": None,
        }
        return response, metrics
