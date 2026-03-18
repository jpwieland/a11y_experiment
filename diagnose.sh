#!/usr/bin/env bash
# ==============================================================================
#  diagnose.sh — Diagnóstico completo do ambiente a11y-autofix
#
#  Uso:
#    bash diagnose.sh          # diagnóstico completo
#    bash diagnose.sh --json   # saída em JSON (para CI/automação)
#    bash diagnose.sh --fix    # mostra comandos de correção agrupados no final
#
#  Verifica:
#    1.  Hardware (RAM, disco, GPU)
#    2.  Python e pacotes
#    3.  Virtual environment + CLI
#    4.  Node.js e ferramentas de acessibilidade
#    5.  Playwright + Chromium
#    6.  Backends LLM (Ollama, vLLM, llama.cpp, LM Studio)
#    7.  Modelos disponíveis
#    8.  Variáveis de ambiente (.env)
#    9.  Ferramentas de sistema
#   10.  Resumo e comandos de correção
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Flags ─────────────────────────────────────────────────────────────────────
JSON_MODE=false
SHOW_FIX=false
for arg in "$@"; do
    case "$arg" in
        --json) JSON_MODE=true ;;
        --fix)  SHOW_FIX=true ;;
    esac
done

# ── Cores ─────────────────────────────────────────────────────────────────────
if [ -t 1 ] && [ "$JSON_MODE" = "false" ]; then
    OK='\033[0;32m'; WARN='\033[1;33m'; FAIL='\033[0;31m'
    INFO='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
else
    OK=''; WARN=''; FAIL=''; INFO=''; BOLD=''; DIM=''; NC=''
fi

# ── Contadores e lista de problemas ───────────────────────────────────────────
N_OK=0; N_WARN=0; N_FAIL=0
MISSING_ITEMS=()   # array de strings "TIPO|item|comando de correção"
CURRENT_SECTION=""

# ── Helpers de output ─────────────────────────────────────────────────────────
section() {
    CURRENT_SECTION="$1"
    echo -e "\n${BOLD}${INFO}▶ $1${NC}"
    echo -e "${DIM}$(printf '─%.0s' {1..60})${NC}"
}

ok()   {
    echo -e "  ${OK}✔${NC}  $*"
    N_OK=$((N_OK+1))
}

warn() {
    echo -e "  ${WARN}⚠${NC}  $*"
    N_WARN=$((N_WARN+1))
}

fail() {
    local msg="$1"
    local fix="${2:-}"
    echo -e "  ${FAIL}✘${NC}  $msg"
    N_FAIL=$((N_FAIL+1))
    [ -n "$fix" ] && MISSING_ITEMS+=("$CURRENT_SECTION|$msg|$fix")
}

info() { echo -e "  ${DIM}→  $*${NC}"; }

has() { command -v "$1" &>/dev/null; }

# ── Python helper ─────────────────────────────────────────────────────────────
PYTHON=""
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

find_python() {
    if [ -x "$VENV_PYTHON" ]; then
        PYTHON="$VENV_PYTHON"
    else
        for candidate in python3.12 python3.11 python3.10 python3 python; do
            if has "$candidate"; then
                local ver
                ver=$("$candidate" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null || true)
                local maj min
                maj=$(echo "$ver" | cut -d. -f1)
                min=$(echo "$ver" | cut -d. -f2)
                if [ "${maj:-0}" -ge 3 ] && [ "${min:-0}" -ge 10 ]; then
                    PYTHON="$candidate"; return 0
                fi
            fi
        done
    fi
}

py_has_pkg() {
    [ -n "$PYTHON" ] && "$PYTHON" -c "import $1" 2>/dev/null
}

py_pkg_version() {
    [ -n "$PYTHON" ] && "$PYTHON" -c "import importlib.metadata; print(importlib.metadata.version('$1'))" 2>/dev/null || echo "?"
}

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${OK}♿ a11y-autofix — Diagnóstico Completo do Ambiente${NC}"
echo -e "${DIM}$(printf '═%.0s' {1..60})${NC}"
echo -e "  Projeto : $SCRIPT_DIR"
echo -e "  Data    : $(date '+%Y-%m-%d %H:%M:%S')"
echo -e "  OS      : $(uname -s) $(uname -r | cut -d- -f1)"


# ==============================================================================
# 1. HARDWARE
# ==============================================================================
section "1. Hardware"

# RAM
RAM_GB=0
if has free; then
    RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)
elif [ "$(uname -s)" = "Darwin" ]; then
    RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    RAM_GB=$(( RAM_BYTES / 1024 / 1024 / 1024 ))
fi

if [ "${RAM_GB:-0}" -ge 16 ]; then
    ok "RAM: ${RAM_GB} GB  (≥ 16 GB recomendado para modelos 7B+)"
elif [ "${RAM_GB:-0}" -ge 8 ]; then
    warn "RAM: ${RAM_GB} GB  (mínimo para modelos 7B; recomendado ≥ 16 GB)"
else
    warn "RAM: ${RAM_GB} GB  (insuficiente para modelos locais — use modelos via API)"
fi

# Disco
DISK_FREE_GB=0
if has df; then
    DISK_FREE_GB=$(df -BG "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2{gsub("G",""); print $4}' || echo 0)
fi

if [ "${DISK_FREE_GB:-0}" -ge 50 ]; then
    ok "Disco livre: ${DISK_FREE_GB} GB  (≥ 50 GB para modelos + snapshots)"
elif [ "${DISK_FREE_GB:-0}" -ge 20 ]; then
    warn "Disco livre: ${DISK_FREE_GB} GB  (suficiente para 1-2 modelos; recomendado ≥ 50 GB)"
else
    warn "Disco livre: ${DISK_FREE_GB} GB  (pode ser insuficiente — libere espaço)"
fi

# GPU
GPU_DETECTED=false
GPU_TYPE="none"
GPU_VRAM_GB=0
GPU_INFO=""

if has nvidia-smi; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || true)
    if [ -n "$GPU_INFO" ]; then
        GPU_DETECTED=true
        GPU_TYPE="nvidia"
        VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{printf "%.0f", $1}' || echo 0)
        GPU_VRAM_GB=$(( VRAM_MB / 1024 ))
        CUDA_VER=$(nvidia-smi 2>/dev/null | grep "CUDA Version" | awk '{print $NF}' || echo "?")
        ok "GPU NVIDIA: $GPU_INFO  |  VRAM: ${GPU_VRAM_GB} GB  |  CUDA: $CUDA_VER"
        if [ "$GPU_VRAM_GB" -ge 24 ]; then
            info "VRAM ≥ 24 GB → pode usar vLLM para máxima performance"
        elif [ "$GPU_VRAM_GB" -ge 16 ]; then
            info "VRAM ≥ 16 GB → recomendado qwen2.5-coder:14b ou deepseek-coder-v2:16b"
        elif [ "$GPU_VRAM_GB" -ge 8 ]; then
            info "VRAM ≥ 8 GB → recomendado qwen2.5-coder:7b"
        else
            warn "VRAM < 8 GB → apenas modelos 7B com quantização Q4"
        fi
    fi
elif has rocm-smi || [ -d /opt/rocm ]; then
    GPU_DETECTED=true
    GPU_TYPE="amd"
    GPU_INFO=$(rocm-smi --showproductname 2>/dev/null | grep "GPU" | head -1 | xargs || echo "AMD GPU")
    ok "GPU AMD (ROCm): $GPU_INFO"
elif [ "$(uname -s)" = "Darwin" ] && \
     python3 -c "import platform; exit(0 if 'arm' in platform.machine().lower() else 1)" 2>/dev/null; then
    GPU_DETECTED=true
    GPU_TYPE="apple"
    UNIFIED_GB=$(python3 -c "import subprocess; r=subprocess.run(['sysctl','hw.memsize'],capture_output=True,text=True); print(round(int(r.stdout.split()[-1])/1e9,1))" 2>/dev/null || echo "?")
    ok "Apple Silicon (Metal)  |  Memória unificada: ${UNIFIED_GB} GB"
    GPU_VRAM_GB="${UNIFIED_GB%.*}"
else
    warn "GPU não detectada — modelos LLM rodarão em CPU (lento para modelos ≥ 7B)"
    info "Para NVIDIA: instale CUDA → https://developer.nvidia.com/cuda-downloads"
    info "Para AMD   : instale ROCm → https://rocm.docs.amd.com"
fi


# ==============================================================================
# 2. PYTHON
# ==============================================================================
section "2. Python"

find_python

if [ -z "$PYTHON" ]; then
    fail "Python 3.10+ não encontrado" \
         "# macOS: brew install python@3.12  |  Ubuntu: sudo apt-get install -y python3.12 python3.12-venv"
else
    PY_VER=$("$PYTHON" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}')")
    if [ "$PYTHON" = "$VENV_PYTHON" ]; then
        ok "Python $PY_VER  [$PYTHON]  (venv ativo)"
    else
        ok "Python $PY_VER  [$PYTHON]"
    fi
fi

# pip
if [ -n "$PYTHON" ] && "$PYTHON" -m pip --version &>/dev/null 2>&1; then
    PIP_VER=$("$PYTHON" -m pip --version 2>/dev/null | awk '{print $2}')
    ok "pip $PIP_VER"
else
    fail "pip não disponível" \
         "curl https://bootstrap.pypa.io/get-pip.py | $PYTHON"
fi


# ==============================================================================
# 3. VIRTUAL ENVIRONMENT + CLI
# ==============================================================================
section "3. Virtual Environment e CLI"

VENV_DIR="$SCRIPT_DIR/.venv"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
    VENV_PY_VER=$("$VENV_DIR/bin/python" --version 2>/dev/null | awk '{print $2}')
    ok ".venv existe  ($VENV_DIR)  |  Python $VENV_PY_VER"
    PYTHON="$VENV_DIR/bin/python"
else
    fail ".venv não encontrado" \
         "cd $SCRIPT_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
fi

# CLI entry point
if [ -x "$VENV_DIR/bin/a11y-autofix" ]; then
    ok "CLI a11y-autofix instalada  ($VENV_DIR/bin/a11y-autofix)"
else
    fail "CLI a11y-autofix não instalada" \
         "cd $SCRIPT_DIR && source .venv/bin/activate && pip install -e '.[dev]'"
fi

# Pacotes Python obrigatórios
PYTHON_PKGS=(
    "pydantic:pydantic>=2.7"
    "pydantic_settings:pydantic-settings>=2.3"
    "structlog:structlog>=24.1"
    "typer:typer[all]>=0.12"
    "rich:rich>=13.7"
    "httpx:httpx[http2]>=0.27"
    "aiohttp:aiohttp>=3.9"
    "tenacity:tenacity>=8.3"
    "openai:openai>=1.30"
    "playwright:playwright>=1.44"
    "orjson:orjson>=3.10"
    "deepdiff:deepdiff>=7.0"
    "tabulate:tabulate>=0.9"
    "git:gitpython>=3.1"
    "dotenv:python-dotenv>=1.0"
    "jinja2:jinja2>=3.1"
    "yaml:pyyaml>=6.0"
    "dateutil:python-dateutil>=2.9"
)

PYTHON_PKGS_DEV=(
    "pytest:pytest>=8.2"
    "pytest_asyncio:pytest-asyncio>=0.23"
    "pytest_cov:pytest-cov>=5.0"
    "ruff:ruff>=0.4"
    "mypy:mypy>=1.10"
)

echo ""
info "Pacotes obrigatórios:"
MISSING_PY=()
for entry in "${PYTHON_PKGS[@]}"; do
    import_name="${entry%%:*}"
    pkg_spec="${entry##*:}"
    if py_has_pkg "$import_name"; then
        ver=$(py_pkg_version "${pkg_spec%%[>=<]*}" 2>/dev/null || echo "?")
        ok "  $pkg_spec  (instalado: $ver)"
    else
        fail "  $pkg_spec  não instalado" \
             "pip install '$pkg_spec'"
        MISSING_PY+=("$pkg_spec")
    fi
done

if [ ${#MISSING_PY[@]} -gt 0 ]; then
    info "Instalar todos de uma vez:"
    info "  cd $SCRIPT_DIR && source .venv/bin/activate && pip install -e '.[dev]'"
fi

echo ""
info "Pacotes de desenvolvimento:"
for entry in "${PYTHON_PKGS_DEV[@]}"; do
    import_name="${entry%%:*}"
    pkg_spec="${entry##*:}"
    if py_has_pkg "$import_name"; then
        ver=$(py_pkg_version "${pkg_spec%%[>=<]*}" 2>/dev/null || echo "?")
        ok "  $pkg_spec  (instalado: $ver)"
    else
        warn "  $pkg_spec  não instalado  (necessário para testes/lint)"
    fi
done

# Pacotes opcionais para GPU
echo ""
info "Pacotes opcionais (GPU):"
if py_has_pkg "vllm"; then
    VLLM_VER=$(py_pkg_version "vllm" 2>/dev/null || echo "?")
    ok "  vllm $VLLM_VER  (backend de alta performance)"
else
    if [ "$GPU_TYPE" = "nvidia" ] && [ "${GPU_VRAM_GB:-0}" -ge 24 ]; then
        warn "  vllm não instalado  (recomendado para NVIDIA ≥ 24 GB VRAM)"
        MISSING_ITEMS+=("3. Virtual Environment e CLI|vllm não instalado|pip install vllm  # requer CUDA")
    else
        info "  vllm não instalado  (opcional — necessário apenas para GPU NVIDIA ≥ 24 GB)"
    fi
fi

if py_has_pkg "psutil"; then
    ok "  psutil  (monitoramento de memória)"
else
    warn "  psutil não instalado  (usado para preflight de hardware)"
fi

if py_has_pkg "numpy"; then
    ok "  numpy  (análise estatística)"
else
    warn "  numpy não instalado  (usado para análise de experimentos)"
fi

if py_has_pkg "scipy"; then
    ok "  scipy  (análise estatística avançada)"
else
    warn "  scipy não instalado  (usado para análise de experimentos)"
fi


# ==============================================================================
# 4. NODE.JS E FERRAMENTAS DE ACESSIBILIDADE
# ==============================================================================
section "4. Node.js e Ferramentas de Acessibilidade"

if has node; then
    NODE_VER=$(node --version 2>/dev/null | sed 's/v//')
    NODE_MAJ=$(echo "$NODE_VER" | cut -d. -f1)
    if [ "${NODE_MAJ:-0}" -ge 18 ]; then
        ok "Node.js $NODE_VER  ($(which node))"
    else
        warn "Node.js $NODE_VER  (recomendado ≥ 18 LTS)"
        MISSING_ITEMS+=("4. Node.js|Node.js $NODE_VER muito antigo|curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs")
    fi
else
    fail "Node.js não encontrado" \
         "# Ubuntu/Debian: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
# macOS: brew install node
# Recomendado (sem sudo): curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && nvm install 20"
fi

if has npm; then
    NPM_VER=$(npm --version 2>/dev/null)
    ok "npm $NPM_VER"
else
    fail "npm não encontrado" "sudo apt-get install -y npm  # ou instale junto com Node.js"
fi

# Ferramentas npm globais
echo ""
info "Ferramentas de scan (npm globais):"

check_npm_tool() {
    local bin="$1" pkg="$2" install_cmd="$3"
    if has "$bin"; then
        local ver
        ver=$("$bin" --version 2>/dev/null | head -1 || echo "?")
        ok "  $pkg  ($ver)"
    else
        fail "  $pkg  não instalado" "$install_cmd"
    fi
}

check_npm_tool "pa11y"      "pa11y"              "npm install -g pa11y"
check_npm_tool "axe"        "@axe-core/cli"      "npm install -g @axe-core/cli"
check_npm_tool "lighthouse" "lighthouse"         "npm install -g lighthouse"
check_npm_tool "eslint"     "eslint"             "npm install -g eslint"

# eslint-plugin-jsx-a11y (verificar se instalado como plugin global)
if has eslint; then
    if eslint --print-config /dev/null 2>/dev/null | grep -q "jsx-a11y" 2>/dev/null || \
       npm list -g eslint-plugin-jsx-a11y --depth=0 &>/dev/null 2>&1; then
        ok "  eslint-plugin-jsx-a11y  (instalado)"
    else
        warn "  eslint-plugin-jsx-a11y  não instalado globalmente"
        MISSING_ITEMS+=("4. Node.js|eslint-plugin-jsx-a11y ausente|npm install -g eslint-plugin-jsx-a11y")
    fi
fi


# ==============================================================================
# 5. PLAYWRIGHT + CHROMIUM
# ==============================================================================
section "5. Playwright + Chromium"

if py_has_pkg "playwright"; then
    PW_VER=$(py_pkg_version "playwright" 2>/dev/null || echo "?")
    ok "playwright $PW_VER  (pacote Python)"

    # Verificar se o browser Chromium está instalado
    PW_BROWSERS=""
    if [ -n "$PYTHON" ]; then
        PW_BROWSERS=$("$PYTHON" -c "
from playwright.sync_api import sync_playwright
import os
try:
    with sync_playwright() as p:
        exe = p.chromium.executable_path
        print(exe)
except Exception:
    print('')
" 2>/dev/null || true)
    fi

    if [ -n "$PW_BROWSERS" ] && [ -f "$PW_BROWSERS" ]; then
        ok "Chromium instalado  ($PW_BROWSERS)"
    else
        # Verificar via arquivo no cache
        CACHE_DIRS=(
            "$HOME/.cache/ms-playwright"
            "$HOME/Library/Caches/ms-playwright"
            "/root/.cache/ms-playwright"
        )
        CHROMIUM_FOUND=false
        for dir in "${CACHE_DIRS[@]}"; do
            if [ -d "$dir" ] && ls "$dir"/chromium-* &>/dev/null 2>&1; then
                ok "Chromium instalado  ($dir)"
                CHROMIUM_FOUND=true
                break
            fi
        done
        if [ "$CHROMIUM_FOUND" = "false" ]; then
            fail "Chromium (Playwright) não instalado" \
                 "source $SCRIPT_DIR/.venv/bin/activate && playwright install chromium --with-deps"
        fi
    fi
else
    fail "playwright não instalado" \
         "source $SCRIPT_DIR/.venv/bin/activate && pip install playwright && playwright install chromium --with-deps"
fi


# ==============================================================================
# 6. FERRAMENTAS DE SISTEMA
# ==============================================================================
section "6. Ferramentas de Sistema"

check_sys_tool() {
    local bin="$1" desc="$2" install="$3" required="${4:-true}"
    if has "$bin"; then
        local ver
        ver=$("$bin" --version 2>/dev/null | head -1 | cut -c1-60 || echo "ok")
        ok "$bin  ($ver)"
    else
        if [ "$required" = "true" ]; then
            fail "$bin não encontrado  ($desc)" "$install"
        else
            warn "$bin não encontrado  ($desc — opcional)"
        fi
    fi
}

check_sys_tool "git"  "controle de versão"        "sudo apt-get install -y git" "true"
check_sys_tool "curl" "download de arquivos"      "sudo apt-get install -y curl" "true"
check_sys_tool "jq"   "processamento de JSON"     "sudo apt-get install -y jq" "false"

# docker / docker compose
if has docker; then
    DOCKER_VER=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
    ok "docker $DOCKER_VER"
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_VER=$(docker compose version 2>/dev/null | awk '{print $NF}')
        ok "docker compose $COMPOSE_VER"
    elif has docker-compose; then
        DC_VER=$(docker-compose --version 2>/dev/null | awk '{print $NF}')
        ok "docker-compose $DC_VER (legado)"
    else
        warn "docker compose não disponível  (necessário para modo containerizado)"
    fi
else
    warn "docker não encontrado  (opcional — necessário para modo containerizado)"
    MISSING_ITEMS+=("6. Sistema|docker ausente (opcional)|# Ubuntu: sudo apt-get install -y docker.io docker-compose-v2\n# Ou: curl -fsSL https://get.docker.com | sh")
fi


# ==============================================================================
# 7. BACKENDS LLM
# ==============================================================================
section "7. Backends LLM"

# ── Ollama ────────────────────────────────────────────────────────────────────
if has ollama; then
    OLLAMA_VER=$(ollama --version 2>/dev/null | head -1 || echo "?")
    ok "Ollama instalado  ($OLLAMA_VER)"

    # Verificar daemon
    OLLAMA_PORT="${OLLAMA_PORT:-11434}"
    if curl -s --max-time 3 "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null 2>&1; then
        ok "Servidor Ollama respondendo  (localhost:${OLLAMA_PORT})"
    else
        warn "Ollama instalado mas daemon não está rodando"
        MISSING_ITEMS+=("7. Backends LLM|Ollama daemon parado|ollama serve  # ou: systemctl start ollama")
    fi
else
    fail "Ollama não instalado  (backend recomendado para GPU < 24 GB e CPU)" \
         "curl -fsSL https://ollama.com/install.sh | sh"
fi

# ── vLLM ──────────────────────────────────────────────────────────────────────
echo ""
info "Backend vLLM (alto desempenho para NVIDIA ≥ 24 GB VRAM):"
if py_has_pkg "vllm"; then
    VLLM_VER=$(py_pkg_version "vllm" 2>/dev/null || echo "?")
    ok "  vLLM $VLLM_VER  (instalado)"
    VLLM_PORT="${VLLM_PORT:-8000}"
    if curl -s --max-time 2 "http://localhost:${VLLM_PORT}/v1/models" &>/dev/null 2>&1; then
        ok "  Servidor vLLM respondendo  (localhost:${VLLM_PORT})"
    else
        info "  Servidor vLLM não está rodando"
        info "  Iniciar: vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --port ${VLLM_PORT} --gpu-memory-utilization 0.85 &"
    fi
else
    if [ "$GPU_TYPE" = "nvidia" ] && [ "${GPU_VRAM_GB:-0}" -ge 24 ]; then
        warn "  vLLM não instalado  (recomendado para sua GPU ≥ 24 GB)"
        MISSING_ITEMS+=("7. Backends LLM|vLLM ausente (GPU ≥24GB detectada)|pip install vllm  # requer CUDA toolkit instalado")
    else
        info "  vLLM não instalado  (opcional — útil apenas com NVIDIA ≥ 24 GB VRAM)"
    fi
fi

# ── llama.cpp ─────────────────────────────────────────────────────────────────
echo ""
info "Backend llama.cpp (servidor local leve):"
LLAMACPP_PORT="${LLAMACPP_PORT:-8080}"
if curl -s --max-time 2 "http://localhost:${LLAMACPP_PORT}/v1/models" &>/dev/null 2>&1; then
    ok "  llama.cpp respondendo  (localhost:${LLAMACPP_PORT})"
elif has llama-server || has llama.cpp; then
    ok "  llama.cpp instalado  (daemon não rodando)"
else
    info "  llama.cpp não detectado  (opcional)"
    info "  Instalar: https://github.com/ggerganov/llama.cpp#build"
fi

# ── LM Studio ─────────────────────────────────────────────────────────────────
echo ""
info "Backend LM Studio (desktop, principalmente macOS/Windows):"
LMS_PORT="${LMS_PORT:-1234}"
if curl -s --max-time 2 "http://localhost:${LMS_PORT}/v1/models" &>/dev/null 2>&1; then
    ok "  LM Studio respondendo  (localhost:${LMS_PORT})"
else
    info "  LM Studio não detectado  (opcional — útil para Apple Silicon)"
    if [ "$GPU_TYPE" = "apple" ]; then
        MISSING_ITEMS+=("7. Backends LLM|LM Studio ausente (Apple Silicon)|# Baixe em: https://lmstudio.ai")
    fi
fi


# ==============================================================================
# 8. MODELOS LLM DISPONÍVEIS
# ==============================================================================
section "8. Modelos LLM Disponíveis"

RECOMMENDED_MODELS=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b")

if has ollama && curl -s --max-time 3 "http://localhost:11434/api/version" &>/dev/null 2>&1; then
    INSTALLED_MODELS=$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' | tr '\n' ' ' || true)

    if [ -z "$INSTALLED_MODELS" ]; then
        fail "Nenhum modelo instalado no Ollama" \
             "ollama pull qwen2.5-coder:7b  # modelo mínimo recomendado (~4.7 GB)"
    else
        echo ""
        info "Modelos instalados:"
        while IFS= read -r model; do
            [ -z "$model" ] && continue
            SIZE=$(ollama list 2>/dev/null | awk -v m="$model" '$1==m {print $3, $4}' || echo "?")
            ok "  $model  [$SIZE]"
        done < <(ollama list 2>/dev/null | awk 'NR>1 {print $1}')
    fi

    echo ""
    info "Modelos recomendados para o experimento:"
    for model in "${RECOMMENDED_MODELS[@]}"; do
        SIZE_B=$(echo "$model" | grep -oE '[0-9]+b' | grep -oE '[0-9]+' || echo "7")
        VRAM_NEEDED=$(( SIZE_B * 2 ))

        if echo "$INSTALLED_MODELS" | grep -qF "${model%%:*}"; then
            ok "  $model  ✔ instalado"
        else
            # Verificar se cabe na VRAM
            if [ "$GPU_DETECTED" = "true" ] && [ "$GPU_VRAM_GB" -gt 0 ] && \
               [ "$VRAM_NEEDED" -gt "$GPU_VRAM_GB" ]; then
                warn "  $model  não instalado  (requer ~${VRAM_NEEDED} GB VRAM, disponível: ${GPU_VRAM_GB} GB)"
            else
                warn "  $model  não instalado"
                MISSING_ITEMS+=("8. Modelos LLM|$model ausente|ollama pull $model")
            fi
        fi
    done

    # Sugerir modelos por VRAM
    echo ""
    if [ "$GPU_VRAM_GB" -ge 40 ]; then
        info "Modelos adicionais para sua VRAM (≥40 GB):"
        info "  ollama pull qwen2.5-coder:32b"
        info "  ollama pull codellama:34b-instruct"
    elif [ "$GPU_VRAM_GB" -ge 8 ]; then
        info "Modelo mínimo para rodar agora:"
        info "  ollama pull qwen2.5-coder:7b"
    fi

else
    warn "Ollama não disponível ou daemon parado — não foi possível listar modelos"
    info "Após instalar Ollama: ollama pull qwen2.5-coder:7b"
fi


# ==============================================================================
# 9. ARQUIVO .env
# ==============================================================================
section "9. Configuração (.env)"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [ -f "$ENV_FILE" ]; then
    ok ".env encontrado  ($ENV_FILE)"

    # Verificar variáveis importantes
    check_env_var() {
        local var="$1" default="$2" required="${3:-false}"
        local val
        val=$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
        if [ -n "$val" ]; then
            ok "  $var = $val"
        elif [ -n "$default" ]; then
            warn "  $var não definido  (padrão: $default)"
        elif [ "$required" = "true" ]; then
            fail "  $var não definido  (obrigatório)" "echo '$var=<valor>' >> $ENV_FILE"
        else
            info "  $var não definido  (opcional)"
        fi
    }

    echo ""
    info "Variáveis principais:"
    check_env_var "DEFAULT_MODEL"       "qwen2.5-coder-7b"
    check_env_var "LOG_LEVEL"           "INFO"
    check_env_var "USE_PA11Y"           "true"
    check_env_var "USE_AXE"             "true"
    check_env_var "USE_LIGHTHOUSE"      "false"
    check_env_var "USE_PLAYWRIGHT"      "true"
    check_env_var "MIN_TOOL_CONSENSUS"  "2"

    echo ""
    info "Variáveis de performance:"
    check_env_var "MAX_CONCURRENT_SCANS"   "4"
    check_env_var "MAX_CONCURRENT_AGENTS"  "2"
    check_env_var "MAX_CONCURRENT_MODELS"  "3"
    check_env_var "SCAN_TIMEOUT"           "90"
    check_env_var "AGENT_TIMEOUT"          "300"

    echo ""
    info "Variáveis de backend LLM:"
    check_env_var "LLM_BACKEND"     "ollama"
    check_env_var "OLLAMA_HOST"     "http://localhost:11434"

else
    fail ".env não encontrado" \
         "cp $ENV_EXAMPLE $ENV_FILE  # depois edite conforme necessário\n# Ou execute: bash $SCRIPT_DIR/setup.sh"
fi


# ==============================================================================
# 10. DATASET E DIRETÓRIOS
# ==============================================================================
section "10. Dataset e Diretórios de Trabalho"

check_dir() {
    local path="$1" desc="$2"
    if [ -d "$path" ]; then
        local count
        count=$(ls "$path" 2>/dev/null | wc -l | tr -d ' ')
        ok "  $path  ($count itens)"
    else
        warn "  $path  não existe  ($desc)"
        MISSING_ITEMS+=("10. Dataset|$desc ausente|mkdir -p $path")
    fi
}

check_dir "$SCRIPT_DIR/dataset/catalog"        "catálogo de projetos"
check_dir "$SCRIPT_DIR/dataset/snapshots"      "snapshots dos projetos"
check_dir "$SCRIPT_DIR/dataset/results"        "resultados de scan"
check_dir "$SCRIPT_DIR/experiment-results"     "resultados dos experimentos"
check_dir "$SCRIPT_DIR/experiments"            "arquivos de configuração de experimentos"
check_dir "$SCRIPT_DIR/a11y-report"            "relatórios gerados"

CATALOG="$SCRIPT_DIR/dataset/catalog/projects.yaml"
if [ -f "$CATALOG" ]; then
    if has python3 || [ -n "$PYTHON" ]; then
        PY="${PYTHON:-python3}"
        CATALOG_STATS=$("$PY" -c "
import yaml
try:
    d = yaml.safe_load(open('$CATALOG')) or {}
    ps = d.get('projects', [])
    inc = sum(1 for p in ps if p.get('status') not in ('excluded',))
    sn  = sum(1 for p in ps if p.get('status') == 'snapshotted')
    sc  = sum(1 for p in ps if p.get('status') == 'scanned')
    print(f'total={len(ps)} incluídos={inc} snapshotted={sn} scanned={sc}')
except Exception as e:
    print(f'erro={e}')
" 2>/dev/null || echo "erro ao ler")
        ok "  catalog/projects.yaml  ($CATALOG_STATS)"
    else
        ok "  catalog/projects.yaml  existe"
    fi
else
    warn "  catalog/projects.yaml  não encontrado  (execute collect.sh para popular)"
fi


# ==============================================================================
# RESUMO FINAL
# ==============================================================================
echo -e "\n${BOLD}${INFO}$(printf '═%.0s' {1..60})${NC}"
echo -e "${BOLD}  RESUMO DO DIAGNÓSTICO${NC}"
echo -e "${BOLD}${INFO}$(printf '═%.0s' {1..60})${NC}"
echo ""
echo -e "  ${OK}✔ OK    : $N_OK${NC}"
echo -e "  ${WARN}⚠ Avisos: $N_WARN${NC}"
echo -e "  ${FAIL}✘ Falhas: $N_FAIL${NC}"
echo ""

if [ ${#MISSING_ITEMS[@]} -gt 0 ]; then
    echo -e "${BOLD}${FAIL}  Itens que precisam de ação:${NC}"
    echo ""
    for item in "${MISSING_ITEMS[@]}"; do
        section_name="${item%%|*}"
        rest="${item#*|}"
        desc="${rest%%|*}"
        fix="${rest##*|}"
        echo -e "  ${FAIL}✘${NC} ${BOLD}[$section_name]${NC} $desc"
        while IFS= read -r line; do
            echo -e "    ${DIM}$  $line${NC}"
        done <<< "$fix"
        echo ""
    done
fi

# ── Bloco de instalação agrupado ──────────────────────────────────────────────
if [ "$SHOW_FIX" = "true" ] && [ ${#MISSING_ITEMS[@]} -gt 0 ]; then
    echo -e "${BOLD}${WARN}  Comandos de instalação agrupados (copie e execute):${NC}"
    echo ""
    echo -e "${DIM}  # ── 1. Ambiente Python ──────────────────────────────────${NC}"
    echo -e "  cd $SCRIPT_DIR"
    echo -e "  python3 -m venv .venv"
    echo -e "  source .venv/bin/activate"
    echo -e "  pip install -e '.[dev]'"
    echo ""
    echo -e "${DIM}  # ── 2. Ferramentas Node.js ──────────────────────────────${NC}"
    echo -e "  npm install -g pa11y @axe-core/cli lighthouse eslint eslint-plugin-jsx-a11y"
    echo ""
    echo -e "${DIM}  # ── 3. Playwright + Chromium ─────────────────────────────${NC}"
    echo -e "  playwright install chromium --with-deps"
    echo ""
    echo -e "${DIM}  # ── 4. Ollama (backend LLM) ──────────────────────────────${NC}"
    echo -e "  curl -fsSL https://ollama.com/install.sh | sh"
    echo -e "  ollama serve &"
    echo -e "  ollama pull qwen2.5-coder:7b"
    echo -e "  ollama pull qwen2.5-coder:14b"
    echo ""
    if [ "$GPU_TYPE" = "nvidia" ] && [ "${GPU_VRAM_GB:-0}" -ge 24 ]; then
        echo -e "${DIM}  # ── 5. vLLM (GPU ≥ 24 GB) ───────────────────────────────${NC}"
        echo -e "  pip install vllm"
        echo ""
    fi
    echo -e "${DIM}  # ── 6. Configurar .env ───────────────────────────────────${NC}"
    if [ -f "$ENV_EXAMPLE" ]; then
        echo -e "  cp $ENV_EXAMPLE $ENV_FILE"
    else
        echo -e "  bash $SCRIPT_DIR/setup.sh  # gera .env automaticamente"
    fi
    echo ""
fi

# ── Avaliação geral ───────────────────────────────────────────────────────────
if [ "$N_FAIL" -eq 0 ] && [ "$N_WARN" -eq 0 ]; then
    echo -e "${OK}${BOLD}  ✔ Ambiente completamente configurado!${NC}"
elif [ "$N_FAIL" -eq 0 ]; then
    echo -e "${WARN}${BOLD}  ⚠ Ambiente funcional com avisos — verifique os itens acima${NC}"
else
    echo -e "${FAIL}${BOLD}  ✘ $N_FAIL item(ns) crítico(s) faltando — veja as correções acima${NC}"
    echo ""
    echo -e "  ${DIM}Dica: execute com --fix para ver todos os comandos agrupados:${NC}"
    echo -e "  ${DIM}  bash diagnose.sh --fix${NC}"
fi

echo ""
