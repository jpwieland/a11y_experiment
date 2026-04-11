#Requires -Version 5.1
# ============================================================
#  run_experiment.ps1 — Executa o experimento LLM via venv
#
#  Uso:
#    .\run_experiment.ps1
#    .\run_experiment.ps1 -Config experiments\experiment_weak_gpu.yaml
#    .\run_experiment.ps1 -Output experiment-results\meu_run_1
#    .\run_experiment.ps1 -Jobs 4           # forçar N agentes paralelos
#    .\run_experiment.ps1 -ScanWorkers 8    # forçar N scans paralelos
#    .\run_experiment.ps1 -SkipPreflight
#
#  Otimizações automáticas:
#    • Detecta VRAM livre e CPU para definir paralelismo ideal
#    • Scan de projetos é cacheado em scan_cache.json — não repete entre modelos
#    • Checkpoints por arquivo — retoma de onde parou sem perder trabalho
#
#  Pré-requisito: .\setup.ps1 já executado (cria o .venv)
# ============================================================

param(
    [string]$Config        = "experiments\experiment_weak_gpu.yaml",
    [string]$Output        = "",
    [int]   $Jobs          = 0,          # 0 = auto-detect pelo VRAM
    [int]   $ScanWorkers   = 0,          # 0 = auto-detect pelo CPU
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

# ════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE HARDWARE — define paralelismo automático
# ════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "  Detectando hardware..." -ForegroundColor DarkGray

# ── CPU ──────────────────────────────────────────────────────
$CpuLogical = 0
try {
    $CpuLogical = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).NumberOfLogicalProcessors
} catch {
    try { $CpuLogical = [int]$env:NUMBER_OF_PROCESSORS } catch { $CpuLogical = 4 }
}

# ── GPU / VRAM ───────────────────────────────────────────────
$VramFreeMb  = 0
$VramTotalMb = 0
$GpuName     = "desconhecida"
$HasGpu      = $false

try {
    $freeLine  = nvidia-smi --query-gpu=memory.free  --format=csv,noheader,nounits 2>$null
    $totalLine = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
    $nameLines = nvidia-smi --query-gpu=name         --format=csv,noheader         2>$null

    if ($freeLine) {
        $VramFreeMb  = ($freeLine  | ForEach-Object { [int]$_.Trim() } | Measure-Object -Sum).Sum
        $VramTotalMb = ($totalLine | ForEach-Object { [int]$_.Trim() } | Measure-Object -Sum).Sum
        $GpuName     = ($nameLines | Select-Object -First 1).Trim()
        $HasGpu      = $true
    }
} catch { }

$VramFreeGb  = [math]::Round($VramFreeMb  / 1024, 1)
$VramTotalGb = [math]::Round($VramTotalMb / 1024, 1)

# ── Paralelismo automático ────────────────────────────────────
# Jobs (agentes LLM simultâneos): limitado pela VRAM livre após o modelo carregar
# ScanWorkers: scan é CPU-bound (Playwright, ESLint) — aproveitar todos os cores

if ($Jobs -eq 0) {
    if (-not $HasGpu)         { $Jobs = 1 }
    elseif ($VramFreeGb -ge 20) { $Jobs = 4 }
    elseif ($VramFreeGb -ge 12) { $Jobs = 3 }
    elseif ($VramFreeGb -ge 6)  { $Jobs = 2 }
    else                        { $Jobs = 1 }
}

if ($ScanWorkers -eq 0) {
    # Playwright cria um processo por scan — limitar a ~metade dos cores
    # para não saturar memória (cada instância usa ~150 MB)
    $ScanWorkers = [math]::Max(2, [math]::Min([int]($CpuLogical / 2), 8))
}

# Exportar como variáveis de ambiente para sobrescrever Settings
$env:MAX_CONCURRENT_AGENTS = $Jobs
$env:MAX_CONCURRENT_SCANS  = $ScanWorkers
$env:MAX_CONCURRENT_MODELS = "1"   # 1 modelo de cada vez na GPU (cold-start)

# ════════════════════════════════════════════════════════════════════════
# STATUS DOS CHECKPOINTS — mostra quanto já foi feito
# ════════════════════════════════════════════════════════════════════════

# Determinar o diretório de output para verificar checkpoints existentes
$EffectiveOutput = if ($Output) {
    Join-Path $ProjectRoot $Output
} else {
    # Tentar ler output_dir do YAML via grep simples
    $YamlContent = Get-Content $ConfigPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    $OutputMatch = [regex]::Match($YamlContent, 'output_dir\s*:\s*(.+)')
    if ($OutputMatch.Success) {
        Join-Path $ProjectRoot $OutputMatch.Groups[1].Value.Trim()
    } else {
        Join-Path $ProjectRoot "experiment-results"
    }
}

$CheckpointDir = Join-Path $EffectiveOutput "checkpoints"
$ScanCachePath = Join-Path $EffectiveOutput "scan_cache.json"

$DoneFiles   = 0
$TotalCached = 0
$IsResume    = $false

if (Test-Path $CheckpointDir) {
    $DoneFiles = (Get-ChildItem -Recurse $CheckpointDir -Filter "*.json" `
                    -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($DoneFiles -gt 0) { $IsResume = $true }
}

if (Test-Path $ScanCachePath) {
    try {
        $CacheJson   = Get-Content $ScanCachePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $TotalCached = ($CacheJson.scans | Get-Member -MemberType NoteProperty | Measure-Object).Count
    } catch { }
}

# ════════════════════════════════════════════════════════════════════════
# VERIFICAR OLLAMA
# ════════════════════════════════════════════════════════════════════════

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

# ════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Cyan
Write-Host "  a11y-autofix -- Experimento LLM" -ForegroundColor Cyan
Write-Host ("=" * 64) -ForegroundColor Cyan
Write-Host "  Config     : $Config" -ForegroundColor White
Write-Host "  Saída      : $EffectiveOutput" -ForegroundColor DarkGray
Write-Host "  Python     : $VenvPython" -ForegroundColor DarkGray
Write-Host "  Início     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# ── Hardware ─────────────────────────────────────────────────
Write-Host "  Hardware:" -ForegroundColor White
Write-Host "    CPU  : $CpuLogical cores lógicos" -ForegroundColor DarkGray
if ($HasGpu) {
    Write-Host ("    GPU  : $GpuName  (VRAM livre: ${VramFreeGb} GB / ${VramTotalGb} GB)") `
        -ForegroundColor DarkGray
} else {
    Write-Host "    GPU  : nao detectada (modo CPU)" -ForegroundColor Yellow
}

# ── Paralelismo ──────────────────────────────────────────────
Write-Host ""
Write-Host "  Paralelismo auto-detectado:" -ForegroundColor White
Write-Host "    Agentes LLM paralelos : $Jobs" -ForegroundColor $(if ($Jobs -gt 1) { "Green" } else { "Yellow" })
Write-Host "    Scans paralelos       : $ScanWorkers" -ForegroundColor Green

# ── Status checkpoints ────────────────────────────────────────
Write-Host ""
if ($IsResume) {
    Write-Host "  [RETOMADA] Checkpoints encontrados:" -ForegroundColor Yellow
    Write-Host "    Arquivos ja processados : $DoneFiles" -ForegroundColor Green
    if ($TotalCached -gt 0) {
        Write-Host "    Scan cache              : $TotalCached arquivos" -ForegroundColor Green
        Write-Host "    Scan sera pulado para arquivos em cache" -ForegroundColor DarkGray
    }
    Write-Host "    O runner retomara automaticamente do ponto de interrupcao" -ForegroundColor DarkGray
} else {
    Write-Host "  Execucao nova (sem checkpoints existentes)" -ForegroundColor DarkGray
    if ($TotalCached -gt 0) {
        Write-Host "  Scan cache encontrado: $TotalCached arquivos" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "  Para monitorar em outro terminal:" -ForegroundColor Yellow
Write-Host "    .\watch.ps1" -ForegroundColor Cyan
Write-Host ("=" * 64) -ForegroundColor Cyan
Write-Host ""

# ════════════════════════════════════════════════════════════════════════
# MONTAR E EXECUTAR COMANDO
# ════════════════════════════════════════════════════════════════════════

$CmdArgs = @("-m", "a11y_autofix.cli", "experiment", "run", $ConfigPath,
             "--parallel", $Jobs)

if ($Output) {
    $OutputPath = Join-Path $ProjectRoot $Output
    $CmdArgs += @("--output", $OutputPath)
}

if ($SkipPreflight) {
    $CmdArgs += "--skip-preflight"
}

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

# ════════════════════════════════════════════════════════════════════════
# RESULTADO
# ════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Cyan

if ($exitCode -eq 0) {
    Write-Host "  Experimento concluido com sucesso!" -ForegroundColor Green
    Write-Host "  Resultados em: $EffectiveOutput" -ForegroundColor White

    # Mostrar quantos arquivos novos foram processados
    if (Test-Path $CheckpointDir) {
        $FinalFiles = (Get-ChildItem -Recurse $CheckpointDir -Filter "*.json" `
                         -ErrorAction SilentlyContinue | Measure-Object).Count
        $NewFiles   = $FinalFiles - $DoneFiles
        if ($NewFiles -gt 0) {
            Write-Host "  Arquivos processados nesta execucao: $NewFiles" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "  Experimento encerrado com codigo $exitCode" -ForegroundColor Yellow
    Write-Host "  Verifique os logs acima." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Para retomar (usa checkpoints automaticamente):" -ForegroundColor DarkGray
    $resumeCmd = ".\run_experiment.ps1 -Config $Config"
    if ($Output) { $resumeCmd += " -Output $Output" }
    Write-Host "    $resumeCmd" -ForegroundColor Cyan
}

Write-Host ("=" * 64) -ForegroundColor Cyan
Write-Host ""

exit $exitCode
