#!/usr/bin/env bash
# =============================================================================
#  setup_linux.sh — Configuração do ambiente sem root (Linux)
#
#  Instala TUDO em ~/ sem precisar de sudo nem Docker:
#    • Python 3.12  via pyenv      (~/.pyenv)
#    • Node.js LTS  via nvm        (~/.nvm)
#    • Ollama       binário direto (~/.local/bin)
#    • vLLM         via pip no .venv (CUDA nativo, Linux)
#    • pa11y / axe-core via npm local
#    • Playwright Chromium
#
#  Uso:
#    chmod +x setup_linux.sh
#    ./setup_linux.sh               # setup completo
#    ./setup_linux.sh --no-models   # pula pull dos modelos
#    ./setup_linux.sh --cpu-only    # força CPU (sem GPU)
#    ./setup_linux.sh --vllm        # instala vLLM (requer CUDA ≥12.1)
#    ./setup_linux.sh --no-vllm     # usa só Ollama (mais simples)
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Flags ─────────────────────────────────────────────────────────────────────
NO_MODELS=0
CPU_ONLY=0
USE_VLLM=0      # por padrão tenta detectar; --vllm força; --no-vllm desativa
NO_VLLM=0

for arg in "$@"; do
    case "$arg" in
        --no-models) NO_MODELS=1 ;;
        --cpu-only)  CPU_ONLY=1  ;;
        --vllm)      USE_VLLM=1  ;;
        --no-vllm)   NO_VLLM=1   ;;
    esac
done

# ── Cores ─────────────────────────────────────────────────────────────────────
R='\033[0m'; BOLD='\033[1m'
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; GRAY='\033[0;37m'

pass()    { echo -e "  ${GREEN}[OK]${R} $*"; }
warn()    { echo -e "  ${YELLOW}[AVISO]${R} $*"; }
fail()    { echo -e "  ${RED}[ERRO]${R} $*"; }
info()    { echo -e "  ${CYAN}-->${R} $*"; }
section() { echo -e "\n${MAGENTA}=== $* ===${R}"; }
die()     { echo -e "\n${RED}ERRO FATAL: $*${R}" >&2; exit 1; }

LOG="$SCRIPT_DIR/setup_linux.log"
echo "Setup iniciado em $(date)" > "$LOG"
tee_log() { tee -a "$LOG" > /dev/null; }

# Contadores
N_PASS=0; N_WARN=0; N_FAIL=0
ok()  { N_PASS=$((N_PASS+1)); pass "$@"; }
nok() { N_WARN=$((N_WARN+1)); warn "$@"; }

has() { command -v "$1" &>/dev/null; }

echo ""
echo -e "${CYAN}${BOLD}*** a11y-autofix — Setup Linux (sem root) ***${R}"
echo "=================================================="
echo "  Projeto : $SCRIPT_DIR"
echo "  Log     : $LOG"
echo "  Data    : $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# =============================================================================
# STEP 1 — Verificar dependências do sistema (sem instalar)
# =============================================================================
section "[1/12] Verificar dependências do sistema"

MISSING_SYSTEM=()
for dep in curl git gcc make; do
    if has "$dep"; then
        ok "$dep encontrado"
    else
        MISSING_SYSTEM+=("$dep")
        fail "$dep não encontrado — necessário para compilar Python"
    fi
done

if [[ ${#MISSING_SYSTEM[@]} -gt 0 ]]; then
    echo ""
    warn "Dependências de sistema ausentes: ${MISSING_SYSTEM[*]}"
    warn "Peça ao administrador para instalar (sem root você não pode):"
    echo -e "  ${GRAY}# Ubuntu/Debian:${R}"
    echo -e "  ${CYAN}sudo apt-get install -y curl git gcc make \\"
    echo -e "    libssl-dev zlib1g-dev libbz2-dev libreadline-dev \\"
    echo -e "    libsqlite3-dev libffi-dev liblzma-dev${R}"
    echo ""
    echo -e "  ${GRAY}# RHEL/CentOS/Rocky:${R}"
    echo -e "  ${CYAN}sudo dnf install -y curl git gcc make \\"
    echo -e "    openssl-devel zlib-devel bzip2-devel readline-devel \\"
    echo -e "    sqlite-devel libffi-devel xz-devel${R}"
    echo ""
    # Não aborta — pode ser que Python já esteja disponível no sistema
fi

# =============================================================================
# STEP 2 — Python 3.10+ (sistema ou pyenv)
# =============================================================================
section "[2/12] Python 3.10+"

PYTHON_EXE=""

# Procurar Python 3.10+ já disponível no sistema
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if has "$candidate"; then
        ver=$("$candidate" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null || echo "0.0")
        major=${ver%%.*}; minor=${ver##*.}
        if [[ $major -ge 3 && $minor -ge 10 ]]; then
            PYTHON_EXE="$candidate"
            ok "Python $("$candidate" --version 2>&1) encontrado em PATH"
            break
        fi
    fi
done

if [[ -z "$PYTHON_EXE" ]]; then
    info "Python 3.10+ não encontrado — instalando via pyenv..."

    PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"

    if [[ ! -d "$PYENV_ROOT" ]]; then
        info "Clonando pyenv em $PYENV_ROOT..."
        git clone --depth=1 https://github.com/pyenv/pyenv.git "$PYENV_ROOT" >> "$LOG" 2>&1
        git clone --depth=1 https://github.com/pyenv/pyenv-virtualenv.git \
            "$PYENV_ROOT/plugins/pyenv-virtualenv" >> "$LOG" 2>&1 || true
    fi

    export PYENV_ROOT
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)" 2>/dev/null || true

    if ! pyenv versions --bare 2>/dev/null | grep -q "^3.12"; then
        info "Compilando Python 3.12.3 (pode levar 3-5 min)..."
        PYTHON_CONFIGURE_OPTS="--enable-optimizations --with-lto" \
            pyenv install 3.12.3 >> "$LOG" 2>&1
    fi

    pyenv global 3.12.3
    PYTHON_EXE="python"
    ok "Python $(python --version 2>&1) via pyenv"

    # Persistir pyenv no shell
    SHELL_RC=""
    [[ -f "$HOME/.zshrc" ]] && SHELL_RC="$HOME/.zshrc"
    [[ -f "$HOME/.bashrc" ]] && SHELL_RC="$HOME/.bashrc"

    if [[ -n "$SHELL_RC" ]]; then
        if ! grep -q "pyenv init" "$SHELL_RC"; then
            cat >> "$SHELL_RC" << 'PYENV_BLOCK'

# pyenv — adicionado pelo a11y-autofix setup_linux.sh
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
PYENV_BLOCK
            info "pyenv adicionado a $SHELL_RC"
        fi
    fi
fi

PYTHON_FULL=$("$PYTHON_EXE" --version 2>&1)

# =============================================================================
# STEP 3 — Virtual environment (.venv)
# =============================================================================
section "[3/12] Virtual environment (.venv/)"

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [[ -d "$VENV_DIR" && -x "$VENV_PYTHON" ]]; then
    ok ".venv já existe — reutilizando"
else
    [[ -d "$VENV_DIR" ]] && { info ".venv corrompido — recriando..."; rm -rf "$VENV_DIR"; }
    info "Criando .venv com $PYTHON_EXE..."
    "$PYTHON_EXE" -m venv "$VENV_DIR"
    ok ".venv criado em $VENV_DIR"
fi

# =============================================================================
# STEP 4 — Dependências Python
# =============================================================================
section "[4/12] Dependências Python"

info "Atualizando pip / setuptools / wheel..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel --quiet >> "$LOG" 2>&1

info "Instalando a11y-autofix[dev]..."
"$VENV_PYTHON" -m pip install -e "$SCRIPT_DIR[dev]" --quiet >> "$LOG" 2>&1
ok "a11y-autofix instalado (editable)"

info "Instalando extras científicos..."
"$VENV_PYTHON" -m pip install --quiet \
    psutil numpy scipy pandas matplotlib seaborn \
    >> "$LOG" 2>&1
ok "psutil, numpy, scipy, pandas, matplotlib, seaborn"

# =============================================================================
# STEP 5 — Detectar GPU
# =============================================================================
section "[5/12] Detectar GPU"

GPU_TYPE="none"
GPU_VRAM_GB=0
GPU_NAME=""
CUDA_VERSION=""
N_GPUS=0

if [[ $CPU_ONLY -eq 1 ]]; then
    nok "Modo CPU forçado via --cpu-only"
elif has nvidia-smi; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | xargs)
    # Soma VRAM de todas as GPUs
    TOTAL_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
        | awk '{sum+=$1} END{print sum}')
    GPU_VRAM_GB=$(echo "scale=1; $TOTAL_VRAM_MB / 1024" | bc 2>/dev/null || echo "0")
    N_GPUS=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)
    CUDA_VERSION=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version: \K[\d.]+" | head -1)
    GPU_TYPE="nvidia"
    ok "NVIDIA GPU(s): $N_GPUS × $GPU_NAME  (VRAM total: ${GPU_VRAM_GB} GB, CUDA: $CUDA_VERSION)"
elif has rocm-smi; then
    GPU_TYPE="amd"
    GPU_NAME="AMD ROCm"
    ok "AMD ROCm detectado"
else
    nok "Nenhuma GPU detectada — rodará em CPU"
fi

echo "  GPU_TYPE=$GPU_TYPE  VRAM=${GPU_VRAM_GB}GB  N_GPUS=$N_GPUS" >> "$LOG"

# =============================================================================
# STEP 6 — vLLM (Linux + CUDA, muito mais rápido que Ollama)
# =============================================================================
section "[6/12] Backend LLM (vLLM / Ollama)"

VLLM_OK=0

if [[ $NO_VLLM -eq 1 ]]; then
    info "vLLM desativado via --no-vllm — usando Ollama"
elif [[ "$GPU_TYPE" == "nvidia" ]]; then
    info "CUDA detectado — verificando vLLM..."

    # Verificar versão CUDA ≥ 12.1 (requisito vLLM ≥ 0.4)
    CUDA_MAJOR=${CUDA_VERSION%%.*}
    CUDA_MINOR=$(echo "$CUDA_VERSION" | cut -d. -f2)

    if [[ $USE_VLLM -eq 1 ]] || { [[ -n "$CUDA_MAJOR" ]] && \
        { [[ $CUDA_MAJOR -gt 12 ]] || { [[ $CUDA_MAJOR -eq 12 ]] && [[ ${CUDA_MINOR:-0} -ge 1 ]]; }; }; }; then

        if "$VENV_PYTHON" -c "import vllm" 2>/dev/null; then
            VLLM_VER=$("$VENV_PYTHON" -c "import vllm; print(vllm.__version__)" 2>/dev/null)
            ok "vLLM $VLLM_VER já instalado"
            VLLM_OK=1
        else
            info "Instalando vLLM (pode demorar — baixa ~2 GB de wheels CUDA)..."
            "$VENV_PYTHON" -m pip install vllm --quiet >> "$LOG" 2>&1 && {
                VLLM_VER=$("$VENV_PYTHON" -c "import vllm; print(vllm.__version__)" 2>/dev/null)
                ok "vLLM $VLLM_VER instalado"
                VLLM_OK=1
            } || {
                nok "vLLM falhou — usando Ollama como fallback"
            }
        fi
    else
        nok "CUDA $CUDA_VERSION < 12.1 — vLLM requer CUDA ≥ 12.1, usando Ollama"
    fi
elif [[ "$GPU_TYPE" == "amd" ]]; then
    info "AMD ROCm detectado — tentando vLLM+ROCm..."
    "$VENV_PYTHON" -m pip install vllm --quiet >> "$LOG" 2>&1 && VLLM_OK=1 || \
        nok "vLLM+ROCm falhou — usando Ollama"
else
    info "CPU only — vLLM não instalado (sem GPU CUDA)"
fi

# =============================================================================
# STEP 7 — Ollama (binário local, sem root)
# =============================================================================
section "[7/12] Ollama (instalação local)"

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

# Adicionar ~/.local/bin ao PATH se necessário
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi

OLLAMA_BIN="$LOCAL_BIN/ollama"

if [[ -x "$OLLAMA_BIN" ]]; then
    OLLAMA_VER=$("$OLLAMA_BIN" --version 2>/dev/null | head -1 || echo "desconhecida")
    ok "Ollama já instalado: $OLLAMA_VER"
else
    info "Baixando binário Ollama para $OLLAMA_BIN..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  OLLAMA_URL="https://ollama.com/download/ollama-linux-amd64" ;;
        aarch64) OLLAMA_URL="https://ollama.com/download/ollama-linux-arm64" ;;
        *)       die "Arquitetura $ARCH não suportada pelo Ollama" ;;
    esac

    curl -fsSL "$OLLAMA_URL" -o "$OLLAMA_BIN" >> "$LOG" 2>&1
    chmod +x "$OLLAMA_BIN"
    ok "Ollama instalado em $OLLAMA_BIN"
fi

# Configurar variáveis de ambiente para Ollama sem root
OLLAMA_MODELS_DIR="$HOME/.ollama/models"
mkdir -p "$OLLAMA_MODELS_DIR"

# Persistir PATH e variáveis do Ollama no shell
SHELL_RC=""
[[ -f "$HOME/.zshrc"  ]] && SHELL_RC="$HOME/.zshrc"
[[ -f "$HOME/.bashrc" ]] && SHELL_RC="$HOME/.bashrc"

if [[ -n "$SHELL_RC" ]]; then
    if ! grep -q "a11y-autofix setup_linux" "$SHELL_RC"; then
        cat >> "$SHELL_RC" << SHELL_BLOCK

# a11y-autofix setup_linux.sh
export PATH="\$HOME/.local/bin:\$PATH"
export OLLAMA_MODELS="\$HOME/.ollama/models"
SHELL_BLOCK
        # pyenv block (se instalado acima)
        info "PATH e OLLAMA_MODELS adicionados a $SHELL_RC"
    fi
fi

# =============================================================================
# STEP 8 — Node.js via nvm (sem root)
# =============================================================================
section "[8/12] Node.js via nvm"

NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    source "$NVM_DIR/nvm.sh"
    ok "nvm já instalado: $(nvm --version 2>/dev/null)"
else
    info "Instalando nvm..."
    # Baixa e executa o install script do nvm (instala apenas em ~/.nvm)
    INSTALL_SCRIPT=$(mktemp)
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh \
        -o "$INSTALL_SCRIPT" >> "$LOG" 2>&1
    bash "$INSTALL_SCRIPT" >> "$LOG" 2>&1
    rm -f "$INSTALL_SCRIPT"
    # shellcheck source=/dev/null
    source "$NVM_DIR/nvm.sh"
    ok "nvm instalado: $(nvm --version 2>/dev/null)"
fi

# Instalar Node.js LTS se necessário
if has node; then
    NODE_VER=$(node --version 2>/dev/null)
    ok "Node.js $NODE_VER já disponível"
else
    info "Instalando Node.js LTS..."
    nvm install --lts >> "$LOG" 2>&1
    nvm use --lts >> "$LOG" 2>&1
    ok "Node.js $(node --version) instalado via nvm"
fi

# Configurar npm prefix local (evita escrita em diretórios do sistema)
NPM_PREFIX="$HOME/.npm-global"
mkdir -p "$NPM_PREFIX"
npm config set prefix "$NPM_PREFIX" >> "$LOG" 2>&1

if [[ ":$PATH:" != *":$NPM_PREFIX/bin:"* ]]; then
    export PATH="$NPM_PREFIX/bin:$PATH"
fi

if [[ -n "$SHELL_RC" ]] && ! grep -q "npm-global" "$SHELL_RC"; then
    echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$SHELL_RC"
    info "npm-global/bin adicionado a $SHELL_RC"
fi

ok "npm $(npm --version) / prefix: $NPM_PREFIX"

# =============================================================================
# STEP 9 — Ferramentas de acessibilidade npm
# =============================================================================
section "[9/12] Ferramentas a11y (npm)"

install_npm_tool() {
    local pkg="$1" bin="${2:-$1}"
    if has "$bin"; then
        ok "$pkg  ($(command "$bin" --version 2>/dev/null | head -1))"
    else
        info "Instalando $pkg..."
        npm install -g "$pkg" >> "$LOG" 2>&1 && ok "$pkg instalado" || \
            nok "Falha ao instalar $pkg — tente: npm install -g $pkg"
    fi
}

install_npm_tool "pa11y"         "pa11y"
install_npm_tool "@axe-core/cli" "axe"
install_npm_tool "eslint"        "eslint"
install_npm_tool "eslint-plugin-jsx-a11y" "eslint"   # plugin, não tem bin próprio

# =============================================================================
# STEP 10 — Playwright Chromium (sem root — instala em ~/.cache)
# =============================================================================
section "[10/12] Playwright Chromium"

info "Instalando Playwright Chromium (sem root, instala em ~/.cache/ms-playwright)..."
PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" \
    "$VENV_PYTHON" -m playwright install chromium >> "$LOG" 2>&1 && \
    ok "Playwright Chromium instalado" || \
    nok "playwright install falhou — tente: $VENV_PYTHON -m playwright install chromium"

if [[ -n "$SHELL_RC" ]] && ! grep -q "PLAYWRIGHT_BROWSERS_PATH" "$SHELL_RC"; then
    echo 'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"' >> "$SHELL_RC"
fi

# =============================================================================
# STEP 11 — Configurar .env
# =============================================================================
section "[11/12] Configurar .env"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

gpu_env_block() {
    echo ""
    echo "# --- GPU Configuration (gerado pelo setup_linux.sh) ---"
    if [[ "$GPU_TYPE" == "nvidia" ]]; then
        echo "# NVIDIA — $GPU_NAME  (${GPU_VRAM_GB} GB VRAM, $N_GPUS GPU(s))"
        echo "CUDA_VISIBLE_DEVICES=0"
        if [[ $VLLM_OK -eq 1 ]]; then
            echo "LLM_BACKEND=vllm"
            echo "VLLM_BASE_URL=http://localhost:8000/v1"
            echo "VLLM_TENSOR_PARALLEL_SIZE=$N_GPUS"
        else
            echo "LLM_BACKEND=ollama"
        fi
    elif [[ "$GPU_TYPE" == "amd" ]]; then
        echo "# AMD ROCm"
        echo "HIP_VISIBLE_DEVICES=0"
        echo "LLM_BACKEND=ollama"
    else
        echo "# CPU only"
        echo "LLM_BACKEND=ollama"
        echo "MAX_CONCURRENT_MODELS=1"
    fi
}

if [[ -f "$ENV_FILE" ]]; then
    if grep -q "GPU Configuration" "$ENV_FILE"; then
        ok ".env já contém bloco GPU — mantendo"
    else
        gpu_env_block >> "$ENV_FILE"
        ok "Bloco GPU adicionado ao .env existente"
    fi
else
    if [[ -f "$ENV_EXAMPLE" ]]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        info ".env criado de .env.example"
    else
        cat > "$ENV_FILE" << 'ENVEOF'
# a11y-autofix — gerado pelo setup_linux.sh
DEFAULT_MODEL=qwen2.5-coder-14b
LOG_LEVEL=INFO
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false
USE_PLAYWRIGHT=true
MIN_TOOL_CONSENSUS=2
MAX_CONCURRENT_SCANS=8
MAX_CONCURRENT_AGENTS=4
MAX_CONCURRENT_MODELS=1
SCAN_TIMEOUT=120
AGENT_TIMEOUT=300
SWE_MAX_ISSUES=4
MAX_RETRIES_PER_AGENT=3
OUTPUT_DIR=./a11y-report
RESULTS_DIR=./experiment-results
PLAYWRIGHT_BROWSERS_PATH=${HOME}/.cache/ms-playwright
ENVEOF
    fi
    gpu_env_block >> "$ENV_FILE"
    ok ".env criado"
fi

# =============================================================================
# STEP 12 — Criar diretórios de trabalho
# =============================================================================
section "[12/12] Diretórios e pull de modelos"

for d in experiment-results experiment-results/checkpoints a11y-report \
          dataset/results dataset/catalog dataset/snapshots experiments; do
    mkdir -p "$SCRIPT_DIR/$d"
done
ok "Diretórios criados"

# ── Pull de modelos ─────────────────────────────────────────────────────────
if [[ $NO_MODELS -eq 1 ]]; then
    info "Pull de modelos desativado via --no-models"
elif [[ $VLLM_OK -eq 1 ]]; then
    info "Backend vLLM ativo — modelos serão baixados pelo HuggingFace na primeira execução"
    info "Modelos padrão: Qwen/Qwen2.5-Coder-32B-Instruct (via HF cache em ~/.cache/huggingface)"
    ok "Pull de modelos: delegado ao vLLM na primeira execução"
else
    # Tentar iniciar Ollama em background para pull
    if ! curl -sf http://localhost:11434/ > /dev/null 2>&1; then
        info "Iniciando Ollama em background para pull de modelos..."
        OLLAMA_MODELS="$HOME/.ollama/models" nohup "$OLLAMA_BIN" serve >> "$LOG" 2>&1 &
        OLLAMA_PID=$!
        sleep 5
        STARTED_OLLAMA=1
    else
        STARTED_OLLAMA=0
    fi

    # Selecionar modelos pelo VRAM disponível
    MODELS_TO_PULL=()
    VRAM_INT=${GPU_VRAM_GB%.*}

    if   [[ $VRAM_INT -ge 48 ]]; then
        MODELS_TO_PULL=("qwen2.5-coder:32b" "deepseek-coder-v2:16b" "qwen2.5-coder:14b")
        info "VRAM ≥ 48 GB → baixando modelos 14B–32B"
    elif [[ $VRAM_INT -ge 20 ]]; then
        MODELS_TO_PULL=("qwen2.5-coder:14b" "deepseek-coder-v2:16b" "starcoder2:15b")
        info "VRAM ≥ 20 GB → baixando modelos 14B–16B"
    elif [[ $VRAM_INT -ge 12 ]]; then
        MODELS_TO_PULL=("qwen2.5-coder:14b" "qwen2.5-coder:7b")
        info "VRAM ≥ 12 GB → baixando modelos até 14B"
    else
        MODELS_TO_PULL=("qwen2.5-coder:7b" "codellama:7b")
        info "VRAM < 12 GB → baixando modelos 7B"
    fi

    AVAILABLE=$("$OLLAMA_BIN" list 2>/dev/null | tail -n +2 | awk '{print $1}' || echo "")
    for model in "${MODELS_TO_PULL[@]}"; do
        if echo "$AVAILABLE" | grep -q "^${model}"; then
            ok "Já disponível: $model"
        else
            info "Baixando $model (pode demorar)..."
            "$OLLAMA_BIN" pull "$model" >> "$LOG" 2>&1 && ok "$model baixado" || \
                nok "Falha ao baixar $model — tente depois: ollama pull $model"
        fi
    done

    [[ ${STARTED_OLLAMA:-0} -eq 1 ]] && kill "$OLLAMA_PID" 2>/dev/null || true
fi

# =============================================================================
# Resumo
# =============================================================================
echo ""
echo "=================================================="
echo -e "${BOLD}Resumo do Setup${R}"
echo "=================================================="
echo -e "  ${GREEN}Passou  : $N_PASS${R}"
[[ $N_WARN -gt 0 ]] && echo -e "  ${YELLOW}Avisos  : $N_WARN${R}"

echo ""
echo "Sistema:"
echo "  Python  : $PYTHON_FULL"
echo "  venv    : $VENV_DIR"
case "$GPU_TYPE" in
    nvidia) echo -e "  GPU     : ${GREEN}NVIDIA $GPU_NAME  (${GPU_VRAM_GB} GB VRAM × $N_GPUS)${R}" ;;
    amd)    echo -e "  GPU     : ${YELLOW}AMD ROCm${R}" ;;
    none)   echo -e "  GPU     : ${YELLOW}CPU only${R}" ;;
esac
[[ $VLLM_OK -eq 1 ]] && \
    echo -e "  Backend : ${GREEN}vLLM (mais rápido)${R}" || \
    echo -e "  Backend : ${CYAN}Ollama${R}"

echo ""
echo "Próximos passos:"
echo -e "  ${CYAN}# Terminal 1 — rodar experimento:${R}"
echo -e "  ${CYAN}./run_experiment.sh${R}"
echo ""
echo -e "  ${CYAN}# Terminal 2 — monitorar:${R}"
echo -e "  ${CYAN}./watch.sh${R}"
echo ""
echo -e "  ${GRAY}# Recarregar PATH (ou abrir novo terminal):${R}"
[[ -n "$SHELL_RC" ]] && echo -e "  ${GRAY}source $SHELL_RC${R}"
echo ""
echo "Log completo: $LOG"
echo ""
