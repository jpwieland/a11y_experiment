"""
Router de decisão: escolhe o agente correto baseado em scoring matrix.

O router analisa características dos issues e pontua cada opção,
garantindo que a estratégia mais adequada seja usada automaticamente.
"""

from __future__ import annotations

import structlog

from a11y_autofix.config import (
    AgentType,
    Complexity,
    IssueType,
    RouterDecision,
    ScanResult,
    Settings,
)

log = structlog.get_logger(__name__)

# ROUTING THRESHOLD — empirically calibrated on 50 representative files.
# Methodology: Section 3.4.3 (Scoring Matrix).
# Internal validity threat: if the benchmark distribution differs from
# the calibration set, this threshold may be suboptimal. See Section 3.8.2.
ROUTING_THRESHOLD: int = 3
SWE_MAX_ISSUES: int = 4  # τ in scoring matrix (Table 3.X)

# Tipos de issue que sempre requerem OpenHands (contexto amplo)
_FORCE_OPENHANDS: frozenset[IssueType] = frozenset({
    IssueType.CONTRAST,
    IssueType.SEMANTIC,
})

# Tipos de issue simples e localizados (preferem SWE-agent)
_FORCE_SWE: frozenset[IssueType] = frozenset({
    IssueType.ARIA,
    IssueType.LABEL,
    IssueType.ALT_TEXT,
})


class Router:
    """
    Decide qual agente usar baseado em características dos issues.

    Scoring:
      Score >= 3 → OpenHands (contexto amplo, múltiplos/complexos issues)
      Score <  3 → SWE-agent (cirúrgico, issues simples e localizados)

    O score é calculado somando/subtraindo pontos por fatores:
    - Tipos de issue complexos (contrast, semantic) → +4
    - Muitos issues (≥ swe_max_issues) → +4
    - Muitos issues (≥ 2x threshold) → +5 adicional
    - Issues complexos → +3
    - Tipos de issue diversos (≥3 tipos) → +3
    - Todos simples (aria/label/alt-text) → -3
    """

    def __init__(self, settings: Settings) -> None:
        """
        Args:
            settings: Configuração global, usada para thresholds.
        """
        self.settings = settings

    def decide(
        self,
        scan_result: ScanResult,
        agent_preference: AgentType = AgentType.AUTO,
    ) -> RouterDecision:
        """
        Decide qual agente usar para corrigir os issues do ScanResult.

        Args:
            scan_result: Resultado do scan com issues.
            agent_preference: Override manual do agente (default: AUTO).

        Returns:
            RouterDecision com agente escolhido, score e razão.
        """
        # Override manual tem precedência absoluta
        if agent_preference != AgentType.AUTO:
            return RouterDecision(
                agent=agent_preference.value,
                score=0,
                reason=f"manual override: {agent_preference.value}",
            )

        issues = scan_result.issues
        if not issues:
            return RouterDecision(
                agent="swe-agent",
                score=-10,
                reason="no issues to fix",
            )

        score = 0
        reasons: list[str] = []

        # +4: tipos que sempre precisam OpenHands (contraste, semântica)
        has_complex_types = any(i.issue_type in _FORCE_OPENHANDS for i in issues)
        if has_complex_types:
            score += 4
            complex_names = ", ".join(
                t.value for t in _FORCE_OPENHANDS
                if any(i.issue_type == t for i in issues)
            )
            reasons.append(f"complex types ({complex_names})")

        # +4: muitos issues (≥ swe_max_issues)
        threshold = self.settings.swe_max_issues
        if len(issues) >= threshold:
            score += 4
            reasons.append(f"{len(issues)} issues (threshold: {threshold})")

        # +5: MUITOS issues (≥ 2x threshold)
        if len(issues) >= threshold * 2:
            score += 5
            reasons.append("high volume")

        # +3: issues complexos
        n_complex = sum(1 for i in issues if i.complexity == Complexity.COMPLEX)
        if n_complex > 0:
            score += 3
            reasons.append(f"{n_complex} complex fixes needed")

        # +3: tipos de issue diversificados (≥3 tipos diferentes)
        unique_types = len({i.issue_type for i in issues})
        if unique_types >= 3:
            score += 3
            reasons.append(f"{unique_types} distinct issue types")

        # -3: todos simples (apenas aria/label/alt-text) E poucos
        all_simple = all(i.issue_type in _FORCE_SWE for i in issues)
        if all_simple and len(issues) < threshold:
            score -= 3
            reasons.append("all localized attribute fixes")

        # Decisão
        agent = "openhands" if score >= ROUTING_THRESHOLD else "swe-agent"
        reason = " + ".join(reasons) if reasons else (
            "wide context needed" if agent == "openhands" else "localized fixes"
        )

        decision = RouterDecision(agent=agent, score=score, reason=reason)

        log.info(
            "routing_decision",
            file_id=str(scan_result.file),
            score=score,
            threshold=ROUTING_THRESHOLD,
            agent_selected=agent,
            issue_count=len(issues),
            issue_types=list({i.issue_type.value for i in issues}),
        )

        return decision
