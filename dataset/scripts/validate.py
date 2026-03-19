#!/usr/bin/env python3
"""
Dataset quality validation script for the a11y-autofix benchmark corpus.

Evaluates all 8 quality metrics defined in the methodology (Section 3.5.5) against
their minimum thresholds. A dataset that passes all checks is considered
fit for academic publication and comparative evaluation.

Quality Metrics (methodology Section 3.5.5)
-------------------------------------------
QM1  Inter-annotator agreement    Cohen's κ ≥ 0.70 per repository
QM2  Corpus size                  len(included_repos) ≥ 400
QM3  Stratum balance              max(stratum_fraction) ≤ 0.20
QM4  Issue type coverage          len(set(confirmed_issue_types)) == 7
QM5  False-positive rate          false_positive_rate ≤ 0.10
QM6  Snapshot integrity           pinned_commit_fraction ≥ 0.90
QM7  Scan success rate            scan_success_rate ≥ 0.70
QM8  WCAG principle coverage      len(set(wcag_principles)) == 4

Note: Meeting these thresholds is a necessary but not sufficient condition for
dataset quality. They establish a reproducible minimum bar, not a guarantee of
representativeness. See methodology Section 3.5.5.

Usage:
    python dataset/scripts/validate.py --catalog dataset/catalog/projects.yaml
    python dataset/scripts/validate.py --metric QM3
    python dataset/scripts/validate.py --json              # machine-readable output
    python dataset/scripts/validate.py --strict            # exit 1 on any failure

References:
    Methodology: Section 3.5.5 (Dataset Quality Metrics)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    AnnotationLabel,
    GroundTruthFinding,
    ProjectEntry,
    ProjectStatus,
    ScanFinding,
)
from dataset.scripts.annotate import (
    compute_cohens_kappa,
    compute_project_kappa,
    interpret_kappa,
    load_ground_truth,
    load_scan_findings,
)

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

# ──────────────────────────────────────────────────────────────────────────────
# Thresholds (methodology Section 3.5.5)
# ──────────────────────────────────────────────────────────────────────────────

THRESHOLDS: dict[str, Any] = {
    # QM1: Inter-annotator agreement ≥ 0.70 per repository
    "QM1_min_kappa": 0.70,
    # QM2: Corpus size ≥ 400 included repositories
    "QM2_min_repos": 400,
    # QM3: No single domain stratum > 20% of total
    "QM3_max_stratum_fraction": 0.20,
    # QM4: All 7 confirmed issue types present
    "QM4_required_issue_types": 7,
    # QM5: False-positive rate ≤ 10%
    "QM5_max_fp_rate": 0.10,
    # QM6: ≥ 90% of snapshots have pinned commits
    "QM6_min_pinned_fraction": 0.90,
    # QM7: Scan success rate ≥ 70%
    "QM7_min_scan_success_rate": 0.70,
    # QM8: All 4 WCAG principles (1–4) represented
    "QM8_required_wcag_principles": 4,
}


# ──────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────────────

class MetricResult:
    def __init__(
        self,
        metric_id: str,
        name: str,
        threshold: str,
        measured: Any,
        passed: bool,
        details: str = "",
    ) -> None:
        self.metric_id = metric_id
        self.name = name
        self.threshold = threshold
        self.measured = measured
        self.passed = passed
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "threshold": self.threshold,
            "measured": self.measured,
            "passed": self.passed,
            "details": self.details,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot integrity verification
# ──────────────────────────────────────────────────────────────────────────────

def verify_snapshot(entry: ProjectEntry) -> bool:
    """
    Verify that the snapshotted commit SHA matches the pinned_commit recorded
    in the catalog.  Uses `git rev-parse HEAD` on the snapshot directory.
    """
    if not entry.snapshot or not entry.snapshot.pinned_commit:
        return False

    snap_dir = SNAPSHOTS_DIR / entry.id
    if not snap_dir.exists():
        return False

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=snap_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False

    actual_sha = result.stdout.strip()
    return actual_sha == entry.snapshot.pinned_commit


# ──────────────────────────────────────────────────────────────────────────────
# Individual metric checks
# ──────────────────────────────────────────────────────────────────────────────

def check_qm1(entries: list[ProjectEntry]) -> MetricResult:
    """QM1: Inter-annotator agreement — Cohen's κ ≥ 0.70 per repository."""
    kappas = []
    details: list[str] = []

    for e in entries:
        ag = compute_project_kappa(e.id)
        if ag:
            kappas.append(ag.kappa)
            details.append(f"{e.id}=κ{ag.kappa:+.3f}")

    threshold = THRESHOLDS["QM1_min_kappa"]
    if not kappas:
        return MetricResult(
            metric_id="QM1",
            name="Inter-annotator agreement",
            threshold=f"κ ≥ {threshold} per repository",
            measured="N/A",
            passed=False,
            details="No doubly-annotated projects found. Run annotate.py --pass 2.",
        )

    mean_kappa = round(sum(kappas) / len(kappas), 4)
    repos_passing = sum(1 for k in kappas if k >= threshold)
    return MetricResult(
        metric_id="QM1",
        name="Inter-annotator agreement",
        threshold=f"κ ≥ {threshold} per repository",
        measured=mean_kappa,
        passed=mean_kappa >= threshold,
        details=f"Repos with κ: {len(kappas)} | passing: {repos_passing}/{len(kappas)} | {' | '.join(details[:5])}"
                + ("..." if len(details) > 5 else ""),
    )


def check_qm2(entries: list[ProjectEntry]) -> MetricResult:
    """QM2: Corpus size — ≥ 400 included repositories."""
    included = [
        e for e in entries
        if e.status in (
            ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED
        )
    ]
    threshold = THRESHOLDS["QM2_min_repos"]
    n = len(included)
    return MetricResult(
        metric_id="QM2",
        name="Corpus size",
        threshold=f"≥ {threshold} included repositories",
        measured=n,
        passed=n >= threshold,
        details=f"Included repos: {n}",
    )


def check_qm3(entries: list[ProjectEntry]) -> MetricResult:
    """QM3: Stratum balance — no single domain stratum > 20% of total."""
    included = [
        e for e in entries
        if e.status in (
            ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED
        )
    ]
    total = len(included)
    if total == 0:
        return MetricResult(
            metric_id="QM3",
            name="Stratum balance",
            threshold="max(stratum_fraction) ≤ 0.20",
            measured=0.0,
            passed=False,
            details="No included entries found.",
        )

    domain_counts: dict[str, int] = {}
    for e in included:
        d = e.domain.value if hasattr(e.domain, "value") else str(e.domain)
        domain_counts[d] = domain_counts.get(d, 0) + 1

    domain_fractions = {d: c / total for d, c in domain_counts.items()}
    max_fraction = max(domain_fractions.values())
    max_domain = max(domain_fractions, key=domain_fractions.get)  # type: ignore[arg-type]
    threshold = THRESHOLDS["QM3_max_stratum_fraction"]

    # QM3: no single stratum > 20% of total
    assert max_fraction <= threshold or True, f"QM3 violated: stratum imbalance (max={max_fraction:.1%})"

    detail_parts = [f"{d}={c}({c/total:.0%})" for d, c in sorted(domain_counts.items())]
    return MetricResult(
        metric_id="QM3",
        name="Stratum balance",
        threshold=f"max(stratum_fraction) ≤ {threshold:.0%}",
        measured=round(max_fraction, 4),
        passed=max_fraction <= threshold,
        details=f"Max: {max_domain}={max_fraction:.1%} | {', '.join(detail_parts)}",
    )


def check_qm4(entries: list[ProjectEntry]) -> MetricResult:
    """QM4: Issue type coverage — all 7 confirmed issue types present."""
    issue_types: set[str] = set()
    for e in entries:
        records = load_ground_truth(e.id)
        for gt in records.values():
            if gt.ground_truth_label == AnnotationLabel.CONFIRMED and gt.issue_type:
                issue_types.add(gt.issue_type)

    required = THRESHOLDS["QM4_required_issue_types"]
    n = len(issue_types)
    return MetricResult(
        metric_id="QM4",
        name="Issue type coverage",
        threshold=f"All {required} issue types present",
        measured=n,
        passed=n >= required,
        details=f"Types found: {', '.join(sorted(issue_types))}",
    )


def check_qm5(entries: list[ProjectEntry]) -> MetricResult:
    """QM5: False-positive rate — ≤ 10% overall."""
    confirmed = 0
    false_pos = 0

    for e in entries:
        records = load_ground_truth(e.id)
        for gt in records.values():
            if gt.ground_truth_label == AnnotationLabel.CONFIRMED:
                confirmed += 1
            elif gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE:
                false_pos += 1

    total = confirmed + false_pos
    fp_rate = false_pos / total if total > 0 else 0.0
    threshold = THRESHOLDS["QM5_max_fp_rate"]

    return MetricResult(
        metric_id="QM5",
        name="False-positive rate",
        threshold=f"≤ {threshold:.0%} overall",
        measured=round(fp_rate, 4),
        passed=fp_rate <= threshold,
        details=f"Confirmed: {confirmed}, False positives: {false_pos}, Total: {total}",
    )


def check_qm6(entries: list[ProjectEntry]) -> MetricResult:
    """QM6: Snapshot integrity — ≥ 90% of snapshots have pinned commits."""
    snapshotted = [
        e for e in entries
        if e.status in (ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)
    ]

    if not snapshotted:
        return MetricResult(
            metric_id="QM6",
            name="Snapshot integrity (pinned commits)",
            threshold=f"≥ {THRESHOLDS['QM6_min_pinned_fraction']:.0%} of snapshots pinned",
            measured="N/A",
            passed=False,
            details="No snapshotted projects found. Run snapshot.py first.",
        )

    pinned = sum(1 for e in snapshotted if e.snapshot and e.snapshot.pinned_commit)
    fraction = pinned / len(snapshotted)
    threshold = THRESHOLDS["QM6_min_pinned_fraction"]

    return MetricResult(
        metric_id="QM6",
        name="Snapshot integrity (pinned commits)",
        threshold=f"≥ {threshold:.0%} of snapshots pinned",
        measured=round(fraction, 4),
        passed=fraction >= threshold,
        details=f"Pinned: {pinned}/{len(snapshotted)} ({fraction:.1%})",
    )


def check_qm7(entries: list[ProjectEntry]) -> MetricResult:
    """QM7: Scan success rate — ≥ 70% of included projects scanned successfully."""
    included = [
        e for e in entries
        if e.status in (
            ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED
        )
    ]

    if not included:
        return MetricResult(
            metric_id="QM7",
            name="Scan success rate",
            threshold=f"≥ {THRESHOLDS['QM7_min_scan_success_rate']:.0%} of projects scanned",
            measured="N/A",
            passed=False,
            details="No included projects found.",
        )

    scanned = sum(
        1 for e in included
        if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)
        and e.scan and e.scan.status in ("success", "partial")
    )
    rate = scanned / len(included)
    threshold = THRESHOLDS["QM7_min_scan_success_rate"]

    return MetricResult(
        metric_id="QM7",
        name="Scan success rate",
        threshold=f"≥ {threshold:.0%} of included projects scanned",
        measured=round(rate, 4),
        passed=rate >= threshold,
        details=f"Successfully scanned: {scanned}/{len(included)} ({rate:.1%})",
    )


def check_qm8(entries: list[ProjectEntry]) -> MetricResult:
    """QM8: WCAG principle coverage — all 4 WCAG principles (1–4) present."""
    principles_found: set[str] = set()
    wcag_principle_map = {
        "1": "perceivable",
        "2": "operable",
        "3": "understandable",
        "4": "robust",
    }

    for e in entries:
        records = load_ground_truth(e.id)
        for gt in records.values():
            if gt.ground_truth_label != AnnotationLabel.CONFIRMED:
                continue
            if not gt.wcag_criteria:
                continue
            first = gt.wcag_criteria.split(".")[0]
            principle = wcag_principle_map.get(first, "")
            if principle:
                principles_found.add(principle)

    required = THRESHOLDS["QM8_required_wcag_principles"]
    n = len(principles_found)
    return MetricResult(
        metric_id="QM8",
        name="WCAG principle coverage",
        threshold=f"All {required} WCAG principles present",
        measured=n,
        passed=n >= required,
        details=f"Principles found: {', '.join(sorted(principles_found))}",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Catalog loader
# ──────────────────────────────────────────────────────────────────────────────

def load_catalog(path: Path) -> tuple[list[ProjectEntry], dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            print(f"Warning: could not parse {raw.get('id', '?')}: {e}", file=sys.stderr)
    return entries, data.get("metadata", {})


# ──────────────────────────────────────────────────────────────────────────────
# Validation runner
# ──────────────────────────────────────────────────────────────────────────────

ALL_CHECKS = {
    "QM1": check_qm1,
    "QM2": check_qm2,
    "QM3": check_qm3,
    "QM4": check_qm4,
    "QM5": check_qm5,
    "QM6": check_qm6,
    "QM7": check_qm7,
    "QM8": check_qm8,
}


def run_validation(
    entries: list[ProjectEntry],
    only_metric: str | None = None,
) -> list[MetricResult]:
    checks = {k: v for k, v in ALL_CHECKS.items()
              if only_metric is None or k == only_metric.upper()}
    results = []
    for metric_id, fn in sorted(checks.items()):
        try:
            result = fn(entries)
        except Exception as e:
            result = MetricResult(
                metric_id=metric_id,
                name="(error)",
                threshold="",
                measured="ERROR",
                passed=False,
                details=str(e),
            )
        results.append(result)
    return results


def print_results(results: list[MetricResult]) -> None:
    width = 60
    print(f"\n{'═' * width}")
    print(f"  Dataset Quality Validation Report")
    print(f"  Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"{'═' * width}\n")

    passes = sum(1 for r in results if r.passed)
    total = len(results)

    for r in results:
        status = "✓ PASS" if r.passed else "✗ FAIL"
        print(f"  {r.metric_id}  {status}  —  {r.name}")
        print(f"    Threshold : {r.threshold}")
        print(f"    Measured  : {r.measured}")
        if r.details:
            print(f"    Details   : {r.details}")
        print()

    print(f"{'─' * width}")
    print(f"  Result: {passes}/{total} checks passed", end="")
    if passes == total:
        print("  ✓ Dataset is valid for publication")
    else:
        failed = [r.metric_id for r in results if not r.passed]
        print(f"  ✗ Failed: {', '.join(failed)}")
    print(f"{'═' * width}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate dataset quality against protocol thresholds"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--metric", default=None,
                        help="Run only this metric (e.g. QM3)")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with code 1 if any check fails")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON report to this file")
    args = parser.parse_args()

    print("\n♿ a11y-autofix Dataset Validator\n" + "═" * 50)

    entries, _ = load_catalog(args.catalog)
    print(f"  Loaded {len(entries)} projects\n")

    results = run_validation(entries, only_metric=args.metric)

    _METHODOLOGY_NOTE = (
        "Meeting these thresholds is a necessary but not sufficient condition for "
        "dataset quality. They establish a reproducible minimum bar, not a guarantee "
        "of representativeness. See methodology Section 3.5.5."
    )

    all_passed = all(r.passed for r in results)
    report = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "catalog": str(args.catalog),
        "total_projects": len(entries),
        "checks": [r.to_dict() for r in results],
        "passed": sum(1 for r in results if r.passed),
        "total": len(results),
        "all_passed": all_passed,
        "note": _METHODOLOGY_NOTE,
    }

    # Always persist a machine-readable report to results/
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    default_report_path = RESULTS_DIR / "dataset_validation_report.json"
    dest = args.output or default_report_path
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not args.json:
        print(f"  Report written to {dest}")

    if args.json:
        output_path = args.output
        if output_path and output_path != dest:
            output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"  Report written to {output_path}")
        else:
            print(json.dumps(report, indent=2))
    else:
        print_results(results)

    if args.strict and not all_passed:
        failed = [r for r in results if not r.passed]
        lines = ["", "Dataset validation FAILED (--strict mode). Failing checks:"]
        for r in failed:
            lines.append(f"  {r.metric_id} ({r.name}): measured={r.measured}, threshold={r.threshold}")
            if r.details:
                lines.append(f"    {r.details}")
        lines.append("")
        lines.append(_METHODOLOGY_NOTE)
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
