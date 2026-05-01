"""
ablation_conditions.py
======================
Defines AblationCondition dataclass and builds the prompt for each of the
eight experimental conditions.  Wraps (but does not modify) the existing
PromptBuilder from a11y_autofix.agents.prompts so that condition-specific
behaviour is fully isolated here.

Condition catalogue (see config/prompt_ablation.yaml for rationale):
  full              — all six components
  minus_role        — no Component 1
  minus_user_impact — Component 2 without USER_IMPACT sentence
  minus_code_context — no Component 3
  minus_constraints — no Component 4
  minus_few_shot    — no Component 5  (= zero-shot)
  minus_output_format — no Component 6
  raw_baseline      — raw Pa11y JSON, no structured template
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# ablation_study/src/ → ablation_study/ → a11y_experiment/ (package root)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from a11y_autofix.agents.prompts import (
    PromptingStrategy,
    _FEW_SHOT_ARIA,
    _FEW_SHOT_KEYBOARD,
    _FEW_SHOT_LABEL_AND_ALT,
    _FEW_SHOT_SEMANTIC,
    _select_few_shot_examples,
    format_issues,
)
from a11y_autofix.config import A11yIssue, AgentTask

if TYPE_CHECKING:
    pass


# ── Condition spec ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ComponentFlags:
    role_definition: bool = True
    violation_context: bool = True
    user_impact: bool = True       # sub-field inside violation_context
    code_context: bool = True
    constraints: bool = True
    few_shot: bool = True
    output_format: bool = True
    raw_scanner_output: bool = False  # raw_baseline only


@dataclass(frozen=True)
class AblationCondition:
    id: str
    label: str
    description: str
    flags: ComponentFlags

    def __str__(self) -> str:  # noqa: D105
        return self.id


# ── Condition registry ────────────────────────────────────────────────────────

CONDITIONS: dict[str, AblationCondition] = {
    c.id: c
    for c in [
        AblationCondition(
            id="full",
            label="Full Prompt (Baseline)",
            description="All six components active.",
            flags=ComponentFlags(),
        ),
        AblationCondition(
            id="minus_role",
            label="− Role Definition (C1)",
            description="Component 1 removed.",
            flags=ComponentFlags(role_definition=False),
        ),
        AblationCondition(
            id="minus_user_impact",
            label="− User Impact field (C2 partial)",
            description="USER_IMPACT sentence removed from Component 2.",
            flags=ComponentFlags(user_impact=False),
        ),
        AblationCondition(
            id="minus_code_context",
            label="− Code Context (C3)",
            description="Component 3 removed.",
            flags=ComponentFlags(code_context=False),
        ),
        AblationCondition(
            id="minus_constraints",
            label="− Constraints (C4)",
            description="Component 4 removed.",
            flags=ComponentFlags(constraints=False),
        ),
        AblationCondition(
            id="minus_few_shot",
            label="− Few-Shot Examples (C5)",
            description="Component 5 removed (zero-shot).",
            flags=ComponentFlags(few_shot=False),
        ),
        AblationCondition(
            id="minus_output_format",
            label="− Output Format (C6)",
            description="Component 6 removed; heuristic code-block extraction used.",
            flags=ComponentFlags(output_format=False),
        ),
        AblationCondition(
            id="raw_baseline",
            label="Raw Baseline",
            description="No structured template; raw Pa11y JSON as task body.",
            flags=ComponentFlags(
                role_definition=False,
                violation_context=False,
                user_impact=False,
                code_context=False,
                constraints=False,
                few_shot=False,
                output_format=False,
                raw_scanner_output=True,
            ),
        ),
    ]
}


# ── Prompt builder ────────────────────────────────────────────────────────────

_ROLE_TEXT = (
    "You are an expert React accessibility engineer with deep knowledge of "
    "WCAG 2.1 standards and React-specific implementation patterns. "
    "Your task is to fix accessibility violations while preserving all existing functionality."
)

_CONSTRAINTS_TEXT = """\
CONSTRAINTS:
[YES] Preserve all existing props, state, and handlers
[YES] Maintain the component's public interface
[YES] Follow React best practices for this codebase
[YES] Use the simplest solution that resolves the issue
[NO]  Do NOT modify unrelated code
[NO]  Do NOT introduce new dependencies
[NO]  Do NOT add unnecessary abstraction"""

_OUTPUT_FORMAT_TEXT = """\
Respond with ONLY valid JSON:
{
  "fixed_code": "complete corrected component code",
  "explanation": "one sentence describing the fix",
  "wcag_criteria_addressed": ["1.1.1"],
  "confidence": 0.85
}"""

_USER_IMPACT_TEMPLATES: dict[str, str] = {
    "alt_text":  "screen reader users from understanding the image content",
    "label":     "users of assistive technology from understanding form fields",
    "semantic":  "screen readers and automated tools from correctly interpreting page structure",
    "contrast":  "users with low vision or colour blindness from reading the content",
    "aria":      "assistive technology from correctly conveying the element's role or state",
    "keyboard":  "keyboard-only users from interacting with this element",
    "focus":     "keyboard and switch-access users from knowing where focus is located",
}


def _violation_context_block(
    issue: A11yIssue,
    confidence: str,
    n_tools: int,
    include_user_impact: bool,
) -> str:
    wcag = issue.wcag_criteria or "N/A"
    level = "AA"
    rule = issue.issue_type.value if hasattr(issue, "issue_type") else "unknown"
    selector = issue.selector
    context_html = (issue.context or "")[:300]

    lines = [
        "VIOLATION DETAILS:",
        f"- WCAG Criterion: {wcag} ({level})",
        f"- Rule: {rule}",
        f"- Description: {issue.message}",
        f"- Confidence: {confidence} ({n_tools} tool{'s' if n_tools > 1 else ''} detected)",
        f"- Element: {selector}",
        f"- Current HTML: {context_html}",
    ]

    if include_user_impact:
        category = getattr(getattr(issue, "issue_type", None), "value", "aria")
        impact_text = _USER_IMPACT_TEMPLATES.get(category, "assistive technology users from interacting correctly")
        lines.append(f"\nThis violation prevents {impact_text}.")

    return "\n".join(lines)


def _code_context_block(task: AgentTask) -> str:
    file_name = task.file.name if task.file else "unknown.tsx"
    styling = "CSS Modules / styled-components / inline (auto-detected)"
    return (
        f"CODEBASE CONTEXT:\n"
        f"- Framework: React (functional + hooks)\n"
        f"- Styling: {styling}\n"
        f"- File: {file_name}\n\n"
        f"Current component code:\n"
        f"```jsx\n{task.file_content}\n```"
    )


def build_ablation_prompt(
    task: AgentTask,
    condition: AblationCondition,
    issue: A11yIssue,
    confidence: str = "STANDARD",
    n_tools: int = 1,
) -> str:
    """
    Build the prompt for a single violation under the given ablation condition.

    Args:
        task:       AgentTask containing file path, content, and issue list.
        condition:  AblationCondition specifying which components to include.
        issue:      The specific A11yIssue being repaired in this prompt.
        confidence: "HIGH" or "STANDARD" (multi-tool consensus).
        n_tools:    Number of tools that detected this violation.

    Returns:
        Assembled prompt string for the LLM.
    """
    f = condition.flags

    # raw_baseline: no template, just raw Pa11y JSON
    if f.raw_scanner_output:
        raw = {
            "wcag_criterion": issue.wcag_criteria,
            "rule": getattr(getattr(issue, "issue_type", None), "value", "unknown"),
            "message": issue.message,
            "selector": issue.selector,
            "context": (issue.context or "")[:300],
        }
        return (
            f"Fix this accessibility issue in {task.file.name}:\n\n"
            f"{json.dumps(raw, indent=2)}\n\n"
            f"File content:\n```\n{task.file_content}\n```"
        )

    parts: list[str] = []

    # Component 1
    if f.role_definition:
        parts.append(_ROLE_TEXT)

    # Component 2
    if f.violation_context:
        parts.append(
            _violation_context_block(issue, confidence, n_tools, f.user_impact)
        )

    # Component 3
    if f.code_context:
        parts.append(_code_context_block(task))

    # Component 4
    if f.constraints:
        parts.append(_CONSTRAINTS_TEXT)

    # Component 5
    if f.few_shot:
        parts.append(_select_few_shot_examples([issue]))

    # Component 6
    if f.output_format:
        parts.append(_OUTPUT_FORMAT_TEXT)
    else:
        # Heuristic fallback instruction so the agent still returns code
        parts.append(
            "Return the complete corrected component code in a ```tsx code block."
        )

    return "\n\n".join(parts)


def components_active(condition: AblationCondition) -> list[str]:
    """Return list of active component names for logging."""
    f = condition.flags
    active = []
    if f.role_definition:    active.append("C1_role")
    if f.violation_context:  active.append("C2_violation_context")
    if f.user_impact:        active.append("C2_user_impact")
    if f.code_context:       active.append("C3_code_context")
    if f.constraints:        active.append("C4_constraints")
    if f.few_shot:           active.append("C5_few_shot")
    if f.output_format:      active.append("C6_output_format")
    if f.raw_scanner_output: active.append("RAW")
    return active
