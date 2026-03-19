#Requires -Version 5.1
# ============================================================
#  reset_all.ps1 -- limpa todos os artefatos do dataset (Windows)
#  Equivalente ao reset_all.sh para PowerShell
#
#  O QUE E APAGADO:
#    - dataset\snapshots\*        repos clonados
#    - dataset\results\*          JSONs/JSONLs gerados
#    - dataset\collect.log        log da rodada anterior
#    - a11y-report\*              relatorios HTML
#    - experiment-results\*       outputs de experimentos
#    - **\__pycache__ e *.pyc     bytecode Python
#
#  O QUE E PRESERVADO:
#    + dataset\catalog\projects.yaml   -> backup timestampado; depois zerado
#    + dataset\scripts\*.py            scripts do pipeline
#    + .venv\                          ambiente Python
#    + models.yaml, pyproject.toml     configuracao do projeto
#    + tests\                          suite de testes
#
#  Uso:
#    .\reset_all.ps1            executa o reset
#    .\reset_all.ps1 -DryRun    mostra o que seria apagado
# ============================================================

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"  # Evita NativeCommandError de wrappers npm

# UTF-8 no console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8           = "1"
$env:PYTHONIOENCODING     = "utf-8"
$env:NODE_NO_WARNINGS     = "1"

# ── Helpers ───────────────────────────────────────────────────
function Info    { param([string]$M); Write-Host "  --> $M" -ForegroundColor Cyan }
function Ok      { param([string]$M); Write-Host "  [OK] $M" -ForegroundColor Green }
function Warn    { param([string]$M); Write-Host "  [AVISO] $M" -ForegroundColor Yellow }
function Section { param([string]$M); Write-Host "`n== $M ==" -ForegroundColor Magenta }

function DryRemove {
    param([string]$Path, [string]$Desc)
    if (-not (Test-Path $Path)) { Info "Nao existe (ignorado): $Desc"; return }
    $size = try {
        $items = Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue
        "$($items.Count) arquivos"
    } catch { "?" }
    if ($DryRun) {
        Warn "[DRY-RUN] removeria: $Desc  ($size)"
    } else {
        Remove-Item -Path $Path -Recurse -Force -ErrorAction SilentlyContinue
        Ok "Removido: $Desc  ($size)"
    }
}

function DryTruncate {
    param([string]$File, [string]$Desc)
    if (-not (Test-Path $File)) { Info "Nao existe (ignorado): $Desc"; return }
    if ($DryRun) {
        Warn "[DRY-RUN] zeraria: $Desc"
    } else {
        "" | Out-File -FilePath $File -Force
        Ok "Zerado: $Desc"
    }
}

# ── Caminhos ──────────────────────────────────────────────────
$ScriptDir           = $PSScriptRoot
$DatasetDir          = Join-Path $ScriptDir "dataset"
$SnapshotsDir        = Join-Path $DatasetDir "snapshots"
$ResultsDir          = Join-Path $DatasetDir "results"
$Catalog             = Join-Path $DatasetDir "catalog\projects.yaml"
$CollectLog          = Join-Path $DatasetDir "collect.log"
$A11yReportDir       = Join-Path $ScriptDir "a11y-report"
$ExperimentResultDir = Join-Path $ScriptDir "experiment-results"

# ── Cabecalho ─────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host "  a11y-autofix -- reset_all" -ForegroundColor Cyan
Write-Host ("=" * 50) -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "`n  Modo DRY-RUN: nada sera apagado de verdade." -ForegroundColor Yellow
}

# ── 1. Backup do catalogo ─────────────────────────────────────
Section "1/6  Backup do catalogo de projetos"

if (Test-Path $Catalog) {
    $nProjects = (Get-Content $Catalog | Select-String "^- id:").Count
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = Join-Path $DatasetDir "catalog\projects_backup_${timestamp}.yaml"
    if ($DryRun) {
        Warn "[DRY-RUN] copiaria $Catalog -> $backupPath  ($nProjects projetos)"
    } else {
        Copy-Item $Catalog $backupPath
        Ok "Backup criado: $backupPath  ($nProjects projetos preservados)"
    }
} else {
    Info "Catalogo nao encontrado -- sera criado do zero pelo discover.py"
}

# ── 2. Reset do catalogo ──────────────────────────────────────
Section "2/6  Reset do catalogo para estado vazio"

if ($DryRun) {
    Warn "[DRY-RUN] escreveria: dataset\catalog\projects.yaml -> projects: []"
} else {
    if (-not (Test-Path (Join-Path $DatasetDir "catalog"))) {
        New-Item -ItemType Directory -Path (Join-Path $DatasetDir "catalog") -Force | Out-Null
    }
    "projects: []" | Out-File -FilePath $Catalog -Encoding UTF8
    Ok "Resetado: dataset\catalog\projects.yaml -> projects: []"
}

# ── 3. Snapshots ──────────────────────────────────────────────
Section "3/6  Snapshots de repositorios"

if (Test-Path $SnapshotsDir) {
    $snaps = Get-ChildItem $SnapshotsDir -Directory
    if ($snaps.Count -eq 0) {
        Info "Nenhum snapshot encontrado em dataset\snapshots\"
    } else {
        Info "Encontrados $($snaps.Count) snapshots em dataset\snapshots\"
        foreach ($snap in $snaps) {
            if ($DryRun) {
                Warn "[DRY-RUN] removeria snapshot: $($snap.Name)"
            } else {
                Remove-Item $snap.FullName -Recurse -Force
                Ok "Removido snapshot: $($snap.Name)"
            }
        }
    }
} else {
    Info "dataset\snapshots\ nao existe"
}

if (-not $DryRun) {
    if (-not (Test-Path $SnapshotsDir)) { New-Item -ItemType Directory -Path $SnapshotsDir -Force | Out-Null }
    Ok "dataset\snapshots\ esta vazio e pronto"
}

# ── 4. Resultados e logs ───────────────────────────────────────
Section "4/6  Resultados e logs do dataset"

if (Test-Path $ResultsDir) {
    $resultFiles = Get-ChildItem $ResultsDir -Recurse -File
    if ($resultFiles.Count -eq 0) {
        Info "dataset\results\ ja esta vazio"
    } else {
        if ($DryRun) {
            Warn "[DRY-RUN] removeria $($resultFiles.Count) arquivo(s) em dataset\results\"
        } else {
            Get-ChildItem $ResultsDir -Recurse -File | Remove-Item -Force
            Get-ChildItem $ResultsDir -Recurse -Directory | Sort-Object -Property FullName -Descending | Remove-Item -Force -ErrorAction SilentlyContinue
            Ok "Removidos $($resultFiles.Count) arquivo(s) em dataset\results\"
        }
    }
} else {
    Info "dataset\results\ nao existe -- sera criado pelos scripts"
}

DryTruncate $CollectLog "dataset\collect.log"

# ── 5. Outputs de relatorios e experimentos ───────────────────
Section "5/6  Outputs de relatorios e experimentos"

DryRemove $A11yReportDir       "a11y-report\"
DryRemove $ExperimentResultDir "experiment-results\"

# ── 6. Cache Python ────────────────────────────────────────────
Section "6/6  Cache Python"

$pycacheDirs = Get-ChildItem $ScriptDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -notlike "*\.venv\*" }
$pycFiles    = Get-ChildItem $ScriptDir -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -notlike "*\.venv\*" }

if ($DryRun) {
    Warn "[DRY-RUN] removeria $($pycacheDirs.Count) diretorios __pycache__ e $($pycFiles.Count) arquivos .pyc"
} else {
    $pycacheDirs | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    $pycFiles    | Remove-Item -Force -ErrorAction SilentlyContinue
    Ok "Removidos $($pycacheDirs.Count) __pycache__ e $($pycFiles.Count) .pyc (fora do .venv)"
}

# Recriar diretorios de output vazios
if (-not $DryRun) {
    foreach ($dir in @($SnapshotsDir, $ResultsDir, $A11yReportDir, $ExperimentResultDir)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Ok "Diretorios de output recriados vazios"
}

# ── Resumo ────────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 50) -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "  DRY-RUN concluido -- nada foi alterado." -ForegroundColor Yellow
    Write-Host "  Execute sem -DryRun para aplicar o reset."
} else {
    Write-Host "  Reset concluido com sucesso." -ForegroundColor Green
}
Write-Host ("=" * 50) -ForegroundColor Cyan
Write-Host ""
Write-Host "Proximos passos:"
Write-Host "  1. Descoberta  ->  `$env:GITHUB_TOKEN = 'ghp_...'"
Write-Host "     .venv\Scripts\python dataset\scripts\discover.py --token `$env:GITHUB_TOKEN --output dataset\catalog\projects.yaml"
Write-Host "  2. Pipeline    ->  .\collect.ps1"
Write-Host ""
Write-Host "  (Backup do catalogo anterior em dataset\catalog\ com timestamp)"
Write-Host ""
