"""
progress_dashboard.py
=====================
Rich terminal dashboard for the ablation study experiment.

Reads two sources:
  • results_dir/**/status.json   — live status written by ablation_runner.py
  • results_dir/**/summary.json  — completed run summaries

Renders a full-screen Layout that auto-refreshes every REFRESH_HZ.

Layout (portrait-friendly, works at ≥ 100 cols × 35 rows):

  ┌─────────────────────────────────────────────────────────┐
  │  HEADER: title + wall clock                             │
  ├───────────────────────────┬─────────────────────────────┤
  │  LEFT                     │  RIGHT                      │
  │  Overall progress bar     │  Current run details        │
  │  Cell grid (8×3×3)        │  Layer failure breakdown    │
  ├───────────────────────────┴─────────────────────────────┤
  │  IFR table (completed conditions)                       │
  ├─────────────────────────────────────────────────────────┤
  │  FOOTER: timing + eta                                   │
  └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
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
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

REFRESH_HZ = 1  # seconds between redraws

# ── Condition ordering / labels ────────────────────────────────────────────────

_ORDER = [
    "full",
    "minus_role",
    "minus_user_impact",
    "minus_code_context",
    "minus_constraints",
    "minus_few_shot",
    "minus_output_format",
    "raw_baseline",
]
_SHORT = {
    "full":                "Full",
    "minus_role":          "−Role",
    "minus_user_impact":   "−UserImpact",
    "minus_code_context":  "−CodeCtx",
    "minus_constraints":   "−Constraints",
    "minus_few_shot":      "−FewShot",
    "minus_output_format": "−OutFmt",
    "raw_baseline":        "RawBaseline",
}
_CONDITION_COLOR = {
    "full":        "bold cyan",
    "raw_baseline":"bold red",
}

# ── Data loading ───────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_study_state(results_dir: Path) -> dict[str, Any]:
    """
    Aggregate all status.json and summary.json files into one state dict.
    """
    status_files   = sorted(results_dir.glob("**/status.json"))
    summary_files  = sorted(results_dir.glob("**/summary.json"))

    statuses  = [s for f in status_files  if (s := _load_json(f))]
    summaries = [s for f in summary_files if (s := _load_json(f))]

    # Current running cell (latest status by timestamp)
    current: dict | None = None
    if statuses:
        try:
            current = max(
                statuses,
                key=lambda s: s.get("cell_start", ""),
            )
        except Exception:
            current = statuses[-1]

    # Read study-level metadata from any status
    study_meta = statuses[0] if statuses else {}

    return {
        "current": current,
        "summaries": summaries,
        "study_start": study_meta.get("study_start", ""),
        "cells_total": study_meta.get("cells_total", 0),
        "cells_done": len(summaries),
    }


# ── Panel builders ─────────────────────────────────────────────────────────────

def _header_panel(state: dict, elapsed_s: float) -> Panel:
    cells_done  = state["cells_done"]
    cells_total = state["cells_total"] or 1
    pct = cells_done / cells_total * 100

    elapsed = timedelta(seconds=int(elapsed_s))
    started = state.get("study_start", "")

    title_text = Text("⚡ A11Y Ablation Study — Live Monitor", style="bold white")
    clock_text = Text(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        style="dim white",
    )
    line1 = Text.assemble(title_text, "  │  ", clock_text)
    line2 = Text.assemble(
        f"  Cells completed: ",
        (f"{cells_done}/{cells_total}", "bold yellow"),
        f"  ({pct:.1f}%)",
        f"  │  Elapsed: ",
        (str(elapsed), "bold green"),
        f"  │  Started: {started[:19].replace('T', ' ')}",
    )
    return Panel(
        Align.left(Text.assemble(line1, "\n", line2)),
        style="on grey7",
        border_style="bright_blue",
        padding=(0, 1),
    )


def _overall_progress_panel(state: dict, elapsed_s: float) -> Panel:
    cells_done  = state["cells_done"]
    cells_total = state["cells_total"] or 1

    # ETA estimate based on mean cell wall-clock time
    summaries = state.get("summaries", [])
    mean_cell_s = (
        sum(s.get("wall_clock_total_s", 0) for s in summaries) / len(summaries)
        if summaries else None
    )
    remaining_cells = cells_total - cells_done
    eta_str = "—"
    if mean_cell_s and remaining_cells > 0:
        eta_s = mean_cell_s * remaining_cells
        eta = timedelta(seconds=int(eta_s))
        eta_str = str(eta)

    # Build progress bar manually (rich Progress needs a thread; we use a table)
    bar_width = 36
    filled = int(bar_width * cells_done / cells_total)
    bar = "█" * filled + "░" * (bar_width - filled)

    t = Table.grid(padding=(0, 1))
    t.add_column(no_wrap=True)
    t.add_row(Text(f"[{bar}]", style="bright_green"))
    t.add_row(
        Text.assemble(
            ("Completed ", "dim"),
            (f"{cells_done}", "bold yellow"),
            (" / ", "dim"),
            (f"{cells_total}", "bold"),
            (" cells", "dim"),
        )
    )
    if mean_cell_s:
        t.add_row(
            Text.assemble(
                ("Mean cell time: ", "dim"),
                (f"{mean_cell_s:.0f}s", "cyan"),
                ("  │  ETA: ", "dim"),
                (eta_str, "bold magenta"),
            )
        )
    else:
        t.add_row(Text("ETA: estimating…", style="dim"))

    return Panel(t, title="[bold]Overall Progress[/bold]", border_style="green", padding=(0, 1))


def _cell_grid_panel(state: dict) -> Panel:
    """3×8 grid of model × condition cells. ✓ = done, ● = running, · = pending."""
    summaries = state.get("summaries", [])
    current   = state.get("current") or {}

    done_keys: set[tuple] = set()
    for s in summaries:
        done_keys.add((s.get("condition_id"), s.get("model_name"), s.get("repetition")))

    models = ["qwen2.5-coder-3b", "codellama-7b", "qwen2.5-coder-7b"]
    model_short = {"qwen2.5-coder-3b": "Qwen3B", "codellama-7b": "CL7B", "qwen2.5-coder-7b": "Qwen7B"}

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim")
    t.add_column("Condition", style="dim", min_width=14)
    for m in models:
        t.add_column(model_short[m], justify="center", min_width=8)

    for cid in _ORDER:
        cells = []
        for model in models:
            rep_chars = []
            for rep in (1, 2, 3):
                key = (cid, model, rep)
                cur_key = (
                    current.get("condition_id"),
                    current.get("model"),
                    current.get("repetition"),
                )
                if key == cur_key:
                    rep_chars.append(Text("●", style="bold yellow"))
                elif key in done_keys:
                    rep_chars.append(Text("✓", style="green"))
                else:
                    rep_chars.append(Text("·", style="dim"))
            cell_text = Text(" ").join(rep_chars)
            cells.append(cell_text)

        label = _SHORT.get(cid, cid)
        color = _CONDITION_COLOR.get(cid, "white")
        t.add_row(Text(label, style=color), *cells)

    return Panel(t, title="[bold]Cell Grid[/bold]  ✓=done  ●=running  ·=pending",
                 border_style="blue", padding=(0, 1))


def _current_run_panel(state: dict) -> Panel:
    current = state.get("current")
    if not current:
        return Panel(
            Align.center(Text("Waiting for first run to start…", style="dim italic")),
            title="[bold]Current Run[/bold]",
            border_style="yellow",
        )

    cid    = current.get("condition_id", "—")
    model  = current.get("model", "—")
    rep    = current.get("repetition", "—")
    v_done = current.get("violations_done", 0)
    v_total= current.get("violations_total", 1)
    v_res  = current.get("violations_resolved", 0)
    cur_vid= current.get("current_violation_id", "")
    cur_att= current.get("current_attempt", 1)
    max_att= current.get("max_retries", 3)
    stage  = current.get("stage", "processing")

    bar_w  = 28
    filled = int(bar_w * v_done / max(v_total, 1))
    bar    = "█" * filled + "░" * (bar_w - filled)

    # Infer per-cell ETA from elapsed
    cell_start = current.get("cell_start", "")
    cell_elapsed_s = 0.0
    if cell_start:
        try:
            ts = datetime.fromisoformat(cell_start)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            cell_elapsed_s = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            pass

    cell_eta_str = "—"
    if v_done > 0 and cell_elapsed_s > 0:
        rate = v_done / cell_elapsed_s  # violations/sec
        remaining = v_total - v_done
        eta_s = remaining / rate if rate > 0 else 0
        cell_eta_str = str(timedelta(seconds=int(eta_s)))

    live_ifr = f"{v_res / v_done * 100:.1f}%" if v_done else "—"

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", min_width=18)
    t.add_column(style="bold")
    t.add_row("Condition:",  Text(_SHORT.get(cid, cid), style=_CONDITION_COLOR.get(cid, "cyan bold")))
    t.add_row("Model:",      model)
    t.add_row("Repetition:", f"Rep {rep}")
    t.add_row("Stage:",      Text(stage, style="yellow"))
    t.add_row("", "")
    t.add_row("Violation:",  Text(f"{v_done} / {v_total}", style="white"))
    t.add_row("Progress:",   Text(f"[{bar}] {v_done/max(v_total,1)*100:.0f}%", style="bright_green"))
    t.add_row("Attempt:",    f"{cur_att} / {max_att}")
    t.add_row("", "")
    t.add_row("Live IFR:",   Text(live_ifr, style="bold magenta"))
    t.add_row("Cell ETA:",   Text(cell_eta_str, style="magenta"))
    if cur_vid:
        short_vid = cur_vid[-45:] if len(cur_vid) > 45 else cur_vid
        t.add_row("Current:", Text(f"…{short_vid}", style="dim"))

    return Panel(t, title="[bold]Current Run[/bold]", border_style="yellow", padding=(0, 1))


def _layer_panel(state: dict) -> Panel:
    """Layer failure rates for the current run (from in-progress attempts.jsonl)."""
    current = state.get("current") or {}

    l1 = current.get("layer1_fail_rate", None)
    l2 = current.get("layer2_fail_rate", None)
    l3 = current.get("layer3_fail_rate", None)
    l4 = current.get("layer4_fail_rate", None)
    n  = current.get("n_attempts", 0)

    def _bar(rate: float | None, width: int = 20) -> Text:
        if rate is None:
            return Text("—", style="dim")
        filled = int(width * rate)
        bar = "█" * filled + "░" * (width - filled)
        color = "red" if rate > 0.2 else "yellow" if rate > 0.1 else "green"
        pct = f"{rate * 100:.1f}%"
        return Text.assemble((bar, color), f" {pct}")

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", min_width=16)
    t.add_column()
    t.add_row("L1 Syntax:",      _bar(l1))
    t.add_row("L2 Functional:",  _bar(l2))
    t.add_row("L3 Domain:",      _bar(l3))
    t.add_row("L4 Quality(soft):", _bar(l4))
    t.add_row("", "")
    t.add_row("Attempts so far:", Text(str(n), style="cyan") if n else Text("—", style="dim"))

    return Panel(t, title="[bold]Layer Failures (current run)[/bold]",
                 border_style="red", padding=(0, 1))


def _ifr_table_panel(state: dict) -> Panel:
    summaries = state.get("summaries", [])
    if not summaries:
        return Panel(
            Align.center(Text("No completed runs yet.", style="dim italic")),
            title="[bold]IFR by Condition[/bold]",
            border_style="cyan",
        )

    # Aggregate per (condition, model): mean IFR across reps
    from collections import defaultdict
    data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in summaries:
        cid   = s.get("condition_id", "?")
        model = s.get("model_name", "?")
        ifr   = s.get("ifr", float("nan"))
        data[cid][model].append(ifr)

    models = ["qwen2.5-coder-3b", "codellama-7b", "qwen2.5-coder-7b"]
    model_short = {"qwen2.5-coder-3b": "Qwen3B", "codellama-7b": "CL7B", "qwen2.5-coder-7b": "Qwen7B"}

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim")
    t.add_column("Condition", min_width=16)
    for m in models:
        t.add_column(model_short[m], justify="right", min_width=9)
    t.add_column("Reps", justify="center", min_width=5)

    # Find best IFR per model to highlight
    best: dict[str, float] = {}
    for m in models:
        vals = [
            _mean(data[cid][m])
            for cid in _ORDER
            if data[cid][m]
        ]
        best[m] = max(vals) if vals else 0.0

    for cid in _ORDER:
        if not any(data[cid][m] for m in models):
            continue

        label = _SHORT.get(cid, cid)
        color = _CONDITION_COLOR.get(cid, "white")
        cells: list[Text] = []
        max_reps = 0

        for m in models:
            vals = data[cid][m]
            max_reps = max(max_reps, len(vals))
            if not vals:
                cells.append(Text("—", style="dim"))
                continue
            mean_ifr = _mean(vals)
            pct = f"{mean_ifr * 100:.2f}%"
            is_best = math.isclose(mean_ifr, best.get(m, -1), rel_tol=1e-4)
            style = "bold green" if is_best else ("bold cyan" if cid == "full" else "white")
            cells.append(Text(pct, style=style))

        t.add_row(Text(label, style=color), *cells, Text(str(max_reps), style="dim"))

    return Panel(t, title="[bold]IFR by Condition (completed runs)[/bold]",
                 border_style="cyan", padding=(0, 1))


def _footer_panel(state: dict, elapsed_s: float) -> Panel:
    summaries   = state.get("summaries", [])
    cells_done  = state["cells_done"]
    cells_total = state["cells_total"] or 1

    mean_cell_s = (
        sum(s.get("wall_clock_total_s", 0) for s in summaries) / len(summaries)
        if summaries else None
    )
    remaining_cells = cells_total - cells_done
    total_eta_str = "estimating…"
    if mean_cell_s and remaining_cells > 0:
        total_eta_str = str(timedelta(seconds=int(mean_cell_s * remaining_cells)))

    finish_str = "—"
    if mean_cell_s and remaining_cells > 0:
        finish_dt = datetime.now() + timedelta(seconds=mean_cell_s * remaining_cells)
        finish_str = finish_dt.strftime("%Y-%m-%d %H:%M")

    parts = [
        ("Elapsed: ", "dim"), (str(timedelta(seconds=int(elapsed_s))), "bold green"),
        ("  │  ETA remaining: ", "dim"), (total_eta_str, "bold magenta"),
        ("  │  Est. finish: ", "dim"), (finish_str, "bold white"),
        ("  │  Mean cell: ", "dim"),
        (f"{mean_cell_s:.0f}s" if mean_cell_s else "—", "cyan"),
    ]

    return Panel(
        Align.center(Text.assemble(*parts)),
        style="on grey7",
        border_style="bright_blue",
        padding=(0, 1),
    )


# ── Layout assembly ────────────────────────────────────────────────────────────

def build_layout(state: dict, elapsed_s: float) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(name="header",  size=4),
        Layout(name="body"),
        Layout(name="ifr",     size=12),
        Layout(name="footer",  size=3),
    )

    layout["body"].split_row(
        Layout(name="left",  ratio=1),
        Layout(name="right", ratio=1),
    )

    layout["left"].split_column(
        Layout(name="overall", size=8),
        Layout(name="grid"),
    )

    layout["right"].split_column(
        Layout(name="current"),
        Layout(name="layers", size=10),
    )

    layout["header"].update(_header_panel(state, elapsed_s))
    layout["overall"].update(_overall_progress_panel(state, elapsed_s))
    layout["grid"].update(_cell_grid_panel(state))
    layout["current"].update(_current_run_panel(state))
    layout["layers"].update(_layer_panel(state))
    layout["ifr"].update(_ifr_table_panel(state))
    layout["footer"].update(_footer_panel(state, elapsed_s))

    return layout


# ── Live monitor loop ──────────────────────────────────────────────────────────

def run_monitor(results_dir: Path, refresh_s: float = REFRESH_HZ) -> None:
    """
    Blocking loop that renders the dashboard until Ctrl-C.
    Can be run in a separate terminal while ablation_runner.py executes.
    """
    console = Console()
    start   = time.monotonic()

    with Live(console=console, refresh_per_second=1 / refresh_s, screen=True) as live:
        try:
            while True:
                state     = load_study_state(results_dir)
                elapsed_s = time.monotonic() - start
                layout    = build_layout(state, elapsed_s)
                live.update(layout)
                time.sleep(refresh_s)
        except KeyboardInterrupt:
            pass

    console.print("\n[dim]Monitor stopped.[/dim]")


# ── Utilities ──────────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
