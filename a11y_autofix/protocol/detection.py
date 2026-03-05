"""
Protocolo científico de detecção de issues de acessibilidade.

Implementa deduplicação cross-tool, cálculo de confiança baseado em consenso,
mapeamento WCAG→IssueType e ordenação determinística.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from a11y_autofix.config import (
    A11yIssue,
    Complexity,
    Confidence,
    IssueType,
    ScanResult,
    ScanTool,
    Settings,
    ToolFinding,
)
from a11y_autofix.utils.hashing import stable_issue_id

log = structlog.get_logger(__name__)

# ─── Mapeamento WCAG → IssueType ────────────────────────────────────────────
# Cobrindo >40 critérios WCAG 2.1/2.2
WCAG_TO_ISSUE_TYPE: dict[str, IssueType] = {
    # Contraste
    "1.4.3": IssueType.CONTRAST,
    "1.4.6": IssueType.CONTRAST,
    "1.4.11": IssueType.CONTRAST,

    # Texto alternativo
    "1.1.1": IssueType.ALT_TEXT,

    # Semântica / Estrutura
    "1.3.1": IssueType.SEMANTIC,
    "1.3.2": IssueType.SEMANTIC,
    "1.3.3": IssueType.SEMANTIC,
    "1.3.4": IssueType.SEMANTIC,
    "1.3.5": IssueType.SEMANTIC,
    "2.4.6": IssueType.SEMANTIC,
    "2.4.10": IssueType.SEMANTIC,
    "3.1.1": IssueType.SEMANTIC,
    "3.1.2": IssueType.SEMANTIC,
    "3.2.1": IssueType.SEMANTIC,
    "3.2.2": IssueType.SEMANTIC,
    "4.1.1": IssueType.SEMANTIC,

    # Labels
    "1.3.6": IssueType.LABEL,
    "2.4.2": IssueType.LABEL,
    "2.4.4": IssueType.LABEL,
    "2.5.3": IssueType.LABEL,

    # ARIA
    "4.1.2": IssueType.ARIA,
    "4.1.3": IssueType.ARIA,

    # Teclado
    "2.1.1": IssueType.KEYBOARD,
    "2.1.2": IssueType.KEYBOARD,
    "2.1.3": IssueType.KEYBOARD,
    "2.1.4": IssueType.KEYBOARD,
    "2.4.1": IssueType.KEYBOARD,
    "2.4.3": IssueType.KEYBOARD,
    "2.4.7": IssueType.KEYBOARD,
    "2.4.11": IssueType.KEYBOARD,
    "2.4.12": IssueType.KEYBOARD,

    # Foco
    "2.4.7": IssueType.FOCUS,
    "2.4.11": IssueType.FOCUS,
    "3.2.1": IssueType.FOCUS,

    # Multimídia
    "1.2.1": IssueType.OTHER,
    "1.2.2": IssueType.OTHER,
    "1.2.3": IssueType.OTHER,
    "1.2.4": IssueType.OTHER,
    "1.2.5": IssueType.OTHER,

    # Movimento / Acessibilidade visual
    "1.4.1": IssueType.CONTRAST,
    "1.4.2": IssueType.OTHER,
    "1.4.4": IssueType.OTHER,
    "1.4.5": IssueType.OTHER,
    "1.4.10": IssueType.OTHER,
    "1.4.12": IssueType.OTHER,
    "1.4.13": IssueType.OTHER,
}

# ─── Mapeamento rule_id → IssueType (fallback quando WCAG não disponível) ────
RULE_TO_ISSUE_TYPE: dict[str, IssueType] = {
    "color-contrast": IssueType.CONTRAST,
    "color-contrast-enhanced": IssueType.CONTRAST,
    "image-alt": IssueType.ALT_TEXT,
    "input-image-alt": IssueType.ALT_TEXT,
    "object-alt": IssueType.ALT_TEXT,
    "button-name": IssueType.LABEL,
    "link-name": IssueType.LABEL,
    "label": IssueType.LABEL,
    "label-content-name-mismatch": IssueType.LABEL,
    "aria-label": IssueType.ARIA,
    "aria-labelledby": IssueType.ARIA,
    "aria-required-attr": IssueType.ARIA,
    "aria-required-children": IssueType.ARIA,
    "aria-required-parent": IssueType.ARIA,
    "aria-roles": IssueType.ARIA,
    "aria-valid-attr": IssueType.ARIA,
    "aria-valid-attr-value": IssueType.ARIA,
    "aria-hidden-body": IssueType.ARIA,
    "aria-hidden-focus": IssueType.ARIA,
    "aria-input-field-name": IssueType.ARIA,
    "aria-toggle-field-name": IssueType.ARIA,
    "aria-command-name": IssueType.ARIA,
    "aria-meter-name": IssueType.ARIA,
    "aria-progressbar-name": IssueType.ARIA,
    "aria-tooltip-name": IssueType.ARIA,
    "aria-treeitem-name": IssueType.ARIA,
    "keyboard": IssueType.KEYBOARD,
    "tabindex": IssueType.KEYBOARD,
    "focus-order-semantics": IssueType.FOCUS,
    "focus-trap": IssueType.FOCUS,
    "scrollable-region-focusable": IssueType.FOCUS,
    "bypass": IssueType.KEYBOARD,
    "document-title": IssueType.SEMANTIC,
    "html-has-lang": IssueType.SEMANTIC,
    "html-lang-valid": IssueType.SEMANTIC,
    "html-xml-lang-mismatch": IssueType.SEMANTIC,
    "landmark-one-main": IssueType.SEMANTIC,
    "page-has-heading-one": IssueType.SEMANTIC,
    "region": IssueType.SEMANTIC,
    "skip-link": IssueType.KEYBOARD,
    "duplicate-id": IssueType.SEMANTIC,
    "duplicate-id-active": IssueType.SEMANTIC,
    "duplicate-id-aria": IssueType.ARIA,
    "list": IssueType.SEMANTIC,
    "listitem": IssueType.SEMANTIC,
    "definition-list": IssueType.SEMANTIC,
    "dlitem": IssueType.SEMANTIC,
    "frame-title": IssueType.LABEL,
    "frame-focusable-content": IssueType.KEYBOARD,
    "heading-order": IssueType.SEMANTIC,
    "identical-links-same-purpose": IssueType.LABEL,
    "meta-refresh": IssueType.OTHER,
    "meta-viewport": IssueType.OTHER,
    "audio-caption": IssueType.OTHER,
    "video-caption": IssueType.OTHER,
}

# ─── Mapeamento WCAG → Complexity ───────────────────────────────────────────
WCAG_TO_COMPLEXITY: dict[str, Complexity] = {
    # SIMPLE: Apenas adicionar atributo
    "1.1.1": Complexity.SIMPLE,   # alt text
    "2.4.2": Complexity.SIMPLE,   # page title
    "3.1.1": Complexity.SIMPLE,   # lang attribute
    "4.1.2": Complexity.SIMPLE,   # name, role, value
    "4.1.1": Complexity.SIMPLE,   # parsing

    # MODERATE: Reestruturação parcial
    "1.3.1": Complexity.MODERATE,   # info and relationships
    "1.3.2": Complexity.MODERATE,   # meaningful sequence
    "2.1.1": Complexity.MODERATE,   # keyboard
    "2.4.1": Complexity.MODERATE,   # bypass blocks
    "2.4.3": Complexity.MODERATE,   # focus order
    "2.4.4": Complexity.MODERATE,   # link purpose
    "2.4.6": Complexity.MODERATE,   # headings and labels
    "2.4.7": Complexity.MODERATE,   # focus visible
    "4.1.3": Complexity.MODERATE,   # status messages

    # COMPLEX: Redesign substancial
    "1.4.3": Complexity.COMPLEX,    # contrast
    "1.4.6": Complexity.COMPLEX,    # enhanced contrast
    "1.4.11": Complexity.COMPLEX,   # non-text contrast
    "1.4.10": Complexity.COMPLEX,   # reflow
    "1.4.12": Complexity.COMPLEX,   # text spacing
    "1.3.4": Complexity.COMPLEX,    # orientation
    "2.1.2": Complexity.COMPLEX,    # no keyboard trap
    "2.4.11": Complexity.COMPLEX,   # focus appearance minimum
    "2.4.12": Complexity.COMPLEX,   # focus appearance enhanced
}

# Mapeamento impact → prioridade numérica
_IMPACT_PRIORITY: dict[str, int] = {
    "critical": 4,
    "serious": 3,
    "moderate": 2,
    "minor": 1,
}

# Mapeamento confidence → prioridade numérica
_CONFIDENCE_PRIORITY: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
}


class DetectionProtocol:
    """
    Protocolo científico de detecção de issues.

    Responsabilidades:
    - Deduplicação cross-tool (mesmo elemento + mesmo critério = 1 issue)
    - Cálculo de confiança baseado em consenso multi-ferramenta
    - Mapeamento WCAG → IssueType e Complexity
    - Geração de IDs estáveis (content-addressed)
    - Ordenação determinística
    """

    def __init__(self, settings: Settings) -> None:
        """
        Args:
            settings: Configuração global, usada para min_tool_consensus.
        """
        self.settings = settings

    def run(
        self,
        file: Path,
        file_content: str,
        findings_by_tool: dict[ScanTool, list[ToolFinding]],
        tools_used: list[ScanTool],
        tool_versions: dict[str, str],
    ) -> ScanResult:
        """
        Executa o protocolo completo de detecção.

        Args:
            file: Arquivo analisado.
            file_content: Conteúdo do arquivo (para hash).
            findings_by_tool: Findings crus por ferramenta.
            tools_used: Lista de ferramentas executadas.
            tool_versions: Versões das ferramentas.

        Returns:
            ScanResult com issues deduplificados e metadados científicos.
        """
        import hashlib

        file_hash = "sha256:" + hashlib.sha256(file_content.encode()).hexdigest()

        # 1. Agrupa findings por chave de deduplicação
        grouped = self._group_findings(file, findings_by_tool)

        # 2. Converte grupos em A11yIssue
        issues: list[A11yIssue] = []
        for key, (tool_findings, tools) in grouped.items():
            issue = self._build_issue(file, key, tool_findings, tools)
            issues.append(issue)

        # 3. Ordenação determinística
        issues = self._sort_issues(issues)

        return ScanResult(
            file=file,
            file_hash=file_hash,
            issues=issues,
            tools_used=tools_used,
            tool_versions=tool_versions,
        )

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _group_findings(
        self,
        file: Path,
        findings_by_tool: dict[ScanTool, list[ToolFinding]],
    ) -> dict[str, tuple[list[ToolFinding], list[ScanTool]]]:
        """
        Agrupa findings de múltiplas ferramentas que apontam para o mesmo elemento.

        Chave de deduplicação: selector + wcag_criteria
        (normalizado para comparação case-insensitive).
        """
        groups: dict[str, tuple[list[ToolFinding], list[ScanTool]]] = {}

        for tool, findings in findings_by_tool.items():
            for finding in findings:
                key = self._dedup_key(finding)
                if key not in groups:
                    groups[key] = ([], [])
                groups[key][0].append(finding)
                if tool not in groups[key][1]:
                    groups[key][1].append(tool)

        return groups

    def _dedup_key(self, finding: ToolFinding) -> str:
        """
        Gera chave de deduplicação para um finding.

        Dois findings com mesma chave representam o mesmo problema.
        """
        selector = finding.selector.strip().lower()
        wcag = (finding.wcag_criteria or "").strip()
        rule = finding.rule_id.strip().lower()

        # Priorizar WCAG quando disponível; fallback para rule_id
        criteria = wcag if wcag else rule
        return f"{selector}|{criteria}"

    def _build_issue(
        self,
        file: Path,
        dedup_key: str,
        findings: list[ToolFinding],
        tools: list[ScanTool],
    ) -> A11yIssue:
        """
        Constrói um A11yIssue a partir de um grupo de findings.
        """
        # Usar o finding mais informativo como base
        primary = self._pick_primary(findings)

        # Classificar tipo e complexidade
        issue_type = self._classify_type(primary)
        complexity = self._classify_complexity(primary)

        # Calcular confiança
        confidence = self._compute_confidence(tools, primary.impact)

        # Construir issue
        issue = A11yIssue(
            file=str(file),
            selector=primary.selector,
            issue_type=issue_type,
            complexity=complexity,
            wcag_criteria=primary.wcag_criteria,
            impact=primary.impact,
            confidence=confidence,
            found_by=tools,
            tool_consensus=len(tools),
            findings=findings,
            message=primary.message,
            context=primary.context,
        )
        issue.compute_id()
        return issue

    def _pick_primary(self, findings: list[ToolFinding]) -> ToolFinding:
        """
        Escolhe o finding mais informativo como representante do grupo.

        Prioriza: wcag_criteria presente > impact alto > mais contexto.
        """
        impact_order = ["critical", "serious", "moderate", "minor"]

        def rank(f: ToolFinding) -> tuple[int, int, int]:
            has_wcag = 1 if f.wcag_criteria else 0
            impact_score = 4 - impact_order.index(f.impact) if f.impact in impact_order else 0
            context_len = len(f.context)
            return (has_wcag, impact_score, context_len)

        return max(findings, key=rank)

    def _classify_type(self, finding: ToolFinding) -> IssueType:
        """Classifica o tipo do issue usando WCAG ou rule_id como fallback."""
        if finding.wcag_criteria:
            if finding.wcag_criteria in WCAG_TO_ISSUE_TYPE:
                return WCAG_TO_ISSUE_TYPE[finding.wcag_criteria]

        if finding.rule_id:
            rule = finding.rule_id.lower()
            # Busca exata
            if rule in RULE_TO_ISSUE_TYPE:
                return RULE_TO_ISSUE_TYPE[rule]
            # Busca parcial
            for key, issue_type in RULE_TO_ISSUE_TYPE.items():
                if key in rule:
                    return issue_type

        return IssueType.OTHER

    def _classify_complexity(self, finding: ToolFinding) -> Complexity:
        """Classifica a complexidade de correção do issue."""
        if finding.wcag_criteria and finding.wcag_criteria in WCAG_TO_COMPLEXITY:
            return WCAG_TO_COMPLEXITY[finding.wcag_criteria]

        # Fallback: baseado no impact
        if finding.impact in ("critical", "serious"):
            return Complexity.MODERATE
        return Complexity.SIMPLE

    def _compute_confidence(self, tools: list[ScanTool], impact: str) -> Confidence:
        """
        Calcula confiança baseado em consenso de ferramentas.

        Regra:
        - ≥ min_tool_consensus ferramentas → HIGH
        - 1 ferramenta + impact critical/serious → MEDIUM
        - Demais → LOW
        """
        n_tools = len(tools)

        if n_tools >= self.settings.min_tool_consensus:
            return Confidence.HIGH

        if n_tools == 1 and impact in ("critical", "serious"):
            return Confidence.MEDIUM

        return Confidence.LOW

    def _sort_issues(self, issues: list[A11yIssue]) -> list[A11yIssue]:
        """
        Ordena issues de forma determinística para reprodutibilidade.

        Ordem: confidence DESC, impact DESC, wcag_criteria ASC
        """
        return sorted(
            issues,
            key=lambda i: (
                -_CONFIDENCE_PRIORITY.get(i.confidence.value, 1),
                -_IMPACT_PRIORITY.get(i.impact, 1),
                i.wcag_criteria or "9.9.9",
                i.selector,
            ),
        )
