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


def test_inferential_axisA_and_axisB_present():
    sa = _load_module()
    outcomes = {
        "cell_A1_baseline_fewshot_auto": _outcomes(34, 40),   # SR alto
        "cell_A2_zeroshot_auto":         _outcomes(16, 40),   # SR baixo → efeito
        "cell_A3_cot_auto":              _outcomes(33, 40),
        "cell_B1_fewshot_direct":        _outcomes(10, 40),   # scaffolding fraco
        "cell_B3_fewshot_openhands":     _outcomes(33, 40),
    }
    infer = sa.run_inferential(outcomes)
    assert infer is not None
    # Eixo A: omnibus + 3 pares (A1/A2/A3)
    assert "kruskal_wallis_p" in infer["axisA"]
    assert len(infer["axisA"]["pairwise"]) == 3
    a1a2 = next(p for p in infer["axisA"]["pairwise"] if {p["a"], p["b"]} == {"A1", "A2"})
    assert a1a2["significant"] is True  # SR 0.85 vs 0.40, δ grande
    # Eixo B: McNemar pareado vs A1, para B1 e B3 (B2 ausente → ignorado)
    cells = {c["cell"] for c in infer["axisB"]["comparisons"]}
    assert cells == {"B1", "B3"}
    b1 = next(c for c in infer["axisB"]["comparisons"] if c["cell"] == "B1")
    assert b1["n_pairs"] == 40 and b1["significant"] is True


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
    outcomes = sa._cell_file_outcomes(rp)
    assert outcomes == {"/p/a.tsx": 1, "/p/b.tsx": 0}  # c.tsx excluído
