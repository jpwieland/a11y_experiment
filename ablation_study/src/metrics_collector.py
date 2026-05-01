"""
metrics_collector.py
====================
Thread-safe JSONL persistence for all ablation study records.

One collector instance per (condition, model, repetition) run.
Writes are atomic (line-at-a-time) so the file is readable even if the
process is interrupted mid-run.

Hierarchy of output files (all inside results_dir):
  {condition_id}/{model}/{rep}/attempts.jsonl    — AttemptRecord per line
  {condition_id}/{model}/{rep}/violations.jsonl  — ViolationRecord per line
  {condition_id}/{model}/{rep}/files.jsonl       — FileRecord per line
  {condition_id}/{model}/{rep}/summary.json      — RunSummary (overwritten)
  {condition_id}/{model}/{rep}/prompts.jsonl     — prompt log (optional)
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ablation_study.src.metrics_schema import (
        AttemptRecord,
        FileRecord,
        RunSummary,
        ViolationRecord,
    )


class MetricsCollector:
    """
    Writes ablation metrics to JSONL files with per-file locking.

    One instance per run (condition × model × repetition).  All public
    methods are thread-safe; internal writes use per-file locks so that
    concurrent attempt workers do not interleave lines.
    """

    def __init__(
        self,
        results_dir: Path,
        condition_id: str,
        model_name: str,
        repetition: int,
        *,
        write_prompt_log: bool = True,
        prompt_log_max_chars: int = 8000,
    ) -> None:
        self.run_dir = results_dir / condition_id / model_name / f"rep{repetition}"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self._attempts_path   = self.run_dir / "attempts.jsonl"
        self._violations_path = self.run_dir / "violations.jsonl"
        self._files_path      = self.run_dir / "files.jsonl"
        self._summary_path    = self.run_dir / "summary.json"
        self._prompts_path    = self.run_dir / "prompts.jsonl"

        self._write_prompt_log     = write_prompt_log
        self._prompt_log_max_chars = prompt_log_max_chars

        # One lock per output file to serialise concurrent writes
        self._lock_attempts   = threading.Lock()
        self._lock_violations = threading.Lock()
        self._lock_files      = threading.Lock()
        self._lock_prompts    = threading.Lock()

    # ── Public write methods ──────────────────────────────────────────────────

    def write_attempt(self, record: "AttemptRecord") -> None:
        self._append_jsonl(self._attempts_path, self._lock_attempts, record)

    def write_violation(self, record: "ViolationRecord") -> None:
        self._append_jsonl(self._violations_path, self._lock_violations, record)

    def write_file(self, record: "FileRecord") -> None:
        self._append_jsonl(self._files_path, self._lock_files, record)

    def write_summary(self, record: "RunSummary") -> None:
        """Overwrites the summary file (one summary per run)."""
        data = dataclasses.asdict(record)  # type: ignore[arg-type]
        tmp = self._summary_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self._summary_path)

    def write_prompt_log(
        self,
        violation_id: str,
        attempt_number: int,
        prompt: str,
        response: str,
    ) -> None:
        if not self._write_prompt_log:
            return
        entry = {
            "timestamp": _now_iso(),
            "violation_id": violation_id,
            "attempt_number": attempt_number,
            "prompt": prompt[: self._prompt_log_max_chars],
            "response": response[: self._prompt_log_max_chars],
        }
        with self._lock_prompts:
            with self._prompts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _append_jsonl(path: Path, lock: threading.Lock, record: object) -> None:
        data = dataclasses.asdict(record)  # type: ignore[arg-type]
        line = json.dumps(data, ensure_ascii=False, default=str) + "\n"
        with lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)


# ── Aggregation helpers ────────────────────────────────────────────────────────

def aggregate_violation_record(
    attempts: "list[AttemptRecord]",
    run_id: str,
    condition_id: str,
    model_name: str,
    repetition: int,
) -> "ViolationRecord":
    """Build a ViolationRecord by collapsing a list of AttemptRecords."""
    from ablation_study.src.metrics_schema import ViolationRecord

    if not attempts:
        raise ValueError("attempts list is empty")

    first = attempts[0]
    resolved_attempt = next(
        (a for a in attempts if a.attempt_success), None
    )

    total_time = sum(a.time_total_s for a in attempts)
    tokens_out = sum(a.tokens_output for a in attempts)
    tokens_in  = sum(a.tokens_input_estimated for a in attempts)

    agents_used = {a.agent_used for a in attempts}

    return ViolationRecord(
        run_id=run_id,
        condition_id=condition_id,
        model_name=model_name,
        repetition=repetition,
        violation_id=first.violation_id,
        file_path=first.file_path,
        wcag_criterion=first.wcag_criterion,
        wcag_category=first.wcag_category,
        confidence=first.confidence,
        n_tools_detected=first.n_tools_detected,
        resolved=resolved_attempt is not None,
        resolved_on_attempt=resolved_attempt.attempt_number if resolved_attempt else None,
        total_attempts=len(attempts),
        retries_exhausted=resolved_attempt is None,
        attempt1_success=attempts[0].attempt_success if attempts else False,
        attempt1_failure_layer=attempts[0].failure_layer if attempts else None,
        attempt1_failure_type=attempts[0].failure_type if attempts else "",
        tokens_output_total=tokens_out,
        tokens_input_estimated_total=tokens_in,
        time_total_s=total_time,
        agent_primary_used=attempts[0].agent_used,
        escalated_to_openhands="openhands" in agents_used and len(agents_used) > 1,
    )


def aggregate_file_record(
    violations: "list[ViolationRecord]",
    file_path: str,
    run_id: str,
    condition_id: str,
    model_name: str,
    repetition: int,
) -> "FileRecord":
    """Build a FileRecord from a list of ViolationRecords for the same file."""
    from ablation_study.src.metrics_schema import FileRecord

    n_violations = len(violations)
    n_resolved   = sum(1 for v in violations if v.resolved)
    n_pending    = n_violations - n_resolved

    categories = list({v.wcag_category for v in violations})
    high   = sum(1 for v in violations if v.confidence == "HIGH")
    std    = sum(1 for v in violations if v.confidence == "STANDARD")

    mttr   = sum(v.time_total_s for v in violations)
    tokens = sum(v.tokens_output_total for v in violations)

    return FileRecord(
        run_id=run_id,
        condition_id=condition_id,
        model_name=model_name,
        repetition=repetition,
        file_path=file_path,
        n_violations=n_violations,
        n_resolved=n_resolved,
        n_pending=n_pending,
        sr=n_pending == 0,
        file_ifr=n_resolved / n_violations if n_violations else 0.0,
        wcag_categories_present=sorted(categories),
        high_confidence_violations=high,
        standard_confidence_violations=std,
        mttr_s=mttr,
        tokens_output_total=tokens,
    )


def build_run_summary(
    violation_records: "list[ViolationRecord]",
    attempt_records: "list[AttemptRecord]",
    file_records: "list[FileRecord]",
    run_id: str,
    condition_id: str,
    condition_label: str,
    components_active: "list[str]",
    model_name: str,
    repetition: int,
    seed: int,
    git_hash: str,
    timestamp_start: str,
    timestamp_end: str,
    wall_clock_total_s: float,
) -> "RunSummary":
    """Compute all RunSummary fields from lower-granularity records."""
    from ablation_study.src.metrics_schema import RunSummary

    n_total    = len(violation_records)
    n_resolved = sum(1 for v in violation_records if v.resolved)
    ifr        = n_resolved / n_total if n_total else 0.0

    n_files            = len(file_records)
    n_files_all_resolved = sum(1 for f in file_records if f.sr)
    sr_violation_files = n_files_all_resolved / n_files if n_files else 0.0

    n_attempts = len(attempt_records)

    def _fail_n(layer: int) -> int:
        return sum(1 for a in attempt_records if a.failure_layer == layer)

    def _fail_rate(layer: int) -> float:
        n = _fail_n(layer)
        return n / n_attempts if n_attempts else 0.0

    layer4_fail_n = sum(
        1 for a in attempt_records
        if not a.layer4_quality_pass and a.attempt_success  # passed layers 1-3 but not 4
    )

    tokens_out = sum(a.tokens_output for a in attempt_records)
    tokens_in  = sum(a.tokens_input_estimated for a in attempt_records)
    tokens_out_per_fix = tokens_out / n_resolved if n_resolved else 0.0
    tokens_total_per_fix = (tokens_out + tokens_in) / n_resolved if n_resolved else 0.0

    def _cat_ifr(cat: str) -> float:
        sub = [v for v in violation_records if v.wcag_category == cat]
        if not sub:
            return 0.0
        return sum(1 for v in sub if v.resolved) / len(sub)

    n_high     = sum(1 for v in violation_records if v.confidence == "HIGH")
    n_high_res = sum(1 for v in violation_records if v.confidence == "HIGH" and v.resolved)
    n_std      = sum(1 for v in violation_records if v.confidence == "STANDARD")
    n_std_res  = sum(1 for v in violation_records if v.confidence == "STANDARD" and v.resolved)

    ifr_first = (
        sum(1 for v in violation_records if v.attempt1_success) / n_total
        if n_total else 0.0
    )

    n_swe      = sum(1 for a in attempt_records if a.agent_used == "swe-agent")
    n_oh       = sum(1 for a in attempt_records if a.agent_used == "openhands")
    n_oh_succ  = sum(
        1 for a in attempt_records
        if a.agent_used == "openhands" and a.attempt_success
    )

    return RunSummary(
        run_id=run_id,
        condition_id=condition_id,
        condition_label=condition_label,
        components_active=components_active,
        model_name=model_name,
        repetition=repetition,
        seed=seed,
        git_hash=git_hash,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        wall_clock_total_s=wall_clock_total_s,
        n_violations_total=n_total,
        n_violations_resolved=n_resolved,
        ifr=ifr,
        n_files_total=n_files,
        n_files_all_resolved=n_files_all_resolved,
        sr_violation_files=sr_violation_files,
        n_attempts_total=n_attempts,
        layer1_fail_n=_fail_n(1),
        layer1_fail_rate=_fail_rate(1),
        layer2_fail_n=_fail_n(2),
        layer2_fail_rate=_fail_rate(2),
        layer3_fail_n=_fail_n(3),
        layer3_fail_rate=_fail_rate(3),
        layer4_fail_n=layer4_fail_n,
        layer4_fail_rate=layer4_fail_n / n_attempts if n_attempts else 0.0,
        n_invalid_patch=sum(1 for a in attempt_records if a.failure_type == "invalid_patch"),
        n_functional_regression=sum(1 for a in attempt_records if a.failure_type == "functional_regression"),
        n_domain_violation=sum(1 for a in attempt_records if a.failure_type == "domain_violation"),
        n_retry_exhausted=sum(1 for v in violation_records if v.retries_exhausted),
        tokens_output_total=tokens_out,
        tokens_input_estimated_total=tokens_in,
        tokens_output_per_fix=tokens_out_per_fix,
        tokens_total_per_fix=tokens_total_per_fix,
        mttr_s=(
            sum(f.mttr_s for f in file_records) / n_files if n_files else 0.0
        ),
        mean_inference_s=(
            sum(a.time_inference_s for a in attempt_records) / n_attempts
            if n_attempts else 0.0
        ),
        mean_validation_s=(
            sum(a.time_validation_s for a in attempt_records) / n_attempts
            if n_attempts else 0.0
        ),
        mean_chromium_coldstart_s=(
            sum(a.chromium_coldstart_s for a in attempt_records) / n_attempts
            if n_attempts else 0.0
        ),
        n_swe_agent_sessions=n_swe,
        n_openhands_sessions=n_oh,
        openhands_success_rate=n_oh_succ / n_oh if n_oh else 0.0,
        ifr_alt_text=_cat_ifr("alt_text"),
        ifr_label=_cat_ifr("label"),
        ifr_semantic=_cat_ifr("semantic"),
        ifr_contrast=_cat_ifr("contrast"),
        ifr_aria=_cat_ifr("aria"),
        ifr_keyboard=_cat_ifr("keyboard"),
        ifr_focus=_cat_ifr("focus"),
        n_high_confidence=n_high,
        n_high_confidence_resolved=n_high_res,
        ifr_high_confidence=n_high_res / n_high if n_high else 0.0,
        n_standard_confidence=n_std,
        n_standard_confidence_resolved=n_std_res,
        ifr_standard_confidence=n_std_res / n_std if n_std else 0.0,
        ifr_first_attempt=ifr_first,
        mean_prompt_chars=(
            sum(a.prompt_char_count for a in attempt_records) / n_attempts
            if n_attempts else 0.0
        ),
        mean_tokens_input_estimated=(
            sum(a.tokens_input_estimated for a in attempt_records) / n_attempts
            if n_attempts else 0.0
        ),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
