#!/usr/bin/env python3
"""
Dashboard em tempo real do progresso de scan — combina catálogo + progresso
por arquivo + findings ao vivo.

Inspirado no visual de scan_status.py, mas atualizado em tempo real
enquanto o scan.py está rodando, mostrando:

  ▸ Barra de progresso geral (projetos concluídos / total)
  ▸ Projetos em andamento com barra de arquivo (X/Y arquivos)
  ▸ Últimos projetos concluídos com contagem de findings
  ▸ Sumário de critérios WCAG detectados ao vivo

Fonte de dados:
  - dataset/catalog/projects.yaml          → status geral (lido a cada 10s)
  - dataset/results/*/scan_progress.json   → progresso por projeto em curso
  - dataset/results/*/summary.json         → projetos recém-concluídos
  - dataset/results/live_findings.jsonl    → findings em tempo real

Uso:
    # Em uma aba separada enquanto o scan.py roda:
    python dataset/scripts/live_progress.py

    # Atualizar a cada 5s (padrão: 2s):
    python dataset/scripts/live_progress.py --interval 5

    # Mostrar até 20 projetos recentes (padrão: 8):
    python dataset/scripts/live_progress.py --recent 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR  = DATASET_ROOT / "results"
DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

sys.path.insert(0, str(REPO_ROOT))

import yaml
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── Paletas de cor ─────────────────────────────────────────────────────────────

_DOMAIN_STYLE = {
    "ecommerce":    "bright_green",
    "dashboard":    "bright_blue",
    "saas":         "bright_cyan",
    "social":       "magenta",
    "government":   "yellow",
    "healthcare":   "red",
    "finance":      "bright_yellow",
    "education":    "cyan",
    "productivity": "blue",
    "developer_tools": "bright_magenta",
    "other":        "dim white",
}

_IMPACT_STYLE = {
    "critical": "bold red",
    "serious":  "red",
    "moderate": "yellow",
    "minor":    "dim white",
}

_WCAG_PRINCIPLE = {"1": "Perceptível", "2": "Operável", "3": "Compreensível", "4": "Robusto"}


# ── Leitura de dados ──────────────────────────────────────────────────────────

def _read_catalog(path: Path) -> dict:
    """Lê o catálogo YAML. Retorna dict com 'projects' list."""
    if not path.exists():
        return {"projects": []}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"projects": []}
    except Exception:
        return {"projects": []}


def _read_progress_files(results_dir: Path) -> list[dict]:
    """Lê todos scan_progress.json — projetos atualmente em scan."""
    items = []
    if not results_dir.exists():
        return items
    for p in results_dir.glob("*/scan_progress.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append(data)
        except Exception:
            pass
    return items


def _read_recent_summaries(results_dir: Path, limit: int = 20) -> list[dict]:
    """Lê summary.json de projetos concluídos, ordenados do mais recente."""
    items = []
    if not results_dir.exists():
        return items
    for p in results_dir.glob("*/summary.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_project_id"] = p.parent.name
            items.append(data)
        except Exception:
            pass
    # Ordenar pelo scan_date (mais recente primeiro)
    items.sort(key=lambda x: x.get("scan_date", ""), reverse=True)
    return items[:limit]


def _read_live_findings(results_dir: Path) -> list[dict]:
    """Lê live_findings.jsonl (escrito pelo scan.py em tempo real)."""
    path = results_dir / "live_findings.jsonl"
    if not path.exists():
        return []
    items = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return items


# ── Formatação auxiliar ────────────────────────────────────────────────────────

def _fmt_dur(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _progress_bar(done: int, total: int, width: int = 20, style: str = "green") -> Text:
    pct    = done / max(total, 1)
    filled = int(width * pct)
    bar    = Text()
    bar.append("█" * filled, style=style)
    bar.append("░" * (width - filled), style="dim")
    return bar


# ── Painel: progresso geral (do catálogo) ─────────────────────────────────────

def _build_overall_panel(projects: list[dict], ts: str, elapsed: float) -> Panel:
    from collections import Counter as _Counter
    by_status = _Counter(p.get("status", "?") for p in projects)

    total     = len(projects)
    n_scanned = by_status.get("scanned", 0) + by_status.get("annotated", 0)
    n_pending = by_status.get("snapshotted", 0)
    n_error   = by_status.get("error", 0)
    pct       = n_scanned / max(total, 1)

    # Barra de progresso
    bar_w  = 40
    filled = int(bar_w * pct)
    bar    = Text()
    bar.append("  [", style="dim")
    bar.append("█" * filled, style="green bold")
    bar.append("░" * (bar_w - filled), style="dim")
    bar.append("] ", style="dim")
    bar.append(f"{n_scanned}/{total}", style="bold white")
    bar.append(f"  ({pct:.1%})", style="dim white")

    # Contadores
    counters = Table.grid(expand=True, padding=(0, 3))
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)

    def _cell(icon: str, label: str, n: int, style: str) -> Text:
        t = Text()
        t.append(f"{icon} ", style=style)
        t.append(f"{label}: ", style="dim white")
        t.append(str(n), style=f"bold {style}")
        return t

    counters.add_row(
        _cell("✅", "Concluídos",  n_scanned, "green"),
        _cell("🔍", "Na fila",     n_pending,  "yellow"),
        _cell("⏱",  "Elapsed",     0, "dim"),
        _cell("❌", "Erros",       n_error, "red"),
    )

    # Substituir a célula de elapsed por texto real
    elapsed_text = Text()
    elapsed_text.append("⏱ ", style="dim")
    elapsed_text.append("Tempo: ", style="dim white")
    elapsed_text.append(_fmt_dur(elapsed), style="bold cyan")

    grid = Table.grid(expand=True, padding=0)
    grid.add_column()
    grid.add_row(bar)
    grid.add_row(Text(""))
    grid.add_row(counters)

    subtitle = f"[dim]{ts}  |  ⏱ {_fmt_dur(elapsed)}  |  Ctrl+C para sair[/dim]"
    return Panel(grid, title="[bold cyan]♿ a11y-autofix — Progresso do Scan[/bold cyan]",
                 border_style="cyan", subtitle=subtitle)


# ── Painel: projetos em andamento ─────────────────────────────────────────────

def _build_active_panel(progress_items: list[dict]) -> Panel | Text:
    if not progress_items:
        return Panel(
            Text("  Nenhum projeto em scan no momento…", style="dim"),
            title="[yellow]Em andamento[/yellow]",
            border_style="dim yellow",
        )

    # Ordenar por last_update desc (mais recentemente atualizado primeiro)
    items = sorted(progress_items, key=lambda x: x.get("last_update", ""), reverse=True)

    tbl = Table(
        show_header=True,
        header_style="bold yellow",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        padding=(0, 1),
        expand=True,
    )
    tbl.add_column("Projeto",   style="white",         min_width=28)
    tbl.add_column("Progresso",                        width=22)
    tbl.add_column("Arquivos",  justify="right",       width=10)
    tbl.add_column("Issues",    justify="right", style="yellow", width=7)
    tbl.add_column("Status",    style="dim",           width=12)

    for item in items[:10]:
        pid        = item.get("project_id", "?")
        total_f    = item.get("total_files", 0)
        done_f     = item.get("files_done", 0)
        issues     = item.get("issues_so_far", 0)
        status     = item.get("status", "scanning")

        # Calcula ETA
        started    = item.get("started_at", "")
        last_upd   = item.get("last_update", "")

        pct_f = done_f / max(total_f, 1)
        bar   = _progress_bar(done_f, total_f, width=16, style="bright_yellow")
        bar.append(f" {pct_f:.0%}", style="dim white")

        status_text = Text("scanning…", style="yellow") if status == "scanning" \
                      else Text(status, style="dim red")

        tbl.add_row(
            Text(pid[:30], style="white"),
            bar,
            f"{done_f}/{total_f}",
            str(issues) if issues else "—",
            status_text,
        )

    return Panel(
        tbl,
        title=f"[yellow]Em andamento  ({len(progress_items)} projeto(s))[/yellow]",
        border_style="yellow",
    )


# ── Painel: projetos recém-concluídos ─────────────────────────────────────────

def _build_recent_panel(summaries: list[dict], show_n: int = 8) -> Panel:
    if not summaries:
        return Panel(
            Text("  Nenhum projeto concluído ainda…", style="dim"),
            title="[green]Recém concluídos[/green]",
            border_style="dim green",
        )

    tbl = Table(
        show_header=True,
        header_style="bold green",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        padding=(0, 1),
        expand=True,
    )
    tbl.add_column("Projeto",       style="white",        min_width=28)
    tbl.add_column("Issues",        justify="right", style="yellow", width=7)
    tbl.add_column("High",          justify="right", style="green",  width=6)
    tbl.add_column("Arquivos",      justify="right",               width=9)
    tbl.add_column("Duração",       justify="right", style="cyan",  width=9)

    for s in summaries[:show_n]:
        pid      = s.get("_project_id", "?")
        total_i  = s.get("total_issues", 0)
        high_i   = s.get("high_confidence", 0)
        files    = s.get("files_scanned", 0)
        dur      = s.get("scan_duration_seconds", 0.0)

        tbl.add_row(
            Text(pid[:30], style="white"),
            str(total_i) if total_i else "[dim]0[/dim]",
            str(high_i)  if high_i  else "[dim]—[/dim]",
            str(files),
            _fmt_dur(dur),
        )

    subtitle = f"[dim]últimos {min(show_n, len(summaries))} de {len(summaries)} concluídos[/dim]"
    return Panel(tbl, title="[green]Recém concluídos[/green]",
                 border_style="green", subtitle=subtitle)


# ── Painel: findings ao vivo (WCAG) ──────────────────────────────────────────

def _build_findings_panel(findings: list[dict]) -> Panel:
    if not findings:
        return Panel(
            Text("  Aguardando findings… (live_findings.jsonl vazio)", style="dim"),
            title="[bright_blue]Findings ao vivo[/bright_blue]",
            border_style="dim blue",
        )

    by_wcag:   dict[str, int] = defaultdict(int)
    by_impact: dict[str, int] = defaultdict(int)
    by_conf:   dict[str, int] = defaultdict(int)
    n_high = n_medium = n_low = 0

    for f in findings:
        wcag = f.get("wcag_criteria") or "N/A"
        by_wcag[wcag] += 1
        by_impact[f.get("impact", "?")] += 1
        conf = f.get("confidence", "low")
        by_conf[conf] += 1
        if conf == "high":
            n_high += 1
        elif conf == "medium":
            n_medium += 1
        else:
            n_low += 1

    total = len(findings)

    # Linha de resumo
    summary = Text("  ")
    summary.append(f"Total: {total}", style="bold yellow")
    summary.append("  ", style="dim")
    summary.append(f"High: {n_high}", style="bold green")
    summary.append(f"  Med: {n_medium}", style="yellow")
    summary.append(f"  Low: {n_low}", style="dim")

    # Impactos
    imp_row = Text("  Impacto: ")
    for imp in ["critical", "serious", "moderate", "minor"]:
        cnt = by_impact.get(imp, 0)
        if cnt:
            imp_row.append(f" {imp}:{cnt}", style=_IMPACT_STYLE.get(imp, "white"))

    # Top critérios WCAG
    top_wcag = sorted(by_wcag.items(), key=lambda x: -x[1])[:16]
    wcag_row = Text("  WCAG: ")
    for wcag, cnt in top_wcag:
        p_key = wcag[0] if wcag and wcag[0].isdigit() else "?"
        style = {"1": "bright_green", "2": "bright_blue",
                 "3": "bright_yellow", "4": "bright_cyan"}.get(p_key, "white")
        wcag_row.append(f"  {wcag}:{cnt}", style=style)
    if len(by_wcag) > 16:
        wcag_row.append(f"  +{len(by_wcag)-16} mais", style="dim")

    # Princípios
    by_principle: dict[str, int] = defaultdict(int)
    for wcag, cnt in by_wcag.items():
        p = wcag[0] if wcag and wcag[0].isdigit() else "?"
        by_principle[_WCAG_PRINCIPLE.get(p, f"P{p}")] += cnt

    p_row = Text("  Princípios: ")
    for pname in ["Perceptível", "Operável", "Compreensível", "Robusto"]:
        cnt = by_principle.get(pname, 0)
        p_row.append(f"  {pname[:4]}:{cnt}", style="dim cyan")

    body = Table.grid(expand=True, padding=0)
    body.add_column()
    body.add_row(summary)
    body.add_row(imp_row)
    body.add_row(Text(""))
    body.add_row(wcag_row)
    body.add_row(p_row)

    return Panel(body, title=f"[bright_blue]Findings ao vivo  ({len(by_wcag)} critérios WCAG únicos)[/bright_blue]",
                 border_style="blue")


# ── Renderização principal ─────────────────────────────────────────────────────

def build_dashboard(
    catalog_path: Path,
    results_dir: Path,
    ts: str,
    elapsed: float,
    recent_n: int,
) -> Table:
    """Monta o layout completo do dashboard."""

    projects       = _read_catalog(catalog_path).get("projects", [])
    progress_items = _read_progress_files(results_dir)
    summaries      = _read_recent_summaries(results_dir, limit=recent_n + 10)
    findings       = _read_live_findings(results_dir)

    # IDs dos projetos em andamento (para não duplicar na lista de recentes)
    active_ids = {item.get("project_id") for item in progress_items}

    overall   = _build_overall_panel(projects, ts, elapsed)
    active    = _build_active_panel(progress_items)
    recent    = _build_recent_panel(
        [s for s in summaries if s.get("_project_id") not in active_ids],
        show_n=recent_n,
    )
    findings_panel = _build_findings_panel(findings)

    layout = Table.grid(expand=True, padding=(0, 0))
    layout.add_column()
    layout.add_row(overall)
    layout.add_row(active)
    layout.add_row(recent)
    layout.add_row(findings_panel)

    return layout


# ── Loop principal ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dashboard em tempo real do progresso de scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--catalog",  type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--results",  type=Path, default=RESULTS_DIR)
    parser.add_argument("--interval", "-i", type=int, default=2,
                        help="Intervalo de atualização em segundos (padrão: 2)")
    parser.add_argument("--recent",   type=int, default=8,
                        help="Número de projetos recentes a exibir (padrão: 8)")
    args = parser.parse_args()

    if not args.catalog.exists():
        print(f"❌ Catálogo não encontrado: {args.catalog}", file=sys.stderr)
        sys.exit(1)

    args.results.mkdir(parents=True, exist_ok=True)

    console = Console()
    t_start = time.time()

    console.print("\n[bold cyan]♿ a11y-autofix — Live Progress[/bold cyan]")
    console.print(f"[dim]  Catálogo : {args.catalog}[/dim]")
    console.print(f"[dim]  Results  : {args.results}[/dim]")
    console.print(f"[dim]  Intervalo: {args.interval}s  |  Ctrl+C para sair[/dim]\n")

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                ts      = time.strftime("%H:%M:%S")
                elapsed = time.time() - t_start
                dash    = build_dashboard(
                    args.catalog, args.results, ts, elapsed, args.recent
                )
                live.update(dash)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass

    # Resumo final
    findings = _read_live_findings(args.results)
    summaries = _read_recent_summaries(args.results, limit=9999)
    console.print(f"\n[bold]Sessão encerrada após {_fmt_dur(time.time()-t_start)}[/bold]")
    console.print(f"  Projetos concluídos nesta sessão: {len(summaries)}")
    console.print(f"  Findings ao vivo registrados    : {len(findings)}\n")


if __name__ == "__main__":
    main()
