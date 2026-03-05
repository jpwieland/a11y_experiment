"""
Cálculo de métricas comparativas para experimentos multi-modelo.

Metric definitions (methodology Section 3.7.1):

  SR  (Success Rate, file-level binary):
      SR = |{f ∈ F : patch(f) passes all four validation layers}| / |F|
      A file contributes 1 only if its patch passes ALL four layers.
      Partial fixes within a file count as SR=0 for that file.

  IFR (Issue Fix Rate, issue-level with partial credit):
      IFR = Σ |I_fixed(f)| / Σ |I(f)|
      A file with 3 issues where 2 are fixed contributes 2/3 to IFR.

  MTTR (Mean Time To Repair):
      Computed only over F_fixed (files where a patch was ultimately accepted).
      Failed-repair wall-clock time is NOT included.

  TE  (Token Efficiency):
      TE = (IFR × |I|) / (C_total / 1000)
      where C_total is the sum of input tokens across all prompts in the condition.
"""

from __future__ import annotations

from typing import Any

from a11y_autofix.config import FixResult


def compute_te(
    ifr: float,
    total_issues: int,
    total_input_tokens: int,
) -> float:
    """
    Token Efficiency = (IFR × |I|) / (C_total / 1000)

    Args:
        ifr: Issue Fix Rate for the condition (0.0–1.0).
        total_issues: Total number of issues |I| in the condition.
        total_input_tokens: Sum of input tokens across all prompts (C_total).

    Returns:
        Token efficiency score, or 0.0 if total_input_tokens is zero.
    """
    if total_input_tokens == 0:
        return 0.0
    return (ifr * total_issues) / (total_input_tokens / 1000)


def compute_sr(results: list[FixResult]) -> float:
    """
    File-level Success Rate (methodology Eq. 3.1).

    A file contributes 1 to the numerator only if its patch passes
    all four validation layers (final_success=True). Partial fixes = 0.

    Returns:
        SR ∈ [0, 1].
    """
    if not results:
        return 0.0
    return sum(1 for r in results if r.final_success) / len(results)


def compute_ifr(results: list[FixResult]) -> tuple[float, int, int]:
    """
    Issue Fix Rate with partial credit (methodology Eq. 3.2).

    Returns:
        Tuple of (IFR, total_issues_fixed, total_issues).
    """
    total_fixed = sum(r.issues_fixed for r in results)
    total_issues = sum(len(r.scan_result.issues) for r in results)
    ifr = total_fixed / total_issues if total_issues > 0 else 0.0
    return ifr, total_fixed, total_issues


def compute_mttr(results: list[FixResult]) -> float | None:
    """
    Mean Time To Repair, computed only over successfully repaired files.

    Files where no patch was accepted are excluded. Returns None if no
    file was successfully repaired.
    """
    fixed_times = [r.total_time for r in results if r.final_success]
    if not fixed_times:
        return None
    return sum(fixed_times) / len(fixed_times)


def compute_experiment_metrics(
    results_by_model: dict[str, list[FixResult]],
    input_tokens_by_model: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Calcula métricas agregadas por modelo para um experimento.

    Metric definitions follow methodology Section 3.7.1:
      - sr: file-level binary success rate (Eq. 3.1)
      - ifr: issue-level fix rate with partial credit (Eq. 3.2)
      - mttr: mean time to repair, computed only over F_fixed
      - te: token efficiency = (IFR × |I|) / (C_total / 1000)

    Args:
        results_by_model: Dicionário com resultados por modelo.
        input_tokens_by_model: Optional dict of total input tokens per model
            (for TE computation). If None, TE is set to None.

    Returns:
        Dicionário com métricas por modelo.
    """
    metrics: dict[str, dict[str, Any]] = {}
    _input_tokens = input_tokens_by_model or {}

    for model_name, results in results_by_model.items():
        if not results:
            metrics[model_name] = {
                "sr": 0.0,
                "ifr": 0.0,
                "mttr": None,
                "te": None,
                "success_rate": 0.0,  # kept for backwards compatibility (= sr * 100)
                "avg_time": 0.0,
                "issues_fixed": 0,
                "issues_pending": 0,
                "total_attempts": 0,
                "files_processed": 0,
                "files_successful": 0,
                "total_tokens": None,
            }
            continue

        total = len(results)

        # Primary metrics (methodology-aligned)
        sr = compute_sr(results)
        ifr, total_fixed, total_issues = compute_ifr(results)
        mttr = compute_mttr(results)

        # Token counts
        tokens_list = [
            a.tokens_used
            for r in results
            for a in r.attempts
            if a.tokens_used is not None
        ]
        total_tokens = sum(tokens_list) if tokens_list else None
        total_attempts = sum(len(r.attempts) for r in results)

        # Token Efficiency (per condition — requires input token count from API response)
        input_tokens = _input_tokens.get(model_name, 0)
        te: float | None = None
        if input_tokens > 0:
            te = compute_te(ifr, total_issues, input_tokens)
        elif total_tokens is not None and total_tokens > 0:
            # Fallback: use total tokens as approximation when prompt_tokens not available
            te = compute_te(ifr, total_issues, total_tokens)

        total_pending = sum(r.issues_pending for r in results)
        total_time = sum(r.total_time for r in results)

        metrics[model_name] = {
            # Methodology-aligned primary metrics
            "sr": round(sr, 4),
            "ifr": round(ifr, 4),
            "mttr": round(mttr, 3) if mttr is not None else None,
            "te": round(te, 4) if te is not None else None,
            # Legacy / convenience fields
            "success_rate": round(sr * 100, 2),  # percentage
            "avg_time": round(total_time / total, 3) if total > 0 else 0.0,
            "issues_fixed": total_fixed,
            "issues_pending": total_pending,
            "total_attempts": total_attempts,
            "files_processed": total,
            "files_successful": sum(1 for r in results if r.final_success),
            "total_tokens": total_tokens,
            "avg_tokens": (total_tokens / total_attempts) if (total_tokens and total_attempts) else None,
        }

    return metrics


def compute_per_issue_type_metrics(
    results_by_model: dict[str, list[FixResult]],
) -> dict[str, dict[str, Any]]:
    """
    Calcula taxa de sucesso por tipo de issue para cada modelo.

    Útil para entender quais tipos de issue cada modelo lida melhor.

    Args:
        results_by_model: Resultados por modelo.

    Returns:
        Dicionário com métricas por tipo de issue por modelo.
    """
    metrics: dict[str, dict[str, Any]] = {}

    for model_name, results in results_by_model.items():
        by_type: dict[str, dict[str, int]] = {}

        for fix_result in results:
            issues = fix_result.scan_result.issues
            if not issues:
                continue

            for issue in issues:
                itype = issue.issue_type.value
                if itype not in by_type:
                    by_type[itype] = {"total": 0, "fixed": 0}
                by_type[itype]["total"] += 1
                if fix_result.final_success:
                    by_type[itype]["fixed"] += 1

        metrics[model_name] = {
            itype: {
                "total": counts["total"],
                "fixed": counts["fixed"],
                "rate": (counts["fixed"] / counts["total"] * 100) if counts["total"] > 0 else 0.0,
            }
            for itype, counts in by_type.items()
        }

    return metrics


def rank_models(
    metrics: dict[str, dict[str, Any]],
    primary_metric: str = "success_rate",
) -> list[tuple[str, float]]:
    """
    Ordena modelos por uma métrica primária.

    Args:
        metrics: Métricas calculadas por compute_experiment_metrics.
        primary_metric: Métrica para ordenação.

    Returns:
        Lista de (nome_modelo, valor) ordenada do melhor ao pior.
    """
    ranked = [
        (model, float(m.get(primary_metric, 0)))
        for model, m in metrics.items()
    ]
    # Para avg_time: menor é melhor → inverter ordenação
    reverse = primary_metric != "avg_time"
    return sorted(ranked, key=lambda x: x[1], reverse=reverse)
