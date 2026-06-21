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
    reflection_feedback: bool = True       # V2: pass rejection reason to subsequent retries
    wcag_semantic: bool = True             # V4: include WCAG message/impact/context in prompt
    skip_internal_validation: bool = False # V5: bypass L1-L3, accept any LLM code output


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
        AblationCondition(
            id="arch_full",
            label="Architecture — Full (Baseline)",
            description=(
                "Full pipeline: reflection feedback on retries, few-shot examples, "
                "WCAG semantic descriptions, and all internal validation layers active."
            ),
            flags=ComponentFlags(),
        ),
        AblationCondition(
            id="arch_no_reflection",
            label="Architecture — No Reflection Feedback",
            description=(
                "Retries do NOT receive the rejection reason from the previous attempt. "
                "Each retry sends the same base prompt. Isolates the contribution of "
                "the qualitative feedback loop between attempts."
            ),
            flags=ComponentFlags(reflection_feedback=False),
        ),
        AblationCondition(
            id="arch_no_few_shot",
            label="Architecture — No Few-Shot Examples",
            description=(
                "Component 5 (few-shot examples) removed. Zero-shot approach: the model "
                "receives only the issue description and file content."
            ),
            flags=ComponentFlags(few_shot=False),
        ),
        AblationCondition(
            id="arch_no_wcag_guidelines",
            label="Architecture — No WCAG Semantic Guidelines",
            description=(
                "WCAG human-readable description, impact classification, and context HTML "
                "removed from the prompt. Only the raw rule_id, WCAG criterion number, and "
                "element selector are shown. Isolates the contribution of semantic normative "
                "context."
            ),
            flags=ComponentFlags(wcag_semantic=False),
        ),
        AblationCondition(
            id="arch_no_internal_validation",
            label="Architecture — No Internal Validation (L1-L3 bypassed)",
            description=(
                "Validation pipeline (L1 syntax, L2 functional preservation, L3 domain "
                "heuristic) bypassed entirely. Any non-empty code output from the LLM is "
                "accepted. Isolates the contribution of the pre-injection filter stack. "
                "NOTE: IFR is measured by oracle acceptance (non-empty output), not "
                "browser-based re-scan."
            ),
            flags=ComponentFlags(skip_internal_validation=True),
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
Return the COMPLETE corrected file — every import, export, and line of the original — with only the accessibility fix applied.

Do NOT return just the changed element or a snippet. Do NOT omit or truncate any part of the file.

```tsx
[complete corrected file content here]
```"""

_USER_IMPACT_TEMPLATES: dict[str, str] = {
    "alt_text":  "screen reader users from understanding the image content",
    "label":     "users of assistive technology from understanding form fields",
    "semantic":  "screen readers and automated tools from correctly interpreting page structure",
    "contrast":  "users with low vision or colour blindness from reading the content",
    "aria":      "assistive technology from correctly conveying the element's role or state",
    "keyboard":  "keyboard-only users from interacting with this element",
    "focus":     "keyboard and switch-access users from knowing where focus is located",
}


_LAYER_NAMES = {
    1: "syntax — the output was not valid/parseable TSX",
    2: "functional regression — patch removed or changed an export, prop, or event handler",
    3: "domain — the targeted accessibility issue was NOT resolved",
    4: "code quality — a prohibited pattern was used (e.g. tabIndex < -1)",
}


def _violation_context_block(
    issue: A11yIssue,
    confidence: str,
    n_tools: int,
    include_user_impact: bool,
    wcag_semantic: bool = True,
) -> str:
    wcag = issue.wcag_criteria or "N/A"
    rule = issue.issue_type.value if hasattr(issue, "issue_type") else "unknown"
    selector = issue.selector

    if not wcag_semantic:
        lines = [
            "VIOLATION DETAILS:",
            f"- WCAG Criterion: {wcag}",
            f"- Rule: {rule}",
            f"- Element: {selector}",
        ]
        return "\n".join(lines)

    level = "AA"
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
    previous_rejection: dict | None = None,
) -> str:
    """
    Build the prompt for a single violation under the given ablation condition.

    Args:
        task:               AgentTask containing file path, content, and issue list.
        condition:          AblationCondition specifying which components to include.
        issue:              The specific A11yIssue being repaired in this prompt.
        confidence:         "HIGH" or "STANDARD" (multi-tool consensus).
        n_tools:            Number of tools that detected this violation.
        previous_rejection: Optional dict with keys 'layer', 'reason', 'excerpt'
                            describing why the previous attempt was rejected.
                            Only appended when condition.flags.reflection_feedback is True.

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
            _violation_context_block(issue, confidence, n_tools, f.user_impact, f.wcag_semantic)
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
        # Heuristic fallback: same format expectation, no explicit structure enforced
        parts.append(
            "Return the COMPLETE corrected file — all imports, exports, and every line "
            "of the original — in a ```tsx code block. Do NOT return just the changed element."
        )

    if condition.flags.reflection_feedback and previous_rejection:
        layer = previous_rejection.get("layer")
        reason = (previous_rejection.get("reason") or "unknown").strip()
        excerpt = (previous_rejection.get("excerpt") or "").strip()
        where = _LAYER_NAMES.get(layer, "validation/extraction") if layer else "validation/extraction"
        feedback_lines = [
            "## ⚠ PREVIOUS ATTEMPT REJECTED — do not repeat it",
            f"- Rejected at: {where}",
            f"- Detail: {reason[:300]}",
        ]
        if excerpt:
            feedback_lines.append(f"- Excerpt of your rejected output:\n```\n{excerpt[:400]}\n```")
        feedback_lines.append(
            "Produce a DIFFERENT correction that resolves the issue "
            "AND avoids the rejection cause above. Output the required format exactly."
        )
        parts.append("\n".join(feedback_lines))

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
    if not f.reflection_feedback: active.append("NO_reflection_feedback")
    if not f.wcag_semantic:        active.append("NO_wcag_semantic")
    if f.skip_internal_validation: active.append("SKIP_internal_validation")
    return active


# ── Multi-violation prompt builder (arch ablation pipeline format) ─────────────

def _format_issues_ablation_multi(
    issues: list,
    wcag_semantic: bool = True,
) -> str:
    """
    Format multiple A11yIssue objects for inclusion in an ablation prompt.

    wcag_semantic=True  → verbose format (message, impact, context HTML)
    wcag_semantic=False → minimal format (rule_id + selector + WCAG number only)
                          Used by arch_no_wcag_guidelines to isolate the
                          contribution of WCAG human-readable descriptions.
    """
    lines: list[str] = []
    for i, issue in enumerate(issues, 1):
        rule = issue.issue_type.value if hasattr(issue, "issue_type") else "unknown"
        wcag = issue.wcag_criteria or "N/A"
        selector = issue.selector or "?"

        if wcag_semantic:
            n_tools = issue.tool_consensus if hasattr(issue, "tool_consensus") else 1
            confidence = issue.confidence.value.upper() if hasattr(issue, "confidence") else "STANDARD"
            ctx = (issue.context or "")[:200].replace("\n", " ")
            lines.append(
                f"### Issue {i}: {rule.upper()} | WCAG {wcag} | "
                f"{confidence} confidence ({n_tools} tool{'s' if n_tools > 1 else ''})"
            )
            lines.append(f"- Message: {issue.message}")
            lines.append(f"- Selector: `{selector}`")
            if ctx:
                lines.append(f"- Context: `{ctx}`")
            lines.append("")
        else:
            # Minimal: rule + WCAG number + selector only (no semantic descriptions)
            lines.append(f"{i}. [{rule.upper()}] WCAG {wcag} | selector: `{selector}`")

    return "\n".join(lines)


def build_ablation_prompt_multi(
    task,
    condition: AblationCondition,
    previous_rejection: dict | None = None,
) -> str:
    """
    Build an ablation prompt for ALL violations in a file (pipeline format).

    This is the multi-violation counterpart to build_ablation_prompt().
    It mirrors PromptBuilder.build() from the main pipeline but applies
    condition flags so that individual components can be ablated.

    Used by arch_ablation_pipeline_runner.py — NOT by the original
    single-violation ablation runner.

    Args:
        task:               AgentTask with file path, content, and ALL pending issues.
        condition:          AblationCondition specifying which components to include.
        previous_rejection: Optional dict with keys 'error'/'reason', 'layer', 'excerpt'
                            from the previous rejected attempt. Appended only when
                            condition.flags.reflection_feedback is True.

    Returns:
        Assembled prompt string ready for the LLM.
    """
    f = condition.flags
    file_name = task.file.name if task.file else "unknown.tsx"

    # raw_baseline: no template, raw issue JSON only
    if f.raw_scanner_output:
        raw_issues = [
            {
                "wcag_criterion": iss.wcag_criteria,
                "rule": getattr(getattr(iss, "issue_type", None), "value", "unknown"),
                "message": iss.message,
                "selector": iss.selector,
                "context": (iss.context or "")[:200],
            }
            for iss in task.issues
        ]
        return (
            f"Fix these accessibility issues in {file_name}:\n\n"
            f"{json.dumps(raw_issues, indent=2)}\n\n"
            f"File content:\n```\n{task.file_content}\n```"
        )

    parts: list[str] = []

    # Component 1: Role definition
    if f.role_definition:
        parts.append(
            "You are an expert in React/TypeScript and WCAG 2.1/2.2 WCAG2AA accessibility. "
            "Your task is to fix ALL listed accessibility violations in the provided file."
        )

    # Component 2: Constraints (multi-violation version)
    if f.constraints:
        parts.append(
            "HARD CONSTRAINTS:\n"
            "1. Preserve ALL business logic — do not change functionality\n"
            "2. Prefer semantic HTML over ARIA attributes when possible\n"
            "3. Fix EVERY listed issue in a single corrected file\n"
            "4. For labels: use <label htmlFor>, aria-label, or aria-labelledby\n"
            "5. For images: add descriptive alt text (empty alt=\"\" for decorative)\n"
            "6. Do NOT add TypeScript type annotations not present in original\n"
            "7. Do NOT change import statements\n"
            "8. Do NOT modify unrelated code or add unnecessary abstraction"
        )

    # Component 3: Code context (full file)
    if f.code_context:
        parts.append(f"## File: {file_name}\n\n```tsx\n{task.file_content}\n```")

    # Component 4: Violation context (wcag_semantic flag controls verbosity)
    if f.violation_context and task.issues:
        issues_text = _format_issues_ablation_multi(task.issues, wcag_semantic=f.wcag_semantic)
        header = f"## Accessibility Issues to Fix ({len(task.issues)} total):\n\n"
        parts.append(header + issues_text)

    # Component 5: Few-shot examples
    if f.few_shot:
        parts.append(_select_few_shot_examples(task.issues))

    # Component 6: Output format
    if f.output_format:
        parts.append(
            "Return the COMPLETE corrected file — every import, export, and line of the "
            "original — with ALL accessibility fixes applied.\n\n"
            "Do NOT return just the changed elements or a snippet. "
            "Do NOT omit or truncate any part of the file.\n\n"
            "```tsx\n[complete corrected file content here]\n```"
        )
    else:
        parts.append(
            "Return the COMPLETE corrected file — all imports, exports, and every line — "
            "in a ```tsx code block. Do NOT return just the changed elements."
        )

    # Reflection feedback on retries (arch_no_reflection disables this)
    if f.reflection_feedback and previous_rejection:
        layer = previous_rejection.get("layer")
        reason = (
            previous_rejection.get("reason")
            or previous_rejection.get("error")
            or "unknown"
        ).strip()
        excerpt = (previous_rejection.get("excerpt") or "").strip()
        where = _LAYER_NAMES.get(layer, "validation/extraction") if layer else "validation/extraction"

        feedback_lines = [
            "## ⚠ PREVIOUS ATTEMPT REJECTED — do not repeat it",
            f"- Rejected at: {where}",
            f"- Detail: {reason[:300]}",
        ]
        if excerpt:
            feedback_lines.append(f"- Excerpt of your rejected output:\n```\n{excerpt[:400]}\n```")
        feedback_lines.append(
            "Produce a DIFFERENT correction that resolves ALL listed issues "
            "AND avoids the rejection cause above. Output the COMPLETE corrected file."
        )
        parts.append("\n".join(feedback_lines))

    return "\n\n".join(parts)
