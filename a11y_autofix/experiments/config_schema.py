"""Schema de validação para configurações de experimentos YAML."""

from __future__ import annotations

import json
import random as _random_mod
from collections import defaultdict
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

    # Amostragem estratificada por tipo de issue
    stratified_sampling: bool = Field(
        default=False,
        description=(
            "Se True, aplica amostragem estratificada por tipo de issue (aria, "
            "keyboard, semantic, alt-text, etc.) ao selecionar arquivos quando "
            "max_files_per_project está definido. Requer findings.jsonl pré-computados "
            "em dataset/results/{project_id}/. Garante representação proporcional de "
            "cada tipo de issue no corpus amostrado, eliminando o viés de composição "
            "observado com amostragem aleatória simples."
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

        Se stratified_sampling=True, aplica amostragem estratificada por tipo
        de issue usando findings.jsonl pré-computados em dataset/results/.
        Garante representação proporcional de cada tipo de issue no corpus,
        eliminando o viés de composição observado com amostragem aleatória.

        Args:
            base_dir: Diretório base para resolução de paths relativos.

        Returns:
            Lista de arquivos encontrados.
        """
        from a11y_autofix.utils.files import find_react_files

        base = base_dir or Path.cwd()
        resolved: list[Path] = []
        rng = _random_mod.Random(self.seed)

        for pattern in self.files:
            path = Path(pattern)
            if not path.is_absolute():
                path = base / pattern
            found = find_react_files(path)

            if self.max_files_per_project is not None and len(found) > self.max_files_per_project:
                if self.stratified_sampling:
                    found = self._sample_stratified(found, path, base, rng)
                else:
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

    def _sample_stratified(
        self,
        all_files: list[Path],
        snapshot_path: Path,
        base: Path,
        rng: _random_mod.Random,
    ) -> list[Path]:
        """
        Amostragem estratificada por tipo de issue dentro de um projeto.

        Algoritmo:
          1. Carrega findings.jsonl do projeto para mapear arquivo → tipo(s) de issue.
          2. Classifica cada arquivo em um estrato: o tipo de issue de maior impacto
             presente no arquivo (ou "clean" se nenhum issue for encontrado).
          3. Calcula a alocação proporcional de slots do max_files_per_project
             entre os estratos, mantendo a proporção natural do projeto.
          4. Amostra deterministicamente de cada estrato com o rng compartilhado.
          5. Preenche slots restantes (por arredondamento) com arquivos limpos.

        Garante:
          - Reproducibilidade via rng com seed fixo.
          - Representação de TODOS os tipos de issue presentes no projeto.
          - Nenhum estrato fica com zero arquivos se tiver ao menos 1 disponível.

        Args:
            all_files:     Todos os arquivos React encontrados no projeto.
            snapshot_path: Diretório raiz do snapshot do projeto.
            base:          Diretório base do experimento.
            rng:           Instância Random compartilhada (estado preservado).

        Returns:
            Lista de até max_files_per_project arquivos estratificados.
        """
        n = self.max_files_per_project  # garantido não-None pelo chamador
        assert n is not None

        # ── 1. Descobrir project_id e carregar findings ───────────────────────
        project_id = snapshot_path.name
        findings_path = _find_findings(snapshot_path, base, project_id)

        if findings_path is None or not findings_path.exists():
            # Fallback: amostragem aleatória simples (sem dados de issues)
            sample = list(all_files)
            rng.shuffle(sample)
            return sorted(sample[:n])

        # ── 2. Construir mapa relativo → tipo de issue primário ───────────────
        # Prioridade de impacto usada para decidir o estrato de um arquivo
        # com múltiplos tipos: critical > serious > moderate > minor
        IMPACT_RANK = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
        ISSUE_TYPES_ORDER = ["keyboard", "aria", "semantic", "alt-text",
                             "contrast", "label", "focus", "other"]

        # file_issues: {relative_path → {issue_type: max_impact_rank}}
        file_issues: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(lambda: 99))

        try:
            with findings_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    finding = json.loads(line)
                    rel = _normalize_finding_path(finding.get("file", ""), project_id)
                    if rel is None:
                        continue
                    itype = finding.get("issue_type", "other")
                    impact = finding.get("impact", "minor")
                    rank = IMPACT_RANK.get(impact, 3)
                    # Manter o rank de maior impacto (menor valor) por tipo
                    if rank < file_issues[rel][itype]:
                        file_issues[rel][itype] = rank
        except (OSError, json.JSONDecodeError):
            # Fallback se findings estiver corrompido
            sample = list(all_files)
            rng.shuffle(sample)
            return sorted(sample[:n])

        # ── 3. Classificar arquivos em estratos ──────────────────────────────
        # Estrato = tipo de issue com maior impacto no arquivo
        strata: dict[str, list[Path]] = defaultdict(list)
        all_files_set = {f.resolve() for f in all_files}

        # Mapear paths resolvidos para Path objects
        resolved_to_path: dict[Path, Path] = {f.resolve(): f for f in all_files}

        # Arquivos COM issues
        files_with_issues: set[Path] = set()
        for rel, types in file_issues.items():
            # Tentar localizar o arquivo no snapshot
            candidate = snapshot_path / rel
            resolved_candidate = candidate.resolve()
            if resolved_candidate not in all_files_set:
                continue
            path_obj = resolved_to_path[resolved_candidate]
            files_with_issues.add(path_obj)

            # Estrato = tipo com menor rank (maior impacto)
            primary_type = min(types.items(), key=lambda x: x[1])[0]
            strata[primary_type].append(path_obj)

        # Arquivos SEM issues (estrato "clean")
        for f in all_files:
            if f not in files_with_issues:
                strata["clean"].append(f)

        # ── 4. Calcular alocação proporcional ────────────────────────────────
        total_files = len(all_files)
        # Proporção natural de cada estrato no projeto
        stratum_counts = {k: len(v) for k, v in strata.items()}
        allocation = _proportional_allocation(stratum_counts, n)

        # Garantir pelo menos 1 slot para todo estrato com issues
        # (exceto "clean" — ele recebe o que sobrar)
        issue_strata = [k for k in stratum_counts if k != "clean"]
        for itype in issue_strata:
            if stratum_counts[itype] > 0 and allocation.get(itype, 0) == 0:
                # Roubar 1 slot do estrato com mais arquivos disponíveis
                donor = max(
                    (k for k in allocation if allocation[k] > 1),
                    key=lambda k: allocation[k],
                    default=None,
                )
                if donor is not None:
                    allocation[donor] -= 1
                    allocation[itype] = allocation.get(itype, 0) + 1

        # ── 5. Amostrar de cada estrato ──────────────────────────────────────
        sampled: list[Path] = []
        for itype in ISSUE_TYPES_ORDER + ["clean"]:
            if itype not in strata or itype not in allocation:
                continue
            k = min(allocation[itype], len(strata[itype]))
            if k <= 0:
                continue
            pool = list(strata[itype])
            rng.shuffle(pool)
            sampled.extend(pool[:k])

        # Ordenar deterministicamente para logs consistentes
        return sorted(sampled[:n])


def _find_findings(snapshot_path: Path, base: Path, project_id: str) -> Path | None:
    """
    Localiza o arquivo findings.jsonl para um projeto.

    Tenta caminhos relativos à raiz do repositório e ao diretório base,
    usando a convenção dataset/results/{project_id}/findings.jsonl.
    """
    candidates = [
        # Relativo ao snapshot: subir até encontrar dataset/
        snapshot_path.parent.parent / "results" / project_id / "findings.jsonl",
        # Relativo ao cwd
        Path.cwd() / "dataset" / "results" / project_id / "findings.jsonl",
        # Relativo ao base
        base / "dataset" / "results" / project_id / "findings.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # retorna o mais provável mesmo que não exista (caller checa)


def _normalize_finding_path(raw_path: str, project_id: str) -> str | None:
    """
    Extrai o caminho relativo de um finding dado o project_id.

    Findings armazenam paths absolutos do ambiente de coleta (e.g. Windows).
    Este helper extrai a parte relativa após 'snapshots/{project_id}/'.

    Examples:
        'C:\\...\\snapshots\\PROJ\\src\\foo.tsx' → 'src/foo.tsx'
        'dataset/snapshots/PROJ/src/foo.tsx'     → 'src/foo.tsx'
    """
    # Normalizar separadores para forward-slash
    normalized = raw_path.replace("\\", "/")
    marker = f"snapshots/{project_id}/"
    idx = normalized.find(marker)
    if idx == -1:
        # Tentar apenas o project_id como marcador
        idx = normalized.find(f"{project_id}/")
        if idx == -1:
            return None
        return normalized[idx + len(project_id) + 1:]
    return normalized[idx + len(marker):]


def _proportional_allocation(counts: dict[str, int], total_slots: int) -> dict[str, int]:
    """
    Distribui total_slots entre estratos proporcionalmente ao tamanho de cada um.

    Usa o método Hamilton (largest remainder) para garantir que a soma
    dos slots alocados seja exatamente total_slots.
    """
    grand_total = sum(counts.values())
    if grand_total == 0:
        return {}

    # Cotas exatas (fracionárias)
    exact: dict[str, float] = {
        k: (v / grand_total) * total_slots for k, v in counts.items()
    }
    # Parte inteira
    allocation: dict[str, int] = {k: int(q) for k, q in exact.items()}
    remainder = total_slots - sum(allocation.values())

    # Distribuir o restante pelos maiores restos
    remainders = sorted(
        exact.items(), key=lambda x: x[1] - int(x[1]), reverse=True
    )
    for i in range(remainder):
        k = remainders[i % len(remainders)][0]
        allocation[k] += 1

    return allocation


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
        "checkpoint_per_project", "stratified_sampling",
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
