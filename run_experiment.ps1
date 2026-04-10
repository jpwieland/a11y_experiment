#Requires -Version 5.1
# ============================================================
#  run_experiment.ps1 — Executa o experimento LLM via venv
#
#  Uso:
#    .\run_experiment.ps1
#    .\run_experiment.ps1 -Config experiments/experiment_weak_gpu.yaml
#    .\run_experiment.ps1 -Output experiment-results/meu_run_1
#    .\run_experiment.ps1 -SkipPreflight
#
#  Pre-requisito: .\setup.ps1 ja executado (cria o .venv)
# ============================================================

param(
    [string]$Config        = "experiments\experiment_weak_gpu.yaml",
    [string]$Output        = "",
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8          = "1"
$env:PYTHONIOENCODING    = "utf-8"

$ProjectRoot = $PSScriptRoot
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# ── Verificar venv ────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Host ""
    Write-Host "  [ERRO] Ambiente virtual nao encontrado em .venv\" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Execute primeiro:" -ForegroundColor Yellow
    Write-Host "    .\setup.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# ── Verificar config ─────────────────────────────────────────
$ConfigPath = Join-Path $ProjectRoot $Config
if (-not (Test-Path $ConfigPath)) {
    Write-Host ""
    Write-Host "  [ERRO] Config nao encontrado: $ConfigPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Configs disponiveis:" -ForegroundColor Yellow
    Get-ChildItem (Join-Path $ProjectRoot "experiments") -Filter "*.yaml" |
        ForEach-Object { Write-Host "    experiments\$($_.Name)" -ForegroundColor Cyan }
    Write-Host ""
    exit 1
}

# ── Verificar Ollama ─────────────────────────────────────────
Write-Host ""
Write-Host "  Verificando Ollama..." -ForegroundColor DarkGray
try {
    Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing | Out-Null
    Write-Host "  [OK] Ollama esta rodando" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "  [AVISO] Ollama nao detectado em localhost:11434" -ForegroundColor Yellow
    Write-Host "  Inicie o Ollama antes de continuar:" -ForegroundColor Yellow
    Write-Host "    ollama serve" -ForegroundColor Cyan
    Write-Host ""
    $resp = Read-Host "  Continuar mesmo assim? (s/N)"
    if ($resp -notmatch '^[sS]$') { exit 1 }
}

# ── Header ───────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  a11y-autofix -- Experimento LLM" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Config : $Config" -ForegroundColor White
Write-Host "  Python : $VenvPython" -ForegroundColor DarkGray
Write-Host "  Inicio : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Para monitorar em outro terminal:" -ForegroundColor Yellow
Write-Host "    .\watch.ps1" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""

# ── Montar argumentos ────────────────────────────────────────
$CmdArgs = @("-m", "a11y_autofix.cli", "experiment", "run", $ConfigPath)

if ($Output) {
    $OutputPath = Join-Path $ProjectRoot $Output
    $CmdArgs += @("--output", $OutputPath)
}

if ($SkipPreflight) {
    $CmdArgs += "--skip-preflight"
}

# ── Executar ─────────────────────────────────────────────────
Write-Host "  Iniciando..." -ForegroundColor Green
Write-Host ""

try {
    & $VenvPython @CmdArgs
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host ""
    Write-Host "  [ERRO] Falha ao executar: $_" -ForegroundColor Red
    exit 1
}

# ── Resultado ────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
if ($exitCode -eq 0) {
    Write-Host "  Experimento concluido com sucesso!" -ForegroundColor Green
    Write-Host "  Resultados em: experiment-results\" -ForegroundColor White
    Write-Host "  Analisar: .\run_experiment.ps1 analyze" -ForegroundColor DarkGray
} else {
    Write-Host "  Experimento encerrado com codigo $exitCode" -ForegroundColor Yellow
    Write-Host "  Verifique os logs acima." -ForegroundColor Yellow
    Write-Host "  Para retomar, execute o mesmo comando novamente." -ForegroundColor DarkGray
}
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""

exit $exitCode
