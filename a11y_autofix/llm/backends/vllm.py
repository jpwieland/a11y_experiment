"""Backend vLLM — especialização do LocalLLMClient para vLLM."""

from __future__ import annotations

from a11y_autofix.config import LLMBackend, ModelConfig
from a11y_autofix.llm.client import LocalLLMClient


def create_vllm_client(
    model_id: str,
    base_url: str = "http://localhost:8000/v1",
    **kwargs: object,
) -> LocalLLMClient:
    """
    Cria um cliente LocalLLMClient pré-configurado para vLLM.

    Args:
        model_id: ID do modelo HuggingFace, ex: 'Qwen/Qwen2.5-Coder-32B-Instruct'.
        base_url: URL do servidor vLLM.
        **kwargs: Overrides de ModelConfig.

    Returns:
        LocalLLMClient configurado para vLLM.
    """
    config = ModelConfig(
        name=model_id.split("/")[-1],
        backend=LLMBackend.VLLM,
        model_id=model_id,
        base_url=base_url,
        max_tokens=16384,  # vLLM geralmente suporta contextos maiores
        **kwargs,  # type: ignore[arg-type]
    )
    return LocalLLMClient(config)
