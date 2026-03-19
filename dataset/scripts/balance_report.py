#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relatorio de Balanceamento e Rigor Cientifico do Dataset a11y-autofix.

Avalia se o dataset esta balanceado, justo e cientificamente rigoroso
através de metricas estatísticas e indicadores visuais de qualidade.

Metricas calculadas:
  - Indice de Herfindahl-Hirschman (HHI) por criterio WCAG
    (HHI < 0.18 = diverso, 0.18-0.25 = moderado, > 0.25 = concentrado)
  - Entropia de Shannon (maxima = log2(n), normalizada 0-1)
  - Coeficiente de Gini (desigualdade de findings por projeto)
  - Cobertura de principios WCAG (4/4 necessario para rigor)
  - Distribuicao de densidade (findings/projeto: percentis, outliers)

Uso:
    python dataset/scripts/balance_report.py                  # relatorio completo
    python dataset/scripts/balance_report.py --live           # live_findings.jsonl
    python dataset/scripts/balance_report.py --watch 5        # atualiza a cada 5s
    python dataset/scripts/balance_report.py --min-projects 50 # so se >= 50 projetos
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
CATALOG_PATH = DATASET_ROOT / "catalog" / "projects.yaml"

sys.path.insert(0, str(REPO_ROOT))

# ── Paleta ANSI ──────────────────────────────────────────────────────────────
R = "\033[0m"        # reset
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"

# Cores por status
_C = {
    "PASS": f"{GREEN}{BOLD}",
    "WARN": f"{YELLOW}{BOLD}",
    "FAIL": f"{RED}{BOLD}",
    "INFO": f"{CYAN}",
}

# ── Constantes de avaliação ───────────────────────────────────────────────────
# Herfindahl-Hirschman Index: < 0.18 diverso, 0.18-0.25 moderado, > 0.25 concentrado
HHI_DIVERSE   = 0.18
HHI_MODERATE  = 0.25

# Entropia normalizada: > 0.7 boa, 0.5-0.7 aceitavel, < 0.5 ruim
ENT_GOOD = 0.70
ENT_OK   = 0.50

# Criterios WCAG minimos para rigor
MIN_WCAG_CRITERIA = 6
MIN_WCAG_PRINCIPLES = 4

# Concentracao maxima aceitavel em 1 criterio
MAX_SINGLE_CRITERION_PCT = 0.40

# Projetos minimos para dataset valido
MIN_PROJECTS = 30

# Achados minimos por projeto (mediana)
MIN_MEDIAN_FINDINGS = 3

# Percentual minimo de multi-tool consensus
MIN_MULTI_TOOL_PCT = 0.10

# Mapeamentos
PRINCIPLE = {"1": "perceivable", "2": "operable", "3": "understandable", "4": "robust"}
PRINCIPLE_EMOJI = {
    "perceivable": "👁 ",
    "operable": "⌨ ",
    "understandable": "💡",
    "robust": "🔧",
}
IMPACT_ORDER = ["critical", "serious", "moderate", "minor"]
IMPACT_COLOR = {
    "critical": RED,
    "serious": YELLOW,
    "moderate": BLUE,
    "minor": DIM,
}
DOMAIN_NAMES = {
    "ecommerce": "E-commerce",
    "government": "Government",
    "healthcare": "Healthcare",
    "education": "Education",
    "developer_tools": "Dev Tools",
    "dashboard": "Dashboard",
    "social": "Social",
    "other": "Other",
}


# ─── Utilitários de renderização ──────────────────────────────────────────────

def _w() -> int:
    """Largura do terminal."""
    try:
        return min(os.get_terminal_size().columns, 100)
    except OSError:
        return 88

def _bar(value: float, total: float, width: int = 22, color: str = CYAN) -> str:
    """Barra de progresso colorida."""
    if total <= 0:
        return DIM + "░" * width + R
    ratio = min(value / total, 1.0)
    filled = round(ratio * width)
    return color + "█" * filled + DIM + "░" * (width - filled) + R

def _pct(value: float, total: float) -> str:
    if total <= 0:
        return "  —  "
    return f"{value / total * 100:5.1f}%"

def _status_icon(status: str) -> str:
    icons = {"PASS": f"{GREEN}✔{R}", "WARN": f"{YELLOW}⚠{R}",
             "FAIL": f"{RED}✘{R}", "INFO": f"{CYAN}ℹ{R}"}
    return icons.get(status, " ")

def _hline(width: int, char: str = "─") -> str:
    return char * width

def _box_title(title: str, width: int, char_h: str = "─",
               char_tl: str = "┌", char_tr: str = "┐") -> str:
    inner = f" {title} "
    pad = width - 2 - len(inner)
    return (f"{char_tl}{char_h * 2}{inner}"
            f"{char_h * max(pad, 0)}{char_tr}")

def _clear() -> None:
    os.system("cls" if sys.platform == "win32" else "clear")


# ─── Métricas estatísticas ────────────────────────────────────────────────────

def hhi(counts: dict) -> float:
    """Índice de Herfindahl-Hirschman (concentração de mercado)."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return sum((c / total) ** 2 for c in counts.values())

def shannon_entropy(counts: dict, normalized: bool = True) -> float:
    """Entropia de Shannon. Se normalized=True, divide pelo max (log2 n)."""
    total = sum(counts.values())
    if total == 0 or len(counts) <= 1:
        return 0.0
    ent = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    if normalized:
        max_ent = math.log2(len(counts))
        return ent / max_ent if max_ent > 0 else 0.0
    return ent

def gini(values: list[float]) -> float:
    """Coeficiente de Gini para desigualdade de distribuição."""
    if not values or len(values) < 2:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    cumsum = 0.0
    for i, v in enumerate(vals):
        cumsum += (2 * (i + 1) - n - 1) * v
    total = sum(vals)
    if total == 0:
        return 0.0
    return abs(cumsum) / (n * total)

def percentiles(values: list[float]) -> dict[str, float]:
    """Calcula p5, p25, p50, p75, p95 de uma lista."""
    if not values:
        return {k: 0.0 for k in ("p5", "p25", "p50", "p75", "p95", "mean", "std")}
    s = sorted(values)
    n = len(s)
    def p(pct: float) -> float:
        idx = (pct / 100) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return s[lo] + (idx - lo) * (s[hi] - s[lo])
    return {
        "p5": p(5), "p25": p(25), "p50": p(50),
        "p75": p(75), "p95": p(95),
        "mean": mean(s),
        "std": stdev(s) if n > 1 else 0.0,
    }

def ascii_boxplot(stats: dict, width: int = 30) -> str:
    """Mini boxplot horizontal em ASCII."""
    lo, hi = stats["p5"], stats["p95"]
    rng = hi - lo
    if rng <= 0:
        return "─" * width
    def pos(v: float) -> int:
        return round((v - lo) / rng * width)
    line = [" "] * (width + 1)
    for i in range(width + 1):
        line[i] = "─"
    p25 = min(pos(stats["p25"]), width)
    p75 = min(pos(stats["p75"]), width)
    p50 = min(pos(stats["p50"]), width)
    for i in range(p25, p75 + 1):
        line[i] = "█"
    if 0 <= p50 <= width:
        line[p50] = "┼"
    return "".join(line[:width + 1])


# ─── Carregamento ─────────────────────────────────────────────────────────────

def load_findings(live: bool = False) -> list[dict]:
    findings: list[dict] = []

    if live:
        path = RESULTS_DIR / "live_findings.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        findings.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return findings

    if not RESULTS_DIR.exists():
        return findings
    for proj_dir in sorted(RESULTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        fp = proj_dir / "findings.jsonl"
        if fp.exists():
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        findings.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return findings


def load_catalog() -> dict[str, dict]:
    """Retorna {project_id: entry} do catálogo YAML."""
    if not CATALOG_PATH.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
        return {p["id"]: p for p in data.get("projects", []) if "id" in p}
    except Exception:
        return {}


def load_scan_stats() -> dict[str, int]:
    """Conta projetos por status no catálogo."""
    catalog = load_catalog()
    counts: Counter = Counter()
    for p in catalog.values():
        counts[p.get("status", "unknown")] += 1
    return dict(counts)


# ─── Agregação ────────────────────────────────────────────────────────────────

def aggregate(findings: list[dict], catalog: dict[str, dict]) -> dict[str, Any]:
    """Computa todas as métricas necessárias para o relatório."""

    by_wcag:       Counter = Counter()
    by_principle:  Counter = Counter()
    by_type:       Counter = Counter()
    by_impact:     Counter = Counter()
    by_confidence: Counter = Counter()
    by_tool:       Counter = Counter()
    by_domain:     Counter = Counter()
    by_size:       Counter = Counter()
    by_project:    Counter = Counter()
    by_rule:       Counter = Counter()
    multi_tool = 0

    domain_map = {pid: p.get("domain", "other")    for pid, p in catalog.items()}
    size_map   = {pid: p.get("size_category", "?") for pid, p in catalog.items()}

    for f in findings:
        wcag    = f.get("wcag_criteria") or ""
        itype   = f.get("issue_type", "other")
        impact  = f.get("impact", "moderate")
        conf    = f.get("confidence", "low")
        project = f.get("project_id", "?")
        found   = f.get("found_by", [])
        rule    = f.get("rule_id", "?")

        by_project[project] += 1
        by_type[itype] += 1
        by_impact[impact] += 1
        by_confidence[conf] += 1
        by_rule[rule] += 1

        if wcag:
            by_wcag[wcag] += 1
            pk = wcag.split(".")[0]
            by_principle[PRINCIPLE.get(pk, "unknown")] += 1
        else:
            by_principle["unknown"] += 1

        if isinstance(found, list):
            for t in found:
                by_tool[str(t)] += 1
            if len(found) >= 2:
                multi_tool += 1

        dom = domain_map.get(project, catalog.get(project, {}).get("domain", "other"))
        by_domain[str(dom)] += 1

        sz = size_map.get(project, catalog.get(project, {}).get("size_category", "?"))
        by_size[str(sz)] += 1

    total = sum(by_project.values())
    n_projects = len(by_project)
    findings_per_project = list(by_project.values())

    # Métricas de concentração
    hhi_wcag  = hhi(by_wcag) if by_wcag else 0.0
    hhi_type  = hhi(by_type) if by_type else 0.0
    ent_wcag  = shannon_entropy(by_wcag)
    ent_type  = shannon_entropy(by_type)
    ent_domain = shannon_entropy(by_domain)
    gini_proj = gini(findings_per_project)
    pct_stats = percentiles([float(v) for v in findings_per_project])

    # Critério mais concentrado
    top_criterion_pct = max(by_wcag.values()) / total if total and by_wcag else 0.0
    top_criterion     = max(by_wcag, key=by_wcag.get) if by_wcag else "—"

    multi_tool_pct = multi_tool / total if total else 0.0

    return {
        "total": total,
        "n_projects": n_projects,
        "multi_tool": multi_tool,
        "multi_tool_pct": multi_tool_pct,
        "by_wcag":       dict(sorted(by_wcag.items())),
        "by_principle":  dict(by_principle),
        "by_type":       dict(sorted(by_type.items(),      key=lambda x: -x[1])),
        "by_impact":     {k: by_impact[k] for k in IMPACT_ORDER if k in by_impact},
        "by_confidence": {k: by_confidence[k]
                          for k in ("high", "medium", "low") if k in by_confidence},
        "by_tool":       dict(sorted(by_tool.items(),      key=lambda x: -x[1])),
        "by_domain":     dict(sorted(by_domain.items(),    key=lambda x: -x[1])),
        "by_size":       dict(sorted(by_size.items(),      key=lambda x: -x[1])),
        "by_project":    dict(sorted(by_project.items(),   key=lambda x: -x[1])),
        "by_rule":       dict(sorted(by_rule.items(),      key=lambda x: -x[1])),
        "hhi_wcag":      hhi_wcag,
        "hhi_type":      hhi_type,
        "ent_wcag":      ent_wcag,
        "ent_type":      ent_type,
        "ent_domain":    ent_domain,
        "gini_proj":     gini_proj,
        "pct_stats":     pct_stats,
        "top_criterion": top_criterion,
        "top_criterion_pct": top_criterion_pct,
        "n_wcag_criteria":   len(by_wcag),
        "n_wcag_principles": sum(
            1 for p in ("perceivable", "operable", "understandable", "robust")
            if by_principle.get(p, 0) > 0
        ),
        "n_domains": len(by_domain),
    }


# ─── Checklist de rigor científico ────────────────────────────────────────────

def rigor_checks(a: dict, scan_stats: dict) -> list[tuple[str, str, str]]:
    """
    Retorna lista de (status, label, detalhe) para cada critério de rigor.
    status = 'PASS' | 'WARN' | 'FAIL' | 'INFO'
    """
    total   = a["total"]
    n_proj  = a["n_projects"]
    checks: list[tuple[str, str, str]] = []

    # ── 1. Volume mínimo ────────────────────────────────────────────────────
    s = "PASS" if n_proj >= MIN_PROJECTS else ("WARN" if n_proj >= 10 else "FAIL")
    checks.append((s, "Volume de projetos",
                   f"{n_proj} projetos  (mínimo recomendado: {MIN_PROJECTS})"))

    # ── 2. Cobertura WCAG (4 princípios) ────────────────────────────────────
    np = a["n_wcag_principles"]
    s = "PASS" if np >= 4 else ("WARN" if np >= 3 else "FAIL")
    checks.append((s, "Cobertura WCAG — 4 princípios",
                   f"{np}/4 princípios com findings"))

    # ── 3. Diversidade de critérios WCAG ────────────────────────────────────
    nc = a["n_wcag_criteria"]
    s = "PASS" if nc >= MIN_WCAG_CRITERIA else ("WARN" if nc >= 4 else "FAIL")
    checks.append((s, "Diversidade de critérios WCAG",
                   f"{nc} critérios únicos  (mínimo: {MIN_WCAG_CRITERIA})"))

    # ── 4. Concentração (HHI) ───────────────────────────────────────────────
    h = a["hhi_wcag"]
    if h < HHI_DIVERSE:
        s, desc = "PASS", "distribuição diversa"
    elif h < HHI_MODERATE:
        s, desc = "WARN", "concentração moderada"
    else:
        s, desc = "FAIL", "concentração excessiva"
    checks.append((s, "Índice HHI — critérios WCAG",
                   f"HHI = {h:.3f}  ({desc})  top: {a['top_criterion']} "
                   f"({a['top_criterion_pct']:.0%})"))

    # ── 5. Concentração de tipo ─────────────────────────────────────────────
    ht = a["hhi_type"]
    s = "PASS" if ht < HHI_DIVERSE else ("WARN" if ht < HHI_MODERATE else "FAIL")
    checks.append((s, "Índice HHI — tipos de issue",
                   f"HHI = {ht:.3f}"))

    # ── 6. Entropia Shannon ─────────────────────────────────────────────────
    ew = a["ent_wcag"]
    s = "PASS" if ew >= ENT_GOOD else ("WARN" if ew >= ENT_OK else "FAIL")
    checks.append((s, "Entropia Shannon — critérios WCAG",
                   f"H = {ew:.2f}  (normalizada; 1.0 = distribuição perfeita)"))

    # ── 7. Desigualdade por projeto (Gini) ──────────────────────────────────
    g = a["gini_proj"]
    if g < 0.4:
        s, desc = "PASS", "distribuição homogênea"
    elif g < 0.65:
        s, desc = "WARN", "desigualdade moderada entre projetos"
    else:
        s, desc = "FAIL", "alguns projetos dominam o dataset"
    checks.append((s, "Coeficiente de Gini — findings/projeto",
                   f"Gini = {g:.2f}  ({desc})"))

    # ── 8. Densidade mínima ─────────────────────────────────────────────────
    med = a["pct_stats"]["p50"]
    s = "PASS" if med >= MIN_MEDIAN_FINDINGS else ("WARN" if med >= 1 else "FAIL")
    checks.append((s, "Densidade mediana de findings/projeto",
                   f"mediana = {med:.0f}  (mínimo: {MIN_MEDIAN_FINDINGS})"))

    # ── 9. Consenso multi-ferramenta ────────────────────────────────────────
    mt = a["multi_tool_pct"]
    s = "PASS" if mt >= MIN_MULTI_TOOL_PCT else ("WARN" if mt > 0 else "INFO")
    checks.append((s, "Findings com consenso multi-ferramenta",
                   f"{mt:.1%} confirmados por ≥ 2 ferramentas"))

    # ── 10. Diversidade de domínios ─────────────────────────────────────────
    nd = a["n_domains"]
    s = "PASS" if nd >= 5 else ("WARN" if nd >= 3 else "FAIL")
    checks.append((s, "Diversidade de domínios",
                   f"{nd} domínios representados"))

    return checks


# ─── Renderização ──────────────────────────────────────────────────────────────

def render(findings: list[dict], catalog: dict, scan_stats: dict,
           live: bool, timestamp: str) -> None:
    W = _w()

    if not findings:
        print(f"\n{YELLOW}  Nenhum finding encontrado ainda.{R}")
        print(f"  Execute o scan: python dataset/scripts/scan.py")
        return

    a = aggregate(findings, catalog)
    checks = rigor_checks(a, scan_stats)
    total = a["total"]

    # ── Cabeçalho ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═' * W}{R}")
    title = "♿  a11y-autofix — Dataset Balance Report"
    print(f"{BOLD}  {title}{R}")
    subtitle = f"{'[AO VIVO]  ' if live else ''}{timestamp}"
    print(f"{DIM}  {subtitle}{R}")
    print(f"{BOLD}{'═' * W}{R}")

    # Status rápido
    scanned = scan_stats.get("scanned", 0)
    total_cat = sum(scan_stats.values())
    prog_pct = scanned / total_cat * 100 if total_cat else 0
    print(f"\n  {BOLD}{a['n_projects']}{R} projetos  •  "
          f"{BOLD}{total:,}{R} findings  •  "
          f"scan {CYAN}{scanned}/{total_cat}{R} ({prog_pct:.0f}%)  •  "
          f"{len(a['by_wcag'])} critérios WCAG únicos")

    # ── Painel de indicadores de qualidade ────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  INDICADORES DE RIGOR CIENTÍFICO{R}")
    print(f"{BOLD}  {'─' * (W - 2)}{R}")

    pass_c = sum(1 for s, _, _ in checks if s == "PASS")
    warn_c = sum(1 for s, _, _ in checks if s == "WARN")
    fail_c = sum(1 for s, _, _ in checks if s == "FAIL")

    score_color = GREEN if fail_c == 0 and warn_c <= 2 else (YELLOW if fail_c == 0 else RED)
    print(f"  Score: {score_color}{BOLD}{pass_c}/{len(checks)} PASS{R}  "
          f"{YELLOW}{warn_c} WARN{R}  {RED}{fail_c} FAIL{R}")
    print()

    label_w = 42
    for status, label, detail in checks:
        icon = _status_icon(status)
        sc = _C.get(status, "")
        print(f"  {icon}  {sc}{label:<{label_w}}{R}  {DIM}{detail}{R}")

    # ── Distribuição por Princípio WCAG ───────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  DISTRIBUIÇÃO POR PRINCÍPIO WCAG{R}")
    print(f"  {'─' * (W - 2)}")

    principles_order = ["perceivable", "operable", "understandable", "robust"]
    p_names = {
        "perceivable":    "P1 Perceivable  — imagens, contraste, texto",
        "operable":       "P2 Operable     — teclado, foco, links",
        "understandable": "P3 Understandable — labels, idioma",
        "robust":         "P4 Robust       — ARIA, nome/papel/valor",
    }
    for pk in principles_order:
        count  = a["by_principle"].get(pk, 0)
        emoji  = PRINCIPLE_EMOJI.get(pk, "  ")
        bar    = _bar(count, total, width=20, color=CYAN)
        pct    = _pct(count, total)
        status = f"{GREEN}✔{R}" if count > 0 else f"{RED}✘{R}"
        print(f"  {emoji}  {status}  {p_names[pk]:<44}  {bar}  {count:>6}  {pct}")

    unknown_p = a["by_principle"].get("unknown", 0)
    if unknown_p:
        print(f"  {'':4}  {DIM}sem princípio{' ' * 39}"
              f"  {_bar(unknown_p, total, 20, DIM)}  {unknown_p:>6}  {_pct(unknown_p, total)}{R}")

    # ── Por Critério WCAG ─────────────────────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  CRITÉRIOS WCAG ENCONTRADOS  "
          f"{DIM}(HHI={a['hhi_wcag']:.3f}  H={a['ent_wcag']:.2f}){R}")
    print(f"  {'─' * (W - 2)}")

    def wcag_key(s: str) -> tuple:
        parts = s.split(".")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (99, 99, 99)

    prev_p = ""
    for wcag, count in sorted(a["by_wcag"].items(), key=lambda x: wcag_key(x[0])):
        pk = wcag.split(".")[0]
        pname = PRINCIPLE.get(pk, "")
        if pname and pname != prev_p:
            pcolor = {
                "perceivable": CYAN, "operable": GREEN,
                "understandable": YELLOW, "robust": MAGENTA,
            }.get(pname, WHITE)
            print(f"\n  {pcolor}{BOLD}  ── {pname.upper()} ──{R}")
            prev_p = pname

        pct_val = count / total if total else 0
        bar_color = (RED if pct_val > MAX_SINGLE_CRITERION_PCT
                     else (YELLOW if pct_val > 0.20 else CYAN))
        bar = _bar(count, total, width=18, color=bar_color)
        warn = f" {RED}◀ alta concentração{R}" if pct_val > MAX_SINGLE_CRITERION_PCT else ""
        print(f"  {BOLD}{wcag:<10}{R}  {bar}  {count:>6}  {_pct(count, total)}{warn}")

    # ── Por Tipo de Issue ─────────────────────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  POR TIPO DE ISSUE  "
          f"{DIM}(HHI={a['hhi_type']:.3f}  H={a['ent_type']:.2f}){R}")
    print(f"  {'─' * (W - 2)}")

    type_colors = {
        "alt_text":  GREEN,   "contrast": YELLOW,  "label":    CYAN,
        "aria":      MAGENTA, "keyboard": BLUE,    "focus":    WHITE,
        "semantic":  GREEN,   "other":    DIM,
    }
    for itype, count in a["by_type"].items():
        color = type_colors.get(itype, WHITE)
        bar   = _bar(count, total, width=20, color=color)
        print(f"  {color}{itype:<18}{R}  {bar}  {count:>6}  {_pct(count, total)}")

    # ── Por Impacto e Confiança ───────────────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  IMPACTO  ×  CONFIANÇA{R}")
    print(f"  {'─' * (W - 2)}")

    col_w = (W - 6) // 2
    # Imprimir lado a lado
    impact_lines = []
    for imp, count in a["by_impact"].items():
        c = IMPACT_COLOR.get(imp, "")
        b = _bar(count, total, width=14, color=c)
        impact_lines.append(f"  {c}{imp:<12}{R}  {b}  {count:>5}  {_pct(count, total)}")

    conf_lines = []
    conf_symbols = {"high": "●●●", "medium": "●●○", "low": "●○○"}
    conf_colors  = {"high": GREEN, "medium": YELLOW, "low": DIM}
    for conf, count in a["by_confidence"].items():
        sym = conf_symbols.get(conf, "   ")
        c   = conf_colors.get(conf, "")
        b   = _bar(count, total, width=14, color=c)
        conf_lines.append(f"  {c}{sym} {conf:<8}{R}  {b}  {count:>5}  {_pct(count, total)}")

    for il, cl in zip(impact_lines, conf_lines):
        print(il)
    for cl in conf_lines[len(impact_lines):]:
        print(cl)

    # ── Por Ferramenta ────────────────────────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  POR FERRAMENTA  "
          f"{DIM}({a['multi_tool_pct']:.1%} com consenso ≥ 2 ferramentas){R}")
    print(f"  {'─' * (W - 2)}")

    tool_colors = {
        "playwright+axe": CYAN, "axe-core": BLUE,
        "pa11y": YELLOW, "eslint": GREEN,
    }
    for tool, count in a["by_tool"].items():
        color = tool_colors.get(tool, WHITE)
        bar   = _bar(count, total, width=20, color=color)
        print(f"  {color}{tool:<22}{R}  {bar}  {count:>6}  {_pct(count, total)}")

    # ── Distribuição por domínio e tamanho ────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  DOMÍNIO  ×  TAMANHO DO PROJETO{R}")
    print(f"  {'─' * (W - 2)}")

    # Domínio
    dom_total = sum(a["by_domain"].values())
    for dom, count in a["by_domain"].items():
        dom_label = DOMAIN_NAMES.get(dom, dom)
        bar = _bar(count, dom_total, width=16, color=CYAN)
        print(f"  {dom_label:<18}  {bar}  {count:>5} proj  {_pct(count, dom_total)}")

    # Tamanho
    print()
    size_colors = {"small": GREEN, "medium": YELLOW, "large": RED}
    size_labels = {"small": "small (10-50 arq)", "medium": "medium (51-300)", "large": "large (301+)"}
    sz_total = sum(a["by_size"].values())
    for sz in ("small", "medium", "large"):
        count = a["by_size"].get(sz, 0)
        color = size_colors.get(sz, WHITE)
        bar   = _bar(count, sz_total, width=16, color=color)
        print(f"  {color}{size_labels.get(sz, sz):<20}{R}  {bar}  {count:>5} proj  {_pct(count, sz_total)}")

    # ── Distribuição de densidade (findings/projeto) ───────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    ps = a["pct_stats"]
    print(f"{BOLD}  DISTRIBUIÇÃO DE FINDINGS POR PROJETO  "
          f"{DIM}(Gini = {a['gini_proj']:.2f}){R}")
    print(f"  {'─' * (W - 2)}")

    print(f"  média   {ps['mean']:>7.1f}   "
          f"mediana {ps['p50']:>7.1f}   "
          f"desvio {ps['std']:>7.1f}")
    print(f"  p5   {ps['p5']:>5.0f}   "
          f"p25 {ps['p25']:>5.0f}   "
          f"p75 {ps['p75']:>5.0f}   "
          f"p95 {ps['p95']:>5.0f}")

    bp = ascii_boxplot(ps, width=min(50, W - 20))
    lo_lbl = f"{ps['p5']:.0f}"
    hi_lbl = f"{ps['p95']:.0f}"
    print(f"\n  {DIM}p5={lo_lbl}{R}  {CYAN}{bp}{R}  {DIM}p95={hi_lbl}{R}")
    print(f"  {DIM}{'':>{len(lo_lbl)+4}}{'▲ mediana':>{round(len(bp) * ps['p50'] / max(ps['p95'] - ps['p5'], 1))}}{R}")

    # Outliers (projetos com > p95 findings)
    outliers = [(pid, cnt) for pid, cnt in a["by_project"].items()
                if cnt > ps["p95"] * 1.5]
    if outliers:
        print(f"\n  {YELLOW}Outliers ({len(outliers)} projetos > 1.5×p95 = "
              f"{ps['p95'] * 1.5:.0f}):{R}")
        for pid, cnt in sorted(outliers, key=lambda x: -x[1])[:5]:
            print(f"  {DIM}  {pid:<50}  {cnt:>5} findings{R}")

    # ── Top regras ────────────────────────────────────────────────────────
    print(f"\n{BOLD}  {'─' * (W - 2)}{R}")
    print(f"{BOLD}  TOP 15 REGRAS ESPECÍFICAS{R}")
    print(f"  {'─' * (W - 2)}")

    for rule, count in list(a["by_rule"].items())[:15]:
        bar = _bar(count, total, width=16, color=CYAN)
        pct_v = count / total if total else 0
        color = RED if pct_v > 0.30 else (YELLOW if pct_v > 0.15 else R)
        print(f"  {color}{rule:<45}{R}  {bar}  {count:>5}  {_pct(count, total)}")

    # ── Rodapé com veredicto ───────────────────────────────────────────────
    print(f"\n{BOLD}{'═' * W}{R}")
    print(f"{BOLD}  VEREDICTO CIENTÍFICO{R}")
    print(f"{'═' * W}{R}")

    if fail_c == 0 and warn_c == 0:
        print(f"\n  {GREEN}{BOLD}✔  EXCELENTE — Dataset balanceado e cientificamente rigoroso.{R}")
    elif fail_c == 0:
        print(f"\n  {YELLOW}{BOLD}⚠  BOM — {warn_c} aviso(s). Dataset utilizável com ressalvas.{R}")
        for status, label, detail in checks:
            if status == "WARN":
                print(f"     → {label}: {detail}")
    else:
        print(f"\n  {RED}{BOLD}✘  ATENÇÃO — {fail_c} problema(s) crítico(s) no dataset:{R}")
        for status, label, detail in checks:
            if status == "FAIL":
                print(f"     → {RED}{label}{R}: {detail}")
        if warn_c:
            print(f"\n  {YELLOW}  Adicionalmente {warn_c} aviso(s):{R}")
            for status, label, detail in checks:
                if status == "WARN":
                    print(f"     → {label}: {detail}")

    print(f"\n{BOLD}{'═' * W}{R}\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relatório de balanceamento e rigor científico do dataset a11y-autofix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Ler de live_findings.jsonl (scan em andamento)",
    )
    parser.add_argument(
        "--watch", type=int, default=0, metavar="SECS",
        help="Atualizar a cada N segundos (ex: --watch 10)",
    )
    parser.add_argument(
        "--min-projects", type=int, default=0, metavar="N",
        help="Só exibir se houver pelo menos N projetos com findings",
    )
    args = parser.parse_args()

    catalog   = load_catalog()
    scan_stats = load_scan_stats()

    def run_once() -> None:
        findings  = load_findings(live=args.live)
        n_proj = len({f.get("project_id") for f in findings if f.get("project_id")})
        if args.min_projects and n_proj < args.min_projects:
            ts = time.strftime("%H:%M:%S")
            print(f"\r  {DIM}[{ts}] aguardando {args.min_projects} projetos... "
                  f"(atual: {n_proj}){R}", end="", flush=True)
            return

        if args.watch:
            _clear()

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        render(findings, catalog, scan_stats, live=args.live, timestamp=ts)

    if args.watch:
        try:
            while True:
                run_once()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print(f"\n{DIM}  Interrompido pelo usuário.{R}\n")
    else:
        run_once()


if __name__ == "__main__":
    main()
