# a11y-autofix Benchmark Dataset

A stratified, multi-domain corpus of real-world web accessibility violations for
evaluating automated detection and remediation systems. The dataset is designed
for academic research and follows a rigorous methodology grounded in published
benchmark-construction literature.

> **Full methodology**: [`PROTOCOL.md`](PROTOCOL.md)

---

## Contents

```
dataset/
├── PROTOCOL.md              # Academic construction protocol + validity framework
├── README.md                # This file
├── catalog/
│   └── projects.yaml        # Master project catalog (all entries + metadata)
├── schema/
│   ├── __init__.py
│   └── models.py            # Pydantic v2 data models for all records
├── scripts/
│   ├── __init__.py
│   ├── discover.py          # GitHub search + automated screening
│   ├── snapshot.py          # Shallow clone + commit pinning
│   ├── scan.py              # Accessibility scanning (pa11y / axe / Lighthouse)
│   ├── annotate.py          # Ground-truth annotation + Cohen's κ
│   ├── validate.py          # Quality metric validation (QM1–QM8)
│   ├── analyze.py           # Statistical analysis + LaTeX table export
│   └── catalog.py           # Catalog management CLI
├── snapshots/               # Git-pinned repository clones (auto-created)
│   └── <project-id>/
└── results/                 # Per-project scan + annotation output (auto-created)
    ├── <project-id>/
    │   ├── scan_results.json
    │   ├── summary.json
    │   ├── findings.jsonl
    │   └── ground_truth.jsonl
    ├── dataset_findings.jsonl   # Consolidated scan findings
    ├── ground_truth_all.jsonl   # Consolidated confirmed findings
    └── dataset_stats.json       # Aggregate statistics
```

---

## Research Questions

| ID  | Research Question |
|-----|-------------------|
| RQ1 | What is the distribution of WCAG violations across application domains? |
| RQ2 | How does tool-consensus rate relate to false-positive rate? |
| RQ3 | Which WCAG criteria exhibit the highest violation frequency? |
| RQ4 | Does finding complexity correlate with automated-fix success rate? |
| RQ5 | What is the comparative precision/recall of each detection tool? |

---

## Quick Start

### Prerequisites

```bash
pip install -e .          # Install a11y-autofix (from repo root)
pip install httpx pyyaml  # Dataset script dependencies

# GitHub API token (recommended, avoids rate limits)
export GITHUB_TOKEN=ghp_...
```

### Full Pipeline

```
discover → snapshot → scan → annotate → validate → analyze
```

#### 1. Discover projects from GitHub

```bash
python dataset/scripts/discover.py \
    --token $GITHUB_TOKEN \
    --output dataset/catalog/projects.yaml \
    --max 10
```

Searches GitHub using domain-specific queries, applies inclusion/exclusion
criteria IC1–IC7, and appends qualifying repositories to the catalog.

#### 2. Snapshot repositories

```bash
python dataset/scripts/snapshot.py \
    --catalog dataset/catalog/projects.yaml \
    --workers 4
```

Performs shallow git clones into `dataset/snapshots/<project-id>/` and pins each
snapshot to a specific commit SHA for full reproducibility.

To verify existing snapshots:

```bash
python dataset/scripts/snapshot.py --verify-only
```

#### 3. Scan for accessibility violations

```bash
python dataset/scripts/scan.py \
    --catalog dataset/catalog/projects.yaml \
    --workers 2 \
    --timeout 600
```

Runs pa11y, axe-core, Lighthouse, and Playwright+axe on every snapshotted
project. Outputs per-project `findings.jsonl` and a consolidated
`dataset_findings.jsonl`.

#### 4. Annotate ground truth

```bash
# Pass 1 – annotator Alice
python dataset/scripts/annotate.py \
    --annotator alice \
    --pass 1

# Pass 2 – annotator Bob
python dataset/scripts/annotate.py \
    --annotator bob \
    --pass 2 \
    --catalog dataset/catalog/projects.yaml
```

High-confidence, multi-tool findings are auto-accepted. Single-tool findings
are presented in an interactive terminal session.

Compute inter-annotator agreement:

```bash
python dataset/scripts/annotate.py --compute-kappa
```

Consolidate all confirmed findings:

```bash
python dataset/scripts/annotate.py --consolidate
```

#### 5. Validate quality metrics

```bash
python dataset/scripts/validate.py \
    --catalog dataset/catalog/projects.yaml
```

Checks all 8 quality metrics (QM1–QM8) against the thresholds defined in
`PROTOCOL.md §5`. Use `--strict` to exit with code 1 on any failure (CI use):

```bash
python dataset/scripts/validate.py --strict --json --output reports/validation.json
```

#### 6. Analyse and export

```bash
# Full statistical report
python dataset/scripts/analyze.py \
    --output-dir reports/

# Specific analyses only
python dataset/scripts/analyze.py --analysis A1 A3 A9

# With LaTeX table export
python dataset/scripts/analyze.py --output-dir reports/ --latex
```

---

## Catalog Management

The `catalog.py` CLI provides subcommands for day-to-day catalog operations:

```bash
# Validate YAML schema
python dataset/scripts/catalog.py validate

# Distribution statistics
python dataset/scripts/catalog.py stats

# Inspect a single project
python dataset/scripts/catalog.py show saleor__storefront
python dataset/scripts/catalog.py show saleor__storefront --json

# Add a new project
python dataset/scripts/catalog.py add grafana grafana \
    --domain dashboard \
    --scan-paths "public/app" \
    --rationale "High-traffic analytics dashboard, strong a11y signal"

# Bulk status update
python dataset/scripts/catalog.py update-status \
    --from snapshotted --to scanned

# Export to CSV
python dataset/scripts/catalog.py export \
    --format csv --output catalog.csv

# Check GitHub URL reachability
python dataset/scripts/catalog.py check-urls --token $GITHUB_TOKEN

# Diff against a saved reference
cp dataset/catalog/projects.yaml /tmp/catalog_baseline.yaml
# ... make changes ...
python dataset/scripts/catalog.py diff /tmp/catalog_baseline.yaml
```

---

## Data Models

All records are serialised as [Pydantic v2](https://docs.pydantic.dev/) models
defined in `dataset/schema/models.py`.

### ProjectEntry (catalog unit)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | `owner__repo` primary key |
| `owner` | `str` | GitHub owner |
| `repo` | `str` | GitHub repo name |
| `github_url` | `str` | Canonical URL |
| `domain` | `ProjectDomain` | Application domain (7 classes) |
| `status` | `ProjectStatus` | Pipeline stage |
| `size_category` | `ProjectSize` | small / medium / large |
| `popularity_tier` | `ProjectPopularity` | emerging / established / popular |
| `scan_paths` | `list[str]` | Directories to scan |
| `screening` | `ScreeningRecord` | IC/EC evaluation results |
| `snapshot` | `SnapshotMetadata` | Clone + commit metadata |
| `scan` | `ProjectScanSummary` | Aggregated scan statistics |
| `annotation_summary` | `AnnotationAgreement` | κ + FP rate |

### ScanFinding (raw detector output)

| Field | Type | Description |
|-------|------|-------------|
| `finding_id` | `str` | SHA-256 content hash |
| `project_id` | `str` | Parent project |
| `file` | `str` | Source file path |
| `selector` | `str` | CSS selector |
| `wcag_criteria` | `str` | E.g. `"1.4.3"` |
| `issue_type` | `str` | Normalised type slug |
| `impact` | `str` | critical / serious / moderate / minor |
| `complexity` | `str` | trivial / simple / moderate / complex |
| `tool_consensus` | `int` | Number of tools that flagged this |
| `found_by` | `list[str]` | Tool names |
| `confidence` | `str` | high / medium / low |

### GroundTruthFinding (annotated record)

Extends ScanFinding with:

| Field | Type | Description |
|-------|------|-------------|
| `auto_accepted` | `bool` | Skipped human review (consensus ≥ 2, high confidence) |
| `ground_truth_label` | `AnnotationLabel` | confirmed / false_positive / uncertain |
| `annotator_1_label` | `AnnotationLabel` | Pass-1 label |
| `annotator_1_id` | `str` | Pass-1 annotator ID |
| `annotator_2_label` | `AnnotationLabel \| None` | Pass-2 label (optional) |
| `annotator_2_id` | `str \| None` | Pass-2 annotator ID |
| `agreement` | `bool \| None` | Whether both annotators agreed |
| `annotation_notes` | `str \| None` | Free-text notes |

---

## Quality Metrics

| ID  | Metric | Threshold |
|-----|--------|-----------|
| QM1 | Corpus size | ≥ 20 projects with ≥ 1 confirmed finding |
| QM2 | Domain coverage | All 7 domains represented |
| QM3 | WCAG principle coverage | All 4 principles with ≥ 10 confirmed findings |
| QM4 | Issue type coverage | ≥ 5 distinct issue types |
| QM5 | False-positive rate | ≤ 30 % overall |
| QM6 | Inter-annotator agreement | Mean Cohen's κ ≥ 0.70 |
| QM7 | Snapshot integrity | 100 % of snapshots SHA-verified |
| QM8 | High-confidence ratio | ≥ 40 % of confirmed findings are high-confidence |

---

## Stratification Design

The corpus targets balanced coverage across three independent dimensions:

| Dimension | Strata |
|-----------|--------|
| **Domain** | e-commerce, government, healthcare, education, developer-tools, dashboard, social |
| **Size** | small (10–50 TSX files), medium (51–300), large (> 300) |
| **Popularity** | emerging (100–999 ★), established (1 K–10 K ★), popular (≥ 10 K ★) |

Target corpus size: **50 projects** (≥ 5 per domain stratum, ≥ 3 per size
class, ≥ 3 per popularity tier).

---

## Inclusion / Exclusion Criteria

### Inclusion Criteria

| ID  | Criterion | Method |
|-----|-----------|--------|
| IC1 | ≥ 100 GitHub stars | API field `stargazers_count` |
| IC2 | ≥ 1 commit in last 18 months | API field `pushed_at` |
| IC3 | React or Next.js codebase | `package.json` dependency check |
| IC4 | ≥ 10 TSX/JSX component files | File tree count |
| IC5 | Public repository | API field `private == false` |
| IC6 | Non-generated UI code | Heuristic scan for auto-gen headers |
| IC7 | Contains rendered UI components | Exports from `*.tsx` files |

### Exclusion Criteria

| ID  | Criterion |
|-----|-----------|
| EC1 | Starter template / boilerplate |
| EC2 | Monorepo with no directly scannable UI package |
| EC3 | Archived repository |
| EC4 | Fork of another included project |
| EC5 | Primary language not JavaScript/TypeScript |
| EC6 | Less than 6 months of commit history |
| EC7 | Predominantly auto-generated UI (> 70 % generated files) |

---

## Reproducibility

The dataset is fully reproducible from the catalog snapshot:

1. All repositories are pinned to a specific git commit SHA (`pinned_commit`).
2. Scan configuration is versioned (`scan_tool_versions`, `scan_date`).
3. The catalog YAML is the single source of truth; all downstream artefacts are
   derived deterministically from it.
4. Random seeds are fixed in all sampling operations.
5. The `dataset/PROTOCOL.md` documents every decision with its rationale.

To regenerate all artefacts from scratch:

```bash
python dataset/scripts/snapshot.py --catalog dataset/catalog/projects.yaml
python dataset/scripts/scan.py     --catalog dataset/catalog/projects.yaml
python dataset/scripts/annotate.py --auto-accept-only
python dataset/scripts/validate.py --strict
python dataset/scripts/analyze.py  --output-dir reports/
```

---

## Citing This Dataset

If you use this dataset in your research, please cite:

```bibtex
@dataset{a11y_autofix_dataset_2025,
  title   = {a11y-autofix Benchmark Dataset: A Stratified Corpus of
             Web Accessibility Violations in Real-World React Applications},
  author  = {Wieland, João and others},
  year    = {2025},
  url     = {https://github.com/your-org/a11y-autofix},
  note    = {Constructed following the methodology of Alshayban et al.
             (ICSE 2020) and evaluated against W3C WCAG-EM.}
}
```

---

## References

1. Alshayban, A., Ahmed, I., & Malek, S. (2020). Accessibility Issues in Android Apps.
   *ICSE 2020*, pp. 1323–1334.
2. Bajammal, M., & Mesbah, A. (2021). Semantic Web Accessibility Testing via
   Hierarchical Visual Analysis. *W4A 2021*.
3. Just, R., et al. (2014). Defects4J: A Database of Existing Faults.
   *ISSTA 2014*, pp. 437–440.
4. Wohlin, C., et al. (2012). *Experimentation in Software Engineering*.
   Springer.
5. W3C. (2014). *Website Accessibility Conformance Evaluation Methodology
   (WCAG-EM) 1.0*. W3C Working Group Note.
6. Cohen, J. (1960). A Coefficient of Agreement for Nominal Scales.
   *Educational and Psychological Measurement*, 20(1), 37–46.
