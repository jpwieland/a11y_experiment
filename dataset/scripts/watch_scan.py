#!/usr/bin/env python3
"""
Acompanhe quais critérios WCAG estão sendo detectados em tempo real.

Lê o arquivo live_findings.jsonl que o scan.py grava após cada arquivo
processado e exibe uma tabela atualizada automaticamente.

Usage:
    # Em uma aba de terminal separada, enquanto o scan roda:
    python dataset/scripts/watch_scan.py

    # Atualizar a cada 5s em vez de 2s:
    python dataset/scripts/watch_scan.py --interval 5

    # Filtrar só um projeto:
    python dataset/scripts/watch_scan.py --project owner__repo

    # Ler findings já concluídos (em vez do live file):
    python dataset/scripts/watch_scan.py --completed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── Descrições curtas por critério WCAG ──────────────────────────────────────
_WCAG_LABELS: dict[str, str] = {
    "1.1.1":  "Non-text Content",
    "1.2.1":  "Audio-only / Video-only",
    "1.2.2":  "Captions (Prerecorded)",
    "1.3.1":  "Info & Relationships",
    "1.3.2":  "Meaningful Sequence",
    "1.3.3":  "Sensory Characteristics",
    "1.3.4":  "Orientation",
    "1.3.5":  "Identify Input Purpose",
    "1.4.1":  "Use of Color",
    "1.4.2":  "Audio Control",
    "1.4.3":  "Contrast (Minimum)",
    "1.4.4":  "Resize Text",
    "1.4.5":  "Images of Text",
    "1.4.6":  "Contrast (Enhanced)",
    "1.4.10": "Reflow",
    "1.4.11": "Non-text Contrast",
    "1.4.12": "Text Spacing",
    "1.4.13": "Content on Hover/Focus",
    "2.1.1":  "Keyboard",
    "2.1.2":  "No Keyboard Trap",
    "2.1.4":  "Char. Key Shortcuts",
    "2.4.1":  "Bypass Blocks",
    "2.4.2":  "Page Titled",
    "2.4.3":  "Focus Order",
    "2.4.4":  "Link Purpose",
    "2.4.5":  "Multiple Ways",
    "2.4.6":  "Headings & Labels",
    "2.4.7":  "Focus Visible",
    "2.4.11": "Focus Not Obscured",
    "2.5.3":  "Label in Name",
    "3.1.1":  "Language of Page",
    "3.1.2":  "Language of Parts",
    "3.2.1":  "On Focus",
    "3.2.2":  "On Input",
    "3.3.1":  "Error Identification",
    "3.3.2":  "Labels or Instructions",
    "3.3.3":  "Error Suggestion",
    "4.1.1":  "Parsing",
    "4.1.2":  "Name, Role, Value",
    "4.1.3":  "Status Messages",
}

# Princípios WCAG (primeiro dígito do critério)
_PRINCIPLES = {"1": "Perceptível", "2": "Operável", "3": "Compreensível", "4": "Robusto"}

# Cores por tipo de issue
_TYPE_STYLE: dict[str, str] = {
    "alt-text":  "green",
    "aria":      "bright_blue",
    "contrast":  "yellow",
    "keyboard":  "red",
    "label":     "magenta",
    "focus":     "cyan",
    "semantic":  "blue",
    "other":     "white",
}


# ── Leitura de dados ──────────────────────────────────────────────────────────

def _read_live(results_dir: Path) -> list[dict]:
    """Lê live_findings.jsonl (escrito por scan.py em tempo real)."""
    path = results_dir / "live_findings.jsonl"
    return _parse_jsonl(path)


def _read_completed(results_dir: Path, project_filter: str | None) -> list[dict]:
    """Lê todos os findings.jsonl de projetos já concluídos."""
    pattern = (
        f"{project_filter}/findings.jsonl"
        if project_filter
        else "*/findings.jsonl"
    )
    findings: list[dict] = []
    for fpath in results_dir.glob(pattern):
        findings.extend(_parse_jsonl(fpath))
    return findings


def _parse_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    if not path.exists():
        return items
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


# ── Agregação ─────────────────────────────────────────────────────────────────

def _aggregate(findings: list[dict]) -> dict:
    total:   dict[str, int]       = defaultdict(int)
    high:    dict[str, int]       = defaultdict(int)
    medium:  dict[str, int]       = defaultdict(int)
    itype:   dict[str, str]       = {}
    tools:   dict[str, set[str]]  = defaultdict(set)
    files:   set[str]             = set()
    impacts: dict[str, int]       = defaultdict(int)

    for f in findings:
        wcag       = f.get("wcag_criteria") or "N/A"
        confidence = f.get("confidence", "low")
        total[wcag]  += 1
        if confidence == "high":
            high[wcag]   += 1
        elif confidence == "medium":
            medium[wcag] += 1
        if wcag not in itype:
            itype[wcag] = f.get("issue_type", "?")
        for t in f.get("found_by", []):
            tools[wcag].add(t)
        fname = f.get("file", "")
        if fname:
            files.add(fname)
        impacts[f.get("impact", "?")]  += 1

    return {
        "total": total, "high": high, "medium": medium,
        "type": itype, "tools": tools,
        "n": len(findings),
        "n_high": sum(high.values()),
        "n_medium": sum(medium.values()),
        "n_files": len(files),
        "impacts": impacts,
    }


# ── Renderização ──────────────────────────────────────────────────────────────

def _render(
    agg: dict,
    last_ts: str,
    interval: int,
    mode: str,
    elapsed: float,
) -> Panel:

    n        = agg["n"]
    n_high   = agg["n_high"]
    n_medium = agg["n_medium"]
    n_low    = n - n_high - n_medium
    n_files  = agg["n_files"]
    sorted_criteria = sorted(agg["total"].items(), key=lambda x: -x[1])

    # ── cabeçalho ─────────────────────────────────────────────────────────────
    hdr = Table.grid(expand=True, padding=(0, 1))
    hdr.add_column(ratio=1)
    hdr.add_column(justify="right", no_wrap=True)

    left = Text()
    left.append(f"  📂 {n_files} arquivo(s) detectados   ", style="dim white")
    left.append(f"🔍 {n} findings   ", style="bold yellow")
    left.append(f"🔒 {n_high} high   ", style="bold green")
    left.append(f"⚡ {n_medium} med   ", style="yellow")
    left.append(f"· {n_low} low", style="dim")

    right = Text(justify="right")
    right.append(f"{last_ts}  ⏱ {elapsed:.0f}s  ", style="dim cyan")
    right.append(f"atualiza/{interval}s", style="dim")

    hdr.add_row(left, right)

    # ── impacto rápido ─────────────────────────────────────────────────────────
    imp_row = Text("  ")
    imp_colors = {"critical": "bold red", "serious": "red",
                  "moderate": "yellow", "minor": "dim white"}
    for imp, cnt in sorted(agg["impacts"].items(), key=lambda x: -x[1]):
        style = imp_colors.get(imp, "white")
        imp_row.append(f" {imp}:{cnt}", style=style)

    # ── critérios WCAG ─────────────────────────────────────────────────────────
    tbl = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,
        show_edge=False,
        padding=(0, 1),
    )
    tbl.add_column("Critério",  style="bold white",    width=9,  no_wrap=True)
    tbl.add_column("Descrição",                        width=24, no_wrap=True)
    tbl.add_column("Tipo",                             width=11, no_wrap=True)
    tbl.add_column("Total",  justify="right", style="bold yellow", width=6)
    tbl.add_column("High",   justify="right", style="bold green",  width=5)
    tbl.add_column("Med",    justify="right", style="yellow",      width=4)
    tbl.add_column("Ferramentas", style="dim",                     width=30)

    if not sorted_criteria:
        tbl.add_row("—", "Aguardando scan...", "", "", "", "", "")
    else:
        for wcag, count in sorted_criteria[:20]:
            desc    = _WCAG_LABELS.get(wcag, "—")
            tp      = agg["type"].get(wcag, "?")
            tcolor  = _TYPE_STYLE.get(tp, "white")
            h       = agg["high"].get(wcag, 0)
            m       = agg["medium"].get(wcag, 0)
            tool_s  = ", ".join(sorted(agg["tools"].get(wcag, set())))
            tbl.add_row(
                wcag, desc,
                Text(tp, style=tcolor),
                str(count), str(h),
                str(m) if m else "—",
                tool_s[:30],
            )
        if len(sorted_criteria) > 20:
            extra = len(sorted_criteria) - 20
            tbl.add_row(
                "…", f"+ {extra} critérios adicionais",
                "", "", "", "", "",
            )

    # ── por princípio ─────────────────────────────────────────────────────────
    by_p: dict[str, int] = defaultdict(int)
    for wcag, cnt in agg["total"].items():
        p = wcag[0] if wcag != "N/A" and wcag[0].isdigit() else "?"
        by_p[_PRINCIPLES.get(p, f"P{p}")] += cnt

    p_row = Text("  Princípios: ")
    for p_name, p_cnt in sorted(by_p.items()):
        p_row.append(f"  {p_name}:{p_cnt}", style="dim cyan")

    # ── rodapé ────────────────────────────────────────────────────────────────
    mode_label = "live (tempo real)" if mode == "live" else "concluídos"
    footer = Text(
        f"\n  Fonte: {mode_label}  |  Ctrl+C para sair",
        style="dim",
    )

    # ── montar grid ───────────────────────────────────────────────────────────
    body = Table.grid(expand=True, padding=0)
    body.add_column()
    body.add_row(hdr)
    body.add_row(Text(""))
    body.add_row(imp_row)
    body.add_row(Text(""))
    body.add_row(tbl)
    body.add_row(Text(""))
    body.add_row(p_row)
    body.add_row(footer)

    title = (
        "[bold cyan]♿ WCAG Detection — Tempo Real[/bold cyan]"
        if mode == "live"
        else "[bold cyan]♿ WCAG Detection — Findings Concluídos[/bold cyan]"
    )
    return Panel(body, title=title, border_style="cyan")


# ── Loop principal ────────────────────────────────────────────────────────────

def watch(
    results_dir: Path,
    interval: int,
    project_filter: str | None,
    completed: bool,
) -> None:
    console = Console()
    t_start = time.time()

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                if completed:
                    findings = _read_completed(results_dir, project_filter)
                else:
                    findings = _read_live(results_dir)
                    # também inclui findings de projetos já finalizados nesta sessão
                    findings += _read_completed(results_dir, project_filter)

                agg = _aggregate(findings)
                ts  = time.strftime("%H:%M:%S")
                elapsed = time.time() - t_start
                live.update(_render(agg, ts, interval, "completed" if completed else "live", elapsed))
                time.sleep(interval)
        except KeyboardInterrupt:
            pass

    # Resumo final após sair
    findings = _read_live(results_dir) if not completed else _read_completed(results_dir, project_filter)
    agg = _aggregate(findings)
    console.print(f"\n  ✅ Total de findings monitorados: {agg['n']}")
    console.print(f"  🔒 High confidence: {agg['n_high']}")
    console.print(f"  📋 Critérios WCAG únicos detectados: {len(agg['total'])}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Acompanha critérios WCAG detectados em tempo real durante o scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=2,
        help="Intervalo de atualização em segundos (padrão: 2)",
    )
    parser.add_argument(
        "--project", "-p", type=str, default=None,
        help="Filtrar por projeto específico (ex: owner__repo)",
    )
    parser.add_argument(
        "--results", type=Path, default=RESULTS_DIR,
        help="Diretório de resultados (padrão: dataset/results)",
    )
    parser.add_argument(
        "--completed", action="store_true",
        help="Ler só findings de projetos já concluídos (*/findings.jsonl)",
    )
    args = parser.parse_args()

    if not args.results.exists():
        print(f"❌ Diretório não encontrado: {args.results}", file=sys.stderr)
        sys.exit(1)

    live_path = args.results / "live_findings.jsonl"
    mode_desc = "projetos concluídos" if args.completed else f"live ({live_path.name})"
    print(f"\n♿  WCAG Watch — monitorando {mode_desc}")
    if args.project:
        print(f"   Projeto: {args.project}")
    print(f"   Intervalo: {args.interval}s  |  Ctrl+C para sair\n")

    watch(args.results, args.interval, args.project, args.completed)


if __name__ == "__main__":
    main()
