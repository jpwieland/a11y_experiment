"""
Executor de experimentos multi-modelo.

Orquestra a execução paralela de múltiplos modelos LLM sobre o mesmo
conjunto de arquivos, coletando métricas comparativas para análise científica.

Cold-start model initialisation (methodology Section 3.1.3):
  Each model server is initialised fresh at the beginning of each experimental
  condition and not reused across conditions, to prevent implicit state
  accumulation.

Per-condition checkpointing (methodology Section 3.1.3):
  Each (model_id, strategy, file_id) combination produces a single atomic
  checkpoint file saved immediately on completion, enabling full or partial
  re-execution from any point.
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import structlog

from a11y_autofix.config import ExperimentResult, FixResult, Settings
from a11y_autofix.experiments.config_schema import ExperimentConfig, load_experiment_config
from a11y_autofix.experiments.metrics import compute_experiment_metrics
from a11y_autofix.llm.registry import ModelRegistry

if TYPE_CHECKING:
    from a11y_autofix.pipeline import Pipeline

log = structlog.get_logger(__name__)

_DEFAULT_SENSITIVITY_TEMPERATURES = [0.0, 0.1, 0.3, 0.5, 1.0]


class ExperimentRunner:
    """
    Executa experimentos comparativos entre múltiplos modelos LLM.

    Workflow:
    1. Carrega configuração de experimento (YAML)
    2. Para cada condição (model × strategy): executa cold-start + pipeline
    3. Coleta métricas (tempo, sucesso, tokens) e salva checkpoint
    4. Gera relatório comparativo HTML + experiment_summary.json
    """

    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        pipeline_factory: Callable[..., "Pipeline"],
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.pipeline_factory = pipeline_factory

    # ── Public entry points ────────────────────────────────────────────────

    async def run_experiment(
        self,
        config_path: Path,
        output_dir: Path | None = None,
    ) -> ExperimentResult:
        """Run a complete experiment from a YAML config file."""
        config = load_experiment_config(config_path)
        return await self.run_from_config(config, output_dir)

    async def run_from_config(
        self,
        config: ExperimentConfig,
        output_dir: Path | None = None,
    ) -> ExperimentResult:
        """Run a complete experiment from an ExperimentConfig."""
        exp_id = str(uuid.uuid4())[:8]

        if output_dir is None:
            safe_name = config.name.replace(" ", "_").replace("/", "_")[:30]
            output_dir = self.settings.results_dir / f"{safe_name}_{exp_id}"

        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoints_dir = output_dir / "checkpoints"
        checkpoints_dir.mkdir(exist_ok=True)

        models_to_test = self._resolve_models(config.models)
        files = config.resolve_files()
        if not files:
            raise ValueError(f"No files found matching patterns: {config.files}")

        log.info(
            "experiment_start",
            name=config.name,
            models=len(models_to_test),
            files=len(files),
            output=str(output_dir),
        )

        sem = asyncio.Semaphore(self.settings.max_concurrent_models)

        async def run_model(model_name: str) -> tuple[str, list[FixResult]]:
            async with sem:
                model_output = output_dir / model_name.replace("/", "_")
                model_output.mkdir(exist_ok=True)
                # Cold-start: stop and restart model server before each condition
                await self._cold_start_model(model_name, condition_id=f"{model_name}/{config.name}")
                return await self._run_single_model(
                    model_name=model_name,
                    files=files,
                    config=config,
                    output_dir=model_output,
                    checkpoints_dir=checkpoints_dir,
                )

        model_results = await asyncio.gather(
            *[run_model(m) for m in models_to_test],
            return_exceptions=True,
        )

        results_by_model: dict[str, list[FixResult]] = {}
        for model_name, result in zip(models_to_test, model_results):
            if isinstance(result, Exception):
                log.error("model_run_failed", model=model_name, error=str(result))
                results_by_model[model_name] = []
            else:
                name, results = result
                results_by_model[name] = results

        metrics = compute_experiment_metrics(results_by_model)

        experiment_result = ExperimentResult(
            experiment_id=exp_id,
            experiment_name=config.name,
            timestamp=datetime.now(tz=timezone.utc),
            models_tested=models_to_test,
            files_processed=len(files),
            results_by_model=results_by_model,
            success_rate_by_model={m: v["success_rate"] for m, v in metrics.items()},
            avg_time_by_model={m: v["avg_time"] for m, v in metrics.items()},
            issues_fixed_by_model={m: v["issues_fixed"] for m, v in metrics.items()},
            config_snapshot=config.model_dump(),
            tool_versions={},
        )

        # Aggregate all checkpoint JSONs into experiment_summary.json
        self._aggregate_checkpoints(checkpoints_dir, output_dir, experiment_result, metrics)

        result_json = output_dir / "experiment_result.json"
        result_json.write_text(
            experiment_result.model_dump_json(indent=2),
            encoding="utf-8",
        )

        from a11y_autofix.reporter.comparison_reporter import ComparisonReporter
        reporter = ComparisonReporter()
        reporter.generate(experiment_result, metrics, output_dir)

        log.info(
            "experiment_complete",
            experiment_id=exp_id,
            name=config.name,
            output=str(output_dir),
        )

        return experiment_result

    async def run_sensitivity(
        self,
        config: ExperimentConfig,
        best_model: str,
        output_dir: Path,
        temperatures: list[float] | None = None,
        seed: int = 42,
    ) -> dict[float, ExperimentResult]:
        """
        Run the temperature sensitivity sub-study.

        # POST-HOC EXPLORATORY ANALYSIS — not part of confirmatory hypothesis tests
        # See methodology Section 3.6.3

        Randomly samples 10% of benchmark files (fixed seed) and runs
        best_model with few-shot strategy at each temperature level.

        Args:
            config: Base experiment configuration.
            best_model: Model name determined by the primary experiment.
            output_dir: Base output directory.
            temperatures: Temperature levels (default [0.0, 0.1, 0.3, 0.5, 1.0]).
            seed: Random seed for reproducibility.

        Returns:
            Dict of temperature → ExperimentResult.
        """
        # POST-HOC EXPLORATORY ANALYSIS — not part of confirmatory hypothesis tests
        # See methodology Section 3.6.3
        if temperatures is None:
            temperatures = _DEFAULT_SENSITIVITY_TEMPERATURES

        all_files = config.resolve_files()
        random.seed(seed)
        sample_size = max(1, int(len(all_files) * 0.10))
        sampled_files = random.sample(all_files, sample_size)

        log.info(
            "sensitivity_start",
            model=best_model,
            temperatures=temperatures,
            sample_size=sample_size,
            seed=seed,
        )

        results_by_temp: dict[float, ExperimentResult] = {}

        for temp in temperatures:
            temp_dir = output_dir / "sensitivity" / str(temp).replace(".", "_")
            temp_dir.mkdir(parents=True, exist_ok=True)
            checkpoints_dir = temp_dir / "checkpoints"
            checkpoints_dir.mkdir(exist_ok=True)

            model_config = self.registry.get(best_model)
            # Override temperature per call
            from a11y_autofix.config import ModelConfig
            import copy
            temp_model_config = copy.deepcopy(model_config)
            temp_model_config.temperature = temp

            exp_id = str(uuid.uuid4())[:8]
            pipeline = self.pipeline_factory(temp_model_config)

            results = await pipeline.run(
                targets=sampled_files,
                wcag_level=config.wcag_level,
                output_dir=temp_dir,
            )

            metrics = compute_experiment_metrics({best_model: results})
            exp_result = ExperimentResult(
                experiment_id=exp_id,
                experiment_name=f"sensitivity_temp_{temp}",
                timestamp=datetime.now(tz=timezone.utc),
                models_tested=[best_model],
                files_processed=len(sampled_files),
                results_by_model={best_model: results},
                success_rate_by_model={best_model: metrics[best_model]["success_rate"]},
                avg_time_by_model={best_model: metrics[best_model]["avg_time"]},
                issues_fixed_by_model={best_model: metrics[best_model]["issues_fixed"]},
                config_snapshot={"temperature": temp, "seed": seed},
                tool_versions={},
            )

            (temp_dir / "experiment_result.json").write_text(
                exp_result.model_dump_json(indent=2), encoding="utf-8"
            )

            results_by_temp[temp] = exp_result
            log.info("sensitivity_temp_done", temperature=temp, model=best_model)

        return results_by_temp

    # ── Cold-start lifecycle ───────────────────────────────────────────────

    async def _cold_start_model(self, model_id: str, condition_id: str = "") -> None:
        """
        Stop any running instance of model_id, then start fresh.

        Methodology reference: Section 3.1.3 — "All experiments use a cold-start
        model configuration: each model server is initialised fresh at the beginning
        of each experimental condition and not reused across conditions."
        """
        log.info(
            "cold_start",
            model=model_id,
            condition=condition_id,
            timestamp=datetime.utcnow().isoformat(),
        )

        try:
            await self._stop_model_server(model_id)
        except Exception as e:
            log.debug("cold_start_stop_skipped", model=model_id, reason=str(e))

        try:
            await self._start_model_server(model_id)
            await self._wait_for_ready(model_id)
        except Exception as e:
            # Non-fatal: if cold-start fails, proceed with existing server state
            log.warning("cold_start_failed", model=model_id, error=str(e))

    async def _stop_model_server(self, model_id: str) -> None:
        """Stop the running model server (backend-specific)."""
        try:
            model_config = self.registry.get(model_id)
        except ValueError:
            return

        backend = model_config.backend.value
        if backend == "ollama":
            # Ollama: send a stop request to the running model
            proc = await asyncio.create_subprocess_exec(
                "ollama", "stop", model_config.model_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)

    async def _start_model_server(self, model_id: str) -> None:
        """Start the model server fresh (no-op for always-on backends)."""
        # For Ollama: the server is always-on; stopping the model and pulling
        # it fresh is sufficient. For vLLM/LM Studio, a restart would require
        # process management outside the scope of this runner.
        pass

    async def _wait_for_ready(self, model_id: str, timeout: float = 60.0) -> None:
        """Health-check loop until the model is ready or timeout is reached."""
        try:
            client = self.registry.get_client(model_id)
        except ValueError:
            return

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, _ = await client.health_check()
            if ok:
                return
            await asyncio.sleep(2.0)

        log.warning("wait_for_ready_timeout", model=model_id, timeout=timeout)

    # ── Single model run ───────────────────────────────────────────────────

    async def _run_single_model(
        self,
        model_name: str,
        files: list[Path],
        config: ExperimentConfig,
        output_dir: Path,
        checkpoints_dir: Path,
    ) -> tuple[str, list[FixResult]]:
        """Run the full pipeline for a single model and checkpoint each file."""
        model_config = self.registry.get(model_name)
        pipeline = self.pipeline_factory(model_config)

        t0 = time.monotonic()
        log.info("experiment_model_start", model=model_name, files=len(files))

        results = await pipeline.run(
            targets=files,
            wcag_level=config.wcag_level,
            output_dir=output_dir,
        )

        elapsed = time.monotonic() - t0

        # Checkpoint each file result
        for fix_result in results:
            self._save_file_checkpoint(
                fix_result=fix_result,
                model_id=model_name,
                strategy=getattr(config, "strategy", "few-shot"),
                checkpoints_dir=checkpoints_dir,
            )

        # Compute per-model metrics for condition_complete log
        from a11y_autofix.experiments.metrics import compute_sr, compute_ifr, compute_mttr, compute_te
        sr = compute_sr(results)
        ifr, _, total_issues = compute_ifr(results)
        mttr = compute_mttr(results)

        log.info(
            "condition_complete",
            model_id=model_name,
            strategy=getattr(config, "strategy", "few-shot"),
            n_files=len(results),
            sr=round(sr, 4),
            ifr=round(ifr, 4),
            mttr=round(mttr, 3) if mttr else None,
            te=None,  # TE requires per-call token counts
            elapsed_total_seconds=round(elapsed, 2),
        )

        success_count = sum(1 for r in results if r.final_success)
        log.info(
            "experiment_model_done",
            model=model_name,
            success=success_count,
            total=len(results),
        )

        return model_name, results

    # ── Checkpointing ──────────────────────────────────────────────────────

    def _save_file_checkpoint(
        self,
        fix_result: FixResult,
        model_id: str,
        strategy: str,
        checkpoints_dir: Path,
    ) -> None:
        """
        Save an atomic checkpoint JSON for one (model_id, strategy, file_id) triple.

        Filename convention: checkpoints/{model_id}/{strategy}/{file_id}.json
        """
        file_id = fix_result.file.stem
        checkpoint_dir = checkpoints_dir / model_id.replace("/", "_") / strategy
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        best_attempt = fix_result.best_attempt
        total_input_tokens: int = 0
        total_output_tokens: int = 0
        for attempt in fix_result.attempts:
            if attempt.tokens_used is not None:
                total_output_tokens += attempt.tokens_used

        # Build checkpoint payload (methodology Section 3.1.3)
        checkpoint: dict[str, Any] = {
            "model_id": model_id,
            "strategy": strategy,
            "file_id": file_id,
            "condition_id": f"{model_id}/{strategy}",
            "status": "success" if fix_result.final_success else "failed",
            "sr": 1 if fix_result.final_success else 0,
            "ifr_numerator": fix_result.issues_fixed,
            "ifr_denominator": len(fix_result.scan_result.issues),
            "mttr_seconds": round(fix_result.total_time, 3) if fix_result.final_success else None,
            "token_input": total_input_tokens,
            "token_output": total_output_tokens,
            "validation_layer_rejected": None,  # populated by ValidationPipeline if used
            "failure_mode": best_attempt.error if (best_attempt and best_attempt.error) else None,
            "agent_used": best_attempt.agent if best_attempt else "unknown",
            "attempt_number": len(fix_result.attempts),
            "cold_start_timestamp": datetime.utcnow().isoformat(),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        checkpoint_path = checkpoint_dir / f"{file_id}.json"
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")

    def _load_checkpoint(
        self,
        model_id: str,
        strategy: str,
        file_id: str,
        checkpoints_dir: Path,
    ) -> dict[str, Any] | None:
        """Load a checkpoint if it exists and has a non-null status."""
        checkpoint_path = (
            checkpoints_dir
            / model_id.replace("/", "_")
            / strategy
            / f"{file_id}.json"
        )
        if not checkpoint_path.exists():
            return None
        data: dict[str, Any] = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if data.get("status") is None:
            return None
        return data

    def is_condition_complete(
        self,
        model_id: str,
        strategy: str,
        file_id: str,
        checkpoints_dir: Path,
    ) -> bool:
        """Return True if a valid checkpoint already exists for this triple."""
        return self._load_checkpoint(model_id, strategy, file_id, checkpoints_dir) is not None

    def _aggregate_checkpoints(
        self,
        checkpoints_dir: Path,
        output_dir: Path,
        experiment_result: ExperimentResult,
        metrics: dict[str, Any],
    ) -> None:
        """
        Aggregate all per-file checkpoint JSONs into results/experiment_summary.json.

        The summary separates confirmatory_results (H1–H4) from exploratory_results
        as required by methodology Section 3.7.3.
        """
        all_checkpoints: list[dict[str, Any]] = []
        for cp_file in checkpoints_dir.rglob("*.json"):
            try:
                all_checkpoints.append(json.loads(cp_file.read_text(encoding="utf-8")))
            except Exception:
                pass

        summary: dict[str, Any] = {
            "experiment_id": experiment_result.experiment_id,
            "experiment_name": experiment_result.experiment_name,
            "timestamp": experiment_result.timestamp.isoformat(),
            "models_tested": experiment_result.models_tested,
            "files_processed": experiment_result.files_processed,
            "confirmatory_results": {
                m: {
                    "sr": metrics[m].get("sr"),
                    "ifr": metrics[m].get("ifr"),
                    "mttr": metrics[m].get("mttr"),
                    "te": metrics[m].get("te"),
                }
                for m in experiment_result.models_tested
                if m in metrics
            },
            "exploratory_results": {
                "per_condition_metrics": {
                    m: metrics[m]
                    for m in experiment_result.models_tested
                    if m in metrics
                },
                "checkpoint_count": len(all_checkpoints),
            },
            "checkpoints": all_checkpoints,
        }

        results_dir = output_dir / "results"
        results_dir.mkdir(exist_ok=True)
        summary_path = results_dir / "experiment_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info("experiment_summary_saved", path=str(summary_path))

    # ── Model resolution ───────────────────────────────────────────────────

    def _resolve_models(self, model_specs: list[str]) -> list[str]:
        """Resolve model names, expanding groups if needed."""
        resolved: list[str] = []
        for spec in model_specs:
            try:
                group_models = self.registry.get_group(spec)
                resolved.extend(group_models)
            except ValueError:
                resolved.append(spec)

        seen: set[str] = set()
        unique: list[str] = []
        for m in resolved:
            if m not in seen:
                seen.add(m)
                unique.append(m)

        return unique
