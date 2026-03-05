"""Componentes Rich para UI no terminal."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

console = Console()


def make_progress() -> Progress:
    """Cria barra de progresso Rich padrão do sistema."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def print_banner() -> None:
    """Exibe banner do sistema."""
    banner = Panel(
        Text.assemble(
            ("♿ a11y-autofix", "bold cyan"),
            " v2.0 — ",
            ("Sistema de Correção Automática de Acessibilidade", "dim"),
            "\n",
            ("100% local · Multi-modelo · WCAG 2.1/2.2 · Reprodutível", "dim"),
        ),
        border_style="cyan",
    )
    console.print(banner)


def print_scan_summary(
    total_files: int,
    files_with_issues: int,
    total_issues: int,
    high_confidence: int,
) -> None:
    """Exibe resumo de scan em tabela Rich."""
    table = Table(title="Resumo do Scan", show_header=True, header_style="bold cyan")
    table.add_column("Métrica", style="dim")
    table.add_column("Valor", justify="right")
    table.add_row("Arquivos analisados", str(total_files))
    table.add_row("Arquivos com issues", str(files_with_issues))
    table.add_row("Total de issues", str(total_issues))
    table.add_row("Alta confiança (≥2 ferramentas)", str(high_confidence))
    console.print(table)


def print_experiment_summary(results: dict[str, dict[str, Any]]) -> None:
    """Exibe tabela comparativa de experimento multi-modelo."""
    table = Table(
        title="Resultados do Experimento",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Modelo", style="cyan")
    table.add_column("Taxa de Sucesso", justify="right")
    table.add_column("Tempo Médio (s)", justify="right")
    table.add_column("Issues Corrigidos", justify="right")

    for model_name, metrics in results.items():
        success_pct = f"{metrics.get('success_rate', 0):.1f}%"
        avg_time = f"{metrics.get('avg_time', 0):.1f}s"
        fixed = str(metrics.get("issues_fixed", 0))
        table.add_row(model_name, success_pct, avg_time, fixed)

    console.print(table)


def format_issue_list(issues: list[Any]) -> str:
    """Formata lista de issues para exibição no terminal."""
    lines = []
    for i, issue in enumerate(issues, 1):
        confidence_icon = {
            "high": "🔴",
            "medium": "🟡",
            "low": "🟢",
        }.get(issue.confidence.value, "⚪")
        msg = issue.message[:80]
        lines.append(
            f"  {i}. [{issue.issue_type.value.upper()}] "
            f"WCAG {issue.wcag_criteria or 'N/A'} "
            f"{confidence_icon} {issue.confidence.value.upper()} — {msg}"
        )
        lines.append(f"     Selector: {issue.selector}")
    return "\n".join(lines)
