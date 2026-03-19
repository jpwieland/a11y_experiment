#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reset_consistent.py -- sincroniza o catalogo com o estado real do disco.

Reconcilia discrepâncias entre o status no catálogo e os arquivos em
dataset/snapshots/ e dataset/results/. Ideal para usar após um reset
parcial mal-sucedido que deixou o catálogo em estado inconsistente.

  ✓  Mantém: dataset/snapshots/<projeto>/   repos já clonados
  ✓  Mantém: dataset/catalog/projects.yaml  (com backup timestampado)
  ✗  Reseta: campos scan.* e annotation_summary em cada projeto afetado
  ✗  Apaga:  dataset/results/               todos os resultados de scan

Regras de reset de status:
  - excluded                       → permanece excluded (nunca tocado)
  - candidate / pending            → sem alteração (já aguardam snapshot)
  - scanned / annotated / error    → snapshotted  (se snapshot dir existe no disco)
                                   → pending      (se NÃO há snapshot no disco)
  - snapshotted                    → snapshotted  (se snapshot dir existe no disco)
                                   → pending      (se NÃO há snapshot no disco)

Uso:
    python dataset/scripts/reset_consistent.py            # interativo
    python dataset/scripts/reset_consistent.py --dry-run  # simula, nada é alterado
    python dataset/scripts/reset_consistent.py --yes      # sem confirmação
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT     = Path(__file__).parent.parent.parent
DATASET_ROOT  = REPO_ROOT / "dataset"
DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
RESULTS_DIR   = DATASET_ROOT / "results"

sys.path.insert(0, str(REPO_ROOT))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# Statuses que são candidatos a reset
_RESET_STATUSES = {"scanned", "annotated", "error", "snapshotted"}

# Scan em branco compatível com o modelo Pydantic (FindingSummary / ProjectScanSummary)
_EMPTY_SCAN: dict = {
    "status": "pending",
    "findings": {
        "total_issues": 0,
        "high_confidence": 0,
        "medium_confidence": 0,
        "low_confidence": 0,
        "files_scanned": 0,
        "files_with_issues": 0,
        "scan_duration_seconds": 0.0,
        "scan_date": "",
        "tools_succeeded": [],
        "tool_versions": {},
        "by_type": {},
        "by_principle": {},
        "by_impact": {},
        "by_criterion": {},
    },
    "error_message": "",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def snapshot_exists(project_id: str) -> bool:
    """Retorna True se o diretório de snapshot existe e não está vazio."""
    p = SNAPSHOTS_DIR / project_id
    if not p.is_dir():
        return False
    try:
        return any(p.iterdir())
    except PermissionError:
        return False


def load_catalog(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_catalog(data: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def clear_results_dir() -> int:
    """Remove todos os arquivos dentro de RESULTS_DIR. Retorna o total removido."""
    if not RESULTS_DIR.exists():
        return 0
    n = 0
    for f in RESULTS_DIR.rglob("*"):
        if f.is_file():
            f.unlink()
            n += 1
    # Remover dirs vazios (preserva o diretório raiz results/)
    for d in sorted(RESULTS_DIR.rglob("*"), reverse=True):
        if d.is_dir() and d != RESULTS_DIR:
            try:
                d.rmdir()
            except OSError:
                pass
    return n


# ── Reset de projeto ──────────────────────────────────────────────────────────

def compute_new_status(project: dict, include_excluded: bool = False) -> str | None:
    """
    Calcula o novo status para um projeto baseado no estado do disco.
    Retorna None se o projeto não deve ser alterado.

    include_excluded=True: re-habilita projetos excluídos por IC4 sem clone real.
    Esses projetos têm component_file_count=0 e pinned_commit vazio, o que indica
    que o IC4 rodou sem clonar o repo — exclusão falsa positiva sistematica.
    """
    old = project.get("status", "candidate")

    if old == "excluded":
        if not include_excluded:
            return None
        # Só re-habilitar se a exclusão foi por IC4 sem clone real
        snap = project.get("snapshot") or {}
        screening = project.get("screening") or {}
        ic4 = screening.get("ic4_component_files") or {}
        ic4_fail = (isinstance(ic4, dict) and ic4.get("status") == "fail") or ic4 == "fail"
        never_cloned = not snap.get("pinned_commit")
        if ic4_fail and never_cloned:
            return "pending"
        return None

    if old not in _RESET_STATUSES:
        return None
    return "snapshotted" if snapshot_exists(project["id"]) else "pending"


def apply_reset(project: dict, new_status: str) -> None:
    """Aplica o reset in-place no dict do projeto."""
    old_status = project.get("status")
    project["status"] = new_status
    project["scan"] = _EMPTY_SCAN.copy()
    if "annotation_summary" in project:
        project["annotation_summary"] = {}
    # Se estava excluído, limpar screening para que snapshot.py re-avalie IC4
    if old_status == "excluded":
        project["screening"] = {}
        snap = project.get("snapshot") or {}
        # Zerar contagem de arquivos (será recontada no snapshot)
        snap["component_file_count"] = 0
        project["snapshot"] = snap


# ── Exibição ──────────────────────────────────────────────────────────────────

_STATUS_COLORS = {
    "scanned":     "green",
    "annotated":   "cyan",
    "snapshotted": "yellow",
    "pending":     "bright_cyan",
    "candidate":   "dim white",
    "error":       "red",
    "excluded":    "dim",
}


def _make_status_bar(by_status: Counter, total: int) -> Table:
    tbl = Table.grid(padding=(0, 3))
    tbl.add_column(style="dim white", width=14)
    tbl.add_column(justify="right", style="bold white", width=5)
    tbl.add_column(width=22)

    bar_w = 18
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        color = _STATUS_COLORS.get(status, "white")
        filled = int(bar_w * count / max(total, 1))
        bar = Text()
        bar.append("█" * filled, style=color)
        bar.append("░" * (bar_w - filled), style="dim")
        tbl.add_row(
            Text(f"  {status}", style=color),
            str(count),
            bar,
        )
    return tbl


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sincroniza o catálogo com o estado real do disco",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula o reset sem modificar nenhum arquivo")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Executa sem pedir confirmação")
    parser.add_argument(
        "--include-excluded", action="store_true", dest="include_excluded",
        help=(
            "Re-habilita projetos excluídos por IC4 sem clone real "
            "(falsos positivos: component_file_count=0 sem pinned_commit). "
            "snapshot.py re-avaliará IC4 clonando de verdade."
        ),
    )
    args = parser.parse_args()

    console.print()
    console.print("[bold cyan]♿ a11y-autofix — Reset Consistente[/bold cyan]")
    console.print("[dim]" + "─" * 60 + "[/dim]")

    if not args.catalog.exists():
        console.print(f"[red]❌ Catálogo não encontrado: {args.catalog}[/red]")
        sys.exit(1)

    if args.dry_run:
        console.print("[yellow bold]  MODO DRY-RUN — nenhum arquivo será modificado[/yellow bold]")
    console.print()

    # ── Carregar catálogo ──────────────────────────────────────────────────
    data     = load_catalog(args.catalog)
    projects = data.get("projects", [])
    total    = len(projects)

    by_status_before = Counter(p.get("status", "?") for p in projects)
    n_snaps_disk     = sum(1 for p in projects if snapshot_exists(p["id"]))
    n_result_files   = sum(1 for _ in RESULTS_DIR.rglob("*") if _.is_file()) \
                       if RESULTS_DIR.exists() else 0

    # ── Estado antes do reset ──────────────────────────────────────────────
    console.print(Panel(
        _make_status_bar(by_status_before, total),
        title=f"Estado atual  ({total} projetos)",
        border_style="dim cyan",
        subtitle=(
            f"[dim]{n_snaps_disk} snapshot(s) no disco  |  "
            f"{n_result_files} arquivo(s) em results/[/dim]"
        ),
    ))

    # ── Calcular o que será alterado ──────────────────────────────────────
    changes: list[tuple[dict, str]] = []   # (project_dict, new_status)
    for p in projects:
        new = compute_new_status(p, include_excluded=args.include_excluded)
        if new is not None and new != p.get("status"):
            changes.append((p, new))

    to_snapshotted   = sum(1 for _, ns in changes if ns == "snapshotted")
    to_pending       = sum(1 for _, ns in changes if ns == "pending")
    excl_reactivated = sum(1 for p, ns in changes if p.get("status") == "excluded")
    untouched        = total - len(changes)

    console.print(f"\n  [bold]O que será alterado:[/bold]")
    console.print(f"  [yellow]→ snapshotted[/yellow]  {to_snapshotted:>4}  projetos "
                  f"[dim](snapshot existe no disco)[/dim]")
    console.print(f"  [cyan]→ pending[/cyan]      {to_pending:>4}  projetos "
                  f"[dim](sem snapshot — precisam ser re-clonados)[/dim]")
    if excl_reactivated:
        console.print(f"  [magenta]  (incl. {excl_reactivated} excluídos re-habilitados — IC4 falso positivo)[/magenta]")
    console.print(f"  [dim]→ sem alteração[/dim]  {untouched:>4}  projetos "
                  f"[dim](excluded / candidate / pending já ok)[/dim]")
    console.print(f"  [red]✗ results/[/red]       {n_result_files:>4}  arquivo(s) serão removidos")
    if args.include_excluded:
        console.print(f"\n  [magenta bold]  --include-excluded ativo:[/magenta bold] "
                      f"{excl_reactivated} excluídos por IC4 sem clone real serão re-habilitados")
        console.print(f"  [dim]  snapshot.py re-avaliará IC4 clonando os repos de verdade[/dim]")
    console.print()

    if not changes and n_result_files == 0:
        console.print("[green]  ✓ Catálogo já está consistente com o disco.[/green]\n")
        return

    if args.dry_run:
        console.print("[yellow]  Dry-run concluído — nenhuma alteração aplicada.[/yellow]\n")
        return

    if not args.yes:
        try:
            resp = input("  Confirmar reset? [s/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = ""
        if resp not in ("s", "y", "sim", "yes"):
            console.print("\n  Cancelado.")
            return

    console.print()

    # ── 1. Backup do catálogo ──────────────────────────────────────────────
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = args.catalog.parent / f"projects_backup_{ts}.yaml"
    shutil.copy2(args.catalog, backup)
    console.print(f"  [green]✓[/green] Backup criado: [dim]{backup.name}[/dim]")

    # ── 2. Aplicar reset no catálogo ───────────────────────────────────────
    reset_count = 0
    for p, new_status in changes:
        apply_reset(p, new_status)
        reset_count += 1

    data["projects"] = projects
    save_catalog(data, args.catalog)
    console.print(f"  [green]✓[/green] Catálogo atualizado: {reset_count} projeto(s) resetados")

    # ── 3. Limpar results/ ─────────────────────────────────────────────────
    n_removed = clear_results_dir()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"  [green]✓[/green] results/ limpo: {n_removed} arquivo(s) removidos")

    # ── Estado final ──────────────────────────────────────────────────────
    final_data     = load_catalog(args.catalog)
    final_projects = final_data.get("projects", [])
    by_status_after = Counter(p.get("status", "?") for p in final_projects)

    console.print()
    console.print(Panel(
        _make_status_bar(by_status_after, total),
        title="Estado após reset",
        border_style="green",
    ))

    n_ready     = by_status_after.get("snapshotted", 0)
    n_need_snap = by_status_after.get("pending", 0)

    console.print(f"\n  [bold]Próximos passos:[/bold]")
    step = 1
    if n_need_snap:
        console.print(f"  [cyan]{step}. Re-clonar {n_need_snap} projeto(s) (snapshot + IC4 real):[/cyan]")
        console.print(f"     python dataset/scripts/snapshot.py")
        step += 1
    if n_ready:
        console.print(f"  [green]{step}. Escanear {n_ready} projeto(s) com snapshot pronto:[/green]")
        console.print(f"     bash collect.sh --from scan --workers 2")
        step += 1
    console.print(f"  [dim]  (monitorar em outra aba: python dataset/scripts/live_progress.py)[/dim]")
    if not args.include_excluded and by_status_after.get("excluded", 0):
        n_excl = by_status_after.get("excluded", 0)
        console.print(f"\n  [dim]  Dica: {n_excl} projetos excluídos por IC4 sem clone podem ser "
                      f"falsos positivos.[/dim]")
        console.print(f"  [dim]  Re-execute com --include-excluded para re-habilitá-los.[/dim]")
    console.print()


if __name__ == "__main__":
    main()
