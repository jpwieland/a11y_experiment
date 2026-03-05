# Dataset Construction Protocol
## A Stratified, Multi-Source Benchmark for WCAG Violation Detection and Automated Remediation

**Version**: 1.0
**Status**: Normative
**Last revised**: 2025-01

---

## Abstract

This document specifies the complete dataset construction protocol for the **a11y-autofix benchmark corpus** — a collection of real-world React/TypeScript open-source projects systematically sampled from GitHub for use in evaluating automated accessibility remediation systems. The protocol follows established practices from empirical software engineering (Wohlin et al., 2012), program repair benchmarking (Just et al., 2014; Le Goues et al., 2015), and web accessibility evaluation methodology (Abou-Zahra, 2008; Brajnik, 2008). All steps are deterministic, reproducible, and traceable to a versioned artefact.

---

## 1. Research Objectives and Dataset Purpose

This dataset is designed to support the following research questions:

- **RQ1** (Detection validity): Do multi-tool consensus protocols produce lower false-positive rates than single-tool approaches for WCAG violation detection in React components?
- **RQ2** (Repair efficacy): Do LLM-based repair agents achieve statistically different success rates across WCAG violation types (contrast, ARIA, keyboard, label, semantic, alt-text, focus)?
- **RQ3** (Model comparison): Is there a statistically significant difference in repair success rate between code-specialised language models (Qwen2.5-Coder, DeepSeek-Coder, CodeLlama) and general-purpose models (Llama 3.1)?
- **RQ4** (Agent comparison): Under what conditions does an autonomous agent (OpenHands, SWE-agent) outperform direct LLM prompting for accessibility repair?
- **RQ5** (Generalisation): Do results from a synthetic benchmark generalise to a population of real-world production React codebases?

---

## 2. Methodological Foundations

### 2.1 Benchmark Construction Literature

The construction methodology draws from:

| Reference | Contribution |
|-----------|-------------|
| Just et al. (2014) — *Defects4J* | Isolated, reproducible bug corpus; commit-pinning methodology |
| Le Goues et al. (2015) — *ManyBugs/IntroClass* | Stratification criteria; diversity vs. representativeness trade-off |
| Böhme et al. (2017) — *Directed Fuzzing* | Coverage-guided corpus construction |
| Alshayban et al. (2020) — *Accessibility in Android* | Multi-tool scanning methodology; inclusion/exclusion criteria for mobile accessibility |
| Bajammal & Mesbah (2021) — *Web Accessibility Bugs in Web Apps* | Web-specific accessibility corpus design; violation taxonomy |
| Gonzalez-Barahona et al. (2021) — *Dataset Quality in MSR* | Replication package standards; metadata completeness requirements |
| Wohlin et al. (2012) — *Experimentation in Software Engineering* | Experimental validity framework (construct, internal, external, conclusion) |
| W3C/WAI (2023) — *WCAG-EM* | Evaluation methodology for web accessibility conformance |

### 2.2 Validity Framework

Following Wohlin et al. (2012), the protocol addresses four threats to validity:

| Threat Category | Mitigation |
|----------------|------------|
| **Construct validity** | WCAG 2.1/2.2 as the ground-truth standard; multi-tool consensus (≥2 tools) as the primary validity indicator |
| **Internal validity** | Pinned commit SHA ensures identical code across runs; tool version locking prevents environment drift |
| **External validity** | Stratified sampling across 7 domains, 3 size classes, 3 popularity tiers; minimum 5 projects per stratum |
| **Conclusion validity** | Minimum N=50 projects for statistical power; multiple runs per model (runs_per_model ≥ 3); Cohen's κ ≥ 0.7 required for ground-truth annotation agreement |

---

## 3. Population Definition

### 3.1 Target Population

The target population is the set of all publicly accessible GitHub repositories satisfying:

- **Language**: primary language is TypeScript or JavaScript
- **Framework**: uses React ≥ 16.0 (determined by `package.json` dependency)
- **Activity**: at least one commit in the preceding 18 months
- **Size**: between 10 and 2,000 TypeScript/JSX source files
- **Accessibility relevance**: contains rendered UI components (not backend-only or library-only projects)

### 3.2 Sampling Frame

Projects are discovered via the GitHub Search API (REST v3) using the following search strategy:

**Query construction** (iterated across strata):
```
language:TypeScript stars:>100 pushed:>2023-01-01 topic:react
```

Supplementary queries targeting specific domains:
```
"accessibility" OR "a11y" in:description language:TypeScript stars:>50
"WCAG" in:readme language:TypeScript
react-component in:topics language:TypeScript
```

**Rate limiting**: The GitHub Search API enforces 30 requests/minute (authenticated) and returns ≤1,000 results per query. Queries are paginated with `per_page=100` and results are de-duplicated by repository ID.

---

## 4. Inclusion and Exclusion Criteria

### 4.1 Inclusion Criteria

A repository is eligible for the dataset if it satisfies **all** of the following:

| Criterion | Threshold | Measurement Method |
|-----------|-----------|-------------------|
| **IC1** Stars | ≥ 100 | GitHub API `stargazers_count` |
| **IC2** Last commit | ≤ 24 months before crawl date | GitHub API `pushed_at` |
| **IC3** License | OSI-approved open-source (MIT, Apache 2.0, BSD-2, BSD-3, ISC, GPL-2, GPL-3, AGPL-3, MPL-2) | GitHub API `license.spdx_id` |
| **IC4** React component files | ≥ 10 files matching `**/*.{tsx,jsx}` | File tree traversal |
| **IC5** Buildability | `package.json` present at root or first-level directory | File existence check |
| **IC6** Non-generated | Less than 30% of JSX/TSX files are auto-generated (detected by header comments) | Heuristic scan |
| **IC7** UI-rendering | At least one file containing JSX return statements | AST check |

### 4.2 Exclusion Criteria

A repository is excluded if any of the following hold:

| Criterion | Description |
|-----------|-------------|
| **EC1** Starter/template | Repository name or description matches patterns: `starter`, `boilerplate`, `template`, `scaffold`, `create-*`, `*-starter` |
| **EC2** Course project | Description or topics contain: `homework`, `course`, `tutorial`, `learning`, `bootcamp`, `assignment` |
| **EC3** Duplicate | Is a fork of an already-included repository (`fork == true` in GitHub API) |
| **EC4** Archived | Repository is archived (`archived == true`) |
| **EC5** Private dependency | Build fails due to private npm scoped packages (`@scope/package`) not resolvable publicly |
| **EC6** Non-browser target** | React Native, Electron (main process only), or React-based CLI tools with no DOM rendering |
| **EC7** Predominantly generated UI | >50% of component files are generated by Storybook, Plasmic, Builder.io, or similar tools |

### 4.3 Application of Criteria

Inclusion/exclusion screening proceeds in two phases:

1. **Automated screening** (IC1–IC5, EC1–EC5): Applied programmatically by `scripts/discover.py` using GitHub API metadata without cloning.
2. **Manual screening** (IC6, IC7, EC6, EC7): Applied after shallow clone by `scripts/snapshot.py` with human reviewer confirmation.

All screening decisions are recorded in the project catalog with the specific criterion that led to exclusion.

---

## 5. Stratification Design

To ensure diversity and external validity, the sampling frame is stratified across three independent dimensions:

### 5.1 Dimension 1: Application Domain

| Stratum | Label | Target N | Example Topics |
|---------|-------|----------|----------------|
| D1 | E-commerce / Retail | 8 | storefront, shopping-cart, marketplace |
| D2 | Government / Civic | 6 | government, civic-tech, open-government |
| D3 | Healthcare / Medical | 6 | healthcare, medical, EMR, patient |
| D4 | Education / Learning | 7 | education, LMS, e-learning, edtech |
| D5 | Developer Tools / IDE | 7 | developer-tools, IDE, code-editor, devtools |
| D6 | Dashboard / Analytics | 8 | dashboard, analytics, data-viz, monitoring |
| D7 | Social / Communication | 8 | social, messaging, collaboration, chat |

**Total target**: N ≥ 50 projects with minimum 5 per stratum.

### 5.2 Dimension 2: Codebase Size

| Stratum | Label | JSX/TSX File Count |
|---------|-------|-------------------|
| S1 | Small | 10–50 files |
| S2 | Medium | 51–300 files |
| S3 | Large | 301+ files |

Target distribution: 30% S1, 50% S2, 20% S3.

### 5.3 Dimension 3: Project Popularity

| Stratum | Label | GitHub Stars |
|---------|-------|--------------|
| P1 | Emerging | 100–999 |
| P2 | Established | 1,000–9,999 |
| P3 | Popular | ≥ 10,000 |

Target distribution: 40% P1, 40% P2, 20% P3. Higher-starred projects are capped to prevent over-representation of the most popular libraries (which may have stronger accessibility practices than the average project).

### 5.4 WCAG Principle Coverage Requirement

The final corpus must contain projects with detected violations across all four WCAG principles:

| Principle | Target Coverage |
|-----------|----------------|
| Perceivable (1.x) | ≥ 20 projects |
| Operable (2.x) | ≥ 20 projects |
| Understandable (3.x) | ≥ 15 projects |
| Robust (4.x) | ≥ 20 projects |

Coverage is assessed after the initial scan phase. If a principle is under-represented, targeted supplementary sampling is conducted.

---

## 6. Repository Snapshotting Protocol

To guarantee reproducibility, every project is evaluated at a specific immutable commit rather than at a mutable branch head.

### 6.1 Snapshot Procedure

1. **Identify the canonical branch**: Use GitHub API to retrieve the repository's default branch name.
2. **Retrieve latest commit SHA**: Query `GET /repos/{owner}/{repo}/commits/{branch}` to obtain the SHA-1 of the HEAD commit on the default branch at the time of crawling.
3. **Record snapshot metadata**:
   - `pinned_commit`: full 40-character SHA-1
   - `snapshot_date`: ISO 8601 UTC timestamp of the crawl
   - `branch`: default branch name
4. **Shallow clone at pinned commit**:
   ```bash
   git clone --depth 1 --branch <tag_or_sha> <url> snapshots/<id>/
   ```
   If the backend does not support `--branch <sha>`, use:
   ```bash
   git clone <url> snapshots/<id>/
   git -C snapshots/<id>/ checkout <sha>
   ```
5. **Verify integrity**: Compare `git -C snapshots/<id>/ rev-parse HEAD` against recorded `pinned_commit`. Abort if mismatch.
6. **Record `package.json` metadata**: Extract `dependencies.react`, `devDependencies.@types/react`, and `devDependencies.typescript` for version recording.

### 6.2 Snapshot Storage

Cloned snapshots are stored in `dataset/snapshots/<owner>__<repo>/` and are **not committed to the dataset repository** (`.gitignore`d). The catalog entry contains sufficient information to recreate any snapshot deterministically:

```bash
git clone <github_url> && git checkout <pinned_commit>
```

### 6.3 Snapshot Integrity Verification

The script `scripts/validate.py --check-snapshots` verifies that:
- The working tree is clean (no uncommitted modifications)
- `git rev-parse HEAD` matches `pinned_commit` in the catalog
- No files outside the declared `scan_paths` are present in the working tree

---

## 7. Scanning Protocol

### 7.1 Scanner Configuration

All projects are scanned using the a11y-autofix multi-tool scanner with the following fixed configuration to ensure comparability:

```yaml
wcag_level: WCAG2AA
tools:
  - pa11y
  - axe-core
  - playwright+axe
min_tool_consensus: 1   # Collect all findings; consensus applied in analysis
scan_timeout: 90        # seconds per file per tool
```

Note: `min_tool_consensus: 1` during scanning ensures that all raw findings are collected. The consensus threshold is applied at analysis time to allow flexible post-hoc analysis without re-scanning.

### 7.2 Scan Execution

```bash
python dataset/scripts/scan.py --catalog dataset/catalog/projects.yaml \
  --output dataset/results/ \
  --workers 2 \
  --tool-timeout 90
```

### 7.3 Scan Result Storage

For each project, the scanner produces:
- `dataset/results/<owner>__<repo>/report.json` — complete audit trail
- `dataset/results/<owner>__<repo>/summary.json` — aggregated statistics (issue count by type, tool, criterion)

### 7.4 Scan Validity Checks

A scan is considered valid if:
- At least 2 of the 3 configured tools ran successfully
- Scan completed within 3× `scan_timeout`
- The number of scanned component files is ≥ 5 (harness generation succeeded for most files)

Projects failing validity checks are flagged as `scan_error` in the catalog and excluded from the primary analysis (retained in an appendix corpus).

---

## 8. Ground Truth Annotation Protocol

### 8.1 Annotation Strategy

The ground truth is constructed through a **semi-automated annotation process** combining machine-generated candidates with human expert validation:

1. **Candidate generation**: Run all three scanners on each project. All findings with `tool_consensus ≥ 1` are collected as candidates.
2. **High-confidence automatic acceptance**: Findings with `tool_consensus ≥ 2` (confirmed by at least two independent tools) are automatically accepted as ground-truth violations without manual review.
3. **Disputed finding review**: Findings with `tool_consensus == 1` are queued for human review.
4. **Human expert annotation**: Two independent annotators (minimum 1 year of web accessibility experience, WCAG 2.x knowledge) review each disputed finding and assign one of: `CONFIRMED`, `FALSE_POSITIVE`, or `UNCERTAIN`.
5. **Reconciliation**: Findings with annotator disagreement are reconciled via structured discussion. Persistent disagreements result in `UNCERTAIN` classification and exclusion from the primary evaluation set.

### 8.2 Inter-Annotator Agreement

Annotation agreement is measured using **Cohen's κ** (Cohen, 1960) on the three-class labelling task (`CONFIRMED`, `FALSE_POSITIVE`, `UNCERTAIN`):

| κ Range | Interpretation | Required Action |
|---------|----------------|-----------------|
| κ ≥ 0.80 | Near-perfect agreement | Accept |
| 0.60 ≤ κ < 0.80 | Substantial agreement | Accept with reconciliation review |
| 0.40 ≤ κ < 0.60 | Moderate agreement | Reconcile all disagreements |
| κ < 0.40 | Poor agreement | Re-train annotators, re-annotate |

**Minimum required κ**: 0.70. If κ < 0.70 after the first annotation round, annotators review the annotation guidelines together and re-annotate the disputed items.

The κ computation is implemented in `scripts/annotate.py --compute-agreement`.

### 8.3 Annotation Record Schema

Each ground-truth finding record contains:

```yaml
finding_id: "<16-char SHA-256>"
project_id: "<owner>__<repo>"
file: "relative/path/to/Component.tsx"
selector: ".css-selector"
wcag_criteria: "1.4.3"
issue_type: "contrast"
impact: "serious"
confidence: "high"        # system-assigned
tool_consensus: 2

# Ground truth annotation
ground_truth_label: "CONFIRMED"  # CONFIRMED | FALSE_POSITIVE | UNCERTAIN
annotator_1: "CONFIRMED"
annotator_2: "CONFIRMED"
agreement: true
annotation_notes: ""
annotation_date: "2025-01-15T10:00:00Z"
```

---

## 9. Dataset Quality Metrics

The following quality indicators are computed by `scripts/validate.py` and must meet minimum thresholds before the dataset is considered release-ready:

| Metric | Formula | Minimum Threshold |
|--------|---------|------------------|
| **Catalog completeness** | Projects with all required metadata fields / total projects | ≥ 0.95 |
| **Snapshot integrity** | Projects with verified `pinned_commit` / total projects | 1.00 |
| **Scan coverage** | Projects with valid scan / total projects | ≥ 0.90 |
| **Annotation coverage** | Disputed findings annotated / total disputed findings | ≥ 0.95 |
| **Inter-annotator agreement** | Cohen's κ on annotated subset | ≥ 0.70 |
| **WCAG principle coverage** | Principles with ≥ 15 confirmed violations / 4 principles | ≥ 0.75 |
| **IssueType coverage** | IssueTypes with ≥ 10 confirmed instances / 7 types | ≥ 0.85 |
| **Stratum balance** | Coefficient of variation of N across domain strata | ≤ 0.40 |

---

## 10. Reproducibility Package

The complete replication package must include:

| Artefact | Location | Content |
|----------|----------|---------|
| Catalog | `dataset/catalog/projects.yaml` | All project metadata, pinned commits |
| Scan results | `dataset/results/` | JSON audit trails per project |
| Ground truth | `dataset/ground_truth/` | Annotated findings per project |
| Scanner versions | `dataset/metadata/tool_versions.json` | Exact version of each tool used |
| Python environment | `dataset/metadata/requirements.lock` | Frozen pip dependency tree |
| Node environment | `dataset/metadata/npm_versions.json` | npm global package versions |
| Snapshot scripts | `dataset/scripts/snapshot.py` | Deterministic clone procedure |
| Analysis scripts | `dataset/scripts/analyze.py` | All statistical computations |
| Annotation guidelines | `dataset/ANNOTATION_GUIDELINES.md` | Annotator instructions |

---

## 11. Ethical and Legal Considerations

### 11.1 Licensing

Only repositories under OSI-approved open-source licenses are included (criterion IC3). The dataset does not redistribute source code; it records metadata and scan findings only. Replication requires independent cloning of the publicly accessible repositories.

### 11.2 Privacy

No user data is collected or stored. GitHub API calls are performed with an authenticated token to avoid rate limiting but no user-level data beyond public repository metadata is accessed.

### 11.3 Terms of Service

GitHub API usage complies with the GitHub Terms of Service for research purposes (§D.3 of the GitHub Terms of Service as of 2024). The crawl rate is limited to avoid excessive API load.

---

## 12. Change Log

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2025-01 | Initial protocol |

---

## 13. References

- Abou-Zahra, S. (2008). *Web Accessibility Evaluation*. W3C/WAI.
- Alshayban, A., Ahmed, I., & Malek, S. (2020). Accessibility issues in Android apps: State of affairs, user feedback, and ways forward. *ICSE 2020*.
- Bajammal, M., & Mesbah, A. (2021). Web accessibility of select UI components in React, Angular, and Vue. *W4A 2021*.
- Böhme, M., Pham, V. T., Nguyen, M. D., & Roychoudhury, A. (2017). Directed greybox fuzzing. *CCS 2017*.
- Brajnik, G. (2008). A comparative test of web accessibility evaluation methods. *ASSETS 2008*.
- Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement*, 20(1), 37–46.
- Gonzalez-Barahona, J. M., & Robles, G. (2012). On the reproducibility of empirical software engineering studies based on data retrieved from development repositories. *Empirical Software Engineering*.
- Just, R., Jalali, D., & Ernst, M. D. (2014). Defects4J: A database of existing faults to enable controlled testing studies for Java programs. *ISSTA 2014*.
- Le Goues, C., Holtschulte, N., Smith, E. K., Brun, Y., Devanbu, P., Forrest, S., & Weimer, W. (2015). The ManyBugs and IntroClass benchmarks for automated repair of C programs. *IEEE TSE*.
- W3C (2023). *Web Content Accessibility Guidelines (WCAG) 2.2*. W3C Recommendation.
- W3C/WAI (2014). *Website Accessibility Conformance Evaluation Methodology (WCAG-EM) 1.0*.
- Wohlin, C., Runeson, P., Höst, M., Ohlsson, M. C., Regnell, B., & Wesslén, A. (2012). *Experimentation in Software Engineering*. Springer.
