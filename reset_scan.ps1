# ============================================================
#  reset_scan.ps1 — reset parcial do pipeline de scan (Windows)
#  Equivalente ao reset_scan.sh para PowerShell
#
#  Mantém snapshots e reseta APENAS resultados de scan.
#
#  Uso:
#    .\reset_scan.ps1              # executa o reset
#    .\reset_scan.ps1 -DryRun      # mostra o que seria feito
#    .\reset_scan.ps1 -Yes         # sem confirmacao
#    .\reset_scan.ps1 -AndScan     # reseta e re-escaneia
#    .\reset_scan.ps1 -Workers 4   # workers para re-scan
# ============================================================

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$AndScan,
    [int]$Workers = 2,
    [int]$Timeout = 90
)

$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────
function Info    { param([string]$M); Write-Host "  --> $M" -ForegroundColor Cyan }
function Ok      { param([string]$M); Write-Host "  [OK] $M" -ForegroundColor Green }
function Warn    { param([string]$M); Write-Host "  [AVISO] $M" -ForegroundColor Yellow }
function Section { param([string]$M); Write-Host "`n== $M ==" -ForegroundColor Magenta }

# ── Caminhos ──────────────────────────────────────────────────
$ScriptDir    = $PSScriptRoot
$VenvPy       = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$Catalog      = Join-Path $ScriptDir "dataset\catalog\projects.yaml"
$ResultsDir   = Join-Path $ScriptDir "dataset\results"
$SnapshotsDir = Join-Path $ScriptDir "dataset\snapshots"
$CollectLog   = Join-Path $ScriptDir "dataset\collect.log"

# ── Verificacoes iniciais ─────────────────────────────────────
if (-not (Test-Path $VenvPy)) {
    Write-Host "ERRO: .venv nao encontrado. Execute: .\setup.ps1" -ForegroundColor Red; exit 1
}
if (-not (Test-Path $Catalog)) {
    Write-Host "ERRO: Catalogo nao encontrado: $Catalog" -ForegroundColor Red; exit 1
}

# ── Cabecalho ─────────────────────────────────────────────────
Write-Host ""
Write-Host "=" * 50 -ForegroundColor Cyan
Write-Host "  a11y-autofix -- reset_scan (parcial)" -ForegroundColor Cyan
Write-Host "=" * 50 -ForegroundColor Cyan
Write-Host ""
Write-Host "  Projeto  : $ScriptDir"
Write-Host "  Catalogo : $Catalog"
Write-Host "  Data     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($DryRun) { Write-Host "`n  MODO DRY-RUN — nada sera modificado" -ForegroundColor Yellow }
Write-Host ""

# ── Estado atual ──────────────────────────────────────────────
Section "1/5  Estado atual do catalogo"

$statusScript = @"
import sys, yaml
from collections import Counter
data = yaml.safe_load(open(r'$Catalog')) or {}
projects = data.get('projects', [])
c = Counter(p.get('status', '?') for p in projects)
total = len(projects)
to_reset = sum(c.get(s, 0) for s in ['scanned', 'annotated', 'error'])
print(f'  Total de projetos : {total}')
print(f'  snapshotted       : {c.get("snapshotted", 0)}  (serao mantidos)')
print(f'  scanned           : {c.get("scanned", 0)}  <- sera resetado -> snapshotted')
print(f'  annotated         : {c.get("annotated", 0)}  <- sera resetado -> snapshotted')
print(f'  error             : {c.get("error", 0)}  <- sera resetado -> snapshotted')
print(f'  pending/excluded  : {c.get("pending",0)+c.get("excluded",0)}  (nao afetados)')
print(f'\n  Projetos que serao re-habilitados para scan: {to_reset}')
"@
& $VenvPy -c $statusScript

# Contar arquivos de resultado
$nResultDirs  = if (Test-Path $ResultsDir) { (Get-ChildItem $ResultsDir -Directory).Count } else { 0 }
$nResultFiles = if (Test-Path $ResultsDir) { (Get-ChildItem $ResultsDir -Recurse -File).Count } else { 0 }
$nSnaps       = if (Test-Path $SnapshotsDir) { (Get-ChildItem $SnapshotsDir -Directory).Count } else { 0 }

Write-Host ""
Write-Host "  Snapshots (serao MANTIDOS) : $nSnaps repos" -ForegroundColor Green
Write-Host "  Resultados (serao APAGADOS): $nResultDirs dirs / $nResultFiles arquivos" -ForegroundColor Yellow

# ── Confirmacao ───────────────────────────────────────────────
Section "2/5  Confirmacao"

if (-not $DryRun -and -not $Yes) {
    Write-Host ""
    Write-Host "  Este script ira:" -ForegroundColor Yellow
    Write-Host "    1. Resetar status scanned/annotated/error -> snapshotted no catalogo"
    Write-Host "    2. Limpar campos scan.* de cada entrada resetada"
    Write-Host "    3. Apagar $nResultFiles arquivos em dataset\results\"
    Write-Host "    4. Apagar dataset\collect.log"
    Write-Host ""
    Write-Host "  Os $nSnaps repos em dataset\snapshots\ NAO serao tocados." -ForegroundColor Green
    Write-Host ""
    $confirm = Read-Host "  Confirmar reset? [s/N]"
    if ($confirm -notmatch "^[sySY]") {
        Write-Host "`n  Cancelado."
        exit 0
    }
}

# ── Resetar catalogo ──────────────────────────────────────────
Section "3/5  Resetando catalogo"

if ($DryRun) {
    Warn "[DRY-RUN] resetaria status scanned/annotated/error -> snapshotted"
} else {
    $resetScript = @"
import sys, yaml
from pathlib import Path
from collections import Counter

catalog_path = Path(r'$Catalog')
data = yaml.safe_load(catalog_path.read_text(encoding='utf-8')) or {}
projects = data.get('projects', [])

RESET_STATUSES = {'scanned', 'annotated', 'error'}
EMPTY_SCAN = {
    'status': 'pending',
    'findings': {
        'total_issues': 0, 'high_confidence': 0, 'files_scanned': 0,
        'files_with_issues': 0, 'scan_duration_s': 0.0,
        'by_type': {}, 'by_principle': {}, 'by_impact': {}, 'by_criterion': {},
    },
    'error_message': '',
}

reset_count = 0
for p in projects:
    if p.get('status') in RESET_STATUSES:
        old = p['status']
        p['status'] = 'snapshotted'
        p['scan'] = EMPTY_SCAN.copy()
        if 'annotation_summary' in p:
            p['annotation_summary'] = {}
        reset_count += 1
        print(f'  [{old:>10} -> snapshotted] {p["id"]}')

data['projects'] = projects
catalog_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
print(f'\n  Catalogo atualizado: {reset_count} projeto(s) resetados.')
"@
    & $VenvPy -c $resetScript
    Ok "Catalogo atualizado"
}

# ── Limpar resultados ─────────────────────────────────────────
Section "4/5  Limpando resultados de scan"

$scanFiles = @("findings.jsonl", "ground_truth.jsonl", "summary.json", "scan_results.json", "auto_acceptance_calibration.json")
$rootResultFiles = @("dataset_findings.jsonl", "dataset_validation_report.json", "dataset_profile.json", "dataset_stats.json", "quick_scan_report.json")

$nRemoved = 0

if (Test-Path $ResultsDir) {
    foreach ($projDir in (Get-ChildItem $ResultsDir -Directory)) {
        foreach ($fname in $scanFiles) {
            $fpath = Join-Path $projDir.FullName $fname
            if (Test-Path $fpath) {
                if ($DryRun) {
                    Warn "[DRY-RUN] removeria: results\$($projDir.Name)\$fname"
                } else {
                    Remove-Item $fpath -Force
                    $nRemoved++
                }
            }
        }
        if (-not $DryRun) {
            $remaining = (Get-ChildItem $projDir.FullName -File -ErrorAction SilentlyContinue).Count
            if ($remaining -eq 0) { Remove-Item $projDir.FullName -Force -Recurse -ErrorAction SilentlyContinue }
        }
    }

    foreach ($fname in $rootResultFiles) {
        $fpath = Join-Path $ResultsDir $fname
        if (Test-Path $fpath) {
            if ($DryRun) {
                Warn "[DRY-RUN] removeria: results\$fname"
            } else {
                Remove-Item $fpath -Force
                Ok "Removido: results\$fname"
                $nRemoved++
            }
        }
    }
}

if (Test-Path $CollectLog) {
    if ($DryRun) {
        Warn "[DRY-RUN] zeraria: dataset\collect.log"
    } else {
        "" | Out-File -FilePath $CollectLog -Force
        Ok "Zerado: dataset\collect.log"
    }
}

if (-not $DryRun) {
    Ok "Removidos $nRemoved arquivo(s) de resultados de scan"
    if (-not (Test-Path $ResultsDir)) { New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null }
}

# ── Estado final ──────────────────────────────────────────────
Section "5/5  Estado apos reset"

$finalScript = @"
import sys, yaml
from pathlib import Path
from collections import Counter

data = yaml.safe_load(open(r'$Catalog', encoding='utf-8')) or {}
projects = data.get('projects', [])
c = Counter(p.get('status', '?') for p in projects)

print('  Catalogo:')
for status, n in sorted(c.items()):
    print(f'    {status:<15} {n:>3}')
print(f'    {"TOTAL":<15} {len(projects):>3}')

snaps = list(Path(r'$SnapshotsDir').iterdir()) if Path(r'$SnapshotsDir').exists() else []
print(f'\n  Snapshots em disco : {len(snaps)} repos (preservados)')
snapshotted = c.get('snapshotted', 0)
print(f'  Prontos para scan  : {snapshotted} projetos')
"@
& $VenvPy -c $finalScript

# ── Resumo ────────────────────────────────────────────────────
Write-Host ""
Write-Host "=" * 50 -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "  DRY-RUN concluido — nenhum arquivo foi modificado." -ForegroundColor Yellow
    Write-Host "  Execute sem -DryRun para aplicar o reset."
} else {
    Write-Host "  Reset parcial concluido com sucesso." -ForegroundColor Green
    Write-Host "  Snapshots preservados: $nSnaps repos"
}

Write-Host ""
Write-Host "  Proximos passos:" -ForegroundColor White
Write-Host "    .\collect.ps1 -From scan -Workers $Workers"
if ($AndScan -and -not $DryRun) {
    Write-Host ""
    Write-Host "  --AndScan ativo: iniciando scan automaticamente..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "collect.ps1") -From "scan" -Workers $Workers -ScanTimeout $Timeout
}
