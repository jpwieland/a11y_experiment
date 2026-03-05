"""
Cliente LLM genérico para qualquer backend OpenAI-compatible.

Funciona com Ollama, LM Studio, vLLM, llama.cpp, Jan, LocalAI, etc.
NÃO envia dados para a OpenAI — usa o protocolo HTTP deles apenas.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.base import BaseLLMClient

log = structlog.get_logger(__name__)

# URLs padrão por backend
_DEFAULT_URLS: dict[LLMBackend, str] = {
    LLMBackend.OLLAMA: "http://localhost:11434/v1",
    LLMBackend.LM_STUDIO: "http://localhost:1234/v1",
    LLMBackend.VLLM: "http://localhost:8000/v1",
    LLMBackend.LLAMACPP: "http://localhost:8080/v1",
    LLMBackend.JAN: "http://localhost:1337/v1",
    LLMBackend.LOCALAI: "http://localhost:8080/v1",
    LLMBackend.CUSTOM: "http://localhost:11434/v1",
}


class LocalLLMClient(BaseLLMClient):
    """
    Cliente genérico para qualquer backend OpenAI-compatible.

    Suporta todos os backends locais usando o endpoint /v1/chat/completions.
    Não há dependência de SDK externo — usa httpx diretamente para
    controle total de timeout e retry.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__(config)
        self._base_url = self._resolve_base_url()

    def _resolve_base_url(self) -> str:
        """Resolve URL base do backend."""
        if self.config.base_url:
            return self.config.base_url.rstrip("/")
        return _DEFAULT_URLS.get(self.config.backend, "http://localhost:11434/v1")

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Envia requisição de completação para o backend LLM.

        Args:
            system: System prompt.
            user: Mensagem do usuário.
            temperature: Override (usa config.temperature se None).
            max_tokens: Override (usa config.max_tokens se None).

        Returns:
            Texto de resposta do modelo.

        Raises:
            RuntimeError: Se o backend estiver inacessível ou retornar erro.
        """
        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens or self.config.max_tokens

        payload = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temp,
            "max_tokens": tokens,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return str(content)

            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"Cannot connect to LLM at {self._base_url}.\n"
                    f"Backend: {self.config.backend.value}\n"
                    f"Model: {self.config.model_id}\n"
                    f"Make sure the server is running.\n"
                    f"Original error: {e}"
                ) from e

            except httpx.TimeoutException as e:
                raise RuntimeError(
                    f"LLM timeout after {self.config.timeout}s.\n"
                    f"Model: {self.config.model_id}\n"
                    f"Try a smaller model or increase AGENT_TIMEOUT in .env.\n"
                    f"Original error: {e}"
                ) from e

            except (KeyError, IndexError) as e:
                raise RuntimeError(
                    f"Unexpected LLM response format: {data}\n"
                    f"Original error: {e}"
                ) from e

    async def complete_with_metrics(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        Completa e retorna métricas (tokens, tempo).

        Extrai usage da resposta quando disponível (vLLM, Ollama retornam isso).
        """
        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens or self.config.max_tokens

        payload = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temp,
            "max_tokens": tokens,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        elapsed = time.perf_counter() - t0
        content = str(data["choices"][0]["message"]["content"])

        usage = data.get("usage", {})
        metrics: dict[str, Any] = {
            "time_seconds": elapsed,
            "tokens_prompt": usage.get("prompt_tokens"),
            "tokens_completion": usage.get("completion_tokens"),
            "tokens_total": usage.get("total_tokens"),
        }

        log.debug(
            "llm_complete",
            model=self.config.model_id,
            time_s=f"{elapsed:.2f}",
            tokens=metrics["tokens_total"],
        )
        return content, metrics

    async def health_check(self) -> tuple[bool, str]:
        """
        Verifica se o modelo está acessível via /v1/models.

        Returns:
            Tupla (ok, mensagem).
        """
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    model_ids = [m.get("id", "") for m in data.get("data", [])]

                    if self.config.model_id in model_ids:
                        return True, f"Model '{self.config.model_id}' is ready"

                    # Ollama: model IDs podem ser prefixos
                    for mid in model_ids:
                        if self.config.model_id in mid or mid in self.config.model_id:
                            return True, f"Model '{mid}' found (requested: '{self.config.model_id}')"

                    top5 = ", ".join(model_ids[:5])
                    return False, (
                        f"Model '{self.config.model_id}' not found.\n"
                        f"Available: {top5}"
                    )

                return False, f"Server returned HTTP {resp.status_code}"

            except httpx.ConnectError:
                return False, f"Cannot connect to {self._base_url}"
            except Exception as e:
                return False, str(e)

    async def get_model_info(self) -> dict[str, Any]:
        """
        Obtém informações do modelo via /v1/models/{model_id}.

        Fallback para config conhecida se o endpoint não existir.
        """
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get(
                    f"{self._base_url}/models/{self.config.model_id}",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                )
                if resp.status_code == 200:
                    return dict(resp.json())
            except Exception:
                pass

        return {
            "id": self.config.model_id,
            "backend": self.config.backend.value,
            "family": self.config.family,
            "size": self.config.size,
            "quantization": self.config.quantization,
            "base_url": self._base_url,
        }
