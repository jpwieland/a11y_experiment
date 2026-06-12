#!/usr/bin/env python3
"""Consolida os resultados das células do ablation study em uma tabela.

Lê experiment_result.json de cada célula em experiment-results/ablation/
e produz uma tabela comparativa (markdown + CSV) com IFR, SR e tempo.

Uso:
    python experiments/ablation/summarize_ablation.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

RESULTS_ROOT = Path("experiment-results/ablation")

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


if __name__ == "__main__":
    main()
