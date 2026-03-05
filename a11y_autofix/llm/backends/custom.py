"""Backend customizado — para qualquer servidor OpenAI-compatible não listado."""

from __future__ import annotations

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.client import LocalLLMClient


def create_custom_client(
    model_id: str,
    base_url: str,
    api_key: str = "local",
    **kwargs: object,
) -> LocalLLMClient:
    """
    Cria um cliente para qualquer servidor OpenAI-compatible.

    Ideal para backends não listados explicitamente (Jan, LocalAI, TabbyAPI, etc.)
    ou servidores remotos que seguem o protocolo OpenAI.

    Args:
        model_id: ID do modelo no servidor.
        base_url: URL base do servidor (ex: 'http://meu-servidor:8080/v1').
        api_key: Chave de API (default: 'local').
        **kwargs: Overrides adicionais de ModelConfig.

    Returns:
        LocalLLMClient configurado para o servidor customizado.
    """
    config = ModelConfig(
        name=model_id,
        backend=LLMBackend.CUSTOM,
        model_id=model_id,
        base_url=base_url,
        api_key=api_key,
        **kwargs,  # type: ignore[arg-type]
    )
    return LocalLLMClient(config)
