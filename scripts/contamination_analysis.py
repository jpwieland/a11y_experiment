#!/usr/bin/env python3
"""
Análise de contaminação de dados de treinamento.

Estima o risco de que projetos do corpus tenham sido incluídos nos dados
de treinamento dos modelos avaliados, o que pode inflar artificialmente
os resultados (memorização em vez de generalização).

Metodologia: C3.2 do PLANO_CORRECAO_METODOLOGICA.md

Estratégias de estimativa (sem acesso ao training data):
  1. Data-based: data de criação do repositório vs cutoff declarado do modelo
  2. Popularity-based: repositórios muito populares são mais prováveis de aparecer
     nos dados de treinamento (Common Crawl, GitHub data dumps)
  3. Canary test: perplexidade do modelo em trechos específicos do corpus

Saída:
  - contamination_report.json: risco por projeto
  - contamination_summary.md: sumário em markdown para o paper

Uso:
  python scripts/contamination_analysis.py \\
    --catalog dataset/catalog/projects.yaml \\
    --models-yaml models.yaml \\
    --output experiment-results/contamination_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# Cutoffs de treinamento declarados pelos modelos (aproximados)
# Fonte: papers técnicos e documentação oficial
MODEL_TRAINING_CUTOFFS: dict[str, str] = {
    "qwen2.5-coder-3b":   "2024-10-01",
    "qwen2.5-coder-7b":   "2024-10-01",
    "qwen2.5-coder-14b":  "2024-10-01",
    "qwen2.5-coder-32b":  "2024-10-01",
    "deepseek-coder-v2-lite": "2024-05-01",
    "deepseek-coder-v2-236b": "2024-05-01",
    "codellama-7b":        "2023-07-01",
    "codellama-13b":       "2023-07-01",
    "codellama-34b":       "2023-07-01",
    "llama3.1-8b":         "2023-12-01",
    "codestral-22b":       "2024-04-01",
    "starcoder2-15b":      "2024-01-01",
}

# Limiares de risco por popularidade
# Repositórios muito populares têm maior probabilidade de estar em datasets públicos
_RISK_THRESHOLDS = {
    "low":    (0,    1_000),   # < 1K estrelas: risco baixo
    "medium": (1_000, 10_000), # 1K-10K estrelas: risco médio
    "high":   (10_000, None),  # > 10K estrelas: risco alto
}


def estimate_risk(
    project: dict[str, Any],
    model_cutoffs: dict[str, str],
) -> dict[str, Any]:
    """
    Estima o risco de contaminação para um projeto.

    Args:
        project: Metadados do projeto do catalog.yaml
        model_cutoffs: Cutoffs de treinamento por modelo

    Returns:
        Dict com risco estimado por modelo e risco global
    """
    project_id = project.get("id", "unknown")
    stars = project.get("stars", 0) or 0
    created_at = project.get("created_at", "")
    last_commit = project.get("last_commit_date", "")

    # Risco por popularidade
    popularity_risk = "low"
    for risk_level, (min_stars, max_stars) in _RISK_THRESHOLDS.items():
        if max_stars is None:
            if stars >= min_stars:
                popularity_risk = risk_level
        elif min_stars <= stars < max_stars:
            popularity_risk = risk_level
            break

    # Risco por data (usar last_commit como proxy da versão treinada)
    date_risks: dict[str, str] = {}
    reference_date = last_commit or created_at or ""

    for model_name, cutoff_str in model_cutoffs.items():
        if not reference_date:
            date_risks[model_name] = "unknown"
            continue
        try:
            ref_date = datetime.fromisoformat(reference_date.split("T")[0])
            cutoff_date = datetime.fromisoformat(cutoff_str)
            if ref_date <= cutoff_date:
                date_risks[model_name] = "possible"  # dentro do período de treinamento
            else:
                date_risks[model_name] = "unlikely"  # após o cutoff
        except (ValueError, AttributeError):
            date_risks[model_name] = "unknown"

    # Risco global: combina popularidade e data
    any_possible = any(v == "possible" for v in date_risks.values())
    global_risk = popularity_risk
    if any_possible and popularity_risk in ("medium", "high"):
        global_risk = "high"
    elif any_possible and popularity_risk == "low":
        global_risk = "medium"

    return {
        "project_id": project_id,
        "stars": stars,
        "popularity_risk": popularity_risk,
        "reference_date": reference_date,
        "date_risks": date_risks,
        "global_risk": global_risk,
        "recommendation": _recommend_action(global_risk),
    }


def _recommend_action(risk_level: str) -> str:
    recommendations = {
        "low":     "Include in corpus — contamination unlikely",
        "medium":  "Include with caveat — report contamination risk in paper",
        "high":    "Consider sensitivity analysis excluding this project",
        "unknown": "Cannot assess — add metadata to catalog",
    }
    return recommendations.get(risk_level, "Unknown risk level")


def analyze_catalog(
    catalog_path: Path,
    models_yaml_path: Path | None = None,
) -> dict[str, Any]:
    """
    Analisa o catálogo inteiro e gera relatório de contaminação.

    Args:
        catalog_path: Path to projects.yaml
        models_yaml_path: Path to models.yaml (para extrair cutoffs)

    Returns:
        Relatório completo de contaminação
    """
    if not _HAS_YAML:
        raise ImportError("PyYAML required: pip install pyyaml")

    with open(catalog_path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f) or {}

    projects = catalog.get("projects", [])

    # Carregar cutoffs do models.yaml se fornecido
    model_cutoffs = dict(MODEL_TRAINING_CUTOFFS)
    if models_yaml_path and models_yaml_path.exists():
        with open(models_yaml_path, encoding="utf-8") as f:
            models_data = yaml.safe_load(f) or {}
        # Extrair cutoffs se declarados nos modelos
        for model_entry in models_data.get("models", []):
            model_id = model_entry.get("name", "")
            cutoff = model_entry.get("training_cutoff", "")
            if model_id and cutoff:
                model_cutoffs[model_id] = cutoff

    results: list[dict[str, Any]] = []
    for project in projects:
        risk = estimate_risk(project, model_cutoffs)
        results.append(risk)

    # Sumário
    risk_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "unknown": 0}
    for r in results:
        level = r.get("global_risk", "unknown")
        risk_counts[level] = risk_counts.get(level, 0) + 1

    high_risk_projects = [r["project_id"] for r in results if r["global_risk"] == "high"]

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_projects": len(results),
        "risk_distribution": risk_counts,
        "high_risk_projects": high_risk_projects,
        "recommendation": (
            "Run sensitivity analysis excluding high-risk projects. "
            f"{len(high_risk_projects)} projects flagged as high risk."
            if high_risk_projects
            else "No high-risk projects detected."
        ),
        "per_project": results,
    }


def generate_markdown_summary(report: dict[str, Any]) -> str:
    """Gera sumário em markdown para inclusão no paper."""
    dist = report.get("risk_distribution", {})
    high = report.get("high_risk_projects", [])
    total = report.get("total_projects", 0)

    lines = [
        "## Análise de Contaminação de Dados de Treinamento",
        "",
        f"Total de projetos analisados: **{total}**",
        "",
        "### Distribuição de Risco",
        "",
        "| Nível de Risco | N | % |",
        "|---|---|---|",
    ]
    for level in ("low", "medium", "high", "unknown"):
        count = dist.get(level, 0)
        pct = round(100 * count / total, 1) if total > 0 else 0
        lines.append(f"| {level.capitalize()} | {count} | {pct}% |")

    lines.extend([
        "",
        "### Projetos com Alto Risco",
        "",
    ])
    if high:
        for p in high:
            lines.append(f"- `{p}`")
    else:
        lines.append("*Nenhum projeto com alto risco identificado.*")

    lines.extend([
        "",
        "### Metodologia de Estimativa",
        "",
        "O risco foi estimado com base em dois fatores:",
        "1. **Popularidade**: repositórios com >10K estrelas têm maior probabilidade de",
        "   estar em datasets públicos (Common Crawl, GitHub data dumps).",
        "2. **Data**: projetos com commits anteriores ao cutoff de treinamento declarado",
        "   do modelo têm maior risco de contaminação.",
        "",
        "> **Limitação**: Esta estimativa é heurística. Acesso ao training data real",
        "> seria necessário para determinação definitiva.",
        "",
        f"*Relatório gerado em: {report.get('generated_at', 'N/A')}*",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Análise de contaminação de dados de treinamento",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("dataset/catalog/projects.yaml"),
        help="Path to projects.yaml catalog",
    )
    parser.add_argument(
        "--models-yaml",
        type=Path,
        default=Path("models.yaml"),
        help="Path to models.yaml (optional, for training cutoffs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiment-results/contamination_report.json"),
        help="Output JSON report path",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Output markdown summary path (optional)",
    )

    args = parser.parse_args()

    if not _HAS_YAML:
        print("ERROR: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    if not args.catalog.exists():
        print(f"ERROR: Catalog not found: {args.catalog}", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing catalog: {args.catalog}")
    report = analyze_catalog(args.catalog, args.models_yaml)

    # Salvar JSON
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Report saved: {args.output}")

    # Salvar markdown se solicitado
    if args.markdown:
        md = generate_markdown_summary(report)
        args.markdown.write_text(md, encoding="utf-8")
        print(f"Markdown summary saved: {args.markdown}")

    # Imprimir resumo no terminal
    dist = report["risk_distribution"]
    total = report["total_projects"]
    print(f"\nContamination Risk Summary ({total} projects):")
    for level in ("low", "medium", "high", "unknown"):
        count = dist.get(level, 0)
        bar = "█" * count
        print(f"  {level:8s}: {count:3d}  {bar}")

    high = report["high_risk_projects"]
    if high:
        print(f"\n⚠️  {len(high)} high-risk projects: {', '.join(high[:5])}")
        if len(high) > 5:
            print(f"   ... and {len(high) - 5} more")
    else:
        print("\n✓ No high-risk projects detected")


if __name__ == "__main__":
    main()
