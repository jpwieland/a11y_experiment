"""Backend llama.cpp server — especialização do LocalLLMClient para llama.cpp."""

from __future__ import annotations

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.client import LocalLLMClient


def create_llamacpp_client(
    model_id: str = "local-model",
    base_url: str = "http://localhost:8080/v1",
    **kwargs: object,
) -> LocalLLMClient:
    """
    Cria um cliente LocalLLMClient pré-configurado para llama.cpp server.

    Args:
        model_id: ID do modelo (llama.cpp usa 'local-model' por padrão).
        base_url: URL do servidor llama.cpp.
        **kwargs: Overrides de ModelConfig.

    Returns:
        LocalLLMClient configurado para llama.cpp.
    """
    config = ModelConfig(
        name=model_id,
        backend=LLMBackend.LLAMACPP,
        model_id=model_id,
        base_url=base_url,
        **kwargs,  # type: ignore[arg-type]
    )
    return LocalLLMClient(config)
