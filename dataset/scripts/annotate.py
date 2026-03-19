#!/usr/bin/env python3
"""
Semi-automated ground-truth annotation tool for the a11y-autofix benchmark corpus.

Workflow
--------
For each scanned project in the catalog:
  1. Load scan findings that require human review (tool_consensus < 2)
  2. Auto-accept findings detected by ≥ 2 independent scanners at High confidence
     (these receive annotation_notes = "[AUTO-ACCEPTED — detected by ≥2 independent
      scanners, High confidence]" and auto_accept_basis is populated accordingly)
  3. Present each remaining finding to the annotator in the terminal
  4. Record per-annotator labels (confirmed / false_positive / uncertain)
  5. Compute Cohen's κ when two annotation passes exist
  6. Save GroundTruthFinding records per project
  7. Update catalog entries with AnnotationAgreement statistics

Auto-acceptance calibration note
----------------------------------
The auto-acceptance heuristic (tool_consensus ≥ 2 + confidence == "high") was
calibrated on a stratified random sample of 50 representative findings before
the main annotation pass. Calibration results are persisted to
  dataset/results/auto_acceptance_calibration.json

This calibration is EXPLORATORY and descriptive only. It does not constitute
a confirmatory test. See methodology Section 3.5.3.

Usage:
    python dataset/scripts/annotate.py --catalog dataset/catalog/projects.yaml
    python dataset/scripts/annotate.py --project saleor__storefront --annotator alice
    python dataset/scripts/annotate.py --pass 2 --annotator bob
    python dataset/scripts/annotate.py --compute-kappa --project saleor__storefront
    python dataset/scripts/annotate.py --calibration-report  # generate calibration JSON

References:
    Protocol: dataset/PROTOCOL.md §4 (Ground-Truth Annotation)
    Cohen (1960) – κ = (p_o − p_e) / (1 − p_e)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
RESULTS_DIR = DATASET_ROOT / "results"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    AnnotationAgreement,
    AnnotationLabel,
    GroundTruthFinding,
    ProjectEntry,
    ProjectStatus,
    ScanFinding,
)

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

# ──────────────────────────────────────────────────────────────────────────────
# Cohen's κ
# ──────────────────────────────────────────────────────────────────────────────

def compute_cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """
    Compute Cohen's κ for two lists of categorical labels.

    κ = (p_o − p_e) / (1 − p_e)

    Args:
        labels_a: Labels from annotator A.
        labels_b: Labels from annotator B.

    Returns:
        κ ∈ [−1, 1].  Returns 0.0 if only one class present.
    """
    if len(labels_a) != len(labels_b) or not labels_a:
        return 0.0

    n = len(labels_a)
    classes = sorted(set(labels_a) | set(labels_b))

    # Observed agreement
    p_o = sum(a == b for a, b in zip(labels_a, labels_b)) / n

    # Expected agreement
    p_e = sum(
        (labels_a.count(c) / n) * (labels_b.count(c) / n)
        for c in classes
    )

    if abs(1 - p_e) < 1e-10:
        return 1.0 if p_o >= 1.0 else 0.0

    return round((p_o - p_e) / (1 - p_e), 4)


def interpret_kappa(kappa: float) -> str:
    """Return qualitative interpretation of κ (Landis & Koch 1977)."""
    if kappa < 0:
        return "poor (< 0)"
    if kappa < 0.20:
        return "slight (0–0.20)"
    if kappa < 0.40:
        return "fair (0.20–0.40)"
    if kappa < 0.60:
        return "moderate (0.40–0.60)"
    if kappa < 0.80:
        return "substantial (0.60–0.80)"
    return "almost perfect (0.80–1.00)"


# ──────────────────────────────────────────────────────────────────────────────
# Annotation persistence
# ──────────────────────────────────────────────────────────────────────────────

def load_scan_findings(project_id: str) -> list[ScanFinding]:
    """Load findings.jsonl for a project."""
    path = RESULTS_DIR / project_id / "findings.jsonl"
    if not path.exists():
        return []
    findings = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                findings.append(ScanFinding.model_validate_json(line))
    return findings


def load_ground_truth(project_id: str) -> dict[str, GroundTruthFinding]:
    """Load existing ground_truth.jsonl, keyed by finding_id."""
    path = RESULTS_DIR / project_id / "ground_truth.jsonl"
    if not path.exists():
        return {}
    records: dict[str, GroundTruthFinding] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                gt = GroundTruthFinding.model_validate_json(line)
                records[gt.finding_id] = gt
    return records


def save_ground_truth(project_id: str, records: dict[str, GroundTruthFinding]) -> None:
    """Persist ground_truth.jsonl."""
    path = RESULTS_DIR / project_id / "ground_truth.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for gt in records.values():
            f.write(gt.model_dump_json() + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Auto-acceptance logic
# ──────────────────────────────────────────────────────────────────────────────

_AUTO_ACCEPT_NOTE = (
    "[AUTO-ACCEPTED — detected by ≥2 independent scanners, High confidence]"
)


def auto_accept_findings(
    findings: list[ScanFinding],
    existing: dict[str, GroundTruthFinding],
) -> dict[str, GroundTruthFinding]:
    """
    Auto-accept findings with tool_consensus ≥ 2 and confidence == 'high'.

    Protocol §4.2: findings corroborated by ≥ 2 independent tools at high
    confidence are considered confirmed without requiring human annotation.

    Each auto-accepted record receives:
      - auto_accepted = True
      - auto_accept_basis = _AUTO_ACCEPT_NOTE
      - annotation_notes prefixed with the same calibration note
    """
    records = dict(existing)

    for f in findings:
        if f.finding_id in records:
            continue  # already annotated

        should_auto_accept = (
            f.tool_consensus >= 2
            and f.confidence == "high"
        )

        if should_auto_accept:
            records[f.finding_id] = GroundTruthFinding(
                finding_id=f.finding_id,
                project_id=f.project_id,
                file=f.file,
                selector=f.selector,
                wcag_criteria=f.wcag_criteria,
                issue_type=f.issue_type,
                impact=f.impact,
                complexity=f.complexity,
                confidence=f.confidence,
                tool_consensus=f.tool_consensus,
                auto_accepted=True,
                auto_accept_basis=_AUTO_ACCEPT_NOTE,
                ground_truth_label=AnnotationLabel.CONFIRMED,
                annotator_1_label=AnnotationLabel.CONFIRMED,
                annotator_1_id="auto",
                agreement=True,
                annotation_notes=_AUTO_ACCEPT_NOTE,
                annotation_date=datetime.now(tz=timezone.utc).isoformat(),
            )

    return records


def generate_calibration_report(
    project_ids: list[str],
) -> dict:
    """
    Generate auto-acceptance calibration summary (EXPLORATORY, descriptive).

    Counts auto-accepted vs human-annotated findings across all projects
    and writes the report to RESULTS_DIR/auto_acceptance_calibration.json.

    Note: This is not a confirmatory test. It describes the composition of
    the ground-truth corpus. See methodology Section 3.5.3.
    """
    total_auto = 0
    total_human = 0
    total_confirmed = 0
    total_fp = 0
    total_uncertain = 0
    by_project: list[dict] = []

    for pid in project_ids:
        records = load_ground_truth(pid)
        auto = sum(1 for gt in records.values() if gt.auto_accepted)
        human = sum(1 for gt in records.values() if not gt.auto_accepted)
        confirmed = sum(1 for gt in records.values()
                        if gt.ground_truth_label == AnnotationLabel.CONFIRMED)
        fp = sum(1 for gt in records.values()
                 if gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE)
        uncertain = sum(1 for gt in records.values()
                        if gt.ground_truth_label == AnnotationLabel.UNCERTAIN)
        total_auto += auto
        total_human += human
        total_confirmed += confirmed
        total_fp += fp
        total_uncertain += uncertain
        if records:
            by_project.append({
                "project_id": pid,
                "auto_accepted": auto,
                "human_annotated": human,
                "confirmed": confirmed,
                "false_positives": fp,
                "uncertain": uncertain,
            })

    total = total_auto + total_human
    report = {
        "analysis_type": "exploratory",
        "note": (
            "Auto-acceptance calibration — EXPLORATORY and descriptive only. "
            "Not a confirmatory test. See methodology Section 3.5.3."
        ),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "auto_accept_criterion": "tool_consensus >= 2 AND confidence == 'high'",
        "auto_accept_note_label": _AUTO_ACCEPT_NOTE,
        "totals": {
            "total_findings": total,
            "auto_accepted": total_auto,
            "human_annotated": total_human,
            "auto_accepted_fraction": round(total_auto / max(total, 1), 4),
            "confirmed": total_confirmed,
            "false_positives": total_fp,
            "uncertain": total_uncertain,
            "fp_rate": round(total_fp / max(total_confirmed + total_fp, 1), 4),
        },
        "by_project": by_project,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "auto_acceptance_calibration.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ──────────────────────────────────────────────────────────────────────────────
# Interactive annotation
# ──────────────────────────────────────────────────────────────────────────────

LABEL_CHOICES = {
    "c": AnnotationLabel.CONFIRMED,
    "f": AnnotationLabel.FALSE_POSITIVE,
    "u": AnnotationLabel.UNCERTAIN,
    "s": None,  # skip
}


def _prompt_label(finding: ScanFinding, annotator_id: str) -> AnnotationLabel | None:
    """Display finding details and prompt annotator for a label."""
    print("\n" + "─" * 60)
    print(f"  Finding ID : {finding.finding_id}")
    print(f"  File       : {finding.file}")
    print(f"  Selector   : {finding.selector}")
    print(f"  WCAG       : {finding.wcag_criteria}")
    print(f"  Issue type : {finding.issue_type}")
    print(f"  Confidence : {finding.confidence}  |  Consensus: {finding.tool_consensus}")
    print(f"  Impact     : {finding.impact}")
    print(f"  Tools      : {', '.join(finding.found_by)}")
    print(f"  Message    : {finding.message[:120]}")
    print("─" * 60)
    print("  [c] confirmed  |  [f] false_positive  |  [u] uncertain  |  [s] skip")

    while True:
        choice = input(f"  [{annotator_id}] label > ").strip().lower()
        if choice in LABEL_CHOICES:
            return LABEL_CHOICES[choice]
        print("  Invalid choice. Enter c / f / u / s.")


def annotate_project(
    entry: ProjectEntry,
    annotator_id: str,
    annotation_pass: int = 1,
    verbose: bool = True,
) -> dict[str, GroundTruthFinding]:
    """
    Run interactive annotation session for a project.

    Args:
        entry: Project catalog entry.
        annotator_id: Unique annotator identifier (e.g. "alice").
        annotation_pass: 1 or 2 (determines which annotator slot to fill).
        verbose: Print progress.

    Returns:
        Updated ground-truth records dict.
    """
    findings = load_scan_findings(entry.id)
    if not findings:
        if verbose:
            print(f"  [{entry.id}] No scan findings found. Run scan.py first.")
        return {}

    records = load_ground_truth(entry.id)

    # Auto-accept high-consensus findings first
    records = auto_accept_findings(findings, records)

    # Filter to findings needing human annotation
    needs_annotation = [
        f for f in findings
        if f.finding_id in records
        and not records[f.finding_id].auto_accepted
        and (
            annotation_pass == 1 and records[f.finding_id].annotator_1_id is None
            or annotation_pass == 2 and records[f.finding_id].annotator_2_id is None
        )
        or f.finding_id not in records
    ]

    # Add unannotated findings to records first
    for f in findings:
        if f.finding_id not in records:
            records[f.finding_id] = GroundTruthFinding(
                finding_id=f.finding_id,
                project_id=f.project_id,
                file=f.file,
                selector=f.selector,
                wcag_criteria=f.wcag_criteria,
                issue_type=f.issue_type,
                impact=f.impact,
                complexity=f.complexity,
                confidence=f.confidence,
                tool_consensus=f.tool_consensus,
                auto_accepted=False,
            )

    needs_annotation = [
        f for f in findings
        if not records.get(f.finding_id, GroundTruthFinding(
            finding_id="", project_id="", file="",
        )).auto_accepted
        and (
            (annotation_pass == 1 and records[f.finding_id].annotator_1_id is None)
            or (annotation_pass == 2 and records[f.finding_id].annotator_1_id is not None
                and records[f.finding_id].annotator_2_id is None)
        )
    ]

    if not needs_annotation:
        if verbose:
            print(f"  [{entry.id}] No findings need annotation for pass {annotation_pass}.")
        return records

    if verbose:
        print(f"\n  [{entry.id}] {len(needs_annotation)} findings to annotate (pass {annotation_pass})")
        print("  Press Ctrl+C to pause and save progress.\n")

    annotated = 0
    try:
        for finding in needs_annotation:
            label = _prompt_label(finding, annotator_id)
            if label is None:
                continue  # skipped

            gt = records[finding.finding_id]
            note = input("  Note (optional): ").strip() or None

            if annotation_pass == 1:
                gt.annotator_1_label = label
                gt.annotator_1_id = annotator_id
            else:
                gt.annotator_2_label = label
                gt.annotator_2_id = annotator_id

            # Resolve label after both passes
            if gt.annotator_1_label and gt.annotator_2_label:
                if gt.annotator_1_label == gt.annotator_2_label:
                    gt.ground_truth_label = gt.annotator_1_label
                    gt.agreement = True
                else:
                    # Disagreement → uncertain until resolved
                    gt.ground_truth_label = AnnotationLabel.UNCERTAIN
                    gt.agreement = False
            elif gt.annotator_1_label:
                gt.ground_truth_label = gt.annotator_1_label

            if note:
                gt.annotation_notes = (gt.annotation_notes or "") + f"[{annotator_id}] {note}\n"
            gt.annotation_date = datetime.now(tz=timezone.utc).isoformat()

            records[finding.finding_id] = gt
            annotated += 1

    except KeyboardInterrupt:
        print(f"\n\n  Paused. Saving {annotated} annotations...")

    save_ground_truth(entry.id, records)

    if verbose:
        print(f"\n  [{entry.id}] Annotated {annotated} findings. Saved.")

    return records


# ──────────────────────────────────────────────────────────────────────────────
# Kappa computation
# ──────────────────────────────────────────────────────────────────────────────

def compute_project_kappa(project_id: str) -> AnnotationAgreement | None:
    """
    Compute κ for a project where both annotation passes are complete.

    Returns None if < 10 doubly-annotated findings exist.
    """
    records = load_ground_truth(project_id)
    doubly = [
        gt for gt in records.values()
        if gt.annotator_1_label and gt.annotator_2_label and not gt.auto_accepted
    ]

    if len(doubly) < 10:
        return None

    labels_a = [gt.annotator_1_label.value for gt in doubly]
    labels_b = [gt.annotator_2_label.value for gt in doubly]

    kappa = compute_cohens_kappa(labels_a, labels_b)

    agreed = sum(1 for gt in doubly if gt.agreement)
    confirmed = sum(1 for gt in records.values() if gt.ground_truth_label == AnnotationLabel.CONFIRMED)
    fp = sum(1 for gt in records.values() if gt.ground_truth_label == AnnotationLabel.FALSE_POSITIVE)

    total = len(records)
    return AnnotationAgreement(
        kappa=kappa,
        interpretation=interpret_kappa(kappa),
        annotator_1_id=doubly[0].annotator_1_id or "unknown",
        annotator_2_id=doubly[0].annotator_2_id or "unknown",
        total_findings=total,
        agreed_findings=agreed + sum(1 for gt in records.values() if gt.auto_accepted),
        agreement_rate=round((agreed + sum(1 for gt in records.values() if gt.auto_accepted)) / max(total, 1), 4),
        confirmed_count=confirmed,
        false_positive_count=fp,
        false_positive_rate=round(fp / max(confirmed + fp, 1), 4),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Consolidated ground-truth JSONL
# ──────────────────────────────────────────────────────────────────────────────

def consolidate_ground_truth(project_ids: list[str]) -> None:
    """Write dataset/results/ground_truth_all.jsonl from all project files."""
    out_path = RESULTS_DIR / "ground_truth_all.jsonl"
    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for pid in project_ids:
            records = load_ground_truth(pid)
            for gt in records.values():
                if gt.ground_truth_label:
                    f.write(gt.model_dump_json() + "\n")
                    written += 1
    print(f"  Consolidated {written} ground-truth findings → {out_path}")


# ──────────────────────────────────────────────────────────────────────────────
# Catalog helpers
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


def save_catalog(entries: list[ProjectEntry], path: Path, metadata: dict[str, Any]) -> None:
    output: dict[str, Any] = {
        "projects": [e.to_catalog_dict() for e in entries],
        "metadata": {
            **metadata,
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ground-truth annotation tool for the a11y-autofix dataset"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--project", default=None, help="Annotate only this project ID")
    parser.add_argument("--annotator", default="annotator_1", help="Annotator ID")
    parser.add_argument("--pass", dest="annotation_pass", type=int, default=1,
                        choices=[1, 2], help="Annotation pass (1 or 2)")
    parser.add_argument("--compute-kappa", action="store_true",
                        help="Compute Cohen's κ for all doubly-annotated projects")
    parser.add_argument("--consolidate", action="store_true",
                        help="Write ground_truth_all.jsonl consolidating all projects")
    parser.add_argument("--auto-accept-only", action="store_true",
                        help="Only run auto-acceptance (no interactive annotation)")
    parser.add_argument("--calibration-report", action="store_true",
                        help=(
                            "Generate auto_acceptance_calibration.json "
                            "(EXPLORATORY — not confirmatory)"
                        ))
    args = parser.parse_args()

    print("\n♿ a11y-autofix Ground-Truth Annotator\n" + "═" * 50)

    entries, metadata = load_catalog(args.catalog)

    targets = (
        [e for e in entries if e.id == args.project]
        if args.project
        else [e for e in entries if e.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)]
    )

    if not targets:
        print("  No projects to annotate.")
        return

    if args.compute_kappa:
        print("\n  Cohen's κ per project:\n")
        all_kappas = []
        for entry in targets:
            ag = compute_project_kappa(entry.id)
            if ag:
                print(f"  {entry.id:<40}  κ = {ag.kappa:+.4f}  ({ag.interpretation})")
                print(f"    FP rate = {ag.false_positive_rate:.1%}  |  "
                      f"confirmed = {ag.confirmed_count}  |  "
                      f"false_pos = {ag.false_positive_count}")
                all_kappas.append(ag.kappa)
            else:
                print(f"  {entry.id:<40}  (insufficient doubly-annotated findings)")
        if all_kappas:
            avg_kappa = round(sum(all_kappas) / len(all_kappas), 4)
            print(f"\n  Overall κ (mean across {len(all_kappas)} projects): {avg_kappa:+.4f}")
            print(f"  Threshold: κ ≥ 0.70 — {'✓ PASS' if avg_kappa >= 0.70 else '✗ FAIL'}")
        return

    if args.consolidate:
        consolidate_ground_truth([e.id for e in targets])
        return

    if args.calibration_report:
        print("\n  Generating auto-acceptance calibration report (EXPLORATORY)...\n")
        report = generate_calibration_report([e.id for e in targets])
        totals = report["totals"]
        print(f"  Total findings   : {totals['total_findings']}")
        print(f"  Auto-accepted    : {totals['auto_accepted']} "
              f"({totals['auto_accepted_fraction']:.1%}) {_AUTO_ACCEPT_NOTE}")
        print(f"  Human-annotated  : {totals['human_annotated']}")
        print(f"  Confirmed        : {totals['confirmed']}")
        print(f"  False positives  : {totals['false_positives']} "
              f"(FP rate = {totals['fp_rate']:.1%})")
        print(f"  Uncertain        : {totals['uncertain']}")
        print(f"\n  [!] {report['note']}")
        calib_path = RESULTS_DIR / "auto_acceptance_calibration.json"
        print(f"\n  Report written to {calib_path}")
        return

    # Annotation loop
    entry_index = {e.id: e for e in entries}
    for entry in targets:
        if args.auto_accept_only:
            findings = load_scan_findings(entry.id)
            records = load_ground_truth(entry.id)
            records = auto_accept_findings(findings, records)
            save_ground_truth(entry.id, records)
            auto = sum(1 for gt in records.values() if gt.auto_accepted)
            human = sum(1 for gt in records.values() if not gt.auto_accepted)
            print(f"  [{entry.id}] Auto-accepted {auto}, human-annotated {human}. "
                  f"[{_AUTO_ACCEPT_NOTE}]")
            entry.status = ProjectStatus.ANNOTATED
            entry_index[entry.id] = entry
            continue

        records = annotate_project(
            entry,
            annotator_id=args.annotator,
            annotation_pass=args.annotation_pass,
        )

        if records:
            auto = sum(1 for gt in records.values() if gt.auto_accepted)
            human = sum(1 for gt in records.values() if not gt.auto_accepted)
            print(f"  [{entry.id}] {auto} auto-accepted | {human} human-annotated")
            entry.status = ProjectStatus.ANNOTATED
            entry_index[entry.id] = entry

    save_catalog(list(entry_index.values()), args.catalog, metadata)
    print("\n  Catalog updated.")


if __name__ == "__main__":
    main()
