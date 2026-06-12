"""Testes do merge cross-tool de findings (deduplicação por elemento).

Cenários extraídos de dados reais: axe/playwright geram seletores curtos
('img'), pa11y gera caminhos completos ('#root > div > img'), eslint gera
coordenadas de fonte ('Arquivo.tsx:10:5'). Sem o merge, o mesmo problema
conta 2-3× e o consenso multi-ferramenta nunca acontece.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_autofix.config import ScanTool, Settings, ToolFinding
from a11y_autofix.protocol.detection import DetectionProtocol


def _finding(
    tool: ScanTool,
    selector: str,
    wcag: str | None = None,
    rule: str = "image-alt",
    context: str = "",
    impact: str = "serious",
) -> ToolFinding:
    return ToolFinding(
        tool=tool,
        rule_id=rule,
        wcag_criteria=wcag,
        message="msg",
        selector=selector,
        context=context,
        impact=impact,
    )


@pytest.fixture
def protocol() -> DetectionProtocol:
    return DetectionProtocol(Settings())


def _run(protocol: DetectionProtocol, findings_by_tool: dict) -> list:
    result = protocol.run(
        file=Path("Component.tsx"),
        file_content="<div/>",
        findings_by_tool=findings_by_tool,
        tools_used=list(findings_by_tool.keys()),
        tool_versions={},
    )
    return result.issues


class TestCrossToolMerge:
    def test_suffix_selectors_merge_into_one_issue(self, protocol):
        """axe 'img' + pa11y '#root > div > img' = MESMO elemento."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [_finding(ScanTool.PLAYWRIGHT, "img", wcag="1.1.1")],
            ScanTool.PA11Y: [_finding(ScanTool.PA11Y, "#root > div > img", wcag="1.1.1")],
        })
        assert len(issues) == 1
        assert issues[0].tool_consensus == 2
        assert set(issues[0].found_by) == {ScanTool.PLAYWRIGHT, ScanTool.PA11Y}

    def test_consensus_two_tools_yields_high_confidence(self, protocol):
        """Merge cross-tool deve produzir confiança HIGH (consenso ≥2)."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [_finding(ScanTool.PLAYWRIGHT, "img", wcag="1.1.1")],
            ScanTool.PA11Y: [_finding(ScanTool.PA11Y, "#root > div > img", wcag="1.1.1")],
        })
        assert issues[0].confidence.value == "high"

    def test_distinct_elements_do_not_merge(self, protocol):
        """3 links diferentes (nth-child) NÃO podem fundir."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(2) > a", wcag="2.4.4", rule="link-name"),
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(3) > a", wcag="2.4.4", rule="link-name"),
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(4) > a", wcag="2.4.4", rule="link-name"),
            ],
        })
        assert len(issues) == 3

    def test_ambiguous_suffix_does_not_merge(self, protocol):
        """Sufixo 'a' com DOIS candidatos profundos = ambíguo, não funde."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(2) > a", wcag="2.4.4", rule="link-name"),
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(3) > a", wcag="2.4.4", rule="link-name"),
            ],
            ScanTool.PA11Y: [
                _finding(ScanTool.PA11Y, "a", wcag="2.4.4", rule="other-rule"),
            ],
        })
        # 'a' é sufixo de ambos → ambíguo → mantém separado (3 issues)
        assert len(issues) == 3

    def test_ambiguous_suffix_resolved_by_context(self, protocol):
        """Ambiguidade de sufixo resolvida quando contexto HTML casa com um único candidato."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(2) > a", wcag="2.4.4",
                         context='<a href="/sobre"></a>'),
                _finding(ScanTool.PLAYWRIGHT, "li:nth-child(3) > a", wcag="2.4.4",
                         context='<a href="/contato"></a>'),
            ],
            ScanTool.PA11Y: [
                _finding(ScanTool.PA11Y, "a", wcag="2.4.4",
                         context='<a href="/contato"></a>'),
            ],
        })
        assert len(issues) == 2
        merged = [i for i in issues if i.tool_consensus == 2]
        assert len(merged) == 1

    def test_context_merge_without_suffix_relation(self, protocol):
        """Profundidades divergentes sem sufixo: contexto idêntico + mesma tag funde."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "div > img", wcag="1.1.1",
                         context='<img src="logo.png" width="300">'),
            ],
            ScanTool.PA11Y: [
                _finding(ScanTool.PA11Y, "section > figure > img", wcag="1.1.1",
                         context='<img  src="logo.png"   width="300">'),
            ],
        })
        # Whitespace do contexto é normalizado antes da comparação
        assert len(issues) == 1
        assert issues[0].tool_consensus == 2

    def test_wcag_resolved_before_dedup(self, protocol):
        """axe só com rule_id + pa11y com wcag explícito devem casar."""
        issues = _run(protocol, {
            ScanTool.AXE: [
                _finding(ScanTool.AXE, "img", wcag=None, rule="image-alt"),
            ],
            ScanTool.PA11Y: [
                _finding(ScanTool.PA11Y, "#root > img", wcag="1.1.1", rule="H37"),
            ],
        })
        assert len(issues) == 1
        assert issues[0].wcag_criteria == "1.1.1"

    def test_source_coordinates_never_merge_with_dom(self, protocol):
        """eslint (Arquivo.tsx:LL:CC) nunca funde com seletores DOM."""
        issues = _run(protocol, {
            ScanTool.ESLINT: [
                _finding(ScanTool.ESLINT, "Component.tsx:10:5", wcag="1.1.1",
                         rule="jsx-a11y/alt-text"),
            ],
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "img", wcag="1.1.1"),
            ],
        })
        assert len(issues) == 2

    def test_different_criteria_never_merge(self, protocol):
        """Mesmo elemento, critérios diferentes = issues distintos."""
        issues = _run(protocol, {
            ScanTool.PLAYWRIGHT: [
                _finding(ScanTool.PLAYWRIGHT, "img", wcag="1.1.1"),
                _finding(ScanTool.PLAYWRIGHT, "img", wcag="4.1.2", rule="aria-hidden-focus"),
            ],
        })
        assert len(issues) == 2


class TestSelectorSanitization:
    def test_corrupted_pa11y_selector_truncated(self, protocol):
        """Seletores >240 chars (pa11y em bundles) são truncados para 3 segmentos."""
        junk = " > ".join(f"seg{i}':" for i in range(80)) + " > div:nth-child(53) > input"
        assert len(junk) > 240
        issues = _run(protocol, {
            ScanTool.PA11Y: [_finding(ScanTool.PA11Y, junk, wcag="4.1.2", rule="H91")],
        })
        assert len(issues) == 1
        assert len(issues[0].selector) <= 240
        assert issues[0].selector.endswith("input")

    def test_normal_selector_untouched(self, protocol):
        sel = "#root > div.container > form > input[type='text']"
        issues = _run(protocol, {
            ScanTool.PA11Y: [_finding(ScanTool.PA11Y, sel, wcag="1.3.1", rule="label")],
        })
        assert issues[0].selector == sel
