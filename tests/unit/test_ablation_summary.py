"""Testes da estatística inferencial do summarize_ablation.

Garante que o ablation sai com veredito estatístico: Kruskal-Wallis (eixo A,
prompting), McNemar pareado (eixo B, scaffolding) e Cliff's delta.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[2] / "experiments" / "ablation" / "summarize_ablation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("summarize_ablation", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _outcomes(success_files: int, total: int) -> dict[str, int]:
    return {f"/snap/proj/f{i}.tsx": (1 if i < success_files else 0) for i in range(total)}


def test_toolkit_friedman_and_effect_labels():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from analysis.statistical_analyser import friedman_test, interpret_cliffs_delta
    # 3 condições pareadas claramente diferentes → p pequeno
    g1 = [5, 6, 7, 8, 9] * 4
    g2 = [1, 2, 1, 2, 1] * 4
    g3 = [9, 9, 8, 9, 8] * 4
    assert friedman_test(g1, g2, g3) < 0.05
    # tamanhos desiguais → não-pareável → 1.0
    assert friedman_test([1, 2, 3], [1, 2]) == 1.0
    assert interpret_cliffs_delta(0.05) == "negligible"
    assert interpret_cliffs_delta(0.2) == "small"
    assert interpret_cliffs_delta(0.4) == "medium"
    assert interpret_cliffs_delta(0.8) == "large"


def test_inferential_axisA_and_axisB_present():
    sa = _load_module()
    outcomes = {
        "cell_A1_baseline_fewshot_auto": _outcomes(34, 40),   # SR alto
        "cell_A2_zeroshot_auto":         _outcomes(16, 40),   # SR baixo → efeito
        "cell_A3_cot_auto":              _outcomes(33, 40),
        "cell_B1_fewshot_direct":        _outcomes(10, 40),   # scaffolding fraco
        "cell_B3_fewshot_openhands":     _outcomes(33, 40),
    }
    ifrs = {  # IFR por arquivo (contínuo) para a comparação complementar
        "cell_A1_baseline_fewshot_auto": {f"/snap/proj/f{i}.tsx": (0.8 if i < 34 else 0.1) for i in range(40)},
        "cell_A2_zeroshot_auto":         {f"/snap/proj/f{i}.tsx": (0.4 if i < 16 else 0.0) for i in range(40)},
        "cell_A3_cot_auto":              {f"/snap/proj/f{i}.tsx": (0.8 if i < 33 else 0.1) for i in range(40)},
        "cell_B1_fewshot_direct":        {f"/snap/proj/f{i}.tsx": (0.3 if i < 10 else 0.0) for i in range(40)},
        "cell_B3_fewshot_openhands":     {f"/snap/proj/f{i}.tsx": (0.8 if i < 33 else 0.1) for i in range(40)},
    }
    infer = sa.run_inferential(outcomes, ifrs)
    assert infer is not None
    # Eixo A (SR): omnibus Kruskal + Friedman pareado + 3 pares com magnitude
    a = infer["axisA_sr"]
    assert "kruskal_wallis_p" in a and a["friedman_p"] is not None
    assert len(a["pairwise"]) == 3
    a1a2 = next(p for p in a["pairwise"] if {p["a"], p["b"]} == {"A1", "A2"})
    assert a1a2["significant"] is True and a1a2["effect"] in {"medium", "large"}
    # Eixo A (IFR contínuo) presente
    assert infer["axisA_ifr"] and "kruskal_wallis_p" in infer["axisA_ifr"]
    # Eixo B (SR): McNemar pareado vs A1, para B1 e B3 (B2 ausente → ignorado)
    cells = {c["cell"] for c in infer["axisB_sr"]["comparisons"]}
    assert cells == {"B1", "B3"}
    b1 = next(c for c in infer["axisB_sr"]["comparisons"] if c["cell"] == "B1")
    assert b1["n_pairs"] == 40 and b1["significant"] is True
    # Eixo B (IFR): tamanho de efeito presente
    assert infer["axisB_ifr"] and any(c["cell"] == "B1" for c in infer["axisB_ifr"]["comparisons"])


def test_cell_file_outcomes_only_files_with_issues(tmp_path: Path):
    sa = _load_module()
    result = {"results_by_model": {"m": [
        {"file": "/p/a.tsx", "final_success": True,
         "scan_result": {"issues": [{"x": 1}]}},
        {"file": "/p/b.tsx", "final_success": False,
         "scan_result": {"issues": [{"x": 1}]}},
        {"file": "/p/c.tsx", "final_success": True,
         "scan_result": {"issues": []}},  # sem issue → ignorado
    ]}}
    rp = tmp_path / "experiment_result.json"
    rp.write_text(json.dumps(result), encoding="utf-8")
    outcomes = sa._cell_file_outcomes(tmp_path)
    assert outcomes == {"/p/a.tsx": 1, "/p/b.tsx": 0}  # c.tsx excluído
