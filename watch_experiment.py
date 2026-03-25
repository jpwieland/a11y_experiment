#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch_experiment.py — Dashboard de progresso ao vivo para o experimento LLM.

Lê experiment_progress.json e os checkpoints em tempo real.
Pode ser executado de qualquer sessão SSH enquanto o experimento roda em tmux.

Uso:
    python watch_experiment.py                                     # busca auto
    python watch_experiment.py experiment-results/14b_comparison  # diretório específico
    python watch_experiment.py --once                              # imprime 1x e sai
    python watch_experiment.py --interval 5                        # atualiza a cada 5s
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent

# ── ANSI ──────────────────────────────────────────────────────────────────────
R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"
CYAN = "\033[96m"; BLUE = "\033[94m"; MAGENTA = "\033[95m"


def _cls() -> None:
    if platform.system() == "Windows":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="", flush=True)


def _w() -> int:
    try:
        return min(os.get_terminal_size().columns, 110)
    except OSError:
        return 90


def _bar(v: float, total: float, w: int = 20) -> str:
    if total <= 0:
        return DIM + "░" * w + R
    filled = round(min(v / total, 1.0) * w)
    return GREEN + "█" * filled + DIM + "░" * (w - filled) + R


def _pct(n: int, d: int) -> str:
    return f"{n/d*100:.1f}%" if d > 0 else "—"


def _elapsed(ts: str) -> str:
    try:
        start = datetime.fromisoformat(ts)
        s = int((datetime.now(tz=timezone.utc) - start).total_seconds())
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"
    except Exception:
        return "?"


def _find_latest_output_dir() -> Path | None:
    """Encontra o diretório de saída mais recente em experiment-results/."""
    base = REPO_ROOT / "experiment-results"
    if not base.exists():
        return None
    dirs = sorted(
        [d for d in base.iterdir() if d.is_dir() and (d / "experiment_progress.json").exists()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


def _load_progress(output_dir: Path) -> dict | None:
    fp = output_dir / "experiment_progress.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _count_checkpoints(output_dir: Path) -> dict[str, dict]:
    """Conta checkpoints por (model, status) para métricas precisas."""
    counts: dict[str, dict] = {}
    cp_dir = output_dir / "checkpoints"
    if not cp_dir.exists():
        return counts
    for model_dir in cp_dir.iterdir():
        if not model_dir.is_dir():
            continue
        model = model_dir.name
        counts[model] = {"success": 0, "failed": 0,
                         "issues_fixed": 0, "issues_total": 0}
        for strategy_dir in model_dir.iterdir():
            if not strategy_dir.is_dir():
                continue
            for cp_file in strategy_dir.glob("*.json"):
                try:
                    cp = json.loads(cp_file.read_text(encoding="utf-8"))
                    if cp.get("status") == "success":
                        counts[model]["success"] += 1
                    else:
                        counts[model]["failed"] += 1
                    counts[model]["issues_fixed"] += cp.get("ifr_numerator", 0) or 0
                    counts[model]["issues_total"] += cp.get("ifr_denominator", 0) or 0
                except Exception:
                    pass
    return counts


def _read_gpu_stats() -> str | None:
    """Lê VRAM atual via nvidia-smi (sincrono, para o dashboard)."""
    import shutil
    import subprocess
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        lines = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            idx, name, used, total, util, temp = parts[:6]
            used_gb = int(used) / 1024
            total_gb = int(total) / 1024
            pct = int(used) / max(int(total), 1) * 100
            bar_w = 14
            filled = round(min(pct / 100, 1.0) * bar_w)
            bar = GREEN + "█" * filled + DIM + "░" * (bar_w - filled) + R
            lines.append(
                f"  GPU {idx} {CYAN}{name[:24]:<24}{R}  "
                f"VRAM [{bar}] {CYAN}{used_gb:.1f}{R}/{total_gb:.0f} GB  "
                f"Util {YELLOW}{util:>3}%{R}  {DIM}{temp}°C{R}"
            )
        return "\n".join(lines) if lines else None
    except Exception:
        return None


def _load_clone_log(output_dir: Path) -> list[dict]:
    fp = output_dir / "auto_clone.jsonl"
    if not fp.exists():
        return []
    clones = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                clones.append(json.loads(line))
            except Exception:
                pass
    return clones


def render(output_dir: Path) -> list[str]:
    W = _w()
    lines: list[str] = []
    sep = "─" * W

    def add(s: str = "") -> None:
        lines.append(s)

    progress = _load_progress(output_dir)
    checkpoints = _count_checkpoints(output_dir)
    clones = _load_clone_log(output_dir)

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M:%S")
    add(f"{BOLD}{sep}{R}")
    add(f"{BOLD}  ♿  a11y-autofix — LLM Experiment Monitor   {DIM}{now}{R}")
    add(f"{BOLD}{sep}{R}")

    if progress is None:
        add(f"\n  {YELLOW}Aguardando experiment_progress.json em:{R}")
        add(f"  {DIM}{output_dir}{R}\n")
        return lines

    started = progress.get("started_at", "")
    total_files = progress.get("total_files", 0)
    models_state = progress.get("models", {})

    elapsed = _elapsed(started) if started else "?"
    add(f"  {DIM}Diretório: {output_dir.name}   Iniciado há: {elapsed}{R}")
    add(f"  Arquivos no corpus: {CYAN}{total_files}{R}")
    add()

    # ── Tabela por modelo ──────────────────────────────────────────────────────
    add(f"  {BOLD}{'Modelo':<32} {'Status':<10} {'Progresso':>20}  {'SR':>6}  {'IFR':>6}  {'Arquivo atual'}{R}")
    add(f"  {DIM}{sep}{R}")

    for model, state in models_state.items():
        done = state.get("done", 0)
        success = state.get("success", 0)
        failed = state.get("failed", 0)
        issues_fixed = state.get("issues_fixed", 0)
        issues_total = state.get("issues_total", 0)
        status = state.get("status", "?")
        current = state.get("current_file") or ""

        # Usar checkpoints como fonte mais precisa se disponível
        cp = checkpoints.get(model.replace("/", "_"), {})
        if cp:
            done = cp["success"] + cp["failed"]
            success = cp["success"]
            issues_fixed = cp["issues_fixed"]
            issues_total = cp["issues_total"]

        bar = _bar(done, total_files, w=16)
        sr = _pct(success, done) if done else "—"
        ifr = _pct(issues_fixed, issues_total) if issues_total else "—"

        status_color = (GREEN if status == "done" else
                        CYAN if status == "running" else
                        YELLOW if status == "loading" else
                        RED if status == "error" else DIM)
        status_str = f"{status_color}{status:<10}{R}"

        short_model = model.split("/")[-1][:31]
        short_file = current[:35] + "…" if len(current) > 35 else current

        add(f"  {BOLD}{short_model:<32}{R} {status_str} {bar} {done:>3}/{total_files}  "
            f"{CYAN}{sr:>6}{R}  {GREEN}{ifr:>6}{R}  {DIM}{short_file}{R}")

    add()

    # ── Totais ────────────────────────────────────────────────────────────────
    total_done = sum(
        (cp.get("success", 0) + cp.get("failed", 0))
        for cp in checkpoints.values()
    ) if checkpoints else sum(
        s.get("done", 0) for s in models_state.values()
    )
    total_success = sum(
        cp.get("success", 0) for cp in checkpoints.values()
    ) if checkpoints else sum(
        s.get("success", 0) for s in models_state.values()
    )
    total_issues_fixed = sum(cp.get("issues_fixed", 0) for cp in checkpoints.values())
    total_issues = sum(cp.get("issues_total", 0) for cp in checkpoints.values())

    add(f"  {DIM}{sep}{R}")
    add(f"  Total checkpoints: {CYAN}{total_done}{R}  "
        f"Sucesso: {GREEN}{total_success}{R}  "
        f"SR: {CYAN}{_pct(total_success, total_done)}{R}  "
        f"IFR: {GREEN}{_pct(total_issues_fixed, total_issues)}{R}")

    # ── GPU ───────────────────────────────────────────────────────────────────
    gpu_str = _read_gpu_stats()
    if gpu_str:
        add()
        add(f"  {BOLD}GPU{R}")
        add(gpu_str)

    # ── Auto-clones ───────────────────────────────────────────────────────────
    if clones:
        add()
        add(f"  {YELLOW}⬇  Snapshots clonados automaticamente ({len(clones)}):{R}")
        for c in clones[-5:]:
            icon = GREEN + "✔" + R if c.get("status") == "cloned" else RED + "✘" + R
            elapsed_clone = f"  {c.get('elapsed_s', '?')}s" if c.get('elapsed_s') else ""
            add(f"    {icon} {c.get('project_id', '?')}{DIM}{elapsed_clone}{R}")

    # ── Dica de navegação ─────────────────────────────────────────────────────
    add()
    add(f"  {DIM}Ctrl+C para sair  │  tmux attach -t a11y-exp para ver logs completos{R}")
    add(f"  {BOLD}{sep}{R}")

    return lines


def watch(output_dir: Path, interval: int, once: bool) -> None:
    while True:
        lines = render(output_dir)
        _cls()
        print("\n".join(lines), flush=True)
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor ao vivo do experimento LLM de acessibilidade."
    )
    parser.add_argument(
        "output_dir", nargs="?", default=None,
        help="Diretório de saída do experimento (busca automática se omitido).",
    )
    parser.add_argument("--once", action="store_true",
                        help="Imprimir uma vez e sair.")
    parser.add_argument("--interval", type=int, default=4,
                        help="Intervalo de atualização em segundos (default: 4).")
    args = parser.parse_args()

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = _find_latest_output_dir()
        if output_dir is None:
            # Fallback: mostrar diretórios disponíveis
            base = REPO_ROOT / "experiment-results"
            print(f"Nenhum experimento ativo encontrado em {base}")
            print("Uso: python watch_experiment.py <diretório>")
            sys.exit(1)
        print(f"  Monitorando: {output_dir}")

    try:
        watch(output_dir, interval=args.interval, once=args.once)
    except KeyboardInterrupt:
        print(f"\n  Monitor encerrado.")


if __name__ == "__main__":
    main()
