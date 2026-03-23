#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
expand_dataset.py — Pipeline completo para adicionar novos projetos e rebalancear o dataset.

Fases:
  1. DISCOVERY  — busca projetos no GitHub em domínios sub-representados
  2. SNAPSHOT   — clona e valida cada candidato (IC4, IC6, IC7)
  3. SCAN       — executa todos os scanners (playwright+axe, pa11y, eslint-jsx-a11y)
  4. CAP        — limita findings por critério WCAG (cap por projeto)
  5. MERGE      — reconstrói dataset_findings.jsonl consolidado
  6. REPORT     — exibe métricas de equilíbrio antes/depois

Uso rápido (tudo de uma vez):
    python dataset/scripts/expand_dataset.py --target 40 --workers 3

Por fase:
    python dataset/scripts/expand_dataset.py --discover-only --target 60
    python dataset/scripts/expand_dataset.py --snapshot-only
    python dataset/scripts/expand_dataset.py --scan-only --workers 4
    python dataset/scripts/expand_dataset.py --merge-only --cap 10

Token GitHub (obrigatório para discovery):
    export GITHUB_TOKEN=ghp_xxxx  # macOS/Linux
    $env:GITHUB_TOKEN="ghp_xxxx"  # Windows PowerShell
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
SNAPSHOTS_DIR = DATASET_ROOT / "snapshots"
RESULTS_DIR = DATASET_ROOT / "results"
CATALOG_PATH = DATASET_ROOT / "catalog" / "projects.yaml"

sys.path.insert(0, str(REPO_ROOT))

# ── ANSI colors ───────────────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"

OK = f"{GREEN}✔{R}"
FAIL = f"{RED}✘{R}"
INFO = f"{CYAN}ℹ{R}"

# ── Domínios alvo (sub-representados) ────────────────────────────────────────
# Domínio social está em 25.7% (>20% limite) — NÃO adicionar mais.
TARGET_DOMAINS = ["government", "healthcare", "education", "ecommerce"]

# Mínimo de stars (IC1)
MIN_STARS = 50

# Data de corte de atividade (IC2): 36 meses
ACTIVITY_CUTOFF = (datetime.now(tz=timezone.utc) - timedelta(days=1095)).strftime(
    "%Y-%m-%d"
)

# Licenças OSI aceitáveis (IC3)
ACCEPTABLE_LICENSES: frozenset[str] = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "MPL-2.0", "LGPL-2.1", "LGPL-3.0", "CC0-1.0",
})

STARTER_PATTERNS = re.compile(
    r"\b(starter|boilerplate|template|scaffold|create-|skeleton|demo|example|sample)\b", re.I
)
COURSE_PATTERNS = re.compile(
    r"\b(homework|course|tutorial|learning|bootcamp|assignment|practice|clone)\b", re.I
)

# ── Queries GitHub por domínio ────────────────────────────────────────────────
# Priorizadas para projetos com formulários, imagens, tabelas → mais WCAG P1/P3/P4
DOMAIN_QUERIES: dict[str, list[str]] = {
    "government": [
        "topic:government language:TypeScript stars:>50",
        "topic:civic-tech language:TypeScript stars:>50",
        "topic:govtech language:TypeScript stars:>30",
        "topic:open-data language:TypeScript stars:>50",
        "topic:transparency language:TypeScript stars:>50",
        "react typescript government portal accessibility in:description stars:>50",
        "react typescript civic public-sector form in:description stars:>50",
        "react typescript municipality city services in:description stars:>30",
        "nextjs typescript government portal dashboard in:description stars:>50",
        "react typescript voting election civic in:description stars:>50",
        "react typescript open-source public federal in:description stars:>30",
        "typescript government forms accessibility compliance in:description stars:>30",
        "react accessibility government services wcag in:description stars:>30",
    ],
    "healthcare": [
        "topic:healthcare language:TypeScript stars:>50",
        "topic:ehr language:TypeScript stars:>30",
        "topic:fhir language:TypeScript stars:>50",
        "topic:telemedicine language:TypeScript stars:>30",
        "topic:medical language:TypeScript stars:>50",
        "topic:patient language:TypeScript stars:>50",
        "react typescript healthcare patient portal in:description stars:>50",
        "react typescript hospital clinic medical-record form in:description stars:>50",
        "react typescript pharmacy prescription form in:description stars:>30",
        "nextjs typescript health patient appointment in:description stars:>50",
        "react typescript telehealth mental-health wellness in:description stars:>50",
        "typescript hl7 fhir patient health form in:name,description stars:>50",
        "react typescript medical dashboard analytics in:description stars:>50",
        "react accessibility wcag healthcare form table in:description stars:>30",
    ],
    "education": [
        "topic:education language:TypeScript stars:>100",
        "topic:edtech language:TypeScript stars:>50",
        "topic:e-learning language:TypeScript stars:>100",
        "topic:lms language:TypeScript stars:>50",
        "topic:mooc language:TypeScript stars:>50",
        "topic:classroom language:TypeScript stars:>50",
        "react typescript education lms platform in:topics stars:>100",
        "react typescript course assessment quiz form in:description stars:>100",
        "react typescript student teacher school table in:description stars:>100",
        "nextjs typescript education course content in:description stars:>100",
        "react typescript flashcard vocabulary form in:description stars:>50",
        "typescript education gamification achievement dashboard in:description stars:>50",
        "react accessibility wcag education form table in:description stars:>50",
    ],
    "ecommerce": [
        "topic:ecommerce language:TypeScript stars:>100",
        "topic:storefront language:TypeScript stars:>100",
        "topic:shopify language:TypeScript stars:>50",
        "topic:medusajs language:TypeScript stars:>50",
        "topic:woocommerce language:TypeScript stars:>50",
        "react typescript ecommerce storefront product in:topics stars:>100",
        "react typescript shopping-cart checkout form in:description stars:>100",
        "react typescript product catalog images alt in:description stars:>50",
        "nextjs typescript ecommerce shop store in:description stars:>100",
        "remix typescript ecommerce store cart in:name,description stars:>50",
        "react typescript payment stripe form accessibility in:description stars:>50",
        "typescript commerce headless storefront images in:description stars:>100",
    ],
}


# ─── Fase 1: Discovery ────────────────────────────────────────────────────────

def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _search_github(
    query: str,
    token: str,
    per_page: int = 30,
    max_pages: int = 3,
) -> list[dict]:
    """Busca repositórios no GitHub Search API com retry em rate-limit."""
    try:
        import httpx
    except ImportError:
        print(f"{FAIL} httpx não instalado. Execute: pip install httpx", file=sys.stderr)
        return []

    results: list[dict] = []
    url = "https://api.github.com/search/repositories"

    for page in range(1, max_pages + 1):
        params = {"q": query, "per_page": per_page, "page": page, "sort": "stars", "order": "desc"}
        for attempt in range(4):
            try:
                resp = httpx.get(url, headers=_github_headers(token), params=params, timeout=20)
                if resp.status_code == 403 or resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 30))
                    print(f"  {YELLOW}Rate limit — aguardando {wait}s...{R}")
                    time.sleep(wait)
                    continue
                if resp.status_code == 422:
                    # Query inválida
                    return results
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                results.extend(items)
                if len(items) < per_page:
                    return results
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  {FAIL} Erro na busca: {e}", file=sys.stderr)
                else:
                    time.sleep(2 ** attempt)

    return results


def _passes_initial_criteria(repo: dict) -> tuple[bool, str]:
    """Verifica IC1-IC5, EC1-EC4 na resposta da API."""
    name = repo.get("name", "")
    desc = repo.get("description") or ""
    stars = repo.get("stargazers_count", 0)
    lang = (repo.get("language") or "").lower()
    pushed = repo.get("pushed_at") or ""
    license_key = (repo.get("license") or {}).get("spdx_id", "NOASSERTION")
    is_fork = repo.get("fork", False)
    is_archived = repo.get("archived", False)

    # IC1: stars mínimas
    if stars < MIN_STARS:
        return False, f"IC1: {stars} stars < {MIN_STARS}"

    # IC2: atividade recente
    if pushed < ACTIVITY_CUTOFF:
        return False, f"IC2: inativo desde {pushed[:10]}"

    # IC3: licença aceitável
    if license_key not in ACCEPTABLE_LICENSES and license_key != "NOASSERTION":
        return False, f"IC3: licença {license_key}"

    # IC5: TypeScript ou JavaScript
    if lang not in ("typescript", "javascript", ""):
        return False, f"IC5: language={lang}"

    # EC1: não é starter/boilerplate
    if STARTER_PATTERNS.search(name) or STARTER_PATTERNS.search(desc):
        return False, "EC1: starter/template"

    # EC2: não é projeto de curso
    if COURSE_PATTERNS.search(name) or COURSE_PATTERNS.search(desc):
        return False, "EC2: course/tutorial"

    # EC3: não é fork
    if is_fork:
        return False, "EC3: fork"

    # EC4: não está arquivado
    if is_archived:
        return False, "EC4: archived"

    return True, "ok"


def discover_new_projects(
    token: str,
    domains: list[str],
    target: int,
    existing_ids: set[str],
) -> list[dict]:
    """
    Busca novos projetos no GitHub para os domínios especificados.
    Retorna lista de dicts com dados crus da API (prontos para criar ProjectEntry).
    """
    candidates: list[dict] = []
    seen_full_names: set[str] = set(existing_ids)

    print(f"\n{BOLD}═══ Fase 1: Discovery ══════════════════════════════════{R}")
    print(f"  Domínios alvo: {', '.join(domains)}")
    print(f"  Meta: {target} novos projetos\n")

    per_domain_target = max(1, (target + len(domains) - 1) // len(domains))

    for domain in domains:
        queries = DOMAIN_QUERIES.get(domain, [])
        domain_candidates: list[dict] = []

        print(f"  {CYAN}[{domain}]{R} buscando... (meta: {per_domain_target})")

        for query in queries:
            if len(domain_candidates) >= per_domain_target * 3:
                break
            items = _search_github(query, token, per_page=30, max_pages=2)
            time.sleep(1)  # respeitar rate limit

            for repo in items:
                full_name = repo.get("full_name", "")
                project_id = full_name.replace("/", "__")

                if project_id in seen_full_names:
                    continue
                if full_name in seen_full_names:
                    continue

                ok, reason = _passes_initial_criteria(repo)
                if not ok:
                    continue

                seen_full_names.add(project_id)
                seen_full_names.add(full_name)
                repo["_domain"] = domain
                repo["_project_id"] = project_id
                domain_candidates.append(repo)

        print(f"  {OK} {domain}: {len(domain_candidates)} candidatos encontrados")
        candidates.extend(domain_candidates[:per_domain_target * 2])

        if len(candidates) >= target * 2:
            break

    print(f"\n  Total candidatos: {len(candidates)} (serão processados até {target})")
    return candidates[:target * 2]  # margem para exclusões no snapshot


def candidates_to_entries(candidates: list[dict]) -> list[Any]:
    """Converte dados brutos da API em ProjectEntry para o catálogo."""
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
        ProjectScanSummary,
    )

    domain_map = {
        "government": ProjectDomain.GOVERNMENT,
        "healthcare": ProjectDomain.HEALTHCARE,
        "education": ProjectDomain.EDUCATION,
        "ecommerce": ProjectDomain.ECOMMERCE,
        "developer_tools": ProjectDomain.DEVELOPER_TOOLS,
        "dashboard": ProjectDomain.DASHBOARD,
        "social": ProjectDomain.SOCIAL,
    }

    entries = []
    for repo in candidates:
        pid = repo["_project_id"]
        domain_str = repo.get("_domain", "ecommerce")
        stars = repo.get("stargazers_count", 0)
        forks = repo.get("forks_count", 0)

        # Popularidade
        if stars >= 1000:
            pop = ProjectPopularity.POPULAR
        elif stars >= 200:
            pop = ProjectPopularity.ESTABLISHED
        else:
            pop = ProjectPopularity.EMERGING

        # IC1/IC2/IC3 já verificados; marcar como PASS
        screening = ScreeningRecord(
            ic1_stars=InclusionStatus.PASS,
            ic2_last_commit=InclusionStatus.PASS,
            ic3_license=InclusionStatus.PASS,
            ic5_buildability=InclusionStatus.NOT_CHECKED,  # confirmado no snapshot
        )

        github_meta = GitHubMetadata(
            stars=stars,
            forks=forks,
            open_issues=repo.get("open_issues_count", 0),
            watchers=repo.get("watchers_count", 0),
            language=repo.get("language", "TypeScript"),
            topics=repo.get("topics", []),
            license_spdx=(repo.get("license") or {}).get("spdx_id", ""),
            default_branch=repo.get("default_branch", "main"),
            created_at=repo.get("created_at", ""),
            pushed_at=repo.get("pushed_at", ""),
        )

        entry = ProjectEntry(
            id=pid,
            owner=repo.get("full_name", "").split("/")[0],
            repo=repo.get("name", ""),
            github_url=repo.get("html_url", f"https://github.com/{repo.get('full_name','')}"),
            domain=domain_map.get(domain_str, ProjectDomain.ECOMMERCE),
            size_category=ProjectSize.MEDIUM,  # ajustado no snapshot
            popularity_tier=pop,
            scan_paths=["src/", "app/", "apps/"],
            exclude_paths=["**/*.test.*", "**/*.spec.*", "**/__tests__/**", "**/node_modules/**"],
            status=ProjectStatus.CANDIDATE,
            inclusion_rationale=f"Added by expand_dataset.py — domain={domain_str}",
            github=github_meta,
            snapshot=SnapshotMetadata(),
            screening=screening,
            scan=ProjectScanSummary(),
        )
        entries.append(entry)
    return entries


# ─── Fase 2: Snapshot ─────────────────────────────────────────────────────────

def run_snapshot_phase(new_entries: list[Any]) -> list[Any]:
    """Clona e valida os novos projetos."""
    from dataset.scripts.snapshot import snapshot_project
    from dataset.schema.models import ProjectStatus

    print(f"\n{BOLD}═══ Fase 2: Snapshot ══════════════════════════════════{R}")
    print(f"  Projetos a clonar: {len(new_entries)}\n")

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshotted = []

    for i, entry in enumerate(new_entries, 1):
        print(f"  [{i}/{len(new_entries)}] {entry.id}")
        updated = snapshot_project(entry, force=False)
        if updated.status == ProjectStatus.SNAPSHOTTED:
            snapshotted.append(updated)
        else:
            print(f"    {FAIL} Excluído: {getattr(updated.screening, 'exclusion_reason', '?')}")
        time.sleep(0.5)

    print(f"\n  {OK} Snapshots bem-sucedidos: {len(snapshotted)}/{len(new_entries)}")
    return snapshotted


# ─── Fase 3: Scan ─────────────────────────────────────────────────────────────

async def run_scan_phase(
    entries: list[Any],
    workers: int,
    timeout: int,
) -> list[Any]:
    """Executa scan completo (todos os scanners) nos projetos snapshotados."""
    from dataset.scripts.scan import scan_project, issue_to_scan_finding
    from dataset.schema.models import ProjectStatus

    print(f"\n{BOLD}═══ Fase 3: Scan ══════════════════════════════════════{R}")
    print(f"  Projetos: {len(entries)} | Workers: {workers} | Timeout: {timeout}s\n")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(workers)
    results: list[Any] = []
    lock = asyncio.Lock()

    async def _scan_one(entry: Any) -> None:
        async with sem:
            updated, findings = await scan_project(
                entry,
                scan_timeout=timeout,
                min_consensus=1,
                force=False,
            )
            async with lock:
                results.append((updated, findings))

    await asyncio.gather(*[_scan_one(e) for e in entries])

    scanned = [r for r in results if r[0].status == ProjectStatus.SCANNED]
    print(f"\n  {OK} Escaneados: {len(scanned)}/{len(entries)}")
    return results


# ─── Fase 4: Rule Cap ─────────────────────────────────────────────────────────

def apply_rule_cap(project_dir: Path, cap: int) -> int:
    """
    Limita findings.jsonl a 'cap' findings por critério WCAG por projeto.
    Retorna número de findings removidos.
    """
    fp = project_dir / "findings.jsonl"
    if not fp.exists():
        return 0

    lines = [l.strip() for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_criterion: dict[str, list[str]] = defaultdict(list)
    no_criterion: list[str] = []

    for line in lines:
        try:
            d = json.loads(line)
            crit = d.get("wcag_criteria") or ""
            if crit:
                by_criterion[crit].append(line)
            else:
                no_criterion.append(line)
        except Exception:
            no_criterion.append(line)

    kept: list[str] = list(no_criterion)
    removed = 0
    for crit, crit_lines in by_criterion.items():
        kept.extend(crit_lines[:cap])
        removed += max(0, len(crit_lines) - cap)

    if removed > 0:
        fp.write_text("\n".join(kept) + "\n", encoding="utf-8")

    return removed


def run_cap_phase(cap: int, new_project_ids: list[str] | None = None) -> int:
    """
    Aplica rule cap nos projetos indicados (ou em todos se new_project_ids=None).
    Retorna total de findings removidos.
    """
    print(f"\n{BOLD}═══ Fase 4: Rule Cap (máx {cap} por critério) ══════════{R}")

    if new_project_ids:
        dirs = [RESULTS_DIR / pid for pid in new_project_ids if (RESULTS_DIR / pid).is_dir()]
    else:
        dirs = [d for d in sorted(RESULTS_DIR.iterdir()) if d.is_dir()]

    total_removed = 0
    for proj_dir in dirs:
        removed = apply_rule_cap(proj_dir, cap)
        if removed:
            print(f"  {YELLOW}cap{R} {proj_dir.name}: -{removed} findings")
        total_removed += removed

    print(f"  {OK} Total removido pelo cap: {total_removed}")
    return total_removed


# ─── Fase 5: Merge / Rebuild ──────────────────────────────────────────────────

def rebuild_dataset(new_project_ids: list[str] | None = None) -> tuple[int, dict]:
    """
    Reconstrói dataset_findings.jsonl e retorna (total, stats).
    Se new_project_ids fornecido, ADICIONA apenas esses projetos ao consolidado existente.
    Se None, regera do zero.
    """
    print(f"\n{BOLD}═══ Fase 5: Merge/Rebuild ══════════════════════════════{R}")

    out_path = RESULTS_DIR / "dataset_findings.jsonl"

    if new_project_ids is not None:
        # Modo incremental: adiciona novos ao final (evita re-ler tudo)
        existing_ids: set[str] = set()
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        existing_ids.add(d.get("finding_id", ""))
                    except Exception:
                        pass

        added = 0
        with open(out_path, "a", encoding="utf-8") as out:
            for pid in new_project_ids:
                fp = RESULTS_DIR / pid / "findings.jsonl"
                if fp.exists():
                    for line in fp.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line:
                            try:
                                d = json.loads(line)
                                fid = d.get("finding_id", "")
                                if fid not in existing_ids:
                                    out.write(line + "\n")
                                    existing_ids.add(fid)
                                    added += 1
                            except Exception:
                                pass

        total = sum(1 for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip())
        print(f"  {OK} +{added} novos findings adicionados ({total} total)")
    else:
        # Modo full rebuild
        total = 0
        with open(out_path, "w", encoding="utf-8") as out:
            for proj_dir in sorted(RESULTS_DIR.iterdir()):
                if not proj_dir.is_dir():
                    continue
                fp = proj_dir / "findings.jsonl"
                if fp.exists():
                    for line in fp.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line:
                            out.write(line + "\n")
                            total += 1
        print(f"  {OK} Rebuilt: {total} findings total")

    # Recalcular dataset_stats.json
    stats = _compute_stats(out_path)
    stats_path = RESULTS_DIR / "dataset_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {OK} dataset_stats.json atualizado")

    return total, stats


def _compute_stats(findings_path: Path) -> dict:
    """Calcula estatísticas agregadas do dataset_findings.jsonl."""
    by_type: dict = defaultdict(int)
    by_principle: dict = defaultdict(int)
    by_criterion: dict = defaultdict(int)
    by_tool: dict = defaultdict(int)
    by_impact: dict = defaultdict(int)
    projects: set = set()
    total = 0
    high = 0

    PRIN = {"1": "perceivable", "2": "operable", "3": "understandable", "4": "robust"}

    for line in findings_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            total += 1
            if d.get("confidence") == "high":
                high += 1
            itype = d.get("issue_type", "other")
            by_type[itype] += 1
            crit = d.get("wcag_criteria") or ""
            if crit:
                by_criterion[crit] += 1
                p = PRIN.get(crit.split(".")[0], "unknown")
                by_principle[p] += 1
            else:
                by_principle["unknown"] += 1
            for t in (d.get("found_by") or []):
                by_tool[str(t)] += 1
            impact = d.get("impact") or "moderate"
            by_impact[impact] += 1
            pid = d.get("project_id", "")
            if pid:
                projects.add(pid)
        except Exception:
            pass

    return {
        "total_projects_scanned": len(projects),
        "total_issues": total,
        "high_confidence_issues": high,
        "high_conf_rate_pct": round(high / total * 100, 1) if total else 0,
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_principle": dict(by_principle),
        "by_criterion_top20": dict(sorted(by_criterion.items(), key=lambda x: -x[1])[:20]),
        "by_tool": dict(by_tool),
        "by_impact": dict(by_impact),
        "scan_date": datetime.now(tz=timezone.utc).isoformat(),
    }


# ─── Fase 6: Relatório de Balanço ─────────────────────────────────────────────

def print_balance_report(stats: dict, new_count: int = 0) -> None:
    """Imprime métricas de equilíbrio do dataset."""
    total = stats.get("total_issues", 0)
    projects = stats.get("total_projects_scanned", 0)

    print(f"\n{BOLD}═══ Relatório de Equilíbrio ════════════════════════════{R}")
    print(f"  Projetos escaneados : {CYAN}{projects}{R}")
    print(f"  Total de findings   : {CYAN}{total:,}{R}")
    if new_count:
        print(f"  Novos nesta rodada  : {GREEN}+{new_count}{R}")

    # Princípios WCAG
    print(f"\n  {BOLD}WCAG Principles:{R}")
    by_p = stats.get("by_principle", {})
    for p, n in sorted(by_p.items(), key=lambda x: -x[1]):
        pct = n / total * 100 if total else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        color = RED if pct > 90 else (YELLOW if pct > 60 else GREEN)
        print(f"    {p:<15} {color}{bar}{R}  {n:>6,}  ({pct:.1f}%)")

    # Tipos
    print(f"\n  {BOLD}Issue Types (top 8):{R}")
    by_t = stats.get("by_type", {})
    for itype, n in list(sorted(by_t.items(), key=lambda x: -x[1]))[:8]:
        pct = n / total * 100 if total else 0
        color = RED if pct > 90 else (YELLOW if pct > 50 else GREEN)
        print(f"    {itype:<20} {color}{n:>6,}{R}  ({pct:.1f}%)")

    # Ferramentas
    print(f"\n  {BOLD}Por Ferramenta:{R}")
    by_tool = stats.get("by_tool", {})
    for tool, n in sorted(by_tool.items(), key=lambda x: -x[1]):
        pct = n / total * 100 if total else 0
        print(f"    {tool:<28} {CYAN}{n:>6,}{R}  ({pct:.1f}%)")

    # Top WCAG criteria
    print(f"\n  {BOLD}Top 10 Critérios WCAG:{R}")
    by_c = stats.get("by_criterion_top20", {})
    for crit, n in list(by_c.items())[:10]:
        pct = n / total * 100 if total else 0
        color = RED if pct > 50 else (YELLOW if pct > 20 else GREEN)
        print(f"    {crit:<12} {color}{n:>6,}{R}  ({pct:.1f}%)")

    # Avaliação
    print(f"\n  {BOLD}Avaliação:{R}")
    p_perceivable = by_p.get("perceivable", 0) / total * 100 if total else 0
    p_operable = by_p.get("operable", 0) / total * 100 if total else 0
    p_understandable = by_p.get("understandable", 0) / total * 100 if total else 0
    p_robust = by_p.get("robust", 0) / total * 100 if total else 0

    checks = [
        ("≥ 4 princípios WCAG cobertos", len([v for v in by_p.values() if v > 0]) >= 4),
        ("Perceivable > 5%", p_perceivable > 5),
        ("Operable < 85%", p_operable < 85),
        ("Understandable > 2%", p_understandable > 2),
        ("Robust > 1%", p_robust > 1),
        ("Multi-tool findings > 0", sum(1 for t, n in by_tool.items() if n > 0) > 1),
    ]
    all_pass = all(v for _, v in checks)
    for label, passed in checks:
        icon = OK if passed else FAIL
        print(f"    {icon}  {label}")

    if all_pass:
        print(f"\n  {GREEN}{BOLD}✔ Dataset balanceado!{R}")
    else:
        print(f"\n  {YELLOW}{BOLD}⚠ Dataset ainda desequilibrado — continue adicionando projetos.{R}")


# ─── Catálogo ──────────────────────────────────────────────────────────────────

def load_catalog():
    import yaml
    from dataset.schema.models import ProjectEntry
    if not CATALOG_PATH.exists():
        return [], {}
    with open(CATALOG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = []
    for raw in data.get("projects", []):
        try:
            entries.append(ProjectEntry(**raw))
        except Exception as e:
            pass
    return entries, data.get("metadata", {})


def save_catalog(entries: list, metadata: dict) -> None:
    import yaml
    from datetime import datetime, timezone
    output: dict[str, Any] = {
        "projects": [e.to_catalog_dict() for e in entries],
        "metadata": {
            **metadata,
            "last_modified": datetime.now(tz=timezone.utc).date().isoformat(),
            "total_projects": len(entries),
        },
    }
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expande o dataset com novos projetos de domínios sub-representados.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--target", type=int, default=40,
        help="Número de novos projetos a adicionar (default: 40).",
    )
    parser.add_argument(
        "--domains", nargs="+",
        choices=TARGET_DOMAINS,
        default=TARGET_DOMAINS,
        help="Domínios a buscar (default: todos os sub-representados).",
    )
    parser.add_argument(
        "--workers", type=int, default=3,
        help="Workers paralelos no scan (default: 3).",
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Timeout por arquivo em segundos (default: 120).",
    )
    parser.add_argument(
        "--cap", type=int, default=10,
        help="Máximo de findings por critério WCAG por projeto (default: 10, 0=sem cap).",
    )
    parser.add_argument(
        "--token", default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub API token (ou set GITHUB_TOKEN env var).",
    )
    # Flags de fase individual
    parser.add_argument("--discover-only", action="store_true", help="Só fase de discovery.")
    parser.add_argument("--snapshot-only", action="store_true", help="Só fase de snapshot (candidatos já no catálogo).")
    parser.add_argument("--scan-only", action="store_true", help="Só fase de scan (snapshots já prontos).")
    parser.add_argument("--merge-only", action="store_true", help="Só reconstrói dataset_findings.jsonl.")
    parser.add_argument("--cap-only", action="store_true", help="Só aplica rule cap (todos os projetos).")
    parser.add_argument("--no-cap", action="store_true", help="Pular fase de rule cap.")
    parser.add_argument("--full-rebuild", action="store_true", help="Rebuild completo do dataset_findings.jsonl.")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═' * 56}{R}")
    print(f"{BOLD}  ♿  a11y-autofix — Expand Dataset{R}")
    print(f"{BOLD}{'═' * 56}{R}")
    print(f"  {DIM}Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}{R}\n")

    from dataset.schema.models import ProjectStatus

    # ── Só cap ────────────────────────────────────────────────────────────────
    if args.cap_only:
        run_cap_phase(cap=args.cap)
        total, stats = rebuild_dataset()
        print_balance_report(stats)
        return

    # ── Só merge/rebuild ──────────────────────────────────────────────────────
    if args.merge_only or args.full_rebuild:
        total, stats = rebuild_dataset(new_project_ids=None)
        print_balance_report(stats)
        return

    # ── Só scan ───────────────────────────────────────────────────────────────
    if args.scan_only:
        entries, metadata = load_catalog()
        targets = [e for e in entries if e.status == ProjectStatus.SNAPSHOTTED]
        if not targets:
            print(f"  {INFO} Nenhum projeto com status SNAPSHOTTED encontrado.")
            return
        print(f"  {INFO} {len(targets)} projetos snapshotados para escanear.")
        results = asyncio.run(run_scan_phase(targets, workers=args.workers, timeout=args.timeout))
        scanned_ids = [r[0].id for r in results if r[0].status == ProjectStatus.SCANNED]
        # Salvar catálogo
        entry_index = {e.id: e for e in entries}
        for updated, _ in results:
            entry_index[updated.id] = updated
        save_catalog(list(entry_index.values()), metadata)
        if not args.no_cap and args.cap > 0:
            run_cap_phase(cap=args.cap, new_project_ids=scanned_ids)
        total, stats = rebuild_dataset(new_project_ids=scanned_ids if not args.full_rebuild else None)
        print_balance_report(stats, new_count=len(scanned_ids))
        return

    # ── Só snapshot ───────────────────────────────────────────────────────────
    if args.snapshot_only:
        entries, metadata = load_catalog()
        targets = [e for e in entries if e.status == ProjectStatus.CANDIDATE]
        if not targets:
            print(f"  {INFO} Nenhum candidato encontrado no catálogo.")
            return
        print(f"  {INFO} {len(targets)} candidatos para snapshot.")
        snapshotted = run_snapshot_phase(targets)
        entry_index = {e.id: e for e in entries}
        for updated in snapshotted:
            entry_index[updated.id] = updated
        save_catalog(list(entry_index.values()), metadata)
        print(f"\n  {OK} Catálogo atualizado: {CATALOG_PATH}")
        return

    # ── Pipeline completo (ou --discover-only) ────────────────────────────────
    # ── 1. Carregar catálogo e saber quais repos já existem ───────────────────
    entries, metadata = load_catalog()
    existing_ids: set[str] = {e.id for e in entries}
    existing_count = sum(1 for e in entries if e.status == ProjectStatus.SCANNED)
    print(f"  Catálogo atual: {len(entries)} projetos ({existing_count} escaneados)")

    # ── 2. Discovery ──────────────────────────────────────────────────────────
    if not args.token:
        print(f"\n  {FAIL} GitHub token necessário para discovery.")
        print(f"  Set: export GITHUB_TOKEN=ghp_xxx  (macOS/Linux)")
        print(f"  Set: $env:GITHUB_TOKEN='ghp_xxx'  (Windows PowerShell)")
        print(f"\n  Para pular discovery e usar candidatos já no catálogo:")
        print(f"  python dataset/scripts/expand_dataset.py --snapshot-only")
        sys.exit(1)

    raw_candidates = discover_new_projects(
        token=args.token,
        domains=args.domains,
        target=args.target,
        existing_ids=existing_ids,
    )

    if not raw_candidates:
        print(f"\n  {FAIL} Nenhum candidato encontrado. Verifique o token e tente novamente.")
        sys.exit(1)

    # Converter para ProjectEntry e adicionar ao catálogo
    new_entries = candidates_to_entries(raw_candidates)
    entry_index = {e.id: e for e in entries}
    really_new = [e for e in new_entries if e.id not in entry_index]
    for e in really_new:
        entry_index[e.id] = e
    save_catalog(list(entry_index.values()), metadata)
    print(f"\n  {OK} {len(really_new)} novos candidatos adicionados ao catálogo")

    if args.discover_only:
        print(f"\n  Próximo passo:")
        print(f"  python dataset/scripts/expand_dataset.py --snapshot-only")
        return

    # ── 3. Snapshot ───────────────────────────────────────────────────────────
    snapshotted = run_snapshot_phase(really_new)
    entry_index = {e.id: e for e in list(entry_index.values())}
    for updated in snapshotted:
        entry_index[updated.id] = updated
    save_catalog(list(entry_index.values()), metadata)

    if not snapshotted:
        print(f"\n  {FAIL} Nenhum projeto passou nos critérios de snapshot.")
        sys.exit(0)

    # ── 4. Scan ───────────────────────────────────────────────────────────────
    results = asyncio.run(run_scan_phase(snapshotted, workers=args.workers, timeout=args.timeout))
    for updated, _ in results:
        entry_index[updated.id] = updated
    save_catalog(list(entry_index.values()), metadata)

    scanned_ids = [
        r[0].id for r in results if r[0].status == ProjectStatus.SCANNED
    ]
    new_findings_count = sum(len(r[1]) for r in results)
    print(f"\n  {OK} Novos findings brutos: {new_findings_count:,}")

    # ── 5. Rule cap ───────────────────────────────────────────────────────────
    if not args.no_cap and args.cap > 0:
        run_cap_phase(cap=args.cap, new_project_ids=scanned_ids)

    # ── 6. Merge ──────────────────────────────────────────────────────────────
    total, stats = rebuild_dataset(new_project_ids=scanned_ids)

    # ── 7. Relatório ──────────────────────────────────────────────────────────
    print_balance_report(stats, new_count=len(scanned_ids))

    print(f"\n{BOLD}{'═' * 56}{R}")
    print(f"  {GREEN}{BOLD}Pipeline concluído!{R}")
    print(f"  Projetos adicionados : {GREEN}{len(scanned_ids)}{R}")
    print(f"  Catálogo             : {CATALOG_PATH}")
    print(f"  Findings consolidados: {RESULTS_DIR / 'dataset_findings.jsonl'}")
    print(f"{BOLD}{'═' * 56}{R}\n")


if __name__ == "__main__":
    main()
