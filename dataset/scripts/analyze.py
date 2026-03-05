#!/usr/bin/env python3
"""
Statistical analysis and reporting for the a11y-autofix benchmark corpus.

Computes descriptive statistics, frequency distributions, and per-domain
breakdowns suitable for inclusion in academic papers.  Outputs both
human-readable console tables and machine-readable JSON, with optional
LaTeX table generation for direct inclusion in manuscripts.

Analyses performed
------------------
A1  Corpus-level statistics (projects, files, findings)
A2  WCAG principle × impact distribution
A3  Issue type frequency (Pareto ranking)
A4  Per-domain finding distribution
A5  Tool consensus analysis (single vs multi-tool corroboration)
A6  Complexity × confidence cross-tabulation
A7  False-positive rate per domain
A8  Finding density (findings / component file)
A9  Top-20 most frequent WCAG criteria

Usage:
    python dataset/scripts/analyze.py
    python dataset/scripts/analyze.py --output-dir reports/
    python dataset/scripts/analyze.py --latex
    python dataset/scripts/analyze.py --analysis A3 A4
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    AnnotationLabel,
    GroundTruthFinding,
    ProjectEntry,
    ProjectStatus,
    ScanFinding,
)
from dataset.scripts.annotate import load_ground_truth, load_scan_findings

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_catalog(path: Path) -> tuple[list[ProjectEntry], dict[str, Any]]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            print(f"Warning: skipping {raw.get('id', '?')}: {e}", file=sys.stderr)
    return entries, data.get("metadata", {})


def collect_confirmed_findings(
    entries: list[ProjectEntry],
) -> tuple[list[GroundTruthFinding], dict[str, ProjectEntry]]:
    """
    Collect all confirmed GroundTruthFinding records across all projects.

    Falls back to ScanFinding records (treating all as confirmed) when
    ground-truth annotation is not yet available.
    """
    all_findings: list[GroundTruthFinding] = []
    entry_map = {e.id: e for e in entries}

    for entry in entries:
        if entry.status not in (ProjectStatus.ANNOTATED, ProjectStatus.SCANNED):
            continue

        # Try ground-truth first
        records = load_ground_truth(entry.id)
        confirmed = [
            gt for gt in records.values()
            if gt.ground_truth_label == AnnotationLabel.CONFIRMED
        ]

        if confirmed:
            all_findings.extend(confirmed)
        else:
            # Fall back to raw scan findings
            scan_findings = load_scan_findings(entry.id)
            for sf in scan_findings:
                gt = GroundTruthFinding(
                    finding_id=sf.finding_id,
                    project_id=sf.project_id,
                    file=sf.file,
                    selector=sf.selector,
                    wcag_criteria=sf.wcag_criteria,
                    issue_type=sf.issue_type,
                    impact=sf.impact,
                    complexity=sf.complexity,
                    confidence=sf.confidence,
                    tool_consensus=sf.tool_consensus,
                    auto_accepted=sf.tool_consensus >= 2,
                    ground_truth_label=AnnotationLabel.CONFIRMED,
                )
                all_findings.append(gt)

    return all_findings, entry_map


# ──────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────────────────────────────────────

def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "0.0%"


def _print_table(headers: list[str], rows: list[list[Any]], title: str = "") -> None:
    if title:
        print(f"\n  {title}")
        print("  " + "─" * max(60, sum(len(h) + 4 for h in headers)))

    col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                  for i, h in enumerate(headers)]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_widths)

    print(fmt.format(*headers))
    print("  " + "  ".join("─" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


def _latex_table(headers: list[str], rows: list[list[Any]], caption: str, label: str) -> str:
    """Generate a minimal LaTeX tabular environment."""
    col_spec = "l" + "r" * (len(headers) - 1)
    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        r"\small",
        f"\\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        " & ".join(f"\\textbf{{{h}}}" for h in headers) + r" \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(" & ".join(str(v) for v in row) + r" \\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
        r"\end{table}",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Analysis functions
# ──────────────────────────────────────────────────────────────────────────────

def analysis_a1_corpus_stats(
    entries: list[ProjectEntry],
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A1: Corpus-level statistics."""
    scanned = [e for e in entries if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)]
    annotated = [e for e in entries if e.status == ProjectStatus.ANNOTATED]

    domain_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    for e in scanned:
        d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
        domain_counts[d] += 1
        s = e.size_category.value if hasattr(e.size_category, "value") else str(e.size_category or "unknown")
        size_counts[s] += 1

    total_files = sum(
        (e.scan.findings.files_scanned if e.scan and e.scan.findings else 0)
        for e in scanned
    )

    stats: dict[str, Any] = {
        "total_projects_in_catalog": len(entries),
        "total_projects_scanned": len(scanned),
        "total_projects_annotated": len(annotated),
        "total_confirmed_findings": len(findings),
        "total_files_scanned": total_files,
        "avg_findings_per_project": round(len(findings) / max(len(scanned), 1), 1),
        "avg_findings_per_file": round(len(findings) / max(total_files, 1), 3),
        "domain_distribution": dict(domain_counts),
        "size_distribution": dict(size_counts),
    }

    print("\n  A1 – Corpus Statistics")
    print("  " + "─" * 50)
    print(f"  Projects in catalog   : {stats['total_projects_in_catalog']}")
    print(f"  Projects scanned      : {stats['total_projects_scanned']}")
    print(f"  Projects annotated    : {stats['total_projects_annotated']}")
    print(f"  Confirmed findings    : {stats['total_confirmed_findings']}")
    print(f"  Files scanned         : {stats['total_files_scanned']}")
    print(f"  Avg findings/project  : {stats['avg_findings_per_project']}")
    print(f"  Avg findings/file     : {stats['avg_findings_per_file']}")

    return stats


def analysis_a2_wcag_principle_impact(
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A2: WCAG principle × impact cross-tabulation."""
    principles = ["perceivable", "operable", "understandable", "robust", "unknown"]
    impacts = ["critical", "serious", "moderate", "minor"]
    criterion_map = {"1": "perceivable", "2": "operable", "3": "understandable", "4": "robust"}

    table: dict[str, dict[str, int]] = {p: {i: 0 for i in impacts} for p in principles}
    totals_p: dict[str, int] = defaultdict(int)
    totals_i: dict[str, int] = defaultdict(int)

    for f in findings:
        first = (f.wcag_criteria or "").split(".")[0]
        p = criterion_map.get(first, "unknown")
        i = f.impact if f.impact in impacts else "moderate"
        table[p][i] += 1
        totals_p[p] += 1
        totals_i[i] += 1

    headers = ["Principle", "Critical", "Serious", "Moderate", "Minor", "Total"]
    rows = []
    for p in principles:
        row = [
            p.capitalize(),
            table[p]["critical"],
            table[p]["serious"],
            table[p]["moderate"],
            table[p]["minor"],
            totals_p[p],
        ]
        rows.append(row)
    rows.append([
        "Total",
        totals_i["critical"],
        totals_i["serious"],
        totals_i["moderate"],
        totals_i["minor"],
        len(findings),
    ])

    _print_table(headers, rows, "A2 – WCAG Principle × Impact")
    return {"principle_impact_table": {p: dict(v) for p, v in table.items()}}


def analysis_a3_issue_type_pareto(
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A3: Issue type frequency (Pareto ranking)."""
    counter: Counter[str] = Counter(f.issue_type for f in findings if f.issue_type)
    total = len(findings)
    cumulative = 0

    headers = ["Rank", "Issue Type", "Count", "Pct", "Cumulative"]
    rows = []
    for rank, (itype, count) in enumerate(counter.most_common(), 1):
        cumulative += count
        rows.append([
            rank,
            itype,
            count,
            _pct(count, total),
            _pct(cumulative, total),
        ])

    _print_table(headers, rows, "A3 – Issue Type Pareto Ranking")
    return {"issue_type_counts": dict(counter)}


def analysis_a4_per_domain(
    entries: list[ProjectEntry],
    findings: list[GroundTruthFinding],
    entry_map: dict[str, ProjectEntry],
) -> dict[str, Any]:
    """A4: Per-domain finding distribution."""
    domain_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"projects": 0, "findings": 0, "files": 0, "fp": 0}
    )

    for e in entries:
        if e.status not in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED):
            continue
        d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
        domain_data[d]["projects"] += 1
        files = (e.scan.findings.files_scanned if e.scan and e.scan.findings else 0)
        domain_data[d]["files"] += files

    for f in findings:
        entry = entry_map.get(f.project_id)
        if not entry:
            continue
        d = entry.domain.value if hasattr(entry.domain, "value") else str(entry.domain)
        domain_data[d]["findings"] += 1

    # FP from ground-truth records
    for e in entries:
        if e.status != ProjectStatus.ANNOTATED:
            continue
        records = load_ground_truth(e.id)
        d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
        for gt in records.values():
            if gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE:
                domain_data[d]["fp"] += 1

    headers = ["Domain", "Projects", "Files", "Findings", "Findings/File", "FP"]
    rows = []
    for domain, data in sorted(domain_data.items()):
        findings_per_file = round(data["findings"] / max(data["files"], 1), 2)
        rows.append([
            domain,
            data["projects"],
            data["files"],
            data["findings"],
            findings_per_file,
            data["fp"],
        ])
    _print_table(headers, rows, "A4 – Per-Domain Distribution")
    return {"per_domain": {d: dict(v) for d, v in domain_data.items()}}


def analysis_a5_tool_consensus(
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A5: Tool consensus distribution."""
    counter: Counter[int] = Counter(f.tool_consensus for f in findings)
    total = len(findings)

    headers = ["Consensus", "Count", "Pct"]
    rows = sorted([
        [k, v, _pct(v, total)]
        for k, v in counter.items()
    ])
    _print_table(headers, rows, "A5 – Tool Consensus Distribution")

    single_tool = counter.get(1, 0)
    multi_tool = sum(v for k, v in counter.items() if k >= 2)
    return {
        "consensus_counts": dict(counter),
        "single_tool_pct": round(single_tool / total * 100, 1) if total else 0,
        "multi_tool_pct": round(multi_tool / total * 100, 1) if total else 0,
    }


def analysis_a6_complexity_confidence(
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A6: Complexity × confidence cross-tabulation."""
    complexities = ["trivial", "simple", "moderate", "complex"]
    confidences = ["high", "medium", "low"]

    table: dict[str, dict[str, int]] = {
        c: {cf: 0 for cf in confidences}
        for c in complexities
    }

    for f in findings:
        comp = f.complexity if f.complexity in complexities else "simple"
        conf = f.confidence if f.confidence in confidences else "medium"
        table[comp][conf] += 1

    total = len(findings)
    headers = ["Complexity", "High", "Medium", "Low", "Total"]
    rows = []
    for comp in complexities:
        row_total = sum(table[comp].values())
        rows.append([
            comp.capitalize(),
            table[comp]["high"],
            table[comp]["medium"],
            table[comp]["low"],
            row_total,
        ])
    _print_table(headers, rows, "A6 – Complexity × Confidence")
    return {"complexity_confidence_table": {c: dict(v) for c, v in table.items()}}


def analysis_a7_fp_rate_per_domain(
    entries: list[ProjectEntry],
    entry_map: dict[str, ProjectEntry],
) -> dict[str, Any]:
    """A7: False-positive rate per domain."""
    annotated = [e for e in entries if e.status == ProjectStatus.ANNOTATED]
    if not annotated:
        print("\n  A7 – FP Rate per Domain: no annotated projects yet.")
        return {}

    domain_fp: dict[str, dict[str, int]] = defaultdict(lambda: {"confirmed": 0, "fp": 0})
    for e in annotated:
        records = load_ground_truth(e.id)
        d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
        for gt in records.values():
            if gt.ground_truth_label == AnnotationLabel.CONFIRMED:
                domain_fp[d]["confirmed"] += 1
            elif gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE:
                domain_fp[d]["fp"] += 1

    headers = ["Domain", "Confirmed", "FP", "Total", "FP Rate"]
    rows = []
    for domain, data in sorted(domain_fp.items()):
        total = data["confirmed"] + data["fp"]
        fp_rate = _pct(data["fp"], total)
        rows.append([domain, data["confirmed"], data["fp"], total, fp_rate])
    _print_table(headers, rows, "A7 – False-Positive Rate per Domain")
    return {"domain_fp_rates": {d: dict(v) for d, v in domain_fp.items()}}


def analysis_a8_finding_density(
    entries: list[ProjectEntry],
    findings: list[GroundTruthFinding],
    entry_map: dict[str, ProjectEntry],
) -> dict[str, Any]:
    """A8: Finding density distribution (findings per 100 component files)."""
    project_counts: dict[str, dict[str, int]] = {}

    for e in entries:
        if e.status not in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED):
            continue
        files = (e.scan.findings.files_scanned if e.scan and e.scan.findings else 0)
        project_counts[e.id] = {"files": files, "findings": 0}

    for f in findings:
        if f.project_id in project_counts:
            project_counts[f.project_id]["findings"] += 1

    densities = []
    for pid, data in project_counts.items():
        if data["files"] > 0:
            density = data["findings"] / data["files"] * 100
            densities.append((pid, data["files"], data["findings"], round(density, 1)))

    densities.sort(key=lambda x: x[3], reverse=True)

    headers = ["Project", "Files", "Findings", "Density/100 files"]
    rows = [(pid[:35], files, findings, d) for pid, files, findings, d in densities[:20]]
    _print_table(headers, rows, "A8 – Finding Density (top 20 projects)")

    values = [d for _, _, _, d in densities]
    stats: dict[str, Any] = {}
    if values:
        stats = {
            "min": min(values),
            "max": max(values),
            "mean": round(sum(values) / len(values), 1),
            "median": sorted(values)[len(values) // 2],
        }
        print(f"\n  Density stats: min={stats['min']} max={stats['max']} "
              f"mean={stats['mean']} median={stats['median']}")

    return {"density_stats": stats, "per_project_density": {pid: d for pid, _, _, d in densities}}


def analysis_a9_top_criteria(
    findings: list[GroundTruthFinding],
) -> dict[str, Any]:
    """A9: Top-20 most frequent WCAG criteria."""
    counter: Counter[str] = Counter(
        f.wcag_criteria for f in findings if f.wcag_criteria
    )
    total = len(findings)

    headers = ["Rank", "WCAG Criterion", "Count", "Pct"]
    rows = [
        [rank, crit, count, _pct(count, total)]
        for rank, (crit, count) in enumerate(counter.most_common(20), 1)
    ]
    _print_table(headers, rows, "A9 – Top-20 WCAG Criteria")
    return {"top_criteria": dict(counter.most_common(20))}


# ──────────────────────────────────────────────────────────────────────────────
# LaTeX export
# ──────────────────────────────────────────────────────────────────────────────

def export_latex(
    findings: list[GroundTruthFinding],
    entries: list[ProjectEntry],
    output_dir: Path,
) -> None:
    """Export selected tables as LaTeX to output_dir/tables/."""
    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Table 1: Issue type frequencies
    counter: Counter[str] = Counter(f.issue_type for f in findings if f.issue_type)
    total = len(findings)
    rows_t1 = [
        [itype.replace("_", r"\_"), count, f"{count/total*100:.1f}\\%"]
        for itype, count in counter.most_common(10)
    ]
    tex = _latex_table(
        ["Issue Type", "Count", "\\%"],
        rows_t1,
        caption="Top-10 accessibility issue types in the benchmark corpus.",
        label="tab:issue_types",
    )
    (tables_dir / "issue_types.tex").write_text(tex, encoding="utf-8")

    # Table 2: Domain distribution
    domain_counts: Counter[str] = Counter()
    for e in entries:
        if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED):
            d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
            domain_counts[d] += 1
    rows_t2 = [[d.replace("_", r"\_"), c] for d, c in sorted(domain_counts.items())]
    tex2 = _latex_table(
        ["Application Domain", "Projects"],
        rows_t2,
        caption="Distribution of benchmark projects across application domains.",
        label="tab:domain_distribution",
    )
    (tables_dir / "domain_distribution.tex").write_text(tex2, encoding="utf-8")

    print(f"\n  LaTeX tables written to {tables_dir}/")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

ALL_ANALYSES = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Statistical analysis of the a11y-autofix benchmark corpus"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Write JSON report to this directory")
    parser.add_argument("--latex", action="store_true",
                        help="Export LaTeX tables")
    parser.add_argument("--analysis", nargs="+", default=None,
                        choices=ALL_ANALYSES,
                        help="Run only specified analyses (default: all)")
    args = parser.parse_args()

    print("\n♿ a11y-autofix Dataset Analyser\n" + "═" * 50)

    entries, metadata = load_catalog(args.catalog)
    print(f"  Loaded {len(entries)} projects")

    findings, entry_map = collect_confirmed_findings(entries)
    print(f"  Confirmed findings: {len(findings)}\n")

    if not findings:
        print("  No findings to analyse. Run scan.py and annotate.py first.")
        return

    selected = set(args.analysis) if args.analysis else set(ALL_ANALYSES)
    report: dict[str, Any] = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "catalog": str(args.catalog),
    }

    if "A1" in selected:
        report["A1"] = analysis_a1_corpus_stats(entries, findings)
    if "A2" in selected:
        report["A2"] = analysis_a2_wcag_principle_impact(findings)
    if "A3" in selected:
        report["A3"] = analysis_a3_issue_type_pareto(findings)
    if "A4" in selected:
        report["A4"] = analysis_a4_per_domain(entries, findings, entry_map)
    if "A5" in selected:
        report["A5"] = analysis_a5_tool_consensus(findings)
    if "A6" in selected:
        report["A6"] = analysis_a6_complexity_confidence(findings)
    if "A7" in selected:
        report["A7"] = analysis_a7_fp_rate_per_domain(entries, entry_map)
    if "A8" in selected:
        report["A8"] = analysis_a8_finding_density(entries, findings, entry_map)
    if "A9" in selected:
        report["A9"] = analysis_a9_top_criteria(findings)

    # JSON output
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = args.output_dir / "analysis_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n  JSON report written to {report_path}")

        if args.latex:
            export_latex(findings, entries, args.output_dir)
    elif args.latex:
        export_latex(findings, entries, RESULTS_DIR / "reports")

    print(f"\n{'═' * 50}")
    print(f"  Analysis complete. {len(findings)} confirmed findings across "
          f"{sum(1 for e in entries if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED))} projects.")


if __name__ == "__main__":
    main()
