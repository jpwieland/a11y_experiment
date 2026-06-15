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


def _load_stats():
    """Importa o toolkit estatístico (raiz do repo no sys.path). None se faltar."""
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from analysis.statistical_analyser import (  # noqa: PLC0415
            _bonferroni_correct, cliffs_delta, kruskal_wallis,
            mann_whitney_u, mcnemar_test,
        )
        return dict(bonf=_bonferroni_correct, delta=cliffs_delta,
                    kruskal=kruskal_wallis, mwu=mann_whitney_u, mcnemar=mcnemar_test)
    except Exception as exc:  # pragma: no cover
        print(f"[inferencial] toolkit indisponível: {exc}")
        return None


def run_inferential(outcomes_by_cell: dict[str, dict[str, int]]) -> dict | None:
    """Roda os testes pré-registrados sobre as células disponíveis."""
    s = _load_stats()
    if s is None:
        return None
    out: dict = {"unit": "SR file-level (arquivos com issue)",
                 "alpha": _ALPHA, "min_effect_size": _MIN_EFFECT, "axisA": None, "axisB": None}

    # ── Eixo A — prompting (A1/A2/A3), grupos independentes ──────────────────
    axisA_cells = [("A1", "cell_A1_baseline_fewshot_auto", "few-shot"),
                   ("A2", "cell_A2_zeroshot_auto", "zero-shot"),
                   ("A3", "cell_A3_cot_auto", "chain-of-thought")]
    groups = {cid: list(outcomes_by_cell[key].values())
              for cid, key, _ in axisA_cells if key in outcomes_by_cell}
    if len(groups) >= 2:
        ids = list(groups)
        kp = s["kruskal"](*[[float(v) for v in groups[i]] for i in ids])
        pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        raw_p, deltas = [], []
        for a, b in pairs:
            _, p = s["mwu"]([float(v) for v in groups[a]], [float(v) for v in groups[b]])
            raw_p.append(p)
            deltas.append(s["delta"]([float(v) for v in groups[a]], [float(v) for v in groups[b]]))
        corr_p = s["bonf"](raw_p)
        out["axisA"] = {
            "kruskal_wallis_p": round(kp, 6),
            "per_cell_sr": {i: round(sum(groups[i]) / len(groups[i]), 4) for i in ids},
            "n_by_cell": {i: len(groups[i]) for i in ids},
            "pairwise": [
                {"a": a, "b": b, "p_bonferroni": round(pc, 6), "cliffs_delta": round(d, 4),
                 "significant": bool(pc < _ALPHA and abs(d) >= _MIN_EFFECT)}
                for (a, b), pc, d in zip(pairs, corr_p, deltas)
            ],
            "note": ("condições pareadas (mesmos arquivos); Kruskal-Wallis assume "
                     "independência — Friedman seria mais apropriado (limitação)."),
        }

    # ── Eixo B — scaffolding: McNemar pareado A1 (baseline) vs cada Bk ───────
    base_key = "cell_A1_baseline_fewshot_auto"
    if base_key in outcomes_by_cell:
        base = outcomes_by_cell[base_key]
        bcells = [("B1", "cell_B1_fewshot_direct", "direct-llm"),
                  ("B2", "cell_B2_fewshot_swe", "swe-agent"),
                  ("B3", "cell_B3_fewshot_openhands", "openhands")]
        comparisons = []
        for cid, key, agent in bcells:
            if key not in outcomes_by_cell:
                continue
            bk = outcomes_by_cell[key]
            common = sorted(set(base) & set(bk))  # pareado pelos mesmos arquivos
            if not common:
                continue
            a_list = [base[f] for f in common]
            b_list = [bk[f] for f in common]
            p = s["mcnemar"](a_list, b_list)
            d = s["delta"]([float(v) for v in a_list], [float(v) for v in b_list])
            comparisons.append({
                "cell": cid, "agent": agent, "n_pairs": len(common),
                "sr_baseline_A1": round(sum(a_list) / len(a_list), 4),
                "sr_cell": round(sum(b_list) / len(b_list), 4),
                "mcnemar_p": round(p, 6), "cliffs_delta": round(d, 4),
                "significant": bool(p < _ALPHA and abs(d) >= _MIN_EFFECT),
            })
        if comparisons:
            out["axisB"] = {"baseline": "A1 (few-shot, router auto)", "comparisons": comparisons}

    return out


def _print_inferential(infer: dict) -> None:
    print("\n## Estatística inferencial (unidade: SR file-level em arquivos com issue)\n")
    a = infer.get("axisA")
    if a:
        sr = ", ".join(f"{k}={v}" for k, v in a["per_cell_sr"].items())
        print(f"**Eixo A — Prompting** · SR por célula: {sr}")
        print(f"  Kruskal-Wallis (A1/A2/A3): p = {a['kruskal_wallis_p']}")
        for p in a["pairwise"]:
            sig = "✓ significativo" if p["significant"] else "não"
            print(f"  {p['a']} vs {p['b']}: p={p['p_bonferroni']} δ={p['cliffs_delta']} → {sig}")
        print(f"  [nota] {a['note']}")
    b = infer.get("axisB")
    if b:
        print(f"\n**Eixo B — Scaffolding** · baseline {b['baseline']} (McNemar pareado)")
        for c in b["comparisons"]:
            sig = "✓ significativo" if c["significant"] else "não"
            print(f"  {c['cell']} {c['agent']} vs A1: "
                  f"SR {c['sr_baseline_A1']}→{c['sr_cell']} · McNemar p={c['mcnemar_p']} "
                  f"δ={c['cliffs_delta']} (n_pares={c['n_pairs']}) → {sig}")
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

    # ── Estatística inferencial (Kruskal-Wallis + McNemar + Cliff's δ) ───────
    outcomes_by_cell: dict[str, dict[str, int]] = {}
    for cell_name in CELLS:
        rp = _find_result_json(RESULTS_ROOT / cell_name)
        if rp is not None:
            outcomes_by_cell[cell_name] = _cell_file_outcomes(rp)

    if len(outcomes_by_cell) >= 2:
        infer = run_inferential(outcomes_by_cell)
        if infer:
            _print_inferential(infer)
            out_json = RESULTS_ROOT / "ablation_inferential.json"
            out_json.write_text(json.dumps(infer, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            print(f"\nJSON inferencial: {out_json}")
    else:
        print("\n[inferencial] poucas células concluídas — rode mais células "
              "para a análise estatística (mín. 2).")


if __name__ == "__main__":
    main()
