#!/usr/bin/env python3
"""
Multi-tool accessibility scanning script for the a11y-autofix benchmark corpus.

For each snapshotted project in the catalog:
  1. Runs the a11y-autofix MultiToolScanner on all component files
  2. Applies the DetectionProtocol (deduplication, confidence, WCAG mapping)
  3. Saves per-project scan results (full JSON audit trail + summary)
  4. Updates catalog entries with FindingSummary statistics
  5. Emits a consolidated dataset-level findings JSONL for analysis

Usage:
    python dataset/scripts/scan.py --catalog dataset/catalog/projects.yaml
    python dataset/scripts/scan.py --project saleor__storefront
    python dataset/scripts/scan.py --workers 2 --timeout 90

References:
    Protocol: dataset/PROTOCOL.md §7
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

# WCAG criterion → principle mapping
CRITERION_TO_PRINCIPLE: dict[str, str] = {
    "1": "perceivable", "2": "operable", "3": "understandable", "4": "robust",
}


def wcag_to_principle(criterion: str | None) -> str:
    if not criterion:
        return "unknown"
    first_char = criterion.split(".")[0] if criterion else ""
    return CRITERION_TO_PRINCIPLE.get(first_char, "unknown")


def build_findings_summary(issues: list[Any]) -> FindingSummary:
    """
    Aggregate a list of A11yIssue objects into a FindingSummary.
    Accepts the Pydantic A11yIssue model from a11y_autofix.config.
    """
    summary = FindingSummary()
    summary.total_issues = len(issues)

    for issue in issues:
        # Confidence breakdown
        conf = getattr(issue, "confidence", None)
        if conf is not None:
            conf_val = conf.value if hasattr(conf, "value") else str(conf)
            if conf_val == "high":
                summary.high_confidence += 1
            elif conf_val == "medium":
                summary.medium_confidence += 1
            else:
                summary.low_confidence += 1

        # By issue type
        itype = issue.issue_type.value if hasattr(issue.issue_type, "value") else str(issue.issue_type)
        summary.by_type[itype] = summary.by_type.get(itype, 0) + 1

        # By WCAG principle
        principle = wcag_to_principle(issue.wcag_criteria)
        summary.by_principle[principle] = summary.by_principle.get(principle, 0) + 1

        # By impact
        impact = getattr(issue, "impact", "moderate") or "moderate"
        summary.by_impact[impact] = summary.by_impact.get(impact, 0) + 1

        # By criterion
        if issue.wcag_criteria:
            crit = issue.wcag_criteria
            summary.by_criterion[crit] = summary.by_criterion.get(crit, 0) + 1

    return summary


def issue_to_scan_finding(issue: Any, project_id: str, pinned_commit: str) -> ScanFinding:
    """Convert an A11yIssue to a ScanFinding for the dataset findings JSONL."""
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
    scan_timeout: int = 90,
    min_consensus: int = 1,
    force: bool = False,
) -> tuple[ProjectEntry, list[ScanFinding]]:
    """
    Execute the a11y-autofix multi-tool scanner on a snapshotted project.

    Returns updated entry with scan summary and list of ScanFindings.
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.scanner.orchestrator import MultiToolScanner
    from a11y_autofix.utils.files import find_react_files

    project_dir = SNAPSHOTS_DIR / entry.id
    result_dir = RESULTS_DIR / entry.id
    result_dir.mkdir(parents=True, exist_ok=True)

    summary_path = result_dir / "summary.json"
    if not force and summary_path.exists() and entry.status == ProjectStatus.SCANNED:
        print(f"  [{entry.id}] Already scanned. Skipping (use --force to re-scan).")
        return entry, []

    if not project_dir.exists():
        print(f"  [{entry.id}] Snapshot not found. Run snapshot.py first.", file=sys.stderr)
        entry.scan.status = "error"
        entry.scan.error_message = "Snapshot directory not found"
        return entry, []

    # Build settings with loose consensus (collect all findings for annotation)
    settings = Settings(
        use_pa11y=True,
        use_axe=True,
        use_lighthouse=False,  # too slow for bulk scanning
        use_playwright=True,
        min_tool_consensus=min_consensus,
        scan_timeout=scan_timeout,
        max_concurrent_scans=2,
    )
    scanner = MultiToolScanner(settings)

    # Discover component files
    files: list[Path] = []
    for rel_path in entry.scan_paths:
        scan_dir = project_dir / rel_path.rstrip("/")
        if scan_dir.exists():
            found = find_react_files(scan_dir, recursive=True)
            files.extend(found)

    # Deduplicate
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    if not unique_files:
        print(f"  [{entry.id}] No component files found in scan_paths.")
        entry.scan.status = "error"
        entry.scan.error_message = "No component files found"
        return entry, []

    print(f"  [{entry.id}] Scanning {len(unique_files)} files ...")
    t0 = time.perf_counter()

    # Execute scan
    try:
        scan_results = await scanner.scan_files(unique_files, wcag="WCAG2AA")
    except Exception as e:
        print(f"  [{entry.id}] Scan error: {e}", file=sys.stderr)
        entry.scan.status = "error"
        entry.scan.error_message = str(e)[:300]
        return entry, []

    duration = time.perf_counter() - t0

    # Aggregate findings
    all_issues = [issue for sr in scan_results for issue in sr.issues]

    # Build summary
    summary = build_findings_summary(all_issues)
    summary.files_scanned = len(unique_files)
    summary.files_with_issues = sum(1 for sr in scan_results if sr.has_issues)
    summary.scan_duration_seconds = round(duration, 2)
    summary.scan_date = datetime.now(tz=timezone.utc).isoformat()

    # Collect tool info from results
    for sr in scan_results:
        for tool in sr.tools_used:
            tool_name = tool.value if hasattr(tool, "value") else str(tool)
            if tool_name not in summary.tools_succeeded:
                summary.tools_succeeded.append(tool_name)
        summary.tool_versions.update(sr.tool_versions)

    # Build ScanFinding records
    scan_findings = [
        issue_to_scan_finding(issue, entry.id, entry.snapshot.pinned_commit)
        for sr in scan_results
        for issue in sr.issues
    ]

    # Save per-project results
    # Full audit trail (list of ScanResult JSON)
    full_results = [sr.model_dump(mode="json") for sr in scan_results]
    (result_dir / "scan_results.json").write_text(
        json.dumps(full_results, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    # Summary
    (result_dir / "summary.json").write_text(
        json.dumps(summary.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Per-finding JSONL (one finding per line)
    with open(result_dir / "findings.jsonl", "w", encoding="utf-8") as f:
        for finding in scan_findings:
            f.write(finding.model_dump_json() + "\n")

    # Update catalog entry
    entry.scan = ProjectScanSummary(
        status="success" if scan_findings else "no_issues",
        findings=summary,
    )
    entry.status = ProjectStatus.SCANNED

    print(
        f"  [{entry.id}] ✓ {summary.total_issues} issues "
        f"({summary.high_confidence} high-conf) in {duration:.1f}s"
    )
    return entry, scan_findings


def load_catalog(path: Path) -> tuple[list[ProjectEntry], dict[str, Any]]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            print(f"Warning: could not parse {raw.get('id', '?')}: {e}", file=sys.stderr)
    return entries, data.get("metadata", {})


def save_catalog(entries: list[ProjectEntry], path: Path, metadata: dict[str, Any]) -> None:
    output: dict[str, Any] = {
        "projects": [e.to_catalog_dict() for e in entries],
        "metadata": {
            **metadata,
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
        },
    }
    with open(path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


async def main_async(args: argparse.Namespace) -> None:
    print("\n♿ a11y-autofix Dataset Scanner\n" + "═" * 50)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    entries, metadata = load_catalog(args.catalog)
    print(f"  Loaded {len(entries)} projects from catalog")

    targets = (
        [e for e in entries if e.id == args.project]
        if args.project
        else [e for e in entries if e.status == ProjectStatus.SNAPSHOTTED or
              (args.force and e.status == ProjectStatus.SCANNED)]
    )

    if not targets:
        print("  No projects to scan (run snapshot.py first).")
        return

    print(f"  Targets: {len(targets)} projects\n")

    entry_index = {e.id: e for e in entries}
    all_findings: list[ScanFinding] = []
    sem = asyncio.Semaphore(args.workers)

    async def scan_with_sem(entry: ProjectEntry) -> tuple[ProjectEntry, list[ScanFinding]]:
        async with sem:
            return await scan_project(
                entry,
                scan_timeout=args.timeout,
                min_consensus=1,
                force=args.force,
            )

    results = await asyncio.gather(*[scan_with_sem(e) for e in targets], return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            print(f"  Scan failed: {result}", file=sys.stderr)
            continue
        updated_entry, findings = result
        entry_index[updated_entry.id] = updated_entry
        all_findings.extend(findings)

    # Write consolidated dataset findings JSONL
    consolidated_path = RESULTS_DIR / "dataset_findings.jsonl"
    mode = "a" if consolidated_path.exists() and not args.force else "w"
    with open(consolidated_path, mode, encoding="utf-8") as f:
        for finding in all_findings:
            f.write(finding.model_dump_json() + "\n")

    # Write dataset statistics
    total_issues = sum(
        e.scan.findings.total_issues for e in entry_index.values()
        if e.status == ProjectStatus.SCANNED
    )
    high_conf = sum(
        e.scan.findings.high_confidence for e in entry_index.values()
        if e.status == ProjectStatus.SCANNED
    )
    stats = {
        "total_projects_scanned": sum(
            1 for e in entry_index.values() if e.status == ProjectStatus.SCANNED
        ),
        "total_issues": total_issues,
        "high_confidence_issues": high_conf,
        "low_confidence_issues": total_issues - high_conf,
        "by_type": {},
        "by_principle": {},
        "last_updated": datetime.now(tz=timezone.utc).isoformat(),
    }

    for entry in entry_index.values():
        if entry.status != ProjectStatus.SCANNED:
            continue
        for k, v in entry.scan.findings.by_type.items():
            stats["by_type"][k] = stats["by_type"].get(k, 0) + v
        for k, v in entry.scan.findings.by_principle.items():
            stats["by_principle"][k] = stats["by_principle"].get(k, 0) + v

    (RESULTS_DIR / "dataset_stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    save_catalog(list(entry_index.values()), args.catalog, metadata)

    scanned = sum(1 for e in entry_index.values() if e.status == ProjectStatus.SCANNED)
    print(f"\n{'═' * 50}")
    print(f"  Scanned: {scanned} projects  |  Total findings: {len(all_findings)}")
    print(f"  High-confidence: {high_conf} ({high_conf/max(total_issues,1)*100:.1f}%)")
    print(f"  Consolidated findings: {consolidated_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan snapshotted projects for accessibility violations"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--project", default=None, help="Scan only this project ID")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent projects (default 1)")
    parser.add_argument("--timeout", type=int, default=90, help="Per-file tool timeout (seconds)")
    parser.add_argument("--force", action="store_true", help="Re-scan already scanned projects")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
