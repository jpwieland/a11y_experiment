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


# ── Structure-preservation guard (semantic-deletion detector) ──────────────────
#
# run_layer2 above verifies the component's PROGRAMMATIC interface (props,
# exports, handlers). It is blind to the DOCUMENT STRUCTURE — headings,
# landmarks, and visible content. That blindness is exactly the failure mode
# suspected for `semantic` violations: a model can make a heading-order or
# landmark violation "disappear" by DELETING the offending element instead of
# repairing it. Pa11y then credits the fix (the element is gone, so is the
# violation) even though the page regressed.
#
# check_structure_preserved flags a patch that resolves a violation by reducing
# document structure. It is intentionally tolerant of ADDITIONS (a real fix may
# add a landmark or re-tag a heading) and only fails on net REMOVAL beyond a
# tolerance.

_HEADING_RE  = re.compile(r'<h[1-6][\s/>]|role\s*=\s*["\']heading["\']', re.I)
_LANDMARK_RE = re.compile(
    r'<(?:nav|main|header|footer|aside)[\s/>]'
    r'|role\s*=\s*["\'](?:banner|navigation|main|contentinfo|complementary|region)["\']',
    re.I,
)
_TAG_RE      = re.compile(r'<[A-Za-z]')
_STRIP_TAGS_RE = re.compile(r'<[^>]+>')
_STRIP_EXPR_RE = re.compile(r'\{[^{}]*\}')

# Tolerances: a localized accessibility fix should not shrink the file much.
_TEXT_FLOOR_RATIO    = 0.80   # patched visible text must stay >= 80% of original
_ELEMENT_FLOOR_RATIO = 0.70   # patched element count must stay >= 70% of original


def _visible_text_len(code: str) -> int:
    """Rough proxy for visible copy: strip JSX tags and {expressions}, count non-space."""
    t = _STRIP_TAGS_RE.sub(" ", code)
    t = _STRIP_EXPR_RE.sub(" ", t)
    return len("".join(t.split()))


def check_structure_preserved(original: str, patched: str) -> Layer2Result:
    """
    Detect fixes that resolve a violation by deleting document structure.

    Fails (in priority order) when the patch, relative to the original:
      * removes a heading            -> failed_check="heading_removed"
      * removes a landmark           -> failed_check="landmark_removed"
      * truncates visible text >20%  -> failed_check="content_truncation"
      * drops element count >30%     -> failed_check="element_truncation"

    Counts of zero in the original skip the corresponding sub-check, so the
    guard never penalises files that had no headings/landmarks to begin with.
    """
    t0 = time.perf_counter()

    def _fail(check: str) -> Layer2Result:
        return Layer2Result(passed=False, failed_check=check,
                            elapsed_ms=(time.perf_counter() - t0) * 1000)

    h_orig = len(_HEADING_RE.findall(original))
    h_patch = len(_HEADING_RE.findall(patched))
    if h_orig > 0 and h_patch < h_orig:
        return _fail("heading_removed")

    l_orig = len(_LANDMARK_RE.findall(original))
    l_patch = len(_LANDMARK_RE.findall(patched))
    if l_orig > 0 and l_patch < l_orig:
        return _fail("landmark_removed")

    txt_orig = _visible_text_len(original)
    txt_patch = _visible_text_len(patched)
    if txt_orig > 0 and txt_patch < _TEXT_FLOOR_RATIO * txt_orig:
        return _fail("content_truncation")

    el_orig = len(_TAG_RE.findall(original))
    el_patch = len(_TAG_RE.findall(patched))
    if el_orig > 0 and el_patch < _ELEMENT_FLOOR_RATIO * el_orig:
        return _fail("element_truncation")

    return Layer2Result(passed=True, elapsed_ms=(time.perf_counter() - t0) * 1000)
