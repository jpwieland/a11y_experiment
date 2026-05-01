"""
statistical_analysis.py
========================
Statistical analysis pipeline for the prompt-component ablation study.

All tests and effect sizes follow the pre-registered ANALYSIS_PLAN.md.
No post-hoc decisions are permitted after data collection has begun.

Primary test: Wilcoxon signed-rank (paired, non-parametric)
  - Pairing: each violation is a natural unit; conditions are compared on
    per-violation binary outcomes (resolved/not) within the same repetition.
  - Correction: Holm-Bonferroni over 7 pairwise (full vs. ablated) tests.
  - Effect size: rank-biserial correlation r (Kerby 2014).
  - CIs: bootstrap percentile (B=10,000) on IFR difference.
  - Power: post-hoc power computed at observed effect sizes.

Usage:
    python -m ablation_study.src.statistical_analysis \
        --results-dir ablation_study/results \
        --output ablation_study/results/stats_report.json
"""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

# scipy and statsmodels are optional at import time so that
# the module can be inspected without them installed.
try:
    from scipy import stats as scipy_stats
    from scipy.stats import wilcoxon
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
    warnings.warn("scipy not installed; statistical tests will be skipped", stacklevel=1)

try:
    from statsmodels.stats.multitest import multipletests
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False
    warnings.warn("statsmodels not installed; Holm-Bonferroni will be skipped", stacklevel=1)


# ── Data loading ───────────────────────────────────────────────────────────────

def load_violation_records(results_dir: Path) -> list[dict[str, Any]]:
    """Load all ViolationRecord rows from every violations.jsonl file."""
    records: list[dict] = []
    for path in sorted(results_dir.glob("**/violations.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def load_run_summaries(results_dir: Path) -> list[dict[str, Any]]:
    """Load all RunSummary objects from every summary.json file."""
    summaries: list[dict] = []
    for path in sorted(results_dir.glob("**/summary.json")):
        with path.open(encoding="utf-8") as fh:
            summaries.append(json.load(fh))
    return summaries


# ── Paired outcome matrix ──────────────────────────────────────────────────────

def build_paired_matrix(
    records: list[dict],
    baseline_condition: str = "full",
    metric: str = "resolved",
) -> dict[str, dict]:
    """
    Build a paired (violation × condition) outcome matrix for Wilcoxon tests.

    Each violation_id that appears in both the baseline condition and an
    ablation condition (within the same repetition) forms a natural pair.

    Returns:
        {condition_id: {"baseline": [0/1, ...], "ablation": [0/1, ...]}}
    """
    from collections import defaultdict

    # Index: (condition_id, repetition, violation_id) → metric value
    index: dict[tuple, float] = {}
    for r in records:
        key = (r["condition_id"], r["repetition"], r["violation_id"])
        index[key] = float(r[metric]) if metric in r else 0.0

    # Find all violations covered by the baseline across all reps
    ablation_conditions = [
        c for c in {r["condition_id"] for r in records}
        if c != baseline_condition
    ]
    repetitions = sorted({r["repetition"] for r in records})

    paired: dict[str, dict] = {c: {"baseline": [], "ablation": []} for c in ablation_conditions}

    for cond in ablation_conditions:
        for rep in repetitions:
            baseline_violations = {
                r["violation_id"]
                for r in records
                if r["condition_id"] == baseline_condition and r["repetition"] == rep
            }
            ablation_violations = {
                r["violation_id"]
                for r in records
                if r["condition_id"] == cond and r["repetition"] == rep
            }
            common = baseline_violations & ablation_violations

            for vid in sorted(common):
                b_val = index.get((baseline_condition, rep, vid), 0.0)
                a_val = index.get((cond, rep, vid), 0.0)
                paired[cond]["baseline"].append(b_val)
                paired[cond]["ablation"].append(a_val)

    return paired


# ── Statistical tests ──────────────────────────────────────────────────────────

@dataclass
class PairwiseTestResult:
    condition_id: str
    n_pairs: int
    baseline_mean: float
    ablation_mean: float
    delta: float               # ablation_mean - baseline_mean
    w_statistic: float
    p_value_raw: float
    p_value_corrected: float   # after Holm-Bonferroni
    reject_h0: bool            # at alpha = 0.05
    effect_size_r: float       # rank-biserial correlation
    effect_magnitude: str      # "negligible"|"small"|"medium"|"large"
    ci_lower: float            # bootstrap 95% CI on delta
    ci_upper: float
    power: float               # post-hoc power at observed effect


def wilcoxon_signed_rank(
    baseline: list[float],
    ablation: list[float],
    alternative: str = "two-sided",
) -> tuple[float, float, float]:
    """
    Returns (W, p_value, rank_biserial_r).

    rank_biserial_r = (W+ - W-) / n*(n-1)/2  (Kerby 2014 formula).
    For scipy.stats.wilcoxon, r = 1 - 2W / n*(n+1)*0.5 when using
    the exact null.  We use the simpler Kerby approximation.
    """
    if not _HAS_SCIPY:
        return float("nan"), float("nan"), float("nan")

    diffs = [a - b for a, b in zip(ablation, baseline)]
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return 0.0, 1.0, 0.0

    result = wilcoxon(
        [a for a in ablation],
        [b for b in baseline],
        alternative=alternative,
        zero_method="wilcox",
    )
    W = float(result.statistic)
    p = float(result.pvalue)

    # Rank-biserial r (Kerby 2014): r = 1 - (2*W)/(n*(n+1)/2)
    max_W = n * (n + 1) / 2
    r = 1.0 - (2.0 * W / max_W) if max_W > 0 else 0.0

    return W, p, r


def _effect_magnitude(r: float) -> str:
    abs_r = abs(r)
    if abs_r < 0.1:
        return "negligible"
    if abs_r < 0.3:
        return "small"
    if abs_r < 0.5:
        return "medium"
    return "large"


def bootstrap_ci(
    baseline: list[float],
    ablation: list[float],
    n_bootstrap: int = 10_000,
    ci_level: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """
    Bootstrap percentile CI for the difference in means (ablation - baseline).
    """
    rng = np.random.default_rng(seed)
    n   = len(baseline)
    b   = np.array(baseline)
    a   = np.array(ablation)

    deltas = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        deltas[i] = a[idx].mean() - b[idx].mean()

    alpha = 1.0 - ci_level
    lo = float(np.percentile(deltas, 100 * alpha / 2))
    hi = float(np.percentile(deltas, 100 * (1 - alpha / 2)))
    return lo, hi


def post_hoc_power(
    n: int,
    effect_r: float,
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> float:
    """
    Approximate post-hoc power for Wilcoxon signed-rank via normal approximation.
    Based on Noether (1987): z_beta = |r| * sqrt(n) - z_alpha.
    """
    if not _HAS_SCIPY:
        return float("nan")

    if alternative == "two-sided":
        z_alpha = scipy_stats.norm.ppf(1 - alpha / 2)
    else:
        z_alpha = scipy_stats.norm.ppf(1 - alpha)

    z_beta = abs(effect_r) * (n ** 0.5) - z_alpha
    power  = float(scipy_stats.norm.cdf(z_beta))
    return round(max(0.0, min(1.0, power)), 4)


# ── Holm-Bonferroni correction ─────────────────────────────────────────────────

def holm_bonferroni_correction(
    p_values: list[float],
    alpha: float = 0.05,
) -> tuple[list[float], list[bool]]:
    """
    Apply Holm-Bonferroni correction to a list of p-values.

    Returns (corrected_p_values, reject_flags).
    """
    if not _HAS_STATSMODELS:
        return p_values, [p < alpha for p in p_values]

    reject, p_corrected, _, _ = multipletests(
        p_values, alpha=alpha, method="holm"
    )
    return list(p_corrected.astype(float)), list(reject)


# ── Main analysis pipeline ─────────────────────────────────────────────────────

@dataclass
class StudyResults:
    pairwise: list[PairwiseTestResult]
    ifr_by_condition: dict[str, float]
    ifr_ci_by_condition: dict[str, tuple[float, float]]
    secondary_metrics: dict[str, dict[str, float]]
    metadata: dict[str, Any]


def run_analysis(
    results_dir: Path,
    baseline_condition: str = "full",
    alpha: float = 0.05,
    n_bootstrap: int = 10_000,
    ci_level: float = 0.95,
    metrics_to_test: list[str] | None = None,
) -> StudyResults:
    """
    Run the full statistical analysis pipeline.

    Steps:
    1. Load violation records from JSONL files.
    2. Build paired outcome matrix for 'resolved' (primary) metric.
    3. Run Wilcoxon signed-rank for each ablation condition vs. baseline.
    4. Compute rank-biserial r effect sizes.
    5. Apply Holm-Bonferroni correction.
    6. Compute bootstrap CIs on IFR difference.
    7. Compute post-hoc power.
    8. Repeat for each secondary metric in metrics_to_test.
    """
    if metrics_to_test is None:
        metrics_to_test = ["resolved"]

    records   = load_violation_records(results_dir)
    summaries = load_run_summaries(results_dir)

    if not records:
        raise FileNotFoundError(f"No violations.jsonl files found under {results_dir}")

    # IFR per condition (macro-averaged over reps then micro-averaged over violations)
    ifr_by_condition: dict[str, float] = {}
    ifr_ci_by_cond:   dict[str, tuple[float, float]] = {}

    all_conditions = sorted({r["condition_id"] for r in records})

    for cond in all_conditions:
        sub = [r for r in records if r["condition_id"] == cond]
        outcomes = [float(r["resolved"]) for r in sub]
        ifr_by_condition[cond] = float(np.mean(outcomes)) if outcomes else 0.0

    # Primary pairwise tests (resolved metric)
    paired_matrix = build_paired_matrix(records, baseline_condition, "resolved")
    ablation_conditions = [c for c in all_conditions if c != baseline_condition]

    raw_p_values: list[float] = []
    w_stats: list[float] = []
    r_effects: list[float] = []
    n_pairs_list: list[int] = []

    for cond in ablation_conditions:
        b = paired_matrix[cond]["baseline"]
        a = paired_matrix[cond]["ablation"]
        W, p, r = wilcoxon_signed_rank(b, a)
        raw_p_values.append(p)
        w_stats.append(W)
        r_effects.append(r)
        n_pairs_list.append(len(b))

    corrected_p, reject_flags = holm_bonferroni_correction(raw_p_values, alpha)

    pairwise: list[PairwiseTestResult] = []
    for i, cond in enumerate(ablation_conditions):
        b = paired_matrix[cond]["baseline"]
        a = paired_matrix[cond]["ablation"]
        n = n_pairs_list[i]

        ci_lo, ci_hi = bootstrap_ci(b, a, n_bootstrap=n_bootstrap, ci_level=ci_level)
        power = post_hoc_power(n, r_effects[i], alpha=alpha)

        pairwise.append(PairwiseTestResult(
            condition_id=cond,
            n_pairs=n,
            baseline_mean=float(np.mean(b)) if b else 0.0,
            ablation_mean=float(np.mean(a)) if a else 0.0,
            delta=float(np.mean(a) - np.mean(b)) if a else 0.0,
            w_statistic=w_stats[i],
            p_value_raw=raw_p_values[i],
            p_value_corrected=corrected_p[i],
            reject_h0=reject_flags[i],
            effect_size_r=r_effects[i],
            effect_magnitude=_effect_magnitude(r_effects[i]),
            ci_lower=ci_lo,
            ci_upper=ci_hi,
            power=power,
        ))

    # Secondary metrics from summaries
    secondary_metrics: dict[str, dict[str, float]] = {}
    secondary_metric_keys = [
        "sr_violation_files", "mttr_s", "tokens_output_per_fix",
        "layer1_fail_rate", "layer2_fail_rate", "layer3_fail_rate",
    ]
    for key in secondary_metric_keys:
        by_cond: dict[str, list[float]] = {c: [] for c in all_conditions}
        for s in summaries:
            cid = s.get("condition_id", "")
            val = s.get(key, float("nan"))
            if cid in by_cond:
                by_cond[cid].append(float(val))
        secondary_metrics[key] = {
            cid: float(np.nanmean(vals)) if vals else float("nan")
            for cid, vals in by_cond.items()
        }

    # Bootstrap CIs on IFR per condition
    paired_all = build_paired_matrix(records, baseline_condition, "resolved")
    for cond in all_conditions:
        if cond == baseline_condition:
            b_outcomes = [float(r["resolved"]) for r in records if r["condition_id"] == cond]
            # Self-CI via bootstrap
            rng = np.random.default_rng(42)
            boot = [
                float(np.mean(rng.choice(b_outcomes, size=len(b_outcomes), replace=True)))
                for _ in range(n_bootstrap)
            ]
            alpha_ = 1.0 - ci_level
            ifr_ci_by_cond[cond] = (
                float(np.percentile(boot, 100 * alpha_ / 2)),
                float(np.percentile(boot, 100 * (1 - alpha_ / 2))),
            )
        elif cond in paired_all:
            a_outcomes = paired_all[cond]["ablation"]
            if a_outcomes:
                rng = np.random.default_rng(42)
                boot = [
                    float(np.mean(rng.choice(a_outcomes, size=len(a_outcomes), replace=True)))
                    for _ in range(n_bootstrap)
                ]
                alpha_ = 1.0 - ci_level
                ifr_ci_by_cond[cond] = (
                    float(np.percentile(boot, 100 * alpha_ / 2)),
                    float(np.percentile(boot, 100 * (1 - alpha_ / 2))),
                )
            else:
                ifr_ci_by_cond[cond] = (float("nan"), float("nan"))

    metadata = {
        "n_violation_records": len(records),
        "n_run_summaries": len(summaries),
        "baseline_condition": baseline_condition,
        "alpha": alpha,
        "n_bootstrap": n_bootstrap,
        "ci_level": ci_level,
        "all_conditions": all_conditions,
    }

    return StudyResults(
        pairwise=pairwise,
        ifr_by_condition=ifr_by_condition,
        ifr_ci_by_condition=ifr_ci_by_cond,
        secondary_metrics=secondary_metrics,
        metadata=metadata,
    )


def format_results_as_dict(results: StudyResults) -> dict[str, Any]:
    return {
        "pairwise_tests": [asdict(r) for r in results.pairwise],
        "ifr_by_condition": results.ifr_by_condition,
        "ifr_ci_by_condition": {
            k: list(v) for k, v in results.ifr_ci_by_condition.items()
        },
        "secondary_metrics": results.secondary_metrics,
        "metadata": results.metadata,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run ablation study statistical analysis")
    p.add_argument("--results-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--baseline", default="full")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-bootstrap", type=int, default=10_000)
    p.add_argument("--ci-level", type=float, default=0.95)
    args = p.parse_args()

    results = run_analysis(
        results_dir=args.results_dir,
        baseline_condition=args.baseline,
        alpha=args.alpha,
        n_bootstrap=args.n_bootstrap,
        ci_level=args.ci_level,
    )

    out = format_results_as_dict(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2))
        print(f"Results written to {args.output}")
    else:
        print(json.dumps(out, indent=2))

    # Print summary table
    print("\n── Pairwise Test Summary ────────────────────────────────────────────")
    print(f"{'Condition':<25} {'IFR_abl':>8} {'IFR_base':>9} {'Δ':>7} {'p_corr':>8} {'r':>6} {'sig':>4} {'power':>6}")
    print("-" * 80)
    for r in results.pairwise:
        sig = "*" if r.reject_h0 else ""
        print(
            f"{r.condition_id:<25} {r.ablation_mean:>8.4f} {r.baseline_mean:>9.4f}"
            f" {r.delta:>+7.4f} {r.p_value_corrected:>8.4f} {r.effect_size_r:>6.3f}"
            f" {sig:>4} {r.power:>6.3f}"
        )


if __name__ == "__main__":
    main()
