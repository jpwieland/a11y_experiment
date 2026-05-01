# Ablation Study — Pre-registered Analysis Plan

> **Status:** Pre-registered before data collection.  
> **Version:** 1.0.0  
> **Date:** 2026-04-30  
> **Authors:** João Wieland  
> **Paper section:** Section 4.2 / Threats to Validity

This document is a pre-registration record for the prompt-component ablation
study.  All hypotheses, analysis choices, and decision rules are fixed here
**before** any experimental run is executed.  No post-hoc changes to the
primary analysis are permitted once data collection has begun.

---

## 1. Motivation

The internal validity threat documented in Section 6 of the paper is that the
six-component prompt structure was designed as a monolithic unit; we have no
empirical evidence for the independent contribution of each component.
Reviewer Question 5 explicitly asks for this ablation.  This study provides
it.

---

## 2. Research Questions

| ID | Question |
|----|----------|
| RQ-A1 | Does removing the role-definition component (C1) significantly reduce IFR? |
| RQ-A2 | Does removing the user-impact sentence (C2 partial) significantly reduce IFR? |
| RQ-A3 | Does removing the code-context component (C3) significantly reduce IFR? |
| RQ-A4 | Does removing the constraints component (C4) significantly reduce IFR? |
| RQ-A5 | Does removing the few-shot examples component (C5) significantly reduce IFR? |
| RQ-A6 | Does removing the output-format component (C6) significantly reduce IFR? |
| RQ-A7 | Does the structured six-component prompt produce higher IFR than a raw scanner baseline? |

---

## 3. Experimental Design

### 3.1 Conditions

Eight conditions, one baseline plus seven ablations:

| Condition ID        | Components Active           |
|---------------------|-----------------------------|
| `full`              | C1 C2 C2ui C3 C4 C5 C6      |
| `minus_role`        | C2 C2ui C3 C4 C5 C6         |
| `minus_user_impact` | C1 C2 C3 C4 C5 C6           |
| `minus_code_context`| C1 C2 C2ui C4 C5 C6         |
| `minus_constraints` | C1 C2 C2ui C3 C5 C6         |
| `minus_few_shot`    | C1 C2 C2ui C3 C4 C6         |
| `minus_output_format`| C1 C2 C2ui C3 C4 C5        |
| `raw_baseline`      | raw Pa11y JSON only          |

Each condition is an exact copy of the full pipeline with only the specified
component replaced or removed.  All other parameters (model, temperature,
validation layers, retry limit) are held constant.

### 3.2 Models

Primary model: **qwen2.5-coder-3b** (lowest within-condition variance in the
main study, therefore cleanest signal for prompt-effect isolation).

All three models from Table 1 are evaluated for cross-model generalisability.

### 3.3 Repetitions and Randomisation

- Three repetitions per (condition × model) cell.
- Seeds: [42, 137, 2025] — pre-determined, not chosen post-hoc.
- Each seed controls the file-processing order within a repetition.
- All conditions within a repetition use the **same seed** so that file-order
  effects are held constant across conditions.

### 3.4 Corpus

- All 338 violations from the main-study corpus (violations-bearing files only).
- Regression-guard files (no violations) excluded: they contribute trivially to
  SR and add compute cost without informative signal for prompt-component
  analysis.
- File order is seed-shuffled; violation order within a file is deterministic
  (sorted by violation_id).

### 3.5 Validation Pipeline

All four validation layers remain active for all conditions, including
`raw_baseline`.  Disabling validation layers for some conditions would confound
prompt effects with oracle effects.

---

## 4. Primary Metric

**IFR (Issue Fix Rate)** — micro-averaged over all violations:

```
IFR = n_violations_resolved / n_violations_total
```

A violation is resolved if at least one of its up to three repair attempts
passes all three hard validation layers (L1 syntax, L2 functional, L3 domain).

---

## 5. Secondary Metrics

Reported descriptively; not subjected to correction for multiple comparisons
(pre-specified as secondary):

| Metric | Definition |
|--------|------------|
| SR | Fraction of violation-bearing files where all violations are resolved |
| MTTR | Mean wall-clock seconds per file (all violations summed) |
| Tokens/fix | Output tokens per resolved violation |
| L1 fail rate | Fraction of attempts failing Layer 1 (syntax) |
| L2 fail rate | Fraction of attempts failing Layer 2 (functional regression) |
| L3 fail rate | Fraction of attempts failing Layer 3 (domain) |
| Retry exhaustion | Fraction of violations exhausting all 3 retries without success |

---

## 6. Statistical Tests

### 6.1 Primary Test: Wilcoxon Signed-Rank

- **Why paired:** Each violation appears in both the baseline and each ablation
  condition (same corpus, same seed-order).  Pairing on violation eliminates
  between-violation variance.
- **Why non-parametric:** IFR is binary at the violation level (resolved/not);
  the distribution of per-repetition IFR is not guaranteed to be normal with
  only three repetitions.
- **Alternative hypothesis:** Two-sided (we do not assume direction of each
  component's contribution a priori).
- **Unit of analysis:** Per-violation binary outcome (resolved = 1, not = 0).
  All three repetitions are pooled into a single paired comparison
  (condition × violation_id × repetition as the triple-matched unit).
- **Zero handling:** `zero_method='wilcox'` (discard ties; Pratt method would
  be more conservative but Wilcox is standard for binary outcomes).

### 6.2 Correction: Holm-Bonferroni

- Seven pairwise tests (each ablation condition vs. full baseline).
- Holm-Bonferroni sequential correction applied to all seven raw p-values
  simultaneously.
- Global α = 0.05.

### 6.3 Effect Size: Rank-Biserial Correlation r

- Kerby (2014) formula: r = 1 − (2W) / (n(n+1)/2)
- Thresholds (Cohen 1988 adapted for r):
  - Negligible: |r| < 0.10
  - Small: 0.10 ≤ |r| < 0.30
  - Medium: 0.30 ≤ |r| < 0.50
  - Large: |r| ≥ 0.50

### 6.4 Confidence Intervals

- Bootstrap percentile CI on the difference in IFR means (ablation − full).
- B = 10,000 bootstrap samples.
- 95% CI level.
- Seed = 42 for reproducibility.

### 6.5 Post-hoc Power

- Noether (1987) normal approximation: z_β = |r|√n − z_α.
- Reported for transparency; the study is not designed on power grounds
  (corpus size is fixed by the main study).

---

## 7. Decision Rules

A component is deemed to have a **significant independent contribution** if:

1. The Wilcoxon test is significant after Holm-Bonferroni correction (p_corr < 0.05).
2. The 95% bootstrap CI on ΔIFR excludes zero.
3. The effect size is at least negligible (|r| > 0.05) — prevents declaring
   significant a trivially small effect inflated by large n.

Conditions (1) and (2) must both hold; (3) is a sanity check.

---

## 8. Deviations Protocol

Any deviation from this plan discovered after data collection begins must be:

1. Documented in `ANALYSIS_PLAN_DEVIATIONS.md` with date and reason.
2. Treated as a sensitivity analysis rather than the primary result.
3. Clearly distinguished from the pre-registered analysis in the paper.

---

## 9. Implementation References

| Component | File |
|-----------|------|
| Condition definitions | `src/ablation_conditions.py` |
| Metrics schema | `src/metrics_schema.py` |
| Persistence | `src/metrics_collector.py` |
| Orchestrator | `src/ablation_runner.py` |
| Statistical tests | `src/statistical_analysis.py` |
| Report generation | `analysis/ablation_report.py` |
| Config | `config/experiment_config.yaml`, `config/prompt_ablation.yaml` |

---

## 10. Expected Timeline

| Phase | Description | Duration |
|-------|-------------|----------|
| P1 | Corpus scan + cache warm-up | 1 day |
| P2 | 8 conditions × 1 model × 3 reps | 2–3 days |
| P3 | 8 conditions × 2 more models × 3 reps | 4–6 days |
| P4 | Statistical analysis + report generation | 1 day |
| P5 | Paper integration | 1 day |

Total estimated wall time: 9–12 days on a single RTX 4060.

---

## 11. References

- Kerby, D. S. (2014). The simple difference formula: An approach to teaching
  nonparametric correlation. *Comprehensive Psychology*, 3, 11-IT.
- Noether, G. E. (1987). Sample size determination for some common nonparametric
  tests. *JASA*, 82(398), 645–647.
- Cohen, J. (1988). *Statistical power analysis for the behavioral sciences*
  (2nd ed.). Lawrence Erlbaum.
- Holm, S. (1979). A simple sequentially rejective multiple test procedure.
  *Scandinavian Journal of Statistics*, 6(2), 65–70.
