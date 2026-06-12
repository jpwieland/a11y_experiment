"""
Converte report.json do a11y-autofix para CSVs detalhados.

Uso:
    python report_to_csv.py report.json
    python report_to_csv.py report.json --output ./saida/
    python report_to_csv.py report.json --prefix meu_projeto

Gera 4 arquivos:
    issues.csv       — uma linha por issue (tabela principal)
    findings.csv     — uma linha por finding bruto (sub-issues por ferramenta)
    files.csv        — uma linha por arquivo escaneado
    summary.csv      — métricas gerais do relatório
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


# ── Constantes de colunas ────────────────────────────────────────────────────

ISSUES_COLS = [
    "issue_id",
    "file",
    "file_short",
    "module",
    "component",
    "type",
    "wcag_criteria",
    "wcag_description",
    "impact",
    "confidence",
    "complexity",
    "tool_consensus",
    "found_by",
    "selector",
    "message",
    "context",
    "resolved",
    "scan_time_seconds",
    "file_hash",
]

FINDINGS_COLS = [
    "issue_id",
    "file_short",
    "tool",
    "tool_version",
    "rule_id",
    "message",
    "selector",
    "type",
    "impact",
    "wcag_criteria",
]

FILES_COLS = [
    "file",
    "file_short",
    "module",
    "component",
    "total_issues",
    "critical",
    "serious",
    "moderate",
    "minor",
    "contrast",
    "semantic",
    "aria",
    "alt_text",
    "label",
    "keyboard",
    "other",
    "high_confidence",
    "medium_confidence",
    "low_confidence",
    "tools_used",
    "scan_time_seconds",
    "has_error",
    "error",
    "file_hash",
]

SUMMARY_COLS = ["metric", "value"]

# ── Mapeamento WCAG ──────────────────────────────────────────────────────────

WCAG_DESCRIPTIONS = {
    "1.1.1": "1.1.1 — Conteúdo não textual (Alt text)",
    "1.3.1": "1.3.1 — Informação e relacionamentos (Semântica)",
    "1.4.1": "1.4.1 — Uso de cor",
    "1.4.3": "1.4.3 — Contraste mínimo",
    "1.4.4": "1.4.4 — Redimensionamento de texto",
    "2.1.1": "2.1.1 — Teclado",
    "2.4.4": "2.4.4 — Propósito do link",
    "2.5.3": "2.5.3 — Etiqueta no nome",
    "2.5.8": "2.5.8 — Tamanho mínimo do alvo",
    "3.2.2": "3.2.2 — Ao entrar",
    "4.1.1": "4.1.1 — Análise (HTML válido)",
    "4.1.2": "4.1.2 — Nome, função, valor (ARIA)",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def shorten_path(full_path: str) -> str:
    """Remove o prefixo longo do caminho, mantendo a partir de src/app/."""
    for marker in ("src/app/", "projects/web/src/"):
        idx = full_path.find(marker)
        if idx != -1:
            return full_path[idx:]
    return Path(full_path).name


def extract_module(file_short: str) -> str:
    """Extrai o módulo principal a partir do caminho curto."""
    parts = Path(file_short).parts
    # Pula prefixo src/app ou projects/web/src/app
    start = 0
    for i, p in enumerate(parts):
        if p in ("app", "src"):
            start = i + 1
            break
    if start < len(parts):
        return parts[start] if len(parts) > start + 1 else "root"
    return "root"


def extract_component(file_short: str) -> str:
    """Extrai o nome do componente a partir do nome do arquivo."""
    name = Path(file_short).stem  # ex: header.component
    # Remove sufixos comuns Angular
    for suffix in (".component", ".page", ".module", ".service", ".directive"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def clean_message(msg: str) -> str:
    """Normaliza quebras de linha em mensagem para uma única linha."""
    return " | ".join(line.strip() for line in msg.splitlines() if line.strip())


# ── Geração dos CSVs ─────────────────────────────────────────────────────────

def generate_issues_csv(data: dict, output_path: Path) -> int:
    rows = []
    for fe in data.get("files", []):
        full_path = fe.get("file", "")
        file_short = shorten_path(full_path)
        module = extract_module(file_short)
        component = extract_component(file_short)
        scan_time = fe.get("scan_time_seconds", "")
        file_hash = fe.get("file_hash", "")

        for iss in fe.get("issues", []):
            wcag = iss.get("wcag_criteria") or ""
            rows.append({
                "issue_id":           iss.get("issue_id", ""),
                "file":               full_path,
                "file_short":         file_short,
                "module":             module,
                "component":          component,
                "type":               iss.get("type", ""),
                "wcag_criteria":      wcag,
                "wcag_description":   WCAG_DESCRIPTIONS.get(str(wcag), wcag),
                "impact":             iss.get("impact", ""),
                "confidence":         iss.get("confidence", ""),
                "complexity":         iss.get("complexity", ""),
                "tool_consensus":     iss.get("tool_consensus", ""),
                "found_by":           " | ".join(iss.get("found_by", [])),
                "selector":           iss.get("selector", ""),
                "message":            clean_message(iss.get("message", "")),
                "context":            iss.get("context", "").replace("\n", " "),
                "resolved":           iss.get("resolved", False),
                "scan_time_seconds":  scan_time,
                "file_hash":          file_hash,
            })

    _write_csv(output_path, ISSUES_COLS, rows)
    return len(rows)


def generate_findings_csv(data: dict, output_path: Path) -> int:
    rows = []
    for fe in data.get("files", []):
        file_short = shorten_path(fe.get("file", ""))
        for iss in fe.get("issues", []):
            issue_id = iss.get("issue_id", "")
            issue_type = iss.get("type", "")
            impact = iss.get("impact", "")
            wcag = iss.get("wcag_criteria") or ""
            for finding in iss.get("findings", []):
                rows.append({
                    "issue_id":     issue_id,
                    "file_short":   file_short,
                    "tool":         finding.get("tool", ""),
                    "tool_version": finding.get("tool_version", ""),
                    "rule_id":      finding.get("rule_id", ""),
                    "message":      clean_message(finding.get("message", "")),
                    "selector":     finding.get("selector", ""),
                    "type":         issue_type,
                    "impact":       impact,
                    "wcag_criteria": wcag,
                })

    _write_csv(output_path, FINDINGS_COLS, rows)
    return len(rows)


def generate_files_csv(data: dict, output_path: Path) -> int:
    rows = []
    for fe in data.get("files", []):
        full_path = fe.get("file", "")
        file_short = shorten_path(full_path)
        issues = fe.get("issues", [])

        def count(key: str, val: str) -> int:
            return sum(1 for i in issues if i.get(key) == val)

        rows.append({
            "file":               full_path,
            "file_short":         file_short,
            "module":             extract_module(file_short),
            "component":          extract_component(file_short),
            "total_issues":       len(issues),
            "critical":           count("impact", "critical"),
            "serious":            count("impact", "serious"),
            "moderate":           count("impact", "moderate"),
            "minor":              count("impact", "minor"),
            "contrast":           count("type", "contrast"),
            "semantic":           count("type", "semantic"),
            "aria":               count("type", "aria"),
            "alt_text":           count("type", "alt-text"),
            "label":              count("type", "label"),
            "keyboard":           count("type", "keyboard"),
            "other":              count("type", "other"),
            "high_confidence":    count("confidence", "high"),
            "medium_confidence":  count("confidence", "medium"),
            "low_confidence":     count("confidence", "low"),
            "tools_used":         " | ".join(fe.get("tools_used", [])),
            "scan_time_seconds":  fe.get("scan_time_seconds", ""),
            "has_error":          bool(fe.get("error")),
            "error":              fe.get("error") or "",
            "file_hash":          fe.get("file_hash", ""),
        })

    _write_csv(output_path, FILES_COLS, rows)
    return len(rows)


def generate_summary_csv(data: dict, output_path: Path) -> int:
    env = data.get("environment", {})
    cfg = data.get("configuration", {})
    smr = data.get("summary", {})

    rows = [
        {"metric": "execution_id",           "value": data.get("execution_id", "")},
        {"metric": "timestamp",              "value": data.get("timestamp", "")},
        {"metric": "wcag_level",             "value": data.get("wcag_level", "")},
        {"metric": "llm_model",              "value": env.get("llm_model", "")},
        {"metric": "python_version",         "value": env.get("python_version", "")},
        {"metric": "os",                     "value": env.get("os", "")},
        {"metric": "pa11y_version",          "value": env.get("tool_versions", {}).get("pa11y", "")},
        {"metric": "axe_core_version",       "value": env.get("tool_versions", {}).get("axe-core", "")},
        {"metric": "playwright_axe_version", "value": env.get("tool_versions", {}).get("playwright+axe", "")},
        {"metric": "min_tool_consensus",     "value": cfg.get("min_tool_consensus", "")},
        {"metric": "max_retries",            "value": cfg.get("max_retries", "")},
        {"metric": "total_files",            "value": smr.get("total_files", "")},
        {"metric": "files_with_issues",      "value": smr.get("files_with_issues", "")},
        {"metric": "files_clean",            "value": smr.get("total_files", 0) - smr.get("files_with_issues", 0)},
        {"metric": "total_issues",           "value": smr.get("total_issues", "")},
        {"metric": "high_confidence_issues", "value": smr.get("high_confidence_issues", "")},
        {"metric": "issues_fixed",           "value": smr.get("issues_fixed", "")},
        {"metric": "issues_pending",         "value": smr.get("issues_pending", "")},
        {"metric": "success_rate",           "value": smr.get("success_rate", "")},
        {"metric": "total_time_seconds",     "value": smr.get("total_time_seconds", "")},
    ]

    _write_csv(output_path, SUMMARY_COLS, rows)
    return len(rows)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Converte report.json do a11y-autofix para CSVs detalhados.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("report", type=Path, help="Caminho para report.json")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Diretório de saída (padrão: mesmo diretório do JSON)",
    )
    parser.add_argument(
        "--prefix", "-p", default="",
        help="Prefixo para os nomes dos arquivos CSV (ex: 'odr_web_')",
    )
    args = parser.parse_args()

    if not args.report.exists():
        print(f"Erro: arquivo não encontrado: {args.report}", file=sys.stderr)
        sys.exit(1)

    print(f"Lendo {args.report} ...", flush=True)
    with open(args.report, encoding="utf-8") as f:
        data = json.load(f)

    out_dir = args.output or args.report.parent
    prefix = args.prefix

    results = [
        ("issues",   generate_issues_csv,   out_dir / f"{prefix}issues.csv"),
        ("findings", generate_findings_csv, out_dir / f"{prefix}findings.csv"),
        ("files",    generate_files_csv,    out_dir / f"{prefix}files.csv"),
        ("summary",  generate_summary_csv,  out_dir / f"{prefix}summary.csv"),
    ]

    print()
    for name, fn, path in results:
        count = fn(data, path)
        print(f"  {name:10s} → {path}  ({count} linhas)")

    print()
    print("Conversão concluída.")


if __name__ == "__main__":
    main()
