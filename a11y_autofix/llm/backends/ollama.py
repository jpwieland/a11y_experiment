"""Backend Ollama — especialização do LocalLLMClient para Ollama."""

from __future__ import annotations

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.client import LocalLLMClient


def create_ollama_client(model_id: str, **kwargs: object) -> LocalLLMClient:
    """
    Cria um cliente LocalLLMClient pré-configurado para Ollama.

    Args:
        model_id: ID do modelo Ollama, ex: 'qwen2.5-coder:7b'.
        **kwargs: Overrides de ModelConfig (temperature, max_tokens, etc.).

    Returns:
        LocalLLMClient configurado para Ollama.
    """
    config = ModelConfig(
        name=model_id,
        backend=LLMBackend.OLLAMA,
        model_id=model_id,
        base_url="http://localhost:11434/v1",
        **kwargs,  # type: ignore[arg-type]
    )
    return LocalLLMClient(config)
