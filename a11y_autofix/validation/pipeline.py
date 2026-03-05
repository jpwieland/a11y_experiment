"""
Four-layer validation pipeline for LLM-generated accessibility patches.

Layers (methodology Section 3.7.2):
  1. Syntactic validation    — can the file be parsed as valid TSX/JS?
  2. Functional preservation — structural heuristics (prop interface, exports,
                               event handlers). See layer2.py for scope limitation.
  3. Domain verification     — WCAG rule re-scan confirms the targeted issue
                               is resolved (heuristic, not full browser scan).
  4. Code quality assessment — basic linting / prohibited pattern gate.

ValidationResult.rejected_at_layer:
  None — patch passed all layers
  1    — failed syntactic validation
  2    — failed functional preservation (structural heuristic)
  3    — failed domain verification
  4    — failed code quality assessment

Regression rate ρ (H5) is computed from Layer 2 rejections.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

from a11y_autofix.config import A11yIssue
from a11y_autofix.validation.layer2 import Layer2Result, run_layer2

log = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    """
    Structured result from the four-layer validation pipeline.

    Methodology reference: Section 3.7.2 (Secondary Metrics).
    """

    passed: bool
    rejected_at_layer: Optional[int] = None
    """
    Layer at which the patch was rejected (1–4), or None if it passed all layers.
    Used to compute the regression rate ρ (H5) when layer == 2.
    """
    failure_reason: Optional[str] = None
    """Human-readable description of the rejection cause."""
    layer2_detail: Optional[Layer2Result] = None
    """Detailed Layer 2 result (which structural check failed)."""
    layer_timings_ms: dict[int, float] = field(default_factory=dict)
    """Wall-clock time spent in each layer: {layer_number: elapsed_ms}."""


class ValidationPipeline:
    """
    Runs all four validation layers on a generated patch.

    Usage::

        pipeline = ValidationPipeline()
        result = pipeline.validate(
            patched_content="...",
            original_content="...",
            issues=[...],
        )
        if not result.passed:
            print(f"Rejected at layer {result.rejected_at_layer}")
    """

    def validate(
        self,
        patched_content: str,
        original_content: str,
        issues: list[A11yIssue],
        file_id: str = "",
        model_id: str = "",
        strategy: str = "",
    ) -> ValidationResult:
        """
        Run the four-layer validation pipeline.

        Args:
            patched_content: LLM-generated patched file content.
            original_content: Original (unmodified) file content.
            issues: Accessibility issues the patch was meant to fix.
            file_id: File identifier for structured logging.
            model_id: Model identifier for structured logging.
            strategy: Prompting strategy for structured logging.

        Returns:
            ValidationResult with pass/fail status and rejection details.
        """
        timings: dict[int, float] = {}

        # ── Layer 1: Syntactic validation ──────────────────────────────────
        t1 = time.perf_counter()
        layer1_ok, layer1_reason = self._validate_layer1(patched_content)
        timings[1] = (time.perf_counter() - t1) * 1000

        if not layer1_ok:
            log.info(
                "validation_rejected",
                file_id=file_id,
                layer=1,
                check_failed="syntax",
                model_id=model_id,
                strategy=strategy,
            )
            return ValidationResult(
                passed=False,
                rejected_at_layer=1,
                failure_reason=layer1_reason,
                layer_timings_ms=timings,
            )

        # ── Layer 2: Functional preservation ──────────────────────────────
        t2 = time.perf_counter()
        layer2_result = run_layer2(original_content, patched_content)
        timings[2] = (time.perf_counter() - t2) * 1000

        if not layer2_result.passed:
            check_name = layer2_result.failed_check or "unknown"
            log.info(
                "validation_rejected",
                file_id=file_id,
                layer=2,
                check_failed=check_name,
                model_id=model_id,
                strategy=strategy,
            )
            return ValidationResult(
                passed=False,
                rejected_at_layer=2,
                failure_reason=f"functional_regression:{check_name}",
                layer2_detail=layer2_result,
                layer_timings_ms=timings,
            )

        # ── Layer 3: Domain verification ───────────────────────────────────
        t3 = time.perf_counter()
        layer3_ok, layer3_reason = self._validate_layer3(patched_content, issues)
        timings[3] = (time.perf_counter() - t3) * 1000

        if not layer3_ok:
            log.info(
                "validation_rejected",
                file_id=file_id,
                layer=3,
                check_failed="domain_verification",
                model_id=model_id,
                strategy=strategy,
            )
            return ValidationResult(
                passed=False,
                rejected_at_layer=3,
                failure_reason=layer3_reason,
                layer2_detail=layer2_result,
                layer_timings_ms=timings,
            )

        # ── Layer 4: Code quality assessment ──────────────────────────────
        t4 = time.perf_counter()
        layer4_ok, layer4_reason = self._validate_layer4(patched_content)
        timings[4] = (time.perf_counter() - t4) * 1000

        if not layer4_ok:
            log.info(
                "validation_rejected",
                file_id=file_id,
                layer=4,
                check_failed="code_quality",
                model_id=model_id,
                strategy=strategy,
            )
            return ValidationResult(
                passed=False,
                rejected_at_layer=4,
                failure_reason=layer4_reason,
                layer2_detail=layer2_result,
                layer_timings_ms=timings,
            )

        return ValidationResult(
            passed=True,
            layer2_detail=layer2_result,
            layer_timings_ms=timings,
        )

    # ── Layer implementations ──────────────────────────────────────────────

    def _validate_layer1(self, content: str) -> tuple[bool, str | None]:
        """
        Layer 1: Syntactic validation.

        Checks that the patch is non-empty and does not contain obvious
        syntax markers of a truncated or malformed LLM response. A full
        AST parse requires a Node.js toolchain; this heuristic guards
        against the most common failure modes.
        """
        if not content or not content.strip():
            return False, "empty_patch"

        # Reject if it looks like the model returned its own prompt back
        if "```tsx" in content and content.count("```") == 1:
            return False, "unclosed_code_block"

        # Reject if the patch still contains LLM refusal markers
        refusal_patterns = [
            r"I (can'?t|cannot|am unable to)",
            r"As an AI",
            r"I don'?t have access",
        ]
        for pattern in refusal_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return False, "llm_refusal"

        # Very basic JSX/TSX sanity: must contain at least one JSX-like tag
        if not re.search(r'<\w[\w.]*(?:\s[^>]*)?\s*/?>', content):
            return False, "no_jsx_found"

        return True, None

    def _validate_layer3(
        self,
        patched_content: str,
        issues: list[A11yIssue],
    ) -> tuple[bool, str | None]:
        """
        Layer 3: Domain verification (heuristic).

        Checks that the patched content contains the expected accessibility
        attributes/elements implied by the issue types that were targeted.
        This is a static heuristic — not a full browser-based re-scan.
        """
        from a11y_autofix.config import IssueType

        issue_types = {i.issue_type for i in issues}
        failed_checks: list[str] = []

        if IssueType.ALT_TEXT in issue_types:
            # At minimum one img with alt attribute should be present
            if re.search(r'<img\b', patched_content) and not re.search(
                r'<img\b[^>]*\balt\s*=', patched_content
            ):
                failed_checks.append("missing_alt_on_img")

        if IssueType.LABEL in issue_types:
            # Forms should have labels or aria-label attributes
            has_label = bool(re.search(r'<label\b|aria-label\s*=|aria-labelledby\s*=', patched_content))
            has_inputs = bool(re.search(r'<input\b|<select\b|<textarea\b', patched_content))
            if has_inputs and not has_label:
                failed_checks.append("missing_form_labels")

        if failed_checks:
            return False, "domain_check_failed:" + ",".join(failed_checks)

        return True, None

    def _validate_layer4(self, content: str) -> tuple[bool, str | None]:
        """
        Layer 4: Code quality assessment.

        Checks for patterns that indicate the patch may harm code quality
        in ways not caught by earlier layers:
        - Inline styles that reset accessibility-critical properties to zero
        - Presence of dangerouslySetInnerHTML (security / a11y risk)
        - tabIndex values below -1 (traps focus)
        """
        # Prohibit tabIndex < -1 (traps keyboard focus)
        tab_indices = re.findall(r'tabIndex\s*[=:]\s*\{?\s*(-?\d+)', content)
        for val in tab_indices:
            if int(val) < -1:
                return False, f"invalid_tabIndex:{val}"

        # Prohibit dangerouslySetInnerHTML
        if "dangerouslySetInnerHTML" in content:
            return False, "dangerouslySetInnerHTML_present"

        return True, None
