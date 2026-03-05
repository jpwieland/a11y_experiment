"""
Prompts centralizados para todos os agentes de correção.

Centralizar prompts garante consistência e facilita comparação de resultados
entre experimentos. Mudanças de prompt são rastreadas no controle de versão.

Prompt template components (methodology Section 3.6.2, IV2):
  Component 1: Role & context setup
  Component 2: Hard constraints
  Component 3: File content
  Component 4: Issue list
  Component 5: Few-shot examples (absent in zero-shot)
  Component 6: Output format specification (+ CoT instruction for chain-of-thought)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from a11y_autofix.config import A11yIssue, AgentTask


class PromptingStrategy(str, Enum):
    """Prompting strategy for the experiment (methodology Section 3.6.2, IV2)."""

    ZERO_SHOT = "zero-shot"
    FEW_SHOT = "few-shot"
    CHAIN_OF_THOUGHT = "chain-of-thought"


# ---------------------------------------------------------------------------
# Few-shot examples (Component 5) — included only for FEW_SHOT and CoT
# ---------------------------------------------------------------------------

_FEW_SHOT_EXAMPLES = """
### Component 5: Few-Shot Examples

**Example 1 — Missing alt text (WCAG 1.1.1)**
Before:
```tsx
<img src="/hero.jpg" className="hero-image" />
```
After:
```tsx
<img src="/hero.jpg" className="hero-image" alt="Hero banner showing the product dashboard" />
```

**Example 2 — Missing form label (WCAG 1.3.1)**
Before:
```tsx
<input type="email" placeholder="Enter email" />
```
After:
```tsx
<label htmlFor="email-input">Email address</label>
<input id="email-input" type="email" placeholder="Enter email" aria-describedby="email-hint" />
<span id="email-hint" className="hint">We will never share your email</span>
```

**Example 3 — Keyboard inaccessible button (WCAG 2.1.1)**
Before:
```tsx
<div onClick={handleSubmit} className="btn-primary">Submit</div>
```
After:
```tsx
<button
  type="button"
  onClick={handleSubmit}
  className="btn-primary"
>
  Submit
</button>
```
""".strip()

# ---------------------------------------------------------------------------
# CoT reasoning instruction appended to Component 6 (chain-of-thought only)
# ---------------------------------------------------------------------------

_COT_REASONING_INSTRUCTION = """
Before producing the corrected file, reason step-by-step through each violation: \
(1) identify the root cause, (2) determine the minimal change that resolves it \
without affecting functionality, (3) verify that the change does not introduce any \
of the failure modes listed in the constraints above. Output your reasoning inside a \
<!-- CoT --> comment block immediately before the ```tsx code block. Do not include \
reasoning inside the code block itself."""


class PromptBuilder:
    """
    Builds structured six-component prompts for LLM-based accessibility repair.

    Component presence per strategy (methodology Section 3.6.2):
      zero-shot:       Components 1–4 + 6 (Component 5 absent).
      few-shot:        Components 1–6 (full template).
      chain-of-thought: Components 1–6 + CoT instruction appended to Component 6.
    """

    def build(
        self,
        issues: list[A11yIssue],
        file: Path,
        file_content: str,
        strategy: PromptingStrategy = PromptingStrategy.FEW_SHOT,
        wcag_level: str = "WCAG2AA",
    ) -> str:
        """
        Build a structured prompt for the given strategy.

        Args:
            issues: Accessibility issues to fix.
            file: Source file path.
            file_content: Current file contents.
            strategy: Prompting strategy.
            wcag_level: WCAG conformance level.

        Returns:
            Formatted prompt string.
        """
        # Component 1: Role & context
        component1 = (
            f"You are an expert in React/TypeScript and WCAG 2.1/2.2 {wcag_level} accessibility. "
            f"Your task is to fix all listed accessibility violations in the provided component file."
        )

        # Component 2: Hard constraints
        component2 = """HARD CONSTRAINTS:
1. Preserve ALL business logic — do not change functionality
2. Prefer semantic HTML over ARIA attributes when possible
3. For contrast issues: adjust only CSS color/background properties
4. For keyboard issues: add tabIndex + onKeyDown/onKeyPress handlers
5. For labels: use <label htmlFor>, aria-label, or aria-labelledby
6. For images: add descriptive alt text (empty alt="" for decorative images)
7. Return ALWAYS valid TSX that compiles without errors
8. Do NOT add TypeScript type annotations not present in original
9. Do NOT change import statements
10. Do NOT add comments unless explaining non-obvious accessibility logic"""

        # Component 3: File content
        component3 = f"## File: {file.name}\n\n```tsx\n{file_content}\n```"

        # Component 4: Issue list
        issues_text = format_issues(issues, verbose=True)
        component4 = f"## Accessibility Issues ({len(issues)} total):\n\n{issues_text}"

        # Component 5: Few-shot examples (absent for zero-shot)
        component5 = ""
        if strategy in (PromptingStrategy.FEW_SHOT, PromptingStrategy.CHAIN_OF_THOUGHT):
            component5 = _FEW_SHOT_EXAMPLES

        # Component 6: Output format specification
        component6 = (
            "## Output Format (required):\n\n"
            "Return the COMPLETE corrected file. Do not truncate or omit any part.\n\n"
            "```tsx\n[complete corrected file content here]\n```\n\n"
            "If you cannot fix an issue without breaking functionality, explain why "
            "as a comment before the code block and skip that issue."
        )

        # Append CoT reasoning instruction for chain-of-thought
        if strategy == PromptingStrategy.CHAIN_OF_THOUGHT:
            component6 += _COT_REASONING_INSTRUCTION

        # Assemble components (Component 5 is absent for zero-shot)
        parts = [component1, component2, component3, component4]
        if component5:
            parts.append(component5)
        parts.append(component6)

        return "\n\n".join(parts)

    def build_system_prompt(self, strategy: PromptingStrategy = PromptingStrategy.FEW_SHOT) -> str:
        """Return the system prompt for the given strategy."""
        base = (
            "You are an expert accessibility engineer specialising in React/TypeScript "
            "and WCAG 2.1/2.2 conformance. Follow the constraints and output format exactly."
        )
        if strategy == PromptingStrategy.CHAIN_OF_THOUGHT:
            base += (
                " For each issue, reason step-by-step before writing the corrected code. "
                "Place your reasoning in a <!-- CoT --> comment block."
            )
        return base


def system_prompt_openhands() -> str:
    """System prompt para OpenHands (contexto amplo, múltiplos issues)."""
    return """You are an expert in React/TypeScript and WCAG 2.1/2.2 AA accessibility.

HARD CONSTRAINTS:
1. Preserve ALL business logic — do not change functionality
2. Prefer semantic HTML over ARIA attributes when possible
3. For contrast issues: adjust only CSS color/background properties
4. For keyboard issues: add tabIndex + onKeyDown/onKeyPress handlers
5. For labels: use <label htmlFor>, aria-label, or aria-labelledby
6. For images: add descriptive alt text (empty alt="" for decorative images)
7. Return ALWAYS valid TSX that compiles without errors
8. Do NOT add TypeScript type annotations not present in original
9. Do NOT change import statements
10. Do NOT add comments unless explaining non-obvious accessibility logic

OUTPUT FORMAT (required):
```tsx
[complete corrected file content here]
```

If you cannot fix an issue without breaking functionality, explain why and skip it."""


def system_prompt_swe() -> str:
    """System prompt para SWE-agent (correções cirúrgicas, mínimas mudanças)."""
    return """You are a surgical accessibility fixer for React/TypeScript code.

RULES:
- MINIMAL CHANGES ONLY — change the absolute minimum to fix each issue
- Each patch must be a precise find-and-replace
- Preserve all whitespace, indentation, and formatting around changed lines
- Prefer Option B (surgical patches) when fixing ≤5 localized issues

OUTPUT OPTIONS:

Option A — Full file (use when restructuring is needed):
```tsx
[complete file content]
```

Option B — Surgical patches (preferred for ≤5 changes):
PATCH 1:
FIND: `<exact original line>`
REPLACE: `<corrected line>`

PATCH 2:
FIND: `<exact original line>`
REPLACE: `<corrected line>`

Do NOT include line numbers. FIND must match exactly what's in the file."""


def system_prompt_direct() -> str:
    """System prompt para DirectLLMAgent (fallback minimalista)."""
    return """You are an accessibility expert fixing React/TypeScript components.

Fix the listed WCAG accessibility issues. Return the complete corrected file.

OUTPUT:
```tsx
[complete corrected TSX/JSX file]
```

Rules:
- Keep all existing functionality
- Use semantic HTML when possible
- Add aria attributes only when necessary
- Return valid TSX"""


def build_openhands_prompt(task: AgentTask) -> str:
    """
    Constrói prompt do usuário para OpenHands.

    Args:
        task: Tarefa com arquivo e issues a corrigir.

    Returns:
        String do prompt formatado.
    """
    issues_text = format_issues(task.issues, verbose=True)
    return f"""## File: {task.file.name}
WCAG Level: {task.wcag_level}

### Current Code:
```tsx
{task.file_content}
```

### Accessibility Issues ({len(task.issues)} total):
{issues_text}

Fix ALL issues listed above. Return the complete corrected file in a ```tsx code block."""


def build_swe_prompt(task: AgentTask) -> str:
    """
    Constrói prompt do usuário para SWE-agent.

    Args:
        task: Tarefa com arquivo e issues a corrigir.

    Returns:
        String do prompt formatado.
    """
    issues_text = format_issues(task.issues, verbose=False)
    return f"""Fix accessibility issues in: {task.file.name}
WCAG: {task.wcag_level}

```tsx
{task.file_content}
```

Issues to fix:
{issues_text}

Provide PATCH blocks (FIND/REPLACE) or complete file if restructuring needed."""


def build_direct_llm_prompt(task: AgentTask) -> str:
    """
    Constrói prompt minimalista para DirectLLMAgent.

    Args:
        task: Tarefa com arquivo e issues.

    Returns:
        String do prompt.
    """
    issues_text = "\n".join(
        f"- [{i.issue_type.value.upper()}] WCAG {i.wcag_criteria or 'N/A'}: "
        f"{i.message} (selector: {i.selector})"
        for i in task.issues
    )
    return f"""Fix these accessibility issues in {task.file.name}:

{issues_text}

Original file:
```tsx
{task.file_content}
```

Return the complete corrected file."""


def format_issues(issues: list[A11yIssue], verbose: bool = True) -> str:
    """
    Formata issues para inclusão em prompts.

    Args:
        issues: Lista de issues a formatar.
        verbose: Se True, inclui contexto e findings detalhados.

    Returns:
        String formatada com todos os issues.
    """
    lines = []
    confidence_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    for i, issue in enumerate(issues, 1):
        icon = confidence_icons.get(issue.confidence.value, "⚪")
        consensus = f"({issue.tool_consensus} tools)" if issue.tool_consensus > 1 else "(1 tool)"

        if verbose:
            lines.append(
                f"### Issue {i}: {issue.issue_type.value.upper()} | "
                f"WCAG {issue.wcag_criteria or 'N/A'} | "
                f"{icon} {issue.confidence.value.upper()} confidence | "
                f"{issue.complexity.value} | {consensus}"
            )
            tools = ", ".join(t.value for t in issue.found_by)
            lines.append(f"- Found by: {tools}")
            lines.append(f"- Impact: {issue.impact}")
            lines.append(f"- Message: {issue.message}")
            lines.append(f"- Selector: `{issue.selector}`")
            if issue.context:
                ctx = issue.context[:200].replace("\n", " ")
                lines.append(f"- Context: `{ctx}`")
            lines.append("")
        else:
            lines.append(
                f"{i}. [{issue.issue_type.value.upper()}] "
                f"WCAG {issue.wcag_criteria or 'N/A'} "
                f"{icon} — {issue.message[:100]} | selector: `{issue.selector}`"
            )

    return "\n".join(lines)
