"""
ablation_runner.py
==================
Main orchestrator for the prompt-component ablation study.

Runs all 8 conditions × N models × 3 repetitions and writes results via
MetricsCollector.  Integrates with the existing a11y_autofix Pipeline but
replaces the standard PromptBuilder with build_ablation_prompt() so that
only the ablation-specific prompt variation is swapped in.

Entry point:
    python -m ablation_study.src.ablation_runner [--config path] [--dry-run]
    python -m ablation_study.src.ablation_runner --condition full --model qwen2.5-coder-3b --rep 1

Design decisions:
- One repetition = one fresh Pipeline (model reloaded) to prevent cross-rep state.
- Scan results are cached per file across conditions within a repetition so that
  Pa11y/Axe scan time is not counted against prompt-only IFR comparisons.
- Each file's violations are sorted deterministically (by violation_id) before
  processing so that order is reproducible from the seed alone.
- All timing is wall-clock (time.perf_counter) to capture Chromium cold-start.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

# ── Status writer (feeds progress_dashboard.py) ────────────────────────────────

class StatusWriter:
    """
    Writes a single status.json to results_dir at key moments during a run.
    The dashboard polls this file to render the live view.
    Writes are atomic (tmp → rename) so the reader never sees a partial file.
    """

    def __init__(self, results_dir: Path, cells_total: int, study_start: str) -> None:
        self._path = results_dir / "status.json"
        self._base: dict = {
            "study_start": study_start,
            "cells_total": cells_total,
        }
        self._state: dict = {}
        self._lock = __import__("threading").Lock()

    def update(self, **kwargs) -> None:
        with self._lock:
            self._state.update(kwargs)
            payload = {**self._base, **self._state}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(self._path)

    def cell_start(
        self,
        condition_id: str,
        model: str,
        repetition: int,
        violations_total: int,
        max_retries: int,
        cells_done: int,
    ) -> None:
        self.update(
            condition_id=condition_id,
            model=model,
            repetition=repetition,
            cell_start=_now_iso(),
            stage="starting",
            violations_total=violations_total,
            violations_done=0,
            violations_resolved=0,
            current_violation_id="",
            current_attempt=1,
            max_retries=max_retries,
            n_attempts=0,
            layer1_fail_rate=None,
            layer2_fail_rate=None,
            layer3_fail_rate=None,
            layer4_fail_rate=None,
            cells_done=cells_done,
        )

    def violation_update(
        self,
        violation_id: str,
        attempt_number: int,
        violations_done: int,
        violations_resolved: int,
        attempts: "list",
    ) -> None:
        n_att = len(attempts)
        def _rate(layer: int) -> float | None:
            if n_att == 0:
                return None
            return sum(1 for a in attempts if a.failure_layer == layer) / n_att

        self.update(
            stage="fixing",
            current_violation_id=violation_id,
            current_attempt=attempt_number,
            violations_done=violations_done,
            violations_resolved=violations_resolved,
            n_attempts=n_att,
            layer1_fail_rate=_rate(1),
            layer2_fail_rate=_rate(2),
            layer3_fail_rate=_rate(3),
            layer4_fail_rate=sum(
                1 for a in attempts
                if not a.layer4_quality_pass and a.attempt_success
            ) / n_att if n_att else None,
        )

    def cell_done(self) -> None:
        self.update(stage="done")


# ── Path bootstrap ─────────────────────────────────────────────────────────────
# ablation_study/src/ → ablation_study/ → a11y_experiment/ (git root + package root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from a11y_autofix.config import (
    A11yIssue,
    AgentTask,
    AgentType,
    ModelConfig,
    Settings,
)
from a11y_autofix.pipeline import Pipeline

from ablation_study.src.ablation_conditions import (
    CONDITIONS,
    AblationCondition,
    build_ablation_prompt,
    components_active,
)
from ablation_study.src.metrics_collector import (
    MetricsCollector,
    aggregate_file_record,
    aggregate_violation_record,
    build_run_summary,
)
from ablation_study.src.metrics_schema import AttemptRecord

log = structlog.get_logger(__name__)


# ── Config loading ─────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open() as fh:
        return yaml.safe_load(fh)


def _git_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ── Corpus loading ─────────────────────────────────────────────────────────────

def load_violations(
    violations_file: Path,
    include_regression_guards: bool = False,
    subset_size: int | None = None,
    stratify_subset: bool = True,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Load violations from the corpus JSON file.

    Returns a list of dicts, each representing one file's violations:
      {"file_path": str, "issues": [A11yIssue, ...], "wcag_category": str, ...}
    """
    with violations_file.open() as fh:
        corpus = json.load(fh)

    entries = [e for e in corpus if e.get("issues")]

    if not include_regression_guards:
        entries = [e for e in entries if e.get("issues")]

    if subset_size is not None and subset_size < len(entries):
        if stratify_subset:
            entries = _stratified_sample(entries, subset_size, seed)
        else:
            rng = random.Random(seed)
            entries = rng.sample(entries, subset_size)

    return entries


def _stratified_sample(
    entries: list[dict],
    n: int,
    seed: int,
) -> list[dict]:
    """Sample n entries proportionally from each WCAG category."""
    from collections import defaultdict

    by_cat: dict[str, list] = defaultdict(list)
    for e in entries:
        cat = e.get("wcag_category", "unknown")
        by_cat[cat].append(e)

    rng = random.Random(seed)
    sampled: list[dict] = []
    remaining = n

    cats = sorted(by_cat)
    per_cat = max(1, n // len(cats))

    for cat in cats:
        pool = by_cat[cat]
        k = min(per_cat, len(pool), remaining)
        sampled.extend(rng.sample(pool, k))
        remaining -= k

    # Fill remaining slots from the full pool
    taken_ids = {id(e) for e in sampled}
    leftover = [e for e in entries if id(e) not in taken_ids]
    if remaining > 0 and leftover:
        sampled.extend(rng.sample(leftover, min(remaining, len(leftover))))

    return sampled


# ── Per-attempt repair with metrics capture ────────────────────────────────────

class AblationAttemptRunner:
    """
    Runs a single repair attempt for one violation under one condition.

    Wraps the existing Pipeline internals but injects the ablation prompt
    instead of the standard PromptBuilder output.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        condition: AblationCondition,
        run_id: str,
        model_name: str,
        repetition: int,
        seed: int,
        git_hash: str,
        timeout_s: int = 120,
    ) -> None:
        self.pipeline  = pipeline
        self.condition = condition
        self.run_id    = run_id
        self.model_name = model_name
        self.repetition = repetition
        self.seed      = seed
        self.git_hash  = git_hash
        self.timeout_s = timeout_s

    async def run_attempt(
        self,
        task: AgentTask,
        issue: A11yIssue,
        violation_id: str,
        attempt_number: int,
        confidence: str,
        n_tools: int,
        tools_detected_by: list[str],
        wcag_category: str,
    ) -> AttemptRecord:
        record = AttemptRecord(
            run_id=self.run_id,
            condition_id=self.condition.id,
            model_name=self.model_name,
            repetition=self.repetition,
            seed=self.seed,
            git_hash=self.git_hash,
            violation_id=violation_id,
            file_path=str(task.file),
            wcag_criterion=issue.wcag_criteria or "",
            wcag_category=wcag_category,
            confidence=confidence,
            n_tools_detected=n_tools,
            tools_detected_by=tools_detected_by,
            attempt_number=attempt_number,
            agent_used="swe-agent",
            timestamp_start=_now_iso(),
            condition_components_active=components_active(self.condition),
        )

        prompt = build_ablation_prompt(
            task, self.condition, issue, confidence, n_tools
        )
        record.prompt_char_count = len(prompt)
        record.tokens_input_estimated = len(prompt) // 4

        t_total_start = time.perf_counter()

        try:
            result = await asyncio.wait_for(
                self._call_agent(task, issue, prompt, record),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            record.failure_layer = 1
            record.failure_type = "invalid_patch"
            record.layer1_error_msg = f"Timeout after {self.timeout_s}s"
        except Exception as exc:  # noqa: BLE001
            record.failure_layer = 1
            record.failure_type = "invalid_patch"
            record.layer1_error_msg = str(exc)[:500]

        record.time_total_s = time.perf_counter() - t_total_start
        record.timestamp_end = _now_iso()
        record.attempt_success = (
            record.layer1_syntax_pass
            and record.layer2_functional_pass
            and record.layer3_domain_pass
        )

        if not record.attempt_success and record.failure_layer is None:
            if not record.layer3_domain_pass:
                record.failure_layer = 3
                record.failure_type = "domain_violation"
            elif not record.layer2_functional_pass:
                record.failure_layer = 2
                record.failure_type = "functional_regression"
            elif not record.layer1_syntax_pass:
                record.failure_layer = 1
                record.failure_type = "invalid_patch"

        return record

    async def _call_agent(
        self,
        task: AgentTask,
        issue: A11yIssue,
        prompt: str,
        record: AttemptRecord,
    ) -> None:
        """Delegate to the pipeline's agent and populate validation fields."""
        # Use the pipeline's internal DirectLLMAgent with the ablation prompt.
        # The pipeline normally builds the prompt internally; here we bypass
        # PromptBuilder by passing the pre-built prompt via task.context.
        task_with_prompt = task.model_copy(
            update={"context": {**task.context, "_ablation_prompt": prompt}}
        )

        t_inf = time.perf_counter()
        patch = await self.pipeline.llm_client.complete(
            prompt=prompt,
            temperature=0.2,
        )
        record.time_inference_s = time.perf_counter() - t_inf

        record.tokens_output = patch.tokens_completion or 0
        record.response_truncated = _is_truncated(patch.content)
        record.json_parse_success = _try_parse_json(patch.content)

        t_val = time.perf_counter()
        await self._run_validation(task, patch.content, record)
        record.time_validation_s = time.perf_counter() - t_val

    async def _run_validation(
        self,
        task: AgentTask,
        response: str,
        record: AttemptRecord,
    ) -> None:
        """Run layers 1-4 and populate record fields."""
        from a11y_autofix.validation.pipeline import ValidationPipeline

        vp = ValidationPipeline(self.pipeline.settings)

        # Layer 1 — syntax
        t1 = time.perf_counter()
        l1 = await vp.layer1_syntax(response)
        record.time_layer1_s = time.perf_counter() - t1
        record.layer1_syntax_pass = l1.passed
        record.layer1_error_msg = l1.error or ""

        if not l1.passed:
            return

        # Layer 2 — functional
        t2 = time.perf_counter()
        l2 = await vp.layer2_functional(task.file_content, l1.fixed_code or response)
        record.time_layer2_s = time.perf_counter() - t2
        record.layer2_functional_pass = l2.passed
        record.layer2_error_msg = l2.error or ""

        if not l2.passed:
            return

        # Layer 3 — domain (Pa11y re-scan)
        t3 = time.perf_counter()
        l3 = await vp.layer3_domain(task.file, l1.fixed_code or response)
        record.time_layer3_s = time.perf_counter() - t3
        record.chromium_coldstart_s = l3.chromium_coldstart_s or 0.0
        record.layer3_domain_pass = l3.passed
        record.layer3_error_msg = l3.error or ""
        record.layer3_new_violations_introduced = l3.new_violations or 0

        if not l3.passed:
            return

        # Layer 4 — quality (ESLint + complexity, soft)
        t4 = time.perf_counter()
        l4 = await vp.layer4_quality(task.file, l1.fixed_code or response)
        record.time_layer4_s = time.perf_counter() - t4
        record.layer4_quality_pass = l4.passed
        record.layer4_eslint_errors = l4.eslint_errors or 0
        record.layer4_complexity_violations = l4.complexity_violations or 0


# ── Single-run orchestrator ────────────────────────────────────────────────────

class SingleRunOrchestrator:
    """
    Runs one (condition × model × repetition) tuple end-to-end.

    Processes every violation in the corpus in seed-shuffled order,
    retrying failed repairs up to max_retries times, and writes all
    granularity levels to disk via MetricsCollector.
    """

    def __init__(
        self,
        condition: AblationCondition,
        model_name: str,
        repetition: int,
        seed: int,
        config: dict[str, Any],
        results_dir: Path,
        violations_file: Path,
        status_writer: "StatusWriter | None" = None,
        cells_done_so_far: int = 0,
    ) -> None:
        self.condition         = condition
        self.model_name        = model_name
        self.repetition        = repetition
        self.seed              = seed
        self.config            = config
        self.results_dir       = results_dir
        self.violations_file   = violations_file
        self.status_writer     = status_writer
        self.cells_done_so_far = cells_done_so_far

        self.run_id  = str(uuid.uuid4())
        self.git_hash = _git_hash()

        agent_cfg = config.get("agent", {})
        self.max_retries = agent_cfg.get("max_retries_per_file", 3)
        self.timeout_s   = agent_cfg.get("timeout_per_attempt_s", 120)
        self.temperature = agent_cfg.get("temperature", 0.2)

        corpus_cfg = config.get("corpus", {})
        self.include_guards = corpus_cfg.get("include_regression_guards", False)
        self.subset_size    = corpus_cfg.get("subset_size", None)
        self.stratify       = corpus_cfg.get("stratify_subset", True)

        out_cfg = config.get("output", {})
        self.write_prompt_log    = out_cfg.get("write_prompt_log", True)
        self.prompt_log_max_chars = out_cfg.get("prompt_log_max_chars", 8000)

    async def run(self) -> None:
        log.info("run_start", condition=self.condition.id, model=self.model_name, rep=self.repetition)

        entries = load_violations(
            self.violations_file,
            include_regression_guards=self.include_guards,
            subset_size=self.subset_size,
            stratify_subset=self.stratify,
            seed=self.seed,
        )

        # Shuffle file order deterministically per seed
        rng = random.Random(self.seed)
        rng.shuffle(entries)

        # Count total violations for status reporting
        n_violations_total = sum(len(e.get("issues", [])) for e in entries if e.get("issues"))

        if self.status_writer:
            self.status_writer.cell_start(
                condition_id=self.condition.id,
                model=self.model_name,
                repetition=self.repetition,
                violations_total=n_violations_total,
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

        pipeline = self._build_pipeline()
        attempt_runner = AblationAttemptRunner(
            pipeline=pipeline,
            condition=self.condition,
            run_id=self.run_id,
            model_name=self.model_name,
            repetition=self.repetition,
            seed=self.seed,
            git_hash=self.git_hash,
            timeout_s=self.timeout_s,
        )

        all_attempts:   list[AttemptRecord] = []
        all_violations = []
        all_files       = []

        t_wall_start = time.perf_counter()
        ts_start = _now_iso()

        scan_cache: dict[str, Any] = {}
        violations_done = 0
        violations_resolved = 0

        for entry in entries:
            file_path   = entry["file_path"]
            issues_raw  = entry.get("issues", [])
            confidence  = entry.get("confidence", "STANDARD")
            n_tools     = entry.get("n_tools_detected", 1)
            tools_by    = entry.get("tools_detected_by", [])
            wcag_cat    = entry.get("wcag_category", "aria")

            issues = [A11yIssue(**i) if isinstance(i, dict) else i for i in issues_raw]
            if not issues:
                continue

            file_attempts:   list[AttemptRecord] = []
            file_violations = []

            for issue in issues:
                violation_id = f"{file_path}:{issue.selector}:{issue.wcag_criteria}"
                violation_attempts: list[AttemptRecord] = []

                task = AgentTask(
                    file=Path(file_path),
                    file_content=entry.get("file_content", ""),
                    issues=[issue],
                )

                for attempt_num in range(1, self.max_retries + 1):
                    if self.status_writer:
                        self.status_writer.violation_update(
                            violation_id=violation_id,
                            attempt_number=attempt_num,
                            violations_done=violations_done,
                            violations_resolved=violations_resolved,
                            attempts=all_attempts,
                        )

                    rec = await attempt_runner.run_attempt(
                        task=task,
                        issue=issue,
                        violation_id=violation_id,
                        attempt_number=attempt_num,
                        confidence=confidence,
                        n_tools=n_tools,
                        tools_detected_by=tools_by,
                        wcag_category=wcag_cat,
                    )
                    collector.write_attempt(rec)
                    violation_attempts.append(rec)
                    all_attempts.append(rec)

                    if rec.attempt_success:
                        break

                violations_done += 1
                if any(a.attempt_success for a in violation_attempts):
                    violations_resolved += 1

                vr = aggregate_violation_record(
                    violation_attempts,
                    run_id=self.run_id,
                    condition_id=self.condition.id,
                    model_name=self.model_name,
                    repetition=self.repetition,
                )
                collector.write_violation(vr)
                file_violations.append(vr)
                all_violations.append(vr)
                file_attempts.extend(violation_attempts)

            fr = aggregate_file_record(
                file_violations,
                file_path=file_path,
                run_id=self.run_id,
                condition_id=self.condition.id,
                model_name=self.model_name,
                repetition=self.repetition,
            )
            collector.write_file(fr)
            all_files.append(fr)

        ts_end = _now_iso()
        wall_clock_s = time.perf_counter() - t_wall_start

        summary = build_run_summary(
            violation_records=all_violations,
            attempt_records=all_attempts,
            file_records=all_files,
            run_id=self.run_id,
            condition_id=self.condition.id,
            condition_label=self.condition.label,
            components_active=components_active(self.condition),
            model_name=self.model_name,
            repetition=self.repetition,
            seed=self.seed,
            git_hash=self.git_hash,
            timestamp_start=ts_start,
            timestamp_end=ts_end,
            wall_clock_total_s=wall_clock_s,
        )
        collector.write_summary(summary)

        if self.status_writer:
            self.status_writer.cell_done()

        log.info(
            "run_done",
            condition=self.condition.id,
            model=self.model_name,
            rep=self.repetition,
            ifr=round(summary.ifr, 4),
            n_resolved=summary.n_violations_resolved,
            n_total=summary.n_violations_total,
            wall_clock_s=round(wall_clock_s, 1),
        )

    def _build_pipeline(self) -> Pipeline:
        model_cfg = ModelConfig(
            model_name=self.model_name,
            temperature=self.temperature,
        )
        settings = Settings()
        return Pipeline(settings=settings, model_config=model_cfg, agent_preference=AgentType.AUTO)


# ── Full-study orchestrator ────────────────────────────────────────────────────

class AblationStudyOrchestrator:
    """
    Iterates over all (condition, model, repetition) combinations and
    delegates each to SingleRunOrchestrator.

    Skips already-completed runs (summary.json exists) to allow restart
    after partial failures without re-running completed cells.
    """

    def __init__(self, config_path: Path, dry_run: bool = False) -> None:
        self.config_path = config_path
        self.dry_run = dry_run

        exp_dir = config_path.parent.parent
        self.config = load_config(config_path)

        results_rel = self.config.get("output", {}).get("results_dir", "results")
        self.results_dir = (exp_dir / results_rel).resolve()
        self.results_dir.mkdir(parents=True, exist_ok=True)

        corpus_cfg = self.config.get("corpus", {})
        violations_rel = corpus_cfg.get("violations_file", "../dataset/violations.json")
        self.violations_file = (exp_dir / violations_rel).resolve()

        models_cfg = self.config.get("models", {})
        self.models: list[str] = models_cfg.get("all", [models_cfg.get("primary", "qwen2.5-coder-3b")])

        rep_cfg = self.config.get("repetitions", {})
        self.n_reps: int = rep_cfg.get("n", 3)
        self.seeds: list[int] = rep_cfg.get("seeds", [42, 137, 2025])

    async def run_all(
        self,
        condition_filter: list[str] | None = None,
        model_filter: list[str] | None = None,
        rep_filter: list[int] | None = None,
    ) -> None:
        conditions = list(CONDITIONS.values())
        if condition_filter:
            conditions = [c for c in conditions if c.id in condition_filter]

        models = self.models
        if model_filter:
            models = [m for m in models if m in model_filter]

        reps = list(range(1, self.n_reps + 1))
        if rep_filter:
            reps = [r for r in reps if r in rep_filter]

        total = len(conditions) * len(models) * len(reps)
        done  = 0

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
                        self.results_dir
                        / condition.id / model / f"rep{rep}" / "summary.json"
                    )
                    if summary_path.exists():
                        log.info("skip_existing", condition=condition.id, model=model, rep=rep)
                        done += 1
                        continue

                    log.info(
                        "run_cell",
                        condition=condition.id,
                        model=model,
                        rep=rep,
                        seed=seed,
                        progress=f"{done + 1}/{total}",
                    )

                    if self.dry_run:
                        log.info("dry_run_skip")
                        done += 1
                        continue

                    runner = SingleRunOrchestrator(
                        condition=condition,
                        model_name=model,
                        repetition=rep,
                        seed=seed,
                        config=self.config,
                        results_dir=self.results_dir,
                        violations_file=self.violations_file,
                        status_writer=status_writer,
                        cells_done_so_far=done,
                    )
                    await runner.run()
                    done += 1

        log.info("study_complete", total_runs=total)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run prompt-component ablation study"
    )
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "config" / "experiment_config.yaml",
        help="Path to experiment_config.yaml",
    )
    p.add_argument(
        "--condition",
        nargs="+",
        metavar="CONDITION_ID",
        help="Restrict to specific conditions (e.g. full minus_role)",
    )
    p.add_argument(
        "--model",
        nargs="+",
        metavar="MODEL_NAME",
        help="Restrict to specific models",
    )
    p.add_argument(
        "--rep",
        nargs="+",
        type=int,
        metavar="REP",
        help="Restrict to specific repetitions (1, 2, 3)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned runs without executing them",
    )
    p.add_argument(
        "--list-conditions",
        action="store_true",
        help="Print all conditions and exit",
    )
    return p.parse_args()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()

    if args.list_conditions:
        for cid, cond in CONDITIONS.items():
            print(f"  {cid:25s}  {cond.label}")
        return

    orchestrator = AblationStudyOrchestrator(
        config_path=args.config,
        dry_run=args.dry_run,
    )
    await orchestrator.run_all(
        condition_filter=args.condition,
        model_filter=args.model,
        rep_filter=args.rep,
    )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()


# ── Utilities ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_truncated(text: str) -> bool:
    """Heuristic: response ends mid-token or missing closing delimiter."""
    stripped = text.rstrip()
    if not stripped:
        return True
    if stripped[-1] not in {"}", "`", '"', "'"}:
        return True
    return False


def _try_parse_json(text: str) -> bool:
    try:
        obj = json.loads(text)
        return isinstance(obj, dict) and "fixed_code" in obj
    except Exception:
        pass
    # Try to find JSON block
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            return "fixed_code" in obj
        except Exception:
            pass
    return False
