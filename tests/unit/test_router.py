"""Testes unitários do router de decisão."""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_autofix.config import (
    A11yIssue,
    AgentType,
    Complexity,
    Confidence,
    IssueType,
    ScanResult,
    ScanTool,
    Settings,
)
from a11y_autofix.router.engine import Router


@pytest.fixture
def settings() -> Settings:
    return Settings(swe_max_issues=4)


@pytest.fixture
def router(settings: Settings) -> Router:
    return Router(settings)


def make_scan(issues: list[A11yIssue]) -> ScanResult:
    """Factory de ScanResult com issues."""
    return ScanResult(
        file=Path("test.tsx"),
        file_hash="sha256:abc",
        issues=issues,
        tools_used=[ScanTool.PA11Y],
        tool_versions={"pa11y": "8.0.0"},
    )


def make_issue(
    issue_type: IssueType,
    complexity: Complexity = Complexity.SIMPLE,
    impact: str = "moderate",
) -> A11yIssue:
    """Factory de A11yIssue."""
    issue = A11yIssue(
        file="test.tsx",
        selector="button",
        issue_type=issue_type,
        complexity=complexity,
        wcag_criteria="4.1.2",
        impact=impact,
        confidence=Confidence.HIGH,
        message="Test issue",
        tool_consensus=2,
        found_by=[ScanTool.PA11Y, ScanTool.AXE],
    )
    issue.compute_id()
    return issue


class TestRouterDecisions:
    """Testes de decisões do router."""

    def test_contrast_issue_routes_to_openhands(self, router: Router) -> None:
        """Issues de contraste sempre → OpenHands."""
        scan = make_scan([make_issue(IssueType.CONTRAST)])
        decision = router.decide(scan)
        assert decision.agent == "openhands"

    def test_semantic_issue_routes_to_openhands(self, router: Router) -> None:
        """Issues semânticos → OpenHands."""
        scan = make_scan([make_issue(IssueType.SEMANTIC)])
        decision = router.decide(scan)
        assert decision.agent == "openhands"

    def test_many_issues_routes_to_openhands(self, router: Router) -> None:
        """≥ swe_max_issues → OpenHands."""
        issues = [make_issue(IssueType.ARIA) for _ in range(5)]  # > threshold=4
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "openhands"

    def test_few_aria_issues_routes_to_swe(self, router: Router) -> None:
        """Poucos issues de aria/label → SWE-agent."""
        issues = [make_issue(IssueType.ARIA) for _ in range(2)]
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "swe-agent"

    def test_few_label_issues_routes_to_swe(self, router: Router) -> None:
        """Poucos issues de label → SWE-agent."""
        issues = [make_issue(IssueType.LABEL)]
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "swe-agent"

    def test_alt_text_routes_to_swe(self, router: Router) -> None:
        """Issues de alt-text → SWE-agent."""
        issues = [make_issue(IssueType.ALT_TEXT)]
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "swe-agent"

    def test_complex_issues_route_to_openhands(self, router: Router) -> None:
        """Issues com complexity=COMPLEX → OpenHands."""
        issues = [make_issue(IssueType.ARIA, complexity=Complexity.COMPLEX)]
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "openhands"

    def test_diverse_types_route_to_openhands(self, router: Router) -> None:
        """≥3 tipos diferentes → OpenHands."""
        issues = [
            make_issue(IssueType.ARIA),
            make_issue(IssueType.LABEL),
            make_issue(IssueType.KEYBOARD),
        ]
        scan = make_scan(issues)
        decision = router.decide(scan)
        assert decision.agent == "openhands"

    def test_manual_override_openhands(self, router: Router) -> None:
        """Override manual → usa o escolhido independente do score."""
        issues = [make_issue(IssueType.ARIA)]
        scan = make_scan(issues)
        decision = router.decide(scan, AgentType.OPENHANDS)
        assert decision.agent == "openhands"

    def test_manual_override_swe(self, router: Router) -> None:
        """Override para SWE mesmo com issue de contraste."""
        issues = [make_issue(IssueType.CONTRAST)]
        scan = make_scan(issues)
        decision = router.decide(scan, AgentType.SWE_AGENT)
        assert decision.agent == "swe-agent"

    def test_empty_issues_routes_to_swe(self, router: Router) -> None:
        """Sem issues → SWE (score negativo)."""
        scan = make_scan([])
        decision = router.decide(scan)
        assert decision.agent == "swe-agent"

    def test_decision_has_reason(self, router: Router) -> None:
        """Decisão deve sempre ter uma razão."""
        scan = make_scan([make_issue(IssueType.CONTRAST)])
        decision = router.decide(scan)
        assert decision.reason
        assert len(decision.reason) > 0
