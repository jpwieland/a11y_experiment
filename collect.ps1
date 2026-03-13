#Requires -Version 5.1
# ============================================================
#  a11y-autofix -- Coleta do Dataset (Windows)
#  Equivalente ao collect.sh para PowerShell
#
#  Uso:
#    .\collect.ps1                           # pipeline completo
#    .\collect.ps1 -Phase snapshot           # so uma fase
#    .\collect.ps1 -From scan               # a partir de uma fase
#    .\collect.ps1 -Annotator alice          # ativa anotacao manual
#    .\collect.ps1 -DryRun                   # simula sem executar
#    .\collect.ps1 -Status                   # mostra estado atual
# ============================================================

[CmdletBinding()]
param(
    [string]$Phase      = "",
    [string]$From       = "",
    [string]$Token      = "",
    [string]$Annotator  = "",
    [int]$Workers       = 2,
    [int]$ScanTimeout   = 60,
    [switch]$DryRun,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

# ── Caminhos ──────────────────────────────────────────────────
$ProjectRoot  = $PSScriptRoot
$VenvPython   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Catalog      = Join-Path $ProjectRoot "dataset\catalog\projects.yaml"
$ResultsDir   = Join-Path $ProjectRoot "dataset\results"
$LogFile      = Join-Path $ProjectRoot "dataset\collect.log"
$EnvFile      = Join-Path $ProjectRoot ".env"

# ── Helpers ───────────────────────────────────────────────────
function Info    { param([string]$M); $l = "  --> $M"; Write-Host $l -ForegroundColor Cyan;   Add-Content $LogFile $l }
function Ok      { param([string]$M); $l = "  [OK] $M"; Write-Host $l -ForegroundColor Green;  Add-Content $LogFile $l }
function Warn    { param([string]$M); $l = "  [AVISO] $M"; Write-Host $l -ForegroundColor Yellow; Add-Content $LogFile $l }
function Fail    { param([string]$M); $l = "  [ERRO] $M"; Write-Host $l -ForegroundColor Red;   Add-Content $LogFile $l }
function Section { param([string]$M); $l = "`n== $M =="; Write-Host $l -ForegroundColor Magenta; Add-Content $LogFile $l }
function Die     { param([string]$M); Write-Host "`nERRO: $M" -ForegroundColor Red; Add-Content $LogFile "ERRO: $M"; exit 1 }

# ── Verificacoes iniciais ─────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Die ".venv nao encontrado -- execute primeiro: .\setup.ps1"
}
if (-not (Test-Path $Catalog)) {
    Die "Catalogo nao encontrado: $Catalog"
}

# Carregar GITHUB_TOKEN do .env se nao definido
if ([string]::IsNullOrEmpty($Token) -and (Test-Path $EnvFile)) {
    $envLines = Get-Content $EnvFile
    foreach ($line in $envLines) {
        if ($line -match "^GITHUB_TOKEN=(.+)$") {
            $Token = $Matches[1].Trim('"').Trim("'")
            break
        }
    }
}
# Tambem verificar variavel de ambiente do sistema
if ([string]::IsNullOrEmpty($Token) -and $env:GITHUB_TOKEN) {
    $Token = $env:GITHUB_TOKEN
}
if ([string]::IsNullOrEmpty($Annotator) -and $env:ANNOTATOR_ID) {
    $Annotator = $env:ANNOTATOR_ID
}

# Criar diretorios necessarios
if (-not (Test-Path $ResultsDir)) { New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null }
$logDir = Split-Path $LogFile
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# ── Python helper: contar projetos por status ─────────────────
function Count-Status {
    $script = @"
import sys, yaml
from collections import Counter
try:
    data = yaml.safe_load(open(r'$Catalog')) or {}
    projects = data.get('projects', [])
    c = Counter(p.get('status','?') for p in projects)
    total = len(projects)
    if not total:
        print('  (nenhum projeto no catalogo)')
    else:
        for status, n in sorted(c.items()):
            bar = '#' * min(n, 30)
            print(f'    {status:<15} {n:>3}  {bar}')
        print(f'    TOTAL           {total:>3}')
except Exception as e:
    print(f'  (erro ao ler catalogo: {e})')
"@
    & $VenvPython -c $script | Out-Host
}

# ── Banner ────────────────────────────────────────────────────
"" | Out-File -FilePath $LogFile -Force
Write-Host ""
Write-Host "*** a11y-autofix --Coleta do Dataset ***" -ForegroundColor Cyan
Write-Host ("=" * 60)
Write-Host "Projeto  : $ProjectRoot"
Write-Host "Catalogo : $Catalog"
Write-Host "Log      : $LogFile"
Write-Host "Data     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($DryRun) { Write-Host "MODO SIMULACAO --nada sera executado" -ForegroundColor Yellow }
Write-Host ""

# ── --status: mostrar estado e sair ──────────────────────────
if ($Status) {
    Section "Estado Atual do Catalogo"
    Count-Status
    exit 0
}

# ── Determinar fases ─────────────────────────────────────────
$AllPhases = @("discover", "snapshot", "scan", "annotate", "validate", "profile")
$PhasesToRun = @()

if (-not [string]::IsNullOrEmpty($Phase)) {
    $PhasesToRun = @($Phase)
} elseif (-not [string]::IsNullOrEmpty($From)) {
    $found = $false
    foreach ($p in $AllPhases) {
        if ($p -eq $From) { $found = $true }
        if ($found) { $PhasesToRun += $p }
    }
    if ($PhasesToRun.Count -eq 0) {
        Die "Fase desconhecida: $From  (validas: $($AllPhases -join ', '))"
    }
} else {
    $PhasesToRun = $AllPhases
}

# Validar fases
foreach ($p in $PhasesToRun) {
    if ($p -notin $AllPhases) {
        Die "Fase desconhecida: $p  (validas: $($AllPhases -join ', '))"
    }
}

# ── Funcao: executar script Python ───────────────────────────
function Run-Phase {
    param([string]$Label, [string[]]$CmdArgs)
    if ($DryRun) {
        $line = "  DRY-RUN: $VenvPython $($CmdArgs -join ' ')"
        Write-Host $line -ForegroundColor Yellow
        Add-Content $LogFile $line
        return $true
    }
    $line = "  `$ $VenvPython $($CmdArgs -join ' ')"
    Write-Host $line -ForegroundColor DarkGray
    Add-Content $LogFile $line
    try {
        & $VenvPython @CmdArgs 2>&1 | Tee-Object -Append -FilePath $LogFile | Out-Host
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ── Funcao: verificar projetos por status ────────────────────
function Has-ProjectsAt {
    param([string]$StatusVal)
    $script = @"
import sys, yaml
data = yaml.safe_load(open(r'$Catalog')) or {}
n = sum(1 for p in data.get('projects',[]) if p.get('status') == '$StatusVal')
sys.exit(0 if n > 0 else 1)
"@
    try {
        & $VenvPython -c $script 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ── Funcoes de fase ───────────────────────────────────────────
function Run-Discover {
    Section "1/6 . Discover --busca no GitHub"
    if ([string]::IsNullOrEmpty($Token)) {
        Die "GITHUB_TOKEN nao definido.
  Defina no ambiente: `$env:GITHUB_TOKEN = 'ghp_...'
  Ou adicione ao .env: GITHUB_TOKEN=ghp_...
  Ou passe como flag: -Token ghp_..."
    }
    Info "Buscando projetos React/TypeScript com >=100 stars em 7 dominios..."
    $ok = Run-Phase "discover" @(
        "dataset\scripts\discover.py",
        "--token", $Token,
        "--output", $Catalog
    )
    if (-not $ok) { Warn "Discovery terminou com erro --verifique o log"; return $false }
    Ok "Discovery concluida"
    Count-Status
    return $true
}

function Run-Snapshot {
    Section "2/6 . Snapshot --clone e pin de commit"
    if (-not (Has-ProjectsAt "pending")) {
        Warn "Nenhum projeto com status 'pending' --fase ignorada"
        return $true
    }
    Info "Clonando repos e registrando SHA... (workers=$Workers)"
    $ok = Run-Phase "snapshot" @(
        "dataset\scripts\snapshot.py",
        "--catalog", $Catalog,
        "--workers", "$Workers"
    )
    if (-not $ok) { Warn "Snapshot terminou com erro"; return $false }
    Ok "Snapshot concluido"
    Count-Status
    return $true
}

function Run-Scan {
    Section "3/6 . Scan --pa11y + axe-core + lighthouse"
    if (-not (Has-ProjectsAt "snapshotted")) {
        Warn "Nenhum projeto com status 'snapshotted' --fase ignorada"
        return $true
    }
    Info "Escaneando acessibilidade... (workers=$Workers, timeout=${ScanTimeout}s)"
    $ok = Run-Phase "scan" @(
        "dataset\scripts\scan.py",
        "--catalog", $Catalog,
        "--workers", "$Workers",
        "--timeout", "$ScanTimeout"
    )
    if (-not $ok) { Warn "Scan terminou com erro"; return $false }
    Ok "Scan concluido"
    Count-Status
    return $true
}

function Run-Annotate {
    Section "4/6 . Annotate --ground truth"
    if (-not (Has-ProjectsAt "scanned")) {
        Warn "Nenhum projeto com status 'scanned' --fase ignorada"
        return $true
    }
    Info "Auto-aceitando achados com consenso >=2..."
    Run-Phase "annotate-auto" @(
        "dataset\scripts\annotate.py",
        "--catalog", $Catalog,
        "--auto-accept-only"
    ) | Out-Null

    if (-not [string]::IsNullOrEmpty($Annotator)) {
        Info "Anotacao manual --pass 1 (annotator: $Annotator)"
        Run-Phase "annotate-pass1" @(
            "dataset\scripts\annotate.py",
            "--catalog", $Catalog,
            "--annotator", $Annotator,
            "--pass", "1"
        ) | Out-Null
    } else {
        Warn "Anotacao manual ignorada --sem -Annotator"
    }

    Ok "Anotacao concluida"
    Count-Status
    return $true
}

function Run-Validate {
    Section "5/6 . Validate --metricas QM1-QM8"
    Info "Verificando qualidade do dataset..."
    Run-Phase "validate" @(
        "dataset\scripts\validate.py",
        "--catalog", $Catalog
    ) | Out-Null
    Ok "Validacao concluida --veja: dataset\results\dataset_validation_report.json"
    return $true
}

function Run-Profile {
    Section "6/6 . Profile --estatisticas do dataset"
    Run-Phase "profile" @(
        "dataset\scripts\describe_dataset.py",
        "--catalog", $Catalog
    ) | Out-Null
    Ok "Perfil gerado --veja: dataset\results\dataset_profile.json"
    return $true
}

# ── Estado inicial ────────────────────────────────────────────
Section "Estado do Catalogo"
Count-Status

# ── Executar fases ────────────────────────────────────────────
$FailedPhases = @()

foreach ($phase in $PhasesToRun) {
    $ok = switch ($phase) {
        "discover" { Run-Discover }
        "snapshot" { Run-Snapshot }
        "scan"     { Run-Scan }
        "annotate" { Run-Annotate }
        "validate" { Run-Validate }
        "profile"  { Run-Profile }
    }
    if (-not $ok) { $FailedPhases += $phase }
}

# ── Resumo final ──────────────────────────────────────────────
Section "Resumo"
Count-Status
Write-Host ""

if ($FailedPhases.Count -gt 0) {
    Fail "Fases com erro: $($FailedPhases -join ', ')"
    Info "Para retomar a partir da primeira fase com erro:"
    Info "  .\collect.ps1 -From $($FailedPhases[0])"
    exit 1
}

if ($DryRun) {
    Write-Host "Simulacao concluida --nenhum arquivo foi modificado." -ForegroundColor Yellow
} else {
    Ok "Pipeline concluido"
    Info "Resultados em: dataset\results\"
    Info "Log completo : $LogFile"
}
