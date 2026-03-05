"""Gerador de relatórios HTML visuais."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, BaseLoader

log = structlog.get_logger(__name__)

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>♿ a11y-autofix Report — {{ timestamp }}</title>
  <style>
    :root {
      --primary: #2563eb; --success: #16a34a; --warning: #ca8a04;
      --danger: #dc2626; --muted: #6b7280; --bg: #f9fafb;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui,-apple-system,sans-serif; background: var(--bg); color: #111; }
    header { background: var(--primary); color: white; padding: 1.5rem 2rem; }
    header h1 { font-size: 1.5rem; }
    header p { font-size: 0.9rem; opacity: 0.85; margin-top: 0.25rem; }
    main { max-width: 1200px; margin: 0 auto; padding: 2rem; }
    .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin: 1.5rem 0; }
    .metric-card { background: white; border-radius: 8px; padding: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,.1); text-align: center; }
    .metric-card .value { font-size: 2rem; font-weight: 700; color: var(--primary); }
    .metric-card .label { font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }
    .file-card { background: white; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
    .file-header { display: flex; justify-content: space-between; align-items: center; }
    .file-name { font-family: monospace; font-weight: 600; }
    .badge { display: inline-block; padding: .25rem .6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
    .badge-success { background: #dcfce7; color: var(--success); }
    .badge-danger { background: #fee2e2; color: var(--danger); }
    .badge-warning { background: #fef9c3; color: var(--warning); }
    .badge-muted { background: #f3f4f6; color: var(--muted); }
    .issue-list { margin-top: 0.75rem; }
    .issue { border-left: 3px solid var(--warning); padding: .5rem .75rem; margin-bottom: .5rem; background: #fafafa; }
    .issue.high { border-color: var(--danger); }
    .issue.medium { border-color: var(--warning); }
    .issue.low { border-color: #86efac; }
    .issue-header { font-size: 0.85rem; font-weight: 600; }
    .issue-meta { font-size: 0.75rem; color: var(--muted); margin-top: 2px; }
    .diff-block { background: #1e1e1e; color: #d4d4d4; font-family: monospace; font-size: 0.8rem; padding: 1rem; border-radius: 6px; overflow-x: auto; white-space: pre; margin-top: 0.5rem; }
    .diff-add { color: #86efac; }
    .diff-remove { color: #fca5a5; }
    h2 { font-size: 1.2rem; margin: 1.5rem 0 0.75rem; color: #374151; }
    footer { text-align: center; padding: 2rem; color: var(--muted); font-size: 0.8rem; }
  </style>
</head>
<body>
<header>
  <h1>♿ a11y-autofix Report</h1>
  <p>{{ timestamp }} · WCAG {{ wcag_level }} · {{ summary.total_files }} files · Model: {{ model }}</p>
</header>
<main>
  <h2>Resumo</h2>
  <div class="summary-grid">
    <div class="metric-card"><div class="value">{{ summary.total_files }}</div><div class="label">Arquivos</div></div>
    <div class="metric-card"><div class="value">{{ summary.total_issues }}</div><div class="label">Issues</div></div>
    <div class="metric-card"><div class="value">{{ summary.issues_fixed }}</div><div class="label">Corrigidos</div></div>
    <div class="metric-card"><div class="value">{{ "%.1f"|format(summary.success_rate) }}%</div><div class="label">Taxa Sucesso</div></div>
    <div class="metric-card"><div class="value">{{ "%.1f"|format(summary.total_time_seconds) }}s</div><div class="label">Tempo Total</div></div>
    <div class="metric-card"><div class="value">{{ summary.high_confidence_issues }}</div><div class="label">Alta Confiança</div></div>
  </div>

  <h2>Resultados por Arquivo</h2>
  {% for file_entry in files %}
  <div class="file-card">
    <div class="file-header">
      <span class="file-name">{{ file_entry.file }}</span>
      <div>
        {% if file_entry.fix and file_entry.fix.success %}
          <span class="badge badge-success">✓ Corrigido</span>
        {% elif file_entry.issues %}
          <span class="badge badge-danger">✗ Pendente</span>
        {% else %}
          <span class="badge badge-muted">✓ Sem issues</span>
        {% endif %}
        <span class="badge badge-muted">{{ file_entry.issues|length }} issues</span>
      </div>
    </div>
    {% if file_entry.issues %}
    <div class="issue-list">
      {% for issue in file_entry.issues[:10] %}
      <div class="issue {{ issue.confidence }}">
        <div class="issue-header">
          [{{ issue.type|upper }}] WCAG {{ issue.wcag_criteria or 'N/A' }} — {{ issue.message[:100] }}
        </div>
        <div class="issue-meta">
          Selector: <code>{{ issue.selector }}</code> ·
          Impact: {{ issue.impact }} ·
          Confidence: {{ issue.confidence }} ·
          Found by: {{ issue.found_by|join(', ') }}
        </div>
      </div>
      {% endfor %}
      {% if file_entry.issues|length > 10 %}
      <p style="font-size:0.8rem;color:var(--muted);">... e mais {{ file_entry.issues|length - 10 }} issues</p>
      {% endif %}
    </div>
    {% endif %}
    {% if file_entry.fix and file_entry.fix.attempts %}
    <details style="margin-top:0.75rem">
      <summary style="cursor:pointer;font-size:0.85rem;color:var(--primary)">Ver diff da correção</summary>
      {% for attempt in file_entry.fix.attempts %}
      {% if attempt.diff %}
      <div class="diff-block">
        {%- for line in attempt.diff.splitlines() -%}
        {%- if line.startswith('+') and not line.startswith('+++') %}<span class="diff-add">{{ line }}</span>
        {%- elif line.startswith('-') and not line.startswith('---') %}<span class="diff-remove">{{ line }}</span>
        {%- else %}{{ line }}
        {%- endif -%}
        {%- endfor %}
      </div>
      {% endif %}
      {% endfor %}
    </details>
    {% endif %}
  </div>
  {% endfor %}
</main>
<footer>
  Gerado por ♿ a11y-autofix v2.0 · Execution ID: {{ execution_id }}
</footer>
</body>
</html>"""


class HTMLReporter:
    """Gera relatório HTML visual para um run do pipeline."""

    def generate(
        self,
        report_data: dict[str, object],
        output_dir: Path,
    ) -> Path:
        """
        Gera relatório HTML a partir dos dados do JSON reporter.

        Args:
            report_data: Dados do relatório (estrutura do JSONReporter).
            output_dir: Diretório de saída.

        Returns:
            Caminho do arquivo HTML gerado.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(_HTML_TEMPLATE)

        html = template.render(
            timestamp=report_data.get("timestamp", ""),
            wcag_level=report_data.get("wcag_level", "WCAG2AA"),
            model=report_data.get("environment", {}).get("llm_model", "unknown"),  # type: ignore[union-attr]
            execution_id=report_data.get("execution_id", ""),
            summary=report_data.get("summary", {}),
            files=report_data.get("files", []),
        )

        output_path = output_dir / "report.html"
        output_path.write_text(html, encoding="utf-8")
        log.info("html_report_generated", path=str(output_path))
        return output_path
