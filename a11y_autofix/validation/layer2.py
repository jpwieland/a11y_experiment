"""
Layer 2: Functional Preservation (Heuristic).

Checks performed:
  1. Prop interface identity (TypeScript interface AST diff)
  2. Export signature presence (default + named exports)
  3. Event handler presence (onClick, onChange, onSubmit identifiers)

Scope limitation: These checks verify STRUCTURAL signatures only. They do NOT
execute the component, run any test suite, or verify runtime behaviour. A patch
passing this layer may still introduce a runtime regression undetectable by
static analysis.

This boundary is explicitly documented in the methodology (Section 3.8.1,
Construct Validity — Validation pipeline heuristics).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass
class Layer2Result:
    """
    Result of Layer 2 (functional preservation) checks.

    The failed_check field maps directly to the regression rate ρ (H5)
    breakdown by failure type (methodology Section 3.1.4).
    """

    passed: bool
    failed_check: str | None = None
    """
    The specific structural check that failed. Possible values:
      "prop_interface"   — TypeScript prop interface was mutated
      "export_signature" — Default or named export was removed
      "event_handler"    — A required event handler identifier was removed
    """
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------

# Common event handler attribute patterns
_EVENT_HANDLER_RE = re.compile(
    r'\b(onClick|onChange|onSubmit|onKeyDown|onKeyPress|onKeyUp|onFocus|onBlur)\b'
)

# TypeScript interface / type declaration pattern
_INTERFACE_RE = re.compile(
    r'\binterface\s+\w+\s*\{[^}]*\}',
    re.DOTALL,
)

# Export patterns
_DEFAULT_EXPORT_RE = re.compile(r'\bexport\s+default\b')
_NAMED_EXPORT_RE = re.compile(r'\bexport\s+(?:const|function|class|type|interface)\b')


def _extract_interfaces(code: str) -> set[str]:
    """Extract TypeScript interface names."""
    return set(re.findall(r'\binterface\s+(\w+)', code))


def _extract_prop_signatures(code: str) -> set[str]:
    """
    Extract prop-related interface member signatures as a set of strings.

    We normalise whitespace and sort to make comparison order-independent.
    """
    sigs: set[str] = set()
    for block in _INTERFACE_RE.findall(code):
        # Each non-empty, non-comment line inside the block is a "member"
        for line in block.splitlines():
            stripped = line.strip().rstrip(";,")
            if stripped and not stripped.startswith("//") and stripped not in ("{", "}"):
                sigs.add(stripped)
    return sigs


def check_prop_interface(original: str, patched: str) -> Layer2Result:
    """
    Check 1: Prop interface identity.

    Verifies that no TypeScript prop interface was removed or had its
    members changed in a breaking way (members may be added, not removed).
    """
    t0 = time.perf_counter()

    original_interfaces = _extract_interfaces(original)
    patched_interfaces = _extract_interfaces(patched)

    # Interfaces present in original but absent in patch = breaking removal
    removed_interfaces = original_interfaces - patched_interfaces
    if removed_interfaces:
        return Layer2Result(
            passed=False,
            failed_check="prop_interface",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    # Check that prop signatures are a superset (no members removed)
    original_sigs = _extract_prop_signatures(original)
    patched_sigs = _extract_prop_signatures(patched)
    removed_sigs = original_sigs - patched_sigs
    if removed_sigs:
        return Layer2Result(
            passed=False,
            failed_check="prop_interface",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    return Layer2Result(passed=True, elapsed_ms=(time.perf_counter() - t0) * 1000)


def check_export_signature(original: str, patched: str) -> Layer2Result:
    """
    Check 2: Export signature presence.

    Verifies that the default export and named exports present in the
    original file are still present in the patched file.
    """
    t0 = time.perf_counter()

    has_default_orig = bool(_DEFAULT_EXPORT_RE.search(original))
    has_default_patch = bool(_DEFAULT_EXPORT_RE.search(patched))

    if has_default_orig and not has_default_patch:
        return Layer2Result(
            passed=False,
            failed_check="export_signature",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    # Named export count should not decrease
    named_orig = len(_NAMED_EXPORT_RE.findall(original))
    named_patch = len(_NAMED_EXPORT_RE.findall(patched))
    if named_patch < named_orig:
        return Layer2Result(
            passed=False,
            failed_check="export_signature",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    return Layer2Result(passed=True, elapsed_ms=(time.perf_counter() - t0) * 1000)


def check_event_handlers(original: str, patched: str) -> Layer2Result:
    """
    Check 3: Event handler presence.

    Verifies that event handler identifiers present in the original are
    not removed in the patch (they may be added or replaced with equivalent
    handlers, but not silently dropped).
    """
    t0 = time.perf_counter()

    orig_handlers = set(_EVENT_HANDLER_RE.findall(original))
    patch_handlers = set(_EVENT_HANDLER_RE.findall(patched))

    removed_handlers = orig_handlers - patch_handlers
    if removed_handlers:
        return Layer2Result(
            passed=False,
            failed_check="event_handler",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    return Layer2Result(passed=True, elapsed_ms=(time.perf_counter() - t0) * 1000)


def run_layer2(original: str, patched: str) -> Layer2Result:
    """
    Run all Layer 2 checks and return the first failure encountered,
    or a passing result if all checks pass.

    The check order is: prop_interface → export_signature → event_handler.
    """
    for check_fn in (check_prop_interface, check_export_signature, check_event_handlers):
        result = check_fn(original, patched)
        if not result.passed:
            return result

    return Layer2Result(passed=True)
