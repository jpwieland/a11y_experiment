#!/usr/bin/env python3
"""
monitor.py — Ablation Study Live Dashboard
===========================================

Run this in a separate terminal while ablation_runner.py is executing:

    python ablation_study/monitor.py

Or point it at a custom results directory:

    python ablation_study/monitor.py --results-dir path/to/results

Controls:
  Ctrl-C  →  Exit the monitor (does NOT stop the experiment)
  q       →  Same

Requirements: rich >= 13.0  (pip install rich)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Make ablation_study importable regardless of cwd ──────────────────────────
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # repo root

try:
    from rich.console import Console
except ImportError:
    print("ERROR: 'rich' is not installed.  Run:  pip install rich>=13.0")
    sys.exit(1)

from ablation_study.src.progress_dashboard import run_monitor


def _default_results_dir() -> Path:
    return _HERE / "results"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live progress dashboard for the a11y ablation study",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--results-dir",
        type=Path,
        default=_default_results_dir(),
        metavar="DIR",
        help=f"Results directory (default: {_default_results_dir()})",
    )
    p.add_argument(
        "--refresh",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Refresh interval in seconds (default: 1.0)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    console = Console()

    if not args.results_dir.exists():
        console.print(
            f"[yellow]Results directory does not exist yet:[/yellow] {args.results_dir}\n"
            f"The dashboard will start updating once the experiment begins."
        )

    console.print(
        f"[bold bright_blue]A11Y Ablation Study Monitor[/bold bright_blue]"
        f"  │  Watching [cyan]{args.results_dir}[/cyan]"
        f"  │  Refresh: [green]{args.refresh}s[/green]\n"
        f"[dim]Press Ctrl-C to exit (the experiment keeps running)[/dim]\n"
    )

    run_monitor(results_dir=args.results_dir, refresh_s=args.refresh)


if __name__ == "__main__":
    main()
