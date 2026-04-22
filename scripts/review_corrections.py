"""
Gerador de visualizador de correções para revisão manual.

Lê um ou mais experiment_result.json e gera um HTML autocontido
com diffs coloridos e botões Confirmar / Rejeitar por correção.

As revisões são salvas no localStorage do navegador e podem ser
exportadas como JSON para análise posterior.

Uso:
    python scripts/review_corrections.py \\
        experiment-results/7B_Code_Models.../experiment_result.json \\
        [--limit 50] \\
        [--only-with-diff] \\
        [--only-success] \\
        [--model qwen2.5-coder-7b] \\
        [--output review.html]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── Data loading ──────────────────────────────────────────────────────────────

def load_experiment_results(paths: list[Path]) -> list[dict[str, Any]]:
    """Carrega e mescla resultados de múltiplos experiment_result.json."""
    corrections: list[dict[str, Any]] = []

    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        exp_id = data.get("experiment_id", path.parent.name[:8])
        exp_name = data.get("experiment_name", path.parent.name)

        results_by_model: dict[str, list] = data.get("results_by_model", {})
        for model_id, model_results in results_by_model.items():
            for r in model_results:
                attempts = r.get("attempts", [])
                if not attempts:
                    continue

                # Pegar a melhor tentativa (a de maior sucesso, ou a última)
                best = None
                for att in attempts:
                    if att.get("success") and att.get("diff"):
                        best = att
                        break
                if best is None:
                    best = attempts[-1]

                diff = best.get("diff") or ""
                if not diff:
                    continue

                scan = r.get("scan_result", {})
                issues = scan.get("issues", [])

                file_path = r.get("file", "")
                file_name = os.path.basename(file_path)

                corrections.append({
                    "id": f"{exp_id}_{model_id}_{file_name}_{len(corrections)}",
                    "experiment_id": exp_id,
                    "experiment_name": exp_name,
                    "model": model_id,
                    "agent": best.get("agent", "unknown"),
                    "file_path": file_path,
                    "file_name": file_name,
                    "final_success": r.get("final_success", False),
                    "issues_fixed": r.get("issues_fixed", 0),
                    "issues_pending": r.get("issues_pending", 0),
                    "total_issues": len(issues),
                    "issues": [
                        {
                            "issue_id": iss.get("issue_id", ""),
                            "type": iss.get("type", ""),
                            "wcag": iss.get("wcag_criterion", iss.get("wcag", "")),
                            "description": iss.get("description", ""),
                            "element": iss.get("element", ""),
                            "confidence": iss.get("confidence", ""),
                            "tool": iss.get("tool", ""),
                        }
                        for iss in issues
                    ],
                    "diff": diff,
                    "tokens_total": best.get("tokens_used", 0) or 0,
                    "tokens_prompt": best.get("tokens_prompt", 0) or 0,
                    "tokens_completion": best.get("tokens_completion", 0) or 0,
                    "time_seconds": best.get("time_seconds", 0) or 0,
                    "attempt_number": best.get("attempt_number", 1),
                    "n_attempts": len(attempts),
                    "diff_lines": len([l for l in diff.splitlines()
                                       if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]),
                })

    return corrections


def apply_filters(
    corrections: list[dict],
    only_success: bool,
    only_with_diff: bool,
    model_filter: str | None,
    limit: int | None,
) -> list[dict]:
    result = corrections
    if only_success:
        result = [c for c in result if c["final_success"]]
    if only_with_diff:
        result = [c for c in result if c["diff"].strip()]
    if model_filter:
        result = [c for c in result if model_filter.lower() in c["model"].lower()]
    if limit:
        result = result[:limit]
    return result


# ─── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisão de Correções de Acessibilidade</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3248;
    --text: #e2e4f0;
    --text-dim: #7c82a8;
    --accent: #5c6bc0;
    --green: #2e7d32;
    --green-bg: #1b3a1c;
    --green-text: #81c784;
    --red: #c62828;
    --red-bg: #3a1c1c;
    --red-text: #ef9a9a;
    --yellow: #f9a825;
    --yellow-bg: #3a2e0a;
    --hunk: #1e2a3a;
    --hunk-text: #6ea3d8;
    --radius: 8px;
    --mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

  /* ── Layout ── */
  .app { display: flex; flex-direction: column; height: 100vh; }
  .header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; gap: 16px; flex-shrink: 0; flex-wrap: wrap; }
  .header h1 { font-size: 16px; font-weight: 600; color: var(--text); white-space: nowrap; }
  .header h1 span { color: var(--accent); }

  /* ── Progress ── */
  .progress-bar-wrap { flex: 1; min-width: 200px; }
  .progress-bar-track { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .progress-bar-fill { height: 100%; background: linear-gradient(90deg, var(--accent), #7c4dff); border-radius: 3px; transition: width .3s; }
  .progress-label { font-size: 11px; color: var(--text-dim); margin-top: 4px; }

  /* ── Stats pills ── */
  .stats { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .stat-total { background: var(--surface2); color: var(--text-dim); }
  .stat-confirmed { background: var(--green-bg); color: var(--green-text); }
  .stat-rejected { background: var(--red-bg); color: var(--red-text); }
  .stat-pending { background: var(--yellow-bg); color: var(--yellow); }

  /* ── Controls ── */
  .controls { background: var(--surface); border-bottom: 1px solid var(--border); padding: 8px 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; flex-shrink: 0; }
  .controls label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .5px; }
  .controls select, .controls input { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  .btn { padding: 6px 14px; border-radius: 5px; border: none; cursor: pointer; font-size: 12px; font-weight: 600; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn-export { background: var(--accent); color: #fff; margin-left: auto; }
  .btn-reset-filters { background: var(--surface2); color: var(--text-dim); border: 1px solid var(--border); }

  /* ── Main split ── */
  .main { display: flex; flex: 1; overflow: hidden; }

  /* ── List panel ── */
  .list-panel { width: 340px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid var(--border); background: var(--surface); }
  .list-item { padding: 10px 14px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background .1s; position: relative; }
  .list-item:hover { background: var(--surface2); }
  .list-item.active { background: var(--surface2); border-left: 3px solid var(--accent); }
  .list-item.status-confirmed { border-left: 3px solid var(--green); }
  .list-item.status-rejected  { border-left: 3px solid var(--red); }
  .list-item.active.status-confirmed { border-left: 3px solid var(--green); background: #1a2a1c; }
  .list-item.active.status-rejected  { border-left: 3px solid var(--red); background: #2a1a1a; }

  .list-file { font-size: 13px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .list-meta { font-size: 11px; color: var(--text-dim); margin-top: 2px; display: flex; gap: 8px; flex-wrap: wrap; }
  .list-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; }
  .badge-success { background: var(--green-bg); color: var(--green-text); }
  .badge-fail { background: var(--red-bg); color: var(--red-text); }
  .badge-model { background: var(--surface2); color: var(--accent); border: 1px solid var(--border); }
  .list-status-icon { position: absolute; right: 10px; top: 10px; font-size: 16px; }

  .empty-msg { padding: 40px 20px; text-align: center; color: var(--text-dim); font-size: 13px; }

  /* ── Detail panel ── */
  .detail-panel { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
  .no-selection { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-dim); flex-direction: column; gap: 8px; }
  .no-selection .hint { font-size: 12px; color: #3e4268; }

  /* ── Detail header ── */
  .detail-header { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
  .detail-title { font-size: 15px; font-weight: 600; font-family: var(--mono); word-break: break-all; }
  .detail-path { font-size: 11px; color: var(--text-dim); margin-top: 4px; font-family: var(--mono); word-break: break-all; }
  .detail-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .chip { padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; border: 1px solid transparent; }
  .chip-model { background: #1a2240; color: #7986cb; border-color: #2a3260; }
  .chip-agent { background: #1a2a1a; color: #81c784; border-color: #2a3a2a; }
  .chip-tokens { background: var(--surface2); color: var(--text-dim); }
  .chip-time { background: var(--surface2); color: var(--text-dim); }
  .chip-attempts { background: var(--yellow-bg); color: var(--yellow); }

  /* ── Issues ── */
  .section-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: var(--text-dim); margin-bottom: 8px; }
  .issues-list { display: flex; flex-direction: column; gap: 6px; }
  .issue-item { background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 8px 12px; display: flex; gap: 10px; align-items: flex-start; }
  .issue-wcag { font-size: 10px; font-weight: 700; background: #1a2a3a; color: #64b5f6; border: 1px solid #1e3a5f; padding: 2px 6px; border-radius: 3px; white-space: nowrap; flex-shrink: 0; }
  .issue-desc { font-size: 12px; color: var(--text); line-height: 1.4; }
  .issue-tool { font-size: 10px; color: var(--text-dim); margin-top: 2px; }

  /* ── Diff ── */
  .diff-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
  .diff-toolbar { padding: 8px 14px; background: var(--surface2); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; font-size: 11px; color: var(--text-dim); }
  .diff-toolbar b { color: var(--text); }
  .diff-view-toggle { margin-left: auto; display: flex; gap: 4px; }
  .view-btn { padding: 3px 8px; border-radius: 3px; border: 1px solid var(--border); background: transparent; color: var(--text-dim); cursor: pointer; font-size: 11px; }
  .view-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .diff-table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; line-height: 1.6; }
  .diff-table td { padding: 0 14px; white-space: pre-wrap; word-break: break-all; vertical-align: top; }
  .diff-line-num { width: 40px; text-align: right; color: #3e4268; padding: 0 8px; user-select: none; border-right: 1px solid var(--border); min-width: 40px; }
  .diff-add { background: #0f2b10; color: var(--green-text); }
  .diff-add .diff-sign { color: var(--green-text); }
  .diff-del { background: #2b0f0f; color: var(--red-text); }
  .diff-del .diff-sign { color: var(--red-text); }
  .diff-hunk { background: var(--hunk); color: var(--hunk-text); font-style: italic; }
  .diff-ctx { color: #9095b8; }
  .diff-sign { width: 16px; user-select: none; flex-shrink: 0; }

  /* ── Review panel ── */
  .review-panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
  .review-buttons { display: flex; gap: 10px; margin-bottom: 10px; }
  .btn-confirm { background: var(--green); color: #fff; padding: 10px 24px; font-size: 14px; border-radius: 6px; }
  .btn-reject  { background: var(--red); color: #fff; padding: 10px 24px; font-size: 14px; border-radius: 6px; }
  .btn-clear   { background: var(--surface2); color: var(--text-dim); border: 1px solid var(--border); padding: 10px 16px; font-size: 13px; border-radius: 6px; }
  .btn-confirm:hover { background: #388e3c; }
  .btn-reject:hover  { background: #d32f2f; }

  .review-status { padding: 8px 14px; border-radius: 5px; font-weight: 700; font-size: 13px; text-align: center; }
  .review-status.confirmed { background: var(--green-bg); color: var(--green-text); }
  .review-status.rejected { background: var(--red-bg); color: var(--red-text); }

  .notes-label { font-size: 11px; color: var(--text-dim); margin-bottom: 6px; display: block; text-transform: uppercase; letter-spacing: .5px; }
  .notes-input { width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text); border-radius: 5px; padding: 8px 10px; font-size: 12px; resize: vertical; min-height: 70px; font-family: inherit; }
  .notes-input:focus { outline: none; border-color: var(--accent); }

  /* ── Nav buttons ── */
  .nav-buttons { display: flex; justify-content: space-between; align-items: center; }
  .btn-nav { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 7px 16px; border-radius: 5px; cursor: pointer; font-size: 12px; }
  .btn-nav:hover { background: var(--surface); }
  .btn-nav:disabled { opacity: .3; cursor: not-allowed; }
  .nav-counter { font-size: 12px; color: var(--text-dim); }

  /* ── Keyboard hints ── */
  .kb-hints { font-size: 11px; color: var(--text-dim); display: flex; gap: 14px; flex-wrap: wrap; }
  .kb-hints kbd { background: var(--surface2); border: 1px solid var(--border); border-radius: 3px; padding: 1px 5px; font-family: var(--mono); font-size: 10px; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* ── Toast ── */
  .toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 10px 16px; border-radius: 6px; font-size: 13px; box-shadow: 0 4px 20px rgba(0,0,0,.4); transition: opacity .3s; z-index: 999; }
  .toast.hidden { opacity: 0; pointer-events: none; }

  @media (max-width: 900px) {
    .list-panel { width: 260px; }
    .main { flex-direction: column; }
    .list-panel { width: 100%; height: 200px; flex-shrink: 0; }
  }
</style>
</head>
<body>
<div class="app">
  <!-- Header -->
  <div class="header">
    <h1>Revisão de Correções <span>a11y</span></h1>
    <div class="progress-bar-wrap">
      <div class="progress-bar-track"><div class="progress-bar-fill" id="progressFill" style="width:0%"></div></div>
      <div class="progress-label" id="progressLabel">0 / 0 revisados</div>
    </div>
    <div class="stats">
      <div class="stat stat-total" id="statTotal">0 total</div>
      <div class="stat stat-confirmed" id="statConfirmed">0 ✓</div>
      <div class="stat stat-rejected" id="statRejected">0 ✗</div>
      <div class="stat stat-pending" id="statPending">0 pendentes</div>
    </div>
  </div>

  <!-- Controls -->
  <div class="controls">
    <label>Modelo</label>
    <select id="filterModel"><option value="">Todos</option></select>
    <label>Status</label>
    <select id="filterStatus">
      <option value="">Todos</option>
      <option value="pending">Pendente</option>
      <option value="confirmed">Confirmado ✓</option>
      <option value="rejected">Rejeitado ✗</option>
    </select>
    <label>Sucesso</label>
    <select id="filterSuccess">
      <option value="">Todos</option>
      <option value="yes">Sucesso</option>
      <option value="no">Falha</option>
    </select>
    <label>WCAG</label>
    <input id="filterWcag" placeholder="ex: 1.1.1" style="width:80px">
    <button class="btn btn-reset-filters" onclick="resetFilters()">Limpar filtros</button>
    <button class="btn btn-export" onclick="exportReviews()">⬇ Exportar JSON</button>
  </div>

  <!-- Main -->
  <div class="main">
    <!-- List panel -->
    <div class="list-panel" id="listPanel"></div>

    <!-- Detail panel -->
    <div class="detail-panel" id="detailPanel">
      <div class="no-selection">
        <div style="font-size:40px">🔍</div>
        <div>Selecione uma correção à esquerda</div>
        <div class="hint">Use ↑↓ para navegar · Y/C = confirmar · N/R = rejeitar</div>
      </div>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast hidden" id="toast"></div>

<script>
// ── Data ───────────────────────────────────────────────────────────────────────
const CORRECTIONS = __CORRECTIONS_JSON__;
const STORAGE_KEY = "__STORAGE_KEY__";

// ── State ──────────────────────────────────────────────────────────────────────
let reviews = loadReviews();
let currentIdx = 0;
let filteredIds = [];

// ── Storage ────────────────────────────────────────────────────────────────────
function loadReviews() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch { return {}; }
}
function saveReviews() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(reviews));
}

// ── Init ───────────────────────────────────────────────────────────────────────
function init() {
  // Populate model filter
  const models = [...new Set(CORRECTIONS.map(c => c.model))].sort();
  const sel = document.getElementById('filterModel');
  models.forEach(m => {
    const o = document.createElement('option'); o.value = m; o.textContent = m;
    sel.appendChild(o);
  });

  // Event listeners for filters
  ['filterModel','filterStatus','filterSuccess','filterWcag'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('change', applyFilters);
    if (el.tagName === 'INPUT') el.addEventListener('input', applyFilters);
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', handleKey);

  applyFilters();
}

function handleKey(e) {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowDown' || e.key === 'j') { navigate(1); e.preventDefault(); }
  else if (e.key === 'ArrowUp' || e.key === 'k') { navigate(-1); e.preventDefault(); }
  else if (e.key === 'y' || e.key === 'c') setStatus('confirmed');
  else if (e.key === 'n' || e.key === 'r') setStatus('rejected');
  else if (e.key === 'Backspace' || e.key === 'u') clearStatus();
}

// ── Filter & render list ───────────────────────────────────────────────────────
function applyFilters() {
  const model = document.getElementById('filterModel').value;
  const status = document.getElementById('filterStatus').value;
  const succ = document.getElementById('filterSuccess').value;
  const wcag = document.getElementById('filterWcag').value.trim().toLowerCase();

  filteredIds = CORRECTIONS
    .filter(c => {
      if (model && c.model !== model) return false;
      if (succ === 'yes' && !c.final_success) return false;
      if (succ === 'no' && c.final_success) return false;
      const rev = reviews[c.id] || {};
      if (status === 'confirmed' && rev.status !== 'confirmed') return false;
      if (status === 'rejected' && rev.status !== 'rejected') return false;
      if (status === 'pending' && rev.status) return false;
      if (wcag && !c.issues.some(i => (i.wcag||'').toLowerCase().includes(wcag))) return false;
      return true;
    })
    .map(c => c.id);

  if (currentIdx >= filteredIds.length) currentIdx = 0;
  renderList();
  updateStats();
  if (filteredIds.length > 0) renderDetail(filteredIds[currentIdx]);
  else {
    document.getElementById('detailPanel').innerHTML = `
      <div class="no-selection"><div style="font-size:40px">🔍</div><div>Nenhuma correção encontrada</div></div>`;
  }
}

function resetFilters() {
  document.getElementById('filterModel').value = '';
  document.getElementById('filterStatus').value = '';
  document.getElementById('filterSuccess').value = '';
  document.getElementById('filterWcag').value = '';
  applyFilters();
}

function renderList() {
  const panel = document.getElementById('listPanel');
  if (filteredIds.length === 0) {
    panel.innerHTML = '<div class="empty-msg">Nenhum resultado</div>';
    return;
  }

  panel.innerHTML = filteredIds.map((id, idx) => {
    const c = CORRECTIONS.find(x => x.id === id);
    const rev = reviews[id] || {};
    const statusClass = rev.status ? `status-${rev.status}` : '';
    const activeClass = idx === currentIdx ? 'active' : '';
    const statusIcon = rev.status === 'confirmed' ? '✓' : rev.status === 'rejected' ? '✗' : '';
    const successBadge = c.final_success
      ? `<span class="list-badge badge-success">sucesso</span>`
      : `<span class="list-badge badge-fail">falha</span>`;
    const wcags = [...new Set(c.issues.map(i => i.wcag).filter(Boolean))].slice(0, 3);
    return `<div class="list-item ${activeClass} ${statusClass}" onclick="selectItem(${idx})" data-idx="${idx}">
      <div class="list-file" title="${escHtml(c.file_path)}">${escHtml(c.file_name)}</div>
      <div class="list-meta">
        <span class="list-badge badge-model">${escHtml(c.model.replace('qwen2.5-coder-','Q').replace('codellama-','CL'))}</span>
        ${successBadge}
        ${wcags.map(w => `<span class="list-badge" style="background:#1a2a3a;color:#64b5f6;border:1px solid #1e3a5f">${escHtml(w)}</span>`).join('')}
        <span style="color:var(--text-dim)">±${c.diff_lines}L</span>
      </div>
      ${statusIcon ? `<div class="list-status-icon">${statusIcon}</div>` : ''}
    </div>`;
  }).join('');
}

function selectItem(idx) {
  currentIdx = idx;
  renderList();
  renderDetail(filteredIds[idx]);
  // Scroll list item into view
  const items = document.querySelectorAll('.list-item');
  if (items[idx]) items[idx].scrollIntoView({ block: 'nearest' });
}

function navigate(delta) {
  const newIdx = currentIdx + delta;
  if (newIdx < 0 || newIdx >= filteredIds.length) return;
  selectItem(newIdx);
}

// ── Detail rendering ───────────────────────────────────────────────────────────
function renderDetail(id) {
  const c = CORRECTIONS.find(x => x.id === id);
  if (!c) return;
  const rev = reviews[id] || {};

  const issuesHtml = c.issues.length === 0
    ? '<div style="color:var(--text-dim);font-size:12px">Nenhuma issue detectada (arquivo limpo ou erro de scan)</div>'
    : c.issues.map(iss => `
      <div class="issue-item">
        <div class="issue-wcag">${escHtml(iss.wcag || '?')}</div>
        <div>
          <div class="issue-desc">${escHtml(iss.description || iss.type || '—')}</div>
          <div class="issue-tool">${[iss.element, iss.tool, iss.confidence].filter(Boolean).map(escHtml).join(' · ')}</div>
        </div>
      </div>`).join('');

  const diffHtml = renderDiff(c.diff);

  const reviewStatusHtml = rev.status
    ? `<div class="review-status ${rev.status}">${rev.status === 'confirmed' ? '✓ Confirmado — correção válida' : '✗ Rejeitado — correção inválida ou inadequada'}</div>`
    : '';

  const tokensStr = c.tokens_total
    ? `${c.tokens_total.toLocaleString()} tokens` + (c.tokens_prompt ? ` (${c.tokens_prompt}↑ ${c.tokens_completion}↓)` : '')
    : '—';

  document.getElementById('detailPanel').innerHTML = `
    <div class="detail-header">
      <div class="detail-title">${escHtml(c.file_name)}</div>
      <div class="detail-path">${escHtml(c.file_path)}</div>
      <div class="detail-chips">
        <span class="chip chip-model">🤖 ${escHtml(c.model)}</span>
        <span class="chip chip-agent">⚙ ${escHtml(c.agent)}</span>
        <span class="chip chip-tokens">🪙 ${escHtml(tokensStr)}</span>
        <span class="chip chip-time">⏱ ${c.time_seconds.toFixed(1)}s</span>
        ${c.n_attempts > 1 ? `<span class="chip chip-attempts">🔄 ${c.n_attempts} tentativas</span>` : ''}
        <span class="chip" style="background:${c.final_success ? 'var(--green-bg)' : 'var(--red-bg)'}; color:${c.final_success ? 'var(--green-text)' : 'var(--red-text)'}">
          ${c.final_success ? `✓ ${c.issues_fixed}/${c.total_issues} corrigidas` : `✗ ${c.issues_fixed}/${c.total_issues} corrigidas`}
        </span>
      </div>
    </div>

    <div>
      <div class="section-title">Issues detectadas (${c.issues.length})</div>
      <div class="issues-list">${issuesHtml}</div>
    </div>

    <div class="diff-wrap">
      <div class="diff-toolbar">
        <b>Diff</b> · <span>${c.diff_lines} linhas alteradas</span>
        <div class="diff-view-toggle">
          <button class="view-btn active" onclick="setView('unified', this)">Unified</button>
          <button class="view-btn" onclick="setView('split', this)">Split</button>
        </div>
      </div>
      <div id="diffContainer">${diffHtml}</div>
    </div>

    <div class="review-panel">
      <div class="section-title" style="margin-bottom:10px">Revisão manual</div>
      ${reviewStatusHtml}
      <div class="review-buttons" style="margin-top:${rev.status ? '10px' : '0'}">
        <button class="btn btn-confirm" onclick="setStatus('confirmed')">✓ Confirmar</button>
        <button class="btn btn-reject"  onclick="setStatus('rejected')">✗ Rejeitar</button>
        ${rev.status ? `<button class="btn btn-clear" onclick="clearStatus()">↺ Limpar</button>` : ''}
      </div>
      <div style="margin-top:12px">
        <span class="notes-label">Notas do revisor (opcional)</span>
        <textarea class="notes-input" id="notesInput" placeholder="Ex: correção correta mas alt-text genérico; modelo substituiu elemento errado..."
          onchange="saveNote()">${escHtml(rev.notes || '')}</textarea>
      </div>
      <div class="kb-hints" style="margin-top:10px">
        <span><kbd>↑↓</kbd> ou <kbd>j</kbd><kbd>k</kbd> navegar</span>
        <span><kbd>Y</kbd>/<kbd>C</kbd> confirmar</span>
        <span><kbd>N</kbd>/<kbd>R</kbd> rejeitar</span>
        <span><kbd>U</kbd> limpar</span>
      </div>
    </div>

    <div class="nav-buttons">
      <button class="btn-nav btn" onclick="navigate(-1)" ${currentIdx === 0 ? 'disabled' : ''}>← Anterior</button>
      <span class="nav-counter">${currentIdx + 1} / ${filteredIds.length}</span>
      <button class="btn-nav btn" onclick="navigate(1)" ${currentIdx === filteredIds.length - 1 ? 'disabled' : ''}>Próximo →</button>
    </div>
  `;

  // Store current diff for view switching
  window._currentDiff = c.diff;
  window._currentView = 'unified';
}

// ── Diff renderer ──────────────────────────────────────────────────────────────
function renderDiff(raw, mode) {
  mode = mode || window._currentView || 'unified';
  if (!raw || !raw.trim()) return '<div style="padding:16px;color:var(--text-dim);font-size:12px">Sem diff disponível</div>';

  const lines = raw.split('\n');
  if (mode === 'split') return renderSplitDiff(lines);

  // Unified view
  let html = '<table class="diff-table"><tbody>';
  let lineNumA = 0, lineNumB = 0;
  let hunkA = 0, hunkB = 0;

  for (const line of lines) {
    if (line.startsWith('---') || line.startsWith('+++')) {
      html += `<tr class="diff-hunk"><td class="diff-line-num"></td><td><span class="diff-sign"> </span>${escHtml(line)}</td></tr>`;
      continue;
    }
    if (line.startsWith('@@')) {
      const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) { lineNumA = parseInt(m[1])-1; lineNumB = parseInt(m[2])-1; }
      html += `<tr class="diff-hunk"><td class="diff-line-num"></td><td><span class="diff-sign"> </span>${escHtml(line)}</td></tr>`;
      continue;
    }
    if (line.startsWith('+')) {
      lineNumB++;
      html += `<tr class="diff-add"><td class="diff-line-num">${lineNumB}</td><td><span class="diff-sign">+</span>${escHtml(line.slice(1))}</td></tr>`;
    } else if (line.startsWith('-')) {
      lineNumA++;
      html += `<tr class="diff-del"><td class="diff-line-num">${lineNumA}</td><td><span class="diff-sign">−</span>${escHtml(line.slice(1))}</td></tr>`;
    } else {
      lineNumA++; lineNumB++;
      html += `<tr class="diff-ctx"><td class="diff-line-num">${lineNumB}</td><td><span class="diff-sign"> </span>${escHtml(line.slice(1) ?? line)}</td></tr>`;
    }
  }
  return html + '</tbody></table>';
}

function renderSplitDiff(lines) {
  const lefts = [], rights = [];
  let lineA = 0, lineB = 0;

  for (const line of lines) {
    if (line.startsWith('---') || line.startsWith('+++')) continue;
    if (line.startsWith('@@')) {
      const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) { lineA = parseInt(m[1])-1; lineB = parseInt(m[2])-1; }
      lefts.push({ type:'hunk', num:'', content: line });
      rights.push({ type:'hunk', num:'', content: line });
      continue;
    }
    if (line.startsWith('+')) {
      lineB++;
      rights.push({ type:'add', num: lineB, content: line.slice(1) });
      lefts.push({ type:'empty', num:'', content:'' });
    } else if (line.startsWith('-')) {
      lineA++;
      lefts.push({ type:'del', num: lineA, content: line.slice(1) });
      rights.push({ type:'empty', num:'', content:'' });
    } else {
      lineA++; lineB++;
      lefts.push({ type:'ctx', num: lineA, content: line.slice(1) ?? line });
      rights.push({ type:'ctx', num: lineB, content: line.slice(1) ?? line });
    }
  }

  let html = '<table class="diff-table" style="table-layout:fixed"><colgroup><col style="width:50%"><col style="width:50%"></colgroup><tbody>';
  for (let i = 0; i < lefts.length; i++) {
    const l = lefts[i], r = rights[i];
    const lClass = l.type === 'del' ? 'diff-del' : l.type === 'hunk' ? 'diff-hunk' : 'diff-ctx';
    const rClass = r.type === 'add' ? 'diff-add' : r.type === 'hunk' ? 'diff-hunk' : 'diff-ctx';
    html += `<tr>
      <td class="${lClass}" style="border-right:1px solid var(--border)">
        <span class="diff-line-num">${l.num}</span> ${escHtml(l.content)}
      </td>
      <td class="${rClass}">
        <span class="diff-line-num">${r.num}</span> ${escHtml(r.content)}
      </td>
    </tr>`;
  }
  return html + '</tbody></table>';
}

function setView(mode, btn) {
  document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  window._currentView = mode;
  document.getElementById('diffContainer').innerHTML = renderDiff(window._currentDiff, mode);
}

// ── Review actions ─────────────────────────────────────────────────────────────
function setStatus(status) {
  if (!filteredIds.length) return;
  const id = filteredIds[currentIdx];
  if (!reviews[id]) reviews[id] = {};
  reviews[id].status = status;
  reviews[id].reviewed_at = new Date().toISOString();
  saveReviews();
  updateStats();
  renderList();
  renderDetail(id);
  showToast(status === 'confirmed' ? '✓ Confirmado!' : '✗ Rejeitado');
  // Auto-advance to next unreviewed
  setTimeout(() => {
    const nextUnreviewed = findNextUnreviewed();
    if (nextUnreviewed !== null) selectItem(nextUnreviewed);
  }, 400);
}

function clearStatus() {
  if (!filteredIds.length) return;
  const id = filteredIds[currentIdx];
  if (reviews[id]) { delete reviews[id].status; delete reviews[id].reviewed_at; }
  saveReviews();
  updateStats();
  renderList();
  renderDetail(id);
}

function saveNote() {
  if (!filteredIds.length) return;
  const id = filteredIds[currentIdx];
  if (!reviews[id]) reviews[id] = {};
  reviews[id].notes = document.getElementById('notesInput')?.value || '';
  saveReviews();
}

function findNextUnreviewed() {
  for (let i = currentIdx + 1; i < filteredIds.length; i++) {
    if (!reviews[filteredIds[i]]?.status) return i;
  }
  for (let i = 0; i < currentIdx; i++) {
    if (!reviews[filteredIds[i]]?.status) return i;
  }
  return null;
}

// ── Stats ──────────────────────────────────────────────────────────────────────
function updateStats() {
  const total = filteredIds.length;
  const confirmed = filteredIds.filter(id => reviews[id]?.status === 'confirmed').length;
  const rejected = filteredIds.filter(id => reviews[id]?.status === 'rejected').length;
  const reviewed = confirmed + rejected;
  const pending = total - reviewed;
  const pct = total > 0 ? (reviewed / total * 100) : 0;

  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = `${reviewed} / ${total} revisados (${pct.toFixed(0)}%)`;
  document.getElementById('statTotal').textContent = `${total} total`;
  document.getElementById('statConfirmed').textContent = `${confirmed} ✓`;
  document.getElementById('statRejected').textContent = `${rejected} ✗`;
  document.getElementById('statPending').textContent = `${pending} pendentes`;
}

// ── Export ─────────────────────────────────────────────────────────────────────
function exportReviews() {
  const out = {
    exported_at: new Date().toISOString(),
    total_corrections: CORRECTIONS.length,
    total_reviewed: Object.values(reviews).filter(r => r.status).length,
    corrections: CORRECTIONS.map(c => ({
      id: c.id,
      experiment: c.experiment_name,
      model: c.model,
      agent: c.agent,
      file: c.file_name,
      file_path: c.file_path,
      final_success: c.final_success,
      issues_fixed: c.issues_fixed,
      total_issues: c.total_issues,
      wcag_criteria: [...new Set(c.issues.map(i => i.wcag).filter(Boolean))],
      diff_lines: c.diff_lines,
      tokens_total: c.tokens_total,
      review_status: reviews[c.id]?.status || 'pending',
      review_notes: reviews[c.id]?.notes || '',
      reviewed_at: reviews[c.id]?.reviewed_at || null,
    })),
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `corrections_review_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  showToast('📥 JSON exportado!');
}

// ── Utils ──────────────────────────────────────────────────────────────────────
function escHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => t.classList.add('hidden'), 1800);
}

// ── Boot ───────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


# ─── HTML generation ───────────────────────────────────────────────────────────

def generate_html(corrections: list[dict], storage_key: str) -> str:
    """Gera o HTML autocontido com os dados embutidos."""
    corrections_json = json.dumps(corrections, ensure_ascii=False, indent=None)
    html = HTML_TEMPLATE.replace("__CORRECTIONS_JSON__", corrections_json)
    html = html.replace("__STORAGE_KEY__", storage_key)
    return html


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera visualizador HTML de correções para revisão manual.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="experiment_result.json",
        help="Um ou mais arquivos experiment_result.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limitar a N correções (útil para revisão rápida)",
    )
    parser.add_argument(
        "--only-with-diff",
        action="store_true",
        default=True,
        help="Incluir apenas correções com diff disponível (padrão: True)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Incluir todos os resultados, mesmo sem diff",
    )
    parser.add_argument(
        "--only-success",
        action="store_true",
        help="Incluir apenas correções com final_success=True",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help="Filtrar por modelo (substring)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Caminho de saída do HTML (padrão: corrections_review_<timestamp>.html)",
    )
    args = parser.parse_args()

    # Load
    input_paths = [Path(p) for p in args.inputs]
    for p in input_paths:
        if not p.exists():
            print(f"[ERRO] Arquivo não encontrado: {p}", file=sys.stderr)
            sys.exit(1)

    print(f"Carregando {len(input_paths)} arquivo(s)...", flush=True)
    corrections = load_experiment_results(input_paths)
    print(f"  → {len(corrections)} correções com diff encontradas")

    # Filters
    only_with_diff = not args.all
    filtered = apply_filters(
        corrections,
        only_success=args.only_success,
        only_with_diff=only_with_diff,
        model_filter=args.model,
        limit=args.limit,
    )
    print(f"  → {len(filtered)} correções após filtros")

    if not filtered:
        print("[AVISO] Nenhuma correção após filtros. Verifique os argumentos.", file=sys.stderr)
        sys.exit(0)

    # Generate HTML
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    storage_key = f"a11y_review_{ts}"
    html = generate_html(filtered, storage_key)

    # Write
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(f"corrections_review_{ts}.html")

    out_path.write_text(html, encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    print(f"\n✓ Visualizador gerado: {out_path}")
    print(f"  Tamanho: {size_kb:.0f} KB")
    print(f"  Correções: {len(filtered)}")
    print(f"  Atalhos: ↑↓ navegar · Y/C confirmar · N/R rejeitar · U limpar")
    print(f"\nAbra no navegador: file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
