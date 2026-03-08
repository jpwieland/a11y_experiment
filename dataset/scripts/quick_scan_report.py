#!/usr/bin/env python3
"""
Relatório rápido de findings por projeto — sem depender do pipeline completo.

Roda ESLint jsx-a11y diretamente nos projetos snapshotted e mostra:
  - Quantos projetos têm pelo menos 1 finding
  - Distribuição de tipos de erro por projeto
  - Top regras mais violadas
  - Estimativa de cobertura dos QMs (QM4, QM7, QM8)

NÃO modifica o catálogo. Apenas lê e reporta.

Uso:
    python dataset/scripts/quick_scan_report.py
    python dataset/scripts/quick_scan_report.py --max-projects 20
    python dataset/scripts/quick_scan_report.py --project saleor__saleor-dashboard
    python dataset/scripts/quick_scan_report.py --output reports/quick_scan.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import ProjectEntry, ProjectStatus
from a11y_autofix.utils.files import find_react_files

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

# ── Regras jsx-a11y → mapeamento WCAG + tipo de issue ─────────────────────────
RULE_META = {
    "jsx-a11y/alt-text":                     {"wcag": "1.1.1", "type": "alt-text",  "principle": "Perceivable"},
    "jsx-a11y/aria-props":                   {"wcag": "4.1.2", "type": "aria",      "principle": "Robust"},
    "jsx-a11y/aria-role":                    {"wcag": "4.1.2", "type": "aria",      "principle": "Robust"},
    "jsx-a11y/aria-hidden-body":             {"wcag": "4.1.2", "type": "aria",      "principle": "Robust"},
    "jsx-a11y/click-events-have-key-events": {"wcag": "2.1.1", "type": "keyboard",  "principle": "Operable"},
    "jsx-a11y/interactive-supports-focus":   {"wcag": "2.1.1", "type": "focus",     "principle": "Operable"},
    "jsx-a11y/label-has-associated-control": {"wcag": "1.3.1", "type": "label",     "principle": "Perceivable"},
    "jsx-a11y/no-autofocus":                 {"wcag": "2.4.3", "type": "focus",     "principle": "Operable"},
    "jsx-a11y/no-distracting-elements":      {"wcag": "2.2.2", "type": "semantic",  "principle": "Operable"},
    "jsx-a11y/tabindex-no-positive":         {"wcag": "2.4.3", "type": "focus",     "principle": "Operable"},
    "jsx-a11y/anchor-is-valid":              {"wcag": "4.1.2", "type": "semantic",  "principle": "Robust"},
    "jsx-a11y/button-has-type":              {"wcag": "4.1.2", "type": "semantic",  "principle": "Robust"},
    "jsx-a11y/heading-has-content":          {"wcag": "1.3.1", "type": "semantic",  "principle": "Perceivable"},
    "jsx-a11y/html-has-lang":                {"wcag": "3.1.1", "type": "semantic",  "principle": "Understandable"},
    "jsx-a11y/img-redundant-alt":            {"wcag": "1.1.1", "type": "alt-text",  "principle": "Perceivable"},
    "jsx-a11y/no-access-key":               {"wcag": "2.1.1", "type": "keyboard",  "principle": "Operable"},
    "jsx-a11y/mouse-events-have-key-events": {"wcag": "2.1.1", "type": "keyboard",  "principle": "Operable"},
    "jsx-a11y/role-has-required-aria-props": {"wcag": "4.1.2", "type": "aria",      "principle": "Robust"},
    "jsx-a11y/role-supports-aria-props":     {"wcag": "4.1.2", "type": "aria",      "principle": "Robust"},
    "jsx-a11y/scope":                        {"wcag": "1.3.1", "type": "semantic",  "principle": "Perceivable"},
}

ESLINT_CONFIG = {
    "root": True,
    "parser": "@typescript-eslint/parser",
    "parserOptions": {"ecmaVersion": 2022, "ecmaFeatures": {"jsx": True}, "sourceType": "module"},
    "plugins": ["jsx-a11y"],
    "rules": {rule: "error" for rule in RULE_META},
}


async def run_eslint_on_project(
    project_dir: Path,
    scan_paths: list[str],
    max_files: int = 50,
) -> list[dict]:
    """Roda ESLint jsx-a11y num projeto e retorna os findings brutos."""
    files: list[Path] = []
    for rel in scan_paths:
        scan_dir = project_dir / rel.rstrip("/")
        if scan_dir.exists():
            found = find_react_files(scan_dir, recursive=True)
            files.extend(found)

    # Deduplica + limita para agilizar
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    unique = unique[:max_files]

    if not unique:
        return []

    # Config temporária na raiz do projeto
    cfg_path = project_dir / ".tmp_a11y_eslintrc.json"
    try:
        cfg_path.write_text(json.dumps(ESLINT_CONFIG))

        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "eslint",
            "--format", "json",
            "--no-eslintrc",
            "--config", str(cfg_path),
            "--ext", ".tsx,.jsx,.ts,.js",
            *[str(f) for f in unique],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "FORCE_COLOR": "0"},
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return []

        output = stdout.decode(errors="replace").strip()
        if not output:
            return []

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return []
    finally:
        cfg_path.unlink(missing_ok=True)


def count_findings(eslint_output: list[dict]) -> tuple[int, Counter, Counter, set]:
    """Conta findings, regras e princípios WCAG de um projeto."""
    total = 0
    rules: Counter = Counter()
    issue_types: Counter = Counter()
    principles: set = set()

    for file_result in eslint_output:
        for msg in file_result.get("messages", []):
            rule = msg.get("ruleId") or "unknown"
            total += 1
            rules[rule] += 1
            meta = RULE_META.get(rule, {})
            if meta.get("type"):
                issue_types[meta["type"]] += 1
            if meta.get("principle"):
                principles.add(meta["principle"])

    return total, rules, issue_types, principles


async def scan_projects(
    entries: list[ProjectEntry],
    max_files_per_project: int = 50,
    semaphore_limit: int = 4,
) -> dict:
    """Escaneia todos os projetos e agrega resultados."""
    sem = asyncio.Semaphore(semaphore_limit)
    results: dict[str, dict] = {}

    async def scan_one(entry: ProjectEntry) -> None:
        async with sem:
            project_dir = SNAPSHOTS_DIR / entry.id
            if not project_dir.exists():
                print(f"  [{entry.id}] snapshot não encontrado", file=sys.stderr)
                results[entry.id] = {"error": "snapshot_missing", "total": 0}
                return

            print(f"  → {entry.id} ({len(entry.scan_paths)} paths)", end="", flush=True)
            raw = await run_eslint_on_project(
                project_dir,
                entry.scan_paths,
                max_files=max_files_per_project,
            )

            total, rules, issue_types, principles = count_findings(raw)
            print(f" → {total} findings")

            results[entry.id] = {
                "domain": entry.domain.value if hasattr(entry.domain, "value") else str(entry.domain),
                "total": total,
                "rules": dict(rules.most_common(10)),
                "issue_types": dict(issue_types),
                "wcag_principles": sorted(principles),
                "files_scanned": sum(len(fr.get("messages", [])) > 0 for fr in raw),
            }

    await asyncio.gather(*[scan_one(e) for e in entries])
    return results


def print_report(results: dict, entries: list[ProjectEntry]) -> None:
    """Imprime o relatório no terminal."""
    total_projects = len(results)
    projects_with_findings = sum(1 for r in results.values() if r.get("total", 0) > 0)
    all_rules: Counter = Counter()
    all_types: Counter = Counter()
    all_principles: set = set()
    total_findings = 0

    for r in results.values():
        total_findings += r.get("total", 0)
        all_rules.update(r.get("rules", {}))
        all_types.update(r.get("issue_types", {}))
        all_principles.update(r.get("wcag_principles", []))

    print("\n" + "═" * 62)
    print("  Quick Scan Report — ESLint jsx-a11y")
    print("═" * 62)

    print(f"\n  Projetos escaneados : {total_projects}")
    print(f"  Com findings        : {projects_with_findings} ({projects_with_findings/max(total_projects,1)*100:.0f}%)")
    print(f"  Total findings      : {total_findings}")
    print(f"  Média por projeto   : {total_findings/max(projects_with_findings,1):.1f}")

    # QM check preview
    print(f"\n  ── Estimativa de QMs ──────────────────────────────")
    scan_rate = projects_with_findings / max(total_projects, 1)
    print(f"  QM7 scan rate       : {scan_rate:.0%}  {'✓' if scan_rate >= 0.7 else '✗'} (thresh ≥70%)")
    print(f"  QM4 issue types     : {len(all_types)}/7  {'✓' if len(all_types) >= 7 else '✗'} tipos: {', '.join(sorted(all_types))}")
    print(f"  QM8 WCAG principles : {len(all_principles)}/4  {'✓' if len(all_principles) >= 4 else '✗'} {sorted(all_principles)}")

    print(f"\n  ── Top 15 regras mais violadas ────────────────────")
    for rule, count in all_rules.most_common(15):
        meta = RULE_META.get(rule, {})
        wcag = meta.get("wcag", "?")
        bar = "█" * min(count // max(total_findings // 40, 1), 20)
        print(f"  {count:>5}  [{wcag}] {rule.replace('jsx-a11y/', ''):<40} {bar}")

    print(f"\n  ── Tipos de issue ─────────────────────────────────")
    for itype, count in all_types.most_common():
        pct = count / max(total_findings, 1) * 100
        print(f"  {count:>5} ({pct:4.1f}%)  {itype}")

    print(f"\n  ── Distribuição por domínio ────────────────────────")
    domain_totals: dict[str, int] = defaultdict(int)
    domain_projects: dict[str, int] = defaultdict(int)
    for r in results.values():
        d = r.get("domain", "unknown")
        domain_projects[d] += 1
        domain_totals[d] += r.get("total", 0)
    for domain in sorted(domain_totals, key=lambda d: -domain_totals[d]):
        n = domain_projects[domain]
        t = domain_totals[domain]
        avg = t / max(n, 1)
        print(f"  {domain:<20} {n:>3} projetos  {t:>5} findings  avg {avg:5.1f}")

    print(f"\n  ── Top 10 projetos com mais findings ───────────────")
    top = sorted(results.items(), key=lambda x: x[1].get("total", 0), reverse=True)[:10]
    for pid, r in top:
        print(f"  {r.get('total',0):>5}  {pid}")

    print("═" * 62 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick scan report usando ESLint jsx-a11y")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--max-projects", type=int, default=None,
                        help="Limitar número de projetos (para testes rápidos)")
    parser.add_argument("--max-files", type=int, default=50,
                        help="Máximo de arquivos por projeto (default: 50)")
    parser.add_argument("--project", default=None, help="Escanear só um projeto específico")
    parser.add_argument("--output", type=Path, default=None, help="Salvar JSON em arquivo")
    parser.add_argument("--workers", type=int, default=4, help="Projetos em paralelo")
    args = parser.parse_args()

    print("\n♿ a11y-autofix Quick Scan Report")
    print("═" * 50)

    data = yaml.safe_load(args.catalog.read_text())
    all_entries = [
        ProjectEntry.model_validate(p)
        for p in (data.get("projects") or [])
    ]

    # Filtrar só projetos snapshotted
    entries = [
        e for e in all_entries
        if e.status in (ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)
    ]

    if args.project:
        entries = [e for e in entries if e.id == args.project or args.project in e.id]
        if not entries:
            print(f"Projeto '{args.project}' não encontrado ou não snapshotted", file=sys.stderr)
            sys.exit(1)

    if args.max_projects:
        entries = entries[:args.max_projects]

    print(f"  Projetos snapshotted: {len(entries)}")
    print(f"  Arquivos por projeto: máx {args.max_files}")
    print(f"  Workers paralelos   : {args.workers}")
    print()

    # Verificar se ESLint está disponível
    try:
        import subprocess
        r = subprocess.run(
            ["npx", "eslint", "--version"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            print("ERRO: ESLint não disponível. Instale com: npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser", file=sys.stderr)
            sys.exit(1)
        print(f"  ESLint: {r.stdout.strip()}")
    except Exception as e:
        print(f"ERRO ao verificar ESLint: {e}", file=sys.stderr)
        sys.exit(1)

    results = asyncio.run(scan_projects(entries, args.max_files, args.workers))
    print_report(results, entries)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2))
        print(f"  JSON salvo em: {args.output}")


if __name__ == "__main__":
    main()
