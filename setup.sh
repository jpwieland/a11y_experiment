#!/usr/bin/env bash
# ============================================================
#  a11y-autofix — Configuração Completa do Ambiente
#  Suporte: macOS (Intel / Apple Silicon) e Linux (Debian,
#           Ubuntu, Fedora/RHEL, Arch, openSUSE)
#  GPU: NVIDIA CUDA / AMD ROCm / Apple Silicon Metal
#
#  Uso:
#    bash setup.sh                # setup completo
#    bash setup.sh --no-models    # pula pull dos modelos Ollama
#    bash setup.sh --ci           # modo CI (sem prompts)
#    bash setup.sh --no-gpu       # força modo CPU apenas
#
#  Passos executados:
#   1.  Verificar Python ≥ 3.10
#   2.  Criar virtual environment (.venv/)
#   3.  Instalar dependências Python (pip install -e .[dev])
#   4.  Instalar extras científicos (psutil, scipy, numpy)
#   5.  Detectar GPU (NVIDIA / AMD / Apple Silicon)
#   6.  Instalar backends GPU (vLLM para CUDA/ROCm)
#   7.  Instalar Node.js + ferramentas de acessibilidade
#   8.  Instalar Playwright + Chromium
#   9.  Configurar .env (com variáveis GPU)
#  10.  Criar diretórios de trabalho
#  11.  Configurar Ollama (flags de GPU)
#  12.  Baixar modelos recomendados
#  13.  Preflight check de hardware
#  14.  Resumo final
# ============================================================
set -euo pipefail
IFS=$'\n\t'

# ── Flags ────────────────────────────────────────────────────
PULL_MODELS=true
CI_MODE=false
FORCE_CPU=false
for arg in "$@"; do
    case "$arg" in
        --no-models) PULL_MODELS=false ;;
        --ci)        CI_MODE=true ;;
        --no-gpu)    FORCE_CPU=true ;;
    esac
done

# ── Detecção de SO ───────────────────────────────────────────
OS_TYPE="$(uname -s)"   # Darwin | Linux

# Detecção do gerenciador de pacotes do Linux
LINUX_PM=""
if [ "$OS_TYPE" = "Linux" ]; then
    if   command -v apt-get &>/dev/null; then LINUX_PM="apt"
    elif command -v dnf     &>/dev/null; then LINUX_PM="dnf"
    elif command -v yum     &>/dev/null; then LINUX_PM="yum"
    elif command -v pacman  &>/dev/null; then LINUX_PM="pacman"
    elif command -v zypper  &>/dev/null; then LINUX_PM="zypper"
    fi
fi

# Instrução de instalação adaptada ao gerenciador de pacotes
linux_install_hint() {
    # $1 = pacote deb  $2 = pacote rpm  $3 = pacote arch
    local deb="${1:-}" rpm="${2:-}" arch_pkg="${3:-}"
    case "$LINUX_PM" in
        apt)    echo "sudo apt-get install -y $deb" ;;
        dnf|yum) echo "sudo $LINUX_PM install -y $rpm" ;;
        pacman) echo "sudo pacman -S --noconfirm $arch_pkg" ;;
        zypper) echo "sudo zypper install -y $deb" ;;
        *)      echo "instale $deb (ou equivalente para sua distro)" ;;
    esac
}

# ── Caminhos ─────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
LOG_FILE="$PROJECT_ROOT/setup.log"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
OLLAMA_ENV_FILE="$HOME/.ollama/ollama.env"

# ── Cores ────────────────────────────────────────────────────
if [ -t 1 ] && [ "$CI_MODE" = "false" ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

# ── Contadores ───────────────────────────────────────────────
N_PASS=0; N_WARN=0; N_FAIL=0

# ── Helpers ──────────────────────────────────────────────────
pass()    { echo -e "${GREEN}  ✓${NC} $*" | tee -a "$LOG_FILE"; N_PASS=$((N_PASS+1)); }
warn()    { echo -e "${YELLOW}  ⚠${NC} $*" | tee -a "$LOG_FILE"; N_WARN=$((N_WARN+1)); }
fail()    { echo -e "${RED}  ✗${NC} $*" | tee -a "$LOG_FILE"; N_FAIL=$((N_FAIL+1)); }
info()    { echo -e "${BLUE}  →${NC} $*" | tee -a "$LOG_FILE"; }
section() { echo -e "\n${BOLD}${CYAN}── [$1] $2 ──${NC}" | tee -a "$LOG_FILE"; }
has()     { command -v "$1" &>/dev/null; }
die()     { echo -e "\n${RED}${BOLD}ERRO FATAL:${NC} $*" | tee -a "$LOG_FILE"; exit 1; }

run() {
    echo -e "    ${BLUE}\$${NC} $*" | tee -a "$LOG_FILE"
    "$@" >> "$LOG_FILE" 2>&1
}
run_visible() {
    echo -e "    ${BLUE}\$${NC} $*" | tee -a "$LOG_FILE"
    "$@" 2>&1 | tee -a "$LOG_FILE"
}

# ── Inicializar log ───────────────────────────────────────────
cd "$PROJECT_ROOT"
: > "$LOG_FILE"

echo -e "\n${BOLD}${CYAN}♿ a11y-autofix — Configuração Completa do Ambiente${NC}" | tee -a "$LOG_FILE"
echo "══════════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo "Projeto : $PROJECT_ROOT"                              | tee -a "$LOG_FILE"
echo "SO      : $OS_TYPE${LINUX_PM:+ ($LINUX_PM)}"         | tee -a "$LOG_FILE"
echo "Log     : $LOG_FILE"                                  | tee -a "$LOG_FILE"
echo "Data    : $(date '+%Y-%m-%d %H:%M:%S')"              | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════════
# STEP 1 — Python ≥ 3.10
# ═══════════════════════════════════════════════════════════
section "1/14" "Verificar Python ≥ 3.10"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if has "$candidate"; then
        _ver=$("$candidate" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null || true)
        _maj=$(echo "$_ver" | cut -d. -f1)
        _min=$(echo "$_ver" | cut -d. -f2)
        if [ "${_maj:-0}" -ge 3 ] && [ "${_min:-0}" -ge 10 ]; then
            PYTHON="$candidate"; break
        fi
    fi
done

[ -z "$PYTHON" ] && die "Python 3.10+ não encontrado.

  macOS          : brew install python@3.12
  Ubuntu/Debian  : sudo apt-get install -y python3.12 python3.12-venv
                   (ou: sudo add-apt-repository ppa:deadsnakes/ppa)
  Fedora/RHEL    : sudo dnf install -y python3.12
  Arch           : sudo pacman -S python
  openSUSE       : sudo zypper install python312
  Conda          : conda create -n a11y python=3.12 && conda activate a11y"

PY_FULL=$("$PYTHON" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}')")
pass "Python $PY_FULL  ($PYTHON)"

# ═══════════════════════════════════════════════════════════
# STEP 2 — Virtual environment
# ═══════════════════════════════════════════════════════════
section "2/14" "Virtual environment (.venv/)"

if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ] && [ -x "$VENV_DIR/bin/python" ]; then
    pass ".venv já existe — reutilizando"
else
    if [ -d "$VENV_DIR" ]; then
        warn ".venv corrompido (bin/python ausente) — recriando..."
        rm -rf "$VENV_DIR"
    fi
    info "Criando .venv com $PYTHON..."
    run "$PYTHON" -m venv "$VENV_DIR"
    pass ".venv criado em $VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
PYTHON_V="$VENV_DIR/bin/python"
PIP_V="$VENV_DIR/bin/pip"
pass "venv ativado — $($PYTHON_V -c 'import sys; print(sys.executable)')"

# ── Helper: comparação de VRAM sem depender do comando bc ──
# `bc` pode não estar instalado em sistemas Linux mínimos.
# Usa Python (garantido disponível após o passo 2).
# Uso: _vram_gte <threshold>  → retorna 0 se GPU_VRAM_GB >= threshold
_vram_gte() {
    "$PYTHON_V" -c \
        "import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)" \
        "${GPU_VRAM_GB:-0}" "$1" 2>/dev/null || return 1
}

# ═══════════════════════════════════════════════════════════
# STEP 3 — Dependências Python
# ═══════════════════════════════════════════════════════════
section "3/14" "Dependências Python (pip install -e .[dev])"

info "Atualizando pip / setuptools / wheel..."
run "$PIP_V" install --upgrade pip setuptools wheel --quiet

info "Instalando a11y-autofix com extras [dev]..."
run_visible "$PIP_V" install -e ".[dev]" --quiet
pass "a11y-autofix instalado (editable)"

# ═══════════════════════════════════════════════════════════
# STEP 4 — Extras científicos
# ═══════════════════════════════════════════════════════════
section "4/14" "Extras científicos (psutil, numpy, scipy)"

EXTRAS=()
"$PYTHON_V" -c "import psutil"  2>/dev/null || EXTRAS+=(psutil)
"$PYTHON_V" -c "import numpy"   2>/dev/null || EXTRAS+=(numpy)
"$PYTHON_V" -c "import scipy"   2>/dev/null || EXTRAS+=(scipy)

if [ ${#EXTRAS[@]} -gt 0 ]; then
    info "Instalando: ${EXTRAS[*]}"
    run "$PIP_V" install "${EXTRAS[@]}" --quiet
    pass "Instalados: ${EXTRAS[*]}"
else
    pass "psutil, numpy, scipy já presentes"
fi

# ═══════════════════════════════════════════════════════════
# STEP 5 — Detecção de GPU
# ═══════════════════════════════════════════════════════════
section "5/14" "Detecção de GPU"

GPU_TYPE="none"      # none | nvidia | amd | apple
GPU_VRAM_GB=0
GPU_NAME=""
CUDA_VERSION=""

if [ "$FORCE_CPU" = "true" ]; then
    warn "Modo CPU forçado via --no-gpu"
else
    # ── NVIDIA ─────────────────────────────────────────────
    if has nvidia-smi; then
        GPU_TYPE="nvidia"
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
        VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
                  | awk '{s+=$1} END {print s}' || echo "0")
        GPU_VRAM_GB=$(echo "$VRAM_MB" | awk '{printf "%.1f", $1/1024}')
        if has nvcc; then
            CUDA_VERSION=$(nvcc --version 2>/dev/null | grep "release" | awk '{print $NF}' | tr -d ',')
        elif has nvidia-smi; then
            CUDA_VERSION=$(nvidia-smi 2>/dev/null | grep "CUDA Version" | awk '{print $NF}' || echo "unknown")
        fi
        pass "NVIDIA GPU detectada: $GPU_NAME  (VRAM: ${GPU_VRAM_GB} GB, CUDA: $CUDA_VERSION)"

    # ── AMD / ROCm ──────────────────────────────────────────
    elif has rocm-smi || [ -d /opt/rocm ]; then
        GPU_TYPE="amd"
        if has rocm-smi; then
            GPU_NAME=$(rocm-smi --showproductname 2>/dev/null | grep "GPU" | head -1 | awk '{$1=""; print $0}' | xargs || echo "AMD GPU")
            VRAM_MB=$(rocm-smi --showmeminfo vram 2>/dev/null | grep "Total Memory" | awk '{print $NF}' | head -1 || echo "0")
            GPU_VRAM_GB=$(echo "$VRAM_MB" | awk '{printf "%.1f", $1/1024/1024}')
        fi
        ROCM_VERSION=$(cat /opt/rocm/.info/version 2>/dev/null || echo "unknown")
        pass "AMD GPU detectada: $GPU_NAME  (VRAM: ${GPU_VRAM_GB} GB, ROCm: $ROCM_VERSION)"

    # ── Apple Silicon / Metal (macOS apenas) ────────────────
    elif [ "$OS_TYPE" = "Darwin" ] && \
         "$PYTHON_V" -c "import platform; exit(0 if 'arm' in platform.machine().lower() else 1)" 2>/dev/null; then
        GPU_TYPE="apple"
        GPU_NAME=$(system_profiler SPDisplaysDataType 2>/dev/null \
                   | grep "Chipset Model" | head -1 | awk -F': ' '{print $2}' \
                   || echo "Apple Silicon")
        UNIFIED_GB=$("$PYTHON_V" -c \
            "import subprocess; r=subprocess.run(['sysctl','hw.memsize'],capture_output=True,text=True); \
             print(round(int(r.stdout.split()[-1])/1e9,1))" 2>/dev/null || echo "0")
        GPU_VRAM_GB=$UNIFIED_GB
        pass "Apple Silicon detectado: $GPU_NAME  (memória unificada: ${GPU_VRAM_GB} GB)"
        info "Ollama usa Metal automaticamente — nenhuma configuração extra necessária"

    else
        GPU_TYPE="none"
        warn "Nenhuma GPU detectada — modelos rodarão em CPU (mais lento)"
        if [ "$OS_TYPE" = "Linux" ]; then
            warn "  Para NVIDIA: instale CUDA toolkit → https://developer.nvidia.com/cuda-downloads"
            warn "  Para AMD   : instale ROCm         → https://rocm.docs.amd.com/en/latest"
        fi
    fi
fi

echo "  GPU_TYPE=$GPU_TYPE  VRAM=${GPU_VRAM_GB}GB  OS=$OS_TYPE" | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════════
# STEP 6 — Instalar backend GPU (vLLM para NVIDIA/AMD)
# ═══════════════════════════════════════════════════════════
section "6/14" "Backend GPU (vLLM)"

VLLM_INSTALLED=false

if [ "$GPU_TYPE" = "nvidia" ] || [ "$GPU_TYPE" = "amd" ]; then
    if "$PYTHON_V" -c "import vllm" 2>/dev/null; then
        VLLM_VER=$("$PYTHON_V" -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "instalado")
        pass "vLLM $VLLM_VER já instalado"
        VLLM_INSTALLED=true
    else
        info "Instalando vLLM para $GPU_TYPE GPU..."
        if [ "$GPU_TYPE" = "nvidia" ]; then
            CUDA_SHORT=$(echo "$CUDA_VERSION" | grep -oE '[0-9]+\.[0-9]+' | head -1 | tr -d '.')
            if [ -n "$CUDA_SHORT" ] && [ "$CUDA_SHORT" -ge 121 ] 2>/dev/null; then
                VLLM_EXTRA="cuda"
            else
                VLLM_EXTRA=""
            fi
            info "  CUDA $CUDA_VERSION → vllm (pip install vllm)..."
            if run "$PIP_V" install vllm --quiet; then
                pass "vLLM instalado para NVIDIA CUDA"
                VLLM_INSTALLED=true
            else
                warn "Falha ao instalar vLLM"
                warn "  Veja: https://docs.vllm.ai/en/latest/getting_started/installation.html"
                warn "  Garanta que CUDA $CUDA_VERSION está instalado e compatível"
            fi
        elif [ "$GPU_TYPE" = "amd" ]; then
            info "  ROCm → vllm (pip install vllm com ROCm wheel)..."
            if run "$PIP_V" install vllm --extra-index-url https://download.pytorch.org/whl/rocm6.1 --quiet; then
                pass "vLLM instalado para AMD ROCm"
                VLLM_INSTALLED=true
            else
                warn "Falha ao instalar vLLM para ROCm"
                warn "  Veja: https://docs.vllm.ai/en/latest/getting_started/amd-installation.html"
            fi
        fi
    fi
elif [ "$GPU_TYPE" = "apple" ]; then
    info "vLLM não suporta Metal/MPS — modelos grandes rodarão via Ollama (Metal nativo)"
    info "Para modelos via API OpenAI-compatible, use: LM Studio (download em lmstudio.ai)"
    pass "Backend para Apple Silicon: Ollama (Metal) + LM Studio (MPS)"
else
    info "GPU não disponível — vLLM não será instalado"
    info "Modelos rodarão via Ollama em CPU"
    pass "Backend CPU: Ollama"
fi

# ═══════════════════════════════════════════════════════════
# STEP 7 — Node.js + ferramentas de acessibilidade
# ═══════════════════════════════════════════════════════════
section "7/14" "Node.js + ferramentas de acessibilidade"

if ! has node; then
    warn "Node.js não encontrado — pa11y, axe-core e lighthouse serão ignorados"
    if [ "$OS_TYPE" = "Darwin" ]; then
        warn "  → macOS  : brew install node"
    else
        warn "  → $(linux_install_hint "nodejs npm" "nodejs npm" "nodejs npm")"
        warn "  → Recomendado (evita problemas de permissão npm):"
        warn "      curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
        warn "      source ~/.bashrc && nvm install 20 && nvm use 20"
    fi
else
    pass "Node.js $(node --version) / npm $(npm --version)"

    # ── Linux: garantir npm prefix gravável sem sudo ────────
    # Quando Node.js é instalado via apt/dnf, o prefixo npm costuma ser
    # /usr ou /usr/local — ambos exigem sudo para instalar pacotes globais.
    # Reconfigurar para ~/.local/npm resolve sem precisar de root.
    if [ "$OS_TYPE" = "Linux" ]; then
        NPM_PREFIX=$(npm config get prefix 2>/dev/null || echo "")
        case "$NPM_PREFIX" in
            /usr*|/usr/local*)
                info "npm prefix = $NPM_PREFIX (requer sudo para globals)"
                info "Reconfigurando para \$HOME/.local/npm (sem sudo)..."
                mkdir -p "$HOME/.local/npm/bin" "$HOME/.local/npm/lib"
                npm config set prefix "$HOME/.local/npm"
                export PATH="$HOME/.local/npm/bin:$PATH"
                warn "Adicione ao ~/.bashrc (ou ~/.zshrc) para persistir:"
                warn "  export PATH=\"\$HOME/.local/npm/bin:\$PATH\""
                pass "npm prefix reconfigurado: $HOME/.local/npm"
                ;;
            /home/*|"$HOME"/*)
                pass "npm prefix já é de usuário: $NPM_PREFIX"
                # Garante que o bin esteja no PATH da sessão atual
                if [[ ":$PATH:" != *":$NPM_PREFIX/bin:"* ]]; then
                    export PATH="$NPM_PREFIX/bin:$PATH"
                fi
                ;;
        esac
    fi

    # ── Instalar ferramentas npm globais ────────────────────
    install_npm_tool() {
        local pkg="$1" bin="$2"
        if has "$bin"; then
            pass "$pkg  ($($bin --version 2>/dev/null | head -1))"
        else
            info "Instalando $pkg..."
            if npm install -g "$pkg" >> "$LOG_FILE" 2>&1; then
                pass "$pkg instalado"
            else
                warn "Falha ao instalar $pkg"
                if [ "$OS_TYPE" = "Linux" ]; then
                    warn "  Tente: npm config set prefix \$HOME/.local/npm && npm install -g $pkg"
                else
                    warn "  Tente: sudo npm install -g $pkg"
                fi
            fi
        fi
    }

    install_npm_tool "pa11y"         "pa11y"
    install_npm_tool "@axe-core/cli" "axe"
    install_npm_tool "lighthouse"    "lighthouse"
fi

# ═══════════════════════════════════════════════════════════
# STEP 8 — Playwright + Chromium
# ═══════════════════════════════════════════════════════════
section "8/14" "Playwright + Chromium"

if [ "$OS_TYPE" = "Linux" ]; then
    # No Linux, `playwright install --with-deps` tenta usar apt/dnf com sudo.
    # Separamos em duas etapas para máxima compatibilidade:
    #   1. Baixar o browser Chromium (sem root, apenas download)
    #   2. Instalar dependências de sistema (com sudo se disponível)

    info "Baixando Chromium (sem root)..."
    if run_visible "$PYTHON_V" -m playwright install chromium; then
        pass "Chromium baixado"

        info "Instalando dependências de sistema para Chromium..."
        # Tenta sem sudo (funciona quando já é root, ex.: containers CI)
        if "$PYTHON_V" -m playwright install-deps chromium >> "$LOG_FILE" 2>&1; then
            pass "Dependências de sistema instaladas"
        elif sudo "$PYTHON_V" -m playwright install-deps chromium >> "$LOG_FILE" 2>&1; then
            pass "Dependências de sistema instaladas (via sudo)"
        else
            warn "Não foi possível instalar dependências automaticamente."
            warn "Instale manualmente conforme a distro:"
            case "$LINUX_PM" in
                apt)
                    warn "  sudo apt-get install -y libnss3 libatk-bridge2.0-0 libxcomposite1 \\"
                    warn "    libxdamage1 libxrandr2 libgbm1 libxfixes3 libasound2 \\"
                    warn "    libx11-xcb1 libxcb-dri3-0 libdrm2 libxshmfence1"
                    ;;
                dnf|yum)
                    warn "  sudo $LINUX_PM install -y nss atk at-spi2-atk libXcomposite \\"
                    warn "    libXdamage libXrandr mesa-libgbm alsa-lib libX11-xcb"
                    ;;
                pacman)
                    warn "  sudo pacman -S nss atk at-spi2-atk libxcomposite \\"
                    warn "    libxdamage libxrandr mesa alsa-lib"
                    ;;
                *)
                    warn "  Execute: sudo $PYTHON_V -m playwright install-deps chromium"
                    warn "  Veja: https://playwright.dev/docs/browsers#install-system-dependencies"
                    ;;
            esac
            warn "O scanner Playwright pode falhar sem essas bibliotecas."
        fi
    else
        warn "playwright install chromium falhou — verifique a conexão"
        warn "  Retry: $PYTHON_V -m playwright install chromium"
    fi
else
    # macOS: --with-deps instala via download nativo, sem precisar de root
    info "Instalando browser Chromium com dependências..."
    if run_visible "$PYTHON_V" -m playwright install chromium --with-deps; then
        pass "Playwright Chromium instalado"
    else
        warn "Playwright install falhou — tente: playwright install chromium --with-deps"
    fi
fi

# ═══════════════════════════════════════════════════════════
# STEP 9 — Configurar .env (com variáveis de GPU)
# ═══════════════════════════════════════════════════════════
section "9/14" "Configurar .env"

build_gpu_env_block() {
    cat << EOF

# ─── GPU Configuration (gerado pelo setup.sh) ────────────────
EOF

    if [ "$GPU_TYPE" = "nvidia" ]; then
        cat << EOF
# NVIDIA CUDA — $GPU_NAME  (${GPU_VRAM_GB} GB VRAM)
CUDA_VISIBLE_DEVICES=0
# vLLM usa CUDA automaticamente via CUDA_VISIBLE_DEVICES
# Ajuste --tensor-parallel-size se tiver múltiplas GPUs

# Ollama — força GPU NVIDIA (padrão já é automático)
# OLLAMA_GPU_OVERHEAD=0         # bytes reservados para o sistema
# OLLAMA_NUM_GPU=-1              # -1 = usar todas as camadas na GPU
EOF
    elif [ "$GPU_TYPE" = "amd" ]; then
        cat << EOF
# AMD ROCm — $GPU_NAME  (${GPU_VRAM_GB} GB VRAM)
HIP_VISIBLE_DEVICES=0
ROCR_VISIBLE_DEVICES=0
# vLLM com ROCm usa HIP_VISIBLE_DEVICES automaticamente
EOF
    elif [ "$GPU_TYPE" = "apple" ]; then
        cat << EOF
# Apple Silicon — $GPU_NAME  (memória unificada: ${GPU_VRAM_GB} GB)
# Ollama usa Metal automaticamente — nenhuma variável extra necessária
# Contexto máximo ajustado para caber na memória unificada disponível
OLLAMA_CONTEXT_LENGTH=8192
# Para modelos 7B com 12 GB: pode aumentar para 16384
# Para modelos 14B com 12 GB: manter 8192 ou usar quantização Q4
EOF
    else
        cat << EOF
# CPU only — GPU não detectada
# Reduza paralelismo para evitar OOM
MAX_CONCURRENT_MODELS=1
MAX_CONCURRENT_SCANS=2
EOF
    fi
}

if [ -f "$ENV_FILE" ]; then
    if grep -q "GPU Configuration" "$ENV_FILE" 2>/dev/null; then
        pass ".env já contém configuração de GPU — mantendo"
    else
        info "Adicionando configuração de GPU ao .env existente..."
        build_gpu_env_block >> "$ENV_FILE"
        pass "Bloco GPU adicionado ao .env"
    fi
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        info ".env criado de .env.example"
    else
        cat > "$ENV_FILE" << 'ENVEOF'
# a11y-autofix — gerado pelo setup.sh
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
ENVEOF
    fi
    build_gpu_env_block >> "$ENV_FILE"
    pass ".env criado com configuração de GPU ($GPU_TYPE)"
fi

# ═══════════════════════════════════════════════════════════
# STEP 10 — Criar diretórios de trabalho
# ═══════════════════════════════════════════════════════════
section "10/14" "Criar diretórios de trabalho"

WORK_DIRS=(
    "experiment-results"
    "experiment-results/checkpoints"
    "experiment-results/sensitivity"
    "a11y-report"
    "dataset/results"
    "dataset/catalog"
    "dataset/snapshots"
    "experiments"
)
for d in "${WORK_DIRS[@]}"; do
    mkdir -p "$PROJECT_ROOT/$d"
done
pass "Diretórios criados: ${#WORK_DIRS[@]}"

CATALOG="$PROJECT_ROOT/dataset/catalog/projects.yaml"
if [ ! -f "$CATALOG" ]; then
    cat > "$CATALOG" << 'YAMLEOF'
metadata:
  version: "1.0"
  last_modified: ""
projects: []
YAMLEOF
    pass "dataset/catalog/projects.yaml criado (vazio)"
fi

# ═══════════════════════════════════════════════════════════
# STEP 11 — Configurar Ollama para usar GPU
# ═══════════════════════════════════════════════════════════
section "11/14" "Configurar Ollama (flags de GPU)"

if ! has ollama; then
    warn "Ollama não instalado"
    if [ "$OS_TYPE" = "Darwin" ]; then
        warn "  → macOS : brew install ollama"
    else
        warn "  → Linux : curl -fsSL https://ollama.com/install.sh | sh"
    fi
else
    OLLAMA_VER=$(ollama --version 2>/dev/null | head -1 || echo "?")
    pass "Ollama: $OLLAMA_VER"

    mkdir -p "$(dirname "$OLLAMA_ENV_FILE")"

    write_ollama_env() {
        # ~/.ollama/ollama.env é lido quando Ollama é iniciado manualmente
        # (ollama serve) no macOS/Linux. Em Linux com systemd, é necessário
        # um override adicional — veja as instruções impressas abaixo.
        cat > "$OLLAMA_ENV_FILE" << OLLAMAEOF
# Ollama environment — gerado pelo a11y-autofix setup.sh
# Lido por: ollama serve (macOS launchd e Linux manual)
# Linux systemd: veja override em /etc/systemd/system/ollama.service.d/

OLLAMAEOF

        if [ "$GPU_TYPE" = "nvidia" ]; then
            cat >> "$OLLAMA_ENV_FILE" << OLLAMAEOF
# NVIDIA CUDA — $GPU_NAME  (${GPU_VRAM_GB} GB)
CUDA_VISIBLE_DEVICES=0
OLLAMA_GPU_OVERHEAD=256000000   # 256 MB reservados para o sistema
OLLAMA_MAX_LOADED_MODELS=1
OLLAMAEOF
            pass "Ollama configurado para NVIDIA CUDA ($GPU_NAME, ${GPU_VRAM_GB} GB)"

        elif [ "$GPU_TYPE" = "amd" ]; then
            cat >> "$OLLAMA_ENV_FILE" << OLLAMAEOF
# AMD ROCm — $GPU_NAME  (${GPU_VRAM_GB} GB)
HIP_VISIBLE_DEVICES=0
ROCR_VISIBLE_DEVICES=0
OLLAMA_GPU_OVERHEAD=256000000
OLLAMA_MAX_LOADED_MODELS=1
OLLAMAEOF
            pass "Ollama configurado para AMD ROCm ($GPU_NAME, ${GPU_VRAM_GB} GB)"

        elif [ "$GPU_TYPE" = "apple" ]; then
            # Calcular context length ideal para a memória disponível.
            # _vram_gte substitui `bc -l` (nem sempre instalado no Linux).
            if _vram_gte 24; then
                CTX=32768
            elif _vram_gte 16; then
                CTX=16384
            else
                CTX=8192
            fi
            cat >> "$OLLAMA_ENV_FILE" << OLLAMAEOF
# Apple Silicon Metal — $GPU_NAME  (${GPU_VRAM_GB} GB unificada)
OLLAMA_CONTEXT_LENGTH=$CTX
OLLAMA_FLASH_ATTENTION=1        # acelera atenção em Metal
OLLAMA_MAX_LOADED_MODELS=1      # apenas 1 modelo carregado por vez
# Ollama usa Metal automaticamente em Apple Silicon (sem config extra de GPU)
OLLAMAEOF
            pass "Ollama configurado para Apple Silicon ($GPU_NAME, ctx=$CTX)"
            info "  Flash Attention ativado para Metal"

        else
            cat >> "$OLLAMA_ENV_FILE" << OLLAMAEOF
# CPU only — GPU não detectada
OLLAMA_NUM_GPU=0                # forçar CPU
OLLAMA_NUM_PARALLEL=1           # 1 request simultâneo (CPU)
OLLAMA_MAX_LOADED_MODELS=1
OLLAMAEOF
            warn "Ollama configurado para CPU — inferência será lenta"
        fi

        info "  Config em: $OLLAMA_ENV_FILE"
    }

    write_ollama_env

    # ── Linux: instruções para serviço systemd ──────────────
    # ~/.ollama/ollama.env NÃO é lido pelo daemon systemd.
    # É necessário criar um override de serviço.
    if [ "$OS_TYPE" = "Linux" ] && systemctl list-unit-files ollama.service &>/dev/null 2>&1; then
        SYSTEMD_OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
        info ""
        info "╔══ Ollama systemd detectado ══════════════════════════════════╗"
        info "║  ~/.ollama/ollama.env não é lido pelo serviço systemd.      ║"
        info "║  Execute os comandos abaixo para aplicar a config de GPU:   ║"
        info "╚══════════════════════════════════════════════════════════════╝"
        info ""
        info "  sudo mkdir -p $SYSTEMD_OVERRIDE_DIR"

        # Gera o conteúdo do override conforme GPU detectada
        OVERRIDE_CONTENT="[Service]"
        if [ "$GPU_TYPE" = "nvidia" ]; then
            OVERRIDE_CONTENT="$OVERRIDE_CONTENT
Environment=\"CUDA_VISIBLE_DEVICES=0\"
Environment=\"OLLAMA_GPU_OVERHEAD=256000000\"
Environment=\"OLLAMA_MAX_LOADED_MODELS=1\""
        elif [ "$GPU_TYPE" = "amd" ]; then
            OVERRIDE_CONTENT="$OVERRIDE_CONTENT
Environment=\"HIP_VISIBLE_DEVICES=0\"
Environment=\"ROCR_VISIBLE_DEVICES=0\"
Environment=\"OLLAMA_GPU_OVERHEAD=256000000\""
        else
            OVERRIDE_CONTENT="$OVERRIDE_CONTENT
Environment=\"OLLAMA_NUM_GPU=0\"
Environment=\"OLLAMA_NUM_PARALLEL=1\"
Environment=\"OLLAMA_MAX_LOADED_MODELS=1\""
        fi

        # Imprime o comando completo copiável
        info "  sudo tee $SYSTEMD_OVERRIDE_DIR/override.conf << 'SEOF'"
        while IFS= read -r line; do
            info "  $line"
        done <<< "$OVERRIDE_CONTENT"
        info "  SEOF"
        info "  sudo systemctl daemon-reload"
        info "  sudo systemctl restart ollama"
        info ""
    fi

    # ── Recarregar/reiniciar daemon se estiver rodando ─────
    if curl -s --max-time 2 http://localhost:11434/ &>/dev/null; then
        info "Ollama daemon está rodando — reiniciando para aplicar config GPU..."

        if [ "$OS_TYPE" = "Linux" ] && systemctl is-active --quiet ollama 2>/dev/null; then
            # Linux com systemd: reiniciar via systemctl
            if sudo systemctl restart ollama >> "$LOG_FILE" 2>&1; then
                sleep 3
                if curl -s --max-time 5 http://localhost:11434/ &>/dev/null; then
                    pass "Ollama (systemd) reiniciado com config GPU"
                else
                    warn "Ollama não respondeu após reinicialização"
                    warn "  sudo systemctl status ollama"
                fi
            else
                warn "sudo systemctl restart ollama falhou"
                warn "  Reinicie manualmente: sudo systemctl restart ollama"
            fi
        else
            # macOS launchd ou Linux sem systemd: pkill + ollama serve
            if pkill -x ollama 2>/dev/null || pkill ollama 2>/dev/null; then
                sleep 2
                OLLAMA_ORIGINS='*' ollama serve >> "$LOG_FILE" 2>&1 &
                sleep 3
                if curl -s --max-time 5 http://localhost:11434/ &>/dev/null; then
                    pass "Ollama reiniciado com configuração GPU"
                else
                    warn "Ollama não respondeu após reinicialização — verifique: ollama serve"
                fi
            fi
        fi
    else
        info "Ollama daemon não está rodando"
        if [ "$OS_TYPE" = "Linux" ] && systemctl is-enabled --quiet ollama 2>/dev/null; then
            info "  Inicie com: sudo systemctl start ollama"
        else
            info "  Inicie com: OLLAMA_ORIGINS='*' ollama serve"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════
# STEP 12 — Baixar modelos recomendados
# ═══════════════════════════════════════════════════════════
section "12/14" "Baixar modelos Ollama"

if ! has ollama; then
    warn "Ollama não disponível — pulando pull de modelos"
elif ! curl -s --max-time 3 http://localhost:11434/ &>/dev/null; then
    warn "Ollama daemon não está rodando — pulando pull"
    warn "  Execute depois: ollama serve && ollama pull qwen2.5-coder:7b"
elif [ "$PULL_MODELS" = "false" ]; then
    info "Pull desativado via --no-models"
else
    MODELS_TO_PULL=()

    if [ "$GPU_TYPE" = "none" ] || [ "$FORCE_CPU" = "true" ]; then
        # CPU: só modelo pequeno
        MODELS_TO_PULL=("qwen2.5-coder:7b")
        info "CPU only → baixando apenas modelo 7B"
    elif _vram_gte 20; then
        # VRAM ≥ 20 GB: todos os recomendados
        MODELS_TO_PULL=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b")
        info "VRAM ≥ 20 GB → baixando todos os modelos recomendados"
    elif _vram_gte 12; then
        # VRAM 12–20 GB: 7b + 14b
        MODELS_TO_PULL=("qwen2.5-coder:7b" "qwen2.5-coder:14b")
        info "VRAM ${GPU_VRAM_GB} GB → baixando modelos ≤14B"
        warn "deepseek-coder-v2:16b pode não caber na VRAM — baixe com: ollama pull deepseek-coder-v2:16b"
    elif _vram_gte 6; then
        # VRAM 6–12 GB: só 7b
        MODELS_TO_PULL=("qwen2.5-coder:7b")
        info "VRAM ${GPU_VRAM_GB} GB → baixando apenas modelo 7B"
        warn "Modelos 14B+ precisam de mais VRAM — use com offload parcial"
    else
        MODELS_TO_PULL=("qwen2.5-coder:7b")
        info "Baixando modelo mínimo (7B)"
    fi

    AVAILABLE=$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' || true)
    for model in "${MODELS_TO_PULL[@]}"; do
        if echo "$AVAILABLE" | grep -qF "$model"; then
            pass "Já disponível: $model"
        else
            info "Baixando $model (pode demorar)..."
            if ollama pull "$model" 2>&1 | tee -a "$LOG_FILE" | tail -5; then
                pass "$model baixado"
            else
                warn "Falha ao baixar $model → tente: ollama pull $model"
            fi
        fi
    done
fi

# ═══════════════════════════════════════════════════════════
# STEP 13 — Hardware preflight check
# ═══════════════════════════════════════════════════════════
section "13/14" "Hardware preflight check"

if has a11y-autofix; then
    info "Executando a11y-autofix hardware..."
    if a11y-autofix hardware 2>&1 | tee -a "$LOG_FILE"; then
        pass "Hardware preflight OK"
    else
        warn "Alguns checks falharam — veja: a11y-autofix hardware"
    fi
else
    warn "CLI não no PATH — ative o venv e execute: a11y-autofix hardware"
fi

# ═══════════════════════════════════════════════════════════
# STEP 14 — Resumo final
# ═══════════════════════════════════════════════════════════
section "14/14" "Resumo"

echo "" | tee -a "$LOG_FILE"
echo "══════════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo -e "${BOLD}Resumo do Setup${NC}" | tee -a "$LOG_FILE"
echo "══════════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo -e "${GREEN}  ✓ Passou  : $N_PASS${NC}"   | tee -a "$LOG_FILE"
[ "$N_WARN" -gt 0 ] && echo -e "${YELLOW}  ⚠ Avisos  : $N_WARN${NC}" | tee -a "$LOG_FILE"
[ "$N_FAIL" -gt 0 ] && echo -e "${RED}  ✗ Falhas  : $N_FAIL${NC}"   | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

echo -e "${BOLD}SO/GPU:${NC}" | tee -a "$LOG_FILE"
echo "  Sistema  : $OS_TYPE${LINUX_PM:+ ($LINUX_PM)}" | tee -a "$LOG_FILE"
case "$GPU_TYPE" in
    nvidia) echo -e "  GPU      : ${GREEN}NVIDIA${NC} $GPU_NAME  (${GPU_VRAM_GB} GB VRAM, CUDA $CUDA_VERSION)"  ;;
    amd)    echo -e "  GPU      : ${GREEN}AMD${NC} $GPU_NAME  (${GPU_VRAM_GB} GB VRAM, ROCm)"  ;;
    apple)  echo -e "  GPU      : ${GREEN}Apple Silicon${NC} $GPU_NAME  (${GPU_VRAM_GB} GB unificada, Metal)"  ;;
    none)   echo -e "  GPU      : ${YELLOW}CPU only${NC} — inferência mais lenta"  ;;
esac
echo "" | tee -a "$LOG_FILE"

echo -e "${BOLD}Para ativar o ambiente em novos terminais:${NC}"
echo "  source .venv/bin/activate"
echo ""
echo -e "${BOLD}Próximos passos:${NC}"
echo "  a11y-autofix hardware                                # verificar hardware"
echo "  a11y-autofix models list                             # listar modelos"
echo "  a11y-autofix fix ./src --dry-run                     # scan de teste"
echo "  a11y-autofix experiment run experiments/base.yaml    # experimento"
echo ""
echo -e "${BOLD}Log completo:${NC} $LOG_FILE"
echo "" | tee -a "$LOG_FILE"

if [ "$N_FAIL" -gt 0 ]; then
    echo -e "${RED}${BOLD}Setup concluído com $N_FAIL falha(s). Verifique as mensagens acima.${NC}" | tee -a "$LOG_FILE"
    exit 1
elif [ "$N_WARN" -gt 0 ]; then
    echo -e "${YELLOW}${BOLD}Setup concluído com avisos. Experimento pode rodar com funcionalidade reduzida.${NC}" | tee -a "$LOG_FILE"
else
    echo -e "${GREEN}${BOLD}✓ Ambiente configurado com sucesso para o experimento!${NC}" | tee -a "$LOG_FILE"
fi
