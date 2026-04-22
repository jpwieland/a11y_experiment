"""
Avaliador de fixtures sintéticos — calcula métricas sem depender de scanners.

Diferente da avaliação principal (que usa re-scan como árbitro),
a avaliação de fixtures usa:
  1. Comparação com correct.tsx (similaridade de código)
  2. Verificação léxica/AST de violações específicas
  3. Scanner recall/precision calculado contra ground truth conhecido

Metodologia: C2.1 do PLANO_CORRECAO_METODOLOGICA.md

Métricas calculadas:
  - fix_rate_real: violações corrigidas / total (sem scanner como árbitro)
  - similarity_to_correct: SequenceMatcher ratio com versão correta
  - scanner_recall: violações injetadas detectadas / total injetadas
  - scanner_precision: violações detectadas que são injetadas / total detectadas
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@dataclass
class ViolationCheckResult:
    """Resultado da verificação de uma violação específica."""
    violation_id: str
    wcag: str
    violation_type: str
    was_fixed: bool
    confidence: str  # "high", "medium", "low"
    evidence: str    # O que levou à conclusão


@dataclass
class FixtureEvaluationResult:
    """Resultado da avaliação de um fixture."""
    fixture_id: str
    component: str
    n_violations_injected: int
    n_violations_fixed: int
    fix_rate: float
    similarity_to_correct: float
    per_violation: list[ViolationCheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "component": self.component,
            "n_violations_injected": self.n_violations_injected,
            "n_violations_fixed": self.n_violations_fixed,
            "fix_rate": round(self.fix_rate, 4),
            "similarity_to_correct": round(self.similarity_to_correct, 4),
            "per_violation": [
                {
                    "id": v.violation_id,
                    "wcag": v.wcag,
                    "type": v.violation_type,
                    "fixed": v.was_fixed,
                    "confidence": v.confidence,
                    "evidence": v.evidence,
                }
                for v in self.per_violation
            ],
            "notes": self.notes,
        }


class FixtureEvaluator:
    """
    Avalia patches gerados por LLM contra fixtures com ground truth exato.

    Não usa ferramentas de scan — verifica o código diretamente via
    análise léxica e comparação com a versão correta.
    """

    @staticmethod
    def _strip_comments(code: str) -> str:
        """Remove single-line (//...) and block (/* ... */) comments from code.

        This prevents comment mentions of old/new values from producing false
        positives or negatives in lexical checks.
        """
        # Remove block comments first (/* ... */)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        # Remove single-line comments (// ...)
        code = re.sub(r'//[^\n]*', '', code)
        return code

    def evaluate(
        self,
        fixture_dir: Path,
        patched_content: str,
    ) -> FixtureEvaluationResult:
        """
        Avalia se o patch corrigiu as violações injetadas.

        Args:
            fixture_dir: Diretório da fixture (violated.tsx, correct.tsx, metadata.yaml)
            patched_content: Código gerado pelo LLM (ou agente qualquer)

        Returns:
            FixtureEvaluationResult com métricas reais
        """
        if not _HAS_YAML:
            raise ImportError("PyYAML is required: pip install pyyaml")

        metadata_path = fixture_dir / "metadata.yaml"
        correct_path = fixture_dir / "correct.tsx"

        if not metadata_path.exists():
            raise FileNotFoundError(f"metadata.yaml not found in {fixture_dir}")
        if not correct_path.exists():
            raise FileNotFoundError(f"correct.tsx not found in {fixture_dir}")

        with open(metadata_path, encoding="utf-8") as f:
            metadata = yaml.safe_load(f)

        correct_content = correct_path.read_text(encoding="utf-8")

        # Similaridade com versão correta
        similarity = SequenceMatcher(
            None, patched_content, correct_content
        ).ratio()

        # Verificar cada violação
        per_violation: list[ViolationCheckResult] = []
        for v in metadata.get("violations", []):
            check = self._check_violation(v, patched_content)
            per_violation.append(check)

        n_violations = len(per_violation)
        n_fixed = sum(1 for v in per_violation if v.was_fixed)
        fix_rate = n_fixed / n_violations if n_violations > 0 else 0.0

        notes: list[str] = []
        if similarity > 0.95:
            notes.append("Patch very similar to gold standard (>95%)")
        elif similarity < 0.5:
            notes.append("Patch differs significantly from gold standard (<50%)")

        return FixtureEvaluationResult(
            fixture_id=metadata.get("fixture_id", fixture_dir.name),
            component=metadata.get("component", "Unknown"),
            n_violations_injected=n_violations,
            n_violations_fixed=n_fixed,
            fix_rate=fix_rate,
            similarity_to_correct=similarity,
            per_violation=per_violation,
            notes=notes,
        )

    def _check_violation(
        self,
        violation: dict[str, Any],
        code: str,
    ) -> ViolationCheckResult:
        """
        Verifica se uma violação específica foi corrigida.

        Usa verificação léxica simples — não requer parser AST completo.
        """
        v_id = violation.get("id", "?")
        v_wcag = violation.get("wcag", "")
        v_type = violation.get("type", "")

        was_fixed, confidence, evidence = self._dispatch_check(v_type, violation, code)

        return ViolationCheckResult(
            violation_id=v_id,
            wcag=v_wcag,
            violation_type=v_type,
            was_fixed=was_fixed,
            confidence=confidence,
            evidence=evidence,
        )

    def _dispatch_check(
        self,
        v_type: str,
        violation: dict[str, Any],
        code: str,
    ) -> tuple[bool, str, str]:
        """Despacha para o verificador específico por tipo.

        Always strips comments before dispatching so that comment mentions of
        old/new values don't pollute lexical checks.
        """
        # Strip all comments once — every checker receives comment-free code
        clean = self._strip_comments(code)

        if v_type == "alt-text":
            return self._check_alt_text(clean)
        elif v_type == "aria":
            return self._check_aria(violation, clean)
        elif v_type == "label":
            return self._check_label(violation, clean)
        elif v_type == "keyboard":
            return self._check_keyboard(violation, clean)
        elif v_type == "focus":
            return self._check_focus(clean)
        elif v_type == "semantic":
            return self._check_semantic(violation, clean)
        else:
            return False, "low", f"No checker for type '{v_type}'"

    def _check_alt_text(self, code: str) -> tuple[bool, str, str]:
        """Verifica se todas as imgs têm atributo alt."""
        # img sem alt (sem considerar alt="")
        imgs_without_alt = re.findall(
            r'<img\b(?![^>]*\balt\s*=)[^>]*/?>',
            code,
            re.IGNORECASE | re.DOTALL,
        )
        if not imgs_without_alt:
            return True, "high", "No img elements found without alt attribute"
        return False, "high", f"Found {len(imgs_without_alt)} img(s) without alt"

    def _check_aria(
        self, violation: dict[str, Any], code: str
    ) -> tuple[bool, str, str]:
        """Verifica correções ARIA."""
        description = violation.get("description", "").lower()
        invalid_value = violation.get("invalid_value", "")
        attribute = violation.get("attribute", "")

        # aria-expanded missing (accordion pattern)
        if "aria-expanded" in attribute or "aria-expanded" in description:
            has_aria_expanded = bool(re.search(r'\baria-expanded\s*=', code))
            return (
                has_aria_expanded,
                "high",
                "aria-expanded attribute found" if has_aria_expanded
                else "aria-expanded attribute missing",
            )

        # aria-hidden missing on content panel
        if "aria-hidden" in attribute or ("aria-hidden" in description and "panel" in description):
            has_aria_hidden = bool(re.search(r'\baria-hidden\s*=', code))
            return (
                has_aria_hidden,
                "high",
                "aria-hidden attribute found" if has_aria_hidden
                else "aria-hidden attribute missing",
            )

        # Invalid ARIA role — check that the invalid role value is no longer present
        if invalid_value and ("role" in attribute or "invalid" in description):
            bad_role_pattern = rf"role=['\"]{{0,1}}{re.escape(invalid_value)}['\"]{{0,1}}"
            has_bad_role = bool(re.search(bad_role_pattern, code))
            if not has_bad_role:
                return True, "high", f"Invalid role='{invalid_value}' removed"
            return False, "high", f"Still has role='{invalid_value}'"

        if "accessible name" in description or "icon-only" in description:
            # Button/elemento sem nome acessível
            has_aria_label = bool(re.search(r'\baria-label\s*=', code))
            has_aria_labelledby = bool(re.search(r'\baria-labelledby\s*=', code))
            has_title = bool(re.search(r'\btitle\s*=', code))
            fixed = has_aria_label or has_aria_labelledby or has_title
            evidence = (
                "aria-label/labelledby/title found" if fixed
                else "No accessible name attribute found"
            )
            return fixed, "high", evidence

        if "role=" in description or "invalid" in description:
            # Role ARIA inválido — verificação genérica
            # Verificar se role='article' foi removido de contexto não-article
            has_bad_role = bool(re.search(r"role=['\"]article['\"]", code))
            if not has_bad_role:
                return True, "medium", "Invalid role='article' removed or changed"
            return False, "medium", "Still has role='article'"

        return False, "low", "No specific ARIA check matched"

    def _check_label(
        self, violation: dict[str, Any], code: str
    ) -> tuple[bool, str, str]:
        """Verifica se inputs têm labels associados ou links têm texto acessível."""
        description = violation.get("description", "").lower()
        wcag = violation.get("wcag", "")

        # Verificar presença de label ou aria-label no contexto
        has_label = bool(re.search(r'<label\b', code, re.IGNORECASE))
        has_aria_label = bool(re.search(r'\baria-label\s*=', code))
        has_aria_labelledby = bool(re.search(r'\baria-labelledby\s*=', code))

        if "2.4.4" in wcag or "link" in description:
            # Link sem texto acessível — verificar se aria-label foi adicionado.
            # Keywords indicating icon-only / empty / whitespace links — all need aria-label
            icon_link_keywords = (
                "icon", "svg", "empty", "whitespace",
                "ícone", "vazia", "branco", "espaço",
            )
            is_icon_link = any(kw in description for kw in icon_link_keywords)

            if is_icon_link:
                # Icon-only or empty links need aria-label on the <a> element
                links_with_aria = re.findall(
                    r'<a\b[^>]*\baria-label\s*=[^>]*>',
                    code,
                    re.IGNORECASE | re.DOTALL,
                )
                fixed = len(links_with_aria) > 0
                return (
                    fixed,
                    "high",
                    f"Found {len(links_with_aria)} link(s) with aria-label" if fixed
                    else "No links with aria-label found",
                )
            # Generic link text ("aqui", "here", etc.)
            generic_links = re.findall(
                r'<a\b[^>]*>\s*(?:aqui|here|click|clique|more|mais)\s*</a>',
                code,
                re.IGNORECASE,
            )
            fixed = len(generic_links) == 0
            return (
                fixed,
                "medium",
                "Generic link text removed" if fixed else f"Found {len(generic_links)} generic link(s)",
            )

        fixed = has_label or has_aria_label or has_aria_labelledby
        evidence = []
        if has_label:
            evidence.append("<label> present")
        if has_aria_label:
            evidence.append("aria-label present")
        if has_aria_labelledby:
            evidence.append("aria-labelledby present")

        return (
            fixed,
            "high" if fixed else "medium",
            " + ".join(evidence) if evidence else "No label mechanism found",
        )

    def _check_keyboard(
        self, violation: dict[str, Any], code: str
    ) -> tuple[bool, str, str]:
        """Verifica acessibilidade por teclado."""
        description = violation.get("description", "").lower()
        attribute = violation.get("attribute", "")
        invalid_value = violation.get("invalid_value", "")

        # Positive tabIndex — check it was removed
        if "tabindex" in attribute.lower() or "tabindex positivo" in description or "positive" in description:
            if invalid_value:
                # Check this specific positive value is gone
                # tabIndex={N} where N > 0
                pattern = rf'\btabIndex\s*=\s*\{{{re.escape(invalid_value)}\}}'
                still_has = bool(re.search(pattern, code))
                if not still_has:
                    return True, "high", f"tabIndex={{{invalid_value}}} removed"
                return False, "high", f"Still has tabIndex={{{invalid_value}}}"
            # Generic check: no positive tabIndex values remain
            positive_tabindex = re.findall(r'\btabIndex\s*=\s*\{([1-9]\d*)\}', code)
            fixed = len(positive_tabindex) == 0
            return (
                fixed,
                "high",
                "No positive tabIndex values found" if fixed
                else f"Positive tabIndex found: {positive_tabindex}",
            )

        if "span" in description or "replace" in description.lower():
            # span interativo deve virar button
            has_button = bool(re.search(r'<button\b', code, re.IGNORECASE))
            return (
                has_button,
                "high",
                "<button> element found" if has_button else "No <button> found",
            )

        # div/span clicável deve ter role+tabIndex+onKeyDown
        has_role_button = bool(re.search(r"role=['\"]button['\"]", code))
        has_tabindex = bool(re.search(r'\btabIndex\s*=', code))
        has_keydown = bool(re.search(r'\bonKeyDown\b', code))

        indicators = []
        if has_role_button:
            indicators.append("role='button'")
        if has_tabindex:
            indicators.append("tabIndex")
        if has_keydown:
            indicators.append("onKeyDown")

        # Consideramos fixado se tem pelo menos role+tabIndex ou é um <button>
        has_button_el = bool(re.search(r'<button\b', code))
        fixed = (has_role_button and has_tabindex) or has_button_el
        evidence = " + ".join(indicators) if indicators else "No keyboard support attributes found"
        return fixed, "medium", evidence

    def _check_focus(self, code: str) -> tuple[bool, str, str]:
        """Verifica que outline de foco não foi removido em style props.

        `code` is already comment-stripped by _dispatch_check.
        """
        has_outline_none = bool(
            re.search(
                r'\boutline\s*:\s*(?:["\']none["\']|0)\b',
                code,
                re.IGNORECASE,
            )
        )
        if has_outline_none:
            return False, "high", "outline: none/0 still present in style prop"
        return True, "high", "No outline:none found in style props — focus ring preserved"

    def _check_semantic(
        self, violation: dict[str, Any], code: str
    ) -> tuple[bool, str, str]:
        """Verifica correções semânticas."""
        wcag = violation.get("wcag", "")
        description = violation.get("description", "").lower()
        element = violation.get("element", "")

        if "3.1.1" in wcag or "lang" in description:
            # lang attribute on html element
            has_lang = bool(re.search(r'\blang\s*=', code))
            return (
                has_lang,
                "high",
                "lang attribute found" if has_lang else "No lang attribute found",
            )

        if "1.3.1" in wcag or "2.4.6" in wcag or "heading" in description:
            # Hierarquia de headings

            # Check: specific element that should be replaced
            if element and element.startswith("h"):
                # e.g. element="h4" should be h3, or element="h6" should be <p>
                # Verify the problematic element is gone
                bad_el = element.lower()
                still_has = bool(re.search(rf'<{re.escape(bad_el)}\b', code, re.IGNORECASE))
                if not still_has:
                    return True, "high", f"<{bad_el}> removed/replaced"
                return False, "high", f"<{bad_el}> still present in code"

            # Generic hierarchy check
            headings = re.findall(r'<(h[1-6])\b', code, re.IGNORECASE)
            levels = [int(h[1]) for h in headings]
            if not levels:
                return False, "low", "No headings found"
            has_skip = any(
                levels[i + 1] - levels[i] > 1
                for i in range(len(levels) - 1)
            )
            return (
                not has_skip,
                "medium",
                "Heading hierarchy appears correct" if not has_skip
                else "Heading levels skip found",
            )

        return False, "low", "No specific semantic check matched"


def evaluate_batch(
    fixtures_dir: Path,
    patches_by_fixture: dict[str, str],
) -> dict[str, FixtureEvaluationResult]:
    """
    Avalia múltiplos patches contra seus respectivos fixtures.

    Args:
        fixtures_dir: Diretório raiz dos fixtures (contém subpastas por fixture_id)
        patches_by_fixture: {fixture_id: patched_code}

    Returns:
        {fixture_id: FixtureEvaluationResult}
    """
    evaluator = FixtureEvaluator()
    results: dict[str, FixtureEvaluationResult] = {}

    for fixture_id, patched_code in patches_by_fixture.items():
        fixture_dir = fixtures_dir / "components" / fixture_id
        if not fixture_dir.exists():
            continue
        try:
            result = evaluator.evaluate(fixture_dir, patched_code)
            results[fixture_id] = result
        except Exception as exc:
            print(f"[WARN] Failed to evaluate fixture {fixture_id}: {exc}")

    return results


def compute_aggregate_metrics(
    results: dict[str, FixtureEvaluationResult],
) -> dict[str, Any]:
    """
    Agrega métricas de todos os fixtures.

    Returns:
        Dict com fix_rate_real, similarity_mean, per_violation_type metrics
    """
    if not results:
        return {}

    all_fix_rates = [r.fix_rate for r in results.values()]
    all_similarities = [r.similarity_to_correct for r in results.values()]

    # Agrega por tipo de violação
    by_type: dict[str, dict[str, int]] = {}
    for result in results.values():
        for v in result.per_violation:
            if v.violation_type not in by_type:
                by_type[v.violation_type] = {"total": 0, "fixed": 0}
            by_type[v.violation_type]["total"] += 1
            if v.was_fixed:
                by_type[v.violation_type]["fixed"] += 1

    return {
        "n_fixtures": len(results),
        "fix_rate_real_mean": round(sum(all_fix_rates) / len(all_fix_rates), 4),
        "fix_rate_real_min": round(min(all_fix_rates), 4),
        "fix_rate_real_max": round(max(all_fix_rates), 4),
        "similarity_mean": round(sum(all_similarities) / len(all_similarities), 4),
        "by_violation_type": {
            vtype: {
                "total": counts["total"],
                "fixed": counts["fixed"],
                "rate": round(counts["fixed"] / counts["total"], 4) if counts["total"] > 0 else 0.0,
            }
            for vtype, counts in by_type.items()
        },
    }
