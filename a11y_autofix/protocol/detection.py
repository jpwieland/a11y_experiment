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
# Cobrindo critérios WCAG 2.1/2.2 relevantes para React/JSX estático.
#
# IMPORTANTE: sem chaves duplicadas — Python silenciosamente descarta a primeira
# ocorrência quando há duplicação, o que mascara bugs de mapeamento.
# Cada critério aparece UMA vez, na categoria mais específica.
WCAG_TO_ISSUE_TYPE: dict[str, IssueType] = {
    # ── Texto alternativo ───────────────────────────────────────────────────
    "1.1.1": IssueType.ALT_TEXT,     # Non-text Content

    # ── Multimídia ──────────────────────────────────────────────────────────
    "1.2.1": IssueType.OTHER,        # Audio-only and Video-only
    "1.2.2": IssueType.OTHER,        # Captions (Prerecorded)
    "1.2.3": IssueType.OTHER,        # Audio Description or Media Alternative
    "1.2.4": IssueType.OTHER,        # Captions (Live)
    "1.2.5": IssueType.OTHER,        # Audio Description (Prerecorded)

    # ── Semântica / Estrutura ───────────────────────────────────────────────
    "1.3.1": IssueType.SEMANTIC,     # Info and Relationships
    "1.3.2": IssueType.SEMANTIC,     # Meaningful Sequence
    "1.3.3": IssueType.SEMANTIC,     # Sensory Characteristics
    "1.3.4": IssueType.SEMANTIC,     # Orientation
    "1.3.5": IssueType.SEMANTIC,     # Identify Input Purpose
    "3.1.1": IssueType.SEMANTIC,     # Language of Page (html lang=)
    "3.1.2": IssueType.SEMANTIC,     # Language of Parts
    "3.2.1": IssueType.SEMANTIC,     # On Focus (predictable behavior)
    "3.2.2": IssueType.SEMANTIC,     # On Input
    "4.1.1": IssueType.SEMANTIC,     # Parsing

    # ── Contraste ───────────────────────────────────────────────────────────
    "1.4.1": IssueType.CONTRAST,     # Use of Color
    "1.4.3": IssueType.CONTRAST,     # Contrast (Minimum)
    "1.4.6": IssueType.CONTRAST,     # Contrast (Enhanced)
    "1.4.11": IssueType.CONTRAST,    # Non-text Contrast

    # ── Acessibilidade visual / Layout ─────────────────────────────────────
    "1.4.2": IssueType.OTHER,        # Audio Control
    "1.4.4": IssueType.OTHER,        # Resize Text
    "1.4.5": IssueType.OTHER,        # Images of Text
    "1.4.10": IssueType.OTHER,       # Reflow
    "1.4.12": IssueType.OTHER,       # Text Spacing
    "1.4.13": IssueType.OTHER,       # Content on Hover or Focus

    # ── Labels ──────────────────────────────────────────────────────────────
    "1.3.6": IssueType.LABEL,        # Identify Purpose
    "2.4.2": IssueType.LABEL,        # Page Titled
    "2.4.4": IssueType.LABEL,        # Link Purpose (In Context)
    "2.5.3": IssueType.LABEL,        # Label in Name

    # ── Teclado ─────────────────────────────────────────────────────────────
    "2.1.1": IssueType.KEYBOARD,     # Keyboard
    "2.1.2": IssueType.KEYBOARD,     # No Keyboard Trap
    "2.1.3": IssueType.KEYBOARD,     # Keyboard (No Exception)
    "2.1.4": IssueType.KEYBOARD,     # Character Key Shortcuts
    "2.4.1": IssueType.KEYBOARD,     # Bypass Blocks
    "2.4.3": IssueType.KEYBOARD,     # Focus Order (also affects FOCUS type)
    "2.4.12": IssueType.KEYBOARD,    # Focus Appearance (Enhanced)

    # ── Distrações / Timing ─────────────────────────────────────────────────
    "2.2.2": IssueType.OTHER,        # Pause, Stop, Hide (marquee, blink)

    # ── Foco ────────────────────────────────────────────────────────────────
    # Nota: 2.4.7 e 2.4.11 são sobre APARÊNCIA de foco (visual), não teclado
    "2.4.7": IssueType.FOCUS,        # Focus Visible
    "2.4.11": IssueType.FOCUS,       # Focus Appearance (Minimum)

    # ── Headings / Labels de navegação ─────────────────────────────────────
    "2.4.6": IssueType.SEMANTIC,     # Headings and Labels
    "2.4.10": IssueType.SEMANTIC,    # Section Headings

    # ── ARIA ────────────────────────────────────────────────────────────────
    "4.1.2": IssueType.ARIA,         # Name, Role, Value
    "4.1.3": IssueType.ARIA,         # Status Messages
}

# ─── Mapeamento rule_id → IssueType (fallback quando WCAG não disponível) ────
# Inclui IDs de regras dos três scanners principais:
#   - axe-core (sem prefixo)
#   - pa11y (formato WCAG2AA.Principle1.Guideline...)
#   - eslint-plugin-jsx-a11y (prefixo "jsx-a11y/")
#
# Nota: o mapping via WCAG_TO_ISSUE_TYPE tem prioridade quando wcag_criteria
# está disponível. Este dict é o fallback para quando wcag_criteria é None.
RULE_TO_ISSUE_TYPE: dict[str, IssueType] = {
    # ── axe-core rules ──────────────────────────────────────────────────────
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
    "empty-heading": IssueType.SEMANTIC,
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

    # ── eslint-plugin-jsx-a11y rules (prefixo "jsx-a11y/") ──────────────────
    # Usados como fallback; normalmente wcag_criteria já está preenchido
    # pelo EslintRunner via _RULE_META, então o mapping WCAG tem prioridade.
    "jsx-a11y/alt-text": IssueType.ALT_TEXT,
    "jsx-a11y/img-redundant-alt": IssueType.ALT_TEXT,
    "jsx-a11y/heading-has-content": IssueType.SEMANTIC,
    "jsx-a11y/html-has-lang": IssueType.SEMANTIC,
    "jsx-a11y/scope": IssueType.SEMANTIC,
    "jsx-a11y/anchor-has-content": IssueType.LABEL,
    "jsx-a11y/label-has-associated-control": IssueType.LABEL,
    "jsx-a11y/click-events-have-key-events": IssueType.KEYBOARD,
    "jsx-a11y/interactive-supports-focus": IssueType.KEYBOARD,
    "jsx-a11y/mouse-events-have-key-events": IssueType.KEYBOARD,
    "jsx-a11y/no-access-key": IssueType.KEYBOARD,
    "jsx-a11y/tabindex-no-positive": IssueType.FOCUS,
    "jsx-a11y/no-autofocus": IssueType.FOCUS,
    "jsx-a11y/no-distracting-elements": IssueType.OTHER,
    "jsx-a11y/aria-props": IssueType.ARIA,
    "jsx-a11y/aria-proptypes": IssueType.ARIA,
    "jsx-a11y/aria-role": IssueType.ARIA,
    "jsx-a11y/aria-unsupported-elements": IssueType.ARIA,
    "jsx-a11y/role-has-required-aria-props": IssueType.ARIA,
    "jsx-a11y/role-supports-aria-props": IssueType.ARIA,
    "jsx-a11y/anchor-is-valid": IssueType.ARIA,
}

# ─── Regras de NÍVEL DE PÁGINA — excluídas do dataset ────────────────────────
# Estas regras do axe-core (best-practice) verificam propriedades da PÁGINA como
# um todo, não de componentes individuais. Em harness de componente isolado elas
# geram falsos positivos sistemáticos (1 por arquivo), contaminando o dataset.
#
# A exclusão principal ocorre no scanner (playwright_axe.py), mas este set atua
# como rede de segurança: se alguma ferramenta emitir esses rule_ids, eles são
# descartados em DetectionProtocol._group_findings() antes de qualquer mapeamento.
PAGE_LEVEL_RULES_EXCLUDED: frozenset[str] = frozenset({
    # ── Regras que exigem contexto de PÁGINA COMPLETA ────────────────────────
    "page-has-heading-one",        # componentes não são páginas; sem <h1> de página
    "landmark-one-main",           # exige exatamente 1 <main>; harness não tem
    "skip-link",                   # mecanismo de navegação de página inteira
    "bypass",                      # idem
    "region",                      # exige que TODO conteúdo esteja em landmark
    "document-title",              # harness já define <title>

    # ── Artefatos do harness — landmark duplicado ────────────────────────────
    # O harness anterior tinha role="main" no #root. Quando o componente
    # renderizava um <main>, o axe detectava dois landmarks main.
    # Já corrigido em files.py (role="main" removido), mas mantido aqui
    # como rede de segurança para resultados já salvos.
    "landmark-no-duplicate-main",  # dois <main> = harness + componente
    "landmark-main-is-top-level",  # <main> aninhado dentro de outro main

    # ── Artefatos de cobertura — não são issues de acessibilidade ────────────
    "frame-tested",                # axe não conseguiu acessar iframe; não é violação
})

# ─── Mapeamento rule_id → WCAG criterion (fallback para regras sem wcag_criteria) ─
# Usado quando a ferramenta não fornece wcag_criteria (ex: regras best-practice do
# axe-core que não têm tag wcag* — page-has-heading-one, region, etc.).
# Prioridade: wcag_criteria da ferramenta > este dicionário > None.
#
# Nota: as regras listadas em PAGE_LEVEL_RULES_EXCLUDED são filtradas antes de
# chegar aqui, então os mapeamentos correspondentes nunca serão usados em produção.
# Mantidos para documentar a intenção e para uso em ferramentas de análise ad-hoc.
RULE_TO_WCAG_CRITERION: dict[str, str] = {
    # ── axe-core best-practice (sem tag wcag nativa) ─────────────────────────
    "page-has-heading-one":     "2.4.6",   # Headings and Labels (AA) — page-level, filtrado
    "region":                   "1.3.1",   # Info and Relationships (A)
    "landmark-one-main":        "1.3.6",   # Identify Purpose (AAA) / best-practice
    "heading-order":            "1.3.1",   # Info and Relationships (A)
    "skip-link":                "2.4.1",   # Bypass Blocks (A)
    "focus-order-semantics":    "2.4.3",   # Focus Order (A)
    "scrollable-region-focusable": "2.1.1", # Keyboard (A)
    "identical-links-same-purpose": "2.4.4", # Link Purpose (AA)
    # ── axe-core regras com WCAG mas que às vezes chegam sem critério ────────
    "empty-heading":            "1.3.1",   # Info and Relationships (A)
    "document-title":           "2.4.2",   # Page Titled (A)
    "html-has-lang":            "3.1.1",   # Language of Page (A)
    "html-lang-valid":          "3.1.1",   # Language of Page (A)
    "html-xml-lang-mismatch":   "3.1.1",   # Language of Page (A)
    "bypass":                   "2.4.1",   # Bypass Blocks (A)
    "duplicate-id":             "4.1.1",   # Parsing (A)
    "duplicate-id-active":      "4.1.1",   # Parsing (A)
    "duplicate-id-aria":        "4.1.1",   # Parsing (A)
    "list":                     "1.3.1",   # Info and Relationships (A)
    "listitem":                 "1.3.1",   # Info and Relationships (A)
    "definition-list":          "1.3.1",   # Info and Relationships (A)
    "dlitem":                   "1.3.1",   # Info and Relationships (A)
    "color-contrast":           "1.4.3",   # Contrast Minimum (AA)
    "color-contrast-enhanced":  "1.4.6",   # Contrast Enhanced (AAA)
    "image-alt":                "1.1.1",   # Non-text Content (A)
    "image-redundant-alt":      "1.1.1",   # Non-text Content (A) — texto alt redundante
    "input-image-alt":          "1.1.1",   # Non-text Content (A)
    "object-alt":               "1.1.1",   # Non-text Content (A)
    "button-name":              "4.1.2",   # Name, Role, Value (AA)
    "link-name":                "4.1.2",   # Name, Role, Value (AA)
    "label":                    "1.3.1",   # Info and Relationships (A)
    "frame-title":              "4.1.2",   # Name, Role, Value (AA)
    "meta-refresh":             "2.2.1",   # Timing Adjustable (A)
    "meta-viewport":            "1.4.4",   # Resize Text (AA)
    "tabindex":                 "2.1.1",   # Keyboard (A)
    # ── pa11y / WCAG2AA rule IDs ─────────────────────────────────────────────
    "WCAG2AA.Principle1.Guideline1_1.1_1_1.H37":    "1.1.1",
    "WCAG2AA.Principle1.Guideline1_3.1_3_1.H42.2":  "1.3.1",
    "WCAG2AA.Principle2.Guideline2_4.2_4_2.H25.1.NoTitleEl": "2.4.2",
    "WCAG2AA.Principle3.Guideline3_1.3_1_1.H57.2":  "3.1.1",
    "WCAG2AA.Principle4.Guideline4_1.4_1_2.H91.A.NoContent": "4.1.2",
}

# ─── Mapeamento WCAG → Complexity ───────────────────────────────────────────
WCAG_TO_COMPLEXITY: dict[str, Complexity] = {
    # ── SIMPLE: Adicionar/corrigir um atributo ──────────────────────────────
    "1.1.1": Complexity.SIMPLE,   # alt text — só adicionar alt=""
    "2.4.2": Complexity.SIMPLE,   # page title
    "3.1.1": Complexity.SIMPLE,   # lang attribute — só adicionar lang=""
    "4.1.2": Complexity.SIMPLE,   # name, role, value
    "4.1.1": Complexity.SIMPLE,   # parsing
    "4.1.3": Complexity.SIMPLE,   # status messages (aria-live)
    "2.2.2": Complexity.SIMPLE,   # pause/stop/hide — remover <marquee>/<blink>

    # ── MODERATE: Reestruturação parcial ───────────────────────────────────
    "1.3.1": Complexity.MODERATE,   # info and relationships
    "1.3.2": Complexity.MODERATE,   # meaningful sequence
    "2.1.1": Complexity.MODERATE,   # keyboard accessibility
    "2.4.1": Complexity.MODERATE,   # bypass blocks
    "2.4.3": Complexity.MODERATE,   # focus order
    "2.4.4": Complexity.MODERATE,   # link purpose
    "2.4.6": Complexity.MODERATE,   # headings and labels
    "2.4.7": Complexity.MODERATE,   # focus visible

    # ── COMPLEX: Redesign substancial ──────────────────────────────────────
    "1.4.3": Complexity.COMPLEX,    # contrast (minimum)
    "1.4.6": Complexity.COMPLEX,    # contrast (enhanced)
    "1.4.11": Complexity.COMPLEX,   # non-text contrast
    "1.4.10": Complexity.COMPLEX,   # reflow
    "1.4.12": Complexity.COMPLEX,   # text spacing
    "1.3.4": Complexity.COMPLEX,    # orientation
    "2.1.2": Complexity.COMPLEX,    # no keyboard trap
    "2.4.11": Complexity.COMPLEX,   # focus appearance (minimum)
    "2.4.12": Complexity.COMPLEX,   # focus appearance (enhanced)
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

        Filtra automaticamente regras de nível de página (PAGE_LEVEL_RULES_EXCLUDED)
        que geram falsos positivos sistemáticos em harness de componente isolado.
        """
        groups: dict[str, tuple[list[ToolFinding], list[ScanTool]]] = {}

        for tool, findings in findings_by_tool.items():
            for finding in findings:
                # Descartar regras de nível de página — falsos positivos em componente
                if finding.rule_id.lower() in PAGE_LEVEL_RULES_EXCLUDED:
                    log.debug(
                        "detection_page_level_rule_skipped",
                        rule_id=finding.rule_id,
                        file=str(file.name),
                    )
                    continue
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

        # Resolver wcag_criteria: usa o da ferramenta ou fallback pelo rule_id
        wcag_criteria = primary.wcag_criteria
        if not wcag_criteria and primary.rule_id:
            wcag_criteria = RULE_TO_WCAG_CRITERION.get(primary.rule_id.lower())

        # Construir issue
        issue = A11yIssue(
            file=str(file),
            selector=primary.selector,
            issue_type=issue_type,
            complexity=complexity,
            wcag_criteria=wcag_criteria,
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
