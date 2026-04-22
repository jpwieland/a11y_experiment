"""
CLI principal do a11y-autofix.

Interface de linha de comando completa com Typer + Rich.
Comandos: fix, experiment, models, scanners, analyze, setup, hardware.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

log = structlog.get_logger(__name__)

app = typer.Typer(
    name="a11y-autofix",
    help="♿ Sistema de auto-correção de acessibilidade com experimentação multi-modelo",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()
models_app = typer.Typer(help="Gerenciar modelos LLM")
scanners_app = typer.Typer(help="Gerenciar ferramentas de acessibilidade")
experiment_app = typer.Typer(help="Executar experimentos comparativos")
app.add_typer(models_app, name="models")
app.add_typer(scanners_app, name="scanners")
app.add_typer(experiment_app, name="experiment")


def _get_settings() -> object:
    """Carrega settings com dotenv."""
    from a11y_autofix.config import Settings
    return Settings()


def _get_registry(settings: object) -> object:
    """Cria registry de modelos."""
    from a11y_autofix.llm.registry import ModelRegistry
    from a11y_autofix.config import Settings
    if not isinstance(settings, Settings):
        raise TypeError
    return ModelRegistry(settings)


# ════════════════════════════════════════════════════════════════
# Comando: fix
# ════════════════════════════════════════════════════════════════


@app.command()
def fix(
    target: Path = typer.Argument(..., help="Arquivo, pasta ou glob"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Nome do modelo"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Backend LLM"),
    llm_url: Optional[str] = typer.Option(None, "--llm-url", help="URL do servidor LLM"),
    temperature: Optional[float] = typer.Option(None, "--temperature", "-t"),
    wcag: str = typer.Option("AA", "--wcag", "-w", help="Nível WCAG: A, AA, AAA"),
    agent: str = typer.Option("auto", "--agent", "-a", help="auto|openhands|swe-agent|direct-llm"),
    scanners_opt: Optional[str] = typer.Option(None, "--scanners", help="CSV de scanners"),
    fix_all: bool = typer.Option(False, "--fix-all", help="Corrige tudo sem confirmação"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Só escaneia, não corrige"),
    max_retries: int = typer.Option(3, "--max-retries", help="Tentativas por arquivo"),
    output: Path = typer.Option(Path("./a11y-report"), "--output", "-o"),
    create_pr: bool = typer.Option(False, "--create-pr", help="Cria PR via gh CLI"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    Executa pipeline de detecção e correção de acessibilidade.

    \b
    [dim]Exemplos:[/]
      a11y-autofix fix ./src
      a11y-autofix fix ./src --model qwen2.5-coder-14b
      a11y-autofix fix ./Button.tsx --dry-run --wcag AAA
    """
    from a11y_autofix.config import AgentType, LLMBackend, Settings, WCAGLevel
    from a11y_autofix.llm.registry import ModelRegistry
    from a11y_autofix.pipeline import Pipeline
    from a11y_autofix.utils.ui import print_banner

    print_banner()

    settings = Settings()
    settings.max_retries_per_agent = max_retries

    # Aplicar overrides de scanners
    if scanners_opt:
        scanner_list = [s.strip() for s in scanners_opt.split(",")]
        settings.use_pa11y = "pa11y" in scanner_list
        settings.use_axe = "axe-core" in scanner_list
        settings.use_lighthouse = "lighthouse" in scanner_list
        settings.use_playwright = "playwright+axe" in scanner_list

    registry = ModelRegistry(settings)

    # Resolver modelo
    model_name = model or settings.default_model
    try:
        model_config = registry.get(model_name)
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1) from e

    # Aplicar overrides
    if llm_url:
        model_config.base_url = llm_url
    if temperature is not None:
        model_config.temperature = temperature
    if backend:
        try:
            model_config.backend = LLMBackend(backend)
        except ValueError:
            console.print(f"[red]Invalid backend:[/] {backend}")
            raise typer.Exit(1)

    # Mapear WCAG
    wcag_map = {"A": "WCAG2A", "AA": "WCAG2AA", "AAA": "WCAG2AAA"}
    wcag_level = wcag_map.get(wcag.upper(), wcag.upper())

    # Criar agente preference
    agent_type = AgentType.AUTO
    try:
        agent_type = AgentType(agent)
    except ValueError:
        console.print(f"[yellow]Warning: unknown agent '{agent}', using auto[/]")

    console.print(Panel(
        Text.assemble(
            ("Target: ", "dim"), (str(target), "cyan"), "\n",
            ("Model:  ", "dim"), (model_config.model_id, "green"), "\n",
            ("WCAG:   ", "dim"), (wcag_level, "yellow"), "\n",
            ("Agent:  ", "dim"), (agent_type.value, "magenta"), "\n",
            ("Mode:   ", "dim"), ("dry-run" if dry_run else "fix", "bold"),
        ),
        title="Pipeline Configuration",
        border_style="cyan",
    ))

    pipeline = Pipeline(
        settings=settings,
        model_config=model_config,
        agent_preference=agent_type,
        dry_run=dry_run,
    )

    results = asyncio.run(pipeline.run(
        targets=[target],
        wcag_level=wcag_level,
        output_dir=output,
    ))

    # Mostrar resumo
    total_issues = sum(len(r.scan_result.issues) for r in results)
    total_fixed = sum(r.issues_fixed for r in results)

    table = Table(title="Resultado Final", show_header=True, header_style="bold cyan")
    table.add_column("Arquivo")
    table.add_column("Issues", justify="right")
    table.add_column("Corrigidos", justify="right")
    table.add_column("Status")

    for r in results:
        status = "[green]✓ OK[/]" if r.final_success else (
            "[yellow]∅ Sem issues[/]" if not r.scan_result.has_issues else "[red]✗ Falhou[/]"
        )
        table.add_row(
            r.file.name,
            str(len(r.scan_result.issues)),
            str(r.issues_fixed),
            status,
        )

    console.print(table)
    console.print(f"\n[bold]Total:[/] {total_fixed}/{total_issues} issues corrigidos")
    console.print(f"[dim]Relatórios em:[/] {output}/")

    if create_pr and total_fixed > 0:
        from a11y_autofix.utils.git import create_pr_gh, create_branch, commit_changes
        branch_name = f"a11y-fix/{model_name.split('-')[0]}"
        create_branch(branch_name)
        modified = [r.file for r in results if r.final_success]
        commit_changes(
            f"fix(a11y): correct {total_fixed} accessibility issues via {model_name}",
            modified,
        )
        pr_url = create_pr_gh(
            title=f"fix(a11y): {total_fixed} accessibility issues corrected",
            body=f"Automated accessibility fixes using {model_name}.\n\n"
                 f"WCAG Level: {wcag_level}\n"
                 f"Issues fixed: {total_fixed}/{total_issues}",
        )
        if pr_url:
            console.print(f"\n[green]PR criado:[/] {pr_url}")


# ════════════════════════════════════════════════════════════════
# Hardware preflight check
# ════════════════════════════════════════════════════════════════


def preflight_check(verbose: bool = False) -> dict:
    """
    Run hardware preflight checks and log a structured hardware_profile event.

    Checks (methodology Section 3.1.3):
      - Python version ≥ 3.10
      - RAM ≥ 16 GB (for loading ≥7B models)
      - GPU VRAM (optional — detected via nvidia-smi or rocm-smi)
      - Free disk space ≥ 20 GB

    Returns a dict with check results.
    """
    import platform

    profile: dict = {
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "ram_gb": None,
        "gpu_vram_gb": None,
        "disk_free_gb": None,
        "checks_passed": [],
        "checks_failed": [],
    }

    # Python version
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        profile["checks_passed"].append("python_version")
    else:
        profile["checks_failed"].append(f"python_version: {major}.{minor} < 3.10")

    # RAM
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        profile["ram_gb"] = round(ram_gb, 1)
        if ram_gb >= 16:
            profile["checks_passed"].append("ram")
        else:
            profile["checks_failed"].append(f"ram: {ram_gb:.1f} GB < 16 GB required")
    except ImportError:
        profile["checks_failed"].append("ram: psutil not installed (pip install psutil)")

    # GPU VRAM
    for smi_cmd in (["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],):
        if shutil.which(smi_cmd[0]):
            try:
                out = subprocess.run(smi_cmd, capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    vram_mb = sum(float(line.strip()) for line in out.stdout.strip().splitlines() if line.strip())
                    vram_gb = round(vram_mb / 1024, 1)
                    profile["gpu_vram_gb"] = vram_gb
                    if vram_gb >= 8:
                        profile["checks_passed"].append("gpu_vram")
                    else:
                        profile["checks_failed"].append(f"gpu_vram: {vram_gb} GB < 8 GB recommended")
            except Exception:
                pass

    # Disk space
    try:
        disk = shutil.disk_usage(".")
        free_gb = disk.free / (1024 ** 3)
        profile["disk_free_gb"] = round(free_gb, 1)
        if free_gb >= 20:
            profile["checks_passed"].append("disk")
        else:
            profile["checks_failed"].append(f"disk: {free_gb:.1f} GB free < 20 GB required")
    except Exception:
        profile["checks_failed"].append("disk: unable to check free space")

    log.info(
        "hardware_profile",
        python_version=profile["python_version"],
        platform=profile["platform"],
        ram_gb=profile["ram_gb"],
        gpu_vram_gb=profile["gpu_vram_gb"],
        disk_free_gb=profile["disk_free_gb"],
        checks_passed=profile["checks_passed"],
        checks_failed=profile["checks_failed"],
    )
    return profile


# ════════════════════════════════════════════════════════════════
# Comando: hardware
# ════════════════════════════════════════════════════════════════


@app.command()
def hardware() -> None:
    """
    Run hardware preflight checks for experiment execution.

    Reports Python version, RAM, GPU VRAM, and free disk space
    against the minimum requirements for running ≥7B LLMs locally.
    (Methodology Section 3.1.3)
    """
    console.print("\n[bold cyan]♿ Hardware Preflight Check[/]\n" + "═" * 50)
    profile = preflight_check(verbose=True)

    table = Table(title="Hardware Profile", header_style="bold cyan")
    table.add_column("Component")
    table.add_column("Measured")
    table.add_column("Requirement")
    table.add_column("Status")

    rows = [
        ("Python", profile["python_version"], "≥ 3.10",
         "python_version" in profile["checks_passed"]),
        ("RAM", f"{profile['ram_gb']} GB" if profile["ram_gb"] else "N/A", "≥ 16 GB",
         "ram" in profile["checks_passed"]),
        ("GPU VRAM",
         f"{profile['gpu_vram_gb']} GB" if profile["gpu_vram_gb"] else "N/A (CPU)",
         "≥ 8 GB recommended",
         profile["gpu_vram_gb"] is None or "gpu_vram" in profile["checks_passed"]),
        ("Disk (free)", f"{profile['disk_free_gb']} GB" if profile["disk_free_gb"] else "N/A",
         "≥ 20 GB", "disk" in profile["checks_passed"]),
    ]
    for component, measured, req, ok in rows:
        status = "[green]✓ OK[/]" if ok else "[red]✗ FAIL[/]"
        table.add_row(component, measured, req, status)

    console.print(table)

    if profile["checks_failed"]:
        console.print("\n[red]Failing checks:[/]")
        for msg in profile["checks_failed"]:
            console.print(f"  [red]✗[/] {msg}")
        raise typer.Exit(1)
    else:
        console.print("\n[green]✓ All hardware checks passed.[/]")


# ════════════════════════════════════════════════════════════════
# Subcomandos: experiment run / sensitivity
# ════════════════════════════════════════════════════════════════


@experiment_app.command("run")
def experiment_run(
    config: Path = typer.Argument(..., help="Arquivo YAML de configuração"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    parallel: Optional[int] = typer.Option(None, "--parallel", "-p"),
    skip_preflight: bool = typer.Option(False, "--skip-preflight",
                                        help="Skip hardware preflight check"),
) -> None:
    """
    Executa experimento comparativo entre múltiplos modelos.

    \b
    [dim]Exemplos:[/]
      a11y-autofix experiment run experiments/qwen_vs_deepseek.yaml
      a11y-autofix experiment run experiments/all_models.yaml --parallel 2
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.experiments.runner import ExperimentRunner
    from a11y_autofix.llm.registry import ModelRegistry
    from a11y_autofix.pipeline import Pipeline
    from a11y_autofix.utils.ui import print_banner

    print_banner()

    if not skip_preflight:
        profile = preflight_check()
        if profile["checks_failed"]:
            console.print("[yellow]⚠ Hardware preflight warnings:[/]")
            for msg in profile["checks_failed"]:
                console.print(f"  [yellow]•[/] {msg}")
            console.print("[dim]Pass --skip-preflight to suppress.[/]")

    if not config.exists():
        console.print(f"[red]Error:[/] Config not found: {config}")
        raise typer.Exit(1)

    settings = Settings()
    if parallel:
        settings.max_concurrent_models = parallel

    registry = ModelRegistry(settings)

    def pipeline_factory(model_config: object, **kwargs: object) -> object:
        from a11y_autofix.config import ModelConfig
        if not isinstance(model_config, ModelConfig):
            raise TypeError
        return Pipeline(settings=settings, model_config=model_config, **kwargs)  # type: ignore[arg-type]

    runner = ExperimentRunner(
        settings=settings,
        registry=registry,
        pipeline_factory=pipeline_factory,  # type: ignore[arg-type]
    )

    console.print(f"[cyan]Running experiment:[/] {config}")
    result = asyncio.run(runner.run_experiment(config, output))

    # Mostrar resultado
    table = Table(title=f"Experiment: {result.experiment_name}", header_style="bold magenta")
    table.add_column("Modelo", style="cyan")
    table.add_column("Sucesso", justify="right")
    table.add_column("Tempo Médio", justify="right")
    table.add_column("Issues Corrigidos", justify="right")

    for model_name in result.models_tested:
        table.add_row(
            model_name,
            f"{result.success_rate_by_model.get(model_name, 0):.1f}%",
            f"{result.avg_time_by_model.get(model_name, 0):.1f}s",
            str(result.issues_fixed_by_model.get(model_name, 0)),
        )

    console.print(table)

    output_dir = settings.results_dir
    console.print(f"\n[dim]Relatórios em:[/] {output_dir}/")


@experiment_app.command("sensitivity")
def experiment_sensitivity(
    config: Path = typer.Argument(..., help="Arquivo YAML de configuração base"),
    model: str = typer.Option(..., "--model", "-m", help="Model name to run sensitivity on"),
    temperatures: str = typer.Option(
        "0.0,0.2,0.4,0.6,0.8,1.0",
        "--temperatures",
        help="Comma-separated temperature values to sweep",
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """
    Run temperature sensitivity sub-study (POST-HOC EXPLORATORY).

    Sweeps a single model over multiple temperature values to quantify
    stochastic variability in SR. Results tagged as exploratory.
    (Methodology Section 3.7.3 — not a confirmatory test)

    \b
    [dim]Exemplos:[/]
      a11y-autofix experiment sensitivity experiments/base.yaml --model qwen-7b
      a11y-autofix experiment sensitivity experiments/base.yaml --model deepseek-14b \\
          --temperatures 0.0,0.3,0.6,1.0
    """
    from a11y_autofix.config import Settings
    from a11y_autofix.experiments.runner import ExperimentRunner
    from a11y_autofix.llm.registry import ModelRegistry
    from a11y_autofix.pipeline import Pipeline
    from a11y_autofix.utils.ui import print_banner

    print_banner()

    if not config.exists():
        console.print(f"[red]Error:[/] Config not found: {config}")
        raise typer.Exit(1)

    try:
        temp_values = [float(t.strip()) for t in temperatures.split(",")]
    except ValueError:
        console.print(f"[red]Error:[/] Invalid temperatures: {temperatures}")
        raise typer.Exit(1)

    settings = Settings()
    registry = ModelRegistry(settings)

    def pipeline_factory(model_config: object, **kwargs: object) -> object:
        from a11y_autofix.config import ModelConfig
        if not isinstance(model_config, ModelConfig):
            raise TypeError
        return Pipeline(settings=settings, model_config=model_config, **kwargs)  # type: ignore[arg-type]

    runner = ExperimentRunner(
        settings=settings,
        registry=registry,
        pipeline_factory=pipeline_factory,  # type: ignore[arg-type]
    )

    console.print(Panel(
        Text.assemble(
            ("Config: ", "dim"), (str(config), "cyan"), "\n",
            ("Model:  ", "dim"), (model, "green"), "\n",
            ("Temps:  ", "dim"), (", ".join(str(t) for t in temp_values), "yellow"), "\n",
            ("Type:   ", "dim"), ("POST-HOC EXPLORATORY — not confirmatory", "yellow bold"),
        ),
        title="Temperature Sensitivity Sub-study",
        border_style="yellow",
    ))

    output_dir = output or (settings.results_dir / "sensitivity")
    results = asyncio.run(runner.run_sensitivity(
        config_path=config,
        model_name=model,
        temperatures=temp_values,
        output_dir=output_dir,
    ))

    table = Table(
        title=f"Sensitivity Results — {model}",
        header_style="bold yellow",
    )
    table.add_column("Temperature", justify="right")
    table.add_column("SR (mean)", justify="right")
    table.add_column("SR (std)", justify="right")
    table.add_column("Files", justify="right")

    for temp, metrics in sorted(results.items()):
        table.add_row(
            f"{temp:.2f}",
            f"{metrics.get('sr_mean', 0):.3f}",
            f"{metrics.get('sr_std', 0):.3f}",
            str(metrics.get("files_processed", 0)),
        )

    console.print(table)
    console.print(f"\n[dim]Results in:[/] {output_dir}/")
    console.print("[yellow]⚠ These results are exploratory. Do not interpret as confirmatory evidence.[/]")


# ════════════════════════════════════════════════════════════════
# Subcomandos: models
# ════════════════════════════════════════════════════════════════


@models_app.command("list")
def list_models(
    backend: Optional[str] = typer.Option(None, "--backend", "-b"),
    family: Optional[str] = typer.Option(None, "--family", "-f"),
    size: Optional[str] = typer.Option(None, "--size", "-s"),
) -> None:
    """Lista modelos disponíveis no registry."""
    from a11y_autofix.config import LLMBackend, Settings
    from a11y_autofix.llm.registry import ModelRegistry

    settings = Settings()
    registry = ModelRegistry(settings)

    backend_filter = LLMBackend(backend) if backend else None
    models = registry.list_models(family=family, backend=backend_filter, size=size)

    if not models:
        console.print("[yellow]Nenhum modelo encontrado com esses filtros.[/]")
        console.print("Adicione modelos em models.yaml ou use: a11y-autofix models add")
        return

    table = Table(title="Modelos Disponíveis", header_style="bold cyan")
    table.add_column("Nome", style="cyan")
    table.add_column("Backend")
    table.add_column("Model ID", style="dim")
    table.add_column("Família")
    table.add_column("Tamanho")
    table.add_column("Tags")

    for name in models:
        m = registry.get(name)
        table.add_row(
            name,
            m.backend.value,
            m.model_id,
            m.family or "—",
            m.size or "—",
            ", ".join(m.tags[:3]) or "—",
        )

    console.print(table)


@models_app.command("test")
def test_model(model: str = typer.Argument(..., help="Nome do modelo")) -> None:
    """Testa se um modelo está acessível e funcionando."""
    from a11y_autofix.config import Settings
    from a11y_autofix.llm.registry import ModelRegistry

    settings = Settings()
    registry = ModelRegistry(settings)

    async def _test() -> None:
        try:
            config = registry.get(model)
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            return

        console.print(f"Testing [cyan]{model}[/] ({config.backend.value})...")
        client = registry.get_client(model)
        ok, msg = await client.health_check()

        if ok:
            console.print(f"[green]✓[/] {msg}")
            # Teste de geração
            try:
                console.print("Testing generation...")
                response = await client.complete(
                    system="You are a helpful assistant.",
                    user="Say 'Hello from a11y-autofix!' in one line.",
                    max_tokens=50,
                )
                console.print(f"[green]✓ Response:[/] {response[:100]}")
            except Exception as e:
                console.print(f"[yellow]⚠ Generation test failed:[/] {e}")
        else:
            console.print(f"[red]✗[/] {msg}")

    asyncio.run(_test())


@models_app.command("info")
def model_info(model: str = typer.Argument(...)) -> None:
    """Mostra informações detalhadas sobre um modelo."""
    from a11y_autofix.config import Settings
    from a11y_autofix.llm.registry import ModelRegistry

    settings = Settings()
    registry = ModelRegistry(settings)

    try:
        config = registry.get(model)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e

    table = Table(title=f"Model: {model}", show_header=False)
    table.add_column("Property", style="dim")
    table.add_column("Value")
    for field, value in config.model_dump().items():
        table.add_row(field, str(value))

    console.print(table)


@models_app.command("add")
def add_model(
    name: str = typer.Argument(..., help="Nome do modelo"),
    backend: str = typer.Option(..., "--backend", "-b"),
    model_id: str = typer.Option(..., "--model-id", "-i"),
    family: Optional[str] = typer.Option(None, "--family", "-f"),
    base_url: Optional[str] = typer.Option(None, "--base-url"),
) -> None:
    """Adiciona um novo modelo ao registry (salva em models.yaml)."""
    from a11y_autofix.config import LLMBackend, ModelConfig, Settings
    from a11y_autofix.llm.registry import ModelRegistry

    settings = Settings()
    registry = ModelRegistry(settings)

    try:
        lb = LLMBackend(backend)
    except ValueError:
        console.print(f"[red]Invalid backend: {backend}[/]")
        console.print(f"Valid: {', '.join(b.value for b in LLMBackend)}")
        raise typer.Exit(1)

    config = ModelConfig(
        name=name,
        backend=lb,
        model_id=model_id,
        base_url=base_url or "",
        family=family or "",
    )
    registry.register(name, config)
    registry.save_to_yaml()

    console.print(f"[green]✓[/] Model '{name}' added to models.yaml")
    console.print(f"  Test with: [cyan]a11y-autofix models test {name}[/]")


@models_app.command("discover")
def discover_models(backend: str = typer.Argument(...)) -> None:
    """Auto-descobre modelos disponíveis em um backend."""
    from a11y_autofix.config import LLMBackend, Settings
    from a11y_autofix.llm.registry import ModelRegistry

    settings = Settings()
    registry = ModelRegistry(settings)

    try:
        lb = LLMBackend(backend)
    except ValueError:
        console.print(f"[red]Invalid backend: {backend}[/]")
        raise typer.Exit(1)

    console.print(f"Discovering models in [cyan]{backend}[/]...")

    discovered = asyncio.run(registry.auto_discover(lb))

    if discovered:
        console.print(f"[green]Found {len(discovered)} models:[/]")
        for m in discovered:
            console.print(f"  [cyan]{m.name}[/] → {m.model_id}")
        registry.save_to_yaml()
        console.print("[dim]Saved to models.yaml[/]")
    else:
        console.print("[yellow]No models found. Is the backend running?[/]")


# ════════════════════════════════════════════════════════════════
# Subcomandos: scanners
# ════════════════════════════════════════════════════════════════


@scanners_app.command("list")
def list_scanners() -> None:
    """Lista ferramentas de scan disponíveis e seus status."""
    import asyncio

    async def _check() -> None:
        from a11y_autofix.scanner.axe import AxeRunner
        from a11y_autofix.scanner.lighthouse import LighthouseRunner
        from a11y_autofix.scanner.pa11y import Pa11yRunner
        from a11y_autofix.scanner.playwright_axe import PlaywrightAxeRunner

        runners = [Pa11yRunner(), AxeRunner(), LighthouseRunner(), PlaywrightAxeRunner()]

        table = Table(title="Ferramentas de Scan", header_style="bold cyan")
        table.add_column("Ferramenta")
        table.add_column("Status")
        table.add_column("Versão")

        for runner in runners:
            available = await runner.available()
            version = await runner.version() if available else "—"
            status = "[green]✓ Disponível[/]" if available else "[red]✗ Não instalado[/]"
            table.add_row(runner.tool.value, status, version)

        console.print(table)

    asyncio.run(_check())


# ════════════════════════════════════════════════════════════════
# Comando: analyze
# ════════════════════════════════════════════════════════════════


@app.command()
def analyze(
    result: Path = typer.Argument(..., help="Diretório ou JSON de resultado"),
    metric: str = typer.Option("success_rate", "--metric", "-m"),
    fmt: str = typer.Option("table", "--format", "-f", help="table|csv"),
) -> None:
    """
    Analisa resultados de experimentos.

    \b
    [dim]Exemplos:[/]
      a11y-autofix analyze ./experiment-results/my_exp_a3f9/
      a11y-autofix analyze results.json --metric avg_time
    """
    import json

    # Encontrar arquivo de resultado
    if result.is_dir():
        json_path = result / "experiment_result.json"
        if not json_path.exists():
            json_path = result / "report.json"
    else:
        json_path = result

    if not json_path.exists():
        console.print(f"[red]No result file found at {result}[/]")
        raise typer.Exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Tentar como ExperimentResult
    if "results_by_model" in data:
        from a11y_autofix.experiments.metrics import compute_experiment_metrics, rank_models
        from a11y_autofix.config import ExperimentResult
        exp = ExperimentResult(**data)
        metrics = compute_experiment_metrics(exp.results_by_model)
        ranked = rank_models(metrics, metric)

        table = Table(title=f"Experiment Analysis — metric: {metric}", header_style="bold magenta")
        table.add_column("Rank")
        table.add_column("Modelo", style="cyan")
        table.add_column(metric.replace("_", " ").title(), justify="right")

        for i, (model_name, value) in enumerate(ranked, 1):
            rank_str = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else str(i)
            table.add_row(rank_str, model_name, f"{value:.2f}")

        console.print(table)
    else:
        # Pipeline report
        summary = data.get("summary", {})
        table = Table(title="Pipeline Report Summary", header_style="bold cyan")
        table.add_column("Métrica", style="dim")
        table.add_column("Valor", justify="right")
        for key, val in summary.items():
            table.add_row(key.replace("_", " ").title(), str(val))
        console.print(table)


# ════════════════════════════════════════════════════════════════
# Comando: setup
# ════════════════════════════════════════════════════════════════


@app.command()
def setup(
    install_tools: bool = typer.Option(True, "--tools/--no-tools"),
    download_models: bool = typer.Option(False, "--models"),
) -> None:
    """
    Setup automático do sistema.

    Instala Node.js, ferramentas de acessibilidade, Playwright e configura .env.
    """
    console.print(Panel(
        "[bold cyan]♿ a11y-autofix Setup[/]\n"
        "[dim]Instalando dependências e configurando o ambiente...[/]",
        border_style="cyan",
    ))

    from a11y_autofix.utils.ui import console as ui_console
    import subprocess
    import sys

    checks = []

    # Node.js
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        checks.append(("Node.js", True, result.stdout.strip()))
    except FileNotFoundError:
        checks.append(("Node.js", False, "Not found — install from https://nodejs.org"))

    # Pa11y
    try:
        result = subprocess.run(["pa11y", "--version"], capture_output=True, text=True)
        checks.append(("pa11y", True, result.stdout.strip()))
    except FileNotFoundError:
        checks.append(("pa11y", False, "Run: npm install -g pa11y"))

    # Playwright
    try:
        import playwright
        checks.append(("playwright", True, getattr(playwright, "__version__", "installed")))
    except ImportError:
        checks.append(("playwright", False, "Run: pip install playwright && playwright install chromium"))

    # Ollama
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        checks.append(("ollama", True, result.stdout.strip()))
    except FileNotFoundError:
        checks.append(("ollama", False, "Optional — install from https://ollama.com"))

    table = Table(title="Setup Status", header_style="bold cyan")
    table.add_column("Componente")
    table.add_column("Status")
    table.add_column("Info/Action")

    for name, ok, info in checks:
        status = "[green]✓[/]" if ok else "[red]✗[/]"
        table.add_row(name, status, info)

    console.print(table)

    # Criar .env se não existir
    env_path = Path(".env")
    if not env_path.exists():
        env_example = Path(".env.example")
        if env_example.exists():
            import shutil
            shutil.copy(env_example, env_path)
            console.print("[green]✓[/] .env criado a partir de .env.example")
        else:
            env_path.write_text(
                "DEFAULT_MODEL=qwen2.5-coder-7b\n"
                "LOG_LEVEL=INFO\n"
            )
            console.print("[green]✓[/] .env criado com configurações padrão")

    console.print("\n[bold green]Setup concluído![/]")
    console.print("Próximos passos:")
    console.print("  1. [cyan]ollama pull qwen2.5-coder:7b[/]")
    console.print("  2. [cyan]a11y-autofix models list[/]")
    console.print("  3. [cyan]a11y-autofix fix ./seu-projeto/src[/]")


if __name__ == "__main__":
    app()
