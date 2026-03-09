#!/usr/bin/env python3
"""
Multi-tool accessibility scanning script for the a11y-autofix benchmark corpus.

Para cada projeto no catálogo (status = snapshotted):
  1. Descobre todos os arquivos .tsx/.jsx do projeto
  2. Executa o MultiToolScanner (pa11y + axe + playwright+axe + ESLint jsx-a11y)
  3. Aplica o DetectionProtocol (deduplicação, consenso, mapeamento WCAG)
  4. Salva resultados por projeto (JSON) e por finding (JSONL)
  5. Atualiza o catálogo com estatísticas de scan
  6. Emite dataset_findings.jsonl consolidado para análise

Usage:
    python dataset/scripts/scan.py                              # scan todos pendentes
    python dataset/scripts/scan.py --project saleor__storefront # só um projeto
    python dataset/scripts/scan.py --workers 2 --timeout 120   # paralelo
    python dataset/scripts/scan.py --force                     # re-scana escaneados
    python dataset/scripts/scan.py --max-files 10              # teste rápido

Protocol ref: dataset/PROTOCOL.md §7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    FindingSummary,
    ProjectEntry,
    ProjectScanSummary,
    ProjectStatus,
    ScanFinding,
)

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

CRITERION_TO_PRINCIPLE: dict[str, str] = {
    "1": "perceivable",
    "2": "operable",
    "3": "understandable",
    "4": "robust",
}


def wcag_to_principle(criterion: str | None) -> str:
    if not criterion:
        return "unknown"
    first_char = criterion.split(".")[0] if "." in criterion else criterion[:1]
    return CRITERION_TO_PRINCIPLE.get(first_char, "unknown")


def build_findings_summary(issues: list[Any]) -> FindingSummary:
    """Agrega lista de A11yIssue em FindingSummary."""
    summary = FindingSummary()
    summary.total_issues = len(issues)

    for issue in issues:
        conf = getattr(issue, "confidence", None)
        if conf is not None:
            conf_val = conf.value if hasattr(conf, "value") else str(conf)
            if conf_val == "high":
                summary.high_confidence += 1
            elif conf_val == "medium":
                summary.medium_confidence += 1
            else:
                summary.low_confidence += 1

        itype = issue.issue_type.value if hasattr(issue.issue_type, "value") else str(issue.issue_type)
        summary.by_type[itype] = summary.by_type.get(itype, 0) + 1

        principle = wcag_to_principle(issue.wcag_criteria)
        summary.by_principle[principle] = summary.by_principle.get(principle, 0) + 1

        impact = getattr(issue, "impact", "moderate") or "moderate"
        summary.by_impact[impact] = summary.by_impact.get(impact, 0) + 1

        if issue.wcag_criteria:
            crit = issue.wcag_criteria
            summary.by_criterion[crit] = summary.by_criterion.get(crit, 0) + 1

    return summary


def issue_to_scan_finding(issue: Any, project_id: str, pinned_commit: str) -> ScanFinding:
    """Converte A11yIssue em ScanFinding para o JSONL do dataset."""
    return ScanFinding(
        finding_id=issue.issue_id,
        project_id=project_id,
        file=issue.file,
        selector=issue.selector,
        message=issue.message,
        wcag_criteria=issue.wcag_criteria,
        rule_id=issue.findings[0].rule_id if issue.findings else "",
        issue_type=issue.issue_type.value if hasattr(issue.issue_type, "value") else str(issue.issue_type),
        impact=issue.impact,
        complexity=issue.complexity.value if hasattr(issue.complexity, "value") else str(issue.complexity),
        tool_consensus=issue.tool_consensus,
        found_by=[t.value if hasattr(t, "value") else str(t) for t in issue.found_by],
        confidence=issue.confidence.value if hasattr(issue.confidence, "value") else str(issue.confidence),
        raw_findings=[
            {
                "tool": f.tool.value if hasattr(f.tool, "value") else str(f.tool),
                "tool_version": f.tool_version,
                "rule_id": f.rule_id,
                "message": f.message,
                "selector": f.selector,
                "impact": f.impact,
            }
            for f in issue.findings
        ],
        pinned_commit=pinned_commit,
        scan_date=datetime.now(tz=timezone.utc).isoformat(),
    )


async def scan_project(
    entry: ProjectEntry,
    scan_timeout: int = 120,
    min_consensus: int = 1,
    force: bool = False,
    max_files: int | None = None,
) -> tuple[ProjectEntry, list[ScanFinding]]:
    """
    Executa o MultiToolScanner completo em um projeto snapshotado.

    Args:
        entry: Entrada do projeto no catálogo.
        scan_timeout: Timeout por arquivo em segundos.
        min_consensus: Mínimo de ferramentas para HIGH confidence.
        force: Forçar re-scan mesmo se já escaneado.
        max_files: Limitar número de arquivos por projeto (para testes).

    Returns:
        Tupla (entry atualizado, lista de ScanFinding).
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.scanner.orchestrator import MultiToolScanner
    from a11y_autofix.utils.files import find_react_files

    project_dir = SNAPSHOTS_DIR / entry.id
    result_dir = RESULTS_DIR / entry.id
    result_dir.mkdir(parents=True, exist_ok=True)

    summary_path = result_dir / "summary.json"
    if not force and summary_path.exists() and entry.status == ProjectStatus.SCANNED:
        print(f"  [{entry.id}] Já escaneado (use --force para re-escanear).")
        return entry, []

    if not project_dir.exists():
        print(f"  [{entry.id}] ⚠️  Snapshot não encontrado. Execute snapshot.py.", file=sys.stderr)
        entry.scan = ProjectScanSummary(status="error")
        entry.scan.error_message = "Snapshot directory not found"
        return entry, []

    # Configurações: todas as ferramentas habilitadas
    # Lighthouse desabilitado (muito lento para bulk scanning)
    settings = Settings(
        use_pa11y=True,
        use_axe=True,
        use_lighthouse=False,
        use_playwright=True,
        use_eslint=True,
        min_tool_consensus=min_consensus,
        scan_timeout=scan_timeout,
        max_concurrent_scans=2,
    )
    scanner = MultiToolScanner(settings)

    # Descobrir arquivos de componentes React (excluindo testes, stories, etc.)
    files: list[Path] = []
    scan_paths = entry.scan_paths if entry.scan_paths else ["."]
    for rel_path in scan_paths:
        scan_dir = project_dir / rel_path.rstrip("/")
        if scan_dir.exists():
            found = find_react_files(scan_dir, recursive=True)
            files.extend(found)

    # Deduplificar preservando ordem
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    if not unique_files:
        print(f"  [{entry.id}] ⚠️  Nenhum arquivo .tsx/.jsx encontrado em {scan_paths}")
        entry.scan = ProjectScanSummary(status="error")
        entry.scan.error_message = "No component files found"
        return entry, []

    if max_files:
        unique_files = unique_files[:max_files]
        print(f"  [{entry.id}] (limitado a {max_files} arquivos)")

    print(f"  [{entry.id}] 🔍 Escaneando {len(unique_files)} arquivo(s)...")
    t0 = time.perf_counter()

    try:
        scan_results = await scanner.scan_files(unique_files, wcag="WCAG2AA")
    except Exception as e:
        print(f"  [{entry.id}] ❌ Erro no scan: {e}", file=sys.stderr)
        entry.scan = ProjectScanSummary(status="error")
        entry.scan.error_message = str(e)[:500]
        return entry, []

    duration = time.perf_counter() - t0

    # Agregar findings
    all_issues = [issue for sr in scan_results for issue in sr.issues]

    # Construir sumário
    summary = build_findings_summary(all_issues)
    summary.files_scanned = len(unique_files)
    summary.files_with_issues = sum(1 for sr in scan_results if sr.has_issues)
    summary.scan_duration_seconds = round(duration, 2)
    summary.scan_date = datetime.now(tz=timezone.utc).isoformat()

    # Coletar ferramentas e versões usadas
    tools_seen: set[str] = set()
    for sr in scan_results:
        for tool in sr.tools_used:
            tool_name = tool.value if hasattr(tool, "value") else str(tool)
            if tool_name not in tools_seen:
                summary.tools_succeeded.append(tool_name)
                tools_seen.add(tool_name)
        summary.tool_versions.update(sr.tool_versions)

    # Construir ScanFinding records para o JSONL
    scan_findings = [
        issue_to_scan_finding(issue, entry.id, entry.snapshot.pinned_commit)
        for sr in scan_results
        for issue in sr.issues
    ]

    # ── Persistir resultados ───────────────────────────────────────────────────

    # Audit trail completo (JSON)
    full_results = []
    for sr in scan_results:
        try:
            full_results.append(sr.model_dump(mode="json"))
        except Exception:
            full_results.append({"file": str(sr.file), "error": "serialization_error"})

    (result_dir / "scan_results.json").write_text(
        json.dumps(full_results, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    (result_dir / "summary.json").write_text(
        json.dumps(summary.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with open(result_dir / "findings.jsonl", "w", encoding="utf-8") as fp:
        for finding in scan_findings:
            fp.write(finding.model_dump_json() + "\n")

    # Atualizar entrada do catálogo
    entry.scan = ProjectScanSummary(
        status="success" if all_issues else "no_issues",
        findings=summary,
    )
    entry.status = ProjectStatus.SCANNED

    tools_str = ", ".join(summary.tools_succeeded) if summary.tools_succeeded else "nenhuma"
    print(
        f"  [{entry.id}] ✅ {summary.total_issues} issues "
        f"({summary.high_confidence} high) | "
        f"{len(unique_files)} arquivos | "
        f"{duration:.1f}s | "
        f"tools: {tools_str}"
    )
    return entry, scan_findings


def load_catalog(path: Path) -> tuple[list[ProjectEntry], dict[str, Any]]:
    if not path.exists():
        return [], {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            print(f"  ⚠️  Aviso: {raw.get('id', '?')}: {e}", file=sys.stderr)
    return entries, data.get("metadata", {})


def save_catalog(entries: list[ProjectEntry], path: Path, metadata: dict[str, Any]) -> None:
    output: dict[str, Any] = {
        "projects": [e.to_catalog_dict() for e in entries],
        "metadata": {
            **metadata,
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
            "total_projects": len(entries),
            "scanned": sum(1 for e in entries if e.status == ProjectStatus.SCANNED),
        },
    }
    with open(path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


async def main_async(args: argparse.Namespace) -> None:
    print("\n♿  a11y-autofix Dataset Scanner")
    print("=" * 56)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    entries, metadata = load_catalog(args.catalog)
    print(f"  Catálogo: {len(entries)} projetos")

    if args.project:
        targets = [e for e in entries if e.id == args.project]
        if not targets:
            print(f"  ❌ Projeto '{args.project}' não encontrado.", file=sys.stderr)
            sys.exit(1)
    else:
        targets = [
            e for e in entries
            if e.status == ProjectStatus.SNAPSHOTTED
            or (args.force and e.status == ProjectStatus.SCANNED)
        ]

    if not targets:
        scanned = sum(1 for e in entries if e.status == ProjectStatus.SCANNED)
        print(f"  ℹ️  Nenhum projeto para escanear.")
        print(f"     ({scanned} já escaneados / {len(entries)} total)")
        print(f"     Execute snapshot.py primeiro, ou use --force.")
        return

    print(f"  Targets:  {len(targets)} projetos")
    print(f"  Workers:  {args.workers} paralelos")
    print(f"  Timeout:  {args.timeout}s/arquivo")
    print(f"  Consenso: ≥{args.min_consensus} tool(s) → HIGH confidence")
    if args.max_files:
        print(f"  Limite:   {args.max_files} arquivos/projeto")
    print("")

    entry_index = {e.id: e for e in entries}
    all_findings: list[ScanFinding] = []
    sem = asyncio.Semaphore(args.workers)

    async def scan_with_sem(e: ProjectEntry) -> tuple[ProjectEntry, list[ScanFinding]]:
        async with sem:
            return await scan_project(
                e,
                scan_timeout=args.timeout,
                min_consensus=args.min_consensus,
                force=args.force,
                max_files=args.max_files,
            )

    t0 = time.perf_counter()
    results = await asyncio.gather(*[scan_with_sem(e) for e in targets], return_exceptions=True)
    total_dur = time.perf_counter() - t0

    for result in results:
        if isinstance(result, Exception):
            print(f"  ❌ Scan falhou: {result}", file=sys.stderr)
            continue
        updated_entry, findings = result
        entry_index[updated_entry.id] = updated_entry
        all_findings.extend(findings)

    # Consolidated findings JSONL
    consolidated_path = RESULTS_DIR / "dataset_findings.jsonl"
    mode = "a" if consolidated_path.exists() and not args.force else "w"
    with open(consolidated_path, mode, encoding="utf-8") as f:
        for finding in all_findings:
            f.write(finding.model_dump_json() + "\n")

    # Dataset stats
    all_entries = list(entry_index.values())
    scanned_entries = [e for e in all_entries if e.status == ProjectStatus.SCANNED]
    total_issues = sum(e.scan.findings.total_issues for e in scanned_entries)
    high_conf = sum(e.scan.findings.high_confidence for e in scanned_entries)
    medium_conf = sum(e.scan.findings.medium_confidence for e in scanned_entries)

    by_type: dict[str, int] = {}
    by_principle: dict[str, int] = {}
    for e in scanned_entries:
        for k, v in e.scan.findings.by_type.items():
            by_type[k] = by_type.get(k, 0) + v
        for k, v in e.scan.findings.by_principle.items():
            by_principle[k] = by_principle.get(k, 0) + v

    (RESULTS_DIR / "dataset_stats.json").write_text(
        json.dumps({
            "total_projects_in_catalog": len(all_entries),
            "total_projects_scanned": len(scanned_entries),
            "total_issues": total_issues,
            "high_confidence_issues": high_conf,
            "medium_confidence_issues": medium_conf,
            "low_confidence_issues": total_issues - high_conf - medium_conf,
            "high_conf_rate_pct": round(high_conf / max(total_issues, 1) * 100, 1),
            "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
            "by_principle": dict(sorted(by_principle.items(), key=lambda x: -x[1])),
            "scan_date": datetime.now(tz=timezone.utc).isoformat(),
            "total_scan_seconds": round(total_dur, 2),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    save_catalog(all_entries, args.catalog, metadata)

    # Resumo
    print("\n" + "=" * 56)
    print(f"  ✅ Concluído em {total_dur:.1f}s")
    print(f"  Projetos escaneados:  {len(scanned_entries)}/{len(all_entries)}")
    print(f"  Findings (sessão):    {len(all_findings)}")
    print(f"  Total no corpus:      {total_issues} issues")
    print(f"  High confidence:      {high_conf} ({round(high_conf/max(total_issues,1)*100,1)}%)")
    print(f"  Dataset JSONL:        {consolidated_path}")
    print(f"\n  Relatório: python dataset/scripts/findings_report.py\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan de acessibilidade multi-ferramenta para o corpus a11y-autofix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--project", default=None, help="ID exato do projeto a escanear")
    parser.add_argument("--workers", type=int, default=1, help="Projetos em paralelo (default: 1)")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout por arquivo em segundos")
    parser.add_argument("--min-consensus", type=int, default=1, dest="min_consensus",
                        help="Mínimo de tools para HIGH confidence (default: 1)")
    parser.add_argument("--force", action="store_true", help="Re-escanear projetos já escaneados")
    parser.add_argument("--max-files", type=int, default=None, dest="max_files",
                        help="Limitar arquivos por projeto (útil para testes)")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
