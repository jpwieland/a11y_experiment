"""
Validation pipeline for LLM-generated accessibility patches.

Four-layer validation (methodology Section 3.7.2):
  Layer 1 — Syntactic validation (parse / compile check)
  Layer 2 — Functional preservation (structural heuristics)
  Layer 3 — Domain verification (WCAG rule re-scan confirms fix)
  Layer 4 — Code quality assessment (linting / style gate)
"""

from a11y_autofix.validation.pipeline import ValidationPipeline, ValidationResult
from a11y_autofix.validation.layer2 import Layer2Result

__all__ = ["ValidationPipeline", "ValidationResult", "Layer2Result"]
