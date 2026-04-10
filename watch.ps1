#Requires -Version 5.1
# ============================================================
#  watch.ps1 — Monitor ao vivo do experimento LLM (via venv)
#
#  Uso:
#    .\watch.ps1                   # busca automatica
#    .\watch.ps1 -Dir experiment-results\meu_run_1
#    .\watch.ps1 -Interval 5       # atualiza a cada 5s
#    .\watch.ps1 -Once             # imprime uma vez e sai
# ============================================================

param(
    [string]$Dir      = "",
    [int]   $Interval = 4,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8          = "1"
$env:PYTHONIOENCODING    = "utf-8"

$ProjectRoot = $PSScriptRoot
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$WatchScript = Join-Path $ProjectRoot "watch_experiment.py"

# ── Verificar venv ────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERRO] .venv nao encontrado. Execute .\setup.ps1 primeiro." -ForegroundColor Red
    exit 1
}

# ── Montar argumentos ─────────────────────────────────────────
$CmdArgs = @($WatchScript, "--interval", $Interval)

if ($Dir) {
    $DirPath = Join-Path $ProjectRoot $Dir
    $CmdArgs = @($WatchScript, $DirPath, "--interval", $Interval)
}

if ($Once) {
    $CmdArgs += "--once"
}

# ── Executar ─────────────────────────────────────────────────
& $VenvPython @CmdArgs
