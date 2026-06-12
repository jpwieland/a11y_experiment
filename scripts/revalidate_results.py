#!/usr/bin/env python3
"""
Revalidação post-hoc dos resultados experimentais do a11y-autofix.

Motivação (auditoria de junho/2026):
  1. A ValidationPipeline (4 camadas) nunca foi invocada no fluxo de produção —
     `patch.success` significava apenas "o LLM devolveu um bloco de código
     plausível" (extract_code_block + validate_tsx_basic).
  2. `issues_fixed` era atribuído sem verificação: toda issue pendente era
     marcada como corrigida quando o patch era aceito (binário por arquivo).
  3. ρ (H5) era estruturalmente zero porque o prefixo "functional_regression:"
     só é gerado pela ValidationPipeline, que nunca rodava.
  4. 240/338 issues (71%) do experimento canônico são `aria-prohibited-attr`
     no seletor `#root` — artefato do harness de preview (<div id="root"
     aria-label="Component preview">), não código dos arquivos avaliados.

Este script lê os experiment_result.json existentes (os patches completos
estão preservados em attempts[].new_content + attempts[].diff), reconstrói o
conteúdo original revertendo o unified diff, e recomputa todas as métricas
com validação real:

  • Camada 1 (sintática)      — replicada de ValidationPipeline._validate_layer1
  • Camada 2 (funcional)      — importada de a11y_autofix/validation/layer2.py
  • Camada 4 (qualidade)      — replicada de ValidationPipeline._validate_layer4
  • Camada 3 (domínio)        — NOVA verificação estática POR ISSUE, baseada
                                no rule_id de cada finding (ver _verify_issue)
  • Classificação de corpus   — issues do harness (#root/aria-prohibited-attr)
                                são separadas das issues reais de arquivo
  • Noop detection            — patch aceito sem mudança real de código

Veredito por issue ∈ {fixed, not_fixed, unverifiable, harness_artifact}

Métricas recomputadas por modelo (e por repetição):
  IFR_strict   = fixed / (fixed + not_fixed + unverifiable)   [unverifiable conta contra]
  IFR_lenient  = fixed / (fixed + not_fixed)                  [unverifiable excluída]
  ρ            = rejeições L2 / patches validados (com IC 95% Wilson)
  SR_full      = arquivos com TODAS as issues reais corrigidas / arquivos com issues reais
  SR_partial   = arquivos com ≥1 issue real corrigida / arquivos com issues reais
  noop_rate    = patches aceitos sem mudança real / patches aceitos

Uso:
    python scripts/revalidate_results.py [--results-dir DIR] [--output DIR]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Importar layer2 real do projeto (stdlib-only, sem dependências) ──────────
_REPO = Path(__file__).resolve().parent.parent
_layer2_path = _REPO / "a11y_autofix" / "validation" / "layer2.py"
_spec = importlib.util.spec_from_file_location("layer2", _layer2_path)
_layer2 = importlib.util.module_from_spec(_spec)
sys.modules["layer2"] = _layer2  # necessário para @dataclass resolver o módulo
_spec.loader.exec_module(_layer2)
run_layer2 = _layer2.run_layer2


# ═══════════════════════════════════════════════════════════════════════════════
# Reconstrução do conteúdo original (reverse-apply do unified diff)
# ═══════════════════════════════════════════════════════════════════════════════

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def reverse_apply_unified(diff: str, new_content: str) -> str | None:
    """
    Reconstrói o conteúdo ORIGINAL a partir do unified diff e do conteúdo NOVO.

    O diff foi gerado por difflib.unified_diff(original, modified) com
    keepends=True (ver utils/git.py:get_unified_diff). Percorre os hunks
    usando os números de linha do lado novo (+c,d) e substitui as linhas
    '+' pelas linhas '-' correspondentes.

    Retorna None se o diff não puder ser revertido com segurança
    (contexto não confere com new_content).
    """
    if not diff.strip():
        return new_content  # diff vazio = noop → original ≡ novo

    new_lines = new_content.splitlines(keepends=True)
    diff_lines = diff.splitlines(keepends=True)

    old_parts: list[str] = []
    new_pos = 0  # índice 0-based em new_lines
    i = 0
    n_diff = len(diff_lines)
    saw_hunk = False

    while i < n_diff:
        line = diff_lines[i]
        if line.startswith("---") or line.startswith("+++"):
            i += 1
            continue
        m = _HUNK_RE.match(line)
        if not m:
            i += 1
            continue

        saw_hunk = True
        new_start = int(m.group(3))  # 1-based
        # Copiar região inalterada antes do hunk
        target = new_start - 1
        if target < new_pos or target > len(new_lines):
            return None  # hunks fora de ordem ou inválidos
        old_parts.extend(new_lines[new_pos:target])
        new_pos = target
        i += 1

        # Consumir corpo do hunk
        while i < n_diff:
            dl = diff_lines[i]
            if _HUNK_RE.match(dl) or dl.startswith("---") or dl.startswith("+++"):
                break
            prefix = dl[:1]
            body = dl[1:]
            if prefix == " ":
                # contexto: deve bater com new_lines[new_pos]
                if new_pos >= len(new_lines):
                    return None
                # Tolerância: comparar sem o newline final (difflib pode
                # produzir última linha sem \n)
                if new_lines[new_pos].rstrip("\n") != body.rstrip("\n"):
                    return None
                old_parts.append(new_lines[new_pos])
                new_pos += 1
                i += 1
            elif prefix == "-":
                # linha que existia só no original
                old_parts.append(body)
                i += 1
            elif prefix == "+":
                # linha que existe só no novo: pular no new_content
                if new_pos >= len(new_lines):
                    return None
                new_pos += 1
                i += 1
            else:
                # linha estranha (ex.: glue de difflib sem \n) — abortar com
                # segurança em vez de reconstruir conteúdo errado
                return None

    if not saw_hunk:
        return None

    # Copiar o resto do arquivo após o último hunk
    old_parts.extend(new_lines[new_pos:])
    return "".join(old_parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Camadas 1 e 4 — replicadas de ValidationPipeline (validation/pipeline.py)
# ═══════════════════════════════════════════════════════════════════════════════

_REFUSAL_PATTERNS = [
    r"I (can'?t|cannot|am unable to)",
    r"As an AI",
    r"I don'?t have access",
]


def validate_layer1(content: str) -> tuple[bool, str | None]:
    """Camada 1: validação sintática (heurística, idêntica à do projeto)."""
    if not content or not content.strip():
        return False, "empty_patch"
    if "```tsx" in content and content.count("```") == 1:
        return False, "unclosed_code_block"
    for pattern in _REFUSAL_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return False, "llm_refusal"
    if not re.search(r"<\w[\w.]*(?:\s[^>]*)?\s*/?>", content):
        return False, "no_jsx_found"
    return True, None


def validate_layer4(content: str) -> tuple[bool, str | None]:
    """Camada 4: qualidade de código (idêntica à do projeto)."""
    tab_indices = re.findall(r"tabIndex\s*[=:]\s*\{?\s*(-?\d+)", content)
    for val in tab_indices:
        if int(val) < -1:
            return False, f"invalid_tabIndex:{val}"
    if "dangerouslySetInnerHTML" in content:
        return False, "dangerouslySetInnerHTML_present"
    return True, None


# ═══════════════════════════════════════════════════════════════════════════════
# Classificação de corpus: issue do harness vs issue real de arquivo
# ═══════════════════════════════════════════════════════════════════════════════

def is_harness_artifact(issue: dict[str, Any]) -> bool:
    """
    Detecta o falso positivo sistemático do harness de preview.

    O harness renderiza componentes dentro de <div id="root"
    aria-label="Component preview">; o axe acusa aria-prohibited-attr
    nesse div — que não pertence ao arquivo avaliado e não pode ser
    corrigido pelo LLM.
    """
    selector = (issue.get("selector") or "").strip().lower()
    context = (issue.get("context") or "").lower()
    rules = {
        (f.get("rule_id") or "").lower()
        for f in issue.get("findings", [])
    }
    if selector == "#root" and "aria-prohibited-attr" in rules:
        return True
    if 'id="root"' in context and "component preview" in context:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Camada 3 REAL: verificação estática por issue (baseada no rule_id)
# ═══════════════════════════════════════════════════════════════════════════════

_ARIA_ATTR_RE = re.compile(r"\baria-[\w-]+")
_COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)"
    r"|(?:^|[\s:\"'])(?:color|background(?:-color)?|backgroundColor)\s*[:=]\s*['\"]?([\w#(),. ]+)",
    re.MULTILINE,
)
_SIZE_PROP_RE = re.compile(
    r"\b(?:padding|margin|width|height|min-?[wW]idth|min-?[hH]eight|fontSize|font-size)\b\s*[:=]\s*['\"{]?\s*([\w.%]+)"
)


def _count(pattern: str, content: str, flags: int = 0) -> int:
    return len(re.findall(pattern, content, flags))


def _imgs_without_alt(content: str) -> int:
    imgs = re.findall(r"<img\b[^>]*>", content, re.IGNORECASE | re.DOTALL)
    return sum(1 for tag in imgs if not re.search(r"\balt\s*=", tag, re.IGNORECASE))


def _iframes_without_title(content: str) -> int:
    frames = re.findall(r"<iframe\b[^>]*>", content, re.IGNORECASE | re.DOTALL)
    return sum(1 for tag in frames if not re.search(r"\btitle\s*=", tag, re.IGNORECASE))


def _labelish_count(content: str) -> int:
    return (
        _count(r"\baria-label\s*=", content)
        + _count(r"\baria-labelledby\s*=", content)
        + _count(r"\bhtmlFor\s*=", content)
        + _count(r"<label\b", content, re.IGNORECASE)
        + _count(r"\btitle\s*=", content)
    )


def _keyboardish_count(content: str) -> int:
    return (
        _count(r"\bonKey(?:Down|Up|Press)\b", content)
        + _count(r"\btabIndex\s*[=:]", content)
        + _count(r"\brole\s*=", content)
    )


def _color_fingerprint(content: str) -> Counter:
    return Counter(m.group(0) for m in _COLOR_RE.finditer(content))


def _size_fingerprint(content: str) -> Counter:
    return Counter(m.group(0) for m in _SIZE_PROP_RE.finditer(content))


def _verify_issue(issue: dict[str, Any], original: str, patched: str) -> str:
    """
    Verifica estaticamente se o patch endereça a issue.

    Retorna 'fixed', 'not_fixed' ou 'unverifiable'.

    NOTA DE HONESTIDADE METODOLÓGICA: estes são checks de condição
    necessária — 'fixed' significa "o patch alterou o aspecto do código
    que a regra fiscaliza", não "um re-scan de navegador confirmou a
    resolução". Ainda assim, é estritamente mais forte que o critério
    anterior (qualquer bloco de código plausível = tudo corrigido).
    """
    rules = {(f.get("rule_id") or "").lower() for f in issue.get("findings", [])}
    rules_str = " ".join(rules)
    itype = (issue.get("issue_type") or "").lower()
    wcag = issue.get("wcag_criteria") or ""

    # ── alt-text (1.1.1) ────────────────────────────────────────────────────
    if "alt-text" in rules_str or "image-alt" in rules_str or itype == "alt-text":
        before, after = _imgs_without_alt(original), _imgs_without_alt(patched)
        return "fixed" if after < before else "not_fixed"

    # ── frame-title ─────────────────────────────────────────────────────────
    if "frame-title" in rules_str:
        before, after = _iframes_without_title(original), _iframes_without_title(patched)
        return "fixed" if after < before else "not_fixed"

    # ── teclado (2.1.1, click-events-have-key-events, G90) ─────────────────
    if (
        "click-events-have-key-events" in rules_str
        or "g90" in rules_str
        or (itype == "keyboard")
    ):
        return "fixed" if _keyboardish_count(patched) > _keyboardish_count(original) else "not_fixed"

    # ── rótulos/nome acessível (label, *-name, H91, F68, H85, 2.4.x) ──────
    if (
        any(r in rules_str for r in (
            "label", "select-name", "button-name", "link-name",
            "h91", "f68", "h85", "h44", "input-button-name",
        ))
        or itype == "label"
    ):
        return "fixed" if _labelish_count(patched) > _labelish_count(original) else "not_fixed"

    # ── contraste (1.4.3, color-contrast, H49) ──────────────────────────────
    if "color-contrast" in rules_str or "h49" in rules_str or itype == "contrast" or wcag == "1.4.3":
        return "fixed" if _color_fingerprint(patched) != _color_fingerprint(original) else "not_fixed"

    # ── alvo de toque (2.5.8, target-size) ──────────────────────────────────
    if "target-size" in rules_str or wcag == "2.5.8":
        return "fixed" if _size_fingerprint(patched) != _size_fingerprint(original) else "not_fixed"

    # ── ARIA in-file (aria-prohibited-attr fora do harness, aria-role etc.) ─
    if itype == "aria" or "aria-" in rules_str:
        # Tentar extrair o atributo aria ofensivo do contexto da issue
        context = issue.get("context") or ""
        offending = set(_ARIA_ATTR_RE.findall(context))
        if offending:
            before = sum(_count(re.escape(a) + r"\s*=", original) for a in offending)
            after = sum(_count(re.escape(a) + r"\s*=", patched) for a in offending)
            if before > 0:
                return "fixed" if after < before else "not_fixed"
        # Sem contexto acionável: mudança em qualquer aria-*/role conta
        before_aria = len(_ARIA_ATTR_RE.findall(original)) + _count(r"\brole\s*=", original)
        after_aria = len(_ARIA_ATTR_RE.findall(patched)) + _count(r"\brole\s*=", patched)
        if before_aria != after_aria:
            return "fixed"
        return "unverifiable"

    # ── semântica (1.3.1) ───────────────────────────────────────────────────
    if itype == "semantic" or wcag.startswith("1.3"):
        # Mudança em tags semânticas / headings / list structure
        sem_re = r"<(?:h[1-6]|ul|ol|li|table|th|td|thead|tbody|main|nav|header|footer|section|article|fieldset|legend|optgroup|label)\b"
        before, after = _count(sem_re, original, re.IGNORECASE), _count(sem_re, patched, re.IGNORECASE)
        if after != before:
            return "fixed"
        return "fixed" if _labelish_count(patched) > _labelish_count(original) else "not_fixed"

    return "unverifiable"


# ═══════════════════════════════════════════════════════════════════════════════
# Estatística auxiliar
# ═══════════════════════════════════════════════════════════════════════════════

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confiança 95% de Wilson para proporção k/n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (max(0.0, centre - half), min(1.0, centre + half))


def is_noop(original: str, patched: str) -> bool:
    """Patch sem mudança real de código (idêntico módulo whitespace)."""
    norm = lambda s: "\n".join(line.rstrip() for line in s.strip().splitlines())
    return norm(original) == norm(patched)


# ═══════════════════════════════════════════════════════════════════════════════
# Revalidação de um experiment_result.json
# ═══════════════════════════════════════════════════════════════════════════════

def revalidate_experiment(path: Path) -> dict[str, Any]:
    """Revalida todos os modelos de um experiment_result.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    out: dict[str, Any] = {
        "source": str(path),
        "experiment_name": data.get("experiment_name"),
        "experiment_id": data.get("experiment_id"),
        "models": {},
    }

    for model, results in data.get("results_by_model", {}).items():
        agg = _revalidate_model(results)
        out["models"][model] = agg

    return out


def _revalidate_model(results: list[dict[str, Any]]) -> dict[str, Any]:
    # Contadores de corpus
    n_files_total = len(results)
    n_files_with_issues = 0
    n_files_with_real_issues = 0
    n_issues_total = 0
    n_harness = 0

    # Contadores de patches/validação
    patches_attempted = 0          # tentativas bem-sucedidas (critério antigo)
    l1_rejected = 0
    l2_rejected = 0
    l2_rejected_by_check: Counter = Counter()
    l4_rejected = 0
    diff_unrecoverable = 0
    noop_patches = 0
    patches_validated = 0          # passaram L1+L2+L4 e não são noop

    # Vereditos por issue (somente issues reais, não-harness)
    verdicts: Counter = Counter()
    per_criterion: dict[str, Counter] = defaultdict(Counter)
    per_type: dict[str, Counter] = defaultdict(Counter)

    # SR corrigido por arquivo
    files_full_fixed = 0
    files_partial_fixed = 0

    # Comparação com o critério antigo
    claimed_fixed_real = 0  # issues reais que o sistema ANTIGO contou como corrigidas

    for r in results:
        issues = r.get("scan_result", {}).get("issues", [])
        if not issues:
            continue
        n_files_with_issues += 1
        n_issues_total += len(issues)

        real_issues = []
        for iss in issues:
            if is_harness_artifact(iss):
                n_harness += 1
            else:
                real_issues.append(iss)

        if not real_issues:
            continue
        n_files_with_real_issues += 1

        # Critério antigo: binário por arquivo
        old_fixed = r.get("issues_fixed", 0) > 0
        if old_fixed:
            claimed_fixed_real += len(real_issues)

        # Encontrar a tentativa bem-sucedida (confirmado: ≤1 por arquivo)
        succ = [a for a in r.get("attempts", []) if a.get("success") and a.get("new_content")]
        if not succ:
            for iss in real_issues:
                verdicts["not_fixed"] += 1
                _bucket(per_criterion, per_type, iss, "not_fixed")
            continue

        attempt = succ[0]
        patched = attempt["new_content"]
        patches_attempted += 1

        original = reverse_apply_unified(attempt.get("diff", ""), patched)
        if original is None:
            diff_unrecoverable += 1
            # Sem original: L2 impossível; L1/L4 ainda valem
            original = ""

        # ── Validação em camadas ────────────────────────────────────────────
        ok1, _ = validate_layer1(patched)
        if not ok1:
            l1_rejected += 1
            for iss in real_issues:
                verdicts["not_fixed"] += 1
                _bucket(per_criterion, per_type, iss, "not_fixed")
            continue

        if original:
            l2 = run_layer2(original, patched)
            if not l2.passed:
                l2_rejected += 1
                l2_rejected_by_check[l2.failed_check or "unknown"] += 1
                for iss in real_issues:
                    verdicts["not_fixed"] += 1
                    _bucket(per_criterion, per_type, iss, "not_fixed")
                continue

        ok4, _ = validate_layer4(patched)
        if not ok4:
            l4_rejected += 1
            for iss in real_issues:
                verdicts["not_fixed"] += 1
                _bucket(per_criterion, per_type, iss, "not_fixed")
            continue

        # ── Noop ────────────────────────────────────────────────────────────
        if original and is_noop(original, patched):
            noop_patches += 1
            for iss in real_issues:
                verdicts["not_fixed"] += 1
                _bucket(per_criterion, per_type, iss, "not_fixed")
            continue

        patches_validated += 1

        # ── Camada 3 real: veredito por issue ───────────────────────────────
        file_verdicts = []
        for iss in real_issues:
            if original:
                v = _verify_issue(iss, original, patched)
            else:
                v = "unverifiable"
            verdicts[v] += 1
            file_verdicts.append(v)
            _bucket(per_criterion, per_type, iss, v)

        n_fixed_file = sum(1 for v in file_verdicts if v == "fixed")
        if n_fixed_file == len(file_verdicts):
            files_full_fixed += 1
        if n_fixed_file >= 1:
            files_partial_fixed += 1

    # ── Métricas finais ─────────────────────────────────────────────────────
    fixed = verdicts["fixed"]
    not_fixed = verdicts["not_fixed"]
    unver = verdicts["unverifiable"]
    n_real = fixed + not_fixed + unver

    ifr_strict = fixed / n_real if n_real else 0.0
    ifr_lenient = fixed / (fixed + not_fixed) if (fixed + not_fixed) else 0.0
    claimed_ifr_real = claimed_fixed_real / n_real if n_real else 0.0

    rho = l2_rejected / patches_attempted if patches_attempted else 0.0
    rho_ci = wilson_ci(l2_rejected, patches_attempted)

    return {
        "corpus": {
            "files_total": n_files_total,
            "files_with_issues": n_files_with_issues,
            "files_with_real_issues": n_files_with_real_issues,
            "issues_total": n_issues_total,
            "harness_artifacts": n_harness,
            "real_issues": n_real,
            "harness_pct": round(n_harness / n_issues_total * 100, 1) if n_issues_total else 0,
        },
        "patches": {
            "attempted": patches_attempted,
            "l1_rejected": l1_rejected,
            "l2_rejected": l2_rejected,
            "l2_by_check": dict(l2_rejected_by_check),
            "l4_rejected": l4_rejected,
            "noop": noop_patches,
            "validated": patches_validated,
            "diff_unrecoverable": diff_unrecoverable,
        },
        "verdicts": dict(verdicts),
        "metrics": {
            "ifr_strict": round(ifr_strict, 4),
            "ifr_lenient": round(ifr_lenient, 4),
            "claimed_ifr_on_real_corpus": round(claimed_ifr_real, 4),
            "rho": round(rho, 4),
            "rho_ci95": [round(rho_ci[0], 4), round(rho_ci[1], 4)],
            "sr_full": round(files_full_fixed / n_files_with_real_issues, 4) if n_files_with_real_issues else 0.0,
            "sr_partial": round(files_partial_fixed / n_files_with_real_issues, 4) if n_files_with_real_issues else 0.0,
            "noop_rate": round(noop_patches / patches_attempted, 4) if patches_attempted else 0.0,
        },
        "per_criterion": {
            crit: {
                "total": sum(c.values()),
                "fixed": c["fixed"],
                "not_fixed": c["not_fixed"],
                "unverifiable": c["unverifiable"],
                "rate_strict": round(c["fixed"] / sum(c.values()), 4) if sum(c.values()) else 0.0,
            }
            for crit, c in sorted(per_criterion.items())
        },
        "per_type": {
            t: {
                "total": sum(c.values()),
                "fixed": c["fixed"],
                "rate_strict": round(c["fixed"] / sum(c.values()), 4) if sum(c.values()) else 0.0,
            }
            for t, c in sorted(per_type.items())
        },
    }


def _bucket(
    per_criterion: dict[str, Counter],
    per_type: dict[str, Counter],
    issue: dict[str, Any],
    verdict: str,
) -> None:
    crit = issue.get("wcag_criteria") or "none"
    itype = issue.get("issue_type") or "other"
    per_criterion[crit][verdict] += 1
    per_type[itype][verdict] += 1


# ═══════════════════════════════════════════════════════════════════════════════
# Agregação multi-repetição e relatório
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_reps(rep_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Média ± desvio por modelo através das repetições."""
    import statistics as st

    models: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rep in rep_reports:
        for model, agg in rep["models"].items():
            m = agg["metrics"]
            for key in ("ifr_strict", "ifr_lenient", "rho", "sr_full", "sr_partial", "noop_rate",
                        "claimed_ifr_on_real_corpus"):
                models[model][key].append(m[key])

    def stats(vals: list[float]) -> dict[str, float]:
        return {
            "mean": round(st.mean(vals), 4),
            "std": round(st.stdev(vals), 4) if len(vals) > 1 else 0.0,
            "values": [round(v, 4) for v in vals],
        }

    return {
        model: {k: stats(v) for k, v in keys.items()}
        for model, keys in models.items()
    }


def write_markdown(report: dict[str, Any], out_path: Path) -> None:
    """Gera resumo legível em Markdown."""
    lines: list[str] = []
    w = lines.append
    w("# Revalidação Post-Hoc dos Resultados — a11y-autofix")
    w("")
    w(f"Gerado em: {report['generated_at']}")
    w("")
    w("## Contexto")
    w("")
    w("A auditoria do código identificou que a ValidationPipeline nunca foi")
    w("executada e que `issues_fixed` era atribuído sem verificação. Este")
    w("relatório recomputa as métricas aplicando as 4 camadas de validação")
    w("e verificação estática por issue sobre os patches preservados nos")
    w("artefatos experimentais.")
    w("")

    for exp in report["experiments"]:
        w(f"## {exp['experiment_name']}")
        w("")
        w(f"`{exp['source']}`")
        w("")
        for model, agg in exp["models"].items():
            c, p, m = agg["corpus"], agg["patches"], agg["metrics"]
            w(f"### {model}")
            w("")
            w(f"- **Corpus**: {c['issues_total']} issues, das quais "
              f"**{c['harness_artifacts']} ({c['harness_pct']}%) são artefatos do harness** "
              f"(#root/aria-prohibited-attr) → {c['real_issues']} issues reais "
              f"em {c['files_with_real_issues']} arquivos")
            w(f"- **Patches**: {p['attempted']} aceitos pelo critério antigo → "
              f"L1 rejeitou {p['l1_rejected']}, L2 rejeitou {p['l2_rejected']} "
              f"{dict(p['l2_by_check']) if p['l2_by_check'] else ''}, "
              f"L4 rejeitou {p['l4_rejected']}, noops {p['noop']} → "
              f"**{p['validated']} patches válidos**")
            w(f"- **IFR estrito**: **{m['ifr_strict']:.1%}** "
              f"(lenient {m['ifr_lenient']:.1%}) — "
              f"critério antigo no mesmo corpus: {m['claimed_ifr_on_real_corpus']:.1%}")
            w(f"- **ρ (regressão L2)**: **{m['rho']:.1%}** "
              f"IC95 [{m['rho_ci95'][0]:.1%}, {m['rho_ci95'][1]:.1%}] "
              f"({p['l2_rejected']}/{p['attempted']})")
            w(f"- **SR**: full {m['sr_full']:.1%} · partial {m['sr_partial']:.1%} · "
              f"noop_rate {m['noop_rate']:.1%}")
            w("")
            w("| Critério WCAG | Total | Fixed | Not fixed | Unverif. | Taxa estrita |")
            w("|---|---|---|---|---|---|")
            for crit, cc in agg["per_criterion"].items():
                w(f"| {crit} | {cc['total']} | {cc['fixed']} | {cc['not_fixed']} "
                  f"| {cc['unverifiable']} | {cc['rate_strict']:.1%} |")
            w("")

    if report.get("aggregate_by_experiment_group"):
        w("## Agregado multi-repetição (média ± desvio)")
        w("")
        for group, models in report["aggregate_by_experiment_group"].items():
            w(f"### {group}")
            w("")
            w("| Modelo | IFR estrito | IFR lenient | ρ | SR full | SR partial | noop |")
            w("|---|---|---|---|---|---|---|")
            for model, stats in models.items():
                w(f"| {model} "
                  f"| {stats['ifr_strict']['mean']:.1%} ± {stats['ifr_strict']['std']:.1%} "
                  f"| {stats['ifr_lenient']['mean']:.1%} ± {stats['ifr_lenient']['std']:.1%} "
                  f"| {stats['rho']['mean']:.1%} ± {stats['rho']['std']:.1%} "
                  f"| {stats['sr_full']['mean']:.1%} "
                  f"| {stats['sr_partial']['mean']:.1%} "
                  f"| {stats['noop_rate']['mean']:.1%} |")
            w("")

    w("## Implicações para as hipóteses")
    w("")
    w("- **H3 (IFR ≥ 0,70)**: avaliar contra `ifr_strict` no corpus real.")
    w("- **H5 (ρ < 0,05)**: agora medido de fato (rejeições L2 / patches);")
    w("  o valor anterior era estruturalmente zero por defeito de integração.")
    w("- As taxas por critério WCAG anteriores eram artefato de rateio")
    w("  proporcional sobre resultado binário por arquivo; as taxas acima são")
    w("  medidas diretamente por issue.")
    w("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", type=Path,
                    default=_REPO / "experiment-results")
    ap.add_argument("--output", type=Path,
                    default=_REPO / "experiment-results" / "revalidation")
    args = ap.parse_args()

    result_files = sorted(args.results_dir.rglob("experiment_result.json"))
    # Ignorar saídas anteriores da própria revalidação
    result_files = [p for p in result_files if "revalidation" not in p.parts]
    # Deduplicar: o experiment_result.json da raiz do experimento é cópia da
    # última repetição (runner.py grava o resultado da última rep na raiz).
    # Quando existem subdiretórios rep_*, usar apenas os das repetições.
    deduped: list[Path] = []
    for p in result_files:
        if not any(part.startswith("rep_") for part in p.parts):
            exp_dir = p.parent
            if any(exp_dir.glob("rep_*/experiment_result.json")):
                print(f"  (pulando duplicata da raiz: {p.relative_to(args.results_dir)})")
                continue
        deduped.append(p)
    result_files = deduped

    if not result_files:
        print(f"Nenhum experiment_result.json encontrado em {args.results_dir}")
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "experiments": [],
    }

    # Agrupar por diretório de experimento (para agregação multi-rep)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in result_files:
        rel = path.relative_to(args.results_dir)
        print(f"→ Revalidando {rel} ...", flush=True)
        try:
            exp_report = revalidate_experiment(path)
        except Exception as exc:
            print(f"  ERRO: {exc}")
            continue
        exp_report["source"] = str(rel)
        report["experiments"].append(exp_report)
        group = rel.parts[0]
        groups[group].append(exp_report)
        for model, agg in exp_report["models"].items():
            m = agg["metrics"]
            print(f"   {model}: IFR_strict={m['ifr_strict']:.1%} "
                  f"ρ={m['rho']:.1%} SR_full={m['sr_full']:.1%} "
                  f"(harness={agg['corpus']['harness_pct']}% do corpus)")

    report["aggregate_by_experiment_group"] = {
        group: aggregate_reps(reps)
        for group, reps in groups.items()
        if len(reps) >= 1
    }

    json_path = args.output / "revalidation_report.json"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path = args.output / "revalidation_report.md"
    write_markdown(report, md_path)

    print(f"\nRelatórios salvos:\n  {json_path}\n  {md_path}")


if __name__ == "__main__":
    main()
