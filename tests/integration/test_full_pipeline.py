"""
Testes de integração para o pipeline completo.

Esses testes requerem modelos LLM rodando localmente.
Use o marcador @pytest.mark.integration para pulá-los por padrão.
"""

from __future__ import annotations

import pytest
from pathlib import Path


SAMPLE_COMPONENT = """
import React from 'react';

function Button({ onClick, label }) {
  return (
    <button onClick={onClick} style={{ backgroundColor: '#yellow', color: '#white' }}>
      {label}
    </button>
  );
}

export default Button;
"""


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """Cria arquivo de componente React de teste."""
    file = tmp_path / "Button.tsx"
    file.write_text(SAMPLE_COMPONENT, encoding="utf-8")
    return file


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scan_only_no_llm_needed(sample_file: Path) -> None:
    """
    Scan funciona sem LLM (dry-run).

    Requer: pa11y ou axe-core instalados.
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.scanner.orchestrator import MultiToolScanner

    settings = Settings()
    scanner = MultiToolScanner(settings)

    result = await scanner.scan_file(sample_file, "WCAG2AA")

    assert result.file == sample_file
    assert result.file_hash.startswith("sha256:")
    assert result.scan_time > 0

    # Não verificamos o número de issues pois depende das ferramentas instaladas


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pipeline_dry_run(sample_file: Path, tmp_path: Path) -> None:
    """Pipeline em modo dry-run não modifica arquivos."""
    from a11y_autofix.config import AgentType, LLMBackend, ModelConfig, Settings
    from a11y_autofix.pipeline import Pipeline

    settings = Settings()
    model_config = ModelConfig(
        name="test",
        backend=LLMBackend.OLLAMA,
        model_id="qwen2.5-coder:7b",
    )

    pipeline = Pipeline(
        settings=settings,
        model_config=model_config,
        agent_preference=AgentType.AUTO,
        dry_run=True,
    )

    original_content = sample_file.read_text()
    results = await pipeline.run(targets=[sample_file], wcag_level="WCAG2AA")

    # Dry run não modifica arquivo
    assert sample_file.read_text() == original_content
    assert len(results) == 1
