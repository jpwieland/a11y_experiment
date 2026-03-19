#!/usr/bin/env python3
"""
Status atual do corpus de scan — quantos projetos foram analisados,
quantos faltam, tempo estimado para concluir.

Usage:
    python dataset/scripts/scan_status.py               # visão geral
    python dataset/scripts/scan_status.py --pending     # lista projetos na fila
    python dataset/scripts/scan_status.py --domain ecommerce  # filtra domínio
    python dataset/scripts/scan_status.py --watch       # atualiza a cada 10s
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"
sys.path.insert(0, str(REPO_ROOT))

import yaml
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dataset.schema.models import ProjectEntry, ProjectStatus

# ── Helpers ───────────────────────────────────────────────────────────────────

_STATUS_STYLE = {
    ProjectStatus.SCANNED:     ("✅", "green"),
    ProjectStatus.ANNOTATED:   ("📝", "cyan"),
    ProjectStatus.SNAPSHOTTED: ("🔍", "yellow"),
    ProjectStatus.CANDIDATE:   ("📦", "dim white"),
    ProjectStatus.EXCLUDED:    ("🚫", "dim red"),
    ProjectStatus.ERROR:       ("❌", "red"),
}

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
    "other":        "dim white",
}


def load_entries(catalog: Path) -> list[ProjectEntry]:
    if not catalog.exists():
        return []
    with open(catalog, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception:
            pass
    return entries


def _scan_duration(e: ProjectEntry) -> float:
    """Retorna duração do scan em segundos (0 se não escaneado)."""
    if e.scan and e.scan.findings:
        return e.scan.findings.scan_duration_seconds
    return 0.0


def _avg_duration(scanned: list[ProjectEntry]) -> float:
    """Média de duração dos projetos já escaneados."""
    durations = [_scan_duration(e) for e in scanned if _scan_duration(e) > 0]
    return sum(durations) / len(durations) if durations else 0.0


def _fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ── Renderização ──────────────────────────────────────────────────────────────

def build_display(
    entries: list[ProjectEntry],
    domain_filter: str | None,
    show_pending: bool,
    ts: str,
) -> Panel:

    if domain_filter:
        entries = [e for e in entries if e.domain.value == domain_filter]

    # Contagens por status
    by_status: dict[ProjectStatus, list[ProjectEntry]] = {s: [] for s in ProjectStatus}
    for e in entries:
        by_status[e.status].append(e)

    scanned    = by_status[ProjectStatus.SCANNED] + by_status[ProjectStatus.ANNOTATED]
    pending    = by_status[ProjectStatus.SNAPSHOTTED]
    candidates = by_status[ProjectStatus.CANDIDATE]
    errors     = by_status[ProjectStatus.ERROR]
    excluded   = by_status[ProjectStatus.EXCLUDED]

    total      = len(entries)
    n_scanned  = len(scanned)
    n_pending  = len(pending)
    pct        = n_scanned / max(total, 1)

    avg_dur    = _avg_duration(scanned)
    eta_s      = avg_dur * n_pending
    total_issues = sum(
        (e.scan.findings.total_issues if e.scan and e.scan.findings else 0)
        for e in scanned
    )
    total_high = sum(
        (e.scan.findings.high_confidence if e.scan and e.scan.findings else 0)
        for e in scanned
    )

    # ── barra de progresso ────────────────────────────────────────────────────
    bar_w  = 36
    filled = int(bar_w * pct)
    bar    = Text()
    bar.append("  [", style="dim")
    bar.append("█" * filled, style="green bold")
    bar.append("░" * (bar_w - filled), style="dim")
    bar.append("] ", style="dim")
    bar.append(f"{n_scanned}/{total}", style="bold white")
    bar.append(f" ({pct:.0%})", style="dim white")

    # ── contadores de status ──────────────────────────────────────────────────
    counters = Table.grid(expand=True, padding=(0, 2))
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
        _cell("✅", "Escaneados",  n_scanned, "green"),
        _cell("🔍", "Na fila",     n_pending,  "yellow"),
        _cell("📦", "Candidatos", len(candidates), "dim white"),
        _cell("❌", "Erros",       len(errors), "red"),
    )

    # ── estatísticas de findings ──────────────────────────────────────────────
    stats = Table.grid(expand=True, padding=(0, 2))
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)
    stats.add_column(ratio=1)

    stats.add_row(
        Text.assemble(("🔍 Issues detectados: ", "dim white"), (str(total_issues), "bold yellow")),
        Text.assemble(("🔒 High confidence: ",  "dim white"), (str(total_high),   "bold green")),
        Text.assemble(("⏱ Média/projeto: ",     "dim white"), (_fmt_duration(avg_dur), "cyan")),
        Text.assemble(("🕒 ETA fila: ",          "dim white"), (_fmt_duration(eta_s),  "bright_yellow")),
    )

    # ── por domínio ───────────────────────────────────────────────────────────
    domain_counts: dict[str, tuple[int, int]] = {}  # domain → (scanned, total)
    for e in entries:
        d = e.domain.value
        sc, tot = domain_counts.get(d, (0, 0))
        domain_counts[d] = (
            sc + (1 if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED) else 0),
            tot + 1,
        )

    dom_table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        padding=(0, 2),
        expand=False,
    )
    dom_table.add_column("Domínio",    style="bold white",  width=14)
    dom_table.add_column("Escaneados", justify="right",     width=11)
    dom_table.add_column("Total",      justify="right",     width=7)
    dom_table.add_column("Progresso",                       width=22)
    dom_table.add_column("Issues",     justify="right", style="yellow", width=7)

    for dom, (sc, tot) in sorted(domain_counts.items(), key=lambda x: -x[1][1]):
        d_pct   = sc / max(tot, 1)
        d_bar_w = 16
        d_fill  = int(d_bar_w * d_pct)
        d_bar   = Text()
        d_bar.append("█" * d_fill, style=_DOMAIN_STYLE.get(dom, "white"))
        d_bar.append("░" * (d_bar_w - d_fill), style="dim")
        d_bar.append(f" {d_pct:.0%}", style="dim white")

        d_issues = sum(
            (e.scan.findings.total_issues if e.scan and e.scan.findings else 0)
            for e in entries
            if e.domain.value == dom
            and e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)
        )
        dom_table.add_row(
            Text(dom, style=_DOMAIN_STYLE.get(dom, "white")),
            f"{sc}/{tot}",
            str(tot),
            d_bar,
            str(d_issues) if d_issues else "—",
        )

    # ── fila de projetos pendentes ────────────────────────────────────────────
    pending_section = None
    if show_pending and pending:
        p_table = Table(
            show_header=True,
            header_style="bold yellow",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            padding=(0, 1),
        )
        p_table.add_column("#",           justify="right", style="dim", width=4)
        p_table.add_column("Projeto",     style="white",               width=34)
        p_table.add_column("Domínio",                                  width=12)
        p_table.add_column("Arquivos",    justify="right", style="cyan", width=9)
        p_table.add_column("Stars",       justify="right", style="dim", width=7)

        for i, e in enumerate(pending[:30], 1):
            n_tsx = e.snapshot.component_file_count if e.snapshot else 0
            stars = e.snapshot.stars if e.snapshot and hasattr(e.snapshot, "stars") else 0
            p_table.add_row(
                str(i),
                e.id[:34],
                Text(e.domain.value, style=_DOMAIN_STYLE.get(e.domain.value, "white")),
                str(n_tsx) if n_tsx else "?",
                str(stars) if stars else "—",
            )
        if len(pending) > 30:
            p_table.add_row("…", f"+ {len(pending)-30} projetos", "", "", "")

        pending_section = p_table

    # ── montar tudo ───────────────────────────────────────────────────────────
    body = Table.grid(expand=True, padding=0)
    body.add_column()

    body.add_row(bar)
    body.add_row(Text(""))
    body.add_row(counters)
    body.add_row(Text(""))
    body.add_row(stats)
    body.add_row(Text(""))
    body.add_row(Text("  Por domínio:", style="bold dim white"))
    body.add_row(dom_table)

    if pending_section:
        body.add_row(Text(""))
        body.add_row(Text(f"  Próximos na fila ({len(pending)}):", style="bold yellow"))
        body.add_row(pending_section)

    suffix = f"  [dim]{ts}[/dim]"
    if domain_filter:
        suffix += f"  [dim]filtro: {domain_filter}[/dim]"
    body.add_row(Text(f"\n  Ctrl+C para sair", style="dim"))

    title = "[bold cyan]♿ a11y-autofix — Status do Corpus[/bold cyan]"
    return Panel(body, title=title, border_style="cyan", subtitle=suffix)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Status atual do corpus de scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--catalog", type=Path, default=DEFAULT_CATALOG,
        help="Caminho do catálogo YAML",
    )
    parser.add_argument(
        "--pending", action="store_true",
        help="Exibir lista de projetos ainda não escaneados",
    )
    parser.add_argument(
        "--domain", type=str, default=None,
        help="Filtrar por domínio (ex: ecommerce, dashboard, saas...)",
    )
    parser.add_argument(
        "--watch", "-w", action="store_true",
        help="Atualizar automaticamente a cada 10s",
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=10,
        help="Intervalo de atualização em modo --watch (padrão: 10s)",
    )
    args = parser.parse_args()

    if not args.catalog.exists():
        print(f"❌ Catálogo não encontrado: {args.catalog}", file=sys.stderr)
        sys.exit(1)

    console = Console()

    if args.watch:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            try:
                while True:
                    entries = load_entries(args.catalog)
                    ts = time.strftime("%H:%M:%S")
                    live.update(build_display(entries, args.domain, args.pending, ts))
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                pass
    else:
        entries = load_entries(args.catalog)
        ts = time.strftime("%H:%M:%S")
        console.print(build_display(entries, args.domain, args.pending, ts))


if __name__ == "__main__":
    main()
