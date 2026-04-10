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

  ρ   (Regression Rate, H5):
      ρ = |patches rejected at Layer 2| / |patches attempted|
      Layer 2 rejections = functional regression introduced by the patch.

  δ   (Detection Rate):
      δ = |issues detected by scanner set| / |issues in ground truth|
      Requires a ground truth reference; computed separately.

  TPF (Tokens Per Fix):
      TPF = total_input_tokens / issues_fixed
      More precise alternative to TE when prompt tokens are available.
"""

from __future__ import annotations

import statistics
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


def compute_regression_rate(
    results: list[FixResult],
) -> tuple[float, int, int]:
    """
    Taxa de regressão ρ (H5) — metodologia Seção 3.7.1.

    ρ = patches rejeitados na Camada 2 / total de patches tentados

    A rejeição na Camada 2 indica regressão funcional introduzida pelo patch
    (interface quebrada, export removido, event handler perdido).

    Nota: requer que `FixAttempt.error` contenha o prefixo "functional_regression:"
    para contar como rejeição L2 — gerado por ValidationPipeline.

    Returns:
        Tuple (ρ, n_regressions, n_attempts_total)
    """
    n_attempts = 0
    n_regressions = 0
    for result in results:
        for attempt in result.attempts:
            n_attempts += 1
            if attempt.error and attempt.error.startswith("functional_regression:"):
                n_regressions += 1
    rho = n_regressions / n_attempts if n_attempts > 0 else 0.0
    return rho, n_regressions, n_attempts


def compute_validation_layer_breakdown(
    results: list[FixResult],
) -> dict[str, int]:
    """
    Distribui as falhas de validação por camada.

    Conta quantas tentativas foram rejeitadas em cada camada (1–4)
    e quantas passaram em todas as camadas.

    Returns:
        Dict com chaves "layer_1", "layer_2", "layer_3", "layer_4", "passed".
    """
    counts = {"layer_1": 0, "layer_2": 0, "layer_3": 0, "layer_4": 0, "passed": 0}
    layer_prefixes = {
        "layer_1": ("empty_patch", "unclosed_code_block", "llm_refusal", "no_jsx_found"),
        "layer_2": ("functional_regression:",),
        "layer_3": ("domain_check_failed:",),
        "layer_4": ("invalid_tabIndex:", "dangerouslySetInnerHTML_present"),
    }
    for result in results:
        for attempt in result.attempts:
            if attempt.success:
                counts["passed"] += 1
                continue
            err = attempt.error or ""
            matched = False
            for layer, prefixes in layer_prefixes.items():
                if any(err.startswith(p) for p in prefixes):
                    counts[layer] += 1
                    matched = True
                    break
            if not matched and err:
                # Timeout, LLM error, etc. — não são rejeições de validação
                pass
    return counts


def compute_per_complexity_metrics(
    results_by_model: dict[str, list[FixResult]],
) -> dict[str, dict[str, Any]]:
    """
    Taxa de sucesso por nível de complexidade do issue (simple/moderate/complex).

    Permite avaliar se modelos maiores resolvem melhor issues complexas.

    Returns:
        {model: {complexity: {total, fixed, rate}}}
    """
    metrics: dict[str, dict[str, Any]] = {}
    for model_name, results in results_by_model.items():
        by_complexity: dict[str, dict[str, int]] = {}
        for fix_result in results:
            for issue in fix_result.scan_result.issues:
                c = issue.complexity.value
                if c not in by_complexity:
                    by_complexity[c] = {"total": 0, "fixed": 0}
                by_complexity[c]["total"] += 1
                if fix_result.final_success:
                    by_complexity[c]["fixed"] += 1
        metrics[model_name] = {
            c: {
                "total": v["total"],
                "fixed": v["fixed"],
                "rate": v["fixed"] / v["total"] if v["total"] > 0 else 0.0,
            }
            for c, v in by_complexity.items()
        }
    return metrics


def compute_per_wcag_principle_metrics(
    results_by_model: dict[str, list[FixResult]],
) -> dict[str, dict[str, Any]]:
    """
    Taxa de sucesso por princípio WCAG (P1 Perceivable, P2 Operable,
    P3 Understandable, P4 Robust).

    Princípio derivado do primeiro dígito do critério WCAG (1.x, 2.x, 3.x, 4.x).

    Returns:
        {model: {principle: {total, fixed, rate}}}
    """
    _PRINCIPLES = {"1": "P1_Perceivable", "2": "P2_Operable",
                   "3": "P3_Understandable", "4": "P4_Robust"}
    metrics: dict[str, dict[str, Any]] = {}
    for model_name, results in results_by_model.items():
        by_principle: dict[str, dict[str, int]] = {}
        for fix_result in results:
            for issue in fix_result.scan_result.issues:
                wcag = issue.wcag_criteria or ""
                principle = _PRINCIPLES.get(wcag[0], "unknown") if wcag else "unknown"
                if principle not in by_principle:
                    by_principle[principle] = {"total": 0, "fixed": 0}
                by_principle[principle]["total"] += 1
                if fix_result.final_success:
                    by_principle[principle]["fixed"] += 1
        metrics[model_name] = {
            p: {
                "total": v["total"],
                "fixed": v["fixed"],
                "rate": v["fixed"] / v["total"] if v["total"] > 0 else 0.0,
            }
            for p, v in by_principle.items()
        }
    return metrics


def compute_fix_by_agent(
    results_by_model: dict[str, list[FixResult]],
) -> dict[str, dict[str, Any]]:
    """
    Distribui as correções bem-sucedidas por agente (direct-llm, openhands, swe-agent).

    Returns:
        {model: {agent: {attempts, successes, sr}}}
    """
    metrics: dict[str, dict[str, Any]] = {}
    for model_name, results in results_by_model.items():
        by_agent: dict[str, dict[str, int]] = {}
        for fix_result in results:
            for attempt in fix_result.attempts:
                agent = attempt.agent
                if agent not in by_agent:
                    by_agent[agent] = {"attempts": 0, "successes": 0}
                by_agent[agent]["attempts"] += 1
                if attempt.success:
                    by_agent[agent]["successes"] += 1
        metrics[model_name] = {
            agent: {
                "attempts": v["attempts"],
                "successes": v["successes"],
                "sr": v["successes"] / v["attempts"] if v["attempts"] > 0 else 0.0,
            }
            for agent, v in by_agent.items()
        }
    return metrics


def compute_attempt_distribution(
    results: list[FixResult],
) -> dict[int, int]:
    """
    Distribuição do número de tentativas por arquivo.

    Útil para entender se o modelo converge na 1ª tentativa ou precisa de retries.

    Returns:
        {n_attempts: count_of_files}
    """
    dist: dict[int, int] = {}
    for r in results:
        n = len(r.attempts)
        dist[n] = dist.get(n, 0) + 1
    return dict(sorted(dist.items()))


def compute_tpf(
    results: list[FixResult],
) -> float | None:
    """
    Tokens Per Fix (TPF) — custo médio de tokens de input por issue corrigida.

    TPF = total_token_input / issues_fixed

    Mais preciso que TE quando tokens de input estão disponíveis.
    Retorna None se não houver issues corrigidas ou tokens disponíveis.
    """
    total_input = sum(
        a.tokens_prompt or 0
        for r in results
        for a in r.attempts
    )
    total_fixed = sum(r.issues_fixed for r in results)
    if total_fixed == 0 or total_input == 0:
        return None
    return round(total_input / total_fixed, 1)


def compute_diff_stats(
    results: list[FixResult],
) -> dict[str, float | None]:
    """
    Estatísticas do tamanho dos patches gerados (linhas do diff).

    Indica o "conservadorismo" do modelo: patches menores tendem a ser
    mais cirúrgicos e menos propensos a introduzir regressões.

    Returns:
        {mean, median, p25, p75, max}
    """
    sizes = []
    for r in results:
        if r.best_attempt and r.best_attempt.diff:
            sizes.append(len(r.best_attempt.diff.splitlines()))

    if not sizes:
        return {"mean": None, "median": None, "p25": None, "p75": None, "max": None}

    sorted_sizes = sorted(sizes)
    n = len(sorted_sizes)
    return {
        "mean": round(sum(sizes) / n, 1),
        "median": float(statistics.median(sizes)),
        "p25": float(sorted_sizes[n // 4]),
        "p75": float(sorted_sizes[min(3 * n // 4, n - 1)]),
        "max": float(max(sizes)),
    }


def compute_confidence_breakdown(
    results_by_model: dict[str, list[FixResult]],
) -> dict[str, dict[str, Any]]:
    """
    Taxa de correção por nível de confiança do issue (high/medium/low).

    Permite verificar se o modelo tem melhor desempenho em issues com
    alta confiança (multi-tool agreement) vs issues detectadas por 1 tool.

    Returns:
        {model: {confidence_level: {total, fixed, rate}}}
    """
    metrics: dict[str, dict[str, Any]] = {}
    for model_name, results in results_by_model.items():
        by_conf: dict[str, dict[str, int]] = {}
        for fix_result in results:
            for issue in fix_result.scan_result.issues:
                conf = issue.confidence.value
                if conf not in by_conf:
                    by_conf[conf] = {"total": 0, "fixed": 0}
                by_conf[conf]["total"] += 1
                if fix_result.final_success:
                    by_conf[conf]["fixed"] += 1
        metrics[model_name] = {
            conf: {
                "total": v["total"],
                "fixed": v["fixed"],
                "rate": v["fixed"] / v["total"] if v["total"] > 0 else 0.0,
            }
            for conf, v in by_conf.items()
        }
    return metrics


def compute_full_experiment_metrics(
    results_by_model: dict[str, list[FixResult]],
    input_tokens_by_model: dict[str, int] | None = None,
) -> dict[str, Any]:
    """
    Computa TODAS as métricas disponíveis para um experimento.

    Inclui primárias (SR, IFR, MTTR, TE), secundárias (ρ, TPF),
    e todas as decomposições cross-dimensionais.

    Returns:
        Dict com chaves:
          "per_model"          — métricas primárias por modelo
          "regression_rate"    — ρ por modelo
          "validation_layers"  — breakdown de falhas por camada
          "per_issue_type"     — IFR por tipo de issue
          "per_complexity"     — IFR por complexidade
          "per_wcag_principle" — IFR por princípio WCAG
          "per_confidence"     — IFR por nível de confiança
          "fix_by_agent"       — SR por agente
          "attempt_distribution" — distribuição de nº de tentativas
          "diff_stats"         — estatísticas de tamanho de patch
          "tpf"                — tokens per fix por modelo
    """
    per_model = compute_experiment_metrics(results_by_model, input_tokens_by_model)

    regression_rate: dict[str, Any] = {}
    validation_layers: dict[str, Any] = {}
    attempt_dist: dict[str, Any] = {}
    diff_stats: dict[str, Any] = {}
    tpf: dict[str, Any] = {}

    for model_name, results in results_by_model.items():
        rho, n_reg, n_att = compute_regression_rate(results)
        regression_rate[model_name] = {
            "rho": round(rho, 4),
            "regressions": n_reg,
            "attempts": n_att,
        }
        validation_layers[model_name] = compute_validation_layer_breakdown(results)
        attempt_dist[model_name] = compute_attempt_distribution(results)
        diff_stats[model_name] = compute_diff_stats(results)
        tpf[model_name] = compute_tpf(results)

    return {
        "per_model": per_model,
        "regression_rate": regression_rate,
        "validation_layers": validation_layers,
        "per_issue_type": compute_per_issue_type_metrics(results_by_model),
        "per_complexity": compute_per_complexity_metrics(results_by_model),
        "per_wcag_principle": compute_per_wcag_principle_metrics(results_by_model),
        "per_confidence": compute_confidence_breakdown(results_by_model),
        "fix_by_agent": compute_fix_by_agent(results_by_model),
        "attempt_distribution": attempt_dist,
        "diff_stats": diff_stats,
        "tpf": tpf,
    }
