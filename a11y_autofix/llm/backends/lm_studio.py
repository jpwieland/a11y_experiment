"""Backend LM Studio — especialização do LocalLLMClient para LM Studio."""

from __future__ import annotations

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.client import LocalLLMClient


def create_lm_studio_client(model_id: str, **kwargs: object) -> LocalLLMClient:
    """
    Cria um cliente LocalLLMClient pré-configurado para LM Studio.

    Args:
        model_id: ID do modelo, ex: 'TheBloke/CodeLlama-13B-Instruct-GGUF'.
        **kwargs: Overrides de ModelConfig.

    Returns:
        LocalLLMClient configurado para LM Studio.
    """
    config = ModelConfig(
        name=model_id,
        backend=LLMBackend.LM_STUDIO,
        model_id=model_id,
        base_url="http://localhost:1234/v1",
        **kwargs,  # type: ignore[arg-type]
    )
    return LocalLLMClient(config)
