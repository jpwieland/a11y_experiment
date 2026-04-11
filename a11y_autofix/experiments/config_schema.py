"""Schema de validação para configurações de experimentos YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class ScannerVariant(BaseModel):
    """Variante de configuração para ablation study de scanners."""

    name: str = Field(description="Nome da variante")
    scanners: list[str] = Field(description="Lista de scanners para esta variante")


class ExecutionConfig(BaseModel):
    """
    Execution-level settings for experiment runs.

    Methodology reference: Section 3.1.3 (Experimental Protocol).
    """

    cold_start: bool = Field(
        default=True,
        description=(
            "Restart model server between conditions to prevent implicit "
            "state accumulation (methodology Section 3.1.3)."
        ),
    )
    max_concurrent_models: int = Field(
        default=3,
        ge=1,
        description="Maximum number of model servers running in parallel.",
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Default sampling temperature for all conditions.",
    )


class ExperimentConfig(BaseModel):
    """
    Schema de configuração de um experimento multi-modelo.

    Define os modelos, arquivos, configurações e métricas para um
    experimento comparativo reprodutível.
    """

    name: str = Field(description="Nome do experimento")
    description: str = Field(default="", description="Descrição detalhada")

    # Modelos a testar
    models: list[str] = Field(
        description="Nomes dos modelos do registry ou grupo"
    )

    # Arquivos de teste
    files: list[str] = Field(description="Caminhos ou glob patterns dos arquivos")

    # Configuração do pipeline
    wcag_level: str = Field(default="AA", description="Nível WCAG: A, AA, AAA")
    agents: list[str] = Field(
        default_factory=lambda: ["openhands", "swe-agent"],
        description="Agentes habilitados",
    )
    scanners: list[str] = Field(
        default_factory=lambda: ["pa11y", "axe-core", "playwright+axe"],
        description="Ferramentas de scan habilitadas",
    )

    # Prompting strategy (methodology Section 3.6.2, IV2)
    strategy: Literal["zero-shot", "few-shot", "chain-of-thought"] = Field(
        default="few-shot",
        description=(
            "Prompting strategy: "
            "zero-shot (components 1-4+6), "
            "few-shot (full template 1-6), "
            "chain-of-thought (few-shot + CoT instruction). "
            "Ablation conditions always use few-shot as baseline."
        ),
    )

    # Métricas a coletar
    metrics: list[str] = Field(
        default_factory=lambda: ["sr", "ifr", "mttr", "te", "success_rate", "avg_time", "issues_fixed"],
        description="Métricas a coletar (sr/ifr/mttr/te per methodology Section 3.7.1)",
    )

    # Repetições para estabilidade estatística
    repetitions: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Número de repetições do experimento",
    )

    # Saída
    output_format: list[str] = Field(
        default_factory=lambda: ["json", "html"],
        description="Formatos de saída: json, html, csv",
    )

    # Variantes para ablation study
    variants: list[ScannerVariant] | None = Field(
        default=None,
        description="Variantes para ablation study",
    )

    # Limite de arquivos por projeto (evita explodir com componentes internos de libs)
    max_files_per_project: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Máximo de arquivos por projeto de snapshot. "
            "None = sem limite. "
            "Recomendado: 50-100 para GPUs fracas."
        ),
    )

    # Diretório de saída (lido do YAML top-level ou advanced.output_dir)
    output_dir: str | None = Field(
        default=None,
        description="Diretório de saída relativo à raiz do projeto.",
    )

    # Campos extras do bloco advanced (achatados para facilitar acesso)
    seed: int = Field(default=42, description="Seed para reproducibilidade.")
    save_diffs: bool = Field(default=True)
    typescript_validation: bool = Field(default=False)
    auto_clone_missing_snapshots: bool = Field(default=True)
    checkpoint_per_project: bool = Field(default=True)

    # Execution settings (methodology Section 3.1.3)
    execution: ExecutionConfig = Field(
        default_factory=ExecutionConfig,
        description="Cold-start and concurrency settings.",
    )

    @field_validator("wcag_level")
    @classmethod
    def validate_wcag(cls, v: str) -> str:
        """Valida e normaliza nível WCAG."""
        mapping = {"A": "WCAG2A", "AA": "WCAG2AA", "AAA": "WCAG2AAA"}
        v_upper = v.upper()
        if v_upper in mapping:
            return mapping[v_upper]
        if v_upper in mapping.values():
            return v_upper
        raise ValueError(f"Invalid WCAG level: {v}. Use A, AA, or AAA.")

    def resolve_files(self, base_dir: Path | None = None) -> list[Path]:
        """
        Resolve arquivos a partir dos padrões configurados.

        Se max_files_per_project estiver definido, limita o número de arquivos
        por entrada em self.files (cada entrada representa um projeto/snapshot).
        Arquivos são ordenados deterministicamente e amostrados com seed fixo
        para reproducibilidade entre modelos.

        Args:
            base_dir: Diretório base para resolução de paths relativos.

        Returns:
            Lista de arquivos encontrados.
        """
        import random as _random
        from a11y_autofix.utils.files import find_react_files

        base = base_dir or Path.cwd()
        resolved: list[Path] = []

        rng = _random.Random(self.seed)

        for pattern in self.files:
            path = Path(pattern)
            if not path.is_absolute():
                path = base / pattern
            found = find_react_files(path)

            # Aplicar limite por projeto
            if self.max_files_per_project is not None and len(found) > self.max_files_per_project:
                # Amostragem determinística: shuffle com seed, pegar N primeiros
                sample = list(found)
                rng.shuffle(sample)
                found = sorted(sample[: self.max_files_per_project])

            resolved.extend(found)

        # Deduplicar mantendo ordem
        seen: set[Path] = set()
        unique: list[Path] = []
        for f in resolved:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique


def load_experiment_config(path: Path) -> ExperimentConfig:
    """
    Carrega e valida configuração de experimento de um arquivo YAML.

    Args:
        path: Caminho para o arquivo YAML.

    Returns:
        ExperimentConfig validado.

    Raises:
        FileNotFoundError: Se o arquivo não existir.
        ValueError: Se a configuração for inválida.
    """
    if not path.exists():
        raise FileNotFoundError(f"Experiment config not found: {path}")

    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    # Achatar bloco `advanced` nos campos de nível superior
    # (compatibilidade com YAMLs legados que usam advanced.max_files_per_project etc.)
    advanced: dict[str, Any] = data.pop("advanced", {}) or {}
    for key in (
        "max_files_per_project", "seed", "save_diffs",
        "typescript_validation", "auto_clone_missing_snapshots",
        "checkpoint_per_project",
    ):
        if key in advanced and key not in data:
            data[key] = advanced[key]

    # Mover output_dir do advanced para o nível raiz se necessário
    if "output_dir" in advanced and "output_dir" not in data:
        data["output_dir"] = advanced["output_dir"]

    # Remover campos não reconhecidos pelo schema (ignorar sem erro)
    known_fields = ExperimentConfig.model_fields.keys()
    data = {k: v for k, v in data.items() if k in known_fields}

    return ExperimentConfig(**data)
