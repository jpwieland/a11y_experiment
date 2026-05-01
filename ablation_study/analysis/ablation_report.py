"""
ablation_report.py
==================
Generates paper-ready LaTeX tables and matplotlib figures from
StatisticalAnalysis results.

Outputs:
  ablation_table_ifr.tex      — Table 3: IFR per condition with CIs
  ablation_table_tests.tex    — Table 4: Wilcoxon results with effect sizes
  ablation_figure_ifr.pdf     — Figure 4: IFR bar chart with CIs
  ablation_figure_heatmap.pdf — Figure 5: component contribution heatmap

Usage:
    python -m ablation_study.analysis.ablation_report \
        --results-dir ablation_study/results \
        --output-dir ablation_study/results/report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

# ablation_study/analysis/ → ablation_study/ → a11y_experiment/ (package root)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ablation_study.src.statistical_analysis import (
    StudyResults,
    format_results_as_dict,
    run_analysis,
)


# ── Condition ordering and display labels ─────────────────────────────────────

_CONDITION_ORDER = [
    "full",
    "minus_role",
    "minus_user_impact",
    "minus_code_context",
    "minus_constraints",
    "minus_few_shot",
    "minus_output_format",
    "raw_baseline",
]

_CONDITION_LABELS = {
    "full":               r"\textbf{Full (baseline)}",
    "minus_role":         r"$-$ Role (C1)",
    "minus_user_impact":  r"$-$ User Impact (C2)",
    "minus_code_context": r"$-$ Code Context (C3)",
    "minus_constraints":  r"$-$ Constraints (C4)",
    "minus_few_shot":     r"$-$ Few-Shot (C5)",
    "minus_output_format":r"$-$ Output Format (C6)",
    "raw_baseline":       r"Raw Baseline",
}

_PLAIN_LABELS = {
    "full":               "Full (baseline)",
    "minus_role":         "−Role (C1)",
    "minus_user_impact":  "−User Impact (C2)",
    "minus_code_context": "−Code Context (C3)",
    "minus_constraints":  "−Constraints (C4)",
    "minus_few_shot":     "−Few-Shot (C5)",
    "minus_output_format":"−Output Format (C6)",
    "raw_baseline":       "Raw Baseline",
}


# ── LaTeX table: IFR by condition with CIs ────────────────────────────────────

def latex_ifr_table(results: StudyResults) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{IFR by Ablation Condition (mean $\pm$ 95\% CI across three repetitions)}",
        r"\label{tab:ablation_ifr}",
        r"\begin{tabular}{lrll}",
        r"\toprule",
        r"Condition & IFR (\%) & 95\% CI & $\Delta$ vs.\ full \\",
        r"\midrule",
    ]

    baseline_ifr = results.ifr_by_condition.get("full", float("nan"))

    for cid in _CONDITION_ORDER:
        ifr = results.ifr_by_condition.get(cid, float("nan"))
        ci  = results.ifr_ci_by_condition.get(cid, (float("nan"), float("nan")))
        label = _CONDITION_LABELS.get(cid, cid)
        ifr_pct = ifr * 100
        ci_lo   = ci[0] * 100
        ci_hi   = ci[1] * 100
        delta   = (ifr - baseline_ifr) * 100

        if cid == "full":
            delta_str = "—"
        else:
            sign = "+" if delta >= 0 else ""
            delta_str = f"{sign}{delta:.1f}pp"

        ci_str = f"[{ci_lo:.1f}, {ci_hi:.1f}]"
        lines.append(
            f"  {label} & {ifr_pct:.2f} & {ci_str} & {delta_str} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


# ── LaTeX table: Wilcoxon test results ────────────────────────────────────────

def latex_test_table(results: StudyResults) -> str:
    test_by_cond = {r.condition_id: r for r in results.pairwise}

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Wilcoxon Signed-Rank Tests: Each Ablation Condition vs.\ Full Baseline "
        r"(Holm-Bonferroni corrected, $\alpha = 0.05$)}",
        r"\label{tab:ablation_tests}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"Condition & $n$ & $W$ & $p_\mathrm{raw}$ & $p_\mathrm{corr}$ & $r$ & Power \\",
        r"\midrule",
    ]

    for cid in _CONDITION_ORDER:
        if cid == "full":
            continue
        t = test_by_cond.get(cid)
        label = _CONDITION_LABELS.get(cid, cid)
        if t is None:
            lines.append(f"  {label} & — & — & — & — & — & — \\\\")
            continue

        sig_marker = r"$^{*}$" if t.reject_h0 else ""
        p_corr_str = (
            f"< 0.001" if t.p_value_corrected < 0.001
            else f"{t.p_value_corrected:.3f}"
        )
        p_raw_str = (
            f"< 0.001" if t.p_value_raw < 0.001
            else f"{t.p_value_raw:.3f}"
        )

        lines.append(
            f"  {label} & {t.n_pairs} & {t.w_statistic:.0f} & "
            f"{p_raw_str} & {p_corr_str}{sig_marker} & "
            f"{t.effect_size_r:.3f} & {t.power:.3f} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\multicolumn{7}{l}{\small $^{*}$ Significant after Holm-Bonferroni correction.} \\",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


# ── Matplotlib: IFR bar chart ─────────────────────────────────────────────────

def plot_ifr_bar(results: StudyResults, output_path: Path) -> None:
    if not _HAS_MPL:
        return

    conds  = _CONDITION_ORDER
    labels = [_PLAIN_LABELS.get(c, c) for c in conds]
    ifrs   = [results.ifr_by_condition.get(c, 0.0) * 100 for c in conds]
    cis    = [results.ifr_ci_by_condition.get(c, (0.0, 0.0)) for c in conds]
    errs_lo = [ifrs[i] - cis[i][0] * 100 for i in range(len(conds))]
    errs_hi = [cis[i][1] * 100 - ifrs[i] for i in range(len(conds))]

    fig, ax = plt.subplots(figsize=(9, 4))

    colors = ["#2c7bb6" if c == "full" else "#d7191c" if c == "raw_baseline" else "#abd9e9"
              for c in conds]

    bars = ax.bar(labels, ifrs, color=colors, width=0.6, zorder=2)
    ax.errorbar(
        x=range(len(conds)),
        y=ifrs,
        yerr=[errs_lo, errs_hi],
        fmt="none",
        color="black",
        capsize=4,
        linewidth=1.2,
        zorder=3,
    )

    # Significance markers
    test_by_cond = {r.condition_id: r for r in results.pairwise}
    for i, cid in enumerate(conds):
        t = test_by_cond.get(cid)
        if t and t.reject_h0:
            ax.text(i, ifrs[i] + errs_hi[i] + 0.5, "*", ha="center", va="bottom",
                    fontsize=14, color="#c0392b")

    ax.set_ylabel("IFR (%)", fontsize=11)
    ax.set_xlabel("Ablation Condition", fontsize=11)
    ax.set_title("Issue Fix Rate by Prompt-Component Ablation Condition", fontsize=12)
    ax.set_xticks(range(len(conds)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)
    ax.set_ylim(0, max(ifrs) * 1.2 + 5)
    ax.grid(axis="y", alpha=0.3, zorder=1)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    legend_patches = [
        mpatches.Patch(color="#2c7bb6", label="Full baseline"),
        mpatches.Patch(color="#abd9e9", label="Ablation condition"),
        mpatches.Patch(color="#d7191c", label="Raw baseline"),
    ]
    ax.legend(handles=legend_patches, fontsize=9, loc="upper right")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


# ── Matplotlib: component contribution heatmap ────────────────────────────────

def plot_contribution_heatmap(results: StudyResults, output_path: Path) -> None:
    if not _HAS_MPL:
        return

    # Δ IFR = IFR(full) - IFR(minus_X) → contribution of component X
    baseline = results.ifr_by_condition.get("full", float("nan"))

    component_conditions = [c for c in _CONDITION_ORDER
                            if c not in ("full", "raw_baseline")]
    component_labels = [_PLAIN_LABELS.get(c, c) for c in component_conditions]

    # Categories
    categories = ["alt_text", "label", "semantic", "contrast", "aria", "keyboard", "focus"]
    # For per-category contribution we need secondary per-category data
    # (from summaries if available, else use overall delta as proxy)

    delta_overall = np.array([
        (baseline - results.ifr_by_condition.get(c, baseline)) * 100
        for c in component_conditions
    ])

    fig, ax = plt.subplots(figsize=(5, 4))
    im = ax.imshow(
        delta_overall.reshape(1, -1),
        cmap="RdYlGn",
        aspect="auto",
        vmin=-10,
        vmax=max(10.0, float(delta_overall.max()) + 2),
    )

    ax.set_xticks(range(len(component_labels)))
    ax.set_xticklabels(component_labels, rotation=40, ha="right", fontsize=9)
    ax.set_yticks([])
    ax.set_title("IFR drop when component removed (Δ pp)", fontsize=11)

    for j, val in enumerate(delta_overall):
        ax.text(j, 0, f"{val:+.1f}", ha="center", va="center", fontsize=10,
                color="black" if abs(val) < 7 else "white")

    plt.colorbar(im, ax=ax, orientation="horizontal", pad=0.2, label="ΔIFR (pp)")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


# ── Secondary metrics table ───────────────────────────────────────────────────

def latex_secondary_table(results: StudyResults) -> str:
    metric_labels = {
        "sr_violation_files": r"SR (\%)",
        "mttr_s": r"MTTR (s)",
        "tokens_output_per_fix": r"Tokens/fix",
        "layer1_fail_rate": r"L1 fail (\%)",
        "layer2_fail_rate": r"L2 fail (\%)",
        "layer3_fail_rate": r"L3 fail (\%)",
    }

    metrics = list(metric_labels.keys())

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Secondary Metrics by Ablation Condition}",
        r"\label{tab:ablation_secondary}",
        r"\begin{tabular}{l" + "r" * len(metrics) + "}",
        r"\toprule",
        r"Condition & " + " & ".join(metric_labels.values()) + r" \\",
        r"\midrule",
    ]

    for cid in _CONDITION_ORDER:
        label = _CONDITION_LABELS.get(cid, cid)
        vals = []
        for m in metrics:
            v = results.secondary_metrics.get(m, {}).get(cid, float("nan"))
            if "rate" in m or "sr" in m:
                vals.append(f"{v * 100:.1f}" if not np.isnan(v) else "—")
            elif m == "mttr_s":
                vals.append(f"{v:.2f}" if not np.isnan(v) else "—")
            else:
                vals.append(f"{v:.0f}" if not np.isnan(v) else "—")
        lines.append(f"  {label} & " + " & ".join(vals) + r" \\")

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


# ── Main report generator ─────────────────────────────────────────────────────

def generate_report(results_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Running statistical analysis…")
    results = run_analysis(results_dir)

    # Write JSON
    json_path = output_dir / "stats_results.json"
    json_path.write_text(json.dumps(format_results_as_dict(results), indent=2))
    print(f"  ✓ {json_path}")

    # LaTeX tables
    for name, content in [
        ("ablation_table_ifr.tex",     latex_ifr_table(results)),
        ("ablation_table_tests.tex",   latex_test_table(results)),
        ("ablation_table_secondary.tex", latex_secondary_table(results)),
    ]:
        path = output_dir / name
        path.write_text(content)
        print(f"  ✓ {path}")

    # Figures
    if _HAS_MPL:
        plot_ifr_bar(results, output_dir / "ablation_figure_ifr.pdf")
        print(f"  ✓ {output_dir / 'ablation_figure_ifr.pdf'}")

        plot_contribution_heatmap(results, output_dir / "ablation_figure_heatmap.pdf")
        print(f"  ✓ {output_dir / 'ablation_figure_heatmap.pdf'}")
    else:
        print("  ⚠ matplotlib not installed — figures skipped")

    print("\nDone. Paste LaTeX tables directly into your paper or \\input{} them.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Generate ablation study report")
    p.add_argument("--results-dir", type=Path, required=True,
                   help="Directory containing condition/model/rep subdirs")
    p.add_argument("--output-dir", type=Path,
                   default=Path("ablation_study/results/report"),
                   help="Where to write .tex and .pdf files")
    args = p.parse_args()

    generate_report(args.results_dir, args.output_dir)


if __name__ == "__main__":
    main()
