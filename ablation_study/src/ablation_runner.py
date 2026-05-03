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
    python -m ablation_study.src.ablation_runner --reset   # wipe results and start fresh

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
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

import urllib.request

from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ── Verbose console (human-readable progress alongside structlog) ──────────────

_console = Console(highlight=False)


def _vprint(msg: str = "", style: str = "") -> None:
    """Print a line to the rich console (no-op if stdout is not a tty)."""
    _console.print(msg, style=style)


def _vsection(title: str) -> None:
    _console.print(Rule(f"[bold]{title}[/bold]", style="bright_blue"))


def _vstep(icon: str, label: str, value: str = "", style: str = "white") -> None:
    parts = f"  {icon}  [dim]{label}[/dim]"
    if value:
        parts += f"  [bold {style}]{value}[/bold {style}]"
    _console.print(parts)


# ── Ollama pre-flight validation ───────────────────────────────────────────────

def _ollama_base_url(models_yaml_path: Path, model_name: str) -> str:
    """Return the Ollama base URL for the given model (default: localhost:11434)."""
    try:
        spec = yaml.safe_load(models_yaml_path.read_text(encoding="utf-8"))
        m = spec.get("models", {}).get(model_name, {})
        url = m.get("base_url", "")
        if url:
            # strip /v1 suffix — Ollama native API lives at the root
            return url.rstrip("/").removesuffix("/v1")
    except Exception:
        pass
    return "http://localhost:11434"


def _check_ollama(
    model_names: list[str],
    models_yaml_path: Path,
    abort_on_error: bool = True,
) -> bool:
    """
    Validate that Ollama is reachable and every required model is available.

    Checks performed:
      1. Ollama server responds to GET /api/tags (is it running?)
      2. Each model_id appears in the tag list (is the model pulled?)
      3. Each model responds to a minimal /api/generate call (does inference work?)

    Prints a formatted summary table and returns True if all checks pass.
    If abort_on_error=True, raises SystemExit on failure.
    """
    _vsection("OLLAMA PRE-FLIGHT CHECK")

    # Resolve model_ids from models.yaml
    try:
        all_specs = yaml.safe_load(models_yaml_path.read_text(encoding="utf-8")).get("models", {})
    except Exception as exc:
        _vstep("✗", "Cannot read models.yaml:", str(exc), style="red")
        if abort_on_error:
            raise SystemExit(1)
        return False

    # Only check Ollama-backend models
    ollama_models: list[tuple[str, str, str]] = []  # (name, model_id, base_url)
    for name in model_names:
        spec = all_specs.get(name, {})
        if spec.get("backend", "") != "ollama":
            continue
        model_id = spec.get("model_id", name)
        base_url = spec.get("base_url", "").rstrip("/").removesuffix("/v1") or "http://localhost:11434"
        ollama_models.append((name, model_id, base_url))

    if not ollama_models:
        _vstep("ℹ", "No Ollama-backend models in this run — skipping check.", style="dim")
        _vprint()
        return True

    all_ok = True
    results: list[tuple[str, str, str, str]] = []  # (name, model_id, status_icon, message)

    # Check each unique base_url
    checked_servers: dict[str, bool] = {}

    for name, model_id, base_url in ollama_models:
        # ── 1. Server reachability ────────────────────────────────────────
        if base_url not in checked_servers:
            try:
                with urllib.request.urlopen(f"{base_url}/api/tags", timeout=4) as r:
                    tags_data = json.loads(r.read())
                checked_servers[base_url] = True
            except Exception as exc:
                checked_servers[base_url] = False
                msg = str(exc)
                if "Connection refused" in msg or "refused" in msg.lower():
                    hint = "Ollama not running — start it with: ollama serve"
                elif "timed out" in msg.lower():
                    hint = "Connection timed out — is Ollama listening?"
                else:
                    hint = msg[:80]
                results.append((name, model_id, "✗", f"SERVER UNREACHABLE: {hint}"))
                all_ok = False
                continue

        if not checked_servers[base_url]:
            results.append((name, model_id, "✗", "SERVER UNREACHABLE (see above)"))
            all_ok = False
            continue

        # ── 2. Model availability ─────────────────────────────────────────
        available_tags = [m.get("name", "") for m in tags_data.get("models", [])]
        # Ollama tags may include ":latest" suffix; match on prefix
        model_tag_prefix = model_id.split(":")[0]
        matched = any(t == model_id or t.startswith(model_tag_prefix) for t in available_tags)

        if not matched:
            pull_cmd = f"ollama pull {model_id}"
            results.append((name, model_id, "✗", f"NOT PULLED — run: {pull_cmd}"))
            all_ok = False
            continue

        # ── 3. Test inference (tiny prompt, 1 token) ──────────────────────
        try:
            payload = json.dumps({
                "model": model_id,
                "prompt": "Hi",
                "stream": False,
                "options": {"num_predict": 1},
            }).encode()
            req = urllib.request.Request(
                f"{base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            if resp.get("done") is False and "response" not in resp:
                raise ValueError(f"Unexpected response: {str(resp)[:80]}")
            results.append((name, model_id, "✓", "OK — inference working"))
        except Exception as exc:
            msg = str(exc)
            if "model" in msg.lower() and "not found" in msg.lower():
                results.append((name, model_id, "✗", f"MODEL NOT LOADED: {msg[:80]}"))
            elif "timed out" in msg.lower():
                results.append((name, model_id, "⚠", f"INFERENCE TIMEOUT (30s) — model may be loading"))
                all_ok = False  # treat timeout as warning; don't hard-abort
            else:
                results.append((name, model_id, "⚠", f"INFERENCE ERROR: {msg[:80]}"))
                all_ok = False

    # ── Print results table ───────────────────────────────────────────────
    t = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    t.add_column("Model name",  style="cyan",  min_width=22)
    t.add_column("Model ID",    style="dim",   min_width=26)
    t.add_column("Status",      justify="center", min_width=4)
    t.add_column("Details",     min_width=40)

    for name, model_id, icon, msg in results:
        if icon == "✓":
            icon_text = Text("✓", style="bold green")
            msg_text  = Text(msg, style="dim green")
        elif icon == "⚠":
            icon_text = Text("⚠", style="bold yellow")
            msg_text  = Text(msg, style="yellow")
        else:
            icon_text = Text("✗", style="bold red")
            msg_text  = Text(msg, style="red")
        t.add_row(name, model_id, icon_text, msg_text)

    _console.print(t)
    _vprint()

    if all_ok:
        _vstep("✓", "All Ollama models healthy — experiment can proceed.", style="green")
    else:
        _vprint("  [bold red]✗  One or more Ollama checks failed.[/bold red]")
        _vprint("  [dim]Fix the issues above, then re-run the experiment.[/dim]")
        _vprint("  [dim]Pass --skip-ollama-check to bypass this check (not recommended).[/dim]")
        if abort_on_error:
            _vprint()
            raise SystemExit(1)

    _vprint()
    return all_ok


# ── Utilities (defined early — used by StatusWriter and run_all below) ─────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_truncated(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return True
    return stripped[-1] not in {"}", "`", '"', "'"}


def _model_config_from_yaml(model_name: str, temperature_override: float) -> "ModelConfig":
    """
    Build a ModelConfig by reading models.yaml from the repo root.
    The temperature comes from the ablation config (overrides models.yaml default).
    """
    models_file = _REPO_ROOT / "models.yaml"
    if not models_file.exists():
        raise FileNotFoundError(
            f"models.yaml not found at {models_file}\n"
            f"Expected in the a11y_experiment root directory."
        )
    all_models = yaml.safe_load(models_file.read_text(encoding="utf-8")).get("models", {})
    if model_name not in all_models:
        available = ", ".join(all_models.keys())
        raise ValueError(
            f"Model '{model_name}' not found in models.yaml.\n"
            f"Available: {available}"
        )
    spec = all_models[model_name]
    return ModelConfig(
        name=model_name,
        backend=LLMBackend(spec["backend"]),
        model_id=spec["model_id"],
        base_url=spec.get("base_url", ""),
        temperature=temperature_override,
        max_tokens=spec.get("max_tokens", 8192),
        family=spec.get("family", ""),
        size=spec.get("size", ""),
        quantization=spec.get("quantization", ""),
        tags=spec.get("tags", []),
    )


def _try_parse_json(text: str) -> bool:
    import re
    try:
        obj = json.loads(text)
        return isinstance(obj, dict) and "fixed_code" in obj
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return "fixed_code" in json.loads(m.group())
        except Exception:
            pass
    return False


def _reset_results(results_dir: Path) -> None:
    """Delete results_dir entirely and recreate it empty."""
    if results_dir.exists():
        _vprint()
        _vsection("RESET")
        _vstep("⚠", "Deleting results directory:", str(results_dir), style="red")
        shutil.rmtree(results_dir)
        _vstep("✓", "Directory removed. Starting from scratch.", style="green")
        _vprint()
    results_dir.mkdir(parents=True, exist_ok=True)


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
            content = json.dumps(payload, default=str)
            if sys.platform == "win32":
                # Windows: os.replace() raises PermissionError when target is
                # held open by the monitor process. status.json is read-only
                # monitoring data so a direct overwrite is safe.
                try:
                    self._path.write_text(content, encoding="utf-8")
                except PermissionError:
                    pass  # monitor is mid-read; skip this tick, next will land
            else:
                tmp = self._path.with_suffix(".json.tmp")
                tmp.write_text(content, encoding="utf-8")
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
            current_file="",
            current_attempt=1,
            max_retries=max_retries,
            n_attempts=0,
            layer1_fail_rate=None,
            layer2_fail_rate=None,
            layer3_fail_rate=None,
            layer4_fail_rate=None,
            cells_done=cells_done,
            last_attempt_result="",
            last_attempt_layers="",
        )

    def violation_update(
        self,
        violation_id: str,
        attempt_number: int,
        violations_done: int,
        violations_resolved: int,
        attempts: "list",
        current_file: str = "",
    ) -> None:
        n_att = len(attempts)
        def _rate(layer: int) -> float | None:
            if n_att == 0:
                return None
            return sum(1 for a in attempts if a.failure_layer == layer) / n_att

        self.update(
            stage="fixing",
            current_violation_id=violation_id,
            current_file=current_file,
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

    def attempt_done(self, result: str, layers_info: str) -> None:
        self.update(last_attempt_result=result, last_attempt_layers=layers_info)

    def cell_done(self, ifr: float, n_resolved: int, n_total: int, wall_s: float) -> None:
        self.update(
            stage="done",
            last_cell_ifr=round(ifr, 4),
            last_cell_resolved=n_resolved,
            last_cell_total=n_total,
            last_cell_wall_s=round(wall_s, 1),
        )


# ── Path bootstrap ─────────────────────────────────────────────────────────────
# ablation_study/src/ → ablation_study/ → a11y_experiment/ (git root + package root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from a11y_autofix.config import (
    A11yIssue,
    AgentTask,
    AgentType,
    LLMBackend,
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

def _load_selected_projects(selection_file: Path) -> frozenset[str]:
    """
    Parse the canonical experiment manifest (chosen_experiment.yaml) and return
    the set of project folder names that should be included in the ablation.

    The manifest's `files:` list contains paths like:
        dataset/snapshots/agnaistic__agnai
    We extract just the final component (the project name), which is also the
    folder name under dataset/results/.

    Returns frozenset of project names, or empty frozenset if file is missing.
    """
    if not selection_file.exists():
        _vstep("⚠", "project_selection_file not found:", str(selection_file), style="yellow")
        _vstep("  →", "Falling back to full corpus (all projects).", style="yellow")
        return frozenset()

    try:
        data = yaml.safe_load(selection_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _vstep("⚠", "Cannot parse project_selection_file:", str(exc), style="yellow")
        return frozenset()

    file_entries = data.get("files", [])
    if not file_entries:
        _vstep("⚠", "No 'files:' list found in selection manifest.", style="yellow")
        return frozenset()

    projects = frozenset(Path(p).name for p in file_entries if p)
    _vstep("  📋", f"Project selection loaded:", f"{len(projects)} projects from {selection_file.name}")
    return projects


# scan_results.json issue_type values → canonical wcag_category names
_ISSUE_TYPE_MAP: dict[str, str] = {
    "alt-text":  "alt_text",
    "alt_text":  "alt_text",
    "label":     "label",
    "semantic":  "semantic",
    "contrast":  "contrast",
    "aria":      "aria",
    "keyboard":  "keyboard",
    "focus":     "focus",
}


def _normalise_category(raw: str) -> str:
    return _ISSUE_TYPE_MAP.get(raw, raw)


def _issue_confidence(issue: dict) -> str:
    """HIGH if 2+ tools detected the violation, STANDARD otherwise."""
    return "HIGH" if issue.get("tool_consensus", 1) >= 2 else "STANDARD"


def load_corpus(
    corpus_results_dir: Path,
    subset_size: int | None = None,
    stratify_subset: bool = True,
    seed: int = 42,
    selected_projects: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Read violations from dataset/results/*/scan_results.json.

    Returns one dict per FILE that has at least one unresolved issue:
      {
        "file_path":    str,   # absolute path to the .tsx source file
        "file_content": str,   # source code (read from disk; "" if missing)
        "issues":       list,  # raw issue dicts from scan_results.json
      }

    If selected_projects is provided (non-empty frozenset of project folder names),
    only scan_results.json files whose parent directory name is in that set are
    loaded — matching the 36-project canonical experiment corpus.
    """
    _vstep("📂", "Loading corpus from:", str(corpus_results_dir))

    entries: list[dict] = []
    scan_files = sorted(corpus_results_dir.glob("*/scan_results.json"))

    if not scan_files:
        raise FileNotFoundError(
            f"No scan_results.json files found under:\n"
            f"  {corpus_results_dir}\n"
        )

    # Apply project filter when a selection manifest was loaded
    if selected_projects:
        all_count = len(scan_files)
        scan_files = [f for f in scan_files if f.parent.name in selected_projects]
        _vstep(
            "  🔍", "Project filter applied:",
            f"{len(scan_files)} selected / {all_count} total projects"
        )
        missing = selected_projects - {f.parent.name for f in scan_files}
        if missing:
            _vstep(
                "  ⚠", f"{len(missing)} selected project(s) have no scan_results.json:",
                "  ".join(sorted(missing)[:5]) + ("…" if len(missing) > 5 else ""),
                style="yellow",
            )

    skipped_projects = 0
    total_issues_raw = 0

    for scan_file in scan_files:
        try:
            data = json.loads(scan_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("scan_file_read_error", path=str(scan_file), error=str(exc))
            skipped_projects += 1
            continue

        for file_entry in data:
            file_path = file_entry.get("file", "")
            all_issues = file_entry.get("issues", [])
            unresolved = [i for i in all_issues if not i.get("resolved", False)]
            total_issues_raw += len(all_issues)
            if not unresolved:
                continue

            content = ""
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except Exception:
                pass

            entries.append({
                "file_path":    file_path,
                "file_content": content,
                "issues":       unresolved,
            })

    if not entries:
        raise FileNotFoundError(
            f"No scan_results.json files with unresolved issues found under:\n"
            f"  {corpus_results_dir}\n"
            f"Check that the path is correct and that the experiment has been run."
        )

    n_violations = sum(len(e["issues"]) for e in entries)
    _vstep("  ✓", "Projects scanned:", str(len(scan_files)))
    _vstep("  ✓", "Files with violations:", str(len(entries)))
    _vstep("  ✓", "Unresolved violations:", str(n_violations))
    if skipped_projects:
        _vstep("  ⚠", "Projects skipped (read error):", str(skipped_projects), style="yellow")

    if subset_size is not None and subset_size < len(entries):
        _vstep("  ✂", f"Subsetting to {subset_size} files (stratified={stratify_subset}, seed={seed})")
        if stratify_subset:
            entries = _stratified_sample(entries, subset_size, seed)
        else:
            rng = random.Random(seed)
            entries = rng.sample(entries, subset_size)
        n_violations = sum(len(e["issues"]) for e in entries)
        _vstep("  ✓", "After subset — violations:", str(n_violations))

    return entries


def _stratified_sample(
    entries: list[dict],
    n: int,
    seed: int,
) -> list[dict]:
    """Sample n entries proportionally across WCAG categories of first issue."""
    from collections import defaultdict

    by_cat: dict[str, list] = defaultdict(list)
    for e in entries:
        first_issue = e["issues"][0] if e["issues"] else {}
        cat = _normalise_category(first_issue.get("issue_type", "unknown"))
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

        # ── Verbose attempt outcome ──────────────────────────────────────────
        layers_info = (
            f"L1={'✓' if record.layer1_syntax_pass else '✗'} "
            f"L2={'✓' if record.layer2_functional_pass else '✗'} "
            f"L3={'✓' if record.layer3_domain_pass else '✗'} "
            f"L4={'✓' if record.layer4_quality_pass else '✗'}"
        )
        if record.attempt_success:
            outcome_icon, outcome_style = "✓", "green"
            outcome_label = "FIXED"
        else:
            outcome_icon, outcome_style = "✗", "red"
            outcome_label = f"FAIL @ layer {record.failure_layer} ({record.failure_type or '?'})"

        short_vid = (violation_id[-55:] if len(violation_id) > 55 else violation_id)
        _vprint(
            f"      [{outcome_style}]{outcome_icon}[/{outcome_style}] "
            f"[dim]att {attempt_number}[/dim]  "
            f"[{outcome_style}]{outcome_label}[/{outcome_style}]  "
            f"[dim]{layers_info}  {record.time_total_s:.1f}s[/dim]  "
            f"[dim italic]…{short_vid}[/dim italic]"
        )

        return record

    async def _call_agent(
        self,
        task: AgentTask,
        issue: A11yIssue,
        prompt: str,
        record: AttemptRecord,
    ) -> None:
        """Delegate to the pipeline's agent and populate validation fields."""
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
            _vprint(f"        [dim red]L1 syntax error: {record.layer1_error_msg[:120]}[/dim red]")
            return

        # Layer 2 — functional
        t2 = time.perf_counter()
        l2 = await vp.layer2_functional(task.file_content, l1.fixed_code or response)
        record.time_layer2_s = time.perf_counter() - t2
        record.layer2_functional_pass = l2.passed
        record.layer2_error_msg = l2.error or ""
        if not l2.passed:
            _vprint(f"        [dim red]L2 functional error: {record.layer2_error_msg[:120]}[/dim red]")
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
            _vprint(
                f"        [dim red]L3 domain error: {record.layer3_error_msg[:120]}"
                f"  new_violations={record.layer3_new_violations_introduced}[/dim red]"
            )
            return

        # Layer 4 — quality (ESLint + complexity, soft)
        t4 = time.perf_counter()
        l4 = await vp.layer4_quality(task.file, l1.fixed_code or response)
        record.time_layer4_s = time.perf_counter() - t4
        record.layer4_quality_pass = l4.passed
        record.layer4_eslint_errors = l4.eslint_errors or 0
        record.layer4_complexity_violations = l4.complexity_violations or 0
        if not l4.passed:
            _vprint(
                f"        [dim yellow]L4 quality (soft): eslint={record.layer4_eslint_errors} "
                f"complexity={record.layer4_complexity_violations}[/dim yellow]"
            )


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
        corpus_results_dir: Path,
        status_writer: "StatusWriter | None" = None,
        cells_done_so_far: int = 0,
    ) -> None:
        self.condition          = condition
        self.model_name         = model_name
        self.repetition         = repetition
        self.seed               = seed
        self.config             = config
        self.results_dir        = results_dir
        self.corpus_results_dir = corpus_results_dir
        self.status_writer      = status_writer
        self.cells_done_so_far  = cells_done_so_far

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

        # Load canonical project selection (chosen_experiment.yaml)
        selection_rel = corpus_cfg.get("project_selection_file")
        self.selected_projects: frozenset[str] = frozenset()
        if selection_rel:
            exp_dir = results_dir.parent  # results/ → ablation_study/
            # walk up to a11y_experiment/ (ablation_study/../)
            sel_path = (exp_dir.parent / selection_rel.lstrip("../")).resolve()
            # fallback: resolve relative to repo root
            sel_path_alt = (_REPO_ROOT / Path(selection_rel.lstrip("../"))).resolve()
            for candidate in (sel_path, sel_path_alt):
                if candidate.exists():
                    self.selected_projects = _load_selected_projects(candidate)
                    break
            else:
                _vstep("⚠", "Could not locate project_selection_file:", selection_rel, style="yellow")

        out_cfg = config.get("output", {})
        self.write_prompt_log    = out_cfg.get("write_prompt_log", True)
        self.prompt_log_max_chars = out_cfg.get("prompt_log_max_chars", 8000)

    async def run(self) -> None:
        log.info("run_start", condition=self.condition.id, model=self.model_name, rep=self.repetition)

        entries = load_corpus(
            self.corpus_results_dir,
            subset_size=self.subset_size,
            stratify_subset=self.stratify,
            seed=self.seed,
            selected_projects=self.selected_projects or None,
        )

        rng = random.Random(self.seed)
        rng.shuffle(entries)

        n_files = len(entries)
        n_violations_total = sum(len(e.get("issues", [])) for e in entries if e.get("issues"))

        _vprint(f"  [dim]Files to process: [bold]{n_files}[/bold]  |  Violations: [bold]{n_violations_total}[/bold][/dim]")
        _vprint()

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

        for file_idx, entry in enumerate(entries, 1):
            file_path   = entry["file_path"]
            issues_raw  = entry.get("issues", [])
            file_content = entry.get("file_content", "")

            issues: list[A11yIssue] = []
            for raw in issues_raw:
                try:
                    issues.append(A11yIssue(**raw) if isinstance(raw, dict) else raw)
                except Exception:
                    pass
            if not issues:
                continue

            short_path = Path(file_path).name
            _vprint(
                f"  [bold white]▶ File {file_idx}/{n_files}[/bold white]  "
                f"[cyan]{short_path}[/cyan]  "
                f"[dim]({len(issues)} violation{'s' if len(issues) != 1 else ''})[/dim]"
            )

            file_attempts:   list[AttemptRecord] = []
            file_violations = []
            file_resolved = 0

            for issue, raw_issue in zip(issues, issues_raw):
                raw_dict    = raw_issue if isinstance(raw_issue, dict) else {}
                confidence  = _issue_confidence(raw_dict)
                n_tools     = raw_dict.get("tool_consensus", 1)
                tools_by    = raw_dict.get("found_by", [])
                wcag_cat    = _normalise_category(raw_dict.get("issue_type", "aria"))

                violation_id = f"{file_path}:{issue.selector}:{issue.wcag_criteria}"

                _vprint(
                    f"    [dim]⬡ [{wcag_cat}] WCAG {issue.wcag_criteria or '?'}  "
                    f"confidence={confidence}  tools={n_tools}  "
                    f"selector=[italic]{(issue.selector or '')[:60]}[/italic][/dim]"
                )

                violation_attempts: list[AttemptRecord] = []

                task = AgentTask(
                    file=Path(file_path),
                    file_content=file_content,
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
                            current_file=file_path,
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
                resolved_this = any(a.attempt_success for a in violation_attempts)
                if resolved_this:
                    violations_resolved += 1
                    file_resolved += 1

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

            file_ifr = file_resolved / len(issues) if issues else 0.0
            _vprint(
                f"    [dim]└ File IFR: "
                f"[bold {'green' if file_ifr >= 0.5 else 'yellow'}]{file_resolved}/{len(issues)} "
                f"({file_ifr*100:.0f}%)[/bold {'green' if file_ifr >= 0.5 else 'yellow'}][/dim]"
            )

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
            self.status_writer.cell_done(
                ifr=summary.ifr,
                n_resolved=summary.n_violations_resolved,
                n_total=summary.n_violations_total,
                wall_s=wall_clock_s,
            )

        _vprint()
        _vprint(
            f"  [bold green]■ Cell done[/bold green]  "
            f"IFR=[bold magenta]{summary.ifr*100:.2f}%[/bold magenta]  "
            f"resolved=[bold]{summary.n_violations_resolved}/{summary.n_violations_total}[/bold]  "
            f"wall={wall_clock_s:.1f}s"
        )

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
        model_cfg = _model_config_from_yaml(self.model_name, self.temperature)
        _vstep("  🔧", "Loading model:", f"{self.model_name}  [{model_cfg.backend.value}]")
        settings = Settings()
        return Pipeline(settings=settings, model_config=model_cfg, agent_preference=AgentType.AUTO)


# ── Full-study orchestrator ────────────────────────────────────────────────────

class AblationStudyOrchestrator:
    """
    Iterates over all (condition, model, repetition) combinations and
    delegates each to SingleRunOrchestrator.

    Skips already-completed runs (summary.json exists) to allow restart
    after partial failures without re-running completed cells.
    Pass reset=True to wipe results_dir before starting.
    """

    def __init__(
        self,
        config_path: Path,
        dry_run: bool = False,
        reset: bool = False,
        skip_ollama_check: bool = False,
        corpus_dir_override: Path | None = None,
    ) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self.reset = reset
        self.skip_ollama_check = skip_ollama_check

        exp_dir = config_path.parent.parent
        self.config = load_config(config_path)

        results_rel = self.config.get("output", {}).get("results_dir", "results")
        self.results_dir = (exp_dir / results_rel).resolve()

        corpus_cfg = self.config.get("corpus", {})
        if corpus_dir_override is not None:
            self.corpus_results_dir = corpus_dir_override.resolve()
        else:
            corpus_rel = corpus_cfg.get("corpus_results_dir", "../dataset/results")
            self.corpus_results_dir = (exp_dir / corpus_rel).resolve()

        if not self.corpus_results_dir.exists():
            raise FileNotFoundError(
                f"\n\nCorpus directory not found:\n"
                f"  {self.corpus_results_dir}\n\n"
                f"Expected: dataset/results/ containing */scan_results.json files.\n"
                f"Override with --corpus-dir:\n"
                f"  python -m ablation_study.src.ablation_runner "
                f"--corpus-dir path\\to\\dataset\\results\n"
            )

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
        # ── Reset if requested ───────────────────────────────────────────────
        if self.reset:
            _reset_results(self.results_dir)
        else:
            self.results_dir.mkdir(parents=True, exist_ok=True)

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

        # ── Banner ───────────────────────────────────────────────────────────
        _vprint()
        _vsection("ABLATION STUDY — START")
        _vstep("📋", "Experiment:", self.config.get("experiment", {}).get("name", "?"))
        _vstep("📁", "Results dir:", str(self.results_dir))
        _vstep("📦", "Corpus dir:", str(self.corpus_results_dir))
        _vstep("🔢", "Conditions:", str(len(conditions)))
        _vstep("🤖", "Models:", "  ".join(models))
        _vstep("🔁", "Repetitions:", str(len(reps)))
        _vstep("📊", "Total cells:", str(total))
        if self.dry_run:
            _vstep("🏃", "Mode:", "DRY RUN — no LLM calls will be made", style="yellow")
        _vprint()

        # ── Ollama pre-flight ────────────────────────────────────────────────
        if not self.skip_ollama_check and not self.dry_run:
            _check_ollama(
                model_names=models,
                models_yaml_path=_REPO_ROOT / "models.yaml",
                abort_on_error=True,
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
                        self.results_dir
                        / condition.id / model / f"rep{rep}" / "summary.json"
                    )

                    # ── Cell header ──────────────────────────────────────────
                    _vsection(
                        f"Cell {done + 1}/{total}  │  {condition.id}  ×  {model}  ×  rep{rep}"
                    )
                    _vstep("🔑", "Condition:", condition.label)
                    _vstep("🤖", "Model:", model)
                    _vstep("🔁", "Rep / Seed:", f"{rep} / {seed}")

                    if summary_path.exists():
                        _vstep("⏭", "Status:", "SKIPPED — summary.json already exists", style="dim")
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
                        _vstep("🏃", "Status:", "DRY RUN — skipping execution", style="yellow")
                        done += 1
                        continue

                    runner = SingleRunOrchestrator(
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

        _vprint()
        _vsection("STUDY COMPLETE")
        _vstep("✓", "Total cells run:", str(done))
        _vstep("📁", "Results saved to:", str(self.results_dir))
        _vprint()

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
        "--reset",
        action="store_true",
        help="Delete the entire results directory before starting (forces all cells to re-run)",
    )
    p.add_argument(
        "--skip-ollama-check",
        action="store_true",
        help="Skip Ollama pre-flight validation (not recommended)",
    )
    p.add_argument(
        "--list-conditions",
        action="store_true",
        help="Print all conditions and exit",
    )
    p.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Path to dataset/results/ directory (overrides experiment_config.yaml)",
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
        reset=args.reset,
        skip_ollama_check=args.skip_ollama_check,
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
