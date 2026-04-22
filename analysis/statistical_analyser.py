"""
Statistical analyser for the a11y-autofix experiment.

Implements all pre-specified confirmatory tests (H1–H4) and exploratory
descriptive analyses (regression rate ρ, bootstrap CIs) as defined in the
methodology (Section 3.7.3).

Confirmatory vs. exploratory distinction
-----------------------------------------
All result objects carry an `analysis_type` field:
  "confirmatory"  — pre-specified hypothesis tests (H1–H4)
  "exploratory"   — descriptive analyses generating hypotheses for future work
                    (failure mode heatmaps, layer rejection profiles, ρ, etc.)

The Reporter renders this flag visually:
  [Confirmatory — H{n}]  for pre-specified tests
  [Exploratory]          for descriptive analyses

References
----------
Methodology: Section 3.7.3 (Statistical Analysis)
H1 — Ablation study (McNemar's test)
H2 — Prompting strategy (Kruskal-Wallis + post-hoc + TE criterion)
H3 — LLM architecture (Kruskal-Wallis + post-hoc)
H4 — Issue category (Kruskal-Wallis + directional prediction)
H5 — Regression rate ρ (descriptive, exploratory)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

# Optional NumPy/SciPy — gracefully degraded if not installed.
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from scipy import stats as _scipy_stats
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ═══════════════════════════════════════════════════════════════════════════════
# Shared result types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AnalysisResult:
    """Base class for all analysis results."""

    analysis_type: Literal["confirmatory", "exploratory"]
    """
    "confirmatory": part of H1–H4 pre-specified tests.
    "exploratory":  failure mode heatmaps, layer rejection profiles, etc.
    """


@dataclass
class PairwiseResult:
    """Result of a single pairwise comparison (Mann-Whitney U)."""

    condition_a: str
    condition_b: str
    u_statistic: float
    p_value: float
    cliffs_delta: float
    significant: bool
    """True if p < alpha AND |delta| >= min_effect_size."""


@dataclass
class H1Result(AnalysisResult):
    """Result of H1 ablation test for one condition."""

    condition: str
    mcnemar_p: float
    cliffs_delta: float
    significant: bool
    """True when p < alpha AND |delta| >= min_effect_size (both criteria met)."""
    practically_negligible: bool
    """True when p < alpha but |delta| < min_effect_size."""


@dataclass
class H2Result(AnalysisResult):
    """Result of H2 prompting strategy test."""

    kruskal_p: float
    pairwise: dict[tuple[str, str], PairwiseResult] = field(default_factory=dict)
    preferred_strategy: str | None = None
    """Strategy meeting BOTH SR significance AND TE criterion (>=80% of zero-shot TE)."""


@dataclass
class H3Result(AnalysisResult):
    """Result of H3 LLM architecture test."""

    kruskal_p: float
    pairwise: dict[tuple[str, str], PairwiseResult] = field(default_factory=dict)
    best_model: str = ""
    """Model with highest median SR (not a causal claim)."""


@dataclass
class H4Result(AnalysisResult):
    """Result of H4 issue category test."""

    kruskal_p: float
    pairwise: dict[tuple[str, str], PairwiseResult] = field(default_factory=dict)
    directional_prediction_supported: bool = False
    """
    True if at least one (simple, complex) pair shows IFR_simple > IFR_complex
    with |delta| >= min_effect_size.
    """


@dataclass
class RegressionRateResult:
    """Regression rate ρ with bootstrap CI for one (model, category) stratum."""

    rho: float
    ci_lower: float
    ci_upper: float
    n: int
    """Number of patches evaluated in this stratum."""


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: Cliff's delta
# ═══════════════════════════════════════════════════════════════════════════════


def cliffs_delta(x: list[float], y: list[float]) -> float:
    """
    Compute Cliff's delta, a non-parametric effect size measure.

    δ = (number of pairs where x > y − number of pairs where x < y) / (n_x × n_y)

    Returns a value in [−1, 1].
    """
    if not x or not y:
        return 0.0
    n_x, n_y = len(x), len(y)
    dom = sum(
        (1 if xi > yi else -1 if xi < yi else 0)
        for xi in x
        for yi in y
    )
    return dom / (n_x * n_y)


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: Bootstrap CI
# ═══════════════════════════════════════════════════════════════════════════════


def bootstrap_ci(
    values: list[float],
    n_bootstrap: int = 2000,
    ci_level: float = 0.95,
    statistic: Callable[[list[float]], float] | None = None,
    seed: int = 42,
) -> tuple[float, float]:
    """
    Compute a bootstrap confidence interval for a statistic over values.

    Used for SR, IFR, MTTR, and regression rate ρ (H5).
    Methodology: Section 3.7.3 (Bootstrap CI, n=2000, CI=95%).

    Args:
        values: Observed sample values.
        n_bootstrap: Number of bootstrap resamples (default 2000).
        ci_level: Confidence level (default 0.95).
        statistic: Function to apply to each resample. Defaults to mean.
        seed: Random seed for reproducibility.

    Returns:
        (lower_bound, upper_bound) of the bootstrap CI.
    """
    if not values:
        return (0.0, 0.0)

    if statistic is None:
        def statistic(v: list[float]) -> float:  # type: ignore[misc]
            return sum(v) / len(v)

    rng = random.Random(seed)
    n = len(values)
    boot_stats: list[float] = []

    for _ in range(n_bootstrap):
        resample = [rng.choice(values) for _ in range(n)]
        boot_stats.append(statistic(resample))

    boot_stats.sort()
    alpha = 1.0 - ci_level
    lower_idx = max(0, int(math.floor(alpha / 2 * n_bootstrap)))
    upper_idx = min(n_bootstrap - 1, int(math.ceil((1 - alpha / 2) * n_bootstrap)))
    return (boot_stats[lower_idx], boot_stats[upper_idx])


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: McNemar's test
# ═══════════════════════════════════════════════════════════════════════════════


def mcnemar_test(
    outcome_a: list[int],
    outcome_b: list[int],
) -> float:
    """
    McNemar's test for paired binary outcomes.

    Both lists must be binary (0/1) and of equal length.
    Returns the two-sided p-value.

    Uses scipy.stats.chi2 if available, otherwise exact binomial fallback.
    """
    if len(outcome_a) != len(outcome_b):
        raise ValueError("McNemar's test requires equal-length paired lists.")

    # Discordant pairs
    b = sum(1 for a, bb in zip(outcome_a, outcome_b) if a == 1 and bb == 0)
    c = sum(1 for a, bb in zip(outcome_a, outcome_b) if a == 0 and bb == 1)

    n_discordant = b + c
    if n_discordant == 0:
        return 1.0

    if _HAS_SCIPY:
        # McNemar statistic with continuity correction
        chi2 = (abs(b - c) - 1) ** 2 / n_discordant
        p = float(_scipy_stats.chi2.sf(chi2, df=1))
    else:
        # Exact binomial: under H0 B ~ Binomial(b+c, 0.5)
        p = _binomial_exact_two_sided(b, n_discordant, 0.5)

    return p


def _binomial_exact_two_sided(k: int, n: int, p: float) -> float:
    """Two-sided exact binomial p-value (fallback without scipy)."""
    from math import comb

    def binom_pmf(k: int, n: int, p: float) -> float:
        return comb(n, k) * (p ** k) * ((1 - p) ** (n - k))

    observed_pmf = binom_pmf(k, n, p)
    total = sum(binom_pmf(i, n, p) for i in range(n + 1) if binom_pmf(i, n, p) <= observed_pmf)
    return min(1.0, total)


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: Kruskal-Wallis
# ═══════════════════════════════════════════════════════════════════════════════


def kruskal_wallis(*groups: list[float]) -> float:
    """
    Kruskal-Wallis H test for ≥2 groups.

    Returns the p-value. Uses scipy if available, otherwise a pure-Python
    implementation adequate for the expected sample sizes.
    """
    if _HAS_SCIPY:
        p = float(_scipy_stats.kruskal(*groups).pvalue)
        return p

    # Pure-Python approximation via chi-squared distribution of H statistic
    all_values: list[float] = []
    for g in groups:
        all_values.extend(g)

    n = len(all_values)
    if n == 0:
        return 1.0

    sorted_vals = sorted(all_values)
    ranks = {v: i + 1 for i, v in enumerate(sorted_vals)}

    H = 0.0
    for g in groups:
        if not g:
            continue
        n_j = len(g)
        rank_sum = sum(ranks[v] for v in g)
        H += (rank_sum ** 2) / n_j

    H = (12 / (n * (n + 1))) * H - 3 * (n + 1)

    # Chi-squared approximation with df = k - 1
    k = len(groups)
    df = k - 1
    if df <= 0:
        return 1.0

    if _HAS_SCIPY:
        return float(_scipy_stats.chi2.sf(H, df=df))

    # Minimal chi-squared survival function approximation
    return _chi2_sf_approx(H, df)


def _chi2_sf_approx(x: float, df: int) -> float:
    """Approximate chi-squared survival function using regularised gamma."""
    # Uses the fact that chi2(df) CDF = regularized_gamma(df/2, x/2)
    import math

    if x <= 0:
        return 1.0

    def gammainc_upper(a: float, x: float) -> float:
        """Upper regularised incomplete gamma (rough approximation)."""
        # Series expansion for small x
        if x < a + 1:
            s, term, n = 1.0, 1.0, 1
            while abs(term) > 1e-10 and n < 200:
                term *= x / (a + n)
                s += term
                n += 1
            return 1.0 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * s / a
        # Continued fraction for large x
        f, C, D = 0.0, 1e-300, 1.0 / (x - a + 1 + 1e-300)
        D = 1.0 / (x - a + 1 + 1e-300)
        C = 1 + 1e-300
        for n in range(1, 200):
            an = n * (a - n)
            D = 1.0 / (D * an + x - a + 2 * n + 1)
            C = (x - a + 2 * n + 1) + an / C
            delta = C * D
            f += math.log(abs(delta))
            if abs(delta - 1.0) < 1e-10:
                break
        return math.exp(-x + a * math.log(x) - math.lgamma(a) + f)

    return gammainc_upper(df / 2, x / 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: Mann-Whitney U (pairwise)
# ═══════════════════════════════════════════════════════════════════════════════


def mann_whitney_u(x: list[float], y: list[float]) -> tuple[float, float]:
    """
    Two-sided Mann-Whitney U test.

    Returns (U_statistic, p_value).
    """
    if _HAS_SCIPY:
        result = _scipy_stats.mannwhitneyu(x, y, alternative="two-sided")
        return float(result.statistic), float(result.pvalue)

    # Pure-Python fallback (approximate normal approximation)
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0

    U = sum(1 if xi > yi else 0.5 if xi == yi else 0 for xi in x for yi in y)
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return U, 1.0
    z = (U - mu) / sigma
    # Two-sided p from standard normal
    p = 2 * (1 - _norm_cdf(abs(z)))
    return U, p


def _norm_cdf(z: float) -> float:
    """Standard normal CDF via math.erfc."""
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _bonferroni_correct(p_values: list[float]) -> list[float]:
    """Apply Bonferroni correction to a list of p-values."""
    n = len(p_values)
    return [min(1.0, p * n) for p in p_values]


# ═══════════════════════════════════════════════════════════════════════════════
# H1: McNemar's test (Ablation)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h1_ablation(
    full_results: list[int],
    ablated_results: dict[str, list[int]],
    alpha: float = 0.05,
    min_effect_size: float = 0.147,
) -> dict[str, H1Result]:
    """
    H1 — Ablation study: compare full template vs each ablation condition.

    For each ablation condition c_j, apply McNemar's test comparing
    full_results vs ablated_results[c_j] on paired binary outcomes
    (same files, same order).

    'significant' = True only when BOTH criteria are met:
      p < alpha AND |delta| >= min_effect_size.
    (methodology Section 3.1.4, H1 minimum evidence criterion)

    Args:
        full_results: Binary SR per file for the full template condition.
        ablated_results: Mapping condition_name → binary SR per file.
        alpha: Significance level (default 0.05).
        min_effect_size: Minimum |Cliff's delta| (default 0.147, small effect).

    Returns:
        Dict of condition_name → H1Result.
    """
    # POST-HOC EXPLORATORY ANALYSIS note: ablation results are confirmatory
    # as H1 is pre-specified. See methodology Section 3.6.3 for the
    # distinction between H1 (confirmatory) and sensitivity study (exploratory).
    results: dict[str, H1Result] = {}

    for condition_name, ablated in ablated_results.items():
        if len(full_results) != len(ablated):
            raise ValueError(
                f"McNemar's test requires equal-length paired lists. "
                f"full={len(full_results)}, ablated[{condition_name}]={len(ablated)}"
            )

        p = mcnemar_test(full_results, ablated)
        delta = cliffs_delta(
            [float(v) for v in full_results],
            [float(v) for v in ablated],
        )
        abs_delta = abs(delta)

        significant = p < alpha and abs_delta >= min_effect_size
        practically_negligible = p < alpha and abs_delta < min_effect_size

        results[condition_name] = H1Result(
            analysis_type="confirmatory",
            condition=condition_name,
            mcnemar_p=round(p, 6),
            cliffs_delta=round(delta, 4),
            significant=significant,
            practically_negligible=practically_negligible,
        )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Kruskal-Wallis + post-hoc (Prompting Strategy)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h2_prompting_strategy(
    results_by_strategy: dict[str, list[int]],
    te_by_strategy: dict[str, float],
    zero_shot_te: float,
    alpha: float = 0.05,
    max_te_relative_drop: float = 0.20,
) -> H2Result:
    """
    H2 — Prompting strategy: Kruskal-Wallis + pairwise Mann-Whitney U.

    A strategy is 'preferred' only if:
      - SR is significantly higher than baseline (p < alpha, |delta| >= 0.147)
      - TE >= zero_shot_te * (1 − max_te_relative_drop)

    Args:
        results_by_strategy: strategy → list of binary SR per file.
        te_by_strategy: strategy → Token Efficiency value.
        zero_shot_te: Baseline TE for the zero-shot strategy.
        alpha: Significance level.
        max_te_relative_drop: Maximum tolerated TE drop relative to zero-shot.

    Returns:
        H2Result with omnibus p-value, pairwise results, and preferred_strategy.
    """
    strategy_names = list(results_by_strategy.keys())
    groups = [[float(v) for v in results_by_strategy[s]] for s in strategy_names]

    kruskal_p = kruskal_wallis(*groups) if len(groups) >= 2 else 1.0

    # Pairwise comparisons with Bonferroni correction
    pairs = [
        (strategy_names[i], strategy_names[j])
        for i in range(len(strategy_names))
        for j in range(i + 1, len(strategy_names))
    ]
    raw_p_values = []
    u_stats = []
    for a, b in pairs:
        u, p = mann_whitney_u(
            [float(v) for v in results_by_strategy[a]],
            [float(v) for v in results_by_strategy[b]],
        )
        raw_p_values.append(p)
        u_stats.append(u)

    corrected_p = _bonferroni_correct(raw_p_values)

    pairwise: dict[tuple[str, str], PairwiseResult] = {}
    for (a, b), u, p_corr in zip(pairs, u_stats, corrected_p):
        delta = cliffs_delta(
            [float(v) for v in results_by_strategy[a]],
            [float(v) for v in results_by_strategy[b]],
        )
        pairwise[(a, b)] = PairwiseResult(
            condition_a=a,
            condition_b=b,
            u_statistic=u,
            p_value=round(p_corr, 6),
            cliffs_delta=round(delta, 4),
            significant=p_corr < alpha and abs(delta) >= 0.147,
        )

    # Identify preferred strategy
    te_threshold = zero_shot_te * (1.0 - max_te_relative_drop)
    preferred_strategy: str | None = None

    zero_shot_name = "zero-shot"
    if zero_shot_name in results_by_strategy:
        zero_shot_group = [float(v) for v in results_by_strategy[zero_shot_name]]
        for strat in strategy_names:
            if strat == zero_shot_name:
                continue
            te_ok = te_by_strategy.get(strat, 0.0) >= te_threshold
            if not te_ok:
                continue
            # Check if SR is significantly higher than zero-shot
            key = (zero_shot_name, strat) if (zero_shot_name, strat) in pairwise else (strat, zero_shot_name)
            if key in pairwise and pairwise[key].significant:
                strat_group = [float(v) for v in results_by_strategy[strat]]
                strat_median = sorted(strat_group)[len(strat_group) // 2] if strat_group else 0
                zero_median = sorted(zero_shot_group)[len(zero_shot_group) // 2] if zero_shot_group else 0
                if strat_median > zero_median:
                    preferred_strategy = strat
                    break

    return H2Result(
        analysis_type="confirmatory",
        kruskal_p=round(kruskal_p, 6),
        pairwise=pairwise,
        preferred_strategy=preferred_strategy,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Kruskal-Wallis + post-hoc (LLM Architecture)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h3_llm_architecture(
    results_by_model: dict[str, list[int]],
    alpha: float = 0.05,
    min_effect_size: float = 0.147,
) -> H3Result:
    """
    H3 — LLM architecture: Kruskal-Wallis + pairwise Mann-Whitney U.

    Reports Cliff's delta for all significant pairs.

    Args:
        results_by_model: model_id → list of binary SR per file.
        alpha: Significance level.
        min_effect_size: Minimum |Cliff's delta|.

    Returns:
        H3Result with omnibus p-value, pairwise results, and best_model.
    """
    model_names = list(results_by_model.keys())
    groups = [[float(v) for v in results_by_model[m]] for m in model_names]

    kruskal_p = kruskal_wallis(*groups) if len(groups) >= 2 else 1.0

    pairs = [
        (model_names[i], model_names[j])
        for i in range(len(model_names))
        for j in range(i + 1, len(model_names))
    ]
    raw_p_values = []
    u_stats = []
    for a, b in pairs:
        u, p = mann_whitney_u(
            [float(v) for v in results_by_model[a]],
            [float(v) for v in results_by_model[b]],
        )
        raw_p_values.append(p)
        u_stats.append(u)

    corrected_p = _bonferroni_correct(raw_p_values)

    pairwise: dict[tuple[str, str], PairwiseResult] = {}
    for (a, b), u, p_corr in zip(pairs, u_stats, corrected_p):
        delta = cliffs_delta(
            [float(v) for v in results_by_model[a]],
            [float(v) for v in results_by_model[b]],
        )
        pairwise[(a, b)] = PairwiseResult(
            condition_a=a,
            condition_b=b,
            u_statistic=u,
            p_value=round(p_corr, 6),
            cliffs_delta=round(delta, 4),
            significant=p_corr < alpha and abs(delta) >= min_effect_size,
        )

    # Best model by highest median SR (not a causal claim)
    best_model = ""
    best_median = -1.0
    for name, group in zip(model_names, groups):
        if group:
            median = sorted(group)[len(group) // 2]
            if median > best_median:
                best_median = median
                best_model = name

    return H3Result(
        analysis_type="confirmatory",
        kruskal_p=round(kruskal_p, 6),
        pairwise=pairwise,
        best_model=best_model,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H4: Kruskal-Wallis + post-hoc (Issue Category)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h4_issue_category(
    ifr_by_category: dict[str, list[int]],
    simple_categories: list[str] | None = None,
    complex_categories: list[str] | None = None,
    alpha: float = 0.05,
    min_effect_size: float = 0.147,
) -> H4Result:
    """
    H4 — Issue category: Kruskal-Wallis + pairwise Mann-Whitney U.

    Directional prediction: at least one (simple, complex) pair must show
    IFR_simple > IFR_complex with |delta| >= min_effect_size.

    Args:
        ifr_by_category: category → list of binary IFR per issue.
        simple_categories: Issue categories expected to be simpler to fix.
        complex_categories: Issue categories expected to be harder to fix.
        alpha: Significance level.
        min_effect_size: Minimum |Cliff's delta|.

    Returns:
        H4Result with omnibus p-value, pairwise results, and directional flag.
    """
    if simple_categories is None:
        simple_categories = ["alt-text", "aria", "label"]
    if complex_categories is None:
        complex_categories = ["contrast", "semantic"]

    category_names = list(ifr_by_category.keys())
    groups = [[float(v) for v in ifr_by_category[c]] for c in category_names]

    kruskal_p = kruskal_wallis(*groups) if len(groups) >= 2 else 1.0

    pairs = [
        (category_names[i], category_names[j])
        for i in range(len(category_names))
        for j in range(i + 1, len(category_names))
    ]
    raw_p_values = []
    u_stats = []
    for a, b in pairs:
        u, p = mann_whitney_u(
            [float(v) for v in ifr_by_category[a]],
            [float(v) for v in ifr_by_category[b]],
        )
        raw_p_values.append(p)
        u_stats.append(u)

    corrected_p = _bonferroni_correct(raw_p_values)

    pairwise: dict[tuple[str, str], PairwiseResult] = {}
    for (a, b), u, p_corr in zip(pairs, u_stats, corrected_p):
        delta = cliffs_delta(
            [float(v) for v in ifr_by_category[a]],
            [float(v) for v in ifr_by_category[b]],
        )
        pairwise[(a, b)] = PairwiseResult(
            condition_a=a,
            condition_b=b,
            u_statistic=u,
            p_value=round(p_corr, 6),
            cliffs_delta=round(delta, 4),
            significant=p_corr < alpha and abs(delta) >= min_effect_size,
        )

    # Directional prediction: IFR_simple > IFR_complex with |delta| >= min_effect_size
    directional_prediction_supported = False
    for s_cat in simple_categories:
        for c_cat in complex_categories:
            if s_cat not in ifr_by_category or c_cat not in ifr_by_category:
                continue
            s_group = [float(v) for v in ifr_by_category[s_cat]]
            c_group = [float(v) for v in ifr_by_category[c_cat]]
            s_mean = sum(s_group) / len(s_group) if s_group else 0
            c_mean = sum(c_group) / len(c_group) if c_group else 0
            delta = cliffs_delta(s_group, c_group)
            if s_mean > c_mean and abs(delta) >= min_effect_size:
                directional_prediction_supported = True
                break
        if directional_prediction_supported:
            break

    return H4Result(
        analysis_type="confirmatory",
        kruskal_p=round(kruskal_p, 6),
        pairwise=pairwise,
        directional_prediction_supported=directional_prediction_supported,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H5: Regression Rate ρ (descriptive, exploratory)
# ═══════════════════════════════════════════════════════════════════════════════

# POST-HOC EXPLORATORY ANALYSIS — not part of confirmatory hypothesis tests
# See methodology Section 3.6.3


def compute_regression_rate(
    layer2_rejections: dict[str, dict[str, list[int]]],
    n_bootstrap: int = 2000,
) -> dict[str, dict[str, RegressionRateResult]]:
    """
    Compute regression rate ρ and 95% bootstrap CI per (model, category) stratum.

    This is DESCRIPTIVE, not a hypothesis test.

    ρ = fraction of all generated patches rejected at Layer 2 (structural heuristic),
    computed separately per model and per issue category.

    Args:
        layer2_rejections: model_id → category → list of binary
            (1=rejected at Layer 2, 0=not rejected).
        n_bootstrap: Number of bootstrap resamples for CI (default 2000).

    Returns:
        Nested dict: model_id → category → RegressionRateResult.
    """
    results: dict[str, dict[str, RegressionRateResult]] = {}

    for model_id, by_category in layer2_rejections.items():
        results[model_id] = {}
        for category, binary_outcomes in by_category.items():
            if not binary_outcomes:
                results[model_id][category] = RegressionRateResult(
                    rho=0.0, ci_lower=0.0, ci_upper=0.0, n=0,
                )
                continue

            float_outcomes = [float(v) for v in binary_outcomes]
            rho = sum(float_outcomes) / len(float_outcomes)
            ci_lower, ci_upper = bootstrap_ci(
                float_outcomes,
                n_bootstrap=n_bootstrap,
                ci_level=0.95,
            )
            results[model_id][category] = RegressionRateResult(
                rho=round(rho, 4),
                ci_lower=round(ci_lower, 4),
                ci_upper=round(ci_upper, 4),
                n=len(binary_outcomes),
            )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# H1 (Redesign): Detection Rate vs Threshold (Confirmatory)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h1_detection_rate(
    detection_rate: float,
    threshold: float = 0.80,
    n_issues_detected: int = 0,
    n_issues_ground_truth: int = 0,
) -> dict[str, Any]:
    """
    H1 (reformulado): Taxa de detecção δ ≥ 0,80 com consenso multi-ferramenta.

    Teste: comparação com threshold pré-definido (one-sample).
    CONFIRMATORY — pré-registrado.

    ATENÇÃO: δ calculado contra ground truth de ferramentas é CIRCULAR
    (as mesmas ferramentas definem e verificam). Para δ real, usar
    ground truth de fixtures sintéticos (C2.1) ou anotação humana (C4.1).

    Args:
        detection_rate: δ observado (0.0–1.0)
        threshold: Limiar pré-definido (default 0.80)
        n_issues_detected: Numerador de δ
        n_issues_ground_truth: Denominador de δ

    Returns:
        Dict com resultado do teste H1
    """
    passed = detection_rate >= threshold
    gap = round(detection_rate - threshold, 4)

    return {
        "hypothesis": "H1",
        "type": "confirmatory",
        "description": "Detection rate >= 0.80 with multi-tool consensus",
        "metric": "detection_rate",
        "observed": round(detection_rate, 4),
        "threshold": threshold,
        "gap": gap,
        "passed": passed,
        "n_detected": n_issues_detected,
        "n_ground_truth": n_issues_ground_truth,
        "validity_note": (
            "CIRCULAR if ground_truth derived from same scanner tools. "
            "Use fixture-based ground truth (C2.1) for valid δ."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# H3 (Redesign): Correction Rate vs Threshold (Confirmatory)
# ═══════════════════════════════════════════════════════════════════════════════


def test_h3_correction_rate_threshold(
    ifr: float,
    threshold: float = 0.70,
    n_fixed: int = 0,
    n_total: int = 0,
    effective_sr: float | None = None,
) -> dict[str, Any]:
    """
    H3 (reformulado): Taxa de correção τ ≥ 0,70 (Issue Fix Rate).

    Teste: comparação com threshold pré-definido.
    CONFIRMATORY — pré-registrado.

    Inclui SR efetivo (com penalidade noop) como métrica secundária.
    Se effective_sr for fornecido, reportar ambos os valores.

    Args:
        ifr: Issue Fix Rate observado (0.0–1.0)
        threshold: Limiar pré-definido (default 0.70)
        n_fixed: Número de issues corrigidas
        n_total: Número total de issues
        effective_sr: SR com penalidade noop (opcional, C1.1)

    Returns:
        Dict com resultado do teste H3
    """
    passed = ifr >= threshold

    result: dict[str, Any] = {
        "hypothesis": "H3",
        "type": "confirmatory",
        "description": "Issue Fix Rate >= 0.70",
        "metric": "ifr",
        "observed": round(ifr, 4),
        "threshold": threshold,
        "gap": round(ifr - threshold, 4),
        "passed": passed,
        "n_fixed": n_fixed,
        "n_total": n_total,
    }

    if effective_sr is not None:
        result["effective_sr"] = round(effective_sr, 4)
        result["noop_adjustment"] = round(effective_sr - ifr, 4)
        result["note"] = (
            "effective_sr penalizes patches with no actual code changes. "
            "Compare ifr vs effective_sr to assess model conservatism."
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# H5 (Confirmatory): Regression Rate Threshold Test
# ═══════════════════════════════════════════════════════════════════════════════


def test_h5_regression_threshold(
    regression_rate: float,
    threshold: float = 0.05,
    n_regressions: int = 0,
    n_attempts: int = 0,
    ci_lower: float | None = None,
    ci_upper: float | None = None,
) -> dict[str, Any]:
    """
    H5: Taxa de regressão ρ < 5% (confirmatory threshold test).

    ρ = patches rejeitados na Camada 2 / total de patches tentados.
    Rejeição na Camada 2 = regressão funcional (interface quebrada, export removido).

    CONFIRMATORY — pré-registrado. Complementa compute_regression_rate()
    que é exploratório/descritivo.

    Args:
        regression_rate: ρ observado (0.0–1.0)
        threshold: Limiar pré-definido (default 0.05)
        n_regressions: Número de regressões observadas
        n_attempts: Número total de patches tentados
        ci_lower: Limite inferior do IC 95% (bootstrap, opcional)
        ci_upper: Limite superior do IC 95% (bootstrap, opcional)

    Returns:
        Dict com resultado do teste H5
    """
    passed = regression_rate < threshold

    result: dict[str, Any] = {
        "hypothesis": "H5",
        "type": "confirmatory",
        "description": "Regression rate rho < 0.05",
        "metric": "regression_rate",
        "observed": round(regression_rate, 4),
        "threshold": threshold,
        "gap": round(threshold - regression_rate, 4),  # positivo = passou com folga
        "passed": passed,
        "n_regressions": n_regressions,
        "n_attempts": n_attempts,
    }

    if ci_lower is not None and ci_upper is not None:
        result["ci_95"] = [round(ci_lower, 4), round(ci_upper, 4)]
        result["ci_upper_passes"] = ci_upper < threshold
        result["note"] = (
            "ci_upper_passes=True means even the upper CI bound is below threshold "
            "(stronger evidence). ci_upper_passes=False means H5 is marginal."
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Exploratory: Model Size vs Performance
# ═══════════════════════════════════════════════════════════════════════════════


def explore_model_size_effect(
    ifr_by_model: dict[str, list[float]],
    model_sizes_b: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    EXPLORATÓRIO: Correlação entre tamanho do modelo (parâmetros) e IFR.

    Usa Spearman ρ para correlação não-paramétrica (adequado para escala ordinal
    de tamanho de modelo). Aplica correção de Bonferroni se múltiplos testes.

    POST-HOC EXPLORATORY ANALYSIS — não pré-registrado.
    P-valores não corrigidos devem ser interpretados com cautela.

    Args:
        ifr_by_model: {model_name: [ifr_per_file]} — IFR por arquivo por modelo
        model_sizes_b: {model_name: size_in_billions} — tamanho em bilhões

    Returns:
        Dict com correlação Spearman e plot_data para visualização
    """
    import re

    # Extrair tamanhos se não fornecidos
    if model_sizes_b is None:
        model_sizes_b = {}
        for model_name in ifr_by_model:
            match = re.search(r'(\d+(?:\.\d+)?)b', model_name.lower())
            if match:
                model_sizes_b[model_name] = float(match.group(1))

    sizes: list[float] = []
    mean_ifrs: list[float] = []
    model_labels: list[str] = []

    for model_name, ifr_list in ifr_by_model.items():
        size = model_sizes_b.get(model_name)
        if size is None or not ifr_list:
            continue
        sizes.append(size)
        mean_ifrs.append(sum(ifr_list) / len(ifr_list))
        model_labels.append(model_name)

    if len(sizes) < 3:
        return {
            "analysis": "model_size_vs_ifr",
            "type": "exploratory",
            "error": f"Insufficient data points: {len(sizes)} (need ≥ 3)",
        }

    # Spearman correlation (rank-based, robust to outliers)
    spearman_rho, p_value = _spearman_correlation(sizes, mean_ifrs)

    return {
        "analysis": "model_size_vs_ifr",
        "type": "exploratory",
        "test": "Spearman ρ (rank correlation)",
        "n_models": len(sizes),
        "spearman_rho": round(spearman_rho, 4),
        "p_value_uncorrected": round(p_value, 6),
        "interpretation": _interpret_spearman(spearman_rho, p_value),
        "plot_data": [
            {"model": label, "size_b": size, "mean_ifr": round(ifr, 4)}
            for label, size, ifr in zip(model_labels, sizes, mean_ifrs)
        ],
        "caveat": (
            "Exploratory — not pre-registered. "
            "p-value not corrected for multiple comparisons. "
            "Interpret as hypothesis for future confirmatory study."
        ),
    }


def _spearman_correlation(x: list[float], y: list[float]) -> tuple[float, float]:
    """Spearman rank correlation with p-value (no scipy dependency)."""
    n = len(x)
    if n < 3:
        return 0.0, 1.0

    def rank(vals: list[float]) -> list[float]:
        sorted_vals = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(sorted_vals):
            j = i
            while j < len(sorted_vals) - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx = rank(x)
    ry = rank(y)

    # Pearson on ranks = Spearman
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    sy = math.sqrt(sum((v - my) ** 2 for v in ry))

    if sx == 0 or sy == 0:
        return 0.0, 1.0

    rho = cov / (sx * sy)

    # t-statistic for significance
    if abs(rho) >= 1.0:
        return rho, 0.0

    t = rho * math.sqrt(n - 2) / math.sqrt(1 - rho ** 2)
    # Approximate p-value (two-sided) via t distribution
    # Use normal approximation for df > 30, else conservative estimate
    df = n - 2
    if df > 30:
        p = 2 * (1 - _norm_cdf(abs(t)))
    else:
        # Very rough approximation
        p = min(1.0, 2 * math.exp(-0.717 * abs(t) - 0.416 * t * t))

    return round(rho, 6), round(max(0.0, min(1.0, p)), 6)


def _interpret_spearman(rho: float, p: float) -> str:
    """Interpreta a correlação de Spearman."""
    if p >= 0.05:
        return f"No significant correlation (ρ={rho:.3f}, p={p:.3f})"
    strength = "strong" if abs(rho) >= 0.7 else "moderate" if abs(rho) >= 0.4 else "weak"
    direction = "positive" if rho > 0 else "negative"
    return f"Significant {strength} {direction} correlation (ρ={rho:.3f}, p={p:.3f})"


# ═══════════════════════════════════════════════════════════════════════════════
# Exploratory: Noop Analysis
# ═══════════════════════════════════════════════════════════════════════════════


def explore_noop_analysis(
    noop_rates_by_model: dict[str, float],
    sr_by_model: dict[str, float],
    effective_sr_by_model: dict[str, float],
) -> dict[str, Any]:
    """
    EXPLORATÓRIO: Analisa o impacto de noop patches no SR por modelo.

    Compara SR original vs SR efetivo (com penalidade noop).
    Modelos com alta noop_rate estão sendo artificialmente inflados.

    POST-HOC EXPLORATORY ANALYSIS — não pré-registrado.

    Args:
        noop_rates_by_model: {model: noop_rate}
        sr_by_model: {model: sr_original}
        effective_sr_by_model: {model: sr_efetivo}

    Returns:
        Dict com análise comparativa
    """
    comparisons: list[dict[str, Any]] = []
    for model in noop_rates_by_model:
        noop_rate = noop_rates_by_model.get(model, 0.0)
        sr = sr_by_model.get(model, 0.0)
        eff_sr = effective_sr_by_model.get(model, 0.0)
        inflation = round(sr - eff_sr, 4)
        inflation_pct = round(100 * inflation / sr, 1) if sr > 0 else 0.0

        comparisons.append({
            "model": model,
            "noop_rate": round(noop_rate, 4),
            "sr_original": round(sr, 4),
            "sr_effective": round(eff_sr, 4),
            "sr_inflation": inflation,
            "sr_inflation_pct": inflation_pct,
            "conservatism": (
                "high" if noop_rate > 0.20 else
                "medium" if noop_rate > 0.05 else
                "low"
            ),
        })

    comparisons.sort(key=lambda x: x["noop_rate"], reverse=True)

    return {
        "analysis": "noop_impact",
        "type": "exploratory",
        "description": "Impact of noop patches on SR metric",
        "per_model": comparisons,
        "most_conservative": comparisons[0]["model"] if comparisons else None,
        "least_conservative": comparisons[-1]["model"] if comparisons else None,
        "caveat": (
            "Exploratory. Models with high noop_rate should have their SR "
            "results reported with effective_sr (penalized) metric."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Exploratory: Contamination Sensitivity Analysis
# ═══════════════════════════════════════════════════════════════════════════════


def explore_contamination_sensitivity(
    ifr_all: list[float],
    ifr_low_risk: list[float],
    ifr_high_risk: list[float],
    alpha: float = 0.05,
) -> dict[str, Any]:
    """
    EXPLORATÓRIO: Analisa se resultados mudam quando projetos de alto risco
    de contaminação são excluídos.

    Se IFR_all ≈ IFR_low_risk, contamination provavelmente não é um problema.
    Se IFR_low_risk << IFR_all, existe evidência de inflação por memorização.

    POST-HOC EXPLORATORY ANALYSIS — não pré-registrado.

    Args:
        ifr_all: IFR com todos os projetos
        ifr_low_risk: IFR apenas com projetos de baixo risco
        ifr_high_risk: IFR apenas com projetos de alto risco

    Returns:
        Dict com análise de sensibilidade
    """
    mean_all = sum(ifr_all) / len(ifr_all) if ifr_all else 0.0
    mean_low = sum(ifr_low_risk) / len(ifr_low_risk) if ifr_low_risk else 0.0
    mean_high = sum(ifr_high_risk) / len(ifr_high_risk) if ifr_high_risk else 0.0

    delta_low_vs_all = round(mean_all - mean_low, 4)
    delta_high_vs_low = round(mean_high - mean_low, 4)

    # Teste estatístico: low vs high risk
    u_stat, p_value = mann_whitney_u(ifr_low_risk, ifr_high_risk)
    contamination_suspected = p_value < alpha and delta_high_vs_low > 0.05

    return {
        "analysis": "contamination_sensitivity",
        "type": "exploratory",
        "description": "IFR comparison with and without high-contamination-risk projects",
        "ifr_all_mean": round(mean_all, 4),
        "ifr_low_risk_mean": round(mean_low, 4),
        "ifr_high_risk_mean": round(mean_high, 4),
        "delta_low_vs_all": delta_low_vs_all,
        "delta_high_vs_low": delta_high_vs_low,
        "mann_whitney_p": round(p_value, 6),
        "contamination_suspected": contamination_suspected,
        "interpretation": (
            "Contamination likely inflating results — high-risk projects perform "
            f"significantly better (Δ={delta_high_vs_low:+.4f}, p={p_value:.4f})"
            if contamination_suspected else
            "No evidence of contamination — performance similar across risk levels "
            f"(Δ={delta_high_vs_low:+.4f}, p={p_value:.4f})"
        ),
        "recommendation": (
            "Report results separately for low-risk and high-risk subsets. "
            "Primary claims should use low-risk subset only."
            if contamination_suspected else
            "Contamination analysis supports validity of full-corpus results."
        ),
        "caveat": "Exploratory — contamination risk estimated heuristically (see C3.2).",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience: Run All Confirmatory Tests
# ═══════════════════════════════════════════════════════════════════════════════


def run_all_confirmatory_tests(
    *,
    detection_rate: float = 0.0,
    n_issues_detected: int = 0,
    n_issues_ground_truth: int = 0,
    full_results: list[int] | None = None,
    ablated_results: dict[str, list[int]] | None = None,
    results_by_strategy: dict[str, list[int]] | None = None,
    te_by_strategy: dict[str, float] | None = None,
    zero_shot_te: float = 0.0,
    results_by_model: dict[str, list[int]] | None = None,
    ifr_by_category: dict[str, list[int]] | None = None,
    best_ifr: float = 0.0,
    n_fixed: int = 0,
    n_total: int = 0,
    effective_sr: float | None = None,
    regression_rate: float = 0.0,
    n_regressions: int = 0,
    n_attempts: int = 0,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """
    Executa TODOS os testes confirmatórios (H1–H5) em sequência.

    Retorna um dict com todos os resultados, marcados como "confirmatory".
    Deve ser executado UMA ÚNICA VEZ após coleta de todos os dados.

    Args: (todos opcionais — testes sem dados suficientes são pulados)

    Returns:
        Dict com resultados de H1–H5
    """
    results: dict[str, Any] = {
        "analysis_type": "confirmatory",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "alpha": alpha,
        "note": (
            "These are pre-registered confirmatory tests. "
            "Do not modify thresholds or test choices after seeing data."
        ),
    }

    # H1: Detection rate
    results["H1"] = test_h1_detection_rate(
        detection_rate=detection_rate,
        n_issues_detected=n_issues_detected,
        n_issues_ground_truth=n_issues_ground_truth,
    )

    # H1 (ablation, original): scanner ablation
    if full_results and ablated_results:
        results["H1_ablation"] = test_h1_ablation(full_results, ablated_results, alpha=alpha)

    # H2: Prompting strategy
    if results_by_strategy and te_by_strategy:
        results["H2"] = test_h2_prompting_strategy(
            results_by_strategy=results_by_strategy,
            te_by_strategy=te_by_strategy,
            zero_shot_te=zero_shot_te,
            alpha=alpha,
        )

    # H3: LLM architecture comparison
    if results_by_model:
        results["H3_architecture"] = test_h3_llm_architecture(
            results_by_model=results_by_model,
            alpha=alpha,
        )

    # H3: Correction rate threshold
    results["H3_threshold"] = test_h3_correction_rate_threshold(
        ifr=best_ifr,
        n_fixed=n_fixed,
        n_total=n_total,
        effective_sr=effective_sr,
    )

    # H4: Issue category
    if ifr_by_category:
        results["H4"] = test_h4_issue_category(
            ifr_by_category=ifr_by_category,
            alpha=alpha,
        )

    # H5: Regression rate
    results["H5"] = test_h5_regression_threshold(
        regression_rate=regression_rate,
        n_regressions=n_regressions,
        n_attempts=n_attempts,
    )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience: Run All Exploratory Analyses
# ═══════════════════════════════════════════════════════════════════════════════


def run_all_exploratory_analyses(
    *,
    ifr_by_model: dict[str, list[float]] | None = None,
    noop_rates: dict[str, float] | None = None,
    sr_by_model: dict[str, float] | None = None,
    effective_sr_by_model: dict[str, float] | None = None,
    layer2_rejections: dict[str, dict[str, list[int]]] | None = None,
    ifr_all: list[float] | None = None,
    ifr_low_risk: list[float] | None = None,
    ifr_high_risk: list[float] | None = None,
) -> dict[str, Any]:
    """
    Executa TODAS as análises exploratórias disponíveis.

    Análises exploratórias geram hipóteses para estudos futuros.
    P-valores NÃO devem ser usados para confirmação sem pré-registro.

    Returns:
        Dict com todos os resultados exploratórios
    """
    results: dict[str, Any] = {
        "analysis_type": "exploratory",
        "note": (
            "Exploratory analyses — not pre-registered. "
            "Treat p-values as hypothesis-generating, not hypothesis-confirming. "
            "All findings must be replicated in a pre-registered study."
        ),
    }

    if ifr_by_model:
        results["model_size_effect"] = explore_model_size_effect(ifr_by_model)

    if noop_rates and sr_by_model and effective_sr_by_model:
        results["noop_impact"] = explore_noop_analysis(
            noop_rates, sr_by_model, effective_sr_by_model
        )

    if layer2_rejections:
        results["regression_rate_by_stratum"] = compute_regression_rate(layer2_rejections)

    if ifr_all and ifr_low_risk is not None and ifr_high_risk is not None:
        results["contamination_sensitivity"] = explore_contamination_sensitivity(
            ifr_all=ifr_all,
            ifr_low_risk=ifr_low_risk,
            ifr_high_risk=ifr_high_risk,
        )

    return results
