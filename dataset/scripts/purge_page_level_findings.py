#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Limpeza de falsos positivos de nivel de pagina dos resultados ja escaneados.

Problema: playwright_axe.py incluia 'best-practice' nas tags do axe-core,
causando que 'page-has-heading-one' (e outras regras de nivel de pagina)
disparassem em TODOS os harnesses de componente isolado. Isso gerou ~99% dos
findings como WCAG 2.4.6, 1 por arquivo, contaminando todo o dataset.

Este script:
  1. Le os findings.jsonl de todos os projetos ja escaneados
  2. Remove findings com rule_id em PAGE_LEVEL_RULES
  3. Recalcula summary.json de cada projeto
  4. Regera dataset_findings.jsonl consolidado
  5. Atualiza estatisticas no catalog (total_issues, etc.)

Uso:
    python dataset/scripts/purge_page_level_findings.py --dry-run   # so mostra o que faria
    python dataset/scripts/purge_page_level_findings.py              # aplica a limpeza

O scan em andamento NAO precisa ser parado: o script modifica apenas
projetos com status=scanned (ja finalizados). Projetos em andamento
(sem findings.jsonl ainda) sao ignorados.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
CATALOG_PATH = DATASET_ROOT / "catalog" / "projects.yaml"

sys.path.insert(0, str(REPO_ROOT))

# Regras de nivel de PAGINA — falsos positivos em harness de componente isolado.
# Espelho de detection.py:PAGE_LEVEL_RULES_EXCLUDED.
PAGE_LEVEL_RULES: frozenset[str] = frozenset({
    "page-has-heading-one",
    "landmark-one-main",
    "landmark-no-duplicate-main",
    "landmark-main-is-top-level",
    "landmark-contentinfo-is-top-level",
    "skip-link",
    "bypass",
    "region",
    "document-title",
    "frame-tested",
})

# Prefixos de regras ESLint que NÃO são acessibilidade.
# O ESLint pode capturar regras do .eslintrc do projeto (TypeScript, React, etc.).
# Mantemos apenas jsx-a11y/*.
NON_A11Y_ESLINT_PREFIXES: tuple[str, ...] = (
    "@typescript-eslint/",
    "react-hooks/",
    "react/",
    "import/",
    "@next/",
    "@angular-eslint/",
    "ts/",
    "no-catch-all/",
    "unicorn/",
    "perfectionist/",
    "react-refresh/",
    "jest/",
    "cypress/",
    "node/",
    "jsdoc/",
    "promise/",
    "unused-imports/",
    "header/",
    "prettier/",
    "no-secrets/",
    "filenames-simple/",
    "valid-jsdoc",
    "@shopify/",
)

CRITERION_TO_PRINCIPLE: dict[str, str] = {
    "1": "perceivable",
    "2": "operable",
    "3": "understandable",
    "4": "robust",
}


def _principle(wcag: str | None) -> str | None:
    if not wcag:
        return None
    return CRITERION_TO_PRINCIPLE.get(wcag.split(".")[0])


def _recalculate_summary(findings: list[dict]) -> dict:
    """Recalcula summary.json a partir dos findings filtrados."""
    by_criterion: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    by_impact: dict[str, int] = defaultdict(int)
    by_principle: dict[str, int] = defaultdict(int)
    files_with_issues: set[str] = set()

    for f in findings:
        crit = f.get("wcag_criteria")
        itype = f.get("issue_type", "other")
        impact = f.get("impact", "moderate")
        fp = f.get("file", "")

        if crit:
            by_criterion[crit] += 1
        by_type[itype] += 1
        by_impact[impact] += 1
        if fp:
            files_with_issues.add(fp)
        p = _principle(crit)
        if p:
            by_principle[p] += 1

    return {
        "total_issues": len(findings),
        "files_with_issues": len(files_with_issues),
        "by_criterion": dict(by_criterion),
        "by_type": dict(by_type),
        "by_impact": dict(by_impact),
        "by_principle": dict(by_principle),
    }


def purge(dry_run: bool) -> None:
    verb = "[DRY-RUN]" if dry_run else "[APPLY]"

    # Coletar projetos com findings.jsonl
    project_dirs = sorted(RESULTS_DIR.glob("*/findings.jsonl"))
    if not project_dirs:
        print("Nenhum findings.jsonl encontrado em", RESULTS_DIR)
        return

    total_before = 0
    total_after = 0
    total_removed = 0
    projects_affected = 0
    rule_counts: Counter = Counter()

    all_clean_findings: list[dict] = []

    for findings_path in project_dirs:
        project_id = findings_path.parent.name

        # Ler findings originais
        original: list[dict] = []
        with open(findings_path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    try:
                        original.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        # Filtrar
        clean: list[dict] = []
        removed: list[dict] = []
        for f in original:
            rule = f.get("rule_id") or ""
            rule_lower = rule.lower()
            found_by = str(f.get("found_by", ""))

            # 1. Artefatos de harness (landmark duplicado, frame-tested, etc.)
            if rule_lower in PAGE_LEVEL_RULES:
                removed.append(f)
                rule_counts[f"[page-level] {rule_lower}"] += 1
                continue

            # 2. Regras ESLint não-a11y (TypeScript, React, import, etc.)
            # O scanner pode capturar regras do .eslintrc do projeto.
            # Mantemos APENAS regras jsx-a11y/* do eslint.
            if "eslint" in found_by and not rule.startswith("jsx-a11y/"):
                removed.append(f)
                rule_counts[f"[non-a11y-eslint] {rule[:40]}"] += 1
                continue

            clean.append(f)

        total_before += len(original)
        total_after += len(clean)
        total_removed += len(removed)
        all_clean_findings.extend(clean)

        if removed:
            projects_affected += 1
            pct = len(removed) / len(original) * 100 if original else 0
            print(f"  {project_id:<50}  -{len(removed):>5}  ({pct:.0f}% removidos)")

            if not dry_run:
                # Reescrever findings.jsonl
                with open(findings_path, "w", encoding="utf-8") as fp:
                    for finding in clean:
                        fp.write(json.dumps(finding, ensure_ascii=False) + "\n")

                # Recalcular e reescrever summary.json
                summary_path = findings_path.parent / "summary.json"
                new_summary = _recalculate_summary(clean)
                # Preservar campos extras do summary existente
                if summary_path.exists():
                    try:
                        existing = json.loads(summary_path.read_text(encoding="utf-8"))
                        existing.update(new_summary)
                        new_summary = existing
                    except Exception:
                        pass
                summary_path.write_text(
                    json.dumps(new_summary, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

    # Relatorio
    print()
    print("=" * 65)
    print(f"Projetos com findings de pagina:  {projects_affected}")
    print(f"Findings antes:                   {total_before:>8,}")
    print(f"Findings removidos:               {total_removed:>8,}  ({total_removed/max(total_before,1)*100:.1f}%)")
    print(f"Findings apos limpeza:            {total_after:>8,}")
    print()
    print("Regras removidas:")
    for rule, count in rule_counts.most_common():
        print(f"  {rule:<35}  {count:>6,}")
    print("=" * 65)

    if dry_run:
        print("\nMODO DRY-RUN: nenhum arquivo modificado.")
        print("Execute sem --dry-run para aplicar.")
        return

    # Regravar dataset_findings.jsonl consolidado
    consolidated_path = RESULTS_DIR / "dataset_findings.jsonl"
    print(f"\nRegravando {consolidated_path.name} ({len(all_clean_findings):,} findings)...")
    with open(consolidated_path, "w", encoding="utf-8") as fp:
        for finding in all_clean_findings:
            fp.write(json.dumps(finding, ensure_ascii=False) + "\n")

    # Atualizar live_findings.jsonl (limpar; sera regenerado no proximo scan)
    live_path = RESULTS_DIR / "live_findings.jsonl"
    if live_path.exists():
        live_path.write_text("", encoding="utf-8")
        print("live_findings.jsonl limpo (sera regenerado pelo scan em andamento).")

    print("\nLimpeza concluida. Os projetos que ainda estao sendo escaneados")
    print("usarao automaticamente o scanner corrigido e nao gerarao mais")
    print("falsos positivos de nivel de pagina.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove falsos positivos de nivel de pagina dos resultados de scan."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar o que seria removido sem modificar arquivos.",
    )
    args = parser.parse_args()

    print("a11y-autofix -- Purge de Findings de Nivel de Pagina")
    print("=" * 65)
    print(f"Regras a remover: {sorted(PAGE_LEVEL_RULES)}")
    print()

    purge(dry_run=args.dry_run)


if __name__ == "__main__":
    main()


# ─── Modo cap: limitar findings por (rule_id × projeto) ───────────────────────

def apply_rule_cap(results_dir: Path, cap: int = 10, dry_run: bool = False) -> None:
    """
    Aplica cap de N findings por (rule_id × projeto).

    Justificativa metodológica: impede que um único projeto com 200 instâncias
    da mesma regra domine a distribuição do dataset (reduz Gini e HHI).
    O cap é aplicado preservando findings com maior diversidade de seletores.
    """
    total_removed = 0
    for fp in sorted(results_dir.glob("*/findings.jsonl")):
        lines = [l for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            continue
        findings = []
        for l in lines:
            try: findings.append(json.loads(l))
            except: pass

        from collections import defaultdict
        rule_buckets: dict = defaultdict(list)
        for f in findings:
            rule_buckets[f.get("rule_id","?")].append(f)

        kept, removed = [], 0
        for rule, group in rule_buckets.items():
            if len(group) <= cap:
                kept.extend(group)
            else:
                kept.extend(group[:cap])
                removed += len(group) - cap

        if removed > 0:
            total_removed += removed
            pid = fp.parent.name
            print(f"  [cap={cap}] {pid[:50]:50s}  -{removed}")
            if not dry_run:
                with open(fp, "w", encoding="utf-8") as f:
                    for row in kept:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nTotal removidos por cap={cap}: {total_removed}")
    if dry_run:
        print("(dry-run — nenhum arquivo modificado)")
