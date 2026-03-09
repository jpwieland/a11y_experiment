#!/usr/bin/env python3
"""
GitHub discovery script for the a11y-autofix benchmark corpus.

Implements stratified sampling across 7 application domains, 3 size classes,
and 3 popularity tiers using the GitHub Search REST API v3.

Usage:
    python dataset/scripts/discover.py --token <TOKEN> --output dataset/catalog/projects.yaml
    python dataset/scripts/discover.py --token <TOKEN> --domain ecommerce --max 20
    python dataset/scripts/discover.py --token <TOKEN> --top-up          # fill gaps
    python dataset/scripts/discover.py --stats                           # show coverage
    python dataset/scripts/discover.py --dry-run                         # preview queries

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
        # Topic-based queries
        "topic:ecommerce language:TypeScript stars:>100",
        "topic:storefront language:TypeScript stars:>100",
        "topic:shopify language:TypeScript stars:>50",
        "topic:woocommerce language:TypeScript stars:>50",
        "topic:nextjs-ecommerce language:TypeScript",
        "topic:saleor language:TypeScript",
        "topic:magento language:TypeScript stars:>50",
        "topic:commercetools language:TypeScript",
        "topic:medusajs language:TypeScript",
        "topic:stripe language:TypeScript stars:>100",
        # Description-based queries
        "react typescript ecommerce storefront in:topics stars:>100",
        "react typescript shopping-cart marketplace in:topics stars:>50",
        "react typescript online-store checkout in:topics stars:>50",
        "react typescript product catalog shop in:description stars:>100",
        "react typescript payment checkout order in:description stars:>100",
        # Framework-specific
        "next.js typescript ecommerce in:description stars:>100",
        "nextjs typescript shop store cart in:name stars:>100",
        "react typescript commerce headless in:description stars:>100",
        "typescript vite ecommerce shop in:description stars:>50",
        "remix typescript ecommerce store in:name,description stars:>50",
    ],
    ProjectDomain.GOVERNMENT: [
        # Topic-based queries
        "topic:government language:TypeScript stars:>50",
        "topic:civic-tech language:TypeScript",
        "topic:govtech language:TypeScript",
        "topic:open-government language:TypeScript",
        "topic:open-data language:TypeScript stars:>50",
        "topic:transparency language:TypeScript stars:>50",
        "topic:public-service language:TypeScript",
        "topic:democracy language:TypeScript stars:>50",
        # Description-based queries
        "react typescript government civic in:topics stars:>50",
        "react typescript public-sector accessibility in:description stars:>50",
        "react typescript city portal municipality in:description stars:>50",
        "react typescript nonprofit ngo in:topics stars:>50",
        "react typescript usa.gov federal state in:description stars:>50",
        "react typescript open-source government portal in:description stars:>50",
        # Agency/platform specific
        "typescript nextjs government portal in:description stars:>50",
        "react typescript legislation parliament senate in:description stars:>30",
        "react typescript voting election civic in:description stars:>50",
        "typescript public sector dashboard reporting in:description stars:>50",
        "react typescript policy regulation compliance in:description stars:>50",
        "typescript digital government services accessibility in:description stars:>30",
    ],
    ProjectDomain.HEALTHCARE: [
        # Topic-based queries
        "topic:healthcare language:TypeScript stars:>50",
        "topic:ehr language:TypeScript",
        "topic:fhir language:TypeScript stars:>50",
        "topic:telemedicine language:TypeScript",
        "topic:health language:TypeScript stars:>100",
        "topic:openemr language:TypeScript",
        "topic:medical language:TypeScript stars:>50",
        "topic:clinical language:TypeScript stars:>50",
        "topic:patient language:TypeScript stars:>50",
        "topic:telehealth language:TypeScript",
        # Description-based queries
        "react typescript healthcare medical in:topics stars:>50",
        "react typescript patient portal in:description stars:>50",
        "react typescript hospital clinic medical-record in:description stars:>50",
        "react typescript pharmacy prescription in:description stars:>50",
        "react typescript mental-health wellness in:description stars:>50",
        # Framework-specific
        "nextjs typescript health patient in:description stars:>50",
        "react typescript genomics bioinformatics in:description stars:>50",
        "typescript hl7 fhir patient health in:name,description stars:>50",
        "react typescript appointment scheduling clinic in:description stars:>50",
        "typescript health dashboard analytics medical in:description stars:>50",
    ],
    ProjectDomain.EDUCATION: [
        # Topic-based queries
        "topic:education language:TypeScript stars:>100",
        "topic:edtech language:TypeScript",
        "topic:e-learning language:TypeScript stars:>100",
        "topic:lms language:TypeScript stars:>50",
        "topic:mooc language:TypeScript",
        "topic:classroom language:TypeScript stars:>50",
        "topic:moodle language:TypeScript",
        "topic:canvas language:TypeScript stars:>50",
        "topic:coding-education language:TypeScript",
        "topic:math language:TypeScript stars:>100",
        # Description-based queries
        "react typescript education lms in:topics stars:>100",
        "react typescript course platform learning in:description stars:>100",
        "react typescript quiz exam assessment in:topics stars:>50",
        "react typescript student teacher school in:description stars:>100",
        "react typescript online-learning elearning in:description stars:>100",
        # Framework-specific
        "nextjs typescript education course in:description stars:>100",
        "react typescript curriculum lesson content in:description stars:>100",
        "typescript vite learning platform educational in:description stars:>50",
        "react typescript flashcard vocabulary language in:description stars:>50",
        "typescript education gamification achievement in:description stars:>50",
    ],
    ProjectDomain.DEVELOPER_TOOLS: [
        # Topic-based queries
        "topic:developer-tools language:TypeScript stars:>200",
        "topic:design-system language:TypeScript stars:>100",
        "topic:storybook language:TypeScript stars:>100",
        "topic:code-editor language:TypeScript stars:>100",
        "topic:playground language:TypeScript stars:>100",
        "topic:api-client language:TypeScript stars:>100",
        "topic:devtools language:TypeScript stars:>200",
        "topic:vscode language:TypeScript stars:>200",
        "topic:component-library language:TypeScript stars:>200",
        "topic:monorepo language:TypeScript stars:>200",
        # Description-based queries
        "react typescript developer-tools ide in:topics stars:>200",
        "react component library typescript in:topics stars:>500",
        "react typescript cli devtools workspace in:description stars:>200",
        "react typescript debugger profiler trace in:description stars:>100",
        "react typescript api-explorer rest graphql in:description stars:>100",
        # Framework / niche specific
        "typescript nextjs admin panel crud in:description stars:>100",
        "react typescript schema editor form builder in:description stars:>100",
        "typescript openapi swagger client in:description stars:>100",
        "react typescript code snippet documentation in:description stars:>100",
        "typescript nx turborepo monorepo workspace in:description stars:>100",
    ],
    ProjectDomain.DASHBOARD: [
        # Topic-based queries
        "topic:dashboard language:TypeScript stars:>100",
        "topic:data-visualization language:TypeScript stars:>100",
        "topic:admin-dashboard language:TypeScript stars:>100",
        "topic:bi language:TypeScript stars:>100",
        "topic:grafana language:TypeScript stars:>100",
        "topic:superset language:TypeScript",
        "topic:analytics language:TypeScript stars:>200",
        "topic:charting language:TypeScript stars:>100",
        "topic:recharts language:TypeScript stars:>50",
        "topic:d3 language:TypeScript stars:>100",
        # Description-based queries
        "react typescript dashboard analytics in:topics stars:>100",
        "react typescript monitoring analytics in:description stars:>200",
        "react typescript reporting metrics kpi in:description stars:>100",
        "react typescript table grid chart in:topics stars:>100",
        "react typescript admin panel management in:description stars:>200",
        # Framework-specific
        "nextjs typescript dashboard admin in:description stars:>100",
        "react typescript real-time metrics websocket in:description stars:>100",
        "typescript tremor shadcn dashboard in:description stars:>50",
        "react typescript map geospatial visualization in:description stars:>100",
        "typescript observability logs tracing monitoring in:description stars:>100",
    ],
    ProjectDomain.SOCIAL: [
        # Topic-based queries
        "topic:messaging language:TypeScript stars:>100",
        "topic:collaboration language:TypeScript stars:>100",
        "topic:forum language:TypeScript stars:>100",
        "topic:community language:TypeScript stars:>100",
        "topic:slack-alternative language:TypeScript stars:>100",
        "topic:discord-clone language:TypeScript stars:>50",
        "topic:chat language:TypeScript stars:>100",
        "topic:social-network language:TypeScript stars:>100",
        "topic:stream language:TypeScript stars:>100",
        "topic:webrtc language:TypeScript stars:>100",
        # Description-based queries
        "react typescript chat messaging collaboration in:topics stars:>100",
        "react social network typescript in:topics stars:>100",
        "react typescript feed timeline post in:description stars:>100",
        "react typescript video-call webrtc in:topics stars:>100",
        "react typescript live stream broadcast in:description stars:>100",
        # Framework-specific
        "nextjs typescript social feed community in:description stars:>100",
        "react typescript matrix element chat in:description stars:>50",
        "typescript socket.io realtime chat in:description stars:>100",
        "react typescript comment thread discussion in:description stars:>100",
        "typescript nextjs blog cms content social in:description stars:>100",
    ],
}

# Target project counts per domain stratum.
# Total target ≈ 560; after ~25% IC4/IC6/IC7 failures → ~420 included (QM2: ≥ 400).
# Each domain ≈ 60-90 repos → max ≈ 90/420 ≈ 21% — close but domains with lower targets
# help keep max stratum ≤ 20% (QM3).
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

        Args:
            query: GitHub search query string.
            sort: Sort field ('stars', 'updated', 'forks').
            order: Sort direction ('desc', 'asc').
            per_page: Results per page (max 100).
            max_pages: Maximum pages to fetch.

        Returns:
            List of repository dicts from the GitHub API.
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
        max_retries: int = 4,
    ) -> dict[str, Any] | None:
        """
        HTTP GET with exponential backoff on rate-limit (403/429).

        Handles:
        - 200: Return parsed JSON
        - 403/429: Rate limited — use X-RateLimit-Reset or Retry-After header
        - 422: Invalid query — skip immediately (don't retry)
        - Network errors: Exponential backoff
        """
        for attempt in range(max_retries):
            try:
                response = self._client.get(url, params=params)

                if response.status_code == 200:
                    return response.json()

                if response.status_code in (403, 429):
                    # Try Retry-After header first (more accurate for secondary limits)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = int(retry_after) + 5
                        except ValueError:
                            wait = 60
                    else:
                        # Fall back to X-RateLimit-Reset timestamp
                        reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
                        now = int(time.time())
                        wait = max(reset_ts - now + 5, 60) if reset_ts > now else 60

                    print(
                        f"  ⏳ Rate limited (HTTP {response.status_code}). "
                        f"Waiting {wait}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(wait)
                    continue

                if response.status_code == 422:
                    # Unprocessable entity — invalid query syntax, skip
                    print(
                        f"  ⚠️  Invalid query (HTTP 422). Skipping.",
                        file=sys.stderr,
                    )
                    return None

                print(
                    f"  ⚠️  HTTP {response.status_code} for query. "
                    f"Body: {response.text[:200]}",
                    file=sys.stderr,
                )
                return None

            except httpx.RequestError as e:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s, 40s
                print(f"  ⚠️  Network error: {e}. Retrying in {wait}s")
                time.sleep(wait)

        print("  ❌ Max retries exceeded.", file=sys.stderr)
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
            print(f"  ⚠️  Warning: could not parse entry {raw.get('id', '?')}: {e}")

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

    print(f"  💾 Saved {len(entries)} entries to {path}")


def print_stats(entries: dict[str, ProjectEntry]) -> None:
    """
    Print catalog coverage statistics by domain vs. targets.

    Shows current count, target, gap, and QM2/QM3 compliance indicators.
    """
    total_target = sum(DOMAIN_TARGETS.values())
    total_current = len(entries)
    domain_counts: dict[ProjectDomain, int] = {d: 0 for d in DOMAIN_TARGETS}

    for entry in entries.values():
        if entry.domain in domain_counts:
            domain_counts[entry.domain] += 1

    print("\n♿  Catalog Coverage Report")
    print("═" * 60)
    print(f"  {'Domain':<20} {'Current':>8} {'Target':>8} {'Gap':>6}  {'Status'}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*6}  {'-'*12}")

    needs_topup: list[ProjectDomain] = []
    for domain in ProjectDomain:
        if domain == ProjectDomain.OTHER:
            continue
        target = DOMAIN_TARGETS.get(domain, 0)
        current = domain_counts.get(domain, 0)
        gap = max(0, target - current)
        pct = round(current / max(target, 1) * 100)
        bar = "✅" if current >= target else ("⚠️ " if pct >= 70 else "❌")
        print(f"  {domain.value:<20} {current:>8} {target:>8} {gap:>6}  {bar} ({pct}%)")
        if gap > 0:
            needs_topup.append(domain)

    print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*6}")
    total_gap = max(0, total_target - total_current)
    total_pct = round(total_current / max(total_target, 1) * 100)
    print(f"  {'TOTAL':<20} {total_current:>8} {total_target:>8} {total_gap:>6}")

    print()
    print(f"  QM2 (≥400 included):  {'✅' if total_current >= 400 else '❌'} ({total_current} / 400)")

    # QM3: max domain ≤ 20% of total
    if total_current > 0:
        max_domain = max(domain_counts.values())
        max_domain_name = max(domain_counts, key=lambda d: domain_counts[d])
        max_pct = round(max_domain / total_current * 100, 1)
        print(
            f"  QM3 (max domain ≤20%): {'✅' if max_pct <= 20 else '❌'} "
            f"({max_domain_name.value}: {max_pct}%)"
        )

    if needs_topup:
        print(f"\n  Domains below target: {', '.join(d.value for d in needs_topup)}")
        print(f"  Run with --top-up to fill gaps automatically.")
    else:
        print("\n  ✅ All domains at or above target!")
    print()


def discover_domain(
    client: GitHubDiscovery,
    domain: ProjectDomain,
    existing_ids: set[str],
    target: int,
    verbose: bool = False,
    sort: str = "stars",
) -> list[ProjectEntry]:
    """
    Discover and screen projects for a single domain stratum.

    Iterates over all domain queries in order. For each query, fetches up to
    5 pages of results and screens each repository against the inclusion criteria.
    Stops early once `target` new projects have been found.

    Each query is tried sorted by `sort` (default: 'stars'). If enough results
    are not found in the first pass, a second pass with sort='updated' is attempted
    to surface more recently active (but perhaps less starred) projects.

    Args:
        client: Authenticated GitHub API client.
        domain: Target domain stratum.
        existing_ids: Set of repo IDs already in the catalog (to skip duplicates).
        target: Number of NEW projects to find.
        verbose: Print detailed screening information.
        sort: Primary sort field ('stars' or 'updated').

    Returns:
        List of new ProjectEntry objects (length ≤ target).
    """
    queries = DOMAIN_QUERIES[domain]
    found: list[ProjectEntry] = []
    seen_ids: set[str] = set(existing_ids)

    # Two-pass strategy: primary sort first, then 'updated' for diversity
    sort_strategies = [sort] if sort != "stars" else ["stars", "updated"]

    for current_sort in sort_strategies:
        if len(found) >= target:
            break

        if current_sort != sort_strategies[0]:
            remaining_needed = target - len(found)
            print(
                f"    ℹ️  Switching to sort='{current_sort}' to find "
                f"{remaining_needed} more repos..."
            )

        for query in queries:
            if len(found) >= target:
                break

            if verbose:
                print(f"    🔍 Query [{current_sort}]: {query}")

            results = client.search(query, sort=current_sort, max_pages=5)
            new_in_query = 0
            skipped_duplicate = 0
            excluded = 0

            for repo in results:
                if len(found) >= target:
                    break

                repo_id = f"{repo['owner']['login']}__{repo['name']}"
                if repo_id in seen_ids:
                    skipped_duplicate += 1
                    continue
                seen_ids.add(repo_id)

                passes, screening = screen_repository(repo)
                if not passes:
                    excluded += 1
                    if verbose:
                        print(f"      ✗ {repo_id}: {screening.exclusion_reason}")
                    continue

                entry = repo_to_project_entry(repo, domain, screening)
                found.append(entry)
                new_in_query += 1
                print(f"      ✓ {repo_id} ({repo['stargazers_count']} ★)")

            print(
                f"    → {len(results)} raw | "
                f"+{new_in_query} new | "
                f"{skipped_duplicate} dupes | "
                f"{excluded} excluded | "
                f"query: {query[:55]}{'...' if len(query) > 55 else ''}"
            )

            # Respect secondary rate limit between queries
            time.sleep(2.0)

    return found


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover GitHub projects for the a11y-autofix benchmark corpus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover all domains up to targets
  python dataset/scripts/discover.py --token ghp_xxx

  # Show current catalog coverage without calling GitHub API
  python dataset/scripts/discover.py --stats

  # Fill only domains below target (requires token)
  python dataset/scripts/discover.py --token ghp_xxx --top-up

  # Discover a single domain (useful for debugging)
  python dataset/scripts/discover.py --token ghp_xxx --domain ecommerce --max 20

  # Preview queries without calling API
  python dataset/scripts/discover.py --dry-run
        """,
    )
    parser.add_argument(
        "--token",
        default="",
        help="GitHub Personal Access Token (required for API calls)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to catalog YAML file (default: dataset/catalog/projects.yaml)",
    )
    parser.add_argument(
        "--domain",
        choices=[d.value for d in ProjectDomain if d != ProjectDomain.OTHER],
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
        "--top-up",
        action="store_true",
        dest="top_up",
        help=(
            "Only search domains that are below their target count. "
            "Skips domains already at or above target. Requires --token."
        ),
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print catalog coverage statistics and exit (no API calls required)",
    )
    parser.add_argument(
        "--sort",
        choices=["stars", "updated", "forks"],
        default="stars",
        help="Primary sort field for GitHub search (default: stars)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print queries that would be executed without calling the GitHub API",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("\n♿  a11y-autofix Dataset Discovery\n" + "═" * 56)

    # Load existing catalog to avoid duplicates
    existing_entries, metadata = load_existing_catalog(args.output)
    print(f"  Existing catalog: {len(existing_entries)} projects\n")

    # ── Stats-only mode ───────────────────────────────────────────────────────
    if args.stats:
        print_stats(existing_entries)
        return

    # ── Dry-run mode ──────────────────────────────────────────────────────────
    if args.dry_run:
        print("[dry-run] Queries that would be executed:\n")
        for domain in ProjectDomain:
            if domain == ProjectDomain.OTHER:
                continue
            if args.domain and domain.value != args.domain:
                continue
            queries = DOMAIN_QUERIES[domain]
            target = args.max or DOMAIN_TARGETS.get(domain, 0)
            current = sum(1 for e in existing_entries.values() if e.domain == domain)
            gap = max(0, target - current)
            print(f"  [{domain.value}] {current}/{target} (gap: {gap})")
            for q in queries:
                print(f"    • {q}")
            print()
        return

    # ── API discovery mode ────────────────────────────────────────────────────
    if not args.token:
        print(
            "Error: --token is required for API discovery.\n"
            "       Use --stats or --dry-run to inspect the catalog without a token.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = GitHubDiscovery(args.token)
    all_new: list[ProjectEntry] = []

    # Determine which domains to search
    if args.domain:
        domains_to_search = [ProjectDomain(args.domain)]
    else:
        domains_to_search = [d for d in ProjectDomain if d != ProjectDomain.OTHER]

    for domain in domains_to_search:
        target = args.max or DOMAIN_TARGETS.get(domain, 5)
        current_count = sum(
            1 for e in existing_entries.values() if e.domain == domain
        )
        remaining = max(0, target - current_count)

        if remaining == 0:
            print(f"  [{domain.value}] ✅ Already at target ({current_count}/{target}). Skipping.")
            continue

        # In top-up mode, only search domains that are below target
        if args.top_up and remaining == 0:
            continue

        print(
            f"\n  [{domain.value.upper()}] "
            f"Searching for {remaining} more project(s) "
            f"(have {current_count}/{target})"
        )

        new = discover_domain(
            client,
            domain,
            existing_ids=set(existing_entries.keys()),
            target=remaining,
            verbose=args.verbose,
            sort=args.sort,
        )
        all_new.extend(new)
        print(f"  [{domain.value}] Found {len(new)} new projects")

    client.close()

    if not all_new:
        print("\n  ℹ️  No new projects discovered.")
        print_stats(existing_entries)
        return

    # Merge with existing catalog
    for entry in all_new:
        existing_entries[entry.id] = entry

    save_catalog(existing_entries, args.output, metadata)
    print(f"\n  ✓ Added {len(all_new)} new projects. Total: {len(existing_entries)}")

    # Print final stats
    print_stats(existing_entries)


if __name__ == "__main__":
    main()
