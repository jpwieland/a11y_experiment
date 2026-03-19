#!/usr/bin/env bash
# ==============================================================================
#  fix_env.sh — Corrige todos os problemas detectados pelo diagnose.sh
#
#  Baseado no diagnóstico de 2026-03-18 | Máquina: RTX 4090 (23 GB VRAM)
#
#  Uso:
#    bash fix_env.sh           # corrige tudo
#    bash fix_env.sh --dry-run # mostra o que seria feito sem executar
#    bash fix_env.sh --step 3  # executa apenas o passo N
#    bash fix_env.sh --list    # lista todos os passos disponíveis
#
#  Passos:
#    1  pip bootstrap
#    2  pacotes Python (pip install -e '.[dev]')
#    3  extras científicos (psutil, numpy, scipy)
#    4  vLLM (GPU NVIDIA ≥ 24 GB)
#    5  ferramentas Node.js (@axe-core/cli, eslint atualizado)
#    6  Playwright + Chromium
#    7  .env padrão
#    8  diretórios de trabalho
#    9  modelo Ollama (qwen2.5-coder:7b)
# ==============================================================================
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJECT_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$PYTHON -m pip"
LOG="$PROJECT_DIR/fix_env.log"

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false
ONLY_STEP=""
for i in "$@"; do
    case "$i" in
        --dry-run) DRY_RUN=true ;;
        --step)    shift; ONLY_STEP="${1:-}" ;;
        --list)
            echo "Passos disponíveis:"
            echo "  1  pip bootstrap (venv)"
            echo "  2  pacotes Python (pip install -e .[dev])"
            echo "  3  extras científicos (psutil, numpy, scipy)"
            echo "  4  vLLM para GPU NVIDIA"
            echo "  5  ferramentas Node.js"
            echo "  6  Playwright + Chromium"
            echo "  7  .env (configuração)"
            echo "  8  diretórios de trabalho"
            echo "  9  modelo Ollama qwen2.5-coder:7b"
            exit 0 ;;
    esac
done

# ── Cores ─────────────────────────────────────────────────────────────────────
OK='\033[0;32m'; WARN='\033[1;33m'; FAIL='\033[0;31m'
INFO='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
STEP_COLOR='\033[0;36m'

# ── Helpers ───────────────────────────────────────────────────────────────────
STEP=0
step_skip() { [[ -n "$ONLY_STEP" && "$ONLY_STEP" != "$STEP" ]]; }

header() {
    STEP=$((STEP+1))
    echo -e "\n${BOLD}${STEP_COLOR}━━ [${STEP}] $* ${NC}"
    echo "$(date '+%H:%M:%S') ── [${STEP}] $*" >> "$LOG"
}

ok()   { echo -e "  ${OK}✔${NC}  $*"; }
info() { echo -e "  ${INFO}→${NC}  $*"; }
warn() { echo -e "  ${WARN}⚠${NC}  $*"; }
fail() { echo -e "  ${FAIL}✘${NC}  $*"; }

run() {
    echo -e "  ${DIM}\$  $*${NC}"
    if [ "$DRY_RUN" = "false" ]; then
        "$@" >> "$LOG" 2>&1 || {
            echo -e "  ${FAIL}ERRO no comando acima — veja: $LOG${NC}"
            return 1
        }
    fi
}

run_visible() {
    # Como run(), mas mostra output em tempo real (para downloads longos)
    echo -e "  ${DIM}\$  $*${NC}"
    if [ "$DRY_RUN" = "false" ]; then
        "$@" 2>&1 | tee -a "$LOG" | sed 's/^/    /'
    fi
}

has() { command -v "$1" &>/dev/null; }

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${OK}♿ a11y-autofix — Correção do Ambiente${NC}"
echo -e "${DIM}$(printf '═%.0s' {1..60})${NC}"
echo -e "  Projeto : $PROJECT_DIR"
echo -e "  Data    : $(date '+%Y-%m-%d %H:%M:%S')"
echo -e "  Log     : $LOG"
[ "$DRY_RUN" = "true" ]  && echo -e "  ${WARN}MODO DRY-RUN — nenhum comando será executado${NC}"
[ -n "$ONLY_STEP" ]      && echo -e "  ${INFO}Executando apenas passo: $ONLY_STEP${NC}"

# Limpar log anterior
[ "$DRY_RUN" = "false" ] && echo "=== fix_env.sh $(date) ===" > "$LOG"


# ==============================================================================
# PASSO 1 — pip bootstrap no .venv
# ==============================================================================
header "pip bootstrap no .venv"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

if [ ! -d "$VENV" ]; then
    fail ".venv não existe em $VENV"
    info "Crie com: python3.12 -m venv $VENV"
    exit 1
fi

ok ".venv encontrado em $VENV"

# Verificar se pip está funcional
if "$PYTHON" -m pip --version &>/dev/null 2>&1; then
    PIP_VER=$("$PYTHON" -m pip --version 2>/dev/null | awk '{print $2}')
    ok "pip $PIP_VER já funcional"
else
    info "Bootstrapping pip via ensurepip..."
    run "$PYTHON" -m ensurepip --upgrade
    run "$PYTHON" -m pip install --upgrade pip setuptools wheel
    ok "pip instalado"
fi

# Garantir pip, setuptools e wheel atualizados
info "Atualizando pip / setuptools / wheel..."
run "$PYTHON" -m pip install --upgrade pip setuptools wheel hatchling
ok "pip atualizado"

fi # passo 1

# ==============================================================================
# PASSO 2 — Pacotes Python via pyproject.toml
# ==============================================================================
header "Pacotes Python (pip install -e '.[dev]')"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

if [ ! -f "$PROJECT_DIR/pyproject.toml" ]; then
    fail "pyproject.toml não encontrado em $PROJECT_DIR"
    exit 1
fi

info "Instalando projeto + dependências + extras dev..."
# Forçar reinstalação para garantir que tudo esteja no venv atual
run_visible "$PYTHON" -m pip install -e "$PROJECT_DIR/[dev]"

# Verificar CLI
if [ -x "$VENV/bin/a11y-autofix" ]; then
    ok "CLI a11y-autofix instalada"
else
    warn "CLI não encontrada após instalação — verifique $LOG"
fi

fi # passo 2

# ==============================================================================
# PASSO 3 — Extras científicos
# ==============================================================================
header "Extras científicos (psutil, numpy, scipy)"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

SCIENTIFIC_PKGS=("psutil" "numpy" "scipy")
MISSING_SCI=()

for pkg in "${SCIENTIFIC_PKGS[@]}"; do
    if "$PYTHON" -c "import $pkg" &>/dev/null 2>&1; then
        ver=$("$PYTHON" -c "import importlib.metadata; print(importlib.metadata.version('$pkg'))" 2>/dev/null || echo "?")
        ok "$pkg $ver (já instalado)"
    else
        MISSING_SCI+=("$pkg")
    fi
done

if [ ${#MISSING_SCI[@]} -gt 0 ]; then
    info "Instalando: ${MISSING_SCI[*]}"
    run "$PYTHON" -m pip install "${MISSING_SCI[@]}"
    ok "psutil / numpy / scipy instalados"
else
    ok "Todos os extras científicos já estão instalados"
fi

fi # passo 3

# ==============================================================================
# PASSO 4 — vLLM (GPU NVIDIA — RTX 4090 23 GB VRAM)
# ==============================================================================
header "vLLM — backend de alta performance (NVIDIA GPU)"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

# Verificar se há GPU NVIDIA
if ! has nvidia-smi; then
    warn "nvidia-smi não encontrado — pulando vLLM"
else
    VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{printf "%.0f",$1}' || echo 0)
    VRAM_GB=$(( VRAM_MB / 1024 ))
    info "GPU detectada: ${VRAM_GB} GB VRAM"

    if "$PYTHON" -c "import vllm" &>/dev/null 2>&1; then
        VLLM_VER=$("$PYTHON" -c "import importlib.metadata; print(importlib.metadata.version('vllm'))" 2>/dev/null || echo "?")
        ok "vLLM $VLLM_VER já instalado"
    else
        # Verificar CUDA disponível
        CUDA_VER=$("$PYTHON" -c "import torch; print(torch.version.cuda)" 2>/dev/null || echo "")
        if [ -z "$CUDA_VER" ]; then
            info "Verificando CUDA toolkit..."
            CUDA_VER=$(nvcc --version 2>/dev/null | grep "release" | awk '{print $6}' | tr -d ',' || echo "desconhecido")
        fi
        info "CUDA: $CUDA_VER"
        info "Instalando vLLM (pode demorar alguns minutos)..."
        # vLLM requer torch com CUDA — instalar via pip com índice CUDA
        run_visible "$PYTHON" -m pip install vllm
        if "$PYTHON" -c "import vllm" &>/dev/null 2>&1; then
            ok "vLLM instalado com sucesso"
            echo ""
            info "Para usar vLLM como backend:"
            info "  vllm serve Qwen/Qwen2.5-Coder-7B-Instruct \\"
            info "       --port 8000 --gpu-memory-utilization 0.85 &"
            info "  # Então: LLM_BACKEND=vllm no .env"
        else
            warn "vLLM pode precisar do torch com CUDA. Tente manualmente:"
            warn "  pip install torch --index-url https://download.pytorch.org/whl/cu124"
            warn "  pip install vllm"
        fi
    fi
fi

fi # passo 4

# ==============================================================================
# PASSO 5 — Ferramentas Node.js (@axe-core/cli, eslint 8.x, eslint-plugin-jsx-a11y)
# ==============================================================================
header "Ferramentas Node.js de acessibilidade"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

if ! has node; then
    fail "Node.js não encontrado — instale antes de continuar:"
    info "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
    info "  sudo apt-get install -y nodejs"
    exit 1
fi

NODE_VER=$(node --version | sed 's/v//')
info "Node.js $NODE_VER"

# @axe-core/cli
if has axe; then
    AXE_VER=$(axe --version 2>/dev/null || echo "?")
    ok "@axe-core/cli $AXE_VER já instalado"
else
    info "Instalando @axe-core/cli..."
    run npm install -g @axe-core/cli
    ok "@axe-core/cli instalado"
fi

# eslint — verificar versão (precisa ≥ 8.x)
if has eslint; then
    ESLINT_VER=$(eslint --version 2>/dev/null | sed 's/v//')
    ESLINT_MAJ=$(echo "$ESLINT_VER" | cut -d. -f1)
    if [ "${ESLINT_MAJ:-0}" -ge 8 ]; then
        ok "eslint $ESLINT_VER (compatível)"
    else
        warn "eslint $ESLINT_VER é muito antigo (precisa ≥ 8.x) — atualizando..."
        run npm install -g eslint@8
        ok "eslint atualizado para versão 8"
    fi
else
    info "Instalando eslint@8..."
    run npm install -g eslint@8
    ok "eslint instalado"
fi

# eslint-plugin-jsx-a11y
if npm list -g eslint-plugin-jsx-a11y --depth=0 &>/dev/null 2>&1; then
    ok "eslint-plugin-jsx-a11y já instalado"
else
    info "Instalando eslint-plugin-jsx-a11y..."
    run npm install -g eslint-plugin-jsx-a11y
    ok "eslint-plugin-jsx-a11y instalado"
fi

# Confirmação das ferramentas já existentes
for tool in pa11y lighthouse; do
    if has "$tool"; then
        ver=$($tool --version 2>/dev/null | head -1 || echo "?")
        ok "$tool $ver (já presente)"
    fi
done

fi # passo 5

# ==============================================================================
# PASSO 6 — Playwright + Chromium
# ==============================================================================
header "Playwright + Chromium"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

# Playwright Python já deve estar instalado via passo 2 (está no pyproject.toml)
if "$PYTHON" -c "import playwright" &>/dev/null 2>&1; then
    PW_VER=$("$PYTHON" -c "import importlib.metadata; print(importlib.metadata.version('playwright'))" 2>/dev/null || echo "?")
    ok "playwright $PW_VER (pacote Python presente)"
else
    info "Instalando playwright..."
    run "$PYTHON" -m pip install playwright
fi

# Verificar Chromium
CHROMIUM_FOUND=false
for cache_dir in "$HOME/.cache/ms-playwright" "/root/.cache/ms-playwright"; do
    if ls "$cache_dir"/chromium-* &>/dev/null 2>&1; then
        ok "Chromium já instalado em $cache_dir"
        CHROMIUM_FOUND=true
        break
    fi
done

if [ "$CHROMIUM_FOUND" = "false" ]; then
    info "Instalando Chromium + dependências de sistema..."
    info "(isso pode demorar 2-5 minutos)"
    run_visible "$VENV/bin/playwright" install chromium --with-deps
    ok "Chromium instalado"
fi

fi # passo 6

# ==============================================================================
# PASSO 7 — Arquivo .env
# ==============================================================================
header "Arquivo .env (configuração do projeto)"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

ENV_FILE="$PROJECT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    ok ".env já existe — não será sobrescrito"
    info "Para recriar: rm $ENV_FILE && bash fix_env.sh --step 7"
else
    info "Criando .env com configurações para RTX 4090 (23 GB VRAM)..."

    if [ "$DRY_RUN" = "false" ]; then
        cat > "$ENV_FILE" <<'ENVEOF'
# ─── a11y-autofix — Configuração do Ambiente ──────────────────────────────────
# Gerado por fix_env.sh | Máquina: Linux + NVIDIA RTX 4090 (23 GB VRAM)
# Edite conforme necessário antes de executar os experimentos.

# ── Backend LLM ───────────────────────────────────────────────────────────────
# Opções: ollama | vllm | llamacpp | lm_studio | custom
LLM_BACKEND=ollama
OLLAMA_HOST=http://localhost:11434

# Modelo padrão — com 23 GB VRAM pode usar até 14b sem problemas
DEFAULT_MODEL=qwen2.5-coder:7b
# DEFAULT_MODEL=qwen2.5-coder:14b   # descomente após: ollama pull qwen2.5-coder:14b

# ── Scanners de Acessibilidade ────────────────────────────────────────────────
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false        # pesado; ative apenas quando necessário
USE_PLAYWRIGHT=true
USE_ESLINT=true
MIN_TOOL_CONSENSUS=2        # mínimo de ferramentas para classificar como HIGH

# ── Performance (ajustado para GPU + 1077 GB disco) ───────────────────────────
MAX_CONCURRENT_SCANS=4      # scans paralelos
MAX_CONCURRENT_AGENTS=3     # agentes de correção paralelos
MAX_CONCURRENT_MODELS=3     # modelos simultâneos nos experimentos
SCAN_TIMEOUT=90             # segundos por scan
AGENT_TIMEOUT=300           # segundos por agente de correção

# ── Ollama (configurações de performance) ─────────────────────────────────────
OLLAMA_NUM_PARALLEL=2       # requests paralelos por modelo
OLLAMA_MAX_LOADED_MODELS=2  # modelos em VRAM simultaneamente

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO              # DEBUG | INFO | WARNING | ERROR

# ── Playwright ────────────────────────────────────────────────────────────────
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
# Em servidor headless (sem GPU para display), desabilitar GPU no Chromium:
CHROMIUM_FLAGS=--no-sandbox --disable-dev-shm-usage --disable-gpu

# ── GitHub (opcional — para dataset discovery) ────────────────────────────────
# GITHUB_TOKEN=ghp_xxxxxxxxxxxx

# ── vLLM (ative se usar vLLM como backend) ────────────────────────────────────
# LLM_BACKEND=vllm
# VLLM_HOST=http://localhost:8000
# VLLM_GPU_MEMORY_UTILIZATION=0.85
ENVEOF
    fi

    ok ".env criado em $ENV_FILE"
    info "Revise o arquivo antes de executar os experimentos:"
    info "  nano $ENV_FILE"
fi

fi # passo 7

# ==============================================================================
# PASSO 8 — Diretórios de trabalho
# ==============================================================================
header "Diretórios de trabalho"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

DIRS=(
    "$PROJECT_DIR/dataset/snapshots"
    "$PROJECT_DIR/experiment-results"
    "$PROJECT_DIR/a11y-report"
    "$PROJECT_DIR/logs"
)

for dir in "${DIRS[@]}"; do
    if [ -d "$dir" ]; then
        ok "$dir  (já existe)"
    else
        run mkdir -p "$dir"
        ok "$dir  (criado)"
    fi
done

fi # passo 8

# ==============================================================================
# PASSO 9 — Modelo Ollama (qwen2.5-coder:7b)
# ==============================================================================
header "Modelo Ollama — qwen2.5-coder:7b"
if step_skip; then echo -e "  ${DIM}(pulado)${NC}"; else

if ! has ollama; then
    fail "ollama não encontrado — instale com: curl -fsSL https://ollama.com/install.sh | sh"
else
    # Verificar daemon
    if ! curl -s --max-time 3 "http://localhost:11434/api/version" &>/dev/null 2>&1; then
        warn "Daemon Ollama não está rodando — iniciando em background..."
        if [ "$DRY_RUN" = "false" ]; then
            nohup ollama serve >> "$LOG" 2>&1 &
            sleep 3
        fi
    fi

    # Verificar qwen2.5-coder:7b
    if ollama list 2>/dev/null | grep -q "qwen2.5-coder:7b"; then
        ok "qwen2.5-coder:7b já instalado"
    else
        info "Baixando qwen2.5-coder:7b (~4.7 GB)..."
        info "(isso pode demorar conforme a velocidade da rede)"
        run_visible ollama pull qwen2.5-coder:7b
        ok "qwen2.5-coder:7b pronto"
    fi

    # Nota sobre outros modelos (cabem na VRAM de 23 GB com quantização Q4)
    echo ""
    info "Outros modelos compatíveis com 23 GB VRAM (instalação opcional):"
    EXTRA_MODELS=(
        "qwen2.5-coder:14b   # ~8.5 GB Q4 — melhor qualidade"
        "deepseek-coder-v2:16b # ~9.1 GB Q4 — excelente para código"
        "qwen2.5-coder:32b   # ~19 GB Q4 — máxima qualidade, cabe na 4090"
    )
    for m in "${EXTRA_MODELS[@]}"; do
        echo -e "    ${DIM}ollama pull $m${NC}"
    done
fi

fi # passo 9


# ==============================================================================
# RESUMO FINAL
# ==============================================================================
echo -e "\n${BOLD}${OK}$(printf '═%.0s' {1..60})${NC}"
echo -e "${BOLD}  Correção concluída!${NC}"
echo -e "${BOLD}${OK}$(printf '═%.0s' {1..60})${NC}"
echo ""
echo -e "  Log completo: ${DIM}$LOG${NC}"
echo ""

if [ "$DRY_RUN" = "false" ] && [ -z "$ONLY_STEP" ]; then
    echo -e "  ${INFO}Executando diagnóstico de confirmação...${NC}"
    echo ""
    if [ -x "$PROJECT_DIR/diagnose.sh" ]; then
        bash "$PROJECT_DIR/diagnose.sh"
    else
        info "diagnose.sh não encontrado — execute manualmente para confirmar"
    fi
fi
