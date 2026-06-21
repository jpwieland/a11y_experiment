"""
arch_ablation_pipeline_runner.py
=================================
Arch ablation study in MAIN-EXPERIMENT FORMAT.

Each condition runs the same repair loop as the main pipeline:
  - All violations in a file are sent to the LLM in a single call
  - L1-L4 validation gates each attempt (bypassed for V5)
  - Pa11y re-scan after each accepted patch → only credits issues that
    actually disappeared from the scan (same oracle as the main experiment)
  - File is restored to its original content after processing

This produces IFR values directly comparable to the main experiment (~54 %),
avoiding the ~40 pp ceiling effect of the single-violation ablation format.

Conditions processed (from arch_ablation_pipeline_config.yaml):
  arch_full                    V1 — full pipeline (control)
  arch_no_reflection           V2 — no rejection feedback on retries
  arch_no_few_shot             V3 — zero-shot (no few-shot examples)
  arch_no_wcag_guidelines      V4 — raw rule_id only, no semantic descriptions
  arch_no_internal_validation  V5 — L1-L3 bypassed; Pa11y measures true IFR

Statistical design:
  Same corpus, model, temperature, seed per repetition as the prompt ablation.
  Paired Wilcoxon signed-rank, Holm-Bonferroni, 4 comparisons (V2-V5 vs V1).

Usage:
  python -m ablation_study.src.arch_ablation_pipeline_runner
  python -m ablation_study.src.arch_ablation_pipeline_runner --condition arch_full --rep 1
  python -m ablation_study.src.arch_ablation_pipeline_runner --reset --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from a11y_autofix.agents.base import BaseAgent
from a11y_autofix.config import (
    A11yIssue,
    AgentTask,
    AgentType,
    Confidence,
    IssueType,
    PatchResult,
    Settings,
    ScanResult,
)
from a11y_autofix.pipeline import Pipeline
from a11y_autofix.validation.layer2 import run_layer2, check_structure_preserved

from ablation_study.src.ablation_conditions import (
    CONDITIONS,
    AblationCondition,
    build_ablation_prompt_multi,
    components_active,
)
from ablation_study.src.metrics_collector import MetricsCollector
from ablation_study.src.metrics_schema import (
    ArchFileAttemptRecord,
    RunSummary,
    ViolationRecord,
)
from ablation_study.src.ablation_runner import (
    StatusWriter,
    _check_ollama,
    _git_hash,
    _model_config_from_yaml,
    _now_iso,
    _vprint,
    _vsection,
    _vstep,
)

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_ARCH_CONDITION_IDS = frozenset({
    "arch_full",
    "arch_no_reflection",
    "arch_no_few_shot",
    "arch_no_wcag_guidelines",
    "arch_no_internal_validation",
})

_CODE_FENCE_RE = re.compile(
    r'```(?:tsx|jsx|typescript|javascript|ts|js)?\n(.*?)```',
    re.DOTALL,
)

_WCAG_LEVEL = "WCAG2AA"


def _extract_code(response: str) -> str:
    m = _CODE_FENCE_RE.search(response)
    if m:
        return m.group(1).strip()
    return response.strip()


# ── Ablation-specific LLM agent ───────────────────────────────────────────────

class AblationDirectLLMAgent(BaseAgent):
    """
    Subclass of BaseAgent that builds ablation prompts via
    build_ablation_prompt_multi() rather than PromptBuilder.
    Condition flags control which prompt components are included.
    """

    def __init__(self, llm_client, condition: AblationCondition) -> None:
        super().__init__(llm_client)
        self.condition = condition
        self._last_response: str = ""
        self._last_metrics: dict = {}
        self._last_prompt: str = ""

    def name(self) -> str:
        return f"ablation-direct-llm/{self.condition.id}"

    async def run(
        self,
        task: AgentTask,
        previous_rejection: dict | None = None,
    ) -> PatchResult:
        prompt = build_ablation_prompt_multi(
            task=task,
            condition=self.condition,
            previous_rejection=previous_rejection,
        )
        self._last_prompt = prompt

        system_prompt = (
            "You are an expert accessibility engineer specialising in React/TypeScript "
            "and WCAG 2.1/2.2 conformance. Fix ALL listed violations and return the "
            "COMPLETE corrected file. Follow the constraints and output format exactly."
        )

        try:
            response, metrics = await self.llm.complete_with_metrics(
                system=system_prompt,
                user=prompt,
            )
        except Exception as exc:
            self._last_response = ""
            self._last_metrics = {}
            log.error("ablation_agent_llm_failed", error=str(exc)[:200])
            return PatchResult(success=False, error=str(exc)[:300])

        self._last_response = response
        self._last_metrics = metrics or {}

        new_content = _extract_code(response)
        if not new_content:
            return PatchResult(
                success=False,
                error="LLM did not return a valid code block",
                tokens_prompt=metrics.get("tokens_prompt"),
                tokens_completion=metrics.get("tokens_completion"),
                time_seconds=metrics.get("time_seconds", 0.0),
            )

        try:
            from a11y_autofix.utils.git import get_unified_diff
            diff = get_unified_diff(task.file_content, new_content, task.file.name)
        except Exception:
            diff = ""

        return PatchResult(
            success=True,
            new_content=new_content,
            diff=diff,
            tokens_prompt=metrics.get("tokens_prompt"),
            tokens_completion=metrics.get("tokens_completion"),
            time_seconds=metrics.get("time_seconds", 0.0),
        )


# ── Corpus loading ────────────────────────────────────────────────────────────

_ISSUE_TYPE_MAP: dict[str, str] = {
    "alt-text": "alt-text", "alt_text": "alt-text",
    "label": "label", "semantic": "semantic", "contrast": "contrast",
    "aria": "aria", "keyboard": "keyboard", "focus": "focus",
}

_COMPLEXITY_MAP: dict[str, str] = {
    "simple": "simple", "moderate": "moderate", "complex": "complex",
}

_CONFIDENCE_MAP: dict[str, str] = {
    "high": "high", "medium": "medium", "low": "low",
    "HIGH": "high", "STANDARD": "medium",
}


def _make_issue(raw: dict, file_path: str) -> A11yIssue | None:
    try:
        issue_type_raw = _ISSUE_TYPE_MAP.get(raw.get("issue_type", "aria"), "aria")
        try:
            issue_type = IssueType(issue_type_raw)
        except ValueError:
            issue_type = IssueType.OTHER

        from a11y_autofix.config import Complexity
        complexity_raw = _COMPLEXITY_MAP.get(raw.get("complexity", "moderate"), "moderate")
        try:
            complexity = Complexity(complexity_raw)
        except ValueError:
            complexity = Complexity.MODERATE

        confidence_raw = _CONFIDENCE_MAP.get(raw.get("confidence", "medium"), "medium")
        try:
            confidence = Confidence(confidence_raw)
        except ValueError:
            confidence = Confidence.LOW

        issue = A11yIssue(
            issue_id=raw.get("issue_id", ""),
            file=file_path,
            selector=raw.get("selector", ""),
            issue_type=issue_type,
            complexity=complexity,
            wcag_criteria=raw.get("wcag_criteria") or raw.get("wcag_criterion"),
            impact=raw.get("impact", "moderate"),
            confidence=confidence,
            found_by=[],
            tool_consensus=raw.get("tool_consensus", 1),
            findings=[],
            message=raw.get("message", ""),
            context=raw.get("context", ""),
            resolved=False,
        )

        if not issue.issue_id:
            issue.compute_id()

        return issue
    except Exception as exc:
        log.warning("issue_construction_failed", error=str(exc)[:100])
        return None


def load_corpus(
    corpus_results_dir: Path,
    selected_projects: frozenset[str] | None = None,
    subset_size: int | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Load corpus files as dicts with A11yIssue objects."""
    scan_files = sorted(corpus_results_dir.glob("*/scan_results.json"))
    if not scan_files:
        raise FileNotFoundError(f"No scan_results.json under {corpus_results_dir}")

    if selected_projects:
        scan_files = [f for f in scan_files if f.parent.name in selected_projects]

    entries: list[dict] = []
    for scan_file in scan_files:
        try:
            data = json.loads(scan_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("scan_file_error", path=str(scan_file), error=str(exc)[:80])
            continue

        for file_entry in data:
            file_path = file_entry.get("file", "")
            unresolved_raw = [
                i for i in file_entry.get("issues", [])
                if not i.get("resolved", False)
            ]
            if not unresolved_raw:
                continue

            issues = [_make_issue(r, file_path) for r in unresolved_raw]
            issues = [i for i in issues if i is not None]
            if not issues:
                continue

            content = ""
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except Exception:
                pass

            entries.append({"file_path": file_path, "file_content": content, "issues": issues})

    if not entries:
        raise FileNotFoundError(f"No files with violations under {corpus_results_dir}")

    n_violations = sum(len(e["issues"]) for e in entries)
    _vstep("  ✓", "Files:", str(len(entries)))
    _vstep("  ✓", "Violations:", str(n_violations))

    if subset_size is not None and subset_size < len(entries):
        rng = random.Random(seed)
        entries = rng.sample(entries, subset_size)
        _vstep("  ✂", "Subsetted to", str(subset_size))

    return entries


# ── Summary builder (direct — no per-violation AttemptRecord needed) ──────────

def _build_arch_run_summary(
    violations: list[ViolationRecord],
    file_attempts: list[ArchFileAttemptRecord],
    file_groups: dict[str, list[ViolationRecord]],
    run_id: str,
    condition: AblationCondition,
    model_name: str,
    repetition: int,
    seed: int,
    git_hash: str,
    ts_start: str,
    ts_end: str,
    wall_s: float,
) -> RunSummary:
    n_total = len(violations)
    n_resolved = sum(1 for v in violations if v.resolved)
    ifr = n_resolved / n_total if n_total else 0.0

    n_files = len(file_groups)
    n_files_sr = sum(
        1 for vrs in file_groups.values()
        if vrs and all(v.resolved for v in vrs)
    )

    n_atts = len(file_attempts)

    def _fail_n(layer: int) -> int:
        return sum(1 for a in file_attempts if a.validation_rejected_layer == layer)

    l1_n, l2_n, l3_n = _fail_n(1), _fail_n(2), _fail_n(3)
    l4_n = sum(1 for a in file_attempts if not a.layer4_quality_pass)

    tokens_out = sum(a.tokens_completion for a in file_attempts)
    tokens_in = sum(a.tokens_input_estimated for a in file_attempts)

    def _cat_ifr(cat: str) -> float:
        sub = [v for v in violations if v.wcag_category == cat]
        return sum(1 for v in sub if v.resolved) / len(sub) if sub else 0.0

    n_high = sum(1 for v in violations if v.confidence == "HIGH")
    n_high_res = sum(1 for v in violations if v.confidence == "HIGH" and v.resolved)
    n_std = sum(1 for v in violations if v.confidence == "STANDARD")
    n_std_res = sum(1 for v in violations if v.confidence == "STANDARD" and v.resolved)

    return RunSummary(
        run_id=run_id,
        condition_id=condition.id,
        condition_label=condition.label,
        components_active=components_active(condition),
        model_name=model_name,
        repetition=repetition,
        seed=seed,
        git_hash=git_hash,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        wall_clock_total_s=wall_s,
        n_violations_total=n_total,
        n_violations_resolved=n_resolved,
        ifr=ifr,
        n_files_total=n_files,
        n_files_all_resolved=n_files_sr,
        sr_violation_files=n_files_sr / n_files if n_files else 0.0,
        n_attempts_total=n_atts,
        layer1_fail_n=l1_n,
        layer1_fail_rate=l1_n / n_atts if n_atts else 0.0,
        layer2_fail_n=l2_n,
        layer2_fail_rate=l2_n / n_atts if n_atts else 0.0,
        layer3_fail_n=l3_n,
        layer3_fail_rate=l3_n / n_atts if n_atts else 0.0,
        layer4_fail_n=l4_n,
        layer4_fail_rate=l4_n / n_atts if n_atts else 0.0,
        n_invalid_patch=sum(1 for a in file_attempts if not a.patch_extracted),
        n_functional_regression=l2_n,
        n_domain_violation=l3_n,
        n_retry_exhausted=sum(1 for v in violations if v.retries_exhausted),
        tokens_output_total=tokens_out,
        tokens_input_estimated_total=tokens_in,
        tokens_output_per_fix=tokens_out / n_resolved if n_resolved else 0.0,
        tokens_total_per_fix=(tokens_out + tokens_in) / n_resolved if n_resolved else 0.0,
        mttr_s=0.0,
        mean_inference_s=sum(a.time_inference_s for a in file_attempts) / n_atts if n_atts else 0.0,
        mean_validation_s=sum(a.time_validation_s for a in file_attempts) / n_atts if n_atts else 0.0,
        mean_chromium_coldstart_s=0.0,
        n_swe_agent_sessions=0,
        n_openhands_sessions=0,
        openhands_success_rate=0.0,
        ifr_alt_text=_cat_ifr("alt-text"),
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
        ifr_first_attempt=(
            sum(1 for v in violations if v.attempt1_success) / n_total if n_total else 0.0
        ),
        mean_prompt_chars=sum(a.prompt_char_count for a in file_attempts) / n_atts if n_atts else 0.0,
        mean_tokens_input_estimated=sum(a.tokens_input_estimated for a in file_attempts) / n_atts if n_atts else 0.0,
        mean_response_chars=sum(a.response_chars for a in file_attempts) / n_atts if n_atts else 0.0,
        n_validation_bypassed=sum(1 for a in file_attempts if a.validation_bypassed),
        n_reflection_feedback_inactive=sum(1 for a in file_attempts if not a.reflection_feedback_active),
    )


# ── Per-file repair logic ─────────────────────────────────────────────────────

async def _repair_file(
    entry: dict[str, Any],
    agent: AblationDirectLLMAgent,
    pipeline: Pipeline,
    condition: AblationCondition,
    run_id: str,
    model_name: str,
    repetition: int,
    seed: int,
    git_hash: str,
    max_retries: int,
    timeout_s: int,
    collector: MetricsCollector,
) -> tuple[list[ViolationRecord], list[ArchFileAttemptRecord]]:
    """
    Repair all violations in one file in pipeline format.
    File is always restored to original content after processing.
    """
    from a11y_autofix.validation.pipeline import ValidationPipeline

    file_path = Path(entry["file_path"])
    all_issues: list[A11yIssue] = entry["issues"]

    try:
        original_content = file_path.read_text(encoding="utf-8")
    except Exception:
        original_content = entry.get("file_content", "")

    current_content = original_content
    resolved_issue_ids: set[str] = set()
    # Subset of resolved_issue_ids whose resolving attempt also passed L2
    # functional preservation (clean fix, no structural regression).
    resolved_clean_ids: set[str] = set()
    file_attempt_records: list[ArchFileAttemptRecord] = []
    previous_rejection: dict | None = None

    vp = ValidationPipeline()

    for attempt_num in range(1, max_retries + 1):
        pending_issues = [i for i in all_issues if i.issue_id not in resolved_issue_ids]
        if not pending_issues:
            break

        t_att_start = time.perf_counter()
        ts_start = _now_iso()

        att = ArchFileAttemptRecord(
            run_id=run_id,
            condition_id=condition.id,
            model_name=model_name,
            repetition=repetition,
            seed=seed,
            git_hash=git_hash,
            file_path=str(file_path),
            attempt_number=attempt_num,
            timestamp_start=ts_start,
            n_issues_targeted=len(pending_issues),
            n_issues_total_in_file=len(all_issues),
            condition_components_active=components_active(condition),
            reflection_feedback_active=condition.flags.reflection_feedback,
        )

        # ── LLM call ──────────────────────────────────────────────────────────
        task = AgentTask(
            file=file_path,
            file_content=current_content,
            issues=pending_issues,
        )

        t_inf = time.perf_counter()
        try:
            patch = await asyncio.wait_for(
                agent.run(task, previous_rejection=previous_rejection),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            patch = PatchResult(success=False, error=f"LLM timeout after {timeout_s}s")
        except Exception as exc:
            patch = PatchResult(success=False, error=str(exc)[:200])
        att.time_inference_s = time.perf_counter() - t_inf

        att.prompt_char_count = len(agent._last_prompt)
        att.tokens_input_estimated = len(agent._last_prompt) // 4
        att.response_chars = len(agent._last_response)
        att.tokens_prompt = patch.tokens_prompt or 0
        att.tokens_completion = patch.tokens_completion or 0
        att.tokens_output = att.tokens_completion

        patched_content = patch.new_content or ""
        att.patch_extracted = bool(patched_content)
        att.patched_excerpt = patched_content[:3000]

        patch_accepted = patch.success and bool(patched_content)

        # ── Internal validation (bypass for arch_no_internal_validation) ───────
        t_val = time.perf_counter()
        if patch_accepted and not condition.flags.skip_internal_validation:
            try:
                val = vp.validate(
                    patched_content=patched_content,
                    original_content=current_content,
                    issues=pending_issues,
                    file_id=str(file_path),
                    model_id=model_name,
                    strategy=condition.id,
                )
                rl = val.rejected_at_layer
                att.layer1_syntax_pass = rl is None or rl > 1
                att.layer2_functional_pass = rl is None or rl > 2
                att.layer3_domain_pass = rl is None or rl > 3
                att.layer4_quality_pass = rl is None or rl != 4
                att.validation_rejected_layer = rl
                att.validation_failure_reason = val.failure_reason or ""

                if rl in (1, 2, 3):
                    patch_accepted = False
                    if rl == 1:
                        att.layer1_error_msg = val.failure_reason or ""
                    elif rl == 2:
                        att.layer2_error_msg = val.failure_reason or ""
                    else:
                        att.layer3_error_msg = val.failure_reason or ""
            except Exception as val_exc:
                att.layer1_error_msg = str(val_exc)[:200]
                patch_accepted = False
        elif condition.flags.skip_internal_validation and patched_content:
            att.validation_bypassed = True
            att.layer1_syntax_pass = True
            att.layer3_domain_pass = True
            att.layer4_quality_pass = True
            # V5 bypasses validation as a GATE (patch is still injected and
            # re-scanned), but Layer 2 functional preservation is still MEASURED
            # so we can tell whether a Pa11y-credited fix also broke the component.
            try:
                l2 = run_layer2(current_content, patched_content)
                att.layer2_functional_pass = l2.passed
                if not l2.passed:
                    att.layer2_error_msg = f"functional_regression:{l2.failed_check or 'unknown'}"
            except Exception as l2_exc:
                # Fail-open: a checker error must not penalise the bypass condition.
                att.layer2_functional_pass = True
                att.layer2_error_msg = f"layer2_error:{str(l2_exc)[:120]}"
        att.time_validation_s = time.perf_counter() - t_val

        # ── Structure-preservation guard (semantic-deletion detector) ──────────
        # Applied to EVERY condition (not just V5) so the functional-clean IFR is
        # comparable across conditions. run_layer2 / the validation gate verify
        # the programmatic interface but are blind to deleted headings, landmarks,
        # or content — the dominant risk for `semantic` fixes. structure_pass is
        # NOT a gate (it never blocks injection or the re-scan); it only annotates
        # whether a Pa11y-credited fix preserved document structure.
        structure_pass = True
        if patched_content:
            try:
                sp = check_structure_preserved(current_content, patched_content)
                structure_pass = sp.passed
                if not sp.passed and not att.layer2_error_msg:
                    att.layer2_error_msg = f"structure_regression:{sp.failed_check or 'unknown'}"
            except Exception:
                structure_pass = True  # fail-open

        att.patch_accepted = patch_accepted

        # ── Pa11y re-scan (write → scan → restore) ────────────────────────────
        if patch_accepted or (condition.flags.skip_internal_validation and patched_content):
            t_rescan = time.perf_counter()
            att.rescan_performed = True
            effective_content = patched_content

            try:
                file_path.write_text(effective_content, encoding="utf-8")

                rescan: ScanResult = await asyncio.wait_for(
                    pipeline.scanner.scan_file(file_path, _WCAG_LEVEL),
                    timeout=60,
                )

                for iss in rescan.issues:
                    if not iss.issue_id:
                        iss.compute_id()

                remaining_ids = {i.issue_id for i in rescan.issues}

                def _crit(iss: A11yIssue) -> str:
                    return iss.wcag_criteria or f"type:{iss.issue_type.value}"

                before_by_crit: dict[str, int] = {}
                for iss in pending_issues:
                    c = _crit(iss)
                    before_by_crit[c] = before_by_crit.get(c, 0) + 1

                after_by_crit: dict[str, int] = {}
                for iss in rescan.issues:
                    c = _crit(iss)
                    after_by_crit[c] = after_by_crit.get(c, 0) + 1

                credit = {
                    c: max(0, n - after_by_crit.get(c, 0))
                    for c, n in before_by_crit.items()
                }

                newly_resolved: list[A11yIssue] = []
                migrations = 0
                for iss in pending_issues:
                    if iss.issue_id in remaining_ids:
                        continue
                    c = _crit(iss)
                    if credit.get(c, 0) > 0:
                        credit[c] -= 1
                        newly_resolved.append(iss)
                    else:
                        migrations += 1

                att.n_issues_resolved_this_attempt = len(newly_resolved)
                att.n_selector_migrations = migrations

                for iss in newly_resolved:
                    resolved_issue_ids.add(iss.issue_id)
                    # A fix counts as functionally clean iff the attempt that
                    # produced it preserved BOTH the component's programmatic
                    # interface (L2: props/exports/handlers) AND its document
                    # structure (headings/landmarks/content). The structure guard
                    # is what distinguishes a real `semantic` fix from one that
                    # merely deleted the offending element.
                    if att.layer2_functional_pass and structure_pass:
                        resolved_clean_ids.add(iss.issue_id)

                if newly_resolved:
                    current_content = effective_content

                log.info(
                    "arch_rescan",
                    file=file_path.name,
                    attempt=attempt_num,
                    targeted=len(pending_issues),
                    resolved=len(newly_resolved),
                    migrations=migrations,
                )

                if att.n_issues_resolved_this_attempt == 0:
                    previous_rejection = {
                        "error": "Pa11y still detects violations after patch",
                        "layer": None,
                        "excerpt": patched_content[:400],
                    }
                else:
                    previous_rejection = None

            except asyncio.TimeoutError:
                att.rescan_failed = True
                att.rescan_error_msg = "Pa11y re-scan timed out (60s)"
                previous_rejection = {
                    "error": "Pa11y re-scan timed out",
                    "layer": None,
                    "excerpt": patched_content[:400],
                }
            except Exception as rescan_exc:
                att.rescan_failed = True
                att.rescan_error_msg = str(rescan_exc)[:200]
                log.warning("rescan_error", file=str(file_path), error=str(rescan_exc)[:100])
                previous_rejection = {
                    "error": f"Pa11y re-scan error: {str(rescan_exc)[:100]}",
                    "layer": None,
                    "excerpt": patched_content[:400],
                }
            finally:
                att.time_rescan_s = time.perf_counter() - t_rescan
                try:
                    file_path.write_text(current_content, encoding="utf-8")
                except Exception:
                    pass
        else:
            reason = (
                att.layer1_error_msg
                or att.layer2_error_msg
                or att.layer3_error_msg
                or (patch.error or "unknown")
            )
            previous_rejection = {
                "error": reason[:300],
                "layer": att.validation_rejected_layer,
                "excerpt": patched_content[:400],
            }

        att.time_total_s = time.perf_counter() - t_att_start
        att.timestamp_end = _now_iso()
        file_attempt_records.append(att)
        collector.write_file_attempt(att)

    # Restore to original regardless of outcome
    try:
        file_path.write_text(original_content, encoding="utf-8")
    except Exception:
        pass

    # Build ViolationRecord for each issue
    n_atts = len(file_attempt_records)
    tokens_out_total = sum(a.tokens_completion for a in file_attempt_records)
    tokens_in_total = sum(a.tokens_input_estimated for a in file_attempt_records)
    time_total = sum(a.time_total_s for a in file_attempt_records)
    first_att = file_attempt_records[0] if file_attempt_records else None

    violation_records: list[ViolationRecord] = []
    for issue in all_issues:
        resolved = issue.issue_id in resolved_issue_ids
        resolved_clean = issue.issue_id in resolved_clean_ids
        functional_regression = resolved and not resolved_clean

        resolved_on: int | None = None
        if resolved and file_attempt_records:
            for att in file_attempt_records:
                if att.n_issues_resolved_this_attempt > 0:
                    resolved_on = att.attempt_number
                    break

        vr = ViolationRecord(
            run_id=run_id,
            condition_id=condition.id,
            model_name=model_name,
            repetition=repetition,
            seed=seed,
            git_hash=git_hash,
            violation_id=f"{issue.file}:{issue.selector}:{issue.wcag_criteria}",
            file_path=issue.file,
            wcag_criterion=issue.wcag_criteria or "",
            wcag_category=issue.issue_type.value,
            confidence=issue.confidence.value.upper(),
            n_tools_detected=issue.tool_consensus,
            resolved=resolved,
            resolved_on_attempt=resolved_on,
            total_attempts=n_atts,
            retries_exhausted=not resolved and n_atts >= max_retries,
            attempt1_success=(
                first_att is not None and first_att.n_issues_resolved_this_attempt > 0
            ),
            attempt1_failure_layer=(
                first_att.validation_rejected_layer if first_att else None
            ),
            attempt1_failure_type=(
                ""
                if first_att is None or first_att.patch_accepted
                else (
                    "invalid_patch" if not first_att.patch_extracted
                    else "functional_regression" if first_att.validation_rejected_layer == 2
                    else "domain_violation" if first_att.validation_rejected_layer == 3
                    else "rejected"
                )
            ),
            validation_bypassed=condition.flags.skip_internal_validation,
            reflection_feedback_active=condition.flags.reflection_feedback,
            functional_regression=functional_regression,
            resolved_functional_clean=resolved_clean,
            tokens_output_total=tokens_out_total,
            tokens_input_estimated_total=tokens_in_total,
            time_total_s=time_total,
            agent_primary_used=agent.name(),
            escalated_to_openhands=False,
        )
        violation_records.append(vr)

    return violation_records, file_attempt_records


# ── Run orchestrator ──────────────────────────────────────────────────────────

class ArchRunOrchestrator:
    """Runs one (arch_condition × model × repetition) cell in pipeline format."""

    def __init__(
        self,
        condition: AblationCondition,
        model_name: str,
        repetition: int,
        seed: int,
        config: dict[str, Any],
        results_dir: Path,
        corpus_results_dir: Path,
        status_writer: StatusWriter | None = None,
        cells_done_so_far: int = 0,
    ) -> None:
        if condition.id not in _ARCH_CONDITION_IDS:
            raise ValueError(f"ArchRunOrchestrator only handles arch conditions. Got: {condition.id}")

        self.condition = condition
        self.model_name = model_name
        self.repetition = repetition
        self.seed = seed
        self.config = config
        self.results_dir = results_dir
        self.corpus_results_dir = corpus_results_dir
        self.status_writer = status_writer
        self.cells_done_so_far = cells_done_so_far

        self.run_id = str(uuid.uuid4())
        self.git_hash = _git_hash()

        agent_cfg = config.get("agent", {})
        self.max_retries = agent_cfg.get("max_retries_per_file", 3)
        self.timeout_s = agent_cfg.get("timeout_per_attempt_s", 180)
        self.temperature = agent_cfg.get("temperature", 0.1)

        corpus_cfg = config.get("corpus", {})
        self.subset_size = corpus_cfg.get("subset_size", None)

        out_cfg = config.get("output", {})
        self.write_prompt_log = out_cfg.get("write_prompt_log", True)
        self.prompt_log_max_chars = out_cfg.get("prompt_log_max_chars", 0)

        # Project selection file
        self.selected_projects: frozenset[str] = frozenset()
        selection_rel = corpus_cfg.get("project_selection_file")
        if selection_rel:
            for base in [results_dir.parent.parent, _REPO_ROOT, _REPO_ROOT.parent]:
                candidate = (base / Path(selection_rel.lstrip("./"))).resolve()
                if candidate.exists():
                    try:
                        raw = candidate.read_text(encoding="utf-8")
                        if candidate.suffix == ".json":
                            data = json.loads(raw)
                            files = data.get("files", []) if isinstance(data, dict) else data
                        else:
                            data = yaml.safe_load(raw)
                            files = data.get("files", []) if isinstance(data, dict) else []
                        self.selected_projects = frozenset(Path(p).name for p in files if p)
                        _vstep("  📋", "Projects:", f"{len(self.selected_projects)}")
                    except Exception as exc:
                        log.warning("selection_file_error", error=str(exc)[:80])
                    break

    async def run(self) -> None:
        log.info(
            "arch_run_start",
            condition=self.condition.id,
            model=self.model_name,
            rep=self.repetition,
        )

        entries = load_corpus(
            self.corpus_results_dir,
            selected_projects=self.selected_projects or None,
            subset_size=self.subset_size,
            seed=self.seed,
        )

        rng = random.Random(self.seed)
        rng.shuffle(entries)

        n_files = len(entries)
        n_violations = sum(len(e["issues"]) for e in entries)
        _vprint(f"  Files: {n_files}  |  Violations: {n_violations}  |  Oracle: Pa11y re-scan")

        if self.status_writer:
            self.status_writer.cell_start(
                condition_id=self.condition.id,
                model=self.model_name,
                repetition=self.repetition,
                violations_total=n_violations,
                max_retries=self.max_retries,
                cells_done=self.cells_done_so_far,
            )

        collector = MetricsCollector(
            results_dir=self.results_dir,
            condition_id=self.condition.id,
            model_name=self.model_name,
            repetition=self.repetition,
            write_prompt_log=self.write_prompt_log,
            prompt_log_max_chars=self.prompt_log_max_chars,
        )
        cell_dir = self.results_dir / self.condition.id / self.model_name / f"rep{self.repetition}"

        collector.write_run_meta({
            "run_id": self.run_id,
            "format": "pipeline",
            "condition_id": self.condition.id,
            "condition_label": self.condition.label,
            "condition_flags": {
                "reflection_feedback": self.condition.flags.reflection_feedback,
                "wcag_semantic": self.condition.flags.wcag_semantic,
                "skip_internal_validation": self.condition.flags.skip_internal_validation,
                "few_shot": self.condition.flags.few_shot,
            },
            "model_name": self.model_name,
            "repetition": self.repetition,
            "seed": self.seed,
            "git_hash": self.git_hash,
            "max_retries": self.max_retries,
            "temperature": self.temperature,
            "pa11y_rescan": True,
            "oracle": "pa11y_rescan",
            "started_at": _now_iso(),
        })

        pipeline = self._build_pipeline(cell_dir)
        agent = AblationDirectLLMAgent(
            llm_client=pipeline.llm_client,
            condition=self.condition,
        )

        all_violations: list[ViolationRecord] = []
        all_file_attempts: list[ArchFileAttemptRecord] = []
        file_groups: dict[str, list[ViolationRecord]] = {}
        violations_done = 0
        violations_resolved = 0
        files_done = 0
        ts_start = _now_iso()
        t_wall = time.perf_counter()

        for file_idx, entry in enumerate(entries, 1):
            fp = entry["file_path"]
            n_file_iss = len(entry["issues"])
            _vprint(
                f"  [{file_idx}/{n_files}] {Path(fp).name} "
                f"({n_file_iss} issue{'s' if n_file_iss != 1 else ''})"
            )

            vrs, atts = await _repair_file(
                entry=entry,
                agent=agent,
                pipeline=pipeline,
                condition=self.condition,
                run_id=self.run_id,
                model_name=self.model_name,
                repetition=self.repetition,
                seed=self.seed,
                git_hash=self.git_hash,
                max_retries=self.max_retries,
                timeout_s=self.timeout_s,
                collector=collector,
            )

            for vr in vrs:
                collector.write_violation(vr)
                violations_done += 1
                if vr.resolved:
                    violations_resolved += 1
            file_groups[fp] = vrs
            all_violations.extend(vrs)
            all_file_attempts.extend(atts)
            files_done += 1

            file_res = sum(1 for v in vrs if v.resolved)
            file_ifr = file_res / len(vrs) if vrs else 0.0
            running_ifr = violations_resolved / violations_done if violations_done else 0.0

            _vprint(
                f"    -> file IFR: {file_res}/{len(vrs)} ({file_ifr*100:.0f}%)"
                f"  |  running: {running_ifr*100:.1f}%"
            )

            collector.write_checkpoint({
                "ts": _now_iso(),
                "files_done": files_done,
                "files_total": n_files,
                "violations_done": violations_done,
                "violations_resolved": violations_resolved,
                "ifr_running": round(running_ifr, 4),
                "last_file": Path(fp).name,
            })

            if self.status_writer:
                self.status_writer.update(
                    stage="fixing",
                    violations_done=violations_done,
                    violations_resolved=violations_resolved,
                    files_done=files_done,
                    files_total=n_files,
                    current_file=fp,
                    last_cell_ifr=round(running_ifr, 4),
                )

        ts_end = _now_iso()
        wall_s = time.perf_counter() - t_wall

        summary = _build_arch_run_summary(
            violations=all_violations,
            file_attempts=all_file_attempts,
            file_groups=file_groups,
            run_id=self.run_id,
            condition=self.condition,
            model_name=self.model_name,
            repetition=self.repetition,
            seed=self.seed,
            git_hash=self.git_hash,
            ts_start=ts_start,
            ts_end=ts_end,
            wall_s=wall_s,
        )
        collector.write_summary(summary)

        if self.status_writer:
            self.status_writer.cell_done(
                ifr=summary.ifr,
                n_resolved=summary.n_violations_resolved,
                n_total=summary.n_violations_total,
                wall_s=wall_s,
            )

        _vprint(
            f"  Cell done: IFR={summary.ifr*100:.2f}%"
            f" ({summary.n_violations_resolved}/{summary.n_violations_total})"
            f"  wall={wall_s:.0f}s"
        )

        log.info(
            "arch_run_done",
            condition=self.condition.id,
            model=self.model_name,
            rep=self.repetition,
            ifr=round(summary.ifr, 4),
            n_resolved=summary.n_violations_resolved,
            n_total=summary.n_violations_total,
        )

    def _build_pipeline(self, prompt_log_dir: Path | None = None) -> Pipeline:
        model_cfg = _model_config_from_yaml(self.model_name, self.temperature)
        settings = Settings()
        settings.verify_fixes_by_rescan = True
        return Pipeline(
            settings=settings,
            model_config=model_cfg,
            agent_preference=AgentType.DIRECT_LLM,
            prompt_log_dir=prompt_log_dir if self.write_prompt_log else None,
        )


# ── Study orchestrator ────────────────────────────────────────────────────────

class ArchAblationStudyOrchestrator:
    """
    Iterates over all (arch_condition × model × repetition) cells.
    Reads conditions.active from config — only processes arch_* conditions.
    Results go to results_dir/arch_ablation_pipeline/ (separate from single-
    violation ablation results), preserving experiment isolation.
    """

    def __init__(
        self,
        config_path: Path,
        dry_run: bool = False,
        reset: bool = False,
        skip_ollama_check: bool = False,
        auto_pull: bool = True,
        corpus_dir_override: Path | None = None,
    ) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self.reset = reset
        self.skip_ollama_check = skip_ollama_check
        self.auto_pull = auto_pull

        exp_dir = config_path.parent.parent
        self.config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        out_cfg = self.config.get("output", {})
        results_rel = out_cfg.get("results_dir", "results/arch_ablation_pipeline")
        self.results_dir = (exp_dir / results_rel).resolve()

        corpus_cfg = self.config.get("corpus", {})
        if corpus_dir_override is not None:
            self.corpus_results_dir = corpus_dir_override.resolve()
        else:
            corpus_rel = corpus_cfg.get("corpus_results_dir", "../dataset/results")
            self.corpus_results_dir = (exp_dir / corpus_rel).resolve()

        if not self.corpus_results_dir.exists():
            raise FileNotFoundError(f"Corpus not found: {self.corpus_results_dir}")

        models_cfg = self.config.get("models", {})
        self.models: list[str] = models_cfg.get("all", [models_cfg.get("primary", "qwen2.5-coder-7b")])

        rep_cfg = self.config.get("repetitions", {})
        self.n_reps: int = rep_cfg.get("n", 3)
        self.seeds: list[int] = rep_cfg.get("seeds", [42, 137, 2025])

        active_from_config: list[str] = self.config.get("conditions", {}).get("active", [])
        self.arch_conditions: list[AblationCondition] = [
            CONDITIONS[cid]
            for cid in active_from_config
            if cid in CONDITIONS and cid in _ARCH_CONDITION_IDS
        ]
        if not self.arch_conditions:
            _vstep("⚠", "No arch conditions in conditions.active — using all 5")
            self.arch_conditions = [
                CONDITIONS[cid] for cid in [
                    "arch_full", "arch_no_reflection", "arch_no_few_shot",
                    "arch_no_wcag_guidelines", "arch_no_internal_validation",
                ] if cid in CONDITIONS
            ]

    async def run_all(
        self,
        condition_filter: list[str] | None = None,
        model_filter: list[str] | None = None,
        rep_filter: list[int] | None = None,
    ) -> None:
        if self.reset and self.results_dir.exists():
            _vsection("RESET")
            _vstep("⚠", "Deleting:", str(self.results_dir))
            shutil.rmtree(self.results_dir)

        self.results_dir.mkdir(parents=True, exist_ok=True)

        conditions = [
            c for c in self.arch_conditions
            if not condition_filter or c.id in condition_filter
        ]
        models = [m for m in self.models if not model_filter or m in model_filter]
        reps = [r for r in range(1, self.n_reps + 1) if not rep_filter or r in rep_filter]

        total = len(conditions) * len(models) * len(reps)
        done = 0

        _vprint()
        _vsection("ARCH ABLATION — PIPELINE FORMAT")
        _vstep("📋", "Conditions:", "  ".join(c.id for c in conditions))
        _vstep("🤖", "Models:", "  ".join(models))
        _vstep("🔁", "Reps:", str(len(reps)))
        _vstep("📊", "Total cells:", str(total))
        _vstep("🔬", "Oracle:", "Pa11y re-scan (same as main experiment)")
        _vstep("📁", "Results:", str(self.results_dir))
        if self.dry_run:
            _vstep("🏃", "Mode:", "DRY RUN")
        _vprint()

        if not self.skip_ollama_check and not self.dry_run:
            _check_ollama(
                model_names=models,
                models_yaml_path=_REPO_ROOT / "models.yaml",
                abort_on_error=True,
                auto_pull=self.auto_pull,
            )

        study_start = _now_iso()
        status_writer = StatusWriter(
            results_dir=self.results_dir,
            cells_total=total,
            study_start=study_start,
        )

        for condition in conditions:
            for model in models:
                for rep_idx, rep in enumerate(reps):
                    seed = self.seeds[rep_idx] if rep_idx < len(self.seeds) else rep * 100
                    summary_path = (
                        self.results_dir / condition.id / model / f"rep{rep}" / "summary.json"
                    )

                    _vsection(
                        f"Cell {done+1}/{total}  │  {condition.id}  ×  {model}  ×  rep{rep}"
                    )

                    if summary_path.exists():
                        _vstep("⏭", "SKIPPED — summary.json exists")
                        done += 1
                        continue

                    if self.dry_run:
                        _vstep("🏃", "DRY RUN — skipping")
                        done += 1
                        continue

                    runner = ArchRunOrchestrator(
                        condition=condition,
                        model_name=model,
                        repetition=rep,
                        seed=seed,
                        config=self.config,
                        results_dir=self.results_dir,
                        corpus_results_dir=self.corpus_results_dir,
                        status_writer=status_writer,
                        cells_done_so_far=done,
                    )
                    await runner.run()
                    done += 1

        _vsection("DONE")
        _vstep("✓", "Cells run:", str(done))
        _vstep("📁", "Results:", str(self.results_dir))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Arch ablation — pipeline format.\n"
            "All violations/file + Pa11y re-scan (same oracle as main experiment).\n"
            "Results: ablation_study/results/arch_ablation_pipeline/"
        )
    )
    p.add_argument(
        "--config", type=Path,
        default=Path(__file__).parent.parent / "config" / "arch_ablation_pipeline_config.yaml",
    )
    p.add_argument("--condition", nargs="+", metavar="CONDITION_ID")
    p.add_argument("--model", nargs="+", metavar="MODEL_NAME")
    p.add_argument("--rep", nargs="+", type=int, metavar="REP")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reset", action="store_true",
                   help="Delete pipeline results directory before starting")
    p.add_argument("--skip-ollama-check", action="store_true")
    p.add_argument("--no-auto-pull", action="store_true")
    p.add_argument("--corpus-dir", type=Path, default=None)
    return p.parse_args()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()

    orchestrator = ArchAblationStudyOrchestrator(
        config_path=args.config,
        dry_run=args.dry_run,
        reset=args.reset,
        skip_ollama_check=args.skip_ollama_check,
        auto_pull=not args.no_auto_pull,
        corpus_dir_override=args.corpus_dir,
    )
    await orchestrator.run_all(
        condition_filter=args.condition,
        model_filter=args.model,
        rep_filter=args.rep,
    )


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(_main())


if __name__ == "__main__":
    main()
