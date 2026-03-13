#Requires -Version 5.1
# ============================================================
#  a11y-autofix -- Configuracao Completa do Ambiente (Windows)
#  Suporte: Windows 10/11 com PowerShell 5.1+ ou PowerShell 7+
#  GPU: NVIDIA CUDA (via Ollama) / AMD ROCm (via WSL2)
#
#  Uso (execute como Administrador ou configure a politica):
#    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
#    .\setup.ps1                  # setup completo
#    .\setup.ps1 -NoModels        # pula pull dos modelos Ollama
#    .\setup.ps1 -NoGpu           # forca modo CPU apenas
#    .\setup.ps1 -CI              # modo CI (sem prompts)
# ============================================================

[CmdletBinding()]
param(
    [switch]$NoModels,
    [switch]$NoGpu,
    [switch]$CI
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Forcar UTF-8 no console Windows para suportar emojis e caixas Unicode
# nos scripts Python (Rich, box-drawing chars, simbolos de acessibilidade)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8           = "1"   # Python 3.7+: forca UTF-8 em stdin/stdout/stderr
$env:PYTHONIOENCODING     = "utf-8"

# ── Caminhos ──────────────────────────────────────────────────
$ProjectRoot   = $PSScriptRoot
$VenvDir       = Join-Path $ProjectRoot ".venv"
$LogFile       = Join-Path $ProjectRoot "setup.log"
$EnvFile       = Join-Path $ProjectRoot ".env"
$EnvExample    = Join-Path $ProjectRoot ".env.example"
$OllamaEnvDir  = Join-Path $env:USERPROFILE ".ollama"
$OllamaEnvFile = Join-Path $OllamaEnvDir "ollama.env"

# ── Contadores ────────────────────────────────────────────────
$script:NPass = 0
$script:NWarn = 0
$script:NFail = 0

# ── Helpers de output ─────────────────────────────────────────
function Write-Log {
    param([string]$Msg)
    Add-Content -Path $LogFile -Value $Msg -Encoding UTF8
}

function Pass {
    param([string]$Msg)
    $script:NPass++
    $line = "  [OK] $Msg"
    Write-Host $line -ForegroundColor Green
    Write-Log $line
}

function Warn {
    param([string]$Msg)
    $script:NWarn++
    $line = "  [AVISO] $Msg"
    Write-Host $line -ForegroundColor Yellow
    Write-Log $line
}

function Fail {
    param([string]$Msg)
    $script:NFail++
    $line = "  [ERRO] $Msg"
    Write-Host $line -ForegroundColor Red
    Write-Log $line
}

function Info {
    param([string]$Msg)
    $line = "  --> $Msg"
    Write-Host $line -ForegroundColor Cyan
    Write-Log $line
}

function Section {
    param([string]$Title)
    $line = "`n=== $Title ==="
    Write-Host $line -ForegroundColor Magenta
    Write-Log $line
}

function Die {
    param([string]$Msg)
    Write-Host "`nERRO FATAL: $Msg" -ForegroundColor Red
    Write-Log "ERRO FATAL: $Msg"
    exit 1
}

function Has {
    param([string]$Cmd)
    return [bool](Get-Command $Cmd -ErrorAction SilentlyContinue)
}

function RunCmd {
    param([string]$Cmd, [string[]]$CmdArgs)
    $line = "    `$ $Cmd $($CmdArgs -join ' ')"
    Write-Host $line -ForegroundColor DarkGray
    Write-Log $line
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Cmd @CmdArgs 2>&1 | ForEach-Object {
            $s = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { $_ }
            Add-Content -Path $LogFile -Value $s -Encoding UTF8
        }
    } catch { Write-Log "  [aviso] $($_.Exception.Message)" }
    finally { $ErrorActionPreference = $eap }
}

function RunVisible {
    param([string]$Cmd, [string[]]$CmdArgs)
    $line = "    `$ $Cmd $($CmdArgs -join ' ')"
    Write-Host $line -ForegroundColor DarkGray
    Write-Log $line
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Cmd @CmdArgs 2>&1 | ForEach-Object {
            # Converter ErrorRecord (stderr) em string simples para nao gerar NativeCommandError
            $s = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { $_ }
            $s | Out-Host
            Add-Content -Path $LogFile -Value $s -Encoding UTF8
        }
    } catch { Write-Log "  [aviso] $($_.Exception.Message)" }
    finally { $ErrorActionPreference = $eap }
}

# ── Inicializar log ───────────────────────────────────────────
Set-Location $ProjectRoot
"" | Out-File -FilePath $LogFile -Force -Encoding UTF8

Write-Host ""
Write-Host "*** a11y-autofix -- Configuracao Completa do Ambiente (Windows) ***" -ForegroundColor Cyan
Write-Host ("=" * 60)
Write-Host "Projeto : $ProjectRoot"
Write-Host "Log     : $LogFile"
Write-Host "Data    : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""
Write-Log "Projeto : $ProjectRoot"
Write-Log "Data    : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ================================================================
# STEP 1 -- Python >= 3.10
# ================================================================
Section "[1/14] Verificar Python >= 3.10"

$PythonExe = $null
foreach ($candidate in @("python3.12","python3.11","python3.10","python3","python")) {
    if (Has $candidate) {
        try {
            $ver = & $candidate -c "import sys; v=sys.version_info; print(str(v.major)+'.'+str(v.minor))" 2>$null
            if ($ver) {
                $parts = $ver.Split(".")
                $maj = [int]$parts[0]
                $min = [int]$parts[1]
                if ($maj -ge 3 -and $min -ge 10) {
                    $PythonExe = $candidate
                    break
                }
            }
        } catch {}
    }
}

if (-not $PythonExe) {
    Die @"
Python 3.10+ nao encontrado.

  Instale Python 3.12 em: https://www.python.org/downloads/windows/
  OU via winget: winget install Python.Python.3.12
  OU via scoop:  scoop install python

  IMPORTANTE: marque 'Add Python to PATH' durante a instalacao!
"@
}

$PyFull = & $PythonExe -c "import sys; v=sys.version_info; print(str(v.major)+'.'+str(v.minor)+'.'+str(v.micro))"
Pass "Python $PyFull ($PythonExe)"

# ================================================================
# STEP 2 -- Virtual environment
# ================================================================
Section "[2/14] Virtual environment (.venv\)"

$VenvPython  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip     = Join-Path $VenvDir "Scripts\pip.exe"
$VenvActivate = Join-Path $VenvDir "Scripts\Activate.ps1"

if ((Test-Path $VenvDir) -and (Test-Path $VenvPython)) {
    Pass ".venv ja existe -- reutilizando"
} else {
    if (Test-Path $VenvDir) {
        Warn ".venv corrompido -- recriando..."
        Remove-Item -Recurse -Force $VenvDir
    }
    Info "Criando .venv com $PythonExe..."
    RunCmd $PythonExe @("-m","venv",$VenvDir)
    Pass ".venv criado em $VenvDir"
}

Pass "venv disponivel: $VenvPython"

# Helper de comparacao de VRAM (sem dependencia de bc)
function VramGte {
    param([float]$Threshold)
    return ($script:GpuVramGb -ge $Threshold)
}

# ================================================================
# STEP 3 -- Dependencias Python
# ================================================================
Section "[3/14] Dependencias Python (pip install -e .[dev])"

Info "Atualizando pip / setuptools / wheel..."
RunCmd $VenvPython @("-m","pip","install","--upgrade","pip","setuptools","wheel","--quiet")

Info "Instalando a11y-autofix com extras [dev]..."
RunVisible $VenvPython @("-m","pip","install","-e",".[dev]","--quiet")
Pass "a11y-autofix instalado (editable)"

# ================================================================
# STEP 4 -- Extras cientificos
# ================================================================
Section "[4/14] Extras cientificos (psutil, numpy, scipy)"

$Extras = @()
try { & $VenvPython -c "import psutil" 2>$null } catch { $Extras += "psutil" }
try { & $VenvPython -c "import numpy"  2>$null } catch { $Extras += "numpy"  }
try { & $VenvPython -c "import scipy"  2>$null } catch { $Extras += "scipy"  }

if ($Extras.Count -gt 0) {
    Info "Instalando: $($Extras -join ' ')"
    $installArgs = @("-m","pip","install") + $Extras + @("--quiet")
    RunCmd $VenvPython $installArgs
    Pass "Instalados: $($Extras -join ' ')"
} else {
    Pass "psutil, numpy, scipy ja presentes"
}

# ================================================================
# STEP 5 -- Deteccao de GPU
# ================================================================
Section "[5/14] Deteccao de GPU"

$script:GpuType    = "none"
$script:GpuVramGb  = [float]0
$GpuName           = ""
$CudaVersion       = ""

if ($NoGpu) {
    Warn "Modo CPU forcado via -NoGpu"
} else {
    if (Has "nvidia-smi") {
        try {
            $GpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1).Trim()
            $vramList = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
            $vramMb = ($vramList | ForEach-Object { [int]$_.Trim() } | Measure-Object -Sum).Sum
            $script:GpuVramGb = [math]::Round($vramMb / 1024, 1)
            $script:GpuType = "nvidia"
            $nvOut = & nvidia-smi 2>$null | Select-String "CUDA Version"
            if ($nvOut) {
                $CudaVersion = ($nvOut.Line -split "CUDA Version: ")[1].Split()[0]
            }
            Pass "NVIDIA GPU detectada: $GpuName  (VRAM: $($script:GpuVramGb) GB, CUDA: $CudaVersion)"
        } catch {
            Warn "nvidia-smi encontrado mas falhou: $_"
        }
    } elseif (Has "rocm-smi") {
        $script:GpuType = "amd"
        $GpuName = "AMD GPU (ROCm)"
        Warn "AMD ROCm detectado no Windows -- suporte EXPERIMENTAL"
        Warn "  Para melhor suporte: use WSL2 com ROCm instalado no Linux"
        Pass "AMD GPU detectada (modo experimental)"
    } else {
        $script:GpuType = "none"
        Warn "Nenhuma GPU NVIDIA detectada -- modelos rodarao em CPU (mais lento)"
        Warn "  Para NVIDIA: instale CUDA Toolkit em https://developer.nvidia.com/cuda-downloads"
        Warn "  Para AMD   : use WSL2 + ROCm (https://rocm.docs.amd.com)"
    }
}

Write-Log "  GPU_TYPE=$($script:GpuType)  VRAM=$($script:GpuVramGb)GB"

# ================================================================
# STEP 6 -- Backend GPU
# ================================================================
Section "[6/14] Backend GPU"

if ($script:GpuType -eq "nvidia") {
    $vllmOk = $false
    try { & $VenvPython -c "import vllm" 2>$null; $vllmOk = $true } catch {}

    if ($vllmOk) {
        $vllmVer = & $VenvPython -c "import vllm; print(vllm.__version__)" 2>$null
        Pass "vLLM $vllmVer ja instalado"
    } else {
        Warn "vLLM NAO suporta Windows nativamente."
        Warn "  Para modelos grandes (32B+), use WSL2 + vLLM dentro do Ubuntu."
        Info "Ollama sera usado como backend principal (CUDA nativo no Windows)"
        Pass "Backend configurado: Ollama (CUDA no Windows)"
    }
} elseif ($script:GpuType -eq "amd") {
    Warn "vLLM com ROCm requer WSL2 no Windows"
    Pass "Backend CPU/Ollama (AMD requer WSL2 para aceleracao completa)"
} else {
    Info "GPU nao disponivel -- vLLM nao sera instalado"
    Pass "Backend CPU: Ollama"
}

# ================================================================
# STEP 7 -- Node.js + ferramentas de acessibilidade
# ================================================================
Section "[7/14] Node.js + ferramentas de acessibilidade"

if (-not (Has "node")) {
    Warn "Node.js nao encontrado"
    Warn "  Instale em: https://nodejs.org/en/download/"
    Warn "  OU via winget: winget install OpenJS.NodeJS.LTS"
    Warn "  Depois reinicie o terminal e reexecute o setup"
} else {
    $nodeVer = (& node --version 2>$null).Trim()
    $npmVer  = (& npm --version  2>$null).Trim()
    Pass "Node.js $nodeVer / npm $npmVer"

    $npmPrefix = (& npm config get prefix 2>$null).Trim()
    Info "npm prefix: $npmPrefix"

    if ($env:PATH -notlike "*$npmPrefix*") {
        $env:PATH = "$npmPrefix;$env:PATH"
        Info "npm prefix adicionado ao PATH da sessao"
    }

    function Install-NpmTool {
        param([string]$Pkg, [string]$Bin)
        if (Has $Bin) {
            $ver = (& $Bin --version 2>$null | Select-Object -First 1).Trim()
            Pass "$Pkg  ($ver)"
        } else {
            Info "Instalando $Pkg..."
            try {
                RunCmd "npm" @("install","-g",$Pkg)
                Pass "$Pkg instalado"
            } catch {
                Warn "Falha ao instalar $Pkg"
                Warn "  Tente manualmente: npm install -g $Pkg"
            }
        }
    }

    Install-NpmTool "pa11y"         "pa11y"
    Install-NpmTool "@axe-core/cli" "axe"
    Install-NpmTool "lighthouse"    "lighthouse"
}

# ================================================================
# STEP 8 -- Playwright + Chromium
# ================================================================
Section "[8/14] Playwright + Chromium"

Info "Instalando browser Chromium..."
try {
    RunVisible $VenvPython @("-m","playwright","install","chromium","--with-deps")
    Pass "Playwright Chromium instalado"
} catch {
    Warn "playwright install falhou: $_"
    Warn "  Tente: $VenvPython -m playwright install chromium --with-deps"
}

# ================================================================
# STEP 9 -- Configurar .env
# ================================================================
Section "[9/14] Configurar .env"

function Get-GpuEnvBlock {
    $block = "`r`n# --- GPU Configuration (gerado pelo setup.ps1) ---`r`n"
    if ($script:GpuType -eq "nvidia") {
        $block += "# NVIDIA CUDA -- $GpuName  ($($script:GpuVramGb) GB VRAM)`r`n"
        $block += "CUDA_VISIBLE_DEVICES=0`r`n"
    } elseif ($script:GpuType -eq "amd") {
        $block += "# AMD GPU -- suporte experimental Windows`r`n"
        $block += "HIP_VISIBLE_DEVICES=0`r`n"
    } else {
        $block += "# CPU only -- GPU nao detectada`r`n"
        $block += "MAX_CONCURRENT_MODELS=1`r`n"
        $block += "MAX_CONCURRENT_SCANS=2`r`n"
    }
    return $block
}

if (Test-Path $EnvFile) {
    $envContent = Get-Content $EnvFile -Raw -Encoding UTF8
    if ($envContent -match "GPU Configuration") {
        Pass ".env ja contem configuracao de GPU -- mantendo"
    } else {
        Info "Adicionando configuracao de GPU ao .env existente..."
        Add-Content -Path $EnvFile -Value (Get-GpuEnvBlock) -Encoding UTF8
        Pass "Bloco GPU adicionado ao .env"
    }
} else {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Info ".env criado de .env.example"
    } else {
        $defaultEnv = @"
# a11y-autofix -- gerado pelo setup.ps1
DEFAULT_MODEL=qwen2.5-coder-7b
LOG_LEVEL=INFO
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false
USE_PLAYWRIGHT=true
MIN_TOOL_CONSENSUS=2
MAX_CONCURRENT_SCANS=4
MAX_CONCURRENT_AGENTS=2
MAX_CONCURRENT_MODELS=3
SCAN_TIMEOUT=60
AGENT_TIMEOUT=180
SWE_MAX_ISSUES=4
MAX_RETRIES_PER_AGENT=3
OUTPUT_DIR=./a11y-report
RESULTS_DIR=./experiment-results
"@
        $defaultEnv | Out-File -FilePath $EnvFile -Encoding UTF8
    }
    Add-Content -Path $EnvFile -Value (Get-GpuEnvBlock) -Encoding UTF8
    Pass ".env criado com configuracao de GPU ($($script:GpuType))"
}

# ================================================================
# STEP 10 -- Criar diretorios de trabalho
# ================================================================
Section "[10/14] Criar diretorios de trabalho"

$WorkDirs = @(
    "experiment-results",
    "experiment-results\checkpoints",
    "experiment-results\sensitivity",
    "a11y-report",
    "dataset\results",
    "dataset\catalog",
    "dataset\snapshots",
    "experiments"
)

foreach ($d in $WorkDirs) {
    $full = Join-Path $ProjectRoot $d
    if (-not (Test-Path $full)) {
        New-Item -ItemType Directory -Path $full -Force | Out-Null
    }
}
Pass "Diretorios criados: $($WorkDirs.Count)"

$CatalogPath = Join-Path $ProjectRoot "dataset\catalog\projects.yaml"
if (-not (Test-Path $CatalogPath)) {
    "metadata:`r`n  version: `"1.0`"`r`n  last_modified: `"`"`r`nprojects: []`r`n" |
        Out-File -FilePath $CatalogPath -Encoding UTF8
    Pass "dataset/catalog/projects.yaml criado (vazio)"
}

# ================================================================
# STEP 11 -- Configurar Ollama
# ================================================================
Section "[11/14] Configurar Ollama"

if (-not (Has "ollama")) {
    Warn "Ollama nao instalado"
    Warn "  Instale em: https://ollama.com/download/windows"
    Warn "  OU via winget: winget install Ollama.Ollama"
    Warn "  Depois reinicie o terminal e reexecute: .\setup.ps1 -NoModels"
} else {
    $ollamaVer = (& ollama --version 2>$null | Select-Object -First 1).Trim()
    Pass "Ollama: $ollamaVer"

    if (-not (Test-Path $OllamaEnvDir)) {
        New-Item -ItemType Directory -Path $OllamaEnvDir -Force | Out-Null
    }

    $ollamaContent = "# Ollama environment -- gerado pelo a11y-autofix setup.ps1`r`n`r`n"
    if ($script:GpuType -eq "nvidia") {
        $ollamaContent += "CUDA_VISIBLE_DEVICES=0`r`n"
        $ollamaContent += "OLLAMA_GPU_OVERHEAD=268435456`r`n"
        $ollamaContent += "OLLAMA_MAX_LOADED_MODELS=1`r`n"
        Pass "Ollama configurado para NVIDIA CUDA ($GpuName, $($script:GpuVramGb) GB)"
    } elseif ($script:GpuType -eq "amd") {
        $ollamaContent += "HIP_VISIBLE_DEVICES=0`r`n"
        $ollamaContent += "OLLAMA_MAX_LOADED_MODELS=1`r`n"
        Warn "Ollama configurado para AMD (experimental no Windows)"
    } else {
        $ollamaContent += "OLLAMA_NUM_GPU=0`r`n"
        $ollamaContent += "OLLAMA_NUM_PARALLEL=1`r`n"
        $ollamaContent += "OLLAMA_MAX_LOADED_MODELS=1`r`n"
        Warn "Ollama configurado para CPU -- inferencia sera lenta"
    }

    $ollamaContent | Out-File -FilePath $OllamaEnvFile -Encoding UTF8
    Info "Config Ollama salva em: $OllamaEnvFile"

    Write-Host ""
    Write-Host "  IMPORTANTE -- Para aplicar GPU no Ollama (execute como Administrador):" -ForegroundColor Yellow
    if ($script:GpuType -eq "nvidia") {
        Write-Host "    [Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES','0','Machine')" -ForegroundColor Cyan
        Write-Host "    [Environment]::SetEnvironmentVariable('OLLAMA_GPU_OVERHEAD','268435456','Machine')" -ForegroundColor Cyan
    }
    Write-Host "    Restart-Service -Name 'OllamaService' -ErrorAction SilentlyContinue" -ForegroundColor Cyan
    Write-Host ""

    $ollamaRunning = $false
    try {
        Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 2 -UseBasicParsing | Out-Null
        $ollamaRunning = $true
        Info "Ollama daemon esta rodando"
    } catch {
        Info "Ollama daemon nao esta rodando"
        Info "  Inicie com: ollama serve"
    }
}

# ================================================================
# STEP 12 -- Baixar modelos
# ================================================================
Section "[12/14] Baixar modelos Ollama"

if (-not (Has "ollama")) {
    Warn "Ollama nao disponivel -- pulando pull de modelos"
} else {
    $ollamaUp = $false
    try {
        Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing | Out-Null
        $ollamaUp = $true
    } catch {}

    if (-not $ollamaUp) {
        Warn "Ollama daemon nao esta rodando -- pulando pull"
        Warn "  Execute depois: ollama serve   e depois: ollama pull qwen2.5-coder:7b"
    } elseif ($NoModels) {
        Info "Pull desativado via -NoModels"
    } else {
        $ModelsToPull = @()
        if ($script:GpuType -eq "none" -or $NoGpu) {
            $ModelsToPull = @("qwen2.5-coder:7b")
            Info "CPU only -> baixando apenas modelo 7B"
        } elseif (VramGte 20) {
            $ModelsToPull = @("qwen2.5-coder:7b","qwen2.5-coder:14b","deepseek-coder-v2:16b")
            Info "VRAM >= 20 GB -> baixando todos os modelos recomendados"
        } elseif (VramGte 12) {
            $ModelsToPull = @("qwen2.5-coder:7b","qwen2.5-coder:14b")
            Info "VRAM $($script:GpuVramGb) GB -> baixando modelos ate 14B"
        } elseif (VramGte 6) {
            $ModelsToPull = @("qwen2.5-coder:7b")
            Info "VRAM $($script:GpuVramGb) GB -> baixando apenas modelo 7B"
        } else {
            $ModelsToPull = @("qwen2.5-coder:7b")
        }

        $available = (& ollama list 2>$null | Select-Object -Skip 1 |
                      ForEach-Object { ($_ -split "\s+")[0] })

        foreach ($model in $ModelsToPull) {
            if ($available -contains $model) {
                Pass "Ja disponivel: $model"
            } else {
                Info "Baixando $model (pode demorar)..."
                try {
                    RunVisible "ollama" @("pull",$model)
                    Pass "$model baixado"
                } catch {
                    Warn "Falha ao baixar $model -> tente: ollama pull $model"
                }
            }
        }
    }
}

# ================================================================
# STEP 13 -- Hardware preflight check
# ================================================================
Section "[13/14] Hardware preflight check"

$a11yBin = Join-Path $VenvDir "Scripts\a11y-autofix.exe"
if (-not (Test-Path $a11yBin)) {
    $a11yBin = Join-Path $VenvDir "Scripts\a11y-autofix"
}

if (Test-Path $a11yBin) {
    Info "Executando a11y-autofix hardware..."
    try {
        RunVisible $a11yBin @("hardware")
        Pass "Hardware preflight OK"
    } catch {
        Warn "Alguns checks falharam -- veja: a11y-autofix hardware"
    }
} else {
    Warn "CLI nao encontrado -- ative o venv e execute: a11y-autofix hardware"
    Info "  Ativar venv: $VenvActivate"
}

# ================================================================
# STEP 14 -- Resumo final
# ================================================================
Section "[14/14] Resumo"

Write-Host ""
Write-Host ("=" * 60)
Write-Host "Resumo do Setup" -ForegroundColor White
Write-Host ("=" * 60)
Write-Host "  Passou  : $($script:NPass)" -ForegroundColor Green
if ($script:NWarn -gt 0) { Write-Host "  Avisos  : $($script:NWarn)" -ForegroundColor Yellow }
if ($script:NFail -gt 0) { Write-Host "  Falhas  : $($script:NFail)" -ForegroundColor Red   }

Write-Host ""
Write-Host "SO/GPU:" -ForegroundColor White
Write-Host "  Sistema : Windows"
switch ($script:GpuType) {
    "nvidia" { Write-Host "  GPU     : NVIDIA $GpuName  ($($script:GpuVramGb) GB VRAM, CUDA $CudaVersion)" -ForegroundColor Green }
    "amd"    { Write-Host "  GPU     : AMD $GpuName (experimental)" -ForegroundColor Yellow }
    "none"   { Write-Host "  GPU     : CPU only -- inferencia mais lenta" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Para ativar o ambiente em novos terminais:" -ForegroundColor White
Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "Proximos passos:" -ForegroundColor White
Write-Host "  a11y-autofix hardware"
Write-Host "  a11y-autofix models list"
Write-Host "  a11y-autofix fix .\src --dry-run"
Write-Host "  a11y-autofix experiment run .\experiments\qwen_vs_deepseek.yaml"
Write-Host ""
Write-Host "Log completo: $LogFile" -ForegroundColor DarkGray
Write-Host ""

if ($script:NFail -gt 0) {
    Write-Host "Setup concluido com $($script:NFail) falha(s). Verifique as mensagens acima." -ForegroundColor Red
    exit 1
} elseif ($script:NWarn -gt 0) {
    Write-Host "Setup concluido com avisos. Experimento pode rodar com funcionalidade reduzida." -ForegroundColor Yellow
} else {
    Write-Host "Ambiente configurado com sucesso!" -ForegroundColor Green
}
