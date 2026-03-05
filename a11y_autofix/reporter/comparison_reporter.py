"""Gerador de relatório comparativo multi-modelo para experimentos."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import structlog
from jinja2 import BaseLoader, Environment

from a11y_autofix.config import ExperimentResult

log = structlog.get_logger(__name__)

_COMPARISON_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>♿ Experiment: {{ name }}</title>
  <style>
    :root { --primary: #7c3aed; --success: #16a34a; --danger: #dc2626; --muted: #6b7280; --conf: #1d4ed8; --expl: #92400e; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui,-apple-system,sans-serif; background: #f5f3ff; color: #111; }
    header { background: var(--primary); color: white; padding: 1.5rem 2rem; }
    header h1 { font-size: 1.4rem; }
    header p { font-size: 0.85rem; opacity: 0.85; margin-top: 0.3rem; }
    main { max-width: 1200px; margin: 0 auto; padding: 2rem; }
    h2 { font-size: 1.15rem; margin: 1.5rem 0 0.75rem; color: #4c1d95; border-bottom: 2px solid #ede9fe; padding-bottom: 0.4rem; }
    table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 1.5rem; }
    th { background: #ede9fe; color: #4c1d95; padding: .75rem 1rem; text-align: left; font-size: 0.85rem; }
    td { padding: .75rem 1rem; border-bottom: 1px solid #f3f4f6; font-size: 0.85rem; }
    tr:last-child td { border-bottom: none; }
    .winner { font-weight: 700; color: var(--success); }
    .bar-container { height: 10px; background: #e9d5ff; border-radius: 5px; overflow: hidden; }
    .bar { height: 100%; background: var(--primary); border-radius: 5px; }
    .tag { display: inline-block; padding: .15rem .5rem; border-radius: 999px; font-size: 0.7rem; font-weight: 600; background: #ede9fe; color: #4c1d95; margin: 0 2px; }
    .analysis-legend { background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.5rem; font-size: 0.85rem; }
    .analysis-legend h3 { font-size: 0.95rem; margin-bottom: 0.5rem; color: #0c4a6e; }
    .confirmatory-badge { display: inline-block; background: #dbeafe; color: var(--conf); padding: .2rem .6rem; border-radius: 4px; font-weight: 700; font-size: 0.75rem; margin-right: .5rem; }
    .exploratory-badge { display: inline-block; background: #fef3c7; color: var(--expl); padding: .2rem .6rem; border-radius: 4px; font-weight: 700; font-size: 0.75rem; margin-right: .5rem; }
    .caption { font-size: 0.75rem; color: var(--muted); margin-top: 0.25rem; margin-bottom: 0.75rem; }
    footer { text-align: center; padding: 2rem; color: var(--muted); font-size: 0.8rem; }
  </style>
</head>
<body>
<header>
  <h1>♿ Experiment: {{ name }}</h1>
  <p>{{ timestamp }} · {{ models|length }} models · {{ files_processed }} files · Execution ID: {{ experiment_id }}</p>
</header>
<main>

  <div class="analysis-legend">
    <h3>Analysis Type Legend</h3>
    <p>
      <span class="confirmatory-badge">■ Confirmatory (H1–H4)</span>
      Tests pre-specified in the methodology prior to data collection.
      Interpreted within the inferential framework (α = 0.05, Cliff's δ).
    </p>
    <p style="margin-top:0.5rem">
      <span class="exploratory-badge">□ Exploratory</span>
      Descriptive analyses generating hypotheses for future work.
      Not interpreted as confirmatory evidence.
    </p>
  </div>

  <h2>Sumário Comparativo <span class="confirmatory-badge">■ Confirmatory — H2/H3</span></h2>
  <p class="caption">SR (file-level binary), IFR (issue-level partial credit), MTTR (over fixed files only), TE (token efficiency). Methodology Section 3.7.1.</p>
  <table>
    <thead>
      <tr>
        <th>Modelo</th>
        <th>SR</th>
        <th>IFR</th>
        <th>MTTR (s)</th>
        <th>TE</th>
        <th>Visual SR</th>
        <th>Tempo Médio</th>
        <th>Issues Corrigidos</th>
      </tr>
    </thead>
    <tbody>
    {% for model in models %}
    {% set m = metrics[model] %}
    <tr {% if loop.first %}class="winner"{% endif %}>
      <td>{{ model }}{% if loop.first %} 🏆{% endif %}</td>
      <td>{{ "%.3f"|format(m.sr if m.sr is not none else 0) }}</td>
      <td>{{ "%.3f"|format(m.ifr if m.ifr is not none else 0) }}</td>
      <td>{{ "%.1f"|format(m.mttr) if m.mttr is not none else 'N/A' }}</td>
      <td>{{ "%.3f"|format(m.te) if m.te is not none else 'N/A' }}</td>
      <td>
        <div class="bar-container">
          <div class="bar" style="width:{{ ((m.sr if m.sr is not none else 0) * 100)|round }}%"></div>
        </div>
      </td>
      <td>{{ "%.1f"|format(m.avg_time) }}s</td>
      <td>{{ m.issues_fixed }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>

  <h2>Detalhes por Arquivo <span class="exploratory-badge">□ Exploratory</span></h2>
  <p class="caption">Per-file success/failure breakdown. Not a confirmatory test.</p>
  <table>
    <thead>
      <tr>
        <th>Arquivo</th>
        {% for model in models %}<th>{{ model }}</th>{% endfor %}
      </tr>
    </thead>
    <tbody>
    {% for file_name in file_names %}
    <tr>
      <td style="font-family:monospace;font-size:0.8rem">{{ file_name }}</td>
      {% for model in models %}
      {% set result = file_results.get(model, {}).get(file_name) %}
      <td>
        {% if result %}
          {% if result.final_success %}
            <span style="color:var(--success)">✓</span>
            ({{ result.issues_fixed }} fixed)
          {% else %}
            <span style="color:var(--danger)">✗</span>
            ({{ result.issues_pending }} pending)
          {% endif %}
        {% else %}
          <span style="color:var(--muted)">—</span>
        {% endif %}
      </td>
      {% endfor %}
    </tr>
    {% endfor %}
    </tbody>
  </table>

</main>
<footer>
  Gerado por ♿ a11y-autofix v2.0 · Experiment: {{ experiment_id }}
</footer>
</body>
</html>"""


class ComparisonReporter:
    """Gera relatório HTML comparativo para experimentos multi-modelo."""

    def generate(
        self,
        result: ExperimentResult,
        metrics: dict[str, dict[str, Any]],
        output_dir: Path,
    ) -> Path:
        """
        Gera relatório HTML comparativo e CSV de dados.

        Args:
            result: Resultado do experimento.
            metrics: Métricas calculadas por compute_experiment_metrics.
            output_dir: Diretório de saída.

        Returns:
            Caminho do arquivo HTML gerado.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Ordenar modelos por taxa de sucesso (melhor primeiro)
        models_sorted = sorted(
            result.models_tested,
            key=lambda m: metrics.get(m, {}).get("success_rate", 0),
            reverse=True,
        )

        # Construir mapa file_name → {model → FixResult}
        file_results: dict[str, dict[str, Any]] = {}
        file_names_set: set[str] = set()

        for model_name, fix_results in result.results_by_model.items():
            for fix in fix_results:
                fname = fix.file.name
                file_names_set.add(fname)
                if fname not in file_results:
                    file_results[fname] = {}
                file_results[fname][model_name] = fix

        file_names = sorted(file_names_set)

        # Gerar HTML
        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(_COMPARISON_TEMPLATE)
        html = template.render(
            name=result.experiment_name,
            timestamp=result.timestamp.isoformat(),
            experiment_id=result.experiment_id,
            models=models_sorted,
            files_processed=result.files_processed,
            metrics=metrics,
            file_names=file_names,
            file_results=file_results,
        )

        html_path = output_dir / "comparison.html"
        html_path.write_text(html, encoding="utf-8")

        # Gerar CSV
        csv_path = self._generate_csv(result, metrics, models_sorted, output_dir)

        log.info(
            "comparison_report_generated",
            html=str(html_path),
            csv=str(csv_path),
        )
        return html_path

    def _generate_csv(
        self,
        result: ExperimentResult,
        metrics: dict[str, dict[str, Any]],
        models_sorted: list[str],
        output_dir: Path,
    ) -> Path:
        """Gera arquivo CSV com métricas comparativas."""
        csv_path = output_dir / "metrics.csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "model", "success_rate", "avg_time",
                    "issues_fixed", "files_processed", "total_tokens",
                ],
            )
            writer.writeheader()
            for model in models_sorted:
                m = metrics.get(model, {})
                writer.writerow({
                    "model": model,
                    "success_rate": round(m.get("success_rate", 0), 2),
                    "avg_time": round(m.get("avg_time", 0), 3),
                    "issues_fixed": m.get("issues_fixed", 0),
                    "files_processed": m.get("files_processed", 0),
                    "total_tokens": m.get("total_tokens", ""),
                })

        return csv_path
