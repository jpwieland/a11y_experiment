#!/usr/bin/env python3
"""
run_pipeline.py — Executa o pipeline completo de coleta de dados com retries.

Uso:
    python run_pipeline.py
    python run_pipeline.py --workers 8
    python run_pipeline.py --retries 5 --workers 4
    python run_pipeline.py --skip-snapshot   # só scan
    python run_pipeline.py --skip-scan       # só snapshot
"""

import argparse
import subprocess
import sys
import time
import yaml
from collections import Counter
from pathlib import Path

# ─── Configuração ─────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
CATALOG_PATH = SCRIPT_DIR / "dataset" / "catalog" / "projects.yaml"
SNAPSHOT_PY  = SCRIPT_DIR / "dataset" / "scripts" / "snapshot.py"
SCAN_PY      = SCRIPT_DIR / "dataset" / "scripts" / "scan.py"
PYTHON       = sys.executable

# ─── Helpers ──────────────────────────────────────────────────────────────────
def status_counter() -> Counter:
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    return Counter(p.get("status", "?") for p in data.get("projects", []))

def print_status(label: str = ""):
    c = status_counter()
    total = sum(c.values())
    if label:
        print(f"\n{'─'*50}")
        print(f"  {label}")
        print(f"{'─'*50}")
    print(f"  Total   : {total}")
    for status, count in sorted(c.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 40)
        print(f"  {status:<15}: {count:>3}  {bar}")

def run(cmd: list, label: str) -> bool:
    print(f"\n▶  {label}")
    print(f"   $ {' '.join(str(c) for c in cmd)}\n")
    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start
    ok = result.returncode == 0
    icon = "✓" if ok else "✗"
    print(f"\n{icon}  {label} — {'OK' if ok else 'FALHOU'} ({elapsed:.0f}s)")
    return ok

def pending_snapshot(force_statuses: list) -> int:
    c = status_counter()
    return sum(c.get(s, 0) for s in force_statuses)

# ─── Fases ────────────────────────────────────────────────────────────────────
def phase_snapshot(workers: int, force: bool = False) -> bool:
    cmd = [PYTHON, str(SNAPSHOT_PY), "--workers", str(workers)]
    if force:
        cmd.append("--force")
    label = "Snapshot" + (" (--force)" if force else "")
    return run(cmd, label)

def phase_scan(workers: int, timeout: int, force: bool = False) -> bool:
    cmd = [PYTHON, str(SCAN_PY), "--workers", str(workers), "--timeout", str(timeout)]
    if force:
        cmd.append("--force")
    label = "Scan" + (" (--force)" if force else "")
    return run(cmd, label)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Pipeline completo com retries")
    parser.add_argument("--workers",       type=int, default=4,   help="Workers paralelos (default: 4)")
    parser.add_argument("--retries",       type=int, default=3,   help="Tentativas em caso de erro (default: 3)")
    parser.add_argument("--scan-timeout",  type=int, default=180, help="Timeout de scan por arquivo em segundos (default: 180)")
    parser.add_argument("--skip-snapshot", action="store_true",   help="Pular fase de snapshot")
    parser.add_argument("--skip-scan",     action="store_true",   help="Pular fase de scan")
    args = parser.parse_args()

    print("=" * 50)
    print("  a11y-autofix — Pipeline Completo")
    print("=" * 50)
    print(f"  workers     : {args.workers}")
    print(f"  retries     : {args.retries}")
    print(f"  scan-timeout: {args.scan_timeout}s")

    print_status("Status inicial")

    # ── Fase 1: Snapshot ──────────────────────────────────────────────────────
    if not args.skip_snapshot:
        print("\n" + "═" * 50)
        print("  FASE 1 — SNAPSHOT")
        print("═" * 50)

        # 1a. Snapshot dos pending/candidate
        for attempt in range(1, args.retries + 1):
            c = status_counter()
            pending = c.get("pending", 0) + c.get("candidate", 0)
            if pending == 0:
                print("  Nenhum projeto pending/candidate — pulando snapshot inicial.")
                break
            print(f"\n  Tentativa {attempt}/{args.retries} — {pending} projetos para snapshottear")
            phase_snapshot(args.workers, force=False)
            print_status("Após snapshot")

        # 1b. Retry dos errors com --force
        for attempt in range(1, args.retries + 1):
            c = status_counter()
            errors = c.get("error", 0)
            if errors == 0:
                print("  Sem erros de snapshot — OK.")
                break
            print(f"\n  Retry {attempt}/{args.retries} dos {errors} projetos com erro...")
            phase_snapshot(args.workers, force=True)
            print_status(f"Após retry snapshot #{attempt}")
            new_errors = status_counter().get("error", 0)
            if new_errors >= errors:
                print(f"  Erros não reduziram ({errors} → {new_errors}). Desistindo de retries.")
                break

    # ── Fase 2: Scan ──────────────────────────────────────────────────────────
    if not args.skip_scan:
        print("\n" + "═" * 50)
        print("  FASE 2 — SCAN")
        print("═" * 50)

        # 2a. Scan dos snapshotted
        for attempt in range(1, args.retries + 1):
            c = status_counter()
            to_scan = c.get("snapshotted", 0)
            if to_scan == 0:
                print("  Nenhum projeto snapshotted pendente de scan — OK.")
                break
            print(f"\n  Tentativa {attempt}/{args.retries} — {to_scan} projetos para escanear")
            phase_scan(args.workers, args.scan_timeout, force=False)
            print_status(f"Após scan #{attempt}")
            new_to_scan = status_counter().get("snapshotted", 0)
            if new_to_scan == 0:
                break
            if new_to_scan >= to_scan:
                print(f"  Scan não progrediu ({to_scan} → {new_to_scan}). Desistindo de retries.")
                break

        # 2b. Rescan dos que falharam (ficaram em snapshotted após tentativas)
        c = status_counter()
        if c.get("snapshotted", 0) > 0:
            print(f"\n  {c['snapshotted']} projetos ainda snapshotted — tentando scan com --force...")
            phase_scan(args.workers, args.scan_timeout, force=True)

    # ── Resultado final ───────────────────────────────────────────────────────
    print("\n" + "═" * 50)
    print("  RESULTADO FINAL")
    print("═" * 50)
    print_status()
    c = status_counter()
    scanned = c.get("scanned", 0)
    total   = sum(c.values())
    excluded = c.get("excluded", 0)
    usable  = total - excluded
    print(f"\n  Escaneados : {scanned}/{usable} projetos elegíveis")
    if c.get("error", 0):
        print(f"  ⚠  {c['error']} projetos com erro persistente (veja logs acima)")
    if scanned == usable:
        print("  ✓ Pipeline concluído com sucesso!")
    else:
        print(f"  ⚠  {usable - scanned} projetos ainda não escaneados.")
    print()

if __name__ == "__main__":
    main()
