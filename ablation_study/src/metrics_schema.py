"""
metrics_schema.py
=================
Dataclasses defining every parameter captured per repair attempt.
Each field maps to a column in the final analysis dataset.

Granularity levels:
  AttemptRecord   — one record per (violation × retry_attempt)
  ViolationRecord — one record per violation (aggregated over retries)
  FileRecord      — one record per file (aggregated over violations)
  RunSummary      — one record per (condition × model × repetition)

All records are JSON-serialisable via dataclasses.asdict().
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


# ── Attempt-level (finest granularity) ───────────────────────────────────────

@dataclass
class AttemptRecord:
    """
    One record per repair attempt (= one LLM call for one violation).
    A violation may have up to max_retries_per_file attempts.
    """

    # ── Identity ──
    record_type: Literal["attempt"] = field(default="attempt", init=False)
    run_id: str = ""                    # UUID for the full run
    condition_id: str = ""              # e.g. "full", "minus_role"
    model_name: str = ""                # e.g. "qwen2.5-coder-3b"
    repetition: int = 0                 # 1, 2, or 3
    seed: int = 0                       # random seed for this repetition
    git_hash: str = ""                  # repo commit at run time

    # ── Violation identity ──
    violation_id: str = ""              # "<file_path>:<selector>:<wcag>"
    file_path: str = ""                 # relative path from project root
    wcag_criterion: str = ""            # e.g. "1.1.1"
    wcag_category: str = ""             # "alt_text" | "label" | ... (7 categories)
    confidence: str = ""               # "HIGH" | "STANDARD"
    n_tools_detected: int = 0           # 1 = STANDARD, ≥2 = HIGH
    tools_detected_by: list[str] = field(default_factory=list)  # ["pa11y","axe"]

    # ── Attempt metadata ──
    attempt_number: int = 0            # 1, 2, or 3 (retry index)
    agent_used: str = ""               # "swe-agent" | "openhands"
    timestamp_start: str = ""          # ISO-8601
    timestamp_end: str = ""            # ISO-8601

    # ── Prompt composition ──
    condition_components_active: list[str] = field(default_factory=list)
    prompt_char_count: int = 0
    tokens_input_estimated: int = 0    # estimated from char_count / 4

    # ── LLM output ──
    tokens_output: int = 0
    response_truncated: bool = False   # output_format missing → heuristic parse
    json_parse_success: bool = False   # only relevant when output_format active

    # ── Extraction & diagnosis ──
    response_chars: int = 0             # char count do raw LLM response
    patched_excerpt: str = ""           # primeiros 3000 chars do patch extraído (diagnóstico L1-L3)

    # ── Validation layers ──
    layer1_syntax_pass: bool = False
    layer1_error_msg: str = ""
    layer2_functional_pass: bool = False
    layer2_error_msg: str = ""         # changed props/state/handlers
    layer3_domain_pass: bool = False
    layer3_error_msg: str = ""         # Pa11y re-scan failure
    layer3_new_violations_introduced: int = 0
    layer4_quality_pass: bool = False
    layer4_eslint_errors: int = 0
    layer4_complexity_violations: int = 0

    # ── Outcome ──
    attempt_success: bool = False      # all hard layers (1-3) passed
    failure_layer: Optional[int] = None   # 1, 2, 3, or None (success / layer-4 only)
    failure_type: str = ""             # "invalid_patch" | "functional_regression" |
                                       # "domain_violation" | "quality_only" | ""

    # ── Ablation-specific flags ──
    validation_bypassed: bool = False   # True when skip_internal_validation=True (arch_no_internal_validation)
    reflection_feedback_active: bool = True  # False in arch_no_reflection condition

    # ── Timing (seconds) ──
    time_total_s: float = 0.0
    time_detection_s: float = 0.0      # Pa11y/Axe initial scan (from cache if available)
    time_inference_s: float = 0.0      # LLM call wall-clock
    time_validation_s: float = 0.0    # layers 1-4 total
    time_layer1_s: float = 0.0
    time_layer2_s: float = 0.0
    time_layer3_s: float = 0.0        # includes Chromium cold-start
    time_layer4_s: float = 0.0
    chromium_coldstart_s: float = 0.0  # browser launch overhead


# ── Violation-level (aggregated over retries) ─────────────────────────────────

@dataclass
class ViolationRecord:
    """
    One record per violation; aggregates over up to 3 retry attempts.
    The 'resolved' flag is True if any attempt succeeded.
    """

    record_type: Literal["violation"] = field(default="violation", init=False)
    run_id: str = ""
    condition_id: str = ""
    model_name: str = ""
    repetition: int = 0
    seed: int = 0                       # random seed — required for cross-run traceability
    git_hash: str = ""                  # repo commit — required for reproducibility audits

    violation_id: str = ""
    file_path: str = ""
    wcag_criterion: str = ""
    wcag_category: str = ""
    confidence: str = ""
    n_tools_detected: int = 0

    resolved: bool = False             # primary outcome variable
    resolved_on_attempt: Optional[int] = None  # 1, 2, or 3
    total_attempts: int = 0
    retries_exhausted: bool = False    # reached max_retries without success

    # First-attempt outcome (for single-pass analysis)
    attempt1_success: bool = False
    attempt1_failure_layer: Optional[int] = None
    attempt1_failure_type: str = ""

    # Ablation-specific flags (propagated from AttemptRecord for analysis filtering)
    validation_bypassed: bool = False   # True in arch_no_internal_validation — resolved means non-empty output only
    reflection_feedback_active: bool = True  # False in arch_no_reflection

    # Functional preservation (Layer 2) — measured for ALL conditions, including V5
    # where it is recorded but NOT enforced. Lets us decompose Pa11y IFR into
    # functionally-clean fixes vs. fixes that introduced a structural regression.
    functional_regression: bool = False         # the patch that resolved this issue failed L2 functional preservation
    resolved_functional_clean: bool = False      # resolved by Pa11y AND functionally clean (corrected IFR numerator)

    # Cumulative tokens across all attempts for this violation
    tokens_output_total: int = 0
    tokens_input_estimated_total: int = 0
    time_total_s: float = 0.0

    # Agent routing
    agent_primary_used: str = ""       # swe-agent or openhands
    escalated_to_openhands: bool = False


# ── File-level (aggregated over violations within a file) ────────────────────

@dataclass
class FileRecord:
    """
    One record per file; aggregated over all violations in the file.
    SR (file-level success) = all violations resolved.
    """

    record_type: Literal["file"] = field(default="file", init=False)
    run_id: str = ""
    condition_id: str = ""
    model_name: str = ""
    repetition: int = 0

    file_path: str = ""
    n_violations: int = 0
    n_resolved: int = 0
    n_pending: int = 0
    sr: bool = False                   # True iff all violations resolved
    file_ifr: float = 0.0             # n_resolved / n_violations

    wcag_categories_present: list[str] = field(default_factory=list)
    high_confidence_violations: int = 0
    standard_confidence_violations: int = 0

    mttr_s: float = 0.0               # total time for this file (all violations)
    tokens_output_total: int = 0


# ── Run summary (one per condition × model × repetition) ─────────────────────

@dataclass
class RunSummary:
    """
    Aggregated statistics for a complete (condition × model × repetition) run.
    Primary object for statistical analysis.
    """

    record_type: Literal["run_summary"] = field(default="run_summary", init=False)
    run_id: str = ""
    condition_id: str = ""
    condition_label: str = ""
    components_active: list[str] = field(default_factory=list)
    model_name: str = ""
    repetition: int = 0
    seed: int = 0
    git_hash: str = ""

    timestamp_start: str = ""
    timestamp_end: str = ""
    wall_clock_total_s: float = 0.0

    # ── Primary metrics ──
    n_violations_total: int = 0
    n_violations_resolved: int = 0
    ifr: float = 0.0                   # n_resolved / n_total  (primary metric)

    n_files_total: int = 0
    n_files_all_resolved: int = 0
    sr_violation_files: float = 0.0    # fraction of files where ALL resolved

    # ── Layer failure rates ──
    n_attempts_total: int = 0
    layer1_fail_n: int = 0
    layer1_fail_rate: float = 0.0
    layer2_fail_n: int = 0
    layer2_fail_rate: float = 0.0
    layer3_fail_n: int = 0
    layer3_fail_rate: float = 0.0
    layer4_fail_n: int = 0             # soft; does not block
    layer4_fail_rate: float = 0.0

    # ── Failure type breakdown ──
    n_invalid_patch: int = 0
    n_functional_regression: int = 0
    n_domain_violation: int = 0
    n_retry_exhausted: int = 0

    # ── Token metrics ──
    tokens_output_total: int = 0
    tokens_input_estimated_total: int = 0
    tokens_output_per_fix: float = 0.0
    tokens_total_per_fix: float = 0.0  # (output + input_estimated) / n_resolved

    # ── Timing ──
    mttr_s: float = 0.0               # mean wall-clock seconds per file
    mean_inference_s: float = 0.0
    mean_validation_s: float = 0.0
    mean_chromium_coldstart_s: float = 0.0

    # ── Agent routing ──
    n_swe_agent_sessions: int = 0
    n_openhands_sessions: int = 0
    openhands_success_rate: float = 0.0

    # ── Per-category IFR (7 categories) ──
    ifr_alt_text: float = 0.0
    ifr_label: float = 0.0
    ifr_semantic: float = 0.0
    ifr_contrast: float = 0.0
    ifr_aria: float = 0.0
    ifr_keyboard: float = 0.0
    ifr_focus: float = 0.0

    # ── HIGH vs STANDARD confidence IFR ──
    n_high_confidence: int = 0
    n_high_confidence_resolved: int = 0
    ifr_high_confidence: float = 0.0
    n_standard_confidence: int = 0
    n_standard_confidence_resolved: int = 0
    ifr_standard_confidence: float = 0.0

    # ── First-attempt resolution rate (no retries) ──
    ifr_first_attempt: float = 0.0

    # ── Prompt stats ──
    mean_prompt_chars: float = 0.0
    mean_tokens_input_estimated: float = 0.0
    mean_response_chars: float = 0.0

    # ── Ablation-specific counters (for cross-condition analysis) ──
    n_validation_bypassed: int = 0      # attempts where L1-L3 were skipped (V5)
    n_reflection_feedback_inactive: int = 0  # attempts without rejection feedback (V2)


# ── File-level attempt (arch ablation pipeline format) ───────────────────────

@dataclass
class ArchFileAttemptRecord:
    """
    One record per file-level LLM attempt in the arch ablation pipeline format.

    In the arch ablation pipeline format, each LLM call targets ALL pending
    violations in the file (as in the main experiment). This record stores
    the attempt-level details; per-violation outcomes are stored in ViolationRecord.

    Contrast with AttemptRecord (single-violation format used by the original
    ablation runner): ArchFileAttemptRecord is at file granularity.
    """

    record_type: Literal["arch_file_attempt"] = field(
        default="arch_file_attempt", init=False
    )

    # ── Identity ──
    run_id: str = ""
    condition_id: str = ""
    model_name: str = ""
    repetition: int = 0
    seed: int = 0
    git_hash: str = ""

    # ── File identity ──
    file_path: str = ""
    attempt_number: int = 0           # 1, 2, 3 (file-level retry)
    timestamp_start: str = ""
    timestamp_end: str = ""

    # ── Issues in this attempt ──
    n_issues_targeted: int = 0        # pending issues sent to LLM in this attempt
    n_issues_total_in_file: int = 0   # total issues in the file (all attempts combined)

    # ── Prompt composition ──
    condition_components_active: list[str] = field(default_factory=list)
    prompt_char_count: int = 0
    tokens_input_estimated: int = 0
    tokens_output: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    response_chars: int = 0
    patched_excerpt: str = ""         # first 3000 chars of extracted patch

    # ── Ablation-specific flags ──
    validation_bypassed: bool = False
    reflection_feedback_active: bool = True

    # ── Validation layers ──
    patch_extracted: bool = False     # LLM returned parseable code block
    layer1_syntax_pass: bool = False
    layer1_error_msg: str = ""
    layer2_functional_pass: bool = False
    layer2_error_msg: str = ""
    layer3_domain_pass: bool = False
    layer3_error_msg: str = ""
    layer4_quality_pass: bool = False
    validation_rejected_layer: Optional[int] = None
    validation_failure_reason: str = ""

    # ── Patch accepted (written to disk) ──
    patch_accepted: bool = False

    # ── Pa11y re-scan outcome ──
    rescan_performed: bool = False
    rescan_failed: bool = False
    rescan_error_msg: str = ""
    n_issues_resolved_this_attempt: int = 0   # violations credited by Pa11y re-scan
    n_selector_migrations: int = 0            # issues that vanished but weren't credited

    # ── Timing (seconds) ──
    time_total_s: float = 0.0
    time_inference_s: float = 0.0
    time_validation_s: float = 0.0
    time_rescan_s: float = 0.0


def to_dict(record: object) -> dict:
    """Convert any record dataclass to a JSON-serialisable dict."""
    return dataclasses.asdict(record)  # type: ignore[arg-type]
