#!/usr/bin/env python3
"""
Relatório visual de findings coletados pelo scanner a11y-autofix.

Lê os arquivos findings.jsonl (pós-scan) e/ou ground_truth.jsonl (pós-anotação)
de todos os projetos e exibe uma tabela detalhada por:
  - Critério WCAG (todos, não só top-10)
  - Tipo de issue (aria, keyboard, label, etc.)
  - Regra específica (jsx-a11y/alt-text, color-contrast, etc.)
  - Nível de impacto (critical, serious, moderate, minor)
  - Confiança (high, medium, low)
  - Ferramenta detectora (found_by)
  - Princípio WCAG (perceivable, operable, understandable, robust)
  - Domínio do projeto

Valida também se todos os critérios WCAG dos scanners têm mapeamentos corretos.

Uso:
    python dataset/scripts/findings_report.py
    python dataset/scripts/findings_report.py --source scan      # só findings.jsonl
    python dataset/scripts/findings_report.py --source annotated # só ground_truth.jsonl
    python dataset/scripts/findings_report.py --project saleor__storefront
    python dataset/scripts/findings_report.py --output report.json
    python dataset/scripts/findings_report.py --csv report.csv
    python dataset/scripts/findings_report.py --validate-mappings  # só validação
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

# ── WCAG critério → princípio ──────────────────────────────────────────────
_PRINCIPLE = {"1": "perceivable", "2": "operable", "3": "understandable", "4": "robust"}

# ── Todas as regras jsx-a11y com seus critérios WCAG (para validação) ────────
_ESLINT_RULES_WCAG: dict[str, str] = {
    "jsx-a11y/alt-text":                     "1.1.1",
    "jsx-a11y/img-redundant-alt":            "1.1.1",
    "jsx-a11y/heading-has-content":          "1.3.1",
    "jsx-a11y/label-has-associated-control": "1.3.1",
    "jsx-a11y/scope":                        "1.3.1",
    "jsx-a11y/click-events-have-key-events": "2.1.1",
    "jsx-a11y/interactive-supports-focus":   "2.1.1",
    "jsx-a11y/mouse-events-have-key-events": "2.1.1",
    "jsx-a11y/no-access-key":               "2.1.1",
    "jsx-a11y/no-distracting-elements":     "2.2.2",
    "jsx-a11y/tabindex-no-positive":        "2.4.3",
    "jsx-a11y/no-autofocus":               "2.4.3",
    "jsx-a11y/html-has-lang":              "3.1.1",
    "jsx-a11y/aria-props":                 "4.1.2",
    "jsx-a11y/aria-proptypes":             "4.1.2",
    "jsx-a11y/aria-role":                  "4.1.2",
    "jsx-a11y/aria-unsupported-elements":  "4.1.2",
    "jsx-a11y/role-has-required-aria-props": "4.1.2",
    "jsx-a11y/role-supports-aria-props":   "4.1.2",
    "jsx-a11y/anchor-is-valid":            "4.1.2",
    "jsx-a11y/anchor-has-content":         "4.1.2",
}

_WCAG_PRINCIPLE_NAMES = {
    "1": "Princípio 1 — Perceivable",
    "2": "Princípio 2 — Operable",
    "3": "Princípio 3 — Understandable",
    "4": "Princípio 4 — Robust",
}


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento de dados
# ─────────────────────────────────────────────────────────────────────────────

def load_scan_findings(
    project_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Carrega findings.jsonl de todos os projetos scaneados."""
    findings: list[dict[str, Any]] = []
    if not RESULTS_DIR.exists():
        return findings

    for project_dir in sorted(RESULTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        pid = project_dir.name
        if project_filter and project_filter not in pid:
            continue
        jsonl = project_dir / "findings.jsonl"
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    findings.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return findings


def load_ground_truth_findings(
    project_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Carrega ground_truth.jsonl de todos os projetos anotados."""
    findings: list[dict[str, Any]] = []
    if not RESULTS_DIR.exists():
        return findings

    for project_dir in sorted(RESULTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        pid = project_dir.name
        if project_filter and project_filter not in pid:
            continue
        jsonl = project_dir / "ground_truth.jsonl"
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    findings.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return findings


def load_catalog_domain_map() -> dict[str, str]:
    """Retorna mapa project_id → domain para enriquecer o relatório."""
    import yaml
    catalog_path = DATASET_ROOT / "catalog" / "projects.yaml"
    if not catalog_path.exists():
        return {}
    try:
        data = yaml.safe_load(catalog_path.read_text()) or {}
        return {
            p.get("id", ""): p.get("domain", "unknown")
            for p in data.get("projects", [])
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Agregação
# ─────────────────────────────────────────────────────────────────────────────

def aggregate(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega findings em contagens por diversas dimensões."""
    by_wcag: dict[str, int] = defaultdict(int)
    by_rule: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    by_impact: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    by_tool: dict[str, int] = defaultdict(int)
    by_principle: dict[str, int] = defaultdict(int)
    by_domain: dict[str, int] = defaultdict(int)
    by_project: dict[str, int] = defaultdict(int)
    # Para "by_wcag_rule" — cruzamento WCAG × rule (mostra quais regras detectam cada critério)
    wcag_rule_pairs: list[tuple[str, str]] = []

    total = len(findings)
    confirmed = 0
    fp = 0

    domain_map = load_catalog_domain_map()

    for f in findings:
        # Campos de anotação (ground_truth) vs raw (findings.jsonl)
        label = f.get("ground_truth_label", "")
        if label == "FALSE_POSITIVE":
            fp += 1
            continue  # excluir FP da contagem principal
        if label in ("CONFIRMED", ""):
            confirmed += 1 if label == "CONFIRMED" else 0

        wcag = f.get("wcag_criteria") or ""
        rule = f.get("rule_id", "unknown")
        itype = f.get("issue_type", "other")
        impact = f.get("impact", "unknown")
        confidence = f.get("confidence", "unknown")
        project = f.get("project_id", "unknown")
        found_by = f.get("found_by", [])

        # WCAG
        if wcag:
            by_wcag[wcag] += 1
            principle_key = wcag.split(".")[0]
            by_principle[_PRINCIPLE.get(principle_key, "unknown")] += 1
            if rule:
                wcag_rule_pairs.append((wcag, rule))
        else:
            by_wcag["(sem critério)"] += 1
            by_principle["unknown"] += 1

        by_rule[rule] += 1
        by_type[itype] += 1
        by_impact[impact] += 1
        by_confidence[confidence] += 1
        by_project[project] += 1

        for tool in (found_by if isinstance(found_by, list) else [found_by]):
            if tool:
                by_tool[str(tool)] += 1

        domain = domain_map.get(project, "unknown")
        by_domain[domain] += 1

    # Construir mapa WCAG → regras que o detectam
    wcag_to_rules: dict[str, set[str]] = defaultdict(set)
    for wcag, rule in wcag_rule_pairs:
        wcag_to_rules[wcag].add(rule)

    return {
        "total": total,
        "confirmed": confirmed,
        "false_positives": fp,
        "by_wcag": dict(sorted(by_wcag.items())),
        "by_rule": dict(sorted(by_rule.items(), key=lambda x: -x[1])),
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_impact": {k: by_impact[k] for k in ["critical", "serious", "moderate", "minor"] if k in by_impact},
        "by_confidence": {k: by_confidence[k] for k in ["high", "medium", "low"] if k in by_confidence},
        "by_tool": dict(sorted(by_tool.items(), key=lambda x: -x[1])),
        "by_principle": dict(sorted(by_principle.items(), key=lambda x: -x[1])),
        "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])),
        "top_projects": dict(sorted(by_project.items(), key=lambda x: -x[1])[:15]),
        "wcag_to_rules": {k: sorted(v) for k, v in sorted(wcag_to_rules.items())},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Validação de mapeamentos
# ─────────────────────────────────────────────────────────────────────────────

def validate_mappings() -> list[str]:
    """
    Verifica se todos os critérios WCAG dos scanners têm mapeamentos em detection.py.

    Retorna lista de problemas encontrados (vazia = tudo ok).
    """
    problems: list[str] = []

    try:
        from a11y_autofix.protocol.detection import WCAG_TO_ISSUE_TYPE, WCAG_TO_COMPLEXITY
    except ImportError as e:
        return [f"Não foi possível importar detection.py: {e}"]

    # Verificar todas as regras ESLint
    for rule, wcag in _ESLINT_RULES_WCAG.items():
        if wcag not in WCAG_TO_ISSUE_TYPE:
            problems.append(
                f"❌ WCAG {wcag} (de {rule}) NÃO está em WCAG_TO_ISSUE_TYPE"
            )
        if wcag not in WCAG_TO_COMPLEXITY:
            # Não é erro crítico (há fallback), mas é um gap
            problems.append(
                f"⚠️  WCAG {wcag} (de {rule}) não está em WCAG_TO_COMPLEXITY (usará fallback)"
            )

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Impressão
# ─────────────────────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_DIM = "\033[2m"

_IMPACT_COLORS = {
    "critical": "\033[91m",   # red
    "serious": "\033[93m",    # yellow
    "moderate": "\033[94m",   # blue
    "minor": "\033[2m",       # dim
}

_CONFIDENCE_SYMBOLS = {"high": "●●●", "medium": "●●○", "low": "●○○"}


def _bar(count: int, total: int, width: int = 20) -> str:
    if total == 0:
        return " " * width
    filled = round(count / total * width)
    return "█" * filled + "░" * (width - filled)


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "  0.0%"
    return f"{count / total * 100:5.1f}%"


def print_section(title: str) -> None:
    width = 68
    print(f"\n{_BOLD}{'─' * width}{_RESET}")
    print(f"{_BOLD}  {title}{_RESET}")
    print(f"{_BOLD}{'─' * width}{_RESET}")


def print_report(data: dict[str, Any], source_label: str) -> None:
    width = 68
    print(f"\n{_BOLD}{'═' * width}{_RESET}")
    print(f"{_BOLD}  ♿ a11y-autofix — Relatório de Findings{_RESET}")
    print(f"{_BOLD}  Fonte: {source_label}{_RESET}")
    print(f"{_BOLD}{'═' * width}{_RESET}")

    total = data["total"]
    if total == 0:
        print(f"\n  {_YELLOW}Nenhum finding encontrado.{_RESET}")
        print(f"  Execute: bash reset_scan.sh --yes --and-scan")
        return

    confirmed = data.get("confirmed", 0)
    fp = data.get("false_positives", 0)
    print(f"\n  Total de findings : {_BOLD}{total}{_RESET}")
    if confirmed or fp:
        fp_rate = fp / max(total, 1) * 100
        print(f"  Confirmados       : {_GREEN}{confirmed}{_RESET}")
        print(f"  Falsos positivos  : {_RED}{fp}{_RESET}  ({fp_rate:.1f}%)")

    # ── Por Princípio WCAG ─────────────────────────────────────────────────
    print_section("Por Princípio WCAG (QM8 — cobertura 4 princípios)")
    principles = data["by_principle"]
    for p_key, p_name in _WCAG_PRINCIPLE_NAMES.items():
        key_name = _PRINCIPLE.get(p_key, "unknown")
        count = principles.get(key_name, 0)
        bar = _bar(count, total)
        status = _GREEN + "✓" + _RESET if count > 0 else _RED + "✗" + _RESET
        print(f"  {status}  {p_name:<38}  {bar}  {count:>5}  {_pct(count, total)}")
    unknown_p = principles.get("unknown", 0)
    if unknown_p:
        print(f"  {_DIM}     sem princípio{' ' * 26}  {_bar(unknown_p, total)}  {unknown_p:>5}  {_pct(unknown_p, total)}{_RESET}")

    # ── Por Critério WCAG (tabela completa) ────────────────────────────────
    print_section("Por Critério WCAG (todos os critérios encontrados)")
    by_wcag = data["by_wcag"]
    wcag_to_rules = data.get("wcag_to_rules", {})
    if not by_wcag:
        print(f"  {_DIM}  (nenhum){_RESET}")
    else:
        # Ordenar por princípio, depois pelo critério numericamente
        def wcag_sort_key(item: tuple[str, int]) -> tuple[str, float, float]:
            parts = item[0].replace("(", "").replace(")", "").split(".")
            try:
                return (parts[0], float(parts[1]) if len(parts) > 1 else 0, float(parts[2]) if len(parts) > 2 else 0)
            except (ValueError, IndexError):
                return ("9", 99, 99)

        header = f"  {'Critério':<10}  {'Regras que detectam':<35}  {'Count':>6}  {'%':>6}"
        print(f"  {_DIM}{'-' * 66}{_RESET}")
        print(f"{_DIM}{header}{_RESET}")
        print(f"  {_DIM}{'-' * 66}{_RESET}")

        prev_principle = ""
        for wcag, count in sorted(by_wcag.items(), key=wcag_sort_key):
            principle_key = wcag.split(".")[0] if wcag and "sem" not in wcag else ""
            principle_name = _PRINCIPLE.get(principle_key, "")

            if principle_name and principle_name != prev_principle:
                print(f"\n  {_CYAN}  ── {_WCAG_PRINCIPLE_NAMES.get(principle_key, principle_name).upper()} ──{_RESET}")
                prev_principle = principle_name

            rules_str = ", ".join(sorted(wcag_to_rules.get(wcag, [])))[:35]
            bar = _bar(count, total, width=12)
            print(f"  {_BOLD}{wcag:<10}{_RESET}  {_DIM}{rules_str:<35}{_RESET}  {count:>6}  {_pct(count, total)}")

    # ── Por Tipo de Issue ──────────────────────────────────────────────────
    print_section("Por Tipo de Issue (IssueType)")
    for itype, count in data["by_type"].items():
        bar = _bar(count, total)
        print(f"  {itype:<25}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Por Regra Específica ───────────────────────────────────────────────
    print_section("Por Regra Específica (top 25)")
    rules = list(data["by_rule"].items())[:25]
    if not rules:
        print(f"  {_DIM}  (nenhuma){_RESET}")
    for rule, count in rules:
        bar = _bar(count, total, width=15)
        wcag_for_rule = _ESLINT_RULES_WCAG.get(rule, "")
        wcag_hint = f"  [{wcag_for_rule}]" if wcag_for_rule else ""
        print(f"  {rule:<45}  {bar}  {count:>5}{_DIM}{wcag_hint}{_RESET}")

    # ── Por Impacto ────────────────────────────────────────────────────────
    print_section("Por Impacto")
    for impact, count in data["by_impact"].items():
        color = _IMPACT_COLORS.get(impact, "")
        bar = _bar(count, total)
        print(f"  {color}{impact:<15}{_RESET}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Por Confiança ──────────────────────────────────────────────────────
    print_section("Por Confiança (tool consensus)")
    for conf, count in data["by_confidence"].items():
        symbol = _CONFIDENCE_SYMBOLS.get(conf, "   ")
        bar = _bar(count, total)
        print(f"  {symbol}  {conf:<12}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Por Ferramenta ─────────────────────────────────────────────────────
    print_section("Por Ferramenta (found_by)")
    for tool, count in data["by_tool"].items():
        bar = _bar(count, total)
        print(f"  {tool:<30}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Por Domínio ────────────────────────────────────────────────────────
    print_section("Por Domínio do Projeto")
    for domain, count in data["by_domain"].items():
        bar = _bar(count, total)
        print(f"  {domain:<25}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Top projetos ───────────────────────────────────────────────────────
    print_section(f"Top {len(data['top_projects'])} projetos com mais findings")
    for proj, count in data["top_projects"].items():
        bar = _bar(count, total, width=18)
        print(f"  {proj:<45}  {bar}  {count:>5}")

    print(f"\n{_BOLD}{'═' * width}{_RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Exportação
# ─────────────────────────────────────────────────────────────────────────────

def export_json(data: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ JSON exportado: {path}")


def export_csv(findings: list[dict[str, Any]], path: Path) -> None:
    if not findings:
        print(f"  ⚠️  Nenhum finding para exportar")
        return
    fieldnames = [
        "finding_id", "project_id", "file", "wcag_criteria", "rule_id",
        "issue_type", "impact", "confidence", "tool_consensus", "found_by",
        "selector", "message",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for finding in findings:
            row = dict(finding)
            found_by = row.get("found_by", [])
            row["found_by"] = "|".join(found_by) if isinstance(found_by, list) else str(found_by)
            writer.writerow(row)
    print(f"  ✓ CSV exportado: {path}  ({len(findings)} linhas)")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relatório visual de findings de acessibilidade coletados",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source", choices=["scan", "annotated", "both"], default="both",
        help="Fonte dos dados: findings.jsonl (scan), ground_truth.jsonl (annotated), ou ambos (default)",
    )
    parser.add_argument(
        "--project", default=None,
        help="Filtrar por project_id (substring match, ex: saleor__storefront)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Exportar dados agregados como JSON",
    )
    parser.add_argument(
        "--csv", type=Path, default=None, dest="csv_path",
        help="Exportar findings individuais como CSV",
    )
    parser.add_argument(
        "--validate-mappings", action="store_true",
        help="Apenas validar se todos os critérios WCAG têm mapeamentos corretos",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Desativar cores ANSI",
    )
    args = parser.parse_args()

    # ── Validação de mapeamentos ────────────────────────────────────────────
    print("\n♿ a11y-autofix Findings Report")
    print("─" * 68)
    print(f"  Validando mapeamentos WCAG em detection.py ...")

    problems = validate_mappings()
    if problems:
        print(f"  {_YELLOW}⚠️  {len(problems)} gap(s) encontrado(s):{_RESET}")
        for p in problems:
            print(f"     {p}")
    else:
        print(f"  {_GREEN}✓ Todos os critérios WCAG dos scanners têm mapeamentos em detection.py{_RESET}")

    if args.validate_mappings:
        sys.exit(0 if not problems else 1)

    # ── Carregar findings ───────────────────────────────────────────────────
    print(f"\n  Carregando findings de {RESULTS_DIR} ...")

    scan_findings: list[dict] = []
    gt_findings: list[dict] = []

    if args.source in ("scan", "both"):
        scan_findings = load_scan_findings(args.project)
        print(f"  findings.jsonl (scan)        : {len(scan_findings):>6} registros")

    if args.source in ("annotated", "both"):
        gt_findings = load_ground_truth_findings(args.project)
        print(f"  ground_truth.jsonl (anotados): {len(gt_findings):>6} registros")

    # Escolher fonte para o relatório
    if args.source == "scan":
        findings = scan_findings
        source_label = "findings.jsonl (scan bruto)"
    elif args.source == "annotated":
        findings = gt_findings
        source_label = "ground_truth.jsonl (anotados)"
    else:
        # Preferir ground_truth se disponível, complementar com scan
        if gt_findings:
            findings = gt_findings
            source_label = f"ground_truth.jsonl ({len(gt_findings)} anotados) + {len(scan_findings)} scan bruto"
        else:
            findings = scan_findings
            source_label = f"findings.jsonl ({len(scan_findings)} scan bruto)"

    if not findings:
        print(f"\n  {_YELLOW}Nenhum finding encontrado ainda.{_RESET}")
        print(f"\n  O dataset ainda não foi scaneado. Execute:")
        print(f"    bash reset_scan.sh --yes --and-scan")
        print(f"\n  Ou para um scan rápido de validação (10 projetos):")
        print(f"    python dataset/scripts/quick_scan_report.py --max-projects 10\n")
        sys.exit(0)

    # ── Agregar e exibir ────────────────────────────────────────────────────
    data = aggregate(findings)
    print_report(data, source_label)

    # ── QM8 quick check ────────────────────────────────────────────────────
    principles_found = [k for k, v in data["by_principle"].items()
                        if k in ("perceivable", "operable", "understandable", "robust") and v > 0]
    qm8_pass = len(principles_found) >= 4
    status = f"{_GREEN}✓ QM8 PASS{_RESET}" if qm8_pass else f"{_RED}✗ QM8 FAIL{_RESET}"
    print(f"  QM8 — Cobertura WCAG: {len(principles_found)}/4 princípios  {status}")
    if not qm8_pass:
        missing = set(["perceivable", "operable", "understandable", "robust"]) - set(principles_found)
        print(f"        Princípios ausentes: {', '.join(sorted(missing))}")

    # ── Exportar ────────────────────────────────────────────────────────────
    if args.output:
        export_json(data, args.output)

    if args.csv_path:
        export_csv(findings, args.csv_path)

    # ── Cobertura de regras ESLint ──────────────────────────────────────────
    found_rules = set(data["by_rule"].keys())
    eslint_rules_found = found_rules & set(_ESLINT_RULES_WCAG.keys())
    if eslint_rules_found:
        print(f"\n  ESLint jsx-a11y: {len(eslint_rules_found)}/{len(_ESLINT_RULES_WCAG)} regras com findings")
    elif scan_findings or gt_findings:
        print(f"\n  {_YELLOW}⚠️  Nenhuma regra jsx-a11y encontrada nos findings.{_RESET}")
        import sys as _sys
        if _sys.platform == "win32":
            print(f"     Verifique se o ESLint está funcionando: .\\fix_scanners.ps1 -CheckOnly")
        else:
            print(f"     Verifique se o ESLint está funcionando: bash fix_scanners.sh --check-only")

    print()


if __name__ == "__main__":
    main()
