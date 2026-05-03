"""
progress_dashboard.py
=====================
Rich terminal dashboard for the ablation study experiment.

Reads two sources:
  • results_dir/status.json       — live status written by ablation_runner.py
  • results_dir/**/summary.json   — completed run summaries

Renders a full-screen Layout that auto-refreshes every REFRESH_HZ.

Layout:
  ┌──────────────────────────────────────────────────────────┐
  │  HEADER: title + wall clock + overall progress           │
  ├───────────────────────────┬──────────────────────────────┤
  │  LEFT                     │  RIGHT                       │
  │  Overall progress bar     │  Current run details         │
  │  Cell grid (8×3×3)        │  Layer failure breakdown     │
  │                           │  GPU / Ollama status         │
  ├───────────────────────────┴──────────────────────────────┤
  │  IFR table (completed conditions)                        │
  ├──────────────────────────────────────────────────────────┤
  │  FOOTER: timing + eta                                    │
  └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import math
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REFRESH_HZ = 2  # seconds between redraws
_GPU_POLL_INTERVAL = 4  # poll GPU every N seconds (nvidia-smi has ~200ms startup cost)

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


# ── GPU / Ollama polling ───────────────────────────────────────────────────────

def _query_gpu_nvidia() -> dict[str, Any]:
    """
    Query nvidia-smi for GPU utilization and VRAM.
    Returns a dict with keys: name, util_pct, vram_used_mb, vram_total_mb.
    All values are None if nvidia-smi is unavailable.
    """
    result: dict[str, Any] = {
        "name": None, "util_pct": None,
        "vram_used_mb": None, "vram_total_mb": None,
    }
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=4,
        )
        if out.returncode == 0 and out.stdout.strip():
            parts = [p.strip() for p in out.stdout.strip().split(",")]
            if len(parts) >= 4:
                result["name"]         = parts[0]
                result["util_pct"]     = float(parts[1])
                result["vram_used_mb"] = float(parts[2])
                result["vram_total_mb"]= float(parts[3])
    except Exception:
        pass
    return result


def _query_ollama_ps() -> dict[str, Any]:
    """
    Query Ollama /api/ps to get loaded model name and VRAM size.
    Returns {model_name, vram_size_bytes} or empty values on failure.
    """
    result: dict[str, Any] = {"model_name": None, "vram_size_bytes": None}
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2) as r:
            data = json.loads(r.read())
            models = data.get("models", [])
            if models:
                m = models[0]
                result["model_name"]    = m.get("name")
                result["vram_size_bytes"] = m.get("size_vram", m.get("size"))
    except Exception:
        pass
    return result


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
    status_file  = results_dir / "status.json"
    summary_files = sorted(results_dir.glob("**/summary.json"))

    current = _load_json(status_file) if status_file.exists() else None
    summaries = [s for f in summary_files if (s := _load_json(f))]

    study_meta = current or {}

    return {
        "current":     current,
        "summaries":   summaries,
        "study_start": study_meta.get("study_start", ""),
        "cells_total": study_meta.get("cells_total", 0),
        "cells_done":  len(summaries),
    }


# ── Panel builders ─────────────────────────────────────────────────────────────

def _header_panel(state: dict, elapsed_s: float) -> Panel:
    cells_done  = state["cells_done"]
    cells_total = state["cells_total"] or 1
    pct = cells_done / cells_total * 100

    elapsed = timedelta(seconds=int(elapsed_s))
    started = state.get("study_start", "")

    title_text = Text("⚡ A11Y Ablation Study — Live Monitor", style="bold white")
    clock_text = Text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), style="dim white")
    line1 = Text.assemble(title_text, "  │  ", clock_text)
    line2 = Text.assemble(
        "  Cells: ",
        (f"{cells_done}/{cells_total}", "bold yellow"),
        f"  ({pct:.1f}%)",
        "  │  Elapsed: ",
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

    summaries = state.get("summaries", [])
    mean_cell_s = (
        sum(s.get("wall_clock_total_s", 0) for s in summaries) / len(summaries)
        if summaries else None
    )
    remaining_cells = cells_total - cells_done
    eta_str = "—"
    if mean_cell_s and remaining_cells > 0:
        eta_str = str(timedelta(seconds=int(mean_cell_s * remaining_cells)))

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
    model_short = {
        "qwen2.5-coder-3b": "Qwen3B",
        "codellama-7b":      "CL7B",
        "qwen2.5-coder-7b":  "Qwen7B",
    }

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

    return Panel(
        t,
        title="[bold]Cell Grid[/bold]  ✓=done  ●=running  ·=pending",
        border_style="blue",
        padding=(0, 1),
    )


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
    cur_file = current.get("current_file", "")
    cur_att= current.get("current_attempt", 1)
    max_att= current.get("max_retries", 3)
    stage  = current.get("stage", "processing")
    last_result = current.get("last_attempt_result", "")

    bar_w  = 28
    filled = int(bar_w * v_done / max(v_total, 1))
    bar    = "█" * filled + "░" * (bar_w - filled)

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
        rate = v_done / cell_elapsed_s
        remaining = v_total - v_done
        eta_s = remaining / rate if rate > 0 else 0
        cell_eta_str = str(timedelta(seconds=int(eta_s)))

    live_ifr = f"{v_res / v_done * 100:.1f}%" if v_done else "—"

    # Last cell summary
    last_ifr   = current.get("last_cell_ifr")
    last_res   = current.get("last_cell_resolved")
    last_total = current.get("last_cell_total")
    last_wall  = current.get("last_cell_wall_s")

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", min_width=16)
    t.add_column(style="bold")
    t.add_row("Condition:", Text(_SHORT.get(cid, cid), style=_CONDITION_COLOR.get(cid, "cyan bold")))
    t.add_row("Model:",     model)
    t.add_row("Rep:",       f"Rep {rep}")
    t.add_row("Stage:",     Text(stage, style="yellow"))
    t.add_row("", "")
    t.add_row("Violations:", Text(f"{v_done} / {v_total}", style="white"))
    t.add_row("Progress:",   Text(f"[{bar}] {v_done/max(v_total,1)*100:.0f}%", style="bright_green"))
    t.add_row("Attempt:",    f"{cur_att} / {max_att}")
    t.add_row("", "")
    t.add_row("Live IFR:",   Text(live_ifr, style="bold magenta"))
    t.add_row("Cell ETA:",   Text(cell_eta_str, style="magenta"))

    if cur_file:
        short_file = Path(cur_file).name
        t.add_row("File:", Text(short_file, style="cyan"))
    if last_result:
        color = "green" if last_result == "FIXED" else "red"
        t.add_row("Last attempt:", Text(last_result, style=color))

    if last_ifr is not None and last_res is not None:
        t.add_row("", "")
        t.add_row(
            "Prev cell IFR:",
            Text(f"{last_ifr*100:.2f}%  ({last_res}/{last_total})  {last_wall:.0f}s", style="dim cyan")
        )

    return Panel(t, title="[bold]Current Run[/bold]", border_style="yellow", padding=(0, 1))


def _layer_panel(state: dict) -> Panel:
    """Layer failure rates for the current run."""
    current = state.get("current") or {}

    l1 = current.get("layer1_fail_rate", None)
    l2 = current.get("layer2_fail_rate", None)
    l3 = current.get("layer3_fail_rate", None)
    l4 = current.get("layer4_fail_rate", None)
    n  = current.get("n_attempts", 0)

    def _bar(rate: float | None, width: int = 18) -> Text:
        if rate is None:
            return Text("—", style="dim")
        filled = int(width * rate)
        bar = "█" * filled + "░" * (width - filled)
        color = "red" if rate > 0.2 else "yellow" if rate > 0.1 else "green"
        return Text.assemble((bar, color), f" {rate * 100:.1f}%")

    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", min_width=16)
    t.add_column()
    t.add_row("L1 Syntax:",       _bar(l1))
    t.add_row("L2 Functional:",   _bar(l2))
    t.add_row("L3 Domain:",       _bar(l3))
    t.add_row("L4 Quality(soft):", _bar(l4))
    t.add_row("", "")
    t.add_row("Attempts so far:", Text(str(n), style="cyan") if n else Text("—", style="dim"))

    return Panel(t, title="[bold]Layer Failures[/bold]", border_style="red", padding=(0, 1))


def _gpu_panel(gpu: dict[str, Any], ollama: dict[str, Any]) -> Panel:
    """GPU utilization (nvidia-smi) + Ollama loaded model."""
    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim", min_width=16)
    t.add_column()

    util       = gpu.get("util_pct")
    vram_used  = gpu.get("vram_used_mb")
    vram_total = gpu.get("vram_total_mb")
    gpu_name   = gpu.get("name")

    if util is None:
        t.add_row("GPU:", Text("nvidia-smi not found", style="dim"))
    else:
        bar_w = 18
        # GPU utilization bar
        g_filled = int(bar_w * util / 100)
        g_bar    = "█" * g_filled + "░" * (bar_w - g_filled)
        g_color  = "red" if util > 80 else "yellow" if util > 50 else "green"
        t.add_row(
            "GPU util:",
            Text.assemble((g_bar, g_color), f" {util:.0f}%")
        )

        # VRAM bar
        if vram_used is not None and vram_total and vram_total > 0:
            vram_pct  = vram_used / vram_total
            v_filled  = int(bar_w * vram_pct)
            v_bar     = "█" * v_filled + "░" * (bar_w - v_filled)
            v_color   = "red" if vram_pct > 0.9 else "yellow" if vram_pct > 0.7 else "cyan"
            used_gb   = vram_used  / 1024
            total_gb  = vram_total / 1024
            t.add_row(
                "VRAM:",
                Text.assemble((v_bar, v_color), f" {used_gb:.1f}/{total_gb:.1f} GB")
            )

        if gpu_name:
            t.add_row("Card:", Text(gpu_name, style="dim"))

    # Ollama model
    t.add_row("", "")
    model_name = ollama.get("model_name")
    if model_name:
        vram_bytes = ollama.get("vram_size_bytes")
        vram_str   = f"  ({vram_bytes / (1024**3):.1f} GB VRAM)" if vram_bytes else ""
        t.add_row("Ollama model:", Text(f"{model_name}{vram_str}", style="bold cyan"))
    else:
        t.add_row("Ollama model:", Text("none / offline", style="dim"))

    return Panel(t, title="[bold]GPU / Ollama[/bold]", border_style="magenta", padding=(0, 1))


def _ifr_table_panel(state: dict) -> Panel:
    summaries = state.get("summaries", [])
    if not summaries:
        return Panel(
            Align.center(Text("No completed runs yet.", style="dim italic")),
            title="[bold]IFR by Condition[/bold]",
            border_style="cyan",
        )

    from collections import defaultdict
    data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in summaries:
        cid   = s.get("condition_id", "?")
        model = s.get("model_name", "?")
        ifr   = s.get("ifr", float("nan"))
        data[cid][model].append(ifr)

    models = ["qwen2.5-coder-3b", "codellama-7b", "qwen2.5-coder-7b"]
    model_short = {
        "qwen2.5-coder-3b": "Qwen3B",
        "codellama-7b":      "CL7B",
        "qwen2.5-coder-7b":  "Qwen7B",
    }

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim")
    t.add_column("Condition", min_width=16)
    for m in models:
        t.add_column(model_short[m], justify="right", min_width=9)
    t.add_column("Reps", justify="center", min_width=5)

    best: dict[str, float] = {}
    for m in models:
        vals = [_mean(data[cid][m]) for cid in _ORDER if data[cid][m]]
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

    return Panel(
        t,
        title="[bold]IFR by Condition (completed runs)[/bold]",
        border_style="cyan",
        padding=(0, 1),
    )


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
        ("  │  Press Ctrl-C to stop monitor", "dim"),
    ]
    return Panel(
        Align.center(Text.assemble(*parts)),
        style="on grey7",
        border_style="bright_blue",
        padding=(0, 1),
    )


# ── Layout assembly ────────────────────────────────────────────────────────────

def build_layout(
    state: dict,
    elapsed_s: float,
    gpu: dict[str, Any] | None = None,
    ollama: dict[str, Any] | None = None,
) -> Layout:
    gpu    = gpu    or {}
    ollama = ollama or {}

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
        Layout(name="layers", size=9),
        Layout(name="gpu",    size=8),
    )

    layout["header"].update(_header_panel(state, elapsed_s))
    layout["overall"].update(_overall_progress_panel(state, elapsed_s))
    layout["grid"].update(_cell_grid_panel(state))
    layout["current"].update(_current_run_panel(state))
    layout["layers"].update(_layer_panel(state))
    layout["gpu"].update(_gpu_panel(gpu, ollama))
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

    # GPU state: cached between ticks to amortize nvidia-smi startup cost
    _gpu_cache:    dict[str, Any] = {}
    _ollama_cache: dict[str, Any] = {}
    _last_gpu_poll = 0.0

    with Live(console=console, refresh_per_second=1 / refresh_s, screen=True) as live:
        try:
            while True:
                now = time.monotonic()

                # Re-poll GPU only every _GPU_POLL_INTERVAL seconds
                if now - _last_gpu_poll >= _GPU_POLL_INTERVAL:
                    _gpu_cache    = _query_gpu_nvidia()
                    _ollama_cache = _query_ollama_ps()
                    _last_gpu_poll = now

                state     = load_study_state(results_dir)
                elapsed_s = now - start
                layout    = build_layout(state, elapsed_s, gpu=_gpu_cache, ollama=_ollama_cache)
                live.update(layout)
                time.sleep(refresh_s)
        except KeyboardInterrupt:
            pass

    console.print("\n[dim]Monitor stopped.[/dim]")


# ── Utilities ──────────────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
