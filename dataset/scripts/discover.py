#!/usr/bin/env python3
"""
GitHub discovery script for the a11y-autofix benchmark corpus.

Implements stratified sampling across 7 application domains, 3 size classes,
and 3 popularity tiers using the GitHub Search REST API v3.

Usage:
    python dataset/scripts/discover.py --token <GITHUB_TOKEN> --output dataset/catalog/projects.yaml
    python dataset/scripts/discover.py --token <GITHUB_TOKEN> --domain government --max 20
    python dataset/scripts/discover.py --dry-run  # show queries without calling API

References:
    - GitHub Search API: https://docs.github.com/en/rest/search/search
    - Protocol: dataset/PROTOCOL.md §3, §4, §5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

# ── Path resolution ──────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
sys.path.insert(0, str(REPO_ROOT))

from dataset.schema.models import (
    GitHubMetadata,
    InclusionStatus,
    ProjectDomain,
    ProjectEntry,
    ProjectPopularity,
    ProjectSize,
    ProjectStatus,
    ScreeningRecord,
    SnapshotMetadata,
)

# ── Constants ────────────────────────────────────────────────────────────────
GITHUB_API = "https://api.github.com"
SEARCH_ENDPOINT = f"{GITHUB_API}/search/repositories"
DEFAULT_CATALOG = DATASET_ROOT / "catalog" / "projects.yaml"

# Cutoff: projects not updated in the last 24 months are excluded (criterion IC2)
ACTIVITY_CUTOFF = datetime.now(tz=timezone.utc) - timedelta(days=730)

# Minimum stars (criterion IC1)
MIN_STARS = 100

# OSI-approved licenses acceptable under criterion IC3
ACCEPTABLE_LICENSES: frozenset[str] = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "MPL-2.0", "LGPL-2.1", "LGPL-3.0", "CC0-1.0",
})

# Patterns for starter/template exclusion (criterion EC1)
STARTER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(starter|boilerplate|template|scaffold|create-|skeleton)\b", re.I),
]

# Patterns for course project exclusion (criterion EC2)
COURSE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(homework|course|tutorial|learning|bootcamp|assignment|practice)\b", re.I),
]

# ── Stratum definitions ───────────────────────────────────────────────────────

DOMAIN_QUERIES: dict[ProjectDomain, list[str]] = {
    ProjectDomain.ECOMMERCE: [
        "react typescript ecommerce storefront in:topics",
        "react typescript shopping-cart marketplace in:topics",
        "topic:ecommerce language:TypeScript stars:>100",
        "topic:storefront language:TypeScript stars:>100",
        "topic:shopify language:TypeScript stars:>50",
        "topic:woocommerce language:TypeScript stars:>50",
        "react typescript online-store checkout in:topics",
        "topic:nextjs-ecommerce language:TypeScript",
        "react typescript product catalog shop in:description stars:>100",
        "topic:saleor language:TypeScript",
    ],
    ProjectDomain.GOVERNMENT: [
        "react typescript government civic in:topics",
        "topic:government language:TypeScript stars:>50",
        "topic:civic-tech language:TypeScript",
        "react typescript public-sector accessibility in:description stars:>50",
        "topic:open-data language:TypeScript stars:>50",
        "react typescript city portal municipality in:description stars:>50",
        "topic:govtech language:TypeScript",
        "react typescript nonprofit ngo in:topics stars:>50",
        "topic:open-government language:TypeScript",
        "react typescript usa.gov federal state in:description stars:>50",
    ],
    ProjectDomain.HEALTHCARE: [
        "react typescript healthcare medical in:topics",
        "topic:healthcare language:TypeScript stars:>50",
        "topic:ehr language:TypeScript",
        "react typescript patient portal in:description",
        "topic:fhir language:TypeScript stars:>50",
        "topic:telemedicine language:TypeScript",
        "react typescript hospital clinic medical-record in:description stars:>50",
        "topic:health language:TypeScript stars:>100",
        "react typescript pharmacy prescription in:description stars:>50",
        "topic:openemr language:TypeScript",
    ],
    ProjectDomain.EDUCATION: [
        "react typescript education lms in:topics",
        "topic:education language:TypeScript stars:>100",
        "topic:edtech language:TypeScript",
        "topic:e-learning language:TypeScript stars:>100",
        "topic:lms language:TypeScript stars:>50",
        "topic:mooc language:TypeScript",
        "react typescript course platform learning in:description stars:>100",
        "topic:classroom language:TypeScript stars:>50",
        "topic:moodle language:TypeScript",
        "react typescript quiz exam assessment in:topics stars:>50",
    ],
    ProjectDomain.DEVELOPER_TOOLS: [
        "react typescript developer-tools ide in:topics",
        "topic:developer-tools language:TypeScript stars:>200",
        "react component library typescript in:topics stars:>500",
        "topic:design-system language:TypeScript stars:>100",
        "topic:storybook language:TypeScript stars:>100",
        "topic:code-editor language:TypeScript stars:>100",
        "react typescript cli devtools workspace in:description stars:>200",
        "topic:playground language:TypeScript stars:>100",
        "topic:api-client language:TypeScript stars:>100",
        "react typescript debugger profiler trace in:description stars:>100",
    ],
    ProjectDomain.DASHBOARD: [
        "react typescript dashboard analytics in:topics",
        "topic:dashboard language:TypeScript stars:>100",
        "topic:data-visualization language:TypeScript stars:>100",
        "react typescript monitoring analytics in:description stars:>200",
        "topic:admin-dashboard language:TypeScript stars:>100",
        "topic:bi language:TypeScript stars:>100",
        "react typescript reporting metrics kpi in:description stars:>100",
        "topic:grafana language:TypeScript stars:>100",
        "react typescript table grid chart in:topics stars:>100",
        "topic:superset language:TypeScript",
    ],
    ProjectDomain.SOCIAL: [
        "react typescript chat messaging collaboration in:topics",
        "topic:messaging language:TypeScript stars:>100",
        "topic:collaboration language:TypeScript stars:>100",
        "react social network typescript in:topics stars:>100",
        "topic:forum language:TypeScript stars:>100",
        "topic:community language:TypeScript stars:>100",
        "react typescript feed timeline post in:description stars:>100",
        "topic:slack-alternative language:TypeScript stars:>100",
        "topic:discord-clone language:TypeScript stars:>50",
        "react typescript video-call webrtc in:topics stars:>100",
    ],
}

# Target project counts per domain stratum.
# Total ≈ 560; after ~25% IC4/IC6/IC7 failures → ~420 included (QM2: ≥ 400).
# Each domain ≈ 80 repos → 80/420 ≈ 19% < 20% (QM3 passes).
DOMAIN_TARGETS: dict[ProjectDomain, int] = {
    ProjectDomain.ECOMMERCE: 90,
    ProjectDomain.GOVERNMENT: 60,
    ProjectDomain.HEALTHCARE: 60,
    ProjectDomain.EDUCATION: 80,
    ProjectDomain.DEVELOPER_TOOLS: 90,
    ProjectDomain.DASHBOARD: 90,
    ProjectDomain.SOCIAL: 90,
}


def classify_popularity(stars: int) -> ProjectPopularity:
    if stars >= 10_000:
        return ProjectPopularity.POPULAR
    if stars >= 1_000:
        return ProjectPopularity.ESTABLISHED
    return ProjectPopularity.EMERGING


def classify_size(file_count: int) -> ProjectSize:
    if file_count >= 301:
        return ProjectSize.LARGE
    if file_count >= 51:
        return ProjectSize.MEDIUM
    return ProjectSize.SMALL


def is_active(pushed_at: str) -> bool:
    """IC2: Last commit within 24 months."""
    try:
        dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        return dt >= ACTIVITY_CUTOFF
    except (ValueError, AttributeError):
        return False


def has_acceptable_license(license_data: dict[str, Any] | None) -> bool:
    """IC3: OSI-approved open-source license."""
    if not license_data:
        return False
    spdx = license_data.get("spdx_id", "")
    return spdx in ACCEPTABLE_LICENSES


def check_starter_template(name: str, description: str) -> bool:
    """EC1: Is a starter template or boilerplate."""
    text = f"{name} {description}".lower()
    return any(p.search(text) for p in STARTER_PATTERNS)


def check_course_project(description: str, topics: list[str]) -> bool:
    """EC2: Is a course project or learning exercise."""
    text = f"{description} {' '.join(topics)}".lower()
    return any(p.search(text) for p in COURSE_PATTERNS)


def screen_repository(repo: dict[str, Any]) -> tuple[bool, ScreeningRecord]:
    """
    Apply automated inclusion/exclusion screening to a GitHub repository dict.

    Returns (passes, ScreeningRecord) where passes is True iff the repo
    satisfies all automatically-checked criteria.
    """
    record = ScreeningRecord()

    # IC1: Stars ≥ MIN_STARS
    stars = repo.get("stargazers_count", 0)
    record.ic1_stars = InclusionStatus.PASS if stars >= MIN_STARS else InclusionStatus.FAIL

    # IC2: Active within 24 months
    record.ic2_last_commit = (
        InclusionStatus.PASS if is_active(repo.get("pushed_at", ""))
        else InclusionStatus.FAIL
    )

    # IC3: Acceptable license
    record.ic3_license = (
        InclusionStatus.PASS if has_acceptable_license(repo.get("license"))
        else InclusionStatus.FAIL
    )

    # IC5: package.json presence (proxy: language is TypeScript or JavaScript)
    lang = repo.get("language", "")
    record.ic5_buildability = (
        InclusionStatus.PASS if lang in ("TypeScript", "JavaScript")
        else InclusionStatus.FAIL
    )

    # EC1: Starter template
    is_starter = check_starter_template(
        repo.get("name", ""), repo.get("description") or ""
    )
    record.ec1_starter_template = (
        InclusionStatus.FAIL if is_starter else InclusionStatus.PASS
    )

    # EC2: Course project
    is_course = check_course_project(
        repo.get("description") or "", repo.get("topics", [])
    )
    record.ec2_course_project = (
        InclusionStatus.FAIL if is_course else InclusionStatus.PASS
    )

    # EC3: Fork
    is_fork = repo.get("fork", False)
    record.ec3_duplicate = InclusionStatus.FAIL if is_fork else InclusionStatus.PASS

    # EC4: Archived
    is_archived = repo.get("archived", False)
    record.ec4_archived = InclusionStatus.FAIL if is_archived else InclusionStatus.PASS

    # Determine exclusion reason
    if record.ic1_stars == InclusionStatus.FAIL:
        record.exclusion_criterion = "IC1"
        record.exclusion_reason = f"Too few stars ({stars} < {MIN_STARS})"
    elif record.ic2_last_commit == InclusionStatus.FAIL:
        record.exclusion_criterion = "IC2"
        record.exclusion_reason = f"Inactive (last push: {repo.get('pushed_at')})"
    elif record.ic3_license == InclusionStatus.FAIL:
        spdx = (repo.get("license") or {}).get("spdx_id", "none")
        record.exclusion_criterion = "IC3"
        record.exclusion_reason = f"Non-OSI license: {spdx}"
    elif record.ec1_starter_template == InclusionStatus.FAIL:
        record.exclusion_criterion = "EC1"
        record.exclusion_reason = "Identified as starter/template/boilerplate"
    elif record.ec2_course_project == InclusionStatus.FAIL:
        record.exclusion_criterion = "EC2"
        record.exclusion_reason = "Identified as course project or tutorial"
    elif record.ec3_duplicate == InclusionStatus.FAIL:
        record.exclusion_criterion = "EC3"
        record.exclusion_reason = "Repository is a fork"
    elif record.ec4_archived == InclusionStatus.FAIL:
        record.exclusion_criterion = "EC4"
        record.exclusion_reason = "Repository is archived"

    return record.passes_all, record


def repo_to_project_entry(
    repo: dict[str, Any],
    domain: ProjectDomain,
    screening: ScreeningRecord,
) -> ProjectEntry:
    """Convert a GitHub API repository dict to a ProjectEntry."""
    owner = repo["owner"]["login"]
    name = repo["name"]
    project_id = f"{owner}__{name}"

    github_meta = GitHubMetadata(
        stars=repo.get("stargazers_count", 0),
        forks=repo.get("forks_count", 0),
        open_issues=repo.get("open_issues_count", 0),
        watchers=repo.get("watchers_count", 0),
        language=repo.get("language", "TypeScript"),
        topics=repo.get("topics", []),
        license_spdx=(repo.get("license") or {}).get("spdx_id", ""),
        default_branch=repo.get("default_branch", "main"),
        created_at=repo.get("created_at", ""),
        pushed_at=repo.get("pushed_at", ""),
        description=repo.get("description") or "",
        homepage=repo.get("homepage") or "",
        archived=repo.get("archived", False),
        fork=repo.get("fork", False),
    )

    stars = github_meta.stars
    popularity = classify_popularity(stars)

    entry = ProjectEntry(
        id=project_id,
        owner=owner,
        repo=name,
        github_url=f"https://github.com/{owner}/{name}",
        domain=domain,
        popularity_tier=popularity,
        inclusion_rationale=(
            f"Discovered via GitHub search for domain '{domain.value}'. "
            f"Stars: {stars}. Language: {github_meta.language}. "
            f"Topics: {', '.join(github_meta.topics[:5])}."
        ),
        status=ProjectStatus.PENDING,
        github=github_meta,
        screening=screening,
    )
    return entry


class GitHubDiscovery:
    """GitHub API client for repository discovery with rate-limit handling."""

    def __init__(self, token: str) -> None:
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._client = httpx.Client(headers=self._headers, timeout=30.0)

    def search(
        self,
        query: str,
        sort: str = "stars",
        order: str = "desc",
        per_page: int = 100,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute a GitHub repository search query and return all result items.

        Handles pagination and rate-limit backoff automatically.
        """
        results: list[dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            params = {
                "q": query,
                "sort": sort,
                "order": order,
                "per_page": per_page,
                "page": page,
            }
            response = self._request_with_backoff(SEARCH_ENDPOINT, params)
            if response is None:
                break

            items = response.get("items", [])
            results.extend(items)

            total_count = response.get("total_count", 0)
            if len(results) >= total_count or len(items) < per_page:
                break

            # Respect secondary rate limit: 30 requests/min authenticated
            time.sleep(2.0)

        return results

    def _request_with_backoff(
        self,
        url: str,
        params: dict[str, Any],
        max_retries: int = 3,
    ) -> dict[str, Any] | None:
        """HTTP GET with exponential backoff on rate-limit (403/429)."""
        for attempt in range(max_retries):
            try:
                response = self._client.get(url, params=params)

                if response.status_code == 200:
                    return response.json()

                if response.status_code in (403, 429):
                    reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
                    wait = max(reset_ts - int(time.time()) + 5, 60)
                    print(f"  Rate limited. Waiting {wait}s (attempt {attempt + 1})")
                    time.sleep(wait)
                    continue

                print(
                    f"  HTTP {response.status_code} for query. Skipping.",
                    file=sys.stderr,
                )
                return None

            except httpx.RequestError as e:
                wait = 2 ** attempt * 5
                print(f"  Network error: {e}. Retrying in {wait}s")
                time.sleep(wait)

        return None

    def close(self) -> None:
        self._client.close()


def load_existing_catalog(path: Path) -> tuple[dict[str, ProjectEntry], dict[str, Any]]:
    """Load existing catalog YAML; returns (id→entry dict, raw metadata dict)."""
    if not path.exists():
        return {}, {}

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    entries: dict[str, ProjectEntry] = {}
    for raw in data.get("projects", []):
        try:
            entry = ProjectEntry(**raw)
            entries[entry.id] = entry
        except Exception as e:
            print(f"  Warning: could not parse entry {raw.get('id', '?')}: {e}")

    return entries, data.get("metadata", {})


def save_catalog(
    entries: dict[str, ProjectEntry],
    path: Path,
    metadata: dict[str, Any],
) -> None:
    """Serialise catalog to YAML with preserved metadata header."""
    path.parent.mkdir(parents=True, exist_ok=True)

    projects_list = [e.to_catalog_dict() for e in sorted(entries.values(), key=lambda e: e.id)]

    output: dict[str, Any] = {
        "projects": projects_list,
        "metadata": {
            **metadata,
            "total_seed_projects": len(entries),
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
        },
    }

    with open(path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"  Saved {len(entries)} entries to {path}")


def discover_domain(
    client: GitHubDiscovery,
    domain: ProjectDomain,
    existing_ids: set[str],
    target: int,
    verbose: bool = False,
) -> list[ProjectEntry]:
    """Discover and screen projects for a single domain stratum."""
    queries = DOMAIN_QUERIES[domain]
    found: list[ProjectEntry] = []
    seen_ids: set[str] = set(existing_ids)

    for query in queries:
        if len(found) >= target:
            break

        if verbose:
            print(f"    Query: {query}")

        results = client.search(query, max_pages=5)
        print(f"    → {len(results)} raw results for: {query[:60]}")

        for repo in results:
            if len(found) >= target:
                break

            repo_id = f"{repo['owner']['login']}__{repo['name']}"
            if repo_id in seen_ids:
                continue
            seen_ids.add(repo_id)

            passes, screening = screen_repository(repo)
            if not passes:
                if verbose:
                    print(f"      Excluded {repo_id}: {screening.exclusion_reason}")
                continue

            entry = repo_to_project_entry(repo, domain, screening)
            found.append(entry)
            print(f"      ✓ {repo_id} ({repo['stargazers_count']} ★)")

        time.sleep(2.0)  # Respect rate limits between queries

    return found


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover GitHub projects for the a11y-autofix benchmark corpus"
    )
    parser.add_argument(
        "--token",
        default="",
        help="GitHub Personal Access Token (required for search API)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to catalog YAML file",
    )
    parser.add_argument(
        "--domain",
        choices=[d.value for d in ProjectDomain],
        default=None,
        help="Restrict discovery to a single domain stratum",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Override target project count per domain",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print queries without calling the GitHub API",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("Error: --token is required unless --dry-run is set", file=sys.stderr)
        sys.exit(1)

    print("\n♿ a11y-autofix Dataset Discovery\n" + "═" * 50)

    # Load existing catalog to avoid duplicates
    existing_entries, metadata = load_existing_catalog(args.output)
    print(f"  Existing catalog: {len(existing_entries)} projects")

    if args.dry_run:
        print("\n[dry-run] Queries that would be executed:\n")
        for domain, queries in DOMAIN_QUERIES.items():
            if args.domain and domain.value != args.domain:
                continue
            print(f"  [{domain.value}]")
            for q in queries:
                print(f"    {q}")
        return

    client = GitHubDiscovery(args.token)
    all_new: list[ProjectEntry] = []

    domains_to_search = (
        [ProjectDomain(args.domain)] if args.domain
        else list(ProjectDomain)
    )

    for domain in domains_to_search:
        if domain == ProjectDomain.OTHER:
            continue

        target = args.max or DOMAIN_TARGETS.get(domain, 5)
        current_count = sum(
            1 for e in existing_entries.values() if e.domain == domain
        )
        remaining = max(0, target - current_count)

        if remaining == 0:
            print(f"\n  [{domain.value}] Already at target ({current_count}/{target}). Skipping.")
            continue

        print(f"\n  [{domain.value}] Searching for {remaining} more project(s) (have {current_count}/{target})")

        new = discover_domain(
            client,
            domain,
            existing_ids=set(existing_entries.keys()),
            target=remaining,
            verbose=args.verbose,
        )
        all_new.extend(new)
        print(f"  [{domain.value}] Found {len(new)} new projects")

    client.close()

    if not all_new:
        print("\nNo new projects discovered.")
        return

    # Merge with existing catalog
    for entry in all_new:
        existing_entries[entry.id] = entry

    save_catalog(existing_entries, args.output, metadata)
    print(f"\n✓ Added {len(all_new)} new projects. Total: {len(existing_entries)}")


if __name__ == "__main__":
    main()
