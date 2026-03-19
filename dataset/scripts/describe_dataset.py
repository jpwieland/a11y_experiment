#!/usr/bin/env python3
"""
Dataset profile descriptor for the a11y-autofix benchmark corpus.

Computes a comprehensive dataset profile and writes it to
  dataset/results/dataset_profile.json

The profile covers:
  - Corpus size and status breakdown
  - Files per repository (median, p25, p75)
  - Lines of code per file (median, if available)
  - Components per repository (median)
  - Violations distribution (by issue type, WCAG principle, impact)
  - Domain distribution (QM3 stratum balance assertion)
  - Popularity tier distribution
  - Size category distribution

Usage:
    python dataset/scripts/describe_dataset.py
    python dataset/scripts/describe_dataset.py --catalog dataset/catalog/projects.yaml
    python dataset/scripts/describe_dataset.py --json   # suppress human-readable table
    python dataset/scripts/describe_dataset.py --assert-qm3  # exit 1 if stratum > 20%

References:
    Methodology: Section 3.5.1 (Corpus Description) and 3.5.5 (QM3 Stratum Balance)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    AnnotationLabel,
    ProjectEntry,
    ProjectStatus,
)
from dataset.scripts.annotate import load_ground_truth


DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

# Methodology QM3 threshold (Section 3.5.5)
_QM3_MAX_STRATUM_FRACTION = 0.20


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _percentile(data: list[float], p: float) -> float:
    """Return the p-th percentile of data (0 ≤ p ≤ 100)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return round(sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (k - lo), 1)


def _freq_map(values: list[str]) -> dict[str, int]:
    """Count occurrences of each value."""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


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
# Profile computation
# ──────────────────────────────────────────────────────────────────────────────


def compute_dataset_profile(
    entries: list[ProjectEntry],
    assert_qm3: bool = False,
) -> dict[str, Any]:
    """
    Compute a structured dataset profile dict.

    Args:
        entries: All catalog entries.
        assert_qm3: If True, raise AssertionError if any domain stratum > 20%.

    Returns:
        Profile dict ready for JSON serialisation.
    """
    _INCLUDED_STATUSES = (
        ProjectStatus.SNAPSHOTTED,
        ProjectStatus.SCANNED,
        ProjectStatus.ANNOTATED,
    )

    all_entries = entries
    included = [e for e in entries if e.status in _INCLUDED_STATUSES]
    annotated = [e for e in entries if e.status == ProjectStatus.ANNOTATED]
    excluded = [e for e in entries if e.status == ProjectStatus.EXCLUDED]

    # ── Corpus size ────────────────────────────────────────────────────────────
    corpus_size = {
        "total_discovered": len(all_entries),
        "total_included": len(included),
        "total_annotated": len(annotated),
        "total_excluded": len(excluded),
        "by_status": _freq_map([e.status.value for e in all_entries]),
    }

    # ── Files per repository ───────────────────────────────────────────────────
    file_counts = [
        e.snapshot.component_file_count
        for e in included
        if e.snapshot and e.snapshot.component_file_count > 0
    ]
    files_per_repo = {
        "median": _percentile(file_counts, 50) if file_counts else 0,
        "p25": _percentile(file_counts, 25) if file_counts else 0,
        "p75": _percentile(file_counts, 75) if file_counts else 0,
        "min": min(file_counts, default=0),
        "max": max(file_counts, default=0),
        "n": len(file_counts),
    }

    # ── Lines of code (best-effort from snapshot clone size) ──────────────────
    # clone_size_mb is available; LOC per file is not directly tracked,
    # so we report clone size statistics as a proxy.
    clone_sizes = [
        e.snapshot.clone_size_mb
        for e in included
        if e.snapshot and e.snapshot.clone_size_mb > 0
    ]
    clone_size_mb = {
        "median": _percentile(clone_sizes, 50) if clone_sizes else 0,
        "p25": _percentile(clone_sizes, 25) if clone_sizes else 0,
        "p75": _percentile(clone_sizes, 75) if clone_sizes else 0,
        "n": len(clone_sizes),
    }

    # ── Issue type / WCAG / impact distributions ───────────────────────────────
    all_issue_types: list[str] = []
    all_wcag_principles: list[str] = []
    all_impacts: list[str] = []
    all_wcag_criteria: list[str] = []
    total_confirmed = 0
    total_fp = 0
    total_uncertain = 0
    total_auto_accepted = 0
    total_human_annotated = 0

    wcag_principle_map = {
        "1": "perceivable",
        "2": "operable",
        "3": "understandable",
        "4": "robust",
    }

    for e in annotated:
        records = load_ground_truth(e.id)
        for gt in records.values():
            if gt.ground_truth_label == AnnotationLabel.CONFIRMED:
                total_confirmed += 1
                if gt.issue_type:
                    all_issue_types.append(gt.issue_type)
                if gt.wcag_criteria:
                    p = wcag_principle_map.get(gt.wcag_criteria.split(".")[0], "unknown")
                    all_wcag_principles.append(p)
                    crit = ".".join(gt.wcag_criteria.split(".")[:2])
                    all_wcag_criteria.append(crit)
                if gt.impact:
                    all_impacts.append(gt.impact)
            elif gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE:
                total_fp += 1
            elif gt.ground_truth_label == AnnotationLabel.UNCERTAIN:
                total_uncertain += 1

            if gt.auto_accepted:
                total_auto_accepted += 1
            else:
                total_human_annotated += 1

    violations_distribution = {
        "total_confirmed": total_confirmed,
        "total_false_positives": total_fp,
        "total_uncertain": total_uncertain,
        "fp_rate": round(total_fp / max(total_confirmed + total_fp, 1), 4),
        "auto_accepted": total_auto_accepted,
        "human_annotated": total_human_annotated,
        "by_issue_type": _freq_map(all_issue_types),
        "by_wcag_principle": _freq_map(all_wcag_principles),
        "by_impact": _freq_map(all_impacts),
        "top_wcag_criteria": dict(
            list(_freq_map(all_wcag_criteria).items())[:10]
        ),
    }

    # ── Domain distribution (QM3) ──────────────────────────────────────────────
    total_included = len(included)
    domain_counts = _freq_map(
        [e.domain.value if hasattr(e.domain, "value") else str(e.domain)
         for e in included]
    )
    domain_fractions = {
        d: round(c / max(total_included, 1), 4)
        for d, c in domain_counts.items()
    }
    max_fraction = max(domain_fractions.values(), default=0.0)
    max_domain = max(domain_fractions, key=domain_fractions.get, default="")  # type: ignore[arg-type]

    qm3_assertion = {
        "max_stratum": max_domain,
        "max_fraction": max_fraction,
        "threshold": _QM3_MAX_STRATUM_FRACTION,
        "passed": max_fraction <= _QM3_MAX_STRATUM_FRACTION,
    }

    if assert_qm3 and not qm3_assertion["passed"]:
        raise AssertionError(
            f"QM3 violated: domain '{max_domain}' = {max_fraction:.1%} "
            f"> {_QM3_MAX_STRATUM_FRACTION:.0%} threshold "
            f"(methodology Section 3.5.5)"
        )

    domain_distribution = {
        "counts": domain_counts,
        "fractions": domain_fractions,
        "qm3_assertion": qm3_assertion,
    }

    # ── Popularity tier distribution ───────────────────────────────────────────
    popularity_distribution = _freq_map(
        [e.popularity_tier.value if hasattr(e.popularity_tier, "value")
         else str(e.popularity_tier)
         for e in included]
    )

    # ── Size category distribution ─────────────────────────────────────────────
    size_distribution = _freq_map(
        [e.size_category.value if hasattr(e.size_category, "value")
         else str(e.size_category)
         for e in included]
    )

    # ── TypeScript / React versions ────────────────────────────────────────────
    ts_versions = _freq_map(
        [e.snapshot.typescript_version for e in included
         if e.snapshot and e.snapshot.typescript_version]
    )
    react_versions = _freq_map(
        [e.snapshot.react_version for e in included
         if e.snapshot and e.snapshot.react_version]
    )

    profile = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "methodology_reference": "Section 3.5.1 (Corpus Description)",
        "corpus_size": corpus_size,
        "files_per_repo": files_per_repo,
        "clone_size_mb": clone_size_mb,
        "violations_distribution": violations_distribution,
        "domain_distribution": domain_distribution,
        "popularity_distribution": popularity_distribution,
        "size_distribution": size_distribution,
        "typescript_versions": ts_versions,
        "react_versions": react_versions,
    }

    return profile


# ──────────────────────────────────────────────────────────────────────────────
# Human-readable printer
# ──────────────────────────────────────────────────────────────────────────────


def print_profile(profile: dict[str, Any]) -> None:
    width = 62
    print(f"\n{'═' * width}")
    print(f"  Dataset Profile Report")
    print(f"  Generated: {profile['generated_at']}")
    print(f"{'═' * width}\n")

    cs = profile["corpus_size"]
    print(f"  Corpus size")
    print(f"    Discovered  : {cs['total_discovered']}")
    print(f"    Included    : {cs['total_included']}")
    print(f"    Annotated   : {cs['total_annotated']}")
    print(f"    Excluded    : {cs['total_excluded']}")

    fr = profile["files_per_repo"]
    print(f"\n  Files per repository")
    print(f"    Median      : {fr['median']}")
    print(f"    P25 / P75   : {fr['p25']} / {fr['p75']}")

    vd = profile["violations_distribution"]
    print(f"\n  Violations (confirmed findings)")
    print(f"    Total       : {vd['total_confirmed']}")
    print(f"    Auto-accepted  : {vd['auto_accepted']}")
    print(f"    Human-annotated: {vd['human_annotated']}")
    print(f"    FP rate     : {vd['fp_rate']:.1%}")
    if vd["by_issue_type"]:
        print(f"    By type     : " + ", ".join(
            f"{k}={v}" for k, v in list(vd["by_issue_type"].items())[:5]
        ))
    if vd["by_wcag_principle"]:
        print(f"    By principle: " + ", ".join(
            f"{k}={v}" for k, v in vd["by_wcag_principle"].items()
        ))

    dd = profile["domain_distribution"]
    qm3 = dd["qm3_assertion"]
    print(f"\n  Domain distribution  (QM3: max ≤ 20%)")
    for d, c in dd["counts"].items():
        frac = dd["fractions"][d]
        flag = " ← MAX" if d == qm3["max_stratum"] else ""
        print(f"    {d:<20} {c:>4}  ({frac:.0%}){flag}")
    qm3_status = "✓ PASS" if qm3["passed"] else "✗ FAIL"
    print(f"  QM3 assertion: {qm3_status}  (max={qm3['max_fraction']:.1%})")

    print(f"\n  Popularity tiers")
    for tier, c in profile["popularity_distribution"].items():
        print(f"    {tier:<20} {c:>4}")

    print(f"\n  Size categories")
    for size, c in profile["size_distribution"].items():
        print(f"    {size:<20} {c:>4}")

    print(f"\n{'═' * width}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute and save a structured dataset profile"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=None,
                        help="Override output path (default: results/dataset_profile.json)")
    parser.add_argument("--json", action="store_true",
                        help="Print JSON to stdout instead of human-readable table")
    parser.add_argument("--assert-qm3", action="store_true",
                        help="Exit 1 if any domain stratum exceeds 20% (QM3 check)")
    args = parser.parse_args()

    print("\n♿ a11y-autofix Dataset Describer\n" + "═" * 50)

    entries, _ = load_catalog(args.catalog)
    print(f"  Loaded {len(entries)} projects")

    try:
        profile = compute_dataset_profile(entries, assert_qm3=args.assert_qm3)
    except AssertionError as exc:
        print(f"\n[QM3 ASSERTION FAILED] {exc}", file=sys.stderr)
        sys.exit(1)

    # Persist to results/
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.output or (RESULTS_DIR / "dataset_profile.json")
    out_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"  Profile written to {out_path}")

    if args.json:
        print(json.dumps(profile, indent=2))
    else:
        print_profile(profile)


if __name__ == "__main__":
    main()
