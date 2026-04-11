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
import copy
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
from a11y_autofix.utils.gpu_monitor import GpuMonitor

if TYPE_CHECKING:
    from a11y_autofix.pipeline import Pipeline

log = structlog.get_logger(__name__)


# ── Auto-clone helpers ─────────────────────────────────────────────────────────

def _load_catalog_urls(catalog_path: Path) -> dict[str, str]:
    """Carrega mapa project_id → github_url do catalog YAML."""
    try:
        import yaml
        with open(catalog_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {
            p.get("id", ""): p.get("github_url", "")
            for p in data.get("projects", [])
            if p.get("id") and p.get("github_url")
        }
    except Exception as exc:
        log.warning("catalog_load_failed", error=str(exc))
        return {}


def _clone_snapshot(project_id: str, github_url: str,
                    snapshot_dir: Path, clone_log: Path) -> bool:
    """
    Clona um repositório em snapshot_dir via git clone --depth 1.
    Registra o evento em clone_log (JSONL).
    Retorna True em sucesso.
    """
    event: dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "project_id": project_id,
        "github_url": github_url,
        "snapshot_dir": str(snapshot_dir),
        "status": "pending",
    }
    t0 = time.monotonic()
    try:
        snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch",
             github_url, str(snapshot_dir)],
            capture_output=True, text=True, timeout=300,
        )
        elapsed = round(time.monotonic() - t0, 1)
        if result.returncode == 0:
            event.update({"status": "cloned", "elapsed_s": elapsed})
            log.info("snapshot_auto_cloned", project_id=project_id,
                     elapsed_s=elapsed, path=str(snapshot_dir))
        else:
            event.update({"status": "error", "error": result.stderr[:300],
                          "elapsed_s": elapsed})
            log.error("snapshot_clone_failed", project_id=project_id,
                      error=result.stderr[:200])
    except Exception as exc:
        event.update({"status": "error", "error": str(exc)})
        log.error("snapshot_clone_exception", project_id=project_id, error=str(exc))

    # Gravar no log JSONL
    try:
        clone_log.parent.mkdir(parents=True, exist_ok=True)
        with open(clone_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return event["status"] == "cloned"


def ensure_snapshots(config: ExperimentConfig, output_dir: Path) -> list[str]:
    """
    Verifica se todos os diretórios de snapshot do config existem.
    Clona automaticamente os que estiverem faltando.

    Retorna lista de project_ids clonados nesta chamada.
    """
    from pathlib import Path as _Path
    repo_root = _Path(__file__).parent.parent.parent
    catalog_path = repo_root / "dataset" / "catalog" / "projects.yaml"
    clone_log = output_dir / "auto_clone.jsonl"

    # Resolver quais diretórios de snapshot são referenciados
    snapshot_dirs: list[tuple[str, _Path]] = []
    for pattern in config.files:
        p = _Path(pattern) if _Path(pattern).is_absolute() else repo_root / pattern
        # O padrão é um diretório de snapshot, não um glob de arquivo
        if "snapshots" in str(p):
            project_id = p.name
            snapshot_dirs.append((project_id, p))

    if not snapshot_dirs:
        return []

    catalog_urls = _load_catalog_urls(catalog_path)
    cloned: list[str] = []

    for project_id, snap_dir in snapshot_dirs:
        if snap_dir.exists() and any(snap_dir.iterdir()):
            continue  # snapshot já está presente

        github_url = catalog_urls.get(project_id, "")
        if not github_url:
            log.warning("snapshot_missing_no_url", project_id=project_id,
                        hint="Add to catalog or check project_id spelling")
            continue

        log.info("snapshot_missing_cloning", project_id=project_id,
                 github_url=github_url)
        ok = _clone_snapshot(project_id, github_url, snap_dir, clone_log)
        if ok:
            cloned.append(project_id)

    return cloned


# ── Progress tracking ──────────────────────────────────────────────────────────

class _ProgressTracker:
    """
    Escreve um JSON de progresso em disco após cada arquivo processado.
    Permite que watch_experiment.py leia o estado em tempo real.

    Campos adicionais por modelo:
      started_at         — ISO timestamp de quando o modelo começou
      avg_time_per_file_s — média móvel (últimas 10 amostras) de tempo por arquivo
      eta_seconds        — estimativa de segundos restantes para o modelo
      tokens_input       — total de tokens de input consumidos
      tokens_output      — total de tokens de output produzidos
    """

    _DEFAULT_MODEL_STATE: dict[str, Any] = {
        "done": 0, "success": 0, "failed": 0,
        "issues_fixed": 0, "issues_total": 0,
        "current_file": None, "status": "waiting",
        "started_at": None,
        "avg_time_per_file_s": None,
        "eta_seconds": None,
        "tokens_input": 0,
        "tokens_output": 0,
    }

    def __init__(self, output_dir: Path, models: list[str],
                 total_files: int) -> None:
        self.path = output_dir / "experiment_progress.json"
        self.total_files = total_files
        self._state: dict[str, Any] = {
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_files": total_files,
            "models": {m: dict(self._DEFAULT_MODEL_STATE) for m in models},
        }
        self._lock = asyncio.Lock()
        # Histórico de tempos por arquivo para média móvel
        self._file_times: dict[str, list[float]] = {m: [] for m in models}
        self._flush()

    async def update(
        self,
        model: str,
        file_name: str,
        success: bool,
        issues_fixed: int,
        issues_total: int,
        status: str = "running",
        time_seconds: float = 0.0,
        tokens_input: int = 0,
        tokens_output: int = 0,
    ) -> None:
        async with self._lock:
            m = self._state["models"].setdefault(model, dict(self._DEFAULT_MODEL_STATE))
            m["done"] += 1
            m["issues_fixed"] += issues_fixed
            m["issues_total"] += issues_total
            m["current_file"] = file_name
            m["status"] = status
            m["tokens_input"] = (m.get("tokens_input") or 0) + tokens_input
            m["tokens_output"] = (m.get("tokens_output") or 0) + tokens_output
            if success:
                m["success"] += 1
            else:
                m["failed"] += 1

            # ── ETA via média móvel das últimas 10 amostras ───────────────────
            if time_seconds > 0:
                times = self._file_times.setdefault(model, [])
                times.append(time_seconds)
                recent = times[-10:]
                avg = sum(recent) / len(recent)
                m["avg_time_per_file_s"] = round(avg, 1)
                remaining = max(0, self.total_files - m["done"])
                m["eta_seconds"] = round(avg * remaining)

            self._state["last_update"] = datetime.now(tz=timezone.utc).isoformat()
            self._flush()

    async def set_model_status(self, model: str, status: str,
                               current_file: str | None = None) -> None:
        async with self._lock:
            m = self._state["models"].setdefault(model, dict(self._DEFAULT_MODEL_STATE))
            m["status"] = status
            # Registrar timestamp de início ao entrar em execução
            if status == "running" and m.get("started_at") is None:
                m["started_at"] = datetime.now(tz=timezone.utc).isoformat()
            if current_file is not None:
                m["current_file"] = current_file
            self._state["last_update"] = datetime.now(tz=timezone.utc).isoformat()
            self._flush()

    def finish(self) -> None:
        for m in self._state["models"].values():
            if m["status"] not in ("done", "error"):
                m["status"] = "done"
            m["eta_seconds"] = 0
        self._state["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
        self._flush()

    def _flush(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

# ── Scan result cache ──────────────────────────────────────────────────────────

class ScanResultCache:
    """
    Cache de ScanResult persistido em disco.

    Compartilhado entre TODOS os modelos do experimento: o scan é executado
    apenas uma vez e os resultados são reutilizados nas rodadas subsequentes,
    economizando N_models × scan_time por arquivo.

    Formato em disco: scan_cache.json com lista de ScanResult serializados.
    Chave de busca: str(file.resolve()) para paths absolutos e consistentes.
    """

    _VERSION = 2

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path
        self._data: dict[str, Any] = {}

    def load(self) -> int:
        """Carrega cache do disco. Retorna número de entradas carregadas."""
        if not self._path.exists():
            return 0
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("version", 1) < self._VERSION:
                log.info("scan_cache_version_mismatch", path=str(self._path))
                return 0
            self._data = raw.get("scans", {})
            log.info("scan_cache_loaded", entries=len(self._data), path=str(self._path))
            return len(self._data)
        except Exception as exc:
            log.warning("scan_cache_load_failed", error=str(exc))
            self._data = {}
            return 0

    def save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(
                    {"version": self._VERSION, "scans": self._data},
                    ensure_ascii=False,
                    indent=None,       # compacto — pode ter milhares de entradas
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("scan_cache_save_failed", error=str(exc))

    def has(self, file: Path) -> bool:
        return str(file.resolve()) in self._data

    def get(self, file: Path) -> "Any | None":
        """Retorna ScanResult desserializado ou None."""
        from a11y_autofix.config import ScanResult
        entry = self._data.get(str(file.resolve()))
        if entry is None:
            return None
        try:
            return ScanResult.model_validate(entry)
        except Exception:
            return None

    def put(self, result: "Any") -> None:
        """Persiste um ScanResult no cache (em memória; chame save() para disco)."""
        key = str(result.file.resolve())
        self._data[key] = result.model_dump(mode="json")

    def to_dict(self) -> "dict[str, Any]":
        """Retorna dict str(path) → ScanResult para injeção no Pipeline."""
        from a11y_autofix.config import ScanResult
        out: dict[str, Any] = {}
        for k, v in self._data.items():
            try:
                out[k] = ScanResult.model_validate(v)
            except Exception:
                pass
        return out

    def __len__(self) -> int:
        return len(self._data)

    @staticmethod
    def _is_page_level_issue(issue: dict[str, Any]) -> bool:
        """
        Retorna True se o issue é uma regra de nível de página que gera
        falso positivo sistemático em harness de componente isolado.

        Essas regras (page-has-heading-one, landmark-one-main, etc.) verificam
        propriedades da página como um todo e não podem ser corrigidas em
        componentes individuais. Elas não devem entrar no dataset de experimento.

        A filtragem primária ocorre em DetectionProtocol._group_findings(), mas
        resultados pré-compilados importados de dataset/results/ bypassam o
        protocolo, então precisamos re-filtrar aqui.
        """
        from a11y_autofix.protocol.detection import PAGE_LEVEL_RULES_EXCLUDED

        # Checar rule_id nos findings (forma mais confiável)
        for finding in issue.get("findings", []):
            rule_id = (finding.get("rule_id") or "").lower()
            if rule_id in PAGE_LEVEL_RULES_EXCLUDED:
                return True

        # Fallback: checar pelo seletor html + message típica de page-level
        selector = (issue.get("selector") or "").lower()
        message = (issue.get("message") or "").lower()
        if selector == "html" and (
            "heading" in message or "landmark" in message or "main" in message
        ):
            return True

        return False

    def import_from_dataset_results(
        self,
        repo_root: Path,
        project_ids: list[str],
    ) -> int:
        """
        Importa resultados de scan já compilados de dataset/results/<project>/scan_results.json.

        Os paths dentro dos JSONs são absolutos da máquina onde o scan foi feito
        (podem ser Windows enquanto esta máquina é Linux, ou ter caminhos diferentes).
        A normalização resolve o sufixo relativo a partir de 'dataset/snapshots/<project>/'
        e o remapeia para o path atual.

        IMPORTANTE: Issues de nível de página (page-has-heading-one, landmark-one-main, etc.)
        são filtrados durante a importação. Eles bypassam o DetectionProtocol quando carregados
        de arquivos pré-compilados, gerando 75%+ de falsos positivos não-corrigíveis no dataset.

        Retorna o número de entradas novas importadas.
        """
        results_dir = repo_root / "dataset" / "results"
        snapshots_dir = repo_root / "dataset" / "snapshots"
        imported = 0
        page_level_filtered = 0

        for project_id in project_ids:
            scan_file = results_dir / project_id / "scan_results.json"
            if not scan_file.exists():
                log.debug("dataset_scan_missing", project=project_id)
                continue

            try:
                entries: list[dict[str, Any]] = json.loads(
                    scan_file.read_text(encoding="utf-8")
                )
            except Exception as exc:
                log.warning("dataset_scan_load_failed",
                            project=project_id, error=str(exc))
                continue

            project_snap_dir = snapshots_dir / project_id

            for entry in entries:
                raw_file_str: str = entry.get("file", "")
                if not raw_file_str:
                    continue

                # Normalizar path: extrair sufixo relativo ao snapshot do projeto.
                # O separador pode ser '\' (Windows) ou '/' (Linux/Mac).
                normalized = raw_file_str.replace("\\", "/")

                # Encontrar o ponto de corte: .../dataset/snapshots/<project>/...
                marker = f"dataset/snapshots/{project_id}/"
                idx = normalized.find(marker)
                if idx == -1:
                    # Fallback: tentar só pelo nome do projeto
                    marker2 = f"{project_id}/"
                    idx2 = normalized.find(marker2)
                    if idx2 == -1:
                        continue
                    rel_suffix = normalized[idx2 + len(marker2):]
                else:
                    rel_suffix = normalized[idx + len(marker):]

                # Reconstruir path absoluto correto para esta máquina
                local_path = (project_snap_dir / rel_suffix).resolve()
                key = str(local_path)

                if key in self._data:
                    continue  # já no cache

                # Reescrever 'file' e campos aninhados com o path local
                entry_copy = dict(entry)
                entry_copy["file"] = str(local_path)

                # Reescrever 'file' dentro dos issues E filtrar issues de nível de página.
                # Resultados pré-compilados bypassam o DetectionProtocol, então
                # precisamos re-aplicar o filtro de regras page-level aqui.
                new_issues = []
                for iss in entry_copy.get("issues", []):
                    iss2 = dict(iss)
                    iss2["file"] = str(local_path)
                    if self._is_page_level_issue(iss2):
                        page_level_filtered += 1
                        continue
                    new_issues.append(iss2)
                entry_copy["issues"] = new_issues

                self._data[key] = entry_copy
                imported += 1

        if page_level_filtered > 0:
            log.info(
                "dataset_import_page_level_filtered",
                filtered=page_level_filtered,
                reason="page-level rules (page-has-heading-one, landmark-one-main, etc.) "
                       "generate systematic false positives in component harness",
            )

        return imported


_DEFAULT_SENSITIVITY_TEMPERATURES = [0.0, 0.1, 0.3, 0.5, 1.0]


def _override_concurrency(settings: "Settings", max_concurrent_agents: int) -> "Settings":
    """
    Retorna uma cópia das settings com max_concurrent_agents sobrescrito.
    Usado pelo scheduler dinâmico para ajustar o paralelismo por modelo.
    """
    import copy
    s = copy.copy(settings)
    object.__setattr__(s, "max_concurrent_agents", max_concurrent_agents)
    return s


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

        # ── Auto-clone snapshots ausentes ─────────────────────────────────────
        cloned = ensure_snapshots(config, output_dir)
        if cloned:
            log.info("snapshots_auto_cloned", count=len(cloned), projects=cloned)

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

        # ── Scan cache compartilhado entre todos os modelos ──────────────────────
        # O pipeline ESCREVE resultados de scan neste dict à medida que processa
        # cada arquivo (streaming). Quando o modelo 2 começa, o cache já está
        # populado pelo modelo 1 — nenhum arquivo é escaneado duas vezes.
        # A persistência em disco acontece após cada modelo terminar.
        scan_cache = ScanResultCache(output_dir / "scan_cache.json")
        cached_count = scan_cache.load()

        # ── Importar scans já compilados de dataset/results/ ──────────────────
        # Aproveita os resultados de scan executados anteriormente na fase de
        # preparação do dataset, evitando qualquer re-scan desses arquivos.
        repo_root = Path(__file__).parent.parent.parent
        project_ids = self._extract_project_ids(config)
        if project_ids:
            imported = scan_cache.import_from_dataset_results(repo_root, project_ids)
            if imported > 0:
                scan_cache.save()
                log.info(
                    "dataset_scans_imported",
                    imported=imported,
                    total_cached=len(scan_cache),
                    projects=len(project_ids),
                )

        # scan_dict é o dict mutável compartilhado — pipeline lê E escreve nele
        scan_dict: dict[str, Any] = scan_cache.to_dict()

        files_to_scan = len(files) - sum(1 for f in files if str(f.resolve()) in scan_dict or
                                         str(f) in scan_dict)
        log.info(
            "scan_cache_ready",
            cached=len(scan_cache),
            total_files=len(files),
            files_to_scan=max(0, files_to_scan),
            strategy="streaming",
        )

        # ── Progress tracker (lido por watch_experiment.py) ───────────────────
        tracker = _ProgressTracker(output_dir, models_to_test, len(files))

        # ── GPU monitor para paralelismo dinâmico ─────────────────────────────
        gpu_monitor = GpuMonitor(poll_interval=5.0)
        gpu_available = await gpu_monitor.start()
        if gpu_available:
            log.info("gpu_monitor_active", stats=gpu_monitor.format_stats())
        else:
            log.info("gpu_monitor_inactive", reason="nvidia-smi not available — using static concurrency")

        sem = asyncio.Semaphore(self.settings.max_concurrent_models)

        async def run_model(model_name: str) -> tuple[str, list[FixResult]]:
            async with sem:
                model_output = output_dir / model_name.replace("/", "_")
                model_output.mkdir(exist_ok=True)
                await tracker.set_model_status(model_name, "loading")

                # Decidir concorrência de LLM baseada em VRAM disponível
                # antes do cold-start (modelo ainda não carregado = mais VRAM livre)
                model_vram_gb = self._estimate_model_vram(model_name)
                llm_concurrency = gpu_monitor.recommend_concurrency(
                    model_vram_gb=model_vram_gb,
                    base=self.settings.max_concurrent_agents,
                )
                log.info("dynamic_concurrency_set",
                         model=model_name,
                         llm_concurrency=llm_concurrency,
                         gpu_free_gb=f"{gpu_monitor.stats.vram_free_gb:.1f}" if gpu_available else "n/a")

                # Cold-start: stop and restart model server before each condition
                await self._cold_start_model(model_name, condition_id=f"{model_name}/{config.name}")
                await tracker.set_model_status(model_name, "running")

                # Após cold-start o modelo está carregado: recalcular com VRAM real
                await asyncio.sleep(2.0)  # aguardar nvidia-smi atualizar
                llm_concurrency_loaded = gpu_monitor.recommend_concurrency(
                    model_vram_gb=model_vram_gb,
                    base=self.settings.max_concurrent_agents,
                )
                if llm_concurrency_loaded != llm_concurrency:
                    log.info("dynamic_concurrency_adjusted",
                             model=model_name,
                             before=llm_concurrency,
                             after=llm_concurrency_loaded,
                             gpu_free_gb=f"{gpu_monitor.stats.vram_free_gb:.1f}" if gpu_available else "n/a")
                    llm_concurrency = llm_concurrency_loaded

                result = await self._run_single_model(
                    model_name=model_name,
                    files=files,
                    config=config,
                    output_dir=model_output,
                    checkpoints_dir=checkpoints_dir,
                    tracker=tracker,
                    llm_concurrency=llm_concurrency,
                    gpu_monitor=gpu_monitor,
                    scan_cache=scan_cache,
                    scan_dict=scan_dict,
                )
                # Persistir resultados de scan acumulados durante este modelo
                # para que o próximo modelo os encontre no cache em disco
                self._flush_scan_dict_to_cache(scan_dict, scan_cache)
                await tracker.set_model_status(model_name, "done")
                return result

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
        tracker.finish()
        await gpu_monitor.stop()

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

    def _estimate_model_vram(self, model_name: str) -> float:
        """
        Estima VRAM necessária em GB para um modelo, baseado no nome.
        Usado pelo scheduler dinâmico para calcular headroom disponível.
        Fallback conservador: 10 GB para modelos ~14B Q4.
        """
        name_lower = model_name.lower()
        if "32b" in name_lower or "33b" in name_lower:
            return 20.0
        if "14b" in name_lower or "16b" in name_lower or "15b" in name_lower:
            return 10.0
        if "7b" in name_lower or "8b" in name_lower:
            return 6.0
        if "3b" in name_lower or "1b" in name_lower:
            return 3.0
        return 10.0  # conservador

    async def _run_single_model(
        self,
        model_name: str,
        files: list[Path],
        config: ExperimentConfig,
        output_dir: Path,
        checkpoints_dir: Path,
        tracker: "_ProgressTracker | None" = None,
        llm_concurrency: int | None = None,
        gpu_monitor: "GpuMonitor | None" = None,
        scan_cache: "ScanResultCache | None" = None,
        scan_dict: "dict[str, Any] | None" = None,
    ) -> tuple[str, list[FixResult]]:
        """
        Executa o pipeline para um único modelo.

        Otimizações:
        - Arquivos com checkpoint válido são recuperados do disco sem chamar o LLM.
        - Scan results são injetados do ScanResultCache pré-computado (sem re-scan).
        - O pipeline processa apenas os arquivos pendentes.
        """
        strategy = getattr(config, "strategy", "few-shot")
        model_config = self.registry.get(model_name)
        pipeline = self.pipeline_factory(model_config)

        # Usar concorrência dinâmica fornecida ou cair no default das settings
        effective_concurrency = llm_concurrency or self.settings.max_concurrent_agents
        pipeline.settings = _override_concurrency(pipeline.settings, effective_concurrency)

        # ── Separar arquivos já checkpointed dos pendentes ─────────────────────
        pending_files: list[Path] = []
        resumed_results: list[FixResult] = []

        for f in files:
            if self.is_condition_complete(model_name, strategy, f.stem, checkpoints_dir):
                cp = self._load_checkpoint(model_name, strategy, f.stem, checkpoints_dir)
                if cp:
                    scan = (scan_cache.get(f) if scan_cache else None)
                    if scan is None:
                        scan = self._stub_scan(f, cp)
                    resumed_results.append(self._checkpoint_to_fix_result(cp, f, scan))
                else:
                    pending_files.append(f)
            else:
                pending_files.append(f)

        if resumed_results:
            log.info(
                "checkpoint_resume",
                model=model_name,
                skipped=len(resumed_results),
                pending=len(pending_files),
            )
            # Atualizar tracker para arquivos já concluídos (sem bloquear)
            if tracker:
                for r in resumed_results:
                    tokens_out = sum(a.tokens_used or 0 for a in r.attempts)
                    await tracker.update(
                        model=model_name,
                        file_name=r.file.name,
                        success=r.final_success,
                        issues_fixed=r.issues_fixed,
                        issues_total=len(r.scan_result.issues),
                        time_seconds=r.total_time,
                        tokens_output=tokens_out,
                    )

        if not pending_files:
            log.info("all_files_checkpointed", model=model_name, total=len(resumed_results))
            return model_name, resumed_results

        t0 = time.monotonic()
        log.info(
            "experiment_model_start",
            model=model_name,
            pending=len(pending_files),
            resumed=len(resumed_results),
            llm_concurrency=effective_concurrency,
        )

        # scan_dict é o dict mutável compartilhado passado pelo caller.
        # O pipeline escreve novos resultados de scan nele (streaming).
        # Se não recebido, construir a partir do scan_cache local.
        if scan_dict is None:
            scan_dict = scan_cache.to_dict() if scan_cache else None

        # Callback chamado pelo pipeline após cada arquivo
        async def _on_file_done(fix_result: FixResult) -> None:
            if tracker:
                tokens_out = sum(a.tokens_used or 0 for a in fix_result.attempts)
                await tracker.update(
                    model=model_name,
                    file_name=fix_result.file.name,
                    success=fix_result.final_success,
                    issues_fixed=fix_result.issues_fixed,
                    issues_total=len(fix_result.scan_result.issues),
                    time_seconds=fix_result.total_time,
                    tokens_output=tokens_out,
                )
            self._save_file_checkpoint(
                fix_result=fix_result,
                model_id=model_name,
                strategy=strategy,
                checkpoints_dir=checkpoints_dir,
            )

        new_results = await pipeline.run(
            targets=pending_files,
            wcag_level=config.wcag_level,
            output_dir=output_dir,
            on_file_done=_on_file_done,
            scan_cache=scan_dict,
        )

        elapsed = time.monotonic() - t0

        # Fallback checkpoint para arquivos que o callback não cobriu
        for fix_result in new_results:
            fid = fix_result.file.stem
            cp_path = (
                checkpoints_dir
                / model_name.replace("/", "_")
                / strategy
                / f"{fid}.json"
            )
            if not cp_path.exists():
                self._save_file_checkpoint(
                    fix_result=fix_result,
                    model_id=model_name,
                    strategy=strategy,
                    checkpoints_dir=checkpoints_dir,
                )

        results = resumed_results + new_results

        # Compute per-model metrics for condition_complete log
        from a11y_autofix.experiments.metrics import compute_sr, compute_ifr, compute_mttr
        sr = compute_sr(results)
        ifr, _, total_issues = compute_ifr(results)
        mttr = compute_mttr(results)

        log.info(
            "condition_complete",
            model_id=model_name,
            strategy=strategy,
            n_files=len(results),
            n_resumed=len(resumed_results),
            n_new=len(new_results),
            sr=round(sr, 4),
            ifr=round(ifr, 4),
            mttr=round(mttr, 3) if mttr else None,
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

    def _flush_scan_dict_to_cache(
        self,
        scan_dict: dict[str, Any],
        scan_cache: "ScanResultCache",
    ) -> None:
        """
        Sincroniza novos scan results do dict mutável para o ScanResultCache
        e salva em disco. Chamado após cada modelo para que o próximo modelo
        encontre o cache populado sem refazer nenhum scan.
        """
        from a11y_autofix.config import ScanResult
        new_entries = 0
        for path_str, sr in scan_dict.items():
            if isinstance(sr, ScanResult) and not scan_cache.has(sr.file):
                scan_cache.put(sr)
                new_entries += 1
        if new_entries > 0:
            scan_cache.save()
            log.info("scan_cache_flushed", new_entries=new_entries, total=len(scan_cache))

    def _stub_scan(self, file: Path, cp: dict[str, Any]) -> "Any":
        """
        Cria ScanResult mínimo a partir dos metadados do checkpoint.
        Usado quando o scan_cache não tem entrada para o arquivo.
        """
        from a11y_autofix.config import (
            A11yIssue, Complexity, Confidence, IssueType, ScanResult,
        )
        n_issues = cp.get("ifr_denominator", 0)
        issues = [
            A11yIssue(
                file=str(file),
                selector="",
                issue_type=IssueType.OTHER,
                complexity=Complexity.SIMPLE,
                confidence=Confidence.HIGH,
                message="(restored from checkpoint)",
            )
            for _ in range(n_issues)
        ]
        return ScanResult(
            file=file,
            file_hash="sha256:checkpoint",
            issues=issues,
            scan_time=0.0,
        )

    def _checkpoint_to_fix_result(
        self,
        cp: dict[str, Any],
        file: Path,
        scan_result: "Any",
    ) -> FixResult:
        """Reconstrói FixResult a partir de um checkpoint JSON existente."""
        from a11y_autofix.config import FixAttempt
        attempts = [
            FixAttempt(
                attempt_number=a.get("n", 1),
                agent=a.get("agent", "unknown"),
                model=cp.get("model_id", "unknown"),
                timestamp=datetime.now(tz=timezone.utc),
                success=a.get("success", False),
                tokens_used=a.get("token_total"),
                tokens_prompt=a.get("token_prompt"),
                tokens_completion=a.get("token_completion"),
                time_seconds=a.get("time_s", 0.0),
            )
            for a in cp.get("attempts_detail", [])
        ]
        n_total = cp.get("ifr_denominator", len(scan_result.issues))
        n_fixed = cp.get("ifr_numerator", 0)
        return FixResult(
            file=file,
            scan_result=scan_result,
            attempts=attempts,
            final_success=cp.get("status") == "success",
            issues_fixed=n_fixed,
            issues_pending=max(0, n_total - n_fixed),
            total_time=cp.get("total_time_seconds", 0.0),
        )

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

        best_attempt = fix_result.best_attempt  # first successful attempt (or None)
        # For failure diagnostics, use the last attempt regardless of success
        last_attempt = fix_result.attempts[-1] if fix_result.attempts else None
        total_input_tokens: int = 0
        total_output_tokens: int = 0
        total_tokens_all: int = 0
        for attempt in fix_result.attempts:
            if attempt.tokens_prompt is not None:
                total_input_tokens += attempt.tokens_prompt
            if attempt.tokens_completion is not None:
                total_output_tokens += attempt.tokens_completion
            elif attempt.tokens_used is not None:
                # fallback: quando tokens_prompt/completion não disponíveis,
                # usar tokens_used como proxy de output (conservador)
                total_output_tokens += attempt.tokens_used
            if attempt.tokens_used is not None:
                total_tokens_all += attempt.tokens_used

        # Build checkpoint payload (methodology Section 3.1.3)
        # Coletar diff_lines e complexidade dos issues para análise posterior
        best_diff_lines = len(best_attempt.diff.splitlines()) if best_attempt and best_attempt.diff else 0
        issue_types = list({i.issue_type.value for i in fix_result.scan_result.issues})
        complexities = list({i.complexity.value for i in fix_result.scan_result.issues})
        wcag_criteria = list({i.wcag_criteria for i in fix_result.scan_result.issues if i.wcag_criteria})
        # Deriving WCAG principles from criteria (1.x → P1, 2.x → P2, 3.x → P3, 4.x → P4)
        wcag_principles = list({f"P{c[0]}" for c in wcag_criteria if c and c[0].isdigit()})
        tool_consensus_avg = (
            sum(i.tool_consensus for i in fix_result.scan_result.issues) / len(fix_result.scan_result.issues)
            if fix_result.scan_result.issues else 0.0
        )

        checkpoint: dict[str, Any] = {
            # Identificação da condição experimental
            "model_id": model_id,
            "strategy": strategy,
            "file_id": file_id,
            "condition_id": f"{model_id}/{strategy}",

            # Métricas primárias (methodology Section 3.7.1)
            "status": "success" if fix_result.final_success else "failed",
            "sr": 1 if fix_result.final_success else 0,
            "ifr_numerator": fix_result.issues_fixed,
            "ifr_denominator": len(fix_result.scan_result.issues),
            "mttr_seconds": round(fix_result.total_time, 3) if fix_result.final_success else None,
            "total_time_seconds": round(fix_result.total_time, 3),

            # Tokens (separados prompt/completion para TE preciso)
            "token_input": total_input_tokens,
            "token_output": total_output_tokens,
            "token_total": total_tokens_all,
            "token_input_available": total_input_tokens > 0,  # flag para diagnóstico

            # Validação
            "validation_layer_rejected": None,  # populated by ValidationPipeline if used
            # failure_mode: error from the last attempt (not just the first successful one)
            # This ensures 404 errors and LLM failures are captured even when all attempts fail.
            "failure_mode": (
                last_attempt.error if (last_attempt and last_attempt.error)
                else (best_attempt.error if (best_attempt and best_attempt.error) else None)
            ),

            # Agente e tentativas — use last attempt for agent_used when all failed
            "agent_used": (
                best_attempt.agent if best_attempt
                else (last_attempt.agent if last_attempt else "unknown")
            ),
            "attempt_number": len(fix_result.attempts),
            "attempts_detail": [
                {
                    "n": a.attempt_number,
                    "success": a.success,
                    "agent": a.agent,
                    "time_s": round(a.time_seconds, 2),
                    "token_total": a.tokens_used,
                    "token_prompt": a.tokens_prompt,
                    "token_completion": a.tokens_completion,
                }
                for a in fix_result.attempts
            ],

            # Patch
            "diff_lines": best_diff_lines,

            # Metadados dos issues para análise cross-dimensional
            "issue_types": issue_types,
            "complexities": complexities,
            "wcag_criteria": wcag_criteria,
            "wcag_principles": wcag_principles,
            "tool_consensus_avg": round(tool_consensus_avg, 2),
            "issues_confidence": {
                c: sum(1 for i in fix_result.scan_result.issues if i.confidence.value == c)
                for c in ("high", "medium", "low")
            },

            # Timestamps
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

    def _extract_project_ids(self, config: ExperimentConfig) -> list[str]:
        """
        Extrai os IDs de projeto (nome do diretório de snapshot) dos patterns
        definidos em config.files. Usado para importar scans pré-compilados.

        Exemplo: 'dataset/snapshots/OHIF__Viewers' → 'OHIF__Viewers'
        """
        project_ids: list[str] = []
        for pattern in config.files:
            normalized = pattern.replace("\\", "/")
            if "snapshots/" in normalized:
                # Pegar a parte após 'snapshots/'
                parts = normalized.split("snapshots/", 1)
                if len(parts) == 2:
                    proj = parts[1].split("/")[0].strip()
                    if proj:
                        project_ids.append(proj)
        return list(dict.fromkeys(project_ids))  # deduplicar mantendo ordem

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
