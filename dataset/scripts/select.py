#!/usr/bin/env python3
"""Seleção estratificada e seedada de projetos para o experimento.

Fecha a última lacuna de reprodutibilidade do pipeline de dataset: a
passagem de "projetos escaneados" para "projetos do experimento" era
manual (lista escrita à mão no YAML). Este script:

  1. Lê o catálogo + summaries de scan (dataset/results/*/summary.json)
  2. Filtra elegíveis: status=scanned, ≥ min_issues issues, ≥1 arquivo com issue
  3. Estratifica por domínio e aloca por tamanho (S/M/L) dentro do domínio
  4. SORTEIA com seed fixa dentro de cada estrato (elimina o viés top-stars
     da seleção manual)
  5. Gera o experimento final a partir de um template, substituindo o
     bloco `files:` pela seleção, com comentários de auditoria

Uso:
    python dataset/scripts/select.py                          # seleção padrão
    python dataset/scripts/select.py --per-domain 5 --seed 42
    python dataset/scripts/select.py --template experiments/chosen_experiment.yaml \\
        --output experiments/auto_experiment.yaml
    python dataset/scripts/select.py --dry-run                # só mostra a seleção

Protocol ref: dataset/PROTOCOL.md §8 (Selection) — automação 06/2026.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"
DEFAULT_RESULTS = DATASET_ROOT / "results"
DEFAULT_TEMPLATE = REPO_ROOT / "experiments" / "chosen_experiment.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "experiments" / "auto_experiment.yaml"
SELECTION_RECORD = DATASET_ROOT / "catalog" / "selection.json"

SIZE_ORDER = ["small", "medium", "large"]


def load_eligible(catalog_path: Path, results_dir: Path, min_issues: int) -> list[dict]:
    """Projetos scanned com summary válido e issues reais suficientes."""
    cat = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    projects = cat.get("projects", cat) if isinstance(cat, dict) else cat

    eligible: list[dict] = []
    for p in projects:
        if p.get("status") != "scanned":
            continue
        pid = p["id"]
        summary_path = results_dir / pid / "summary.json"
        if not summary_path.exists():
            continue
        try:
            s = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        total = int(s.get("total_issues") or 0)
        files_with = int(s.get("files_with_issues") or 0)
        if total < min_issues or files_with < 1:
            continue
        eligible.append({
            "id": pid,
            "domain": p.get("domain") or "unknown",
            "size": (p.get("size_category") or "medium"),
            "issues": total,
            "files_with_issues": files_with,
            "files_scanned": int(s.get("files_scanned") or 0),
            "pinned_commit": (p.get("snapshot") or {}).get("pinned_commit", ""),
        })
    return eligible


def stratified_select(
    eligible: list[dict],
    per_domain: int,
    seed: int,
) -> list[dict]:
    """
    Sorteio seedado dentro de cada (domínio, tamanho).

    Alocação por domínio: tenta cobrir S/M/L em rodadas (1 de cada
    tamanho disponível por rodada) até atingir per_domain — preserva a
    diversidade de tamanho sem exigir distribuição que o domínio não tem.
    """
    rng = random.Random(seed)
    by_domain: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in eligible:
        by_domain[e["domain"]][e["size"]].append(e)

    # Ordem determinística + shuffle seedado dentro de cada estrato
    selected: list[dict] = []
    for domain in sorted(by_domain):
        buckets = by_domain[domain]
        for size in buckets:
            buckets[size].sort(key=lambda e: e["id"])
            rng.shuffle(buckets[size])

        picked: list[dict] = []
        while len(picked) < per_domain:
            took_any = False
            for size in SIZE_ORDER:
                if len(picked) >= per_domain:
                    break
                if buckets.get(size):
                    picked.append(buckets[size].pop(0))
                    took_any = True
            if not took_any:
                break  # domínio esgotado
        selected.extend(picked)

    return selected


def render_files_block(selected: list[dict]) -> str:
    """Bloco YAML `files:` com comentários de auditoria por projeto."""
    lines = ["files:"]
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for e in selected:
        by_domain[e["domain"]].append(e)
    for domain in sorted(by_domain):
        entries = by_domain[domain]
        total_issues = sum(e["issues"] for e in entries)
        lines.append(f"  # ── {domain} ({len(entries)} projetos | {total_issues} issues) ──")
        for e in sorted(entries, key=lambda x: x["id"]):
            commit = e["pinned_commit"][:10] if e["pinned_commit"] else "?"
            lines.append(
                f"  - dataset/snapshots/{e['id']}"
                f"  # {e['size']:<6} | {e['files_scanned']:>4} arq | "
                f"{e['issues']:>3} issues | @{commit}"
            )
    return "\n".join(lines)


def generate_experiment(template_path: Path, output_path: Path,
                        selected: list[dict], seed: int) -> None:
    """
    Gera o YAML do experimento substituindo o bloco `files:` do template.

    Substituição textual (preserva comentários/estrutura do template fora
    do bloco files) — o bloco files antigo é localizado do `files:` até a
    próxima chave top-level.
    """
    text = template_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = next(i for i, l in enumerate(lines) if l.startswith("files:"))
    end = start + 1
    while end < len(lines):
        l = lines[end]
        # próxima chave top-level (coluna 0, não-comentário, não-item de lista)
        if l and not l.startswith((" ", "\t", "#", "-")) and ":" in l:
            break
        end += 1

    header = (
        f"# ── SELEÇÃO AUTOMÁTICA (dataset/scripts/select.py) ─────────────────\n"
        f"# gerado: {datetime.now(tz=timezone.utc).isoformat()}\n"
        f"# seed: {seed} | projetos: {len(selected)} | "
        f"issues: {sum(e['issues'] for e in selected)}\n"
        f"# NÃO editar à mão — re-execute o select.py para alterar.\n"
    )
    new_block = header + render_files_block(selected)
    out = "\n".join(lines[:start]) + "\n" + new_block + "\n\n" + "\n".join(lines[end:])
    output_path.write_text(out, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                    help="Experimento-template cujo bloco files: será substituído")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--per-domain", type=int, default=5,
                    help="Projetos por domínio (default: 5)")
    ap.add_argument("--min-issues", type=int, default=1,
                    help="Mínimo de issues reais para elegibilidade (default: 1)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true", help="Só exibe a seleção")
    args = ap.parse_args()

    eligible = load_eligible(args.catalog, args.results, args.min_issues)
    if not eligible:
        print("ERRO: nenhum projeto elegível (status=scanned com summary e issues).",
              file=sys.stderr)
        return 1

    selected = stratified_select(eligible, args.per_domain, args.seed)

    domains = sorted({e["domain"] for e in selected})
    print(f"Elegíveis: {len(eligible)} | Selecionados: {len(selected)} "
          f"({len(domains)} domínios, seed={args.seed})")
    print(render_files_block(selected))

    if args.dry_run:
        return 0

    # Registro de auditoria da seleção
    SELECTION_RECORD.parent.mkdir(parents=True, exist_ok=True)
    SELECTION_RECORD.write_text(json.dumps({
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "seed": args.seed,
        "per_domain": args.per_domain,
        "min_issues": args.min_issues,
        "eligible_count": len(eligible),
        "selected": selected,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    generate_experiment(args.template, args.output, selected, args.seed)
    print(f"\n✓ Experimento gerado: {args.output}")
    print(f"✓ Registro de auditoria: {SELECTION_RECORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
