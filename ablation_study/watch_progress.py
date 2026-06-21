#!/usr/bin/env python3
"""
watch_progress.py
=================
Live progress monitor for the architectural ablation study.

Shows per-cell IFR, ETA, and cumulative stats. Reads checkpoint.json and
progress.jsonl written by the runner — no coupling to the runner process.

Usage:
    python3 ablation_study/watch_progress.py
    python3 ablation_study/watch_progress.py --results-dir ablation_study/results/arch_ablation
    python3 ablation_study/watch_progress.py --interval 10   # refresh every 10s (default: 5)
    python3 ablation_study/watch_progress.py --once           # print once and exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── ANSI colour helpers ───────────────────────────────────────────────────────
ESC = "\033["
RESET  = f"{ESC}0m"
BOLD   = f"{ESC}1m"
DIM    = f"{ESC}2m"
GREEN  = f"{ESC}32m"
YELLOW = f"{ESC}33m"
RED    = f"{ESC}31m"
CYAN   = f"{ESC}36m"
MAGENTA= f"{ESC}35m"
BLUE   = f"{ESC}34m"
WHITE  = f"{ESC}97m"

def clr(text: str, *codes: str) -> str:
    return "".join(codes) + str(text) + RESET

def bar(fraction: float, width: int = 20, full: str = "█", empty: str = "░") -> str:
    filled = int(round(fraction * width))
    colour = GREEN if fraction >= 0.7 else YELLOW if fraction >= 0.4 else RED
    return clr(full * filled + empty * (width - filled), colour)

def clear_screen() -> None:
    os.system("clear" if os.name != "nt" else "cls")

# ── Data loading ──────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_last_jsonl(path: Path) -> dict:
    """Return the last valid JSON line from a JSONL file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    return json.loads(line)
                except Exception:
                    continue
    except Exception:
        pass
    return {}


def _count_jsonl_lines(path: Path) -> int:
    try:
        return sum(1 for l in path.read_text(encoding="utf-8").splitlines() if l.strip())
    except Exception:
        return 0


def _run_meta_started_at(meta_path: Path) -> float | None:
    """Return the epoch timestamp when the run started, or None."""
    meta = _read_json(meta_path)
    started = meta.get("started_at", "")
    if not started:
        return None
    try:
        return datetime.fromisoformat(started).timestamp()
    except Exception:
        return None


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _eta_str(elapsed_s: float, done: int, total: int) -> str:
    if done <= 0 or total <= 0 or elapsed_s <= 0:
        return "—"
    rate = done / elapsed_s   # units per second
    remaining = (total - done) / rate
    return _fmt_duration(remaining)

# ── Cell state ────────────────────────────────────────────────────────────────

def collect_cells(results_dir: Path) -> list[dict]:
    """
    Walk results_dir and collect state for every (condition, model, rep) cell.
    Returns list of dicts sorted by condition → model → rep.
    """
    cells = []
    if not results_dir.exists():
        return cells

    for condition_dir in sorted(results_dir.iterdir()):
        if not condition_dir.is_dir() or condition_dir.name.startswith("."):
            continue
        for model_dir in sorted(condition_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            for rep_dir in sorted(model_dir.iterdir()):
                if not rep_dir.is_dir() or not rep_dir.name.startswith("rep"):
                    continue

                summary_path    = rep_dir / "summary.json"
                checkpoint_path = rep_dir / "checkpoint.json"
                meta_path       = rep_dir / "run_meta.json"
                progress_path   = rep_dir / "progress.jsonl"

                summary    = _read_json(summary_path)
                checkpoint = _read_json(checkpoint_path)
                meta       = _read_json(meta_path)

                is_done = summary_path.exists() and bool(summary.get("n_violations_total"))
                is_running = checkpoint_path.exists() and not is_done

                started_at  = _run_meta_started_at(meta_path)
                elapsed_s   = (time.time() - started_at) if started_at else None

                last_prog = _read_last_jsonl(progress_path)
                n_prog_lines = _count_jsonl_lines(progress_path)

                cells.append({
                    "condition":   condition_dir.name,
                    "model":       model_dir.name,
                    "rep":         rep_dir.name,
                    "rep_dir":     rep_dir,
                    "is_done":     is_done,
                    "is_running":  is_running,
                    "is_pending":  not is_done and not is_running,
                    "summary":     summary,
                    "checkpoint":  checkpoint,
                    "meta":        meta,
                    "started_at":  started_at,
                    "elapsed_s":   elapsed_s,
                    "last_prog":   last_prog,
                    "n_prog_lines":n_prog_lines,
                })

    return cells

# ── Rendering ─────────────────────────────────────────────────────────────────

def render(results_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    cells = collect_cells(results_dir)

    n_done    = sum(1 for c in cells if c["is_done"])
    n_running = sum(1 for c in cells if c["is_running"])
    n_pending = sum(1 for c in cells if c["is_pending"])
    n_total   = len(cells)

    print(clr(f"\n  Ablation Study — Live Progress", BOLD, CYAN))
    print(clr(f"  Results: {results_dir}", DIM))
    print(clr(f"  Refreshed: {now.strftime('%H:%M:%S UTC')}", DIM))
    print()

    # ── Overall progress bar ─────────────────────────────────────────────────
    overall_frac = n_done / n_total if n_total else 0.0
    print(f"  Cells  {bar(overall_frac, 30)}  "
          f"{clr(f'{n_done}/{n_total}', BOLD)} done  "
          f"{clr(f'{n_running} running', YELLOW)}  "
          f"{clr(f'{n_pending} pending', DIM)}")
    print()

    # ── Per-cell table ───────────────────────────────────────────────────────
    col_w = [32, 22, 5, 12, 12, 10, 12, 18]
    headers = ["Condition", "Model", "Rep", "Status", "IFR", "Files", "Violations", "ETA / Elapsed"]
    header_row = "  " + "  ".join(h.ljust(col_w[i]) for i, h in enumerate(headers))
    print(clr(header_row, BOLD))
    print("  " + "─" * (sum(col_w) + 2 * len(col_w)))

    for c in cells:
        cond  = c["condition"]
        model = c["model"]
        rep   = c["rep"]

        if c["is_done"]:
            s = c["summary"]
            ifr   = s.get("ifr", 0.0)
            n_res = s.get("n_violations_resolved", 0)
            n_tot = s.get("n_violations_total", 0)
            n_f   = s.get("n_files_total", 0)
            wall  = s.get("wall_clock_total_s", 0.0)
            status_str = clr("DONE", GREEN, BOLD)
            ifr_str    = clr(f"{ifr*100:.1f}%", GREEN if ifr >= 0.6 else YELLOW)
            files_str  = str(n_f)
            viol_str   = f"{n_res}/{n_tot}"
            eta_str    = clr(_fmt_duration(wall), DIM)

        elif c["is_running"]:
            ck  = c["checkpoint"]
            fd  = ck.get("files_done", 0)
            ft  = ck.get("files_total", 1)
            vd  = ck.get("violations_done", 0)
            vr  = ck.get("violations_resolved", 0)
            ifr = ck.get("ifr_running", 0.0)
            el  = c["elapsed_s"] or 0.0
            # ETA based on violations processed vs expected total violations
            # Use progress lines as a proxy for total violations
            total_v_est = ck.get("violations_total_est") or max(vd, 1)
            file_frac = fd / max(ft, 1)
            status_str = clr(f"{'▶ ' + bar(file_frac, 10)}", YELLOW)
            ifr_str    = clr(f"{ifr*100:.1f}%", YELLOW)
            files_str  = f"{fd}/{ft}"
            viol_str   = f"{vr}/{vd}"
            eta_str    = _eta_str(el, fd, ft) + clr(f" ({_fmt_duration(el)})", DIM)

        else:
            status_str = clr("pending", DIM)
            ifr_str    = clr("—", DIM)
            files_str  = clr("—", DIM)
            viol_str   = clr("—", DIM)
            eta_str    = clr("—", DIM)

        row = [cond[:col_w[0]], model[:col_w[1]], rep, status_str,
               ifr_str, files_str, viol_str, eta_str]
        print("  " + "  ".join(str(v).ljust(col_w[i]) for i, v in enumerate(row)))

    print()

    # ── Running cell live feed ───────────────────────────────────────────────
    running = [c for c in cells if c["is_running"]]
    for c in running:
        prog = c["last_prog"]
        if not prog:
            continue
        ck = c["checkpoint"]
        el = c["elapsed_s"] or 0.0
        fd, ft = ck.get("files_done", 0), ck.get("files_total", 1)
        vd, vr = prog.get("violations_done", 0), prog.get("violations_resolved", 0)
        ifr_r  = prog.get("ifr_running", 0.0)
        last_f = ck.get("last_file", "?")
        last_f_ifr = ck.get("last_file_ifr", 0.0)
        wcag_cat = prog.get("wcag_category", "?")
        resolved = prog.get("resolved", False)

        print(clr(f"  ▶ Live: {c['condition']} / {c['model']} / {c['rep']}", BOLD, CYAN))
        print(f"    Elapsed:    {clr(_fmt_duration(el), BOLD)}"
              f"   ETA: {clr(_eta_str(el, fd, ft), MAGENTA)}")
        print(f"    Files:      {clr(f'{fd}/{ft}', BOLD)}  {bar(fd/max(ft,1), 15)}")
        print(f"    Violations: done={vd}  resolved={vr}  "
              f"IFR={clr(f'{ifr_r*100:.1f}%', GREEN if ifr_r >= 0.6 else YELLOW, BOLD)}")
        print(f"    Last file:  {last_f}  (file IFR {last_f_ifr*100:.0f}%)")
        print(f"    Last viol:  [{wcag_cat}]  "
              f"{'✓ resolved' if resolved else '✗ unresolved'}")
        print()

    # ── Summary across DONE cells ────────────────────────────────────────────
    done_cells = [c for c in cells if c["is_done"]]
    if done_cells:
        print(clr("  Completed cell IFR summary:", BOLD))
        by_condition: dict[str, list[float]] = {}
        for c in done_cells:
            cond = c["condition"]
            ifr  = c["summary"].get("ifr", 0.0)
            by_condition.setdefault(cond, []).append(ifr)
        for cond, ifrs in sorted(by_condition.items()):
            mean_ifr = sum(ifrs) / len(ifrs)
            reps_str = "  ".join(f"rep{i+1}={v*100:.1f}%" for i, v in enumerate(ifrs))
            col = GREEN if mean_ifr >= 0.6 else YELLOW if mean_ifr >= 0.4 else RED
            print(f"    {cond:35s}  mean={clr(f'{mean_ifr*100:.1f}%', col, BOLD)}  "
                  f"{clr(reps_str, DIM)}")
        print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Ablation study progress monitor")
    p.add_argument(
        "--results-dir",
        type=Path,
        default=Path(__file__).parent / "results" / "arch_ablation",
        help="Path to arch_ablation results directory",
    )
    p.add_argument(
        "--interval", type=float, default=5.0,
        help="Refresh interval in seconds (default: 5)",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Print once and exit (no loop)",
    )
    args = p.parse_args()

    if args.once:
        render(args.results_dir)
        return

    try:
        while True:
            clear_screen()
            render(args.results_dir)
            print(clr(f"  (refreshes every {args.interval:.0f}s — Ctrl+C to quit)", DIM))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  Monitor stopped.")


if __name__ == "__main__":
    main()
