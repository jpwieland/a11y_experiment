#!/usr/bin/env python3
"""Consolida os resultados das células do ablation study em uma tabela.

Lê experiment_result.json de cada célula em experiment-results/ablation/ e produz:
  • tabela comparativa descritiva (markdown + CSV) com IFR, SR e tempo;
  • estatística inferencial (ablation_inferential.json):
      Eixo A (prompting): Kruskal-Wallis A1/A2/A3 + Mann-Whitney par-a-par
                          (Bonferroni) + Cliff's delta;
      Eixo B (scaffolding): McNemar pareado A1-vs-Bk + Cliff's delta.

Uso:
    python experiments/ablation/summarize_ablation.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

RESULTS_ROOT = Path("experiment-results/ablation")

# Critérios de significância (metodologia §3.7.3): p<alpha E |Cliff's δ|>=efeito mínimo.
_ALPHA = 0.05
_MIN_EFFECT = 0.147

# Ordem e rótulos das células
CELLS = {
    "cell_A1_baseline_fewshot_auto": ("A1", "few-shot", "router auto"),
    "cell_A2_zeroshot_auto": ("A2", "zero-shot", "router auto"),
    "cell_A3_cot_auto": ("A3", "chain-of-thought", "router auto"),
    "cell_B1_fewshot_direct": ("B1", "few-shot", "direct-llm"),
    "cell_B2_fewshot_swe": ("B2", "few-shot", "swe-agent"),
    "cell_B3_fewshot_openhands": ("B3", "few-shot", "openhands"),
}


def _find_result_json(cell_dir: Path) -> Path | None:
    """Localiza o experiment_result.json mais recente da célula."""
    if not cell_dir.exists():
        return None
    candidates = sorted(cell_dir.rglob("experiment_result.json"))
    return candidates[-1] if candidates else None


def _cell_metrics(result_path: Path) -> dict:
    """Extrai métricas agregadas de um experiment_result.json."""
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = {}
    for model, results in data.get("results_by_model", {}).items():
        files_with_issues = [
            r for r in results if r.get("scan_result", {}).get("issues")
        ]
        total_issues = sum(
            len(r["scan_result"]["issues"]) for r in files_with_issues
        )
        fixed = sum(r.get("issues_fixed", 0) for r in files_with_issues)
        succeeded = sum(1 for r in files_with_issues if r.get("final_success"))
        total_time = sum(r.get("total_time", 0.0) for r in files_with_issues)
        n = len(files_with_issues)
        rows[model] = {
            "files_com_issues": n,
            "issues": total_issues,
            "ifr": round(fixed / total_issues, 3) if total_issues else None,
            "sr": round(succeeded / n, 3) if n else None,
            "tempo_medio_s": round(total_time / n, 2) if n else None,
        }
    return rows


def _cell_file_outcomes(result_path: Path) -> dict[str, int]:
    """SR file-level por arquivo COM issue: {caminho: 1 se final_success senão 0}.

    É a unidade dos testes H1/H2 (McNemar/Kruskal). Restringir a arquivos com
    issue evita a diluição dos arquivos sem issue (que "passam" trivialmente).
    """
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    outcomes: dict[str, int] = {}
    for results in data.get("results_by_model", {}).values():
        for r in results:
            if not r.get("scan_result", {}).get("issues"):
                continue
            outcomes[str(r.get("file", ""))] = 1 if r.get("final_success") else 0
    return outcomes


def _ensure_repo_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _load_stats():
    """Importa o toolkit estatístico (raiz do repo no sys.path). None se faltar."""
    _ensure_repo_on_path()
    try:
        from analysis.statistical_analyser import (  # noqa: PLC0415
            _bonferroni_correct, bootstrap_ci, cliffs_delta, friedman_test,
            interpret_cliffs_delta, kruskal_wallis, mann_whitney_u, mcnemar_test,
        )
        return dict(bonf=_bonferroni_correct, ci=bootstrap_ci, delta=cliffs_delta,
                    friedman=friedman_test, interp=interpret_cliffs_delta,
                    kruskal=kruskal_wallis, mwu=mann_whitney_u, mcnemar=mcnemar_test)
    except Exception as exc:  # pragma: no cover
        print(f"[inferencial] toolkit indisponível: {exc}")
        return None


def _cell_fix_results(result_path: Path):
    """Reconstrói os objetos FixResult de uma célula a partir do JSON.

    Permite reusar TODAS as funções canônicas de métricas (IFR, SR-completo,
    SR-efetivo, ρ, MTTR, TE, TPF, decomposições por tipo/WCAG/complexidade)
    em vez de recalcular tudo do dict.
    """
    _ensure_repo_on_path()
    from a11y_autofix.config import FixResult  # noqa: PLC0415
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    by_model: dict[str, list] = {}
    for model, results in data.get("results_by_model", {}).items():
        objs = []
        for r in results:
            try:
                objs.append(FixResult.model_validate(r))
            except Exception:
                continue
        by_model[model] = objs
    return by_model


def _cell_rich_metrics(fix_results: list) -> dict:
    """Conjunto COMPLETO de métricas de uma célula (via funções canônicas) + ICs.

    Inclui: ifr, sr, sr_full, effective_sr, mttr, te, te_normalized, ρ, noop_rate,
    tpf, contagens, tokens; ICs bootstrap 95% para ifr/sr/ρ; e decomposições por
    tipo de issue, princípio WCAG e complexidade.
    """
    _ensure_repo_on_path()
    from a11y_autofix.experiments.metrics import (  # noqa: PLC0415
        compute_experiment_metrics, compute_sr_full, compute_regression_rate,
        compute_per_issue_type_metrics, compute_per_wcag_principle_metrics,
        compute_per_complexity_metrics, compute_tpf,
    )
    m = compute_experiment_metrics({"c": fix_results}).get("c", {})
    rho, n_reg, n_att = compute_regression_rate(fix_results)
    with_issues = [r for r in fix_results if r.scan_result.issues]

    out = {
        "n_files_with_issues": len(with_issues),
        "n_issues": int(m.get("issues_fixed", 0) + m.get("issues_pending", 0)),
        "issues_fixed": m.get("issues_fixed"),
        "ifr": m.get("ifr"),
        # SR sobre arquivos COM issue (≥1 corrigido) — métrica honesta; o
        # effective_sr canônico não entra aqui porque inclui arquivos sem issue
        # (fica 0.96 inflado) e contradiria a unidade arquivos-com-issue.
        "sr": round(sum(1 for r in with_issues if r.final_success) / len(with_issues), 4) if with_issues else None,
        "sr_full": round(compute_sr_full(fix_results), 4),
        "mttr": m.get("mttr"),
        "te": m.get("te"),
        "te_normalized": m.get("te_normalized"),
        "regression_rate_rho": round(rho, 4),
        "rho_n_regressions": n_reg,
        "rho_n_attempts": n_att,
        "noop_rate": m.get("noop_rate"),
        "tpf": compute_tpf(fix_results),
        "total_attempts": m.get("total_attempts"),
        "total_tokens_output": m.get("total_tokens"),
        "avg_time_s": m.get("avg_time"),
    }

    # ICs bootstrap (unidades: issue p/ IFR, arquivo-com-issue p/ SR, tentativa p/ ρ)
    s = _load_stats()
    if s is not None:
        ifr_bin, sr_bin, rho_bin = [], [], []
        for r in with_issues:
            n_iss = len(r.scan_result.issues)
            n_fix = max(0, min(r.issues_fixed, n_iss))
            ifr_bin.extend([1.0] * n_fix + [0.0] * (n_iss - n_fix))
            sr_bin.append(1.0 if r.final_success else 0.0)
        for r in fix_results:
            for a in r.attempts:
                rho_bin.append(1.0 if a.validation_rejected_layer == 2 else 0.0)

        def _ci(vals):
            if not vals:
                return None
            lo, hi = s["ci"](vals, n_bootstrap=2000, ci_level=0.95)
            return [round(lo, 4), round(hi, 4)]
        out["ifr_ci95"] = _ci(ifr_bin)
        out["sr_ci95"] = _ci(sr_bin)
        out["rho_ci95"] = _ci(rho_bin)

    # Decomposições (IFR por tipo de issue / princípio WCAG / complexidade)
    out["by_issue_type"] = compute_per_issue_type_metrics({"c": fix_results}).get("c", {})
    out["by_wcag_principle"] = compute_per_wcag_principle_metrics({"c": fix_results}).get("c", {})
    out["by_complexity"] = compute_per_complexity_metrics({"c": fix_results}).get("c", {})
    return out


def _cell_file_ifr(fix_results: list) -> dict[str, float]:
    """IFR por arquivo (fixed/total) — valor contínuo p/ comparação entre células."""
    out: dict[str, float] = {}
    for r in fix_results:
        n = len(r.scan_result.issues)
        if n:
            out[str(r.file)] = max(0, min(r.issues_fixed, n)) / n
    return out


_AXIS_A = [("A1", "cell_A1_baseline_fewshot_auto", "few-shot"),
           ("A2", "cell_A2_zeroshot_auto", "zero-shot"),
           ("A3", "cell_A3_cot_auto", "chain-of-thought")]
_AXIS_B = [("B1", "cell_B1_fewshot_direct", "direct-llm"),
           ("B2", "cell_B2_fewshot_swe", "swe-agent"),
           ("B3", "cell_B3_fewshot_openhands", "openhands")]
_BASE_KEY = "cell_A1_baseline_fewshot_auto"


def run_inferential(
    outcomes_by_cell: dict[str, dict[str, int]],
    ifr_by_cell: dict[str, dict[str, float]] | None = None,
) -> dict | None:
    """Testes inferenciais sobre as células disponíveis (SR binário e IFR contínuo)."""
    s = _load_stats()
    if s is None:
        return None
    ifr_by_cell = ifr_by_cell or {}
    out: dict = {"alpha": _ALPHA, "min_effect_size": _MIN_EFFECT,
                 "axisA_sr": None, "axisA_ifr": None, "axisB_sr": None, "axisB_ifr": None}

    def _omnibus_pairwise(groups: dict[str, list[float]], paired: bool) -> dict:
        ids = list(groups)
        kp = s["kruskal"](*[groups[i] for i in ids])
        res: dict = {
            "kruskal_wallis_p": round(kp, 6),
            "per_cell_mean": {i: round(sum(groups[i]) / len(groups[i]), 4) for i in ids},
            "n_by_cell": {i: len(groups[i]) for i in ids},
        }
        # Friedman (pareado): alinhar pelos arquivos comuns às 3 células
        if paired and len(ids) >= 3:
            res["friedman_p"] = None  # preenchido pelo chamador (precisa do alinhamento)
        pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        raw_p, deltas = [], []
        for a, b in pairs:
            _, p = s["mwu"](groups[a], groups[b])
            raw_p.append(p)
            deltas.append(s["delta"](groups[a], groups[b]))
        corr_p = s["bonf"](raw_p)
        res["pairwise"] = [
            {"a": a, "b": b, "p_bonferroni": round(pc, 6), "cliffs_delta": round(d, 4),
             "effect": s["interp"](d),
             "significant": bool(pc < _ALPHA and abs(d) >= _MIN_EFFECT)}
            for (a, b), pc, d in zip(pairs, corr_p, deltas)
        ]
        return res

    # ── Eixo A (prompting) — SR binário e IFR contínuo, grupos independentes ──
    sr_groups = {cid: [float(v) for v in outcomes_by_cell[key].values()]
                 for cid, key, _ in _AXIS_A if key in outcomes_by_cell}
    if len(sr_groups) >= 2:
        out["axisA_sr"] = _omnibus_pairwise(sr_groups, paired=True)
        out["axisA_sr"]["unit"] = "SR file-level (binário)"
        # Friedman pareado pelos arquivos comuns às células A presentes
        a_keys = [key for _, key, _ in _AXIS_A if key in outcomes_by_cell]
        if len(a_keys) >= 3:
            common = sorted(set.intersection(*[set(outcomes_by_cell[k]) for k in a_keys]))
            if len(common) >= 3:
                cols = [[float(outcomes_by_cell[k][f]) for f in common] for k in a_keys]
                out["axisA_sr"]["friedman_p"] = round(s["friedman"](*cols), 6)
                out["axisA_sr"]["friedman_note"] = (
                    "Friedman = teste pareado (mesmos arquivos), complementa o "
                    "Kruskal-Wallis pré-registrado (que assume independência).")

    ifr_groups = {cid: list(ifr_by_cell[key].values())
                  for cid, key, _ in _AXIS_A if key in ifr_by_cell}
    if len(ifr_groups) >= 2:
        out["axisA_ifr"] = _omnibus_pairwise(ifr_groups, paired=False)
        out["axisA_ifr"]["unit"] = "IFR por arquivo (contínuo)"

    # ── Eixo B (scaffolding) — McNemar (SR pareado) + Cliff's δ no IFR ────────
    if _BASE_KEY in outcomes_by_cell:
        base = outcomes_by_cell[_BASE_KEY]
        comps = []
        for cid, key, agent in _AXIS_B:
            if key not in outcomes_by_cell:
                continue
            bk = outcomes_by_cell[key]
            common = sorted(set(base) & set(bk))
            if not common:
                continue
            a_list = [float(base[f]) for f in common]
            b_list = [float(bk[f]) for f in common]
            p = s["mcnemar"]([int(v) for v in a_list], [int(v) for v in b_list])
            d = s["delta"](b_list, a_list)  # Bk vs baseline
            comps.append({
                "cell": cid, "agent": agent, "n_pairs": len(common),
                "sr_baseline_A1": round(sum(a_list) / len(a_list), 4),
                "sr_cell": round(sum(b_list) / len(b_list), 4),
                "mcnemar_p": round(p, 6), "cliffs_delta": round(d, 4),
                "effect": s["interp"](d),
                "significant": bool(p < _ALPHA and abs(d) >= _MIN_EFFECT),
            })
        if comps:
            out["axisB_sr"] = {"baseline": "A1 (few-shot, router auto)",
                               "unit": "SR file-level (binário, pareado)", "comparisons": comps}

    if _BASE_KEY in ifr_by_cell:
        base_ifr = ifr_by_cell[_BASE_KEY]
        comps = []
        for cid, key, agent in _AXIS_B:
            if key not in ifr_by_cell:
                continue
            bk = ifr_by_cell[key]
            common = sorted(set(base_ifr) & set(bk))
            if not common:
                continue
            a_list = [base_ifr[f] for f in common]
            b_list = [bk[f] for f in common]
            d = s["delta"](b_list, a_list)
            comps.append({
                "cell": cid, "agent": agent, "n_pairs": len(common),
                "ifr_baseline_A1": round(sum(a_list) / len(a_list), 4),
                "ifr_cell": round(sum(b_list) / len(b_list), 4),
                "cliffs_delta": round(d, 4), "effect": s["interp"](d),
            })
        if comps:
            out["axisB_ifr"] = {
                "baseline": "A1", "unit": "IFR por arquivo (contínuo)",
                "comparisons": comps,
                "note": "Tamanho de efeito (Cliff's δ); teste pareado de IFR "
                        "(Wilcoxon) fica como trabalho futuro.",
            }

    return out


def _print_inferential(infer: dict) -> None:
    print("\n## Estatística inferencial\n")
    a = infer.get("axisA_sr")
    if a:
        sr = ", ".join(f"{k}={v}" for k, v in a["per_cell_mean"].items())
        print(f"**Eixo A — Prompting (SR)** · por célula: {sr}")
        line = f"  Kruskal-Wallis p={a['kruskal_wallis_p']}"
        if a.get("friedman_p") is not None:
            line += f" · Friedman (pareado) p={a['friedman_p']}"
        print(line)
        for p in a["pairwise"]:
            sig = "✓ signif." if p["significant"] else "não"
            print(f"  {p['a']} vs {p['b']}: p={p['p_bonferroni']} δ={p['cliffs_delta']} "
                  f"({p['effect']}) → {sig}")
    ai = infer.get("axisA_ifr")
    if ai:
        ifr = ", ".join(f"{k}={v}" for k, v in ai["per_cell_mean"].items())
        print(f"**Eixo A — Prompting (IFR/arquivo)** · {ifr} · Kruskal-Wallis p={ai['kruskal_wallis_p']}")
    b = infer.get("axisB_sr")
    if b:
        print(f"\n**Eixo B — Scaffolding (SR)** · baseline {b['baseline']} (McNemar pareado)")
        for c in b["comparisons"]:
            sig = "✓ signif." if c["significant"] else "não"
            print(f"  {c['cell']} {c['agent']} vs A1: SR {c['sr_baseline_A1']}→{c['sr_cell']} · "
                  f"McNemar p={c['mcnemar_p']} δ={c['cliffs_delta']} ({c['effect']}, "
                  f"n={c['n_pairs']}) → {sig}")
    bi = infer.get("axisB_ifr")
    if bi:
        print(f"**Eixo B — Scaffolding (IFR/arquivo, tamanho de efeito)** · baseline A1")
        for c in bi["comparisons"]:
            print(f"  {c['cell']} {c['agent']}: IFR {c['ifr_baseline_A1']}→{c['ifr_cell']} "
                  f"δ={c['cliffs_delta']} ({c['effect']})")
    print(f"\n_Significância exige p<{infer['alpha']} E |Cliff's δ|>={infer['min_effect_size']}._")


def main() -> None:
    table: list[dict] = []
    for cell_name, (cell_id, strategy, agent) in CELLS.items():
        result_path = _find_result_json(RESULTS_ROOT / cell_name)
        if result_path is None:
            table.append({
                "célula": cell_id, "estratégia": strategy, "agente": agent,
                "status": "PENDENTE", "ifr": "", "sr": "", "tempo_medio_s": "",
            })
            continue
        for model, m in _cell_metrics(result_path).items():
            table.append({
                "célula": cell_id, "estratégia": strategy, "agente": agent,
                "status": "ok",
                "ifr": m["ifr"], "sr": m["sr"], "tempo_medio_s": m["tempo_medio_s"],
            })

    # Markdown
    headers = ["célula", "estratégia", "agente", "status", "ifr", "sr", "tempo_medio_s"]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join("---" for _ in headers) + "|")
    for row in table:
        print("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")

    # CSV
    out_csv = RESULTS_ROOT / "ablation_summary.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(table)
    print(f"\nCSV: {out_csv}")

    # ── Análise completa: métricas ricas por célula + inferência cruzada ─────
    outcomes_by_cell: dict[str, dict[str, int]] = {}
    ifr_by_cell: dict[str, dict[str, float]] = {}
    rich_by_cell: dict[str, dict] = {}
    for cell_name, (cell_id, strategy, agent) in CELLS.items():
        rp = _find_result_json(RESULTS_ROOT / cell_name)
        if rp is None:
            continue
        outcomes_by_cell[cell_name] = _cell_file_outcomes(rp)
        try:
            by_model = _cell_fix_results(rp)
            fr = next(iter(by_model.values()), [])
            if fr:
                ifr_by_cell[cell_name] = _cell_file_ifr(fr)
                rich_by_cell[cell_name] = {
                    "id": cell_id, "estrategia": strategy, "agente": agent,
                    **_cell_rich_metrics(fr),
                }
        except Exception as exc:  # pragma: no cover
            print(f"[análise] célula {cell_id} sem métricas ricas: {str(exc)[:120]}")

    if rich_by_cell:
        print("\n## Métricas completas por célula (ver ablation_analysis.json)\n")
        cols = ["ifr", "sr", "sr_full", "regression_rate_rho",
                "noop_rate", "mttr", "te", "tpf", "n_issues"]
        print("| célula | " + " | ".join(cols) + " |")
        print("|" + "|".join("---" for _ in range(len(cols) + 1)) + "|")
        for cell_name, m in rich_by_cell.items():
            print(f"| {m['id']} | " + " | ".join(str(m.get(c, "")) for c in cols) + " |")

    analysis: dict = {"per_cell": rich_by_cell, "inferential": None}
    if len(outcomes_by_cell) >= 2:
        infer = run_inferential(outcomes_by_cell, ifr_by_cell)
        if infer:
            analysis["inferential"] = infer
            _print_inferential(infer)
    else:
        print("\n[inferencial] poucas células concluídas — rode mais células "
              "(mín. 2) para a análise estatística cruzada.")

    out_json = RESULTS_ROOT / "ablation_analysis.json"
    out_json.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nAnálise completa: {out_json}")


if __name__ == "__main__":
    main()
