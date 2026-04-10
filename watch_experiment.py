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
WHITE = "\033[97m"


def _cls() -> None:
    if platform.system() == "Windows":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="", flush=True)


def _w() -> int:
    try:
        return min(os.get_terminal_size().columns, 120)
    except OSError:
        return 100


def _bar(v: float, total: float, w: int = 20, color: str = GREEN) -> str:
    if total <= 0:
        return DIM + "░" * w + R
    frac = min(v / total, 1.0)
    filled = round(frac * w)
    return color + "█" * filled + DIM + "░" * (w - filled) + R


def _pct(n: int | float, d: int | float) -> str:
    return f"{n/d*100:.1f}%" if d > 0 else "—"


def _fmt_eta(seconds: int | float | None) -> str:
    """Formata segundos como '2h34m', '12m30s' ou '45s'."""
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    if s == 0:
        return "—"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _fmt_elapsed(ts: str | None) -> str:
    """Calcula tempo decorrido desde um ISO timestamp."""
    if not ts:
        return "?"
    try:
        start = datetime.fromisoformat(ts)
        s = int((datetime.now(tz=timezone.utc) - start).total_seconds())
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"
    except Exception:
        return "?"


def _fmt_speed(avg_s: float | None) -> str:
    """Formata velocidade de processamento."""
    if avg_s is None or avg_s <= 0:
        return "  —  "
    if avg_s < 60:
        return f"{avg_s:.1f}s/arq"
    return f"{avg_s/60:.1f}m/arq"


def _fmt_tokens(n: int) -> str:
    """Formata contagem de tokens de forma legível."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def _find_latest_output_dir() -> Path | None:
    base = REPO_ROOT / "experiment-results"
    if not base.exists():
        return None
    dirs = sorted(
        [d for d in base.iterdir()
         if d.is_dir() and (d / "experiment_progress.json").exists()],
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


def _read_gpu_stats() -> list[dict] | None:
    """Lê stats de GPU via nvidia-smi. Retorna lista de dicts por GPU."""
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
        gpus = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            idx, name, used, total, util, temp = parts[:6]
            gpus.append({
                "index": int(idx),
                "name": name,
                "used_mb": int(used),
                "total_mb": int(total),
                "util_pct": int(util),
                "temp_c": int(temp),
            })
        return gpus or None
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


def _overall_eta(models_state: dict, total_files: int) -> int | None:
    """
    Estima ETA total do experimento.
    Para modelos sequenciais: soma os ETAs individuais de todos os modelos
    não concluídos (running + waiting).
    """
    total_eta = 0
    has_estimate = False
    for state in models_state.values():
        status = state.get("status", "")
        if status in ("done", "error"):
            continue
        eta = state.get("eta_seconds")
        if eta is not None:
            total_eta += eta
            has_estimate = True
        elif status == "waiting":
            # Estimar baseado na média dos modelos que já rodaram
            avg_file_times = [
                s["avg_time_per_file_s"]
                for s in models_state.values()
                if s.get("avg_time_per_file_s") and s.get("status") == "done"
            ]
            if avg_file_times:
                avg = sum(avg_file_times) / len(avg_file_times)
                total_eta += round(avg * total_files)
                has_estimate = True
    return total_eta if has_estimate else None


def render(output_dir: Path) -> list[str]:
    W = _w()
    lines: list[str] = []
    sep = "─" * W
    sep_thin = "·" * W

    def add(s: str = "") -> None:
        lines.append(s)

    progress = _load_progress(output_dir)
    checkpoints = _count_checkpoints(output_dir)
    clones = _load_clone_log(output_dir)
    gpus = _read_gpu_stats()

    now_str = datetime.now().strftime("%H:%M:%S")

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    add(f"{BOLD}{sep}{R}")
    add(f"{BOLD}  ♿  a11y-autofix — Monitor de Experimento LLM   {DIM}{now_str}{R}")
    add(f"{BOLD}{sep}{R}")

    if progress is None:
        add(f"\n  {YELLOW}Aguardando experiment_progress.json em:{R}")
        add(f"  {DIM}{output_dir}{R}")
        add()
        add(f"  {DIM}Inicie o experimento com:{R}")
        add(f"  {CYAN}  a11y-autofix experiment experiments/experiment_weak_gpu.yaml{R}")
        add()
        return lines

    started = progress.get("started_at", "")
    finished = progress.get("finished_at")
    total_files = progress.get("total_files", 0)
    models_state = progress.get("models", {})

    elapsed = _fmt_elapsed(started) if started else "?"

    # Status geral
    is_finished = bool(finished)
    if is_finished:
        status_label = f"{GREEN}CONCLUÍDO{R}"
    else:
        status_label = f"{CYAN}EM EXECUÇÃO{R}"

    add(f"  {DIM}Diretório: {BOLD}{output_dir.name}{R}{DIM}   Iniciado há: {elapsed}   {R}{status_label}")
    add()

    # ── Progresso Geral ───────────────────────────────────────────────────────
    n_models = len(models_state)
    total_slots = total_files * n_models  # total de (model, file) pares

    # Coletar totais de checkpoints
    total_done_cp = sum(cp.get("success", 0) + cp.get("failed", 0)
                        for cp in checkpoints.values())
    total_success_cp = sum(cp.get("success", 0) for cp in checkpoints.values())
    total_issues_fixed_cp = sum(cp.get("issues_fixed", 0) for cp in checkpoints.values())
    total_issues_cp = sum(cp.get("issues_total", 0) for cp in checkpoints.values())

    # Fallback para progress.json se não houver checkpoints
    if not checkpoints and models_state:
        total_done_cp = sum(s.get("done", 0) for s in models_state.values())
        total_success_cp = sum(s.get("success", 0) for s in models_state.values())

    overall_eta = _overall_eta(models_state, total_files)
    overall_bar = _bar(total_done_cp, total_slots, w=30)
    overall_pct = _pct(total_done_cp, total_slots) if total_slots > 0 else "—"

    add(f"  {BOLD}PROGRESSO GERAL{R}  {overall_bar} "
        f"{CYAN}{total_done_cp}/{total_slots}{R} ({overall_pct})   "
        f"ETA: {YELLOW}{_fmt_eta(overall_eta)}{R}")
    add(f"  {DIM}{sep_thin}{R}")
    add()

    # ── Tabela por modelo ──────────────────────────────────────────────────────
    col_model   = 28
    col_status  =  8
    col_bar     = 18
    col_cnt     =  8
    col_sr      =  7
    col_ifr     =  7
    col_speed   =  9
    col_eta     =  8
    col_tokens  =  7

    hdr = (
        f"  {BOLD}{'Modelo':<{col_model}} {'Status':<{col_status}} "
        f"{'Progresso':^{col_bar+2}} {'Files':>{col_cnt}} "
        f"{'SR':>{col_sr}} {'IFR':>{col_ifr}} "
        f"{'Vel':>{col_speed}} {'ETA':>{col_eta}} {'Tokens':>{col_tokens}}{R}"
    )
    add(hdr)
    add(f"  {DIM}{sep}{R}")

    for model, state in models_state.items():
        done    = state.get("done", 0)
        success = state.get("success", 0)
        issues_fixed  = state.get("issues_fixed", 0)
        issues_total  = state.get("issues_total", 0)
        status  = state.get("status", "?")
        current = state.get("current_file") or ""
        avg_t   = state.get("avg_time_per_file_s")
        eta_s   = state.get("eta_seconds")
        tok_out = state.get("tokens_output") or 0
        model_started = state.get("started_at")

        # Usar checkpoints como fonte mais precisa se disponível
        cp_key = model.replace("/", "_")
        cp = checkpoints.get(cp_key, {})
        if cp:
            done         = cp["success"] + cp["failed"]
            success      = cp["success"]
            issues_fixed = cp["issues_fixed"]
            issues_total = cp["issues_total"]

        # Barra de progresso do modelo
        bar_color = (GREEN if status == "done" else
                     CYAN  if status == "running" else
                     YELLOW if status == "loading" else DIM)
        bar = _bar(done, total_files, w=col_bar, color=bar_color)

        sr_str  = f"{success/done*100:.0f}%" if done > 0 else "—"
        ifr_str = f"{issues_fixed/issues_total*100:.0f}%" if issues_total > 0 else "—"
        spd_str = _fmt_speed(avg_t)
        eta_str = _fmt_eta(eta_s) if status not in ("done", "waiting") else (
            "—" if status == "done" else "aguarda"
        )
        tok_str = _fmt_tokens(tok_out) if tok_out > 0 else "—"

        status_colors = {
            "done":    GREEN,
            "running": CYAN,
            "loading": YELLOW,
            "error":   RED,
            "waiting": DIM,
        }
        sc = status_colors.get(status, DIM)
        status_icons = {
            "done": "✔ DONE",
            "running": "▶ EXEC",
            "loading": "⟳ LOAD",
            "error": "✘ ERR",
            "waiting": "… WAIT",
        }
        status_str = f"{sc}{status_icons.get(status, status):<{col_status}}{R}"

        short_model = model.split("/")[-1][:col_model]

        add(
            f"  {BOLD}{short_model:<{col_model}}{R} {status_str} "
            f"{bar} {CYAN}{done:>{col_cnt-3}}/{total_files:<3}{R} "
            f"{GREEN}{sr_str:>{col_sr}}{R} {YELLOW}{ifr_str:>{col_ifr}}{R} "
            f"{DIM}{spd_str:>{col_speed}}{R} {MAGENTA}{eta_str:>{col_eta}}{R} "
            f"{DIM}{tok_str:>{col_tokens}}{R}"
        )

        # Linha com arquivo atual (só se estiver rodando)
        if status == "running" and current:
            short_file = current[:60] + "…" if len(current) > 60 else current
            model_elapsed = _fmt_elapsed(model_started) if model_started else "?"
            add(f"  {DIM}  └─ {short_file}   (modelo rodando há {model_elapsed}){R}")

    add()

    # ── Totais ────────────────────────────────────────────────────────────────
    add(f"  {DIM}{sep_thin}{R}")
    sr_total  = _pct(total_success_cp, total_done_cp)
    ifr_total = _pct(total_issues_fixed_cp, total_issues_cp)
    add(
        f"  {BOLD}TOTAL{R}  "
        f"Arquivos: {CYAN}{total_done_cp}/{total_slots}{R}   "
        f"SR: {GREEN}{sr_total}{R}   "
        f"IFR: {YELLOW}{ifr_total}{R}   "
        f"Issues corrigidas: {GREEN}{total_issues_fixed_cp}/{total_issues_cp}{R}"
    )
    add()

    # ── GPU ───────────────────────────────────────────────────────────────────
    if gpus:
        add(f"  {BOLD}GPU{R}")
        for g in gpus:
            used_gb  = g["used_mb"] / 1024
            total_gb = g["total_mb"] / 1024
            pct      = g["used_mb"] / max(g["total_mb"], 1) * 100
            bar_w    = 16
            filled   = round(min(pct / 100, 1.0) * bar_w)
            vram_bar = GREEN + "█" * filled + DIM + "░" * (bar_w - filled) + R
            util_col = (RED if g["util_pct"] > 90 else
                        YELLOW if g["util_pct"] > 70 else GREEN)
            add(
                f"  GPU {g['index']} {CYAN}{g['name'][:26]:<26}{R}  "
                f"VRAM [{vram_bar}] {CYAN}{used_gb:.1f}{R}/{total_gb:.0f} GB  "
                f"Util {util_col}{g['util_pct']:>3}%{R}  {DIM}{g['temp_c']}°C{R}"
            )
        add()
    else:
        add(f"  {DIM}GPU: nvidia-smi não encontrado (CPU ou driver não instalado){R}")
        add()

    # ── Estatísticas parciais detalhadas ──────────────────────────────────────
    running_models = [m for m, s in models_state.items() if s.get("status") == "running"]
    done_models    = [m for m, s in models_state.items() if s.get("status") == "done"]

    if done_models or running_models:
        add(f"  {BOLD}ESTATÍSTICAS PARCIAIS{R}")
        # Métricas dos modelos concluídos
        for model in done_models:
            state = models_state[model]
            done      = state.get("done", 0)
            success   = state.get("success", 0)
            iss_fixed = state.get("issues_fixed", 0)
            iss_total = state.get("issues_total", 0)
            avg_t     = state.get("avg_time_per_file_s")
            tok_out   = state.get("tokens_output") or 0
            short = model.split("/")[-1][:24]
            add(
                f"  {GREEN}✔{R} {BOLD}{short:<24}{R}  "
                f"SR={GREEN}{_pct(success, done)}{R}  "
                f"IFR={YELLOW}{_pct(iss_fixed, iss_total)}{R}  "
                f"vel={DIM}{_fmt_speed(avg_t)}{R}  "
                f"tok={DIM}{_fmt_tokens(tok_out)}{R}"
            )
        for model in running_models:
            state = models_state[model]
            done      = state.get("done", 0)
            success   = state.get("success", 0)
            iss_fixed = state.get("issues_fixed", 0)
            iss_total = state.get("issues_total", 0)
            avg_t     = state.get("avg_time_per_file_s")
            eta_s     = state.get("eta_seconds")
            tok_out   = state.get("tokens_output") or 0
            short = model.split("/")[-1][:24]
            add(
                f"  {CYAN}▶{R} {BOLD}{short:<24}{R}  "
                f"SR={GREEN}{_pct(success, done)}{R}  "
                f"IFR={YELLOW}{_pct(iss_fixed, iss_total)}{R}  "
                f"vel={DIM}{_fmt_speed(avg_t)}{R}  "
                f"ETA={MAGENTA}{_fmt_eta(eta_s)}{R}  "
                f"tok={DIM}{_fmt_tokens(tok_out)}{R}"
            )
        add()

    # ── Auto-clones ───────────────────────────────────────────────────────────
    if clones:
        n_ok  = sum(1 for c in clones if c.get("status") == "cloned")
        n_err = len(clones) - n_ok
        add(f"  {YELLOW}⬇  Snapshots clonados automaticamente: {n_ok} OK  {n_err} erro(s){R}")
        for c in clones[-3:]:
            icon = GREEN + "✔" + R if c.get("status") == "cloned" else RED + "✘" + R
            elapsed_clone = f"  {c.get('elapsed_s', '?')}s" if c.get('elapsed_s') else ""
            add(f"    {icon} {c.get('project_id', '?')}{DIM}{elapsed_clone}{R}")
        add()

    # ── Rodapé ────────────────────────────────────────────────────────────────
    add(f"  {DIM}Ctrl+C para sair  │  atualiza a cada __INTERVAL__s  │  tmux attach -t a11y-exp{R}")
    add(f"  {BOLD}{sep}{R}")

    return lines


def watch(output_dir: Path, interval: int, once: bool) -> None:
    while True:
        lines = render(output_dir)
        # Substituir placeholder do intervalo
        lines = [l.replace("__INTERVAL__", str(interval)) for l in lines]
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
