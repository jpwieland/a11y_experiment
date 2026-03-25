#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_multi_tool_consensus.py — Corrige a métrica de consenso multi-ferramenta.

PROBLEMA DIAGNOSTICADO:
  O DetectionProtocol usa a chave de deduplicação `selector|wcag_criteria`.
  Os três scanners geram selectors estruturalmente incompatíveis:
    - eslint-jsx-a11y:  "AccountSettingBody.tsx:18:28"  (posição no fonte JSX)
    - playwright+axe:   "h3", "a[href='/']"             (CSS seletor simples)
    - pa11y:            "#root > div > div > h3"        (CSS path completo)

  Resultado: tool_consensus=1 em 100% dos findings, pois os selectors NUNCA
  batem entre ferramentas. O 0% multi-tool é real porém métrica errada.

CORREÇÃO APLICADA (sem re-scan):
  1. Agrupa findings por (arquivo, wcag_criteria) dentro de cada projeto
  2. Se ≥2 ferramentas diferentes detectaram o mesmo WCAG num arquivo,
     marca todos os findings do grupo com:
       "cross_tool_files": N   ← quantas ferramentas confirmaram no arquivo
       "cross_tool_confirmed": true
  3. Regrava findings.jsonl e dataset_findings.jsonl com os novos campos
  4. Exibe estatísticas antes/depois

Uso:
    python dataset/scripts/fix_multi_tool_consensus.py
    python dataset/scripts/fix_multi_tool_consensus.py --dry-run  # só conta, não grava
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = REPO_ROOT / "dataset" / "results"

# ── ANSI ──────────────────────────────────────────────────────────────────────
R = "\033[0m"; BOLD = "\033[1m"; GREEN = "\033[92m"
YELLOW = "\033[93m"; CYAN = "\033[96m"; DIM = "\033[2m"


def process_project(proj_dir: Path, dry_run: bool) -> dict:
    """
    Processa um projeto: identifica confirmações cross-tool no nível de arquivo
    e reescreve findings.jsonl com os novos campos.

    Retorna métricas do projeto.
    """
    fp = proj_dir / "findings.jsonl"
    if not fp.exists():
        return {}

    lines_raw = fp.read_text(encoding="utf-8").splitlines()
    findings = []
    for line in lines_raw:
        line = line.strip()
        if not line:
            continue
        try:
            findings.append(json.loads(line))
        except Exception:
            pass

    if not findings:
        return {}

    # Agrupar por (filename, wcag_criteria) → set de ferramentas
    # Usar apenas o nome do arquivo (não o path completo) para portabilidade
    file_wcag_tools: dict[tuple, set] = defaultdict(set)
    for f in findings:
        fname = Path(f.get("file", "") or "").name
        wcag = f.get("wcag_criteria", "") or ""
        tool = (f.get("found_by") or ["?"])[0]
        if wcag and fname:
            file_wcag_tools[(fname, wcag)].add(tool)

    # Identificar grupos confirmados por ≥2 ferramentas
    confirmed_keys: dict[tuple, int] = {
        k: len(v) for k, v in file_wcag_tools.items() if len(v) >= 2
    }

    # Anotar cada finding
    newly_confirmed = 0
    for f in findings:
        fname = Path(f.get("file", "") or "").name
        wcag = f.get("wcag_criteria", "") or ""
        key = (fname, wcag)
        n_tools = confirmed_keys.get(key, 1)
        was_confirmed = f.get("cross_tool_confirmed", False)

        f["cross_tool_files"] = n_tools       # quantas ferramentas neste arquivo+wcag
        f["cross_tool_confirmed"] = n_tools >= 2

        if not was_confirmed and n_tools >= 2:
            newly_confirmed += 1

    if not dry_run and newly_confirmed > 0:
        with open(fp, "w", encoding="utf-8") as out:
            for f in findings:
                out.write(json.dumps(f, ensure_ascii=False) + "\n")

    return {
        "total": len(findings),
        "confirmed": sum(1 for f in findings if f.get("cross_tool_confirmed")),
        "newly_confirmed": newly_confirmed,
        "confirmed_groups": len(confirmed_keys),
    }


def rebuild_consolidated(dry_run: bool) -> tuple[int, int]:
    """Regera dataset_findings.jsonl a partir de todos os findings.jsonl."""
    out_path = RESULTS_DIR / "dataset_findings.jsonl"
    total = 0
    confirmed = 0

    if dry_run:
        # Só conta
        for proj_dir in sorted(RESULTS_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            fp = proj_dir / "findings.jsonl"
            if fp.exists():
                for line in fp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            total += 1
                            if d.get("cross_tool_confirmed"):
                                confirmed += 1
                        except Exception:
                            pass
        return total, confirmed

    with open(out_path, "w", encoding="utf-8") as out:
        for proj_dir in sorted(RESULTS_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            fp = proj_dir / "findings.jsonl"
            if fp.exists():
                for line in fp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            total += 1
                            if d.get("cross_tool_confirmed"):
                                confirmed += 1
                            out.write(line + "\n")
                        except Exception:
                            pass
    return total, confirmed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Corrige métrica de consenso multi-ferramenta no dataset."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Só calcula, não modifica arquivos.")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═' * 60}{R}")
    print(f"{BOLD}  fix_multi_tool_consensus.py{R}")
    print(f"{BOLD}{'═' * 60}{R}\n")

    if args.dry_run:
        print(f"  {YELLOW}[DRY-RUN] Nenhum arquivo será modificado.{R}\n")

    proj_dirs = [d for d in sorted(RESULTS_DIR.iterdir()) if d.is_dir()]
    print(f"  Projetos a processar: {len(proj_dirs)}\n")

    total_findings = 0
    total_confirmed = 0
    total_newly = 0
    projects_with_cross = 0

    for proj_dir in proj_dirs:
        metrics = process_project(proj_dir, dry_run=args.dry_run)
        if not metrics:
            continue
        total_findings += metrics["total"]
        total_confirmed += metrics["confirmed"]
        total_newly += metrics["newly_confirmed"]
        if metrics["confirmed"] > 0:
            projects_with_cross += 1
            print(f"  {GREEN}✔{R} {proj_dir.name:<50} "
                  f"+{metrics['newly_confirmed']:>3} cross-tool  "
                  f"({metrics['confirmed_groups']} grupos)")

    print(f"\n  {'─' * 60}")
    print(f"  Total findings processados  : {total_findings:,}")
    print(f"  Com cross-tool confirmation : {total_confirmed:,} "
          f"({total_confirmed/total_findings*100:.1f}%)")
    print(f"  Projetos com cross-tool     : {projects_with_cross}/{len(proj_dirs)}")

    if not args.dry_run and total_newly > 0:
        print(f"\n  Reconstruindo dataset_findings.jsonl...")
        total, confirmed = rebuild_consolidated(dry_run=False)
        print(f"  {GREEN}✔{R} Rebuild concluído: {total:,} findings, "
              f"{confirmed:,} cross-tool ({confirmed/total*100:.1f}%)")
    elif args.dry_run:
        print(f"\n  [DRY-RUN] Rodando --rebuild-only para contar...")
        total, confirmed = rebuild_consolidated(dry_run=True)
        print(f"  Estado atual:  {total:,} findings, "
              f"{confirmed:,} cross-tool ({confirmed/total*100:.1f}%)")

    print(f"\n{BOLD}{'═' * 60}{R}")
    print(f"  {GREEN}Concluído!{R}  Execute balance_report.py para ver as novas métricas.")
    print(f"{BOLD}{'═' * 60}{R}\n")


if __name__ == "__main__":
    main()
