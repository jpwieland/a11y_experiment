#!/usr/bin/env python3
"""
Catalog management CLI for the a11y-autofix benchmark dataset.

Provides subcommands to inspect, validate, modify, and export the project
catalog (dataset/catalog/projects.yaml) without having to edit the YAML
file by hand.

Subcommands
-----------
validate      Validate catalog YAML against the ProjectEntry Pydantic schema
stats         Print domain / size / popularity / status distributions
show          Display a single project entry (formatted)
add           Add a new project entry (interactive or from arguments)
update-status Bulk update project status (e.g. mark all SNAPSHOTTED → SCANNED)
export        Export catalog to CSV or JSON for downstream use
diff          Show entries whose status changed since a reference snapshot
check-urls    Verify GitHub URLs are reachable (HEAD requests)

Usage:
    python dataset/scripts/catalog.py validate
    python dataset/scripts/catalog.py stats
    python dataset/scripts/catalog.py show saleor__storefront
    python dataset/scripts/catalog.py update-status --from scanned --to annotated
    python dataset/scripts/catalog.py export --format csv --output catalog.csv
    python dataset/scripts/catalog.py check-urls --token $GITHUB_TOKEN
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    ProjectDomain,
    ProjectEntry,
    ProjectPopularity,
    ProjectSize,
    ProjectStatus,
)

DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"


# ──────────────────────────────────────────────────────────────────────────────
# Catalog I/O
# ──────────────────────────────────────────────────────────────────────────────

def load_catalog(path: Path) -> tuple[list[ProjectEntry], dict[str, Any]]:
    """Load projects.yaml and return (entries, metadata)."""
    if not path.exists():
        print(f"Error: catalog not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    entries: list[ProjectEntry] = []
    errors: list[tuple[str, str]] = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            errors.append((raw.get("id", "?"), str(e)))

    if errors:
        print(f"  ⚠  {len(errors)} entries failed validation:", file=sys.stderr)
        for pid, err in errors[:5]:
            print(f"     {pid}: {err}", file=sys.stderr)
        if len(errors) > 5:
            print(f"     … ({len(errors) - 5} more)", file=sys.stderr)

    return entries, data.get("metadata", {})


def save_catalog(entries: list[ProjectEntry], path: Path, metadata: dict[str, Any]) -> None:
    """Persist entries back to projects.yaml."""
    output: dict[str, Any] = {
        "projects": [e.to_catalog_dict() for e in entries],
        "metadata": {
            **metadata,
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
            "total_projects": len(entries),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  Catalog saved to {path}  ({len(entries)} entries)")


# ──────────────────────────────────────────────────────────────────────────────
# validate
# ──────────────────────────────────────────────────────────────────────────────

def cmd_validate(args: argparse.Namespace) -> int:
    """Validate every entry in the catalog against the Pydantic schema."""
    print(f"\n  Validating {args.catalog} …\n")
    with open(args.catalog, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    projects = data.get("projects", [])
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []

    for raw in projects:
        pid = raw.get("id", "?")
        try:
            entry = ProjectEntry(**raw)
            # Extra semantic checks
            if not entry.scan_paths:
                warnings.append((pid, "scan_paths is empty"))
            if entry.status in (ProjectStatus.SCANNED, ProjectStatus.ANNOTATED) \
                    and not entry.snapshot:
                warnings.append((pid, "status is SCANNED but snapshot metadata is missing"))
        except Exception as e:
            errors.append((pid, str(e)))

    print(f"  Entries checked : {len(projects)}")
    print(f"  Errors          : {len(errors)}")
    print(f"  Warnings        : {len(warnings)}")

    if errors:
        print("\n  ✗ Errors:")
        for pid, msg in errors:
            print(f"    {pid}: {msg}")

    if warnings and not getattr(args, "quiet", False):
        print("\n  ⚠ Warnings:")
        for pid, msg in warnings[:10]:
            print(f"    {pid}: {msg}")
        if len(warnings) > 10:
            print(f"    … ({len(warnings) - 10} more)")

    if errors:
        print("\n  ✗ Validation FAILED")
        return 1
    print("\n  ✓ Validation passed")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# stats
# ──────────────────────────────────────────────────────────────────────────────

def cmd_stats(args: argparse.Namespace) -> int:
    entries, metadata = load_catalog(args.catalog)
    n = len(entries)

    print(f"\n  Catalog: {args.catalog}")
    print(f"  Total entries: {n}\n")

    def _dist(values: list[str], title: str) -> None:
        counts: dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        print(f"  {title}:")
        for k, c in sorted(counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(c * 2, 40)
            print(f"    {k:<25} {c:>4}  {bar}")
        print()

    _dist([
        (e.domain.value if hasattr(e.domain, "value") else str(e.domain))
        for e in entries
    ], "Domain")

    _dist([
        (e.status.value if hasattr(e.status, "value") else str(e.status))
        for e in entries
    ], "Status")

    _dist([
        (e.size_category.value if e.size_category and hasattr(e.size_category, "value")
         else str(e.size_category or "unknown"))
        for e in entries
    ], "Size")

    _dist([
        (e.popularity_tier.value if e.popularity_tier and hasattr(e.popularity_tier, "value")
         else str(e.popularity_tier or "unknown"))
        for e in entries
    ], "Popularity")

    # Status pipeline summary
    status_values = [
        e.status.value if hasattr(e.status, "value") else str(e.status)
        for e in entries
    ]
    scanned = status_values.count("scanned")
    annotated = status_values.count("annotated")
    snapshotted = status_values.count("snapshotted")
    excluded = status_values.count("excluded")

    print(f"  Pipeline progress:")
    total_active = n - excluded
    print(f"    Active projects  : {total_active}")
    print(f"    Snapshotted      : {snapshotted} ({snapshotted/max(n,1)*100:.0f}%)")
    print(f"    Scanned          : {scanned} ({scanned/max(n,1)*100:.0f}%)")
    print(f"    Annotated        : {annotated} ({annotated/max(n,1)*100:.0f}%)")
    print(f"    Excluded         : {excluded}")

    if metadata:
        print(f"\n  Metadata: {json.dumps(metadata, indent=4)}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# show
# ──────────────────────────────────────────────────────────────────────────────

def cmd_show(args: argparse.Namespace) -> int:
    entries, _ = load_catalog(args.catalog)
    entry_map = {e.id: e for e in entries}

    pid = args.project_id
    if pid not in entry_map:
        # Try partial match
        matches = [e for e in entries if pid.lower() in e.id.lower()
                   or pid.lower() in f"{e.owner}/{e.repo}".lower()]
        if not matches:
            print(f"  Project '{pid}' not found in catalog.", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print(f"  Multiple matches for '{pid}':")
            for m in matches:
                print(f"    {m.id}")
            return 1
        entry = matches[0]
    else:
        entry = entry_map[pid]

    width = 60
    print(f"\n{'═' * width}")
    print(f"  {entry.id}")
    print(f"{'─' * width}")
    print(f"  GitHub URL      : {entry.github_url}")
    domain = entry.domain.value if hasattr(entry.domain, "value") else str(entry.domain)
    status = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
    size = entry.size_category.value if entry.size_category and hasattr(entry.size_category, "value") else "?"
    pop = entry.popularity_tier.value if entry.popularity_tier and hasattr(entry.popularity_tier, "value") else "?"
    print(f"  Domain          : {domain}")
    print(f"  Status          : {status}")
    print(f"  Size / Popularity: {size} / {pop}")
    print(f"  Scan paths      : {', '.join(entry.scan_paths)}")
    print(f"  Exclude paths   : {', '.join(entry.exclude_paths or [])}")

    if entry.github:
        print(f"\n  GitHub metadata:")
        print(f"    Stars      : {entry.github.stars}")
        print(f"    Forks      : {entry.github.forks}")
        print(f"    Language   : {entry.github.language}")
        print(f"    Topics     : {', '.join(entry.github.topics or [])}")
        print(f"    Pushed at  : {entry.github.pushed_at}")

    if entry.snapshot:
        print(f"\n  Snapshot:")
        print(f"    Commit     : {entry.snapshot.pinned_commit}")
        print(f"    Date       : {entry.snapshot.snapshot_date}")
        print(f"    React      : {entry.snapshot.react_version}")
        print(f"    TypeScript : {entry.snapshot.typescript}")
        print(f"    TSX files  : {entry.snapshot.component_file_count}")

    if entry.scan:
        s = entry.scan
        print(f"\n  Scan summary:")
        print(f"    Scan date  : {s.scan_date}")
        print(f"    Duration   : {s.scan_duration_seconds}s")
        print(f"    Tools ok   : {', '.join(s.tools_succeeded or [])}")
        if s.findings:
            f = s.findings
            print(f"    Total issues: {f.total_issues}")
            print(f"    High / Med / Low confidence: "
                  f"{f.high_confidence} / {f.medium_confidence} / {f.low_confidence}")
            print(f"    Files scanned: {f.files_scanned}  (with issues: {f.files_with_issues})")

    if entry.annotation_summary:
        ag = entry.annotation_summary
        print(f"\n  Annotation:")
        print(f"    κ          : {ag.kappa}  ({ag.interpretation})")
        print(f"    FP rate    : {ag.false_positive_rate:.1%}")
        print(f"    Confirmed  : {ag.confirmed_count}")

    if args.json:
        print(f"\n{'─' * width}")
        print(json.dumps(entry.model_dump(mode="json"), indent=2))

    print(f"{'═' * width}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# add
# ──────────────────────────────────────────────────────────────────────────────

def cmd_add(args: argparse.Namespace) -> int:
    entries, metadata = load_catalog(args.catalog)
    existing_ids = {e.id for e in entries}

    owner = args.owner
    repo = args.repo
    project_id = f"{owner}__{repo}"

    if project_id in existing_ids:
        print(f"  Project '{project_id}' already exists in catalog.", file=sys.stderr)
        return 1

    domain_str = args.domain or "other"
    try:
        domain = ProjectDomain(domain_str)
    except ValueError:
        valid = [d.value for d in ProjectDomain]
        print(f"  Invalid domain '{domain_str}'. Valid options: {', '.join(valid)}", file=sys.stderr)
        return 1

    entry = ProjectEntry(
        id=project_id,
        owner=owner,
        repo=repo,
        github_url=f"https://github.com/{owner}/{repo}",
        domain=domain,
        scan_paths=args.scan_paths or ["src"],
        exclude_paths=args.exclude_paths or [],
        inclusion_rationale=args.rationale or f"Added via catalog.py on {datetime.now().date()}",
        status=ProjectStatus.CANDIDATE,
    )

    entries.append(entry)
    save_catalog(entries, args.catalog, metadata)
    print(f"  Added: {project_id}  (status: candidate)")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# update-status
# ──────────────────────────────────────────────────────────────────────────────

def cmd_update_status(args: argparse.Namespace) -> int:
    entries, metadata = load_catalog(args.catalog)

    try:
        from_status = ProjectStatus(args.from_status)
        to_status = ProjectStatus(args.to_status)
    except ValueError as e:
        valid = [s.value for s in ProjectStatus]
        print(f"  Invalid status: {e}. Valid: {', '.join(valid)}", file=sys.stderr)
        return 1

    targets = [e for e in entries if e.status == from_status]

    if args.project:
        targets = [e for e in targets if e.id == args.project]

    if not targets:
        print(f"  No projects with status '{from_status.value}'"
              + (f" and id '{args.project}'" if args.project else "") + ".")
        return 0

    print(f"\n  Updating {len(targets)} project(s): {from_status.value} → {to_status.value}")
    for e in targets:
        print(f"    {e.id}")
        e.status = to_status

    if not args.dry_run:
        save_catalog(entries, args.catalog, metadata)
    else:
        print("  (dry-run: no changes written)")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# export
# ──────────────────────────────────────────────────────────────────────────────

def cmd_export(args: argparse.Namespace) -> int:
    entries, metadata = load_catalog(args.catalog)

    if args.status:
        try:
            status_filter = ProjectStatus(args.status)
            entries = [e for e in entries if e.status == status_filter]
        except ValueError:
            pass

    output_path = Path(args.output) if args.output else None

    if args.format == "json":
        data = [e.model_dump(mode="json") for e in entries]
        content = json.dumps(data, indent=2)
        if output_path:
            output_path.write_text(content, encoding="utf-8")
            print(f"  Exported {len(entries)} entries to {output_path}")
        else:
            print(content)

    elif args.format == "csv":
        field_names = [
            "id", "owner", "repo", "github_url", "domain",
            "status", "size_category", "popularity_tier",
            "stars", "forks", "scan_paths", "pinned_commit",
        ]
        rows = []
        for e in entries:
            rows.append({
                "id": e.id,
                "owner": e.owner,
                "repo": e.repo,
                "github_url": e.github_url,
                "domain": e.domain.value if hasattr(e.domain, "value") else str(e.domain),
                "status": e.status.value if hasattr(e.status, "value") else str(e.status),
                "size_category": (e.size_category.value if e.size_category and hasattr(e.size_category, "value") else ""),
                "popularity_tier": (e.popularity_tier.value if e.popularity_tier and hasattr(e.popularity_tier, "value") else ""),
                "stars": e.github.stars if e.github else "",
                "forks": e.github.forks if e.github else "",
                "scan_paths": "|".join(e.scan_paths),
                "pinned_commit": (e.snapshot.pinned_commit if e.snapshot else ""),
            })

        if output_path:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=field_names)
                writer.writeheader()
                writer.writerows(rows)
            print(f"  Exported {len(entries)} entries to {output_path}")
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=field_names)
            writer.writeheader()
            writer.writerows(rows)

    return 0


# ──────────────────────────────────────────────────────────────────────────────
# diff
# ──────────────────────────────────────────────────────────────────────────────

def cmd_diff(args: argparse.Namespace) -> int:
    """Compare current catalog status against a reference YAML snapshot."""
    current_entries, _ = load_catalog(args.catalog)
    ref_path = Path(args.reference)

    if not ref_path.exists():
        print(f"  Reference file not found: {ref_path}", file=sys.stderr)
        return 1

    ref_entries, _ = load_catalog(ref_path)
    current_map = {e.id: e for e in current_entries}
    ref_map = {e.id: e for e in ref_entries}

    added = [pid for pid in current_map if pid not in ref_map]
    removed = [pid for pid in ref_map if pid not in current_map]
    changed: list[tuple[str, str, str]] = []

    for pid, entry in current_map.items():
        if pid in ref_map:
            cur_status = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
            ref_status = ref_map[pid].status.value if hasattr(ref_map[pid].status, "value") else str(ref_map[pid].status)
            if cur_status != ref_status:
                changed.append((pid, ref_status, cur_status))

    print(f"\n  Diff: {ref_path} → {args.catalog}")
    print(f"  Added   : {len(added)}")
    print(f"  Removed : {len(removed)}")
    print(f"  Changed : {len(changed)}")

    if added:
        print("\n  + Added:")
        for pid in added:
            print(f"      {pid}")
    if removed:
        print("\n  - Removed:")
        for pid in removed:
            print(f"      {pid}")
    if changed:
        print("\n  ~ Status changes:")
        for pid, old, new in changed:
            print(f"      {pid}: {old} → {new}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# check-urls
# ──────────────────────────────────────────────────────────────────────────────

def cmd_check_urls(args: argparse.Namespace) -> int:
    """Verify all GitHub repository URLs are reachable via the GitHub API."""
    try:
        import httpx
    except ImportError:
        print("  httpx not installed. Run: pip install httpx", file=sys.stderr)
        return 1

    entries, _ = load_catalog(args.catalog)

    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if args.token:
        headers["Authorization"] = f"token {args.token}"

    print(f"\n  Checking {len(entries)} URLs …\n")
    ok = 0
    fail = 0

    with httpx.Client(headers=headers, timeout=10.0) as client:
        for entry in entries:
            api_url = f"https://api.github.com/repos/{entry.owner}/{entry.repo}"
            try:
                r = client.head(api_url)
                if r.status_code == 200:
                    print(f"  ✓  {entry.id}")
                    ok += 1
                else:
                    print(f"  ✗  {entry.id}  (HTTP {r.status_code})")
                    fail += 1
            except Exception as e:
                print(f"  ✗  {entry.id}  (error: {e})")
                fail += 1
            time.sleep(0.2)  # Rate-limit courtesy

    print(f"\n  Reachable: {ok}/{len(entries)}  |  Unreachable: {fail}")
    return 0 if fail == 0 else 1


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="catalog",
        description="Manage the a11y-autofix benchmark dataset catalog",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG,
                        help="Path to projects.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = subparsers.add_parser("validate", help="Validate YAML schema")
    p_val.add_argument("--quiet", action="store_true", help="Suppress warnings")

    # stats
    subparsers.add_parser("stats", help="Print distribution statistics")

    # show
    p_show = subparsers.add_parser("show", help="Show a single project")
    p_show.add_argument("project_id", help="Project ID or owner/repo")
    p_show.add_argument("--json", action="store_true", help="Also print raw JSON")

    # add
    p_add = subparsers.add_parser("add", help="Add a new project")
    p_add.add_argument("owner", help="GitHub owner")
    p_add.add_argument("repo", help="GitHub repository name")
    p_add.add_argument("--domain", default="other",
                       choices=[d.value for d in ProjectDomain])
    p_add.add_argument("--scan-paths", nargs="+", default=["src"])
    p_add.add_argument("--exclude-paths", nargs="+", default=[])
    p_add.add_argument("--rationale", default=None)

    # update-status
    p_us = subparsers.add_parser("update-status", help="Bulk update project status")
    p_us.add_argument("--from", dest="from_status", required=True,
                      choices=[s.value for s in ProjectStatus])
    p_us.add_argument("--to", dest="to_status", required=True,
                      choices=[s.value for s in ProjectStatus])
    p_us.add_argument("--project", default=None, help="Limit to a single project ID")
    p_us.add_argument("--dry-run", action="store_true")

    # export
    p_ex = subparsers.add_parser("export", help="Export catalog to CSV or JSON")
    p_ex.add_argument("--format", choices=["csv", "json"], default="csv")
    p_ex.add_argument("--output", default=None, help="Output file path")
    p_ex.add_argument("--status", default=None, help="Filter by status")

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare against a reference catalog")
    p_diff.add_argument("reference", help="Path to reference catalog YAML")

    # check-urls
    p_cu = subparsers.add_parser("check-urls", help="Verify GitHub URLs are reachable")
    p_cu.add_argument("--token", default=None, help="GitHub personal access token")

    args = parser.parse_args()

    print("\n♿ a11y-autofix Catalog Manager\n" + "═" * 50)

    cmd_map = {
        "validate": cmd_validate,
        "stats": cmd_stats,
        "show": cmd_show,
        "add": cmd_add,
        "update-status": cmd_update_status,
        "export": cmd_export,
        "diff": cmd_diff,
        "check-urls": cmd_check_urls,
    }

    fn = cmd_map.get(args.command)
    if fn:
        sys.exit(fn(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
