"""
watch_ablation.py
=================
Dashboard em tempo real para acompanhar o ablation study arquitetural.

Lê os arquivos de checkpoint/progress gerados pelo runner e exibe:
  - Estado e IFR de cada cell (condition × model × rep)
  - Throughput (violações/min) do cell ativo
  - ETA para o cell atual e para o estudo completo
  - Distribuição de falhas por layer

Uso:
    .venv/bin/python ablation_study/src/watch_ablation.py
    .venv/bin/python ablation_study/src/watch_ablation.py --results-dir ablation_study/results/arch_ablation
    .venv/bin/python ablation_study/src/watch_ablation.py --once   # imprime uma vez e sai
    .venv/bin/python ablation_study/src/watch_ablation.py --refresh 10  # atualiza a cada 10s
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ── Default results dir ───────────────────────────────────────────────────────
_DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "results" / "arch_ablation"

console = Console()

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CellState:
    condition_id: str
    model_name: str
    repetition: int
    seed: int = 0
    git_hash: str = ""

    # Status
    status: str = "pending"        # pending | running | done | error
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    # Progress (from checkpoint.json)
    files_done: int = 0
    files_total: int = 0
    violations_done: int = 0
    violations_resolved: int = 0
    ifr_running: float = 0.0
    last_file: str = ""

    # Final (from summary.json)
    ifr_final: Optional[float] = None
    n_total: int = 0
    n_resolved: int = 0
    wall_s: Optional[float] = None

    # Throughput (from progress.jsonl)
    viol_per_min: float = 0.0
    eta_s: Optional[float] = None

    # Layer failure rates (from checkpoint — live)
    l1_fail_rate: Optional[float] = None
    l2_fail_rate: Optional[float] = None
    l3_fail_rate: Optional[float] = None

    # Error
    error_msg: str = ""

    @property
    def label(self) -> str:
        cid = self.condition_id.replace("arch_", "")
        return f"{cid[:18]:18s} rep{self.repetition}"

    @property
    def progress_pct(self) -> float:
        if self.files_total > 0:
            return self.files_done / self.files_total
        if self.violations_done > 0 and self.ifr_running >= 0:
            return self.violations_done / max(self.violations_done, 1)
        return 0.0


# ── File readers ──────────────────────────────────────────────────────────────

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_progress_tail(path: Path, n: int = 20) -> list[dict]:
    """Read last n lines of progress.jsonl."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-n:]:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        return records
    except Exception:
        return []


def _throughput(progress_records: list[dict]) -> tuple[float, Optional[float]]:
    """
    Returns (violations_per_min, eta_seconds) from the last N progress records.
    ETA is None if no throughput data available.
    """
    if len(progress_records) < 2:
        return 0.0, None
    try:
        first = progress_records[0]
        last  = progress_records[-1]
        t0 = datetime.fromisoformat(first["ts"].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last["ts"].replace("Z", "+00:00"))
        elapsed_min = (t1 - t0).total_seconds() / 60
        if elapsed_min < 0.01:
            return 0.0, None
        done_delta = last["violations_done"] - first["violations_done"]
        if done_delta <= 0:
            return 0.0, None
        vpm = done_delta / elapsed_min
        # estimate remaining
        v_done  = last.get("violations_done", 0)
        v_total_est = v_done / max(last.get("ifr_running", 0.5) + 0.001, 0.001)
        remaining = max(v_total_est - v_done, 0)
        eta_s = (remaining / vpm) * 60 if vpm > 0 else None
        return vpm, eta_s
    except Exception:
        return 0.0, None


def _scan_results_dir(results_dir: Path) -> list[CellState]:
    """Walk results_dir and build one CellState per cell directory found."""
    cells: list[CellState] = []

    if not results_dir.exists():
        return cells

    for cond_dir in sorted(results_dir.iterdir()):
        if not cond_dir.is_dir() or cond_dir.name.startswith("."):
            continue
        condition_id = cond_dir.name

        for model_dir in sorted(cond_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            model_name = model_dir.name

            for rep_dir in sorted(model_dir.iterdir()):
                if not rep_dir.is_dir() or not rep_dir.name.startswith("rep"):
                    continue
                try:
                    rep = int(rep_dir.name.replace("rep", ""))
                except ValueError:
                    continue

                cs = CellState(
                    condition_id=condition_id,
                    model_name=model_name,
                    repetition=rep,
                )

                # run_meta.json — started
                meta = _read_json(rep_dir / "run_meta.json")
                if meta:
                    cs.started_at = meta.get("started_at")
                    cs.seed       = meta.get("seed", 0)
                    cs.git_hash   = meta.get("git_hash", "")
                    cs.status     = "running"

                # summary.json — finished
                summary = _read_json(rep_dir / "summary.json")
                if summary and summary.get("n_violations_total", 0) > 0:
                    cs.status       = "done"
                    cs.ifr_final    = summary.get("ifr")
                    cs.n_total      = summary.get("n_violations_total", 0)
                    cs.n_resolved   = summary.get("n_violations_resolved", 0)
                    cs.wall_s       = summary.get("wall_clock_total_s")
                    cs.finished_at  = summary.get("timestamp_end")
                    cs.violations_done     = cs.n_total
                    cs.violations_resolved = cs.n_resolved
                    cs.ifr_running         = cs.ifr_final or 0.0

                # checkpoint.json — live progress
                ckpt = _read_json(rep_dir / "checkpoint.json")
                if ckpt and cs.status != "done":
                    cs.files_done          = ckpt.get("files_done", 0)
                    cs.files_total         = ckpt.get("files_total", 0)
                    cs.violations_done     = ckpt.get("violations_done", 0)
                    cs.violations_resolved = ckpt.get("violations_resolved", 0)
                    cs.ifr_running         = ckpt.get("ifr_running", 0.0)
                    cs.last_file           = ckpt.get("last_file", "")

                # progress.jsonl — throughput & ETA
                if cs.status == "running":
                    prog = _read_progress_tail(rep_dir / "progress.jsonl", n=30)
                    cs.viol_per_min, cs.eta_s = _throughput(prog)

                cells.append(cs)

    return cells


# ── Study-level ETA ───────────────────────────────────────────────────────────

def _study_eta(cells: list[CellState], expected_total: int) -> Optional[float]:
    """
    Estimate total remaining seconds for the study.
    Uses the median throughput of running cells, or wall_s of done cells.
    """
    done_cells  = [c for c in cells if c.status == "done" and c.wall_s]
    run_cells   = [c for c in cells if c.status == "running" and c.viol_per_min > 0]
    pend_count  = max(expected_total - len(cells), 0) + len([c for c in cells if c.status == "pending"])

    if not done_cells and not run_cells:
        return None

    # Average wall time per completed cell
    avg_wall = sum(c.wall_s for c in done_cells) / len(done_cells) if done_cells else None

    remaining_s = 0.0

    # Running cells: ETA from throughput
    for c in run_cells:
        if c.eta_s:
            remaining_s += c.eta_s

    # Pending cells: use average wall time of done cells
    if avg_wall:
        remaining_s += pend_count * avg_wall
    elif run_cells:
        # Estimate from running throughput
        med_vpm = sorted(c.viol_per_min for c in run_cells)[len(run_cells) // 2]
        if med_vpm > 0:
            # Assume ~100 violations per cell (rough estimate)
            remaining_s += pend_count * (100 / med_vpm) * 60

    return remaining_s if remaining_s > 0 else None


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _fmt_ifr(ifr: Optional[float], bold: bool = False) -> Text:
    if ifr is None:
        return Text("—", style="dim")
    pct = ifr * 100
    if pct >= 60:
        style = "bold green" if bold else "green"
    elif pct >= 40:
        style = "bold yellow" if bold else "yellow"
    else:
        style = "bold red" if bold else "red"
    return Text(f"{pct:.1f}%", style=style)


def _fmt_eta(eta_s: Optional[float]) -> Text:
    if eta_s is None:
        return Text("—", style="dim")
    s = _fmt_duration(eta_s)
    if eta_s < 1800:  # < 30min
        return Text(s, style="green")
    if eta_s < 7200:  # < 2h
        return Text(s, style="yellow")
    return Text(s, style="red")


def _status_icon(cs: CellState) -> Text:
    if cs.status == "done":
        return Text("✓ DONE", style="bold green")
    if cs.status == "running":
        return Text("⟳ RUN ", style="bold cyan")
    if cs.status == "error":
        return Text("✗ ERR ", style="bold red")
    return Text("· WAIT", style="dim")


# ── Main table ────────────────────────────────────────────────────────────────

def _build_table(cells: list[CellState], study_start: Optional[str]) -> Table:
    table = Table(
        show_header=True,
        header_style="bold bright_blue",
        border_style="bright_blue",
        title="[bold]Architectural Ablation — Cell Progress[/bold]",
        title_style="white",
        expand=True,
    )
    table.add_column("Condition / Rep",    style="cyan",  min_width=24, no_wrap=True)
    table.add_column("Status",             min_width=8,   no_wrap=True)
    table.add_column("Files",              justify="right", min_width=10)
    table.add_column("Violations",         justify="right", min_width=14)
    table.add_column("IFR",                justify="right", min_width=7)
    table.add_column("Rate (viol/min)",    justify="right", min_width=14)
    table.add_column("ETA",               justify="right", min_width=9)
    table.add_column("Wall time",          justify="right", min_width=9)

    prev_cond = None
    for cs in cells:
        if prev_cond and cs.condition_id != prev_cond:
            table.add_row("", "", "", "", "", "", "", "", style="dim")
        prev_cond = cs.condition_id

        # Files column
        if cs.files_total > 0:
            files_str = f"{cs.files_done}/{cs.files_total}"
        elif cs.violations_done > 0:
            files_str = "?"
        else:
            files_str = "—"

        # Violations
        if cs.violations_done > 0:
            viol_str = f"{cs.violations_resolved}/{cs.violations_done}"
        else:
            viol_str = "—"

        # IFR
        if cs.status == "done":
            ifr_text = _fmt_ifr(cs.ifr_final, bold=True)
        elif cs.status == "running" and cs.violations_done > 0:
            ifr_text = _fmt_ifr(cs.ifr_running)
        else:
            ifr_text = Text("—", style="dim")

        # Rate
        if cs.status == "running" and cs.viol_per_min > 0:
            rate_str = f"{cs.viol_per_min:.1f}"
            rate_style = "cyan"
        elif cs.status == "done":
            rate_str = "—"
            rate_style = "dim"
        else:
            rate_str = "—"
            rate_style = "dim"

        # Wall time
        if cs.status == "done" and cs.wall_s:
            wall_str = _fmt_duration(cs.wall_s)
        else:
            wall_str = "—"

        table.add_row(
            cs.label,
            _status_icon(cs),
            files_str,
            viol_str,
            ifr_text,
            Text(rate_str, style=rate_style),
            _fmt_eta(cs.eta_s) if cs.status == "running" else Text("—", style="dim"),
            wall_str,
        )

    return table


def _build_summary_panel(
    cells: list[CellState],
    expected_total: int,
    refresh_s: int,
    study_start: Optional[str],
) -> Panel:
    done  = sum(1 for c in cells if c.status == "done")
    run   = sum(1 for c in cells if c.status == "running")
    pend  = max(expected_total - len(cells), 0) + sum(1 for c in cells if c.status == "pending")

    done_ifrs = [c.ifr_final for c in cells if c.status == "done" and c.ifr_final is not None]
    mean_ifr  = sum(done_ifrs) / len(done_ifrs) if done_ifrs else None

    total_eta = _study_eta(cells, expected_total)
    now_str   = datetime.now().strftime("%H:%M:%S")

    # Elapsed since study start
    elapsed_str = "—"
    if study_start:
        try:
            t0 = datetime.fromisoformat(study_start.replace("Z", "+00:00"))
            t0 = t0.replace(tzinfo=None)
            elapsed_s = (datetime.now() - t0).total_seconds()
            elapsed_str = _fmt_duration(elapsed_s)
        except Exception:
            pass

    lines = [
        f"[dim]Updated:[/dim] [white]{now_str}[/white]   [dim]Elapsed:[/dim] [white]{elapsed_str}[/white]   [dim]Refresh:[/dim] {refresh_s}s",
        "",
        f"  [bold green]✓ Done:[/bold green] {done}/{expected_total}   "
        f"[bold cyan]⟳ Running:[/bold cyan] {run}   "
        f"[dim]· Pending:[/dim] {pend}",
    ]

    if mean_ifr is not None:
        lines.append(f"  [dim]Mean IFR (done cells):[/dim] {_fmt_ifr(mean_ifr, bold=True)}")

    lines.append("")
    lines.append(f"  [dim]Study ETA:[/dim] {_fmt_eta(total_eta)}")

    return Panel("\n".join(lines), title="[bold]Study Overview[/bold]", border_style="bright_blue")


def _find_study_start(cells: list[CellState]) -> Optional[str]:
    starts = [c.started_at for c in cells if c.started_at]
    return min(starts) if starts else None


# ── Main entry point ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch ablation study progress")
    p.add_argument(
        "--results-dir", type=Path, default=_DEFAULT_RESULTS,
        help="Path to the results directory (default: ablation_study/results/arch_ablation)",
    )
    p.add_argument(
        "--expected-cells", type=int, default=15,
        help="Total expected cells (conditions × models × reps). Default: 15",
    )
    p.add_argument(
        "--refresh", type=int, default=5,
        help="Refresh interval in seconds (default: 5)",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Print once and exit (no live refresh)",
    )
    return p.parse_args()


def _render(results_dir: Path, expected_cells: int, refresh_s: int) -> None:
    cells = _scan_results_dir(results_dir)
    study_start = _find_study_start(cells)
    table   = _build_table(cells, study_start)
    summary = _build_summary_panel(cells, expected_cells, refresh_s, study_start)
    console.print(summary)
    console.print(table)


def main() -> None:
    args = _parse_args()

    if not args.results_dir.exists():
        console.print(
            f"[yellow]Results directory not found: {args.results_dir}[/yellow]\n"
            f"[dim]Run the experiment first, or pass --results-dir path/to/results[/dim]"
        )
        # Still show empty dashboard so user can see it's waiting
        if not args.once:
            console.print("[dim]Waiting for experiment to start…[/dim]")

    if args.once:
        _render(args.results_dir, args.expected_cells, args.refresh)
        return

    with Live(console=console, refresh_per_second=0.5, screen=False) as live:
        while True:
            cells = _scan_results_dir(args.results_dir)
            study_start = _find_study_start(cells)

            layout = Layout()
            layout.split_column(
                Layout(
                    _build_summary_panel(cells, args.expected_cells, args.refresh, study_start),
                    size=8,
                ),
                Layout(_build_table(cells, study_start)),
            )
            live.update(layout)
            time.sleep(args.refresh)

            # Stop auto-refresh when all cells are done
            done = sum(1 for c in cells if c.status == "done")
            if done >= args.expected_cells:
                # Final render then exit
                time.sleep(1)
                cells = _scan_results_dir(args.results_dir)
                study_start = _find_study_start(cells)
                live.update(_build_table(cells, study_start))
                console.print("\n[bold green]✓ All cells complete.[/bold green]")
                break


if __name__ == "__main__":
    main()
