# a11y-autofix Project Memory

## Project Overview
Mestrado research project: a11y-autofix benchmark dataset for accessibility auto-fix experiment.
Root: `/Users/joaowieland/Documents/mestrado/a11y_agent_experiment/a11y-autofix/`

## Dataset Collection Pipeline
Pipeline script: `collect.sh` — runs 6 phases: discover → snapshot → scan → annotate → validate → profile
- Catalog: `dataset/catalog/projects.yaml`
- Snapshots: `dataset/snapshots/<owner>__<repo>/`
- Scripts: `dataset/scripts/*.py`

## Key Scripts
- `discover.py` — GitHub search, produces projects.yaml entries
- `snapshot.py` — shallow-clones repos, pins commit SHA, applies IC4/IC6/IC7 checks
- `scan.py` — runs pa11y/axe/lighthouse on snapshots
- `validate.py` — checks QM1-QM8 quality metrics
- Schema: `dataset/schema/models.py` — Pydantic models

## QM2 Target: 400 included repos
`DOMAIN_TARGETS` in discover.py set to ~560 total (90/60/60/80/90/90/90 per domain).
After ~25% IC failures → ~420 included. QM3 constraint: max 20% per domain.

## Bugs Fixed (2026-03-02)
1. **snapshot.py TimeoutExpired crash**: `run_git()` now catches `TimeoutExpired`, returns `(-1, "", msg)` — no more unhandled exception
2. **Existing directory on re-run**: Clone failure "already exists" now reuses existing valid git repo instead of marking ERROR
3. **Catalog never saved on crash**: Now saves after EACH project (not just at end)
4. **IC4 false failures (wrong scan_paths)**: Added `find_best_scan_paths()` that tries 10 common alternatives if `src/` gives < 10 tsx/jsx files
5. **collect.sh ignores run_phase failures**: All `run_phase` calls now use `|| { warn ...; return 1; }`
6. **discover.py targets too low**: Was 50 total, now 560 total across 7 domains (10 queries/domain, max_pages=5)

## Status After Fixes (as of 2026-03-02)
- 50 projects in catalog, all still `pending` (snapshot crashes never saved)
- Need to re-run `collect.sh` to snapshot existing 50, then discover 500+ more
