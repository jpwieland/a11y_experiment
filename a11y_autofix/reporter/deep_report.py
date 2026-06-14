"""Relatório profundo de fim de experimento (deep report).

Gerado automaticamente ao final de `ExperimentRunner.run_from_config`,
consolida TODAS as métricas disponíveis para análise científica:

  1. Metadados e configuração (reproducibilidade)
  2. Métricas primárias por modelo com variância entre repetições
     (SR, IFR estrito, MTTR, tempo, tokens/TPF)
  3. Validação em 4 camadas: aceitos/rejeitados por camada + ρ (H5)
  4. Decomposições: tipo de issue, princípio WCAG, complexidade,
     confiança, agente, distribuição de tentativas, tamanho de diff
  5. Taxonomia de modos de falha (erros agrupados)
  6. Drill-down por arquivo (última repetição)
  7. Tabela por repetição (estabilidade)

Saídas: deep_report.json (dados completos), deep_report.md (legível),
deep_report.html (standalone para navegador).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from a11y_autofix.config import FixResult
from a11y_autofix.experiments.metrics import (
    compute_experiment_metrics,
    compute_full_experiment_metrics,
    compute_regression_rate,
    compute_sr_full,
)

log = structlog.get_logger(__name__)


def _mean_sd(values: list[float]) -> dict[str, float | None]:
    """Média ± desvio-padrão (sd=None com <2 amostras)."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"mean": None, "sd": None, "n": 0}
    return {
        "mean": round(statistics.mean(clean), 4),
        "sd": round(statistics.stdev(clean), 4) if len(clean) >= 2 else None,
        "n": len(clean),
    }


def _inferential_statistics(
    last_rep: dict[str, list[FixResult]],
    models: list[str],
    alpha: float = 0.05,
    min_effect_size: float = 0.147,
    n_bootstrap: int = 2000,
) -> dict[str, Any]:
    """Estatística inferencial sobre a última repetição.

    Conecta analysis/statistical_analyser.py (antes dead code) ao relatório:
      • IC 95% bootstrap (n=2000) para IFR, SR-efetivo e ρ por modelo;
      • comparação entre modelos no IFR (issue-level): Kruskal-Wallis +
        Mann-Whitney U par-a-par (Bonferroni) + Cliff's delta (tamanho de efeito).

    Unidades de análise:
      IFR  → 1 por issue (1=corrigida no re-scan, 0=não), agrupadas nos arquivos
             COM issue (evita a diluição dos arquivos sem issue).
      SR-ef→ 1 por arquivo-com-issue (final_success).
      ρ    → 1 por tentativa de patch (rejeitada na Camada 2 = regressão).

    Nota de validade: com poucos issues o IC é largo; modelos compartilham o
    mesmo conjunto de issues (pareados), mas Mann-Whitney é não-pareado
    (conservador). Análise sobre 1 repetição evita pseudo-replicação; a
    variância entre repetições está em stability_across_repetitions.

    Degrada com segurança para {} se o toolkit não puder ser importado.
    """
    try:
        import sys
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from analysis.statistical_analyser import (
            _bonferroni_correct,
            bootstrap_ci,
            cliffs_delta,
            kruskal_wallis,
            mann_whitney_u,
        )
    except Exception as exc:  # pragma: no cover - import defensivo
        log.warning("inferential_stats_unavailable", error=str(exc)[:200])
        return {}

    def _ci(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"point": None, "ci95": [None, None], "n": 0}
        lo, hi = bootstrap_ci(values, n_bootstrap=n_bootstrap, ci_level=0.95)
        return {
            "point": round(sum(values) / len(values), 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "n": len(values),
        }

    issue_outcomes: dict[str, list[float]] = {}
    per_model: dict[str, Any] = {}
    for model in models:
        results = last_rep.get(model, [])
        with_issues = [r for r in results if r.scan_result.issues]

        ifr_bin: list[float] = []
        for r in with_issues:
            n_iss = len(r.scan_result.issues)
            n_fix = max(0, min(r.issues_fixed, n_iss))
            ifr_bin.extend([1.0] * n_fix + [0.0] * (n_iss - n_fix))
        issue_outcomes[model] = ifr_bin

        sr_bin = [1.0 if r.final_success else 0.0 for r in with_issues]
        rho_bin = [
            1.0 if a.validation_rejected_layer == 2 else 0.0
            for r in results for a in r.attempts
        ]
        per_model[model] = {
            "ifr": _ci(ifr_bin),
            "sr_effective": _ci(sr_bin),
            "rho": _ci(rho_bin),
        }

    # Comparação entre modelos no IFR (issue-level)
    comparison: dict[str, Any] = {}
    usable = [m for m in models if issue_outcomes.get(m)]
    if len(usable) >= 2:
        groups = [issue_outcomes[m] for m in usable]
        kp = kruskal_wallis(*groups)
        pairs = [(usable[i], usable[j])
                 for i in range(len(usable)) for j in range(i + 1, len(usable))]
        raw_p, deltas, us = [], [], []
        for a, b in pairs:
            u, p = mann_whitney_u(issue_outcomes[a], issue_outcomes[b])
            raw_p.append(p)
            us.append(u)
            deltas.append(cliffs_delta(issue_outcomes[a], issue_outcomes[b]))
        corr_p = _bonferroni_correct(raw_p)
        comparison = {
            "metric": "ifr_issue_level",
            "kruskal_wallis_p": round(kp, 6),
            "pairwise": [
                {
                    "a": a, "b": b,
                    "p_value_bonferroni": round(pc, 6),
                    "cliffs_delta": round(d, 4),
                    "significant": bool(pc < alpha and abs(d) >= min_effect_size),
                }
                for (a, b), pc, d in zip(pairs, corr_p, deltas)
            ],
        }

    return {
        "method": (
            "Bootstrap CI (n=2000, 95%) por modelo; comparação entre modelos por "
            "Kruskal-Wallis + Mann-Whitney U (Bonferroni) + Cliff's delta."
        ),
        "alpha": alpha,
        "min_effect_size": min_effect_size,
        "n_bootstrap": n_bootstrap,
        "analysis_unit": "última repetição (evita pseudo-replicação entre reps)",
        "per_model": per_model,
        "model_comparison": comparison,
        "caveat": (
            "Significância exige p<alpha E |Cliff's delta|>=min_effect_size. "
            "Com poucos issues os ICs são largos; ausência de significância NÃO "
            "prova equivalência."
        ),
    }


def _failure_taxonomy(results: list[FixResult]) -> dict[str, int]:
    """Agrupa erros de tentativas falhas em categorias estáveis."""
    taxonomy: Counter[str] = Counter()
    for r in results:
        for a in r.attempts:
            if a.success:
                continue
            err = (a.error or "").strip()
            if not err:
                taxonomy["sem_erro_registrado"] += 1
            elif a.validation_rejected_layer is not None:
                taxonomy[f"validacao_camada_{a.validation_rejected_layer}"] += 1
            elif err.startswith("functional_regression:"):
                taxonomy["validacao_camada_2"] += 1
            elif "timeout" in err.lower():
                taxonomy["timeout"] += 1
            elif "no valid code" in err.lower() or "code block" in err.lower():
                taxonomy["llm_sem_codigo_valido"] += 1
            elif "refus" in err.lower():
                taxonomy["llm_recusa"] += 1
            else:
                # Primeiro token do erro como categoria genérica
                taxonomy[f"outro:{err.split(':')[0][:40]}"] += 1
    return dict(taxonomy.most_common())


class DeepReportGenerator:
    """Consolida resultados multi-repetição em relatório de análise profunda."""

    def generate(
        self,
        all_rep_results: list[dict[str, list[FixResult]]],
        output_dir: Path,
        experiment_name: str = "",
        experiment_id: str = "",
        config_snapshot: dict[str, Any] | None = None,
        tool_versions: dict[str, str] | None = None,
        files_count: int = 0,
    ) -> Path:
        """
        Gera deep_report.{json,md,html} em output_dir.

        Args:
            all_rep_results: Um dict {model: [FixResult]} por repetição.
            output_dir: Diretório de saída do experimento.
            experiment_name/id: Identificação.
            config_snapshot: Snapshot da configuração para reproducibilidade.
            tool_versions: Versões das ferramentas de scan.
            files_count: Total de arquivos do experimento.

        Returns:
            Caminho do deep_report.json gerado.
        """
        n_reps = len(all_rep_results)
        last_rep = all_rep_results[-1] if all_rep_results else {}
        models = sorted(last_rep.keys())

        # ── Núcleo: todas as métricas da última repetição ──────────────────
        full_metrics = compute_full_experiment_metrics(last_rep)

        # ── Variância entre repetições ─────────────────────────────────────
        per_rep_rows: list[dict[str, Any]] = []
        stability: dict[str, Any] = {}
        for model in models:
            srs, sr_fulls, ifrs, rhos, times = [], [], [], [], []
            for rep_idx, rep in enumerate(all_rep_results, start=1):
                results = rep.get(model, [])
                if not results:
                    continue
                m = compute_experiment_metrics({model: results}).get(model, {})
                rho, n_reg, n_att = compute_regression_rate(results)
                sr_full = compute_sr_full(results)
                srs.append(m.get("success_rate"))
                sr_fulls.append(sr_full)
                ifrs.append(m.get("ifr"))
                rhos.append(rho)
                # A chave produzida por compute_experiment_metrics é "avg_time"
                # (não "avg_time_seconds"); a chave errada zerava a variância de
                # tempo na seção de estabilidade (n=0).
                times.append(m.get("avg_time"))
                per_rep_rows.append({
                    "rep": rep_idx,
                    "model": model,
                    "sr": m.get("success_rate"),
                    "sr_full": round(sr_full, 4),
                    "ifr": m.get("ifr"),
                    "rho": round(rho, 4),
                    "regressions": n_reg,
                    "attempts": n_att,
                    "avg_time_s": m.get("avg_time"),
                })
            stability[model] = {
                "sr": _mean_sd(srs),
                "sr_full": _mean_sd(sr_fulls),
                "ifr": _mean_sd(ifrs),
                "rho": _mean_sd(rhos),
                "avg_time_s": _mean_sd(times),
            }

        # ── Estatística inferencial (IC bootstrap + comparação entre modelos) ─
        inferential = _inferential_statistics(last_rep, models)

        # ── Taxonomia de falhas + drill-down (última repetição) ───────────
        failure_taxonomy = {m: _failure_taxonomy(last_rep[m]) for m in models}

        file_drilldown: list[dict[str, Any]] = []
        for model in models:
            for r in last_rep[model]:
                if not r.scan_result.issues and not r.attempts:
                    continue  # arquivo sem issues e sem tentativas: ruído
                last_attempt = r.attempts[-1] if r.attempts else None
                file_drilldown.append({
                    "model": model,
                    "file": Path(str(r.file)).name,
                    "issues": len(r.scan_result.issues),
                    "issues_fixed": r.issues_fixed,
                    "issues_pending": r.issues_pending,
                    "attempts": len(r.attempts),
                    "final_success": r.final_success,
                    "validation_layer_rejected": (
                        last_attempt.validation_rejected_layer if last_attempt else None
                    ),
                    "last_error": (last_attempt.error or "")[:120] if last_attempt else "",
                    "total_time_s": round(r.total_time, 2),
                    "wcag_criteria": sorted({
                        i.wcag_criteria for i in r.scan_result.issues if i.wcag_criteria
                    }),
                })

        # ── Documento final ────────────────────────────────────────────────
        report: dict[str, Any] = {
            "report_type": "deep_report",
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "experiment": {
                "name": experiment_name,
                "id": experiment_id,
                "models": models,
                "files": files_count,
                "repetitions": n_reps,
                "config_snapshot": config_snapshot or {},
                "tool_versions": tool_versions or {},
            },
            "stability_across_repetitions": stability,
            "inferential_statistics": inferential,
            "per_repetition": per_rep_rows,
            "metrics_last_repetition": full_metrics,
            "failure_taxonomy": failure_taxonomy,
            "file_drilldown": file_drilldown,
            "notes": {
                "ifr_semantics": (
                    "issues_fixed é creditado por re-scan browser-based quando "
                    "verify_fixes_by_rescan=True (Camada 3 real); caso contrário "
                    "é crédito otimista por patch validado."
                ),
                "rho_semantics": (
                    "ρ = rejeições na Camada 2 (regressão funcional) / total de "
                    "tentativas. Requer ValidationPipeline ativo (integrado 06/2026)."
                ),
            },
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "deep_report.json"
        json_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        md = self._render_markdown(report)
        (output_dir / "deep_report.md").write_text(md, encoding="utf-8")
        (output_dir / "deep_report.html").write_text(
            self._render_html(report, md), encoding="utf-8"
        )

        log.info(
            "deep_report_generated",
            json=str(json_path),
            models=len(models),
            repetitions=n_reps,
            drilldown_rows=len(file_drilldown),
        )
        return json_path

    # ── Renderização ───────────────────────────────────────────────────────

    @staticmethod
    def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
        out = ["| " + " | ".join(headers) + " |",
               "|" + "|".join("---" for _ in headers) + "|"]
        for row in rows:
            out.append("| " + " | ".join(
                "—" if v is None else str(v) for v in row
            ) + " |")
        return "\n".join(out)

    def _render_markdown(self, report: dict[str, Any]) -> str:
        exp = report["experiment"]
        fm = report["metrics_last_repetition"]
        lines: list[str] = []
        add = lines.append

        add(f"# Deep Report — {exp['name']}")
        add("")
        add(f"- **ID:** `{exp['id']}` · **Gerado:** {report['generated_at']}")
        add(f"- **Modelos:** {', '.join(exp['models'])} · **Arquivos:** {exp['files']} · "
            f"**Repetições:** {exp['repetitions']}")
        cfg = exp.get("config_snapshot") or {}
        if cfg:
            interesting = {k: cfg[k] for k in
                           ("strategy", "force_agent_type", "wcag_level", "seed",
                            "verify_fixes_by_rescan", "max_retries_per_agent")
                           if k in cfg}
            if interesting:
                add(f"- **Config:** `{json.dumps(interesting, ensure_ascii=False)}`")
        add("")

        # 1. Estabilidade entre repetições
        add("## 1. Métricas primárias (média ± dp entre repetições)")
        add("")
        add("> **Métricas primárias: IFR e SR-completo** (verificadas por re-scan / "
            "por arquivo-com-issue). **SR (≥1 fix) é secundária e inflada**: conta "
            "como sucesso os arquivos SEM issue. Use IFR/SR-completo para comparar modelos.")
        add("")
        rows = []
        for model, s in report["stability_across_repetitions"].items():
            def fmt(key):
                v = s[key]
                if v["mean"] is None:
                    return "—"
                sd = f" ± {v['sd']}" if v["sd"] is not None else ""
                return f"{v['mean']}{sd}"
            rows.append([model, fmt("ifr"), fmt("sr_full"), fmt("rho"),
                         fmt("avg_time_s"), fmt("sr")])
        add(self._md_table(
            ["Modelo", "IFR (primária)", "SR-completo (primária)", "ρ (H5)",
             "Tempo médio (s)", "SR ≥1 fix (sec., inflada)"], rows))
        add("")

        # 1b. Estatística inferencial
        infer = report.get("inferential_statistics") or {}
        if infer:
            add("## 1b. Estatística inferencial (IC 95% bootstrap + comparação entre modelos)")
            add("")
            add(f"_{infer.get('method', '')}_")
            add("")
            rows = []
            for model, d in (infer.get("per_model") or {}).items():
                def ci(metric):
                    m = d.get(metric, {})
                    pt, c = m.get("point"), m.get("ci95", [None, None])
                    if pt is None:
                        return "—"
                    return f"{pt} [{c[0]}, {c[1]}] (n={m.get('n')})"
                rows.append([model, ci("ifr"), ci("sr_effective"), ci("rho")])
            add(self._md_table(
                ["Modelo", "IFR [IC95]", "SR-efetivo [IC95]", "ρ [IC95]"], rows))
            add("")
            comp = infer.get("model_comparison") or {}
            if comp.get("pairwise"):
                add(f"**Comparação entre modelos (IFR issue-level)** — "
                    f"Kruskal-Wallis p = {comp.get('kruskal_wallis_p')}")
                add("")
                rows = [[f"{p['a']} vs {p['b']}", p["p_value_bonferroni"],
                         p["cliffs_delta"], "sim" if p["significant"] else "não"]
                        for p in comp["pairwise"]]
                add(self._md_table(
                    ["Par", "p (Bonferroni)", "Cliff's δ", "Significativo"], rows))
                add("")
            add(f"_{infer.get('caveat', '')}_")
            add("")

        # 2. Validação 4 camadas
        add("## 2. Validação em 4 camadas (última repetição)")
        add("")
        rows = []
        for model, layers in fm["validation_layers"].items():
            rho = fm["regression_rate"][model]
            rows.append([
                model, layers["passed"], layers["layer_1"], layers["layer_2"],
                layers["layer_3"], layers["layer_4"],
                f"{rho['rho']} ({rho['regressions']}/{rho['attempts']})",
            ])
        add(self._md_table(
            ["Modelo", "Aceitos", "L1 sintática", "L2 funcional",
             "L3 domínio", "L4 qualidade", "ρ"], rows))
        add("")

        # 3. IFR por princípio WCAG
        add("## 3. Correção por princípio WCAG")
        add("")
        per_wcag = fm.get("per_wcag_principle", {})
        for model, principles in per_wcag.items():
            add(f"**{model}**")
            add("")
            rows = [[p, d.get("total"), d.get("fixed"), d.get("rate")]
                    for p, d in sorted(principles.items())]
            add(self._md_table(["Princípio", "Issues", "Corrigidos", "IFR"], rows))
            add("")

        # 4. IFR por tipo de issue
        add("## 4. Correção por tipo de issue")
        add("")
        for model, types in fm.get("per_issue_type", {}).items():
            add(f"**{model}**")
            add("")
            rows = [[t, d.get("total"), d.get("fixed"), d.get("rate")]
                    for t, d in sorted(types.items())]
            add(self._md_table(["Tipo", "Issues", "Corrigidos", "IFR"], rows))
            add("")

        # 5. Tentativas e custos
        add("## 5. Tentativas, tokens e diffs")
        add("")
        rows = []
        for model in exp["models"]:
            att = fm["attempt_distribution"].get(model, {})
            tpf = fm["tpf"].get(model)  # float | None (tokens input / fix)
            diff = fm["diff_stats"].get(model, {})
            rows.append([
                model,
                json.dumps(att, ensure_ascii=False),
                tpf,
                diff.get("mean"),
                diff.get("median"),
                diff.get("max"),
            ])
        add(self._md_table(
            ["Modelo", "Distribuição de tentativas", "Tokens/fix",
             "Diff médio (linhas)", "Diff mediano", "Diff máx"], rows))
        add("")

        # 6. Taxonomia de falhas
        add("## 6. Taxonomia de modos de falha")
        add("")
        for model, tax in report["failure_taxonomy"].items():
            add(f"**{model}**")
            add("")
            if tax:
                add(self._md_table(["Modo de falha", "Ocorrências"],
                                   [[k, v] for k, v in tax.items()]))
            else:
                add("_Sem falhas registradas._")
            add("")

        # 7. Por repetição
        add("## 7. Resultados por repetição")
        add("")
        rows = [[r["rep"], r["model"], r["sr"], r["ifr"], r["rho"],
                 f"{r['regressions']}/{r['attempts']}", r["avg_time_s"]]
                for r in report["per_repetition"]]
        add(self._md_table(
            ["Rep", "Modelo", "SR", "IFR", "ρ", "Regressões/Tentativas",
             "Tempo médio (s)"], rows))
        add("")

        # 8. Drill-down por arquivo
        add("## 8. Drill-down por arquivo (última repetição)")
        add("")
        rows = [[d["model"], d["file"], d["issues"], d["issues_fixed"],
                 d["attempts"],
                 "✓" if d["final_success"] else "✗",
                 d["validation_layer_rejected"],
                 ", ".join(d["wcag_criteria"]),
                 d["last_error"]]
                for d in report["file_drilldown"]]
        add(self._md_table(
            ["Modelo", "Arquivo", "Issues", "Corrigidos", "Tentativas",
             "Sucesso", "Rejeitado em", "WCAG", "Último erro"], rows))
        add("")

        add("---")
        add(f"_Semântica: {report['notes']['ifr_semantics']}_")
        add("")
        add(f"_{report['notes']['rho_semantics']}_")
        return "\n".join(lines)

    @staticmethod
    def _render_html(report: dict[str, Any], markdown_body: str) -> str:
        """HTML standalone com o conteúdo do Markdown convertido (tabelas)."""
        # Conversão minimalista md→html (títulos, tabelas, bold, hr)
        html_lines: list[str] = []
        in_table = False
        for line in markdown_body.splitlines():
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if all(set(c) <= {"-"} for c in cells):
                    continue  # separador
                tag = "th" if not in_table else "td"
                if not in_table:
                    html_lines.append("<table>")
                    in_table = True
                html_lines.append(
                    "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
                )
                continue
            if in_table:
                html_lines.append("</table>")
                in_table = False
            def _inline(text: str) -> str:
                while "**" in text:
                    text = text.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
                while "`" in text:
                    text = text.replace("`", "<code>", 1).replace("`", "</code>", 1)
                return text

            if line.startswith("# "):
                html_lines.append(f"<h1>{_inline(line[2:])}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{_inline(line[3:])}</h2>")
            elif line.startswith("---"):
                html_lines.append("<hr>")
            elif line.startswith("- "):
                html_lines.append(f"<p>{_inline(line[2:])}</p>")
            elif line.strip():
                html_lines.append(f"<p>{_inline(line)}</p>")
        if in_table:
            html_lines.append("</table>")

        body = "\n".join(html_lines)
        return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Deep Report — {report['experiment']['name']}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2rem auto;
         padding: 0 1rem; color: #111; }}
  h1 {{ color: #1565C0; border-bottom: 3px solid #1565C0; padding-bottom: .4rem; }}
  h2 {{ color: #1E293B; margin-top: 2rem; }}
  table {{ border-collapse: collapse; margin: .75rem 0; font-size: .85rem; width: 100%; }}
  th {{ background: #1565C0; color: #fff; padding: 6px 10px; text-align: left; }}
  td {{ border: 1px solid #e2e8f0; padding: 5px 10px; }}
  tr:nth-child(even) td {{ background: #f8fafc; }}
  hr {{ border: none; border-top: 1px solid #cbd5e1; margin: 2rem 0; }}
  p {{ line-height: 1.5; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
