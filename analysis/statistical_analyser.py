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
from typing import Callable, Literal

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
