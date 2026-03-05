#!/usr/bin/env python3
"""
Repository snapshotting script for the a11y-autofix benchmark corpus.

For each project in the catalog:
  1. Shallow-clones the repository at its HEAD commit
  2. Pins the commit SHA for reproducibility
  3. Extracts package.json metadata (React version, TypeScript version)
  4. Counts JSX/TSX component files in scan_paths
  5. Applies manual screening criteria (IC6, IC7, EC6, EC7)
  6. Updates the catalog entry with snapshot metadata

Usage:
    python dataset/scripts/snapshot.py --catalog dataset/catalog/projects.yaml
    python dataset/scripts/snapshot.py --project saleor__storefront
    python dataset/scripts/snapshot.py --verify-only  # Re-check existing snapshots

References:
    Protocol: dataset/PROTOCOL.md §6
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    InclusionStatus,
    ProjectEntry,
    ProjectSize,
    ProjectStatus,
    SnapshotMetadata,
)

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"


# ── Helpers ──────────────────────────────────────────────────────────────────

def run_git(args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str]:
    """Execute a git command, returning (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True,
            cwd=cwd, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"git command timed out after {timeout}s"


def count_tsx_files(directory: Path, scan_paths: list[str], exclude_paths: list[str]) -> int:
    """Count .tsx/.jsx files in scan_paths, respecting exclude_paths."""
    exclude_patterns = [re.compile(
        p.replace("**", ".*").replace("*", "[^/]*").replace(".", r"\.")
    ) for p in exclude_paths]

    total = 0
    for rel_path in scan_paths:
        scan_dir = directory / rel_path.rstrip("/")
        if not scan_dir.exists():
            continue
        for f in scan_dir.rglob("*.tsx"):
            rel = str(f.relative_to(directory))
            if not any(p.search(rel) for p in exclude_patterns):
                total += 1
        for f in scan_dir.rglob("*.jsx"):
            rel = str(f.relative_to(directory))
            if not any(p.search(rel) for p in exclude_patterns):
                total += 1
    return total


def extract_package_metadata(repo_dir: Path) -> dict[str, str]:
    """Extract React and TypeScript versions from package.json."""
    result: dict[str, str] = {"react": "", "typescript": ""}
    pkg_path = repo_dir / "package.json"
    if not pkg_path.exists():
        # Try one level deeper
        for pkg in repo_dir.glob("*/package.json"):
            pkg_path = pkg
            break
    if not pkg_path.exists():
        return result
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        result["react"] = deps.get("react", "")
        result["typescript"] = deps.get("typescript", "")
    except (json.JSONDecodeError, OSError):
        pass
    return result


def has_jsx_exports(repo_dir: Path, scan_paths: list[str]) -> bool:
    """
    IC7: Check that at least one file contains a JSX return statement.
    Uses a simple regex heuristic.
    """
    jsx_return_pattern = re.compile(r"\breturn\s*\(?\s*<[A-Za-z/]")
    checked = 0
    for rel_path in scan_paths:
        scan_dir = repo_dir / rel_path.rstrip("/")
        if not scan_dir.exists():
            continue
        for f in list(scan_dir.rglob("*.tsx"))[:30] + list(scan_dir.rglob("*.jsx"))[:30]:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                if jsx_return_pattern.search(content):
                    return True
                checked += 1
                if checked > 50:
                    break
            except OSError:
                continue
    return False


def is_predominantly_generated(repo_dir: Path, scan_paths: list[str]) -> bool:
    """
    IC6/EC7: Check if >30% of JSX/TSX files appear auto-generated.
    Heuristic: files containing '@generated', 'DO NOT EDIT', or 'auto-generated' headers.
    """
    generated_markers = re.compile(
        r"(DO NOT EDIT|@generated|auto.generated|This file was generated)", re.I
    )
    total = 0
    generated = 0
    for rel_path in scan_paths:
        scan_dir = repo_dir / rel_path.rstrip("/")
        if not scan_dir.exists():
            continue
        for f in list(scan_dir.rglob("*.tsx"))[:100] + list(scan_dir.rglob("*.jsx"))[:100]:
            try:
                header = f.read_text(encoding="utf-8", errors="ignore")[:500]
                total += 1
                if generated_markers.search(header):
                    generated += 1
            except OSError:
                continue
    if total == 0:
        return False
    return (generated / total) > 0.30


# Common alternative scan paths to try when the default yields 0 component files
FALLBACK_SCAN_PATHS: list[list[str]] = [
    ["src/"],
    ["app/"],
    ["apps/"],
    ["packages/"],
    ["components/"],
    ["frontend/src/"],
    ["web/src/"],
    ["client/src/"],
    ["ui/src/"],
    [""],  # repo root
]


def find_best_scan_paths(
    repo_dir: Path,
    configured_paths: list[str],
    exclude_paths: list[str],
) -> tuple[list[str], int]:
    """
    Return the scan_paths and file count that yield the most component files.
    Falls back to common alternative paths when the configured paths give < 10 files.
    """
    count = count_tsx_files(repo_dir, configured_paths, exclude_paths)
    if count >= 10:
        return configured_paths, count

    best_paths = configured_paths
    best_count = count
    for candidate in FALLBACK_SCAN_PATHS:
        if candidate == configured_paths:
            continue
        c = count_tsx_files(repo_dir, candidate, exclude_paths)
        if c > best_count:
            best_count = c
            best_paths = candidate
        if best_count >= 10:
            break

    return best_paths, best_count


def snapshot_project(entry: ProjectEntry, force: bool = False) -> ProjectEntry:
    """
    Clone, pin, and annotate a single project entry.

    Modifies and returns the entry with updated snapshot and screening fields.
    """
    project_dir = SNAPSHOTS_DIR / entry.id

    # Skip if already snapshotted and not forcing
    if (
        not force
        and entry.status in (ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED, ProjectStatus.ANNOTATED)
        and entry.snapshot.pinned_commit
    ):
        print(f"  [{entry.id}] Already snapshotted. Skipping (use --force to re-clone).")
        return entry

    print(f"  [{entry.id}] Cloning {entry.github_url} ...")

    # Remove existing clone if forced
    if force and project_dir.exists():
        import shutil
        shutil.rmtree(project_dir)

    reusing_existing = False

    # Shallow clone
    clone_code, _, clone_err = run_git([
        "clone", "--depth", "1",
        "--single-branch",
        entry.github_url,
        str(project_dir),
    ])

    if clone_code != 0:
        # If the directory already exists (from a previous interrupted run),
        # check whether it is a valid git repo and reuse it.
        if project_dir.exists() and "already exists" in clone_err:
            sha_check, existing_sha, _ = run_git(["rev-parse", "HEAD"], cwd=project_dir)
            if sha_check == 0 and len(existing_sha) == 40:
                print(f"  [{entry.id}] Directory already exists — reusing existing clone.")
                reusing_existing = True
            else:
                print(f"  [{entry.id}] Clone failed and existing directory is invalid: {clone_err}",
                      file=sys.stderr)
                entry.status = ProjectStatus.ERROR
                entry.screening.exclusion_criterion = "CLONE_ERROR"
                entry.screening.exclusion_reason = clone_err[:200]
                return entry
        elif clone_code == -1:
            # Timeout
            print(f"  [{entry.id}] Clone timed out: {clone_err}", file=sys.stderr)
            entry.status = ProjectStatus.ERROR
            entry.screening.exclusion_criterion = "CLONE_TIMEOUT"
            entry.screening.exclusion_reason = clone_err[:200]
            return entry
        else:
            print(f"  [{entry.id}] Clone failed: {clone_err}", file=sys.stderr)
            entry.status = ProjectStatus.ERROR
            entry.screening.exclusion_criterion = "CLONE_ERROR"
            entry.screening.exclusion_reason = clone_err[:200]
            return entry

    # Get pinned commit SHA
    _, commit_sha, _ = run_git(["rev-parse", "HEAD"], cwd=project_dir)
    if len(commit_sha) != 40:
        print(f"  [{entry.id}] Could not retrieve commit SHA", file=sys.stderr)
        entry.status = ProjectStatus.ERROR
        return entry

    # Extract package metadata
    pkg_meta = extract_package_metadata(project_dir)

    # Count component files — try configured paths first, fall back to alternatives
    effective_scan_paths, file_count = find_best_scan_paths(
        project_dir,
        configured_paths=entry.scan_paths,
        exclude_paths=entry.exclude_paths,
    )
    if effective_scan_paths != entry.scan_paths:
        print(f"  [{entry.id}] scan_paths adjusted: {entry.scan_paths} → {effective_scan_paths}")
        entry.scan_paths = effective_scan_paths

    # IC4: Minimum 10 component files
    if file_count < 10:
        print(f"  [{entry.id}] IC4 FAIL: only {file_count} component files found")
        entry.status = ProjectStatus.EXCLUDED
        entry.screening.ic4_component_files = InclusionStatus.FAIL
        entry.screening.exclusion_criterion = "IC4"
        entry.screening.exclusion_reason = (
            f"Insufficient component files: {file_count} < 10"
        )
        return entry

    entry.screening.ic4_component_files = InclusionStatus.PASS

    # IC6: Not predominantly generated
    if is_predominantly_generated(project_dir, entry.scan_paths):
        print(f"  [{entry.id}] IC6 FAIL: predominantly auto-generated code")
        entry.status = ProjectStatus.EXCLUDED
        entry.screening.ic6_non_generated = InclusionStatus.FAIL
        entry.screening.exclusion_criterion = "IC6"
        entry.screening.exclusion_reason = ">30% of component files appear auto-generated"
        return entry

    entry.screening.ic6_non_generated = InclusionStatus.PASS

    # IC7: Contains JSX UI rendering
    if not has_jsx_exports(project_dir, entry.scan_paths):
        print(f"  [{entry.id}] IC7 FAIL: no JSX return statements found")
        entry.status = ProjectStatus.EXCLUDED
        entry.screening.ic7_ui_rendering = InclusionStatus.FAIL
        entry.screening.exclusion_criterion = "IC7"
        entry.screening.exclusion_reason = "No JSX return statements found in scan_paths"
        return entry

    entry.screening.ic7_ui_rendering = InclusionStatus.PASS

    # Classify size
    entry.size_category = (
        ProjectSize.LARGE if file_count >= 301
        else ProjectSize.MEDIUM if file_count >= 51
        else ProjectSize.SMALL
    )

    # Compute clone size
    try:
        import shutil
        total_bytes = sum(
            f.stat().st_size
            for f in project_dir.rglob("*")
            if f.is_file() and "node_modules" not in str(f)
        )
        clone_size_mb = total_bytes / (1024 * 1024)
    except OSError:
        clone_size_mb = 0.0

    # Update snapshot metadata
    entry.snapshot = SnapshotMetadata(
        pinned_commit=commit_sha,
        snapshot_date=datetime.now(tz=timezone.utc).isoformat(),
        branch=entry.github.default_branch,
        react_version=pkg_meta["react"],
        typescript_version=pkg_meta["typescript"],
        typescript=bool(pkg_meta["typescript"]),
        component_file_count=file_count,
        clone_size_mb=round(clone_size_mb, 2),
    )

    entry.status = ProjectStatus.SNAPSHOTTED
    print(
        f"  [{entry.id}] ✓ Pinned to {commit_sha[:8]}. "
        f"Files: {file_count}. React: {pkg_meta['react'] or 'unknown'}"
    )
    return entry


def verify_snapshot(entry: ProjectEntry) -> bool:
    """
    Verify that the existing snapshot matches the pinned commit.
    Returns True if integrity check passes.
    """
    project_dir = SNAPSHOTS_DIR / entry.id
    if not project_dir.exists():
        print(f"  [{entry.id}] ✗ Snapshot directory not found")
        return False

    _, current_sha, _ = run_git(["rev-parse", "HEAD"], cwd=project_dir)
    if current_sha != entry.snapshot.pinned_commit:
        print(
            f"  [{entry.id}] ✗ Commit mismatch: "
            f"expected {entry.snapshot.pinned_commit[:8]}, "
            f"got {current_sha[:8]}"
        )
        return False

    print(f"  [{entry.id}] ✓ Integrity OK ({current_sha[:8]})")
    return True


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot repositories for the a11y-autofix benchmark corpus"
    )
    parser.add_argument(
        "--catalog", type=Path, default=DEFAULT_CATALOG, help="Catalog YAML path"
    )
    parser.add_argument(
        "--project", default=None, help="Snapshot only this project ID"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="Verify existing snapshots without re-cloning"
    )
    parser.add_argument("--force", action="store_true", help="Re-clone even if already snapshotted")
    parser.add_argument("--workers", type=int, default=1, help="Parallel clone workers (default 1)")
    args = parser.parse_args()

    print("\n♿ a11y-autofix Dataset Snapshotting\n" + "═" * 50)

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    entries, metadata = load_catalog(args.catalog)
    print(f"  Loaded {len(entries)} projects from catalog")

    # Filter targets
    if args.project:
        targets = [e for e in entries if e.id == args.project]
        if not targets:
            print(f"Error: project '{args.project}' not found in catalog", file=sys.stderr)
            sys.exit(1)
    else:
        if args.verify_only:
            targets = [
                e for e in entries
                if e.status in (ProjectStatus.SNAPSHOTTED, ProjectStatus.SCANNED,
                                ProjectStatus.ANNOTATED)
            ]
        else:
            targets = [
                e for e in entries
                if e.status in (ProjectStatus.CANDIDATE, ProjectStatus.PENDING)
                or (args.force and e.status == ProjectStatus.SNAPSHOTTED)
            ]

    print(f"  Targets: {len(targets)} projects\n")

    if args.verify_only:
        passed = sum(1 for e in targets if verify_snapshot(e))
        print(f"\n  Integrity: {passed}/{len(targets)} snapshots verified")
        return

    # Build id→entry index for in-place update
    entry_index = {e.id: e for e in entries}
    updated = 0
    errors = 0

    for target in targets:
        updated_entry = snapshot_project(target, force=args.force)
        entry_index[updated_entry.id] = updated_entry
        if updated_entry.status == ProjectStatus.SNAPSHOTTED:
            updated += 1
        elif updated_entry.status in (ProjectStatus.EXCLUDED, ProjectStatus.ERROR):
            errors += 1
        # Save after every project so progress survives interruptions/crashes
        save_catalog(list(entry_index.values()), args.catalog, metadata)
        time.sleep(1.0)  # Avoid hammering GitHub

    print(f"\n{'═' * 50}")
    print(f"  Snapshotted: {updated}  |  Excluded/Error: {errors}")
    print(f"  Catalog updated: {args.catalog}")


if __name__ == "__main__":
    main()
