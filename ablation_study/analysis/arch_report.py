"""
arch_report.py
==============
Report generator for the ARCHITECTURAL ablation study (pipeline format,
Pa11y re-scan oracle — results/arch_ablation_pipeline/).

This is a thin wrapper around ablation_report.py that:
  * uses ``arch_full`` as the baseline (not the prompt-component ``full``),
  * relabels the five architectural conditions,
  * forces file-level pairing (the statistically correct unit; avoids the
    pseudo-replication that violation-level pairing introduces),
  * additionally emits a functional-clean IFR table/JSON that decomposes the
    Pa11y IFR into functionally-clean fixes vs. fixes that introduced a
    structural regression (Layer 2). The functional fields are produced by
    arch_ablation_pipeline_runner.py; on data collected before that change they
    default to ``resolved`` and the JSON flags ``has_functional_field=false``.

Usage:
    python -m ablation_study.analysis.arch_report \
        --results-dir ablation_study/results/arch_ablation_pipeline \
        --output-dir  ablation_study/results/arch_ablation_pipeline/report
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# ablation_study/analysis/ → ablation_study/ → a11y_experiment/ (package root)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ablation_study.analysis import ablation_report as rep
from ablation_study.src.statistical_analysis import (
    format_results_as_dict,
    load_violation_records,
    run_analysis,
)

_ARCH_ORDER = [
    "arch_full",
    "arch_no_reflection",
    "arch_no_few_shot",
    "arch_no_wcag_guidelines",
    "arch_no_internal_validation",
]
_ARCH_LABELS = {
    "arch_full":                   r"\textbf{Full pipeline (baseline)}",
    "arch_no_reflection":          r"$-$ Reflection feedback",
    "arch_no_few_shot":            r"$-$ Few-shot examples",
    "arch_no_wcag_guidelines":     r"$-$ WCAG semantic guidelines",
    "arch_no_internal_validation": r"$-$ Internal validation (L1--L3)",
}
_ARCH_PLAIN = {
    "arch_full":                   "Full pipeline",
    "arch_no_reflection":          "−Reflection",
    "arch_no_few_shot":            "−Few-shot",
    "arch_no_wcag_guidelines":     "−WCAG guidelines",
    "arch_no_internal_validation": "−Internal validation",
}


def _functional_clean(results_dir: Path) -> dict:
    """Decompose Pa11y IFR into functionally-clean vs. regression fixes."""
    records = load_violation_records(results_dir)
    has_field = any("resolved_functional_clean" in r for r in records)
    agg: dict[str, list] = defaultdict(lambda: [0, 0, 0])  # resolved, clean, total
    for r in records:
        cell = agg[r["condition_id"]]
        cell[2] += 1
        if r.get("resolved"):
            cell[0] += 1
        if r.get("resolved_functional_clean", r.get("resolved")):
            cell[1] += 1
    return {
        "has_functional_field": has_field,
        "note": (
            "Field present — functional check populated this run."
            if has_field else
            "Field ABSENT (data predates the V5 functional check). "
            "resolved_functional_clean falls back to resolved, so the "
            "functional-clean IFR equals the raw IFR until a re-run on the "
            "corpus machine populates Layer-2 results for arch_no_internal_validation."
        ),
        "by_condition": {
            c: {
                "ifr": round(v[0] / v[2], 4) if v[2] else 0.0,
                "ifr_functional_clean": round(v[1] / v[2], 4) if v[2] else 0.0,
                "n_violation_rows": v[2],
            }
            for c, v in agg.items()
        },
    }


def _functional_clean_table(fc: dict) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Functional-clean IFR: Pa11y-credited fixes that also preserved "
        r"the component (Layer~2). Only \texttt{arch\_no\_internal\_validation} can "
        r"differ from the raw IFR; for all other conditions Layer~2 was an enforced "
        r"gate, so every credited fix is clean by construction.}",
        r"\label{tab:arch_functional_clean}",
        r"\begin{tabular}{lrr}",
        r"\toprule",
        r"Condition & IFR (\%) & Functional-clean IFR (\%) \\",
        r"\midrule",
    ]
    by = fc["by_condition"]
    for c in _ARCH_ORDER:
        if c not in by:
            continue
        v = by[c]
        lines.append(
            f"  {_ARCH_LABELS.get(c, c)} & {v['ifr'] * 100:.2f} & "
            f"{v['ifr_functional_clean'] * 100:.2f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    if not fc["has_functional_field"]:
        lines.append(r"% NOTE: functional fields absent in this data — columns are identical.")
    return "\n".join(lines)


def generate(results_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Relabel ablation_report's module-level tables for the arch conditions.
    rep._CONDITION_ORDER[:] = _ARCH_ORDER
    rep._CONDITION_LABELS.clear(); rep._CONDITION_LABELS.update(_ARCH_LABELS)
    rep._PLAIN_LABELS.clear(); rep._PLAIN_LABELS.update(_ARCH_PLAIN)

    print("Running statistical analysis (baseline=arch_full, pairing=file)…")
    results = run_analysis(results_dir, baseline_condition="arch_full", pairing="file")
    # ablation_report's delta column looks up "full"; alias it to the arch baseline.
    results.ifr_by_condition["full"] = results.ifr_by_condition["arch_full"]

    (output_dir / "stats_results.json").write_text(
        json.dumps(format_results_as_dict(results), indent=2)
    )
    (output_dir / "ablation_table_ifr.tex").write_text(rep.latex_ifr_table(results))
    (output_dir / "ablation_table_tests.tex").write_text(rep.latex_test_table(results))
    (output_dir / "ablation_table_secondary.tex").write_text(rep.latex_secondary_table(results))

    fc = _functional_clean(results_dir)
    (output_dir / "functional_clean_ifr.json").write_text(json.dumps(fc, indent=2))
    (output_dir / "arch_table_functional_clean.tex").write_text(_functional_clean_table(fc))

    if rep._HAS_MPL:
        rep.plot_ifr_bar(results, output_dir / "ablation_figure_ifr.pdf")
        rep.plot_contribution_heatmap(results, output_dir / "ablation_figure_heatmap.pdf")

    for p in sorted(output_dir.glob("*")):
        print(f"  ✓ {p}")

    print("\nfunctional field present in data:", fc["has_functional_field"])
    print("Pairwise (file-level, Holm-Bonferroni):")
    for t in sorted(results.pairwise, key=lambda t: _ARCH_ORDER.index(t.condition_id)):
        flag = "*" if t.reject_h0 else " "
        print(
            f"  {flag} {t.condition_id:30} n={t.n_pairs:3d} Δ={t.delta:+.3f} "
            f"p_raw={t.p_value_raw:.4g} p_corr={t.p_value_corrected:.4g} r={t.effect_size_r:.3f}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Generate ARCHITECTURAL ablation report")
    p.add_argument(
        "--results-dir", type=Path,
        default=Path("ablation_study/results/arch_ablation_pipeline"),
    )
    p.add_argument(
        "--output-dir", type=Path,
        default=Path("ablation_study/results/arch_ablation_pipeline/report"),
    )
    args = p.parse_args()
    generate(args.results_dir, args.output_dir)


if __name__ == "__main__":
    main()
