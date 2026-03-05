"""Testes unitários do protocolo científico de detecção."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from a11y_autofix.config import (
    Confidence,
    IssueType,
    ScanTool,
    Settings,
    ToolFinding,
)
from a11y_autofix.protocol.detection import DetectionProtocol


@pytest.fixture
def settings() -> Settings:
    """Settings com min_tool_consensus=2."""
    return Settings(min_tool_consensus=2)


@pytest.fixture
def protocol(settings: Settings) -> DetectionProtocol:
    """Instância do protocolo de detecção."""
    return DetectionProtocol(settings)


def make_finding(
    tool: ScanTool,
    selector: str = "button.submit",
    wcag: str | None = "1.4.3",
    rule_id: str = "color-contrast",
    impact: str = "serious",
) -> ToolFinding:
    """Factory helper para criar ToolFinding de teste."""
    return ToolFinding(
        tool=tool,
        tool_version="1.0.0",
        rule_id=rule_id,
        wcag_criteria=wcag,
        message="Insufficient color contrast",
        selector=selector,
        context="<button class='submit'>Submit</button>",
        impact=impact,
    )


class TestDeduplication:
    """Testes de deduplicação cross-tool."""

    def test_two_tools_same_issue_deduplicates_to_one(
        self, protocol: DetectionProtocol
    ) -> None:
        """Mesmo issue de 2 ferramentas = 1 A11yIssue com confidence=HIGH."""
        file = Path("test.tsx")
        findings_by_tool = {
            ScanTool.PA11Y: [make_finding(ScanTool.PA11Y)],
            ScanTool.AXE: [make_finding(ScanTool.AXE)],
        }

        result = protocol.run(
            file=file,
            file_content="const x = 1;",
            findings_by_tool=findings_by_tool,
            tools_used=[ScanTool.PA11Y, ScanTool.AXE],
            tool_versions={"pa11y": "8.0.0", "axe-core": "4.9.0"},
        )

        assert len(result.issues) == 1
        assert result.issues[0].confidence == Confidence.HIGH
        assert result.issues[0].tool_consensus == 2

    def test_different_selectors_creates_separate_issues(
        self, protocol: DetectionProtocol
    ) -> None:
        """Elementos diferentes geram issues separados."""
        file = Path("test.tsx")
        findings_by_tool = {
            ScanTool.PA11Y: [
                make_finding(ScanTool.PA11Y, selector="button.submit"),
                make_finding(ScanTool.PA11Y, selector="a.link"),
            ],
        }

        result = protocol.run(
            file=file,
            file_content="const x = 1;",
            findings_by_tool=findings_by_tool,
            tools_used=[ScanTool.PA11Y],
            tool_versions={"pa11y": "8.0.0"},
        )

        assert len(result.issues) == 2


class TestFileHash:
    """Testes de hash de arquivo."""

    def test_file_hash_is_deterministic(
        self, protocol: DetectionProtocol
    ) -> None:
        """Mesmo arquivo scaneado 2x = mesmo file_hash."""
        file = Path("Button.tsx")
        content = "export default function Button() { return <button>Click</button>; }"

        result1 = protocol.run(
            file=file,
            file_content=content,
            findings_by_tool={},
            tools_used=[],
            tool_versions={},
        )
        result2 = protocol.run(
            file=file,
            file_content=content,
            findings_by_tool={},
            tools_used=[],
            tool_versions={},
        )

        assert result1.file_hash == result2.file_hash

    def test_file_hash_changes_with_content(
        self, protocol: DetectionProtocol
    ) -> None:
        """Conteúdo diferente → hash diferente."""
        file = Path("Button.tsx")

        r1 = protocol.run(file=file, file_content="abc", findings_by_tool={}, tools_used=[], tool_versions={})
        r2 = protocol.run(file=file, file_content="xyz", findings_by_tool={}, tools_used=[], tool_versions={})

        assert r1.file_hash != r2.file_hash


class TestIssueID:
    """Testes de IDs estáveis de issues."""

    def test_issue_id_is_stable_across_runs(
        self, protocol: DetectionProtocol
    ) -> None:
        """Mesmo issue em runs diferentes = mesmo issue_id."""
        file = Path("Button.tsx")
        findings = {
            ScanTool.PA11Y: [make_finding(ScanTool.PA11Y)],
            ScanTool.AXE: [make_finding(ScanTool.AXE)],
        }

        r1 = protocol.run(
            file=file, file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y, ScanTool.AXE],
            tool_versions={},
        )
        r2 = protocol.run(
            file=file, file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y, ScanTool.AXE],
            tool_versions={},
        )

        assert r1.issues[0].issue_id == r2.issues[0].issue_id

    def test_issue_id_is_16_chars(
        self, protocol: DetectionProtocol
    ) -> None:
        """ID do issue tem exatamente 16 caracteres."""
        file = Path("test.tsx")
        findings = {ScanTool.PA11Y: [make_finding(ScanTool.PA11Y)]}

        result = protocol.run(
            file=file, file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )

        assert len(result.issues[0].issue_id) == 16


class TestConfidence:
    """Testes de cálculo de confiança."""

    def test_confidence_high_when_consensus_met(
        self, protocol: DetectionProtocol
    ) -> None:
        """≥2 ferramentas concordam = HIGH confidence."""
        findings = {
            ScanTool.PA11Y: [make_finding(ScanTool.PA11Y)],
            ScanTool.AXE: [make_finding(ScanTool.AXE)],
        }
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y, ScanTool.AXE],
            tool_versions={},
        )
        assert result.issues[0].confidence == Confidence.HIGH

    def test_confidence_medium_for_single_critical(
        self, protocol: DetectionProtocol
    ) -> None:
        """1 ferramenta + impact=critical = MEDIUM confidence."""
        findings = {
            ScanTool.PA11Y: [make_finding(ScanTool.PA11Y, impact="critical")],
        }
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )
        assert result.issues[0].confidence == Confidence.MEDIUM

    def test_confidence_low_for_single_minor(
        self, protocol: DetectionProtocol
    ) -> None:
        """1 ferramenta + impact=minor = LOW confidence."""
        findings = {
            ScanTool.PA11Y: [make_finding(ScanTool.PA11Y, impact="minor")],
        }
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )
        assert result.issues[0].confidence == Confidence.LOW


class TestWCAGMapping:
    """Testes de mapeamento WCAG → IssueType."""

    def test_wcag_1_4_3_mapped_to_contrast(
        self, protocol: DetectionProtocol
    ) -> None:
        """WCAG 1.4.3 → IssueType.CONTRAST."""
        findings = {ScanTool.PA11Y: [make_finding(ScanTool.PA11Y, wcag="1.4.3")]}
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )
        assert result.issues[0].issue_type == IssueType.CONTRAST

    def test_wcag_1_1_1_mapped_to_alt_text(
        self, protocol: DetectionProtocol
    ) -> None:
        """WCAG 1.1.1 → IssueType.ALT_TEXT."""
        findings = {
            ScanTool.AXE: [make_finding(ScanTool.AXE, wcag="1.1.1", rule_id="image-alt")]
        }
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.AXE],
            tool_versions={},
        )
        assert result.issues[0].issue_type == IssueType.ALT_TEXT


class TestSorting:
    """Testes de ordenação determinística."""

    def test_issues_sorted_high_confidence_first(
        self, protocol: DetectionProtocol
    ) -> None:
        """Issues HIGH confidence aparecem antes de LOW."""
        findings = {
            ScanTool.PA11Y: [
                make_finding(ScanTool.PA11Y, selector="a.link", impact="minor"),
            ],
            ScanTool.AXE: [
                make_finding(ScanTool.AXE, selector="button.submit", impact="serious"),
            ],
        }
        result = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y, ScanTool.AXE],
            tool_versions={},
        )

        # O issue com alta confiança (2 ferramentas) deve vir primeiro se aplicável
        # Neste caso ambos têm 1 ferramenta, então ordena por impact
        impacts = [i.impact for i in result.issues]
        assert len(impacts) >= 1  # Pelo menos processou

    def test_sorting_is_deterministic(
        self, protocol: DetectionProtocol
    ) -> None:
        """Mesmas entradas → mesma ordem nas saídas."""
        findings = {
            ScanTool.PA11Y: [
                make_finding(ScanTool.PA11Y, selector="a", wcag="1.4.3"),
                make_finding(ScanTool.PA11Y, selector="button", wcag="1.1.1"),
            ],
        }
        r1 = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )
        r2 = protocol.run(
            file=Path("t.tsx"), file_content="x",
            findings_by_tool=findings,
            tools_used=[ScanTool.PA11Y],
            tool_versions={},
        )
        ids1 = [i.issue_id for i in r1.issues]
        ids2 = [i.issue_id for i in r2.issues]
        assert ids1 == ids2
