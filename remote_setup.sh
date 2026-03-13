#!/usr/bin/env bash
# =============================================================================
#  remote_setup.sh — Bootstrap completo na máquina GPU
#
#  Executado automaticamente pelo transfer_to_gpu.sh (ou manualmente).
#  Configura todo o ambiente necessário para rodar a fase de correção com LLM.
#
#  O que este script faz:
#    1.  Detecta GPU, OS, RAM e recursos disponíveis
#    2.  Instala Python 3.11 (se necessário)
#    3.  Cria .venv e instala dependências Python
#    4.  Instala Node.js 20 LTS (se necessário)
#    5.  Instala ferramentas de scan: pa11y, @axe-core/cli, lighthouse
#    6.  Instala Playwright + Chromium
#    7.  Instala backend LLM: Ollama (recomendado) ou instrui vLLM para GPU grande
#    8.  Gera .env configurado para a máquina
#    9.  Baixa modelos LLM recomendados (Qwen 2.5 Coder + DeepSeek)
#   10.  Executa fix_and_rescan.sh (restaura catalog, clone repos, scan)
#   11.  Valida o ambiente e mostra próximos passos
#
#  Uso (no destino):
#    bash remote_setup.sh
#    bash remote_setup.sh --github-token ghp_xxx
#    bash remote_setup.sh --workers 4 --models small
#    bash remote_setup.sh --skip-models     # pula download de modelos
#    bash remote_setup.sh --skip-scan       # pula clone + scan
#    bash remote_setup.sh --dry-run         # prévia
#
#  Flags:
#    --github-token TOKEN   Token GitHub para discover.py --top-up
#    --workers N            Workers do scan (default: 2)
#    --models GROUP         Grupo de modelos: small|medium|large|recommended (default: recommended)
#    --backend BACKEND      Backend LLM: ollama|vllm|auto (default: auto)
#    --vllm-port PORT       Porta do servidor vLLM (default: 8000)
#    --ollama-port PORT     Porta do servidor Ollama (default: 11434)
#    --skip-models          Pula download de modelos LLM
#    --skip-scan            Pula etapa de clone + scan dos projetos
#    --skip-nodejs          Pula instalação de Node.js e ferramentas npm
#    --dry-run              Mostra o que faria sem executar
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Cores ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

hdr()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"; \
          echo -e "${BOLD}${CYAN}  $*${NC}"; \
          echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"; }
ok()   { echo -e "  ${GREEN}✅ $*${NC}"; }
warn() { echo -e "  ${YELLOW}⚠️  $*${NC}"; }
info() { echo -e "  ${BLUE}ℹ️  $*${NC}"; }
die()  { echo -e "\n${RED}${BOLD}❌ ERRO: $*${NC}" >&2; exit 1; }
run()  { $DRY_RUN && echo "  [DRY-RUN] $*" || eval "$@"; }

_TS_START=$(date +%s)
elapsed() { echo $(( $(date +%s) - _TS_START ))s; }

# ── Defaults ──────────────────────────────────────────────────────────────────
GITHUB_TOKEN=""
WORKERS=2
MODELS_GROUP="recommended"
BACKEND="auto"
VLLM_PORT=8000
OLLAMA_PORT=11434
SKIP_MODELS=false
SKIP_SCAN=false
SKIP_NODEJS=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --github-token) GITHUB_TOKEN="${2:?}"; shift ;;
        --workers)      WORKERS="${2:?}"; shift ;;
        --models)       MODELS_GROUP="${2:?}"; shift ;;
        --backend)      BACKEND="${2:?}"; shift ;;
        --vllm-port)    VLLM_PORT="${2:?}"; shift ;;
        --ollama-port)  OLLAMA_PORT="${2:?}"; shift ;;
        --skip-models)  SKIP_MODELS=true ;;
        --skip-scan)    SKIP_SCAN=true ;;
        --skip-nodejs)  SKIP_NODEJS=true ;;
        --dry-run)      DRY_RUN=true ;;
        --help|-h) sed -n '2,34p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
        *) die "Flag desconhecida: $1" ;;
    esac
    shift
done

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  ♿ a11y-autofix — Setup Máquina GPU                     ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Diretório: $SCRIPT_DIR"
echo -e "  Workers:   $WORKERS  |  Modelos: $MODELS_GROUP  |  Backend: $BACKEND"
$DRY_RUN && echo -e "  ${YELLOW}Modo:      DRY-RUN${NC}"
echo ""

# ── FASE 0: Detecção de hardware ──────────────────────────────────────────────
hdr "FASE 0 — Detecção de hardware e sistema"

# OS
OS_ID=$(cat /etc/os-release 2>/dev/null | grep "^ID=" | cut -d= -f2 | tr -d '"' || echo "unknown")
OS_NAME=$(cat /etc/os-release 2>/dev/null | grep "^PRETTY_NAME" | cut -d= -f2 | tr -d '"' || uname -s)
ARCH=$(uname -m)
ok "Sistema: $OS_NAME ($ARCH)"

# RAM
RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo "?")
[[ "${RAM_GB:-0}" -ge 16 ]] 2>/dev/null && ok "RAM: ${RAM_GB}GB (≥16GB — ok)" \
                                         || warn "RAM: ${RAM_GB}GB (recomendado ≥16GB para modelos 7B+)"

# GPU
GPU_DETECTED=false
GPU_INFO=""
GPU_VRAM_GB=0
if command -v nvidia-smi &>/dev/null 2>&1; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "")
    if [[ -n "$GPU_INFO" ]]; then
        GPU_DETECTED=true
        GPU_VRAM_GB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{printf "%.0f", $1/1024}' || echo "0")
        ok "GPU: $GPU_INFO (VRAM: ~${GPU_VRAM_GB}GB)"

        # Recomendar modelos baseado na VRAM
        if [[ "$GPU_VRAM_GB" -ge 40 ]]; then
            info "VRAM ≥40GB: pode rodar modelos 32B+ (qwen2.5-coder-32b, codellama-34b)"
        elif [[ "$GPU_VRAM_GB" -ge 16 ]]; then
            info "VRAM ≥16GB: recomendado modelos até 16B (deepseek-coder-v2-16b, qwen2.5-coder-14b)"
        elif [[ "$GPU_VRAM_GB" -ge 8 ]]; then
            info "VRAM ≥8GB: recomendado modelos 7B-14B (qwen2.5-coder-7b, qwen2.5-coder-14b)"
        else
            warn "VRAM <8GB: apenas modelos 7B ou menores com quantização Q4"
        fi
    fi
fi
$GPU_DETECTED || warn "GPU NVIDIA não detectada — modelos rodarão em CPU (lento para modelos >7B)"

# Decidir backend automaticamente
if [[ "$BACKEND" == "auto" ]]; then
    if $GPU_DETECTED && [[ "$GPU_VRAM_GB" -ge 24 ]]; then
        BACKEND="vllm"
        info "Backend selecionado: vLLM (GPU ≥24GB detectada — máxima performance)"
    else
        BACKEND="ollama"
        info "Backend selecionado: Ollama (recomendado para GPU <24GB e CPU)"
    fi
fi

# Disco
DISK_FREE_GB=$(df -BG "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2{gsub("G",""); print $4}' || echo "?")
if [[ "${DISK_FREE_GB:-0}" -ge 50 ]] 2>/dev/null; then
    ok "Disco livre: ${DISK_FREE_GB}GB"
else
    warn "Disco livre: ${DISK_FREE_GB}GB (recomendado ≥50GB para modelos + snapshots)"
fi

# ── FASE 1: Python ────────────────────────────────────────────────────────────
hdr "FASE 1 — Python 3.11+"

PYTHON=""
for py in python3.11 python3.12 python3.10 python3; do
    if command -v "$py" &>/dev/null 2>&1; then
        if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$py"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.10+ não encontrado — instalando Python 3.11..."
    case "$OS_ID" in
        ubuntu|debian)
            run "sudo apt-get update -qq && sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip" ;;
        rhel|centos|fedora|rocky|almalinux)
            run "sudo dnf install -y python3.11 python3.11-devel python3-pip" ;;
        arch)
            run "sudo pacman -Sy --noconfirm python python-pip" ;;
        *)
            die "OS não suportado para instalação automática de Python. Instale Python 3.11+ manualmente." ;;
    esac
    PYTHON=$(command -v python3.11 || command -v python3 || die "Python 3.11 não encontrado após instalação")
fi

ok "Python: $($PYTHON --version)"

# Criar virtualenv
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    run "$PYTHON -m venv '$VENV_DIR'"
    ok "Virtualenv criado: $VENV_DIR"
else
    ok "Virtualenv existente: $VENV_DIR"
fi

# Ativar venv
if ! $DRY_RUN; then
    source "$VENV_DIR/bin/activate" 2>/dev/null || true
    PYTHON="$VENV_DIR/bin/python"
fi

# Instalar dependências Python
info "Instalando dependências Python (pyproject.toml)..."
run "$PYTHON -m pip install --upgrade pip -q"
run "$PYTHON -m pip install -e '$SCRIPT_DIR[dev]' -q 2>&1 | tail -5" \
    || run "$PYTHON -m pip install -e '$SCRIPT_DIR' -q 2>&1 | tail -5"
ok "Dependências Python instaladas"

# ── FASE 2: Node.js e ferramentas de scan ────────────────────────────────────
if ! $SKIP_NODEJS; then

hdr "FASE 2 — Node.js 20 LTS + ferramentas de scan"

# Node.js
NODE_OK=false
if command -v node &>/dev/null 2>&1; then
    NODE_VER=$(node --version 2>/dev/null | sed 's/v//' | cut -d. -f1)
    if [[ "${NODE_VER:-0}" -ge 18 ]]; then
        NODE_OK=true
        ok "Node.js: $(node --version)"
    fi
fi

if ! $NODE_OK; then
    warn "Node.js 18+ não encontrado — instalando via NodeSource..."
    case "$OS_ID" in
        ubuntu|debian)
            run "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs" ;;
        rhel|centos|fedora|rocky|almalinux)
            run "curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo dnf install -y nodejs" ;;
        arch)
            run "sudo pacman -Sy --noconfirm nodejs npm" ;;
        *)
            die "Instale Node.js 20 LTS manualmente: https://nodejs.org/en/download"  ;;
    esac
    ok "Node.js instalado: $(node --version 2>/dev/null)"
fi

ok "npm: $(npm --version 2>/dev/null)"

# Ferramentas de scan (globais)
info "Instalando pa11y, @axe-core/cli, lighthouse..."
run "npm install -g pa11y @axe-core/cli lighthouse 2>&1 | tail -3"

# Playwright + Chromium
info "Instalando Playwright e Chromium..."
run "$PYTHON -m playwright install chromium --with-deps 2>&1 | tail -5"

ok "Ferramentas de scan instaladas"

fi  # fim skip_nodejs

# ── FASE 3: Backend LLM ───────────────────────────────────────────────────────
hdr "FASE 3 — Backend LLM: $BACKEND"

case "$BACKEND" in

    # ── Ollama ──────────────────────────────────────────────────────────────
    ollama)
        if ! command -v ollama &>/dev/null 2>&1; then
            info "Instalando Ollama..."
            run "curl -fsSL https://ollama.com/install.sh | sh"
        else
            ok "Ollama já instalado: $(ollama --version 2>/dev/null || echo 'version unknown')"
        fi

        # Iniciar servidor Ollama (se não estiver rodando)
        if ! $DRY_RUN; then
            if ! curl -s "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null; then
                info "Iniciando servidor Ollama na porta ${OLLAMA_PORT}..."
                OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" nohup ollama serve \
                    > "$SCRIPT_DIR/ollama.log" 2>&1 &
                echo $! > "$SCRIPT_DIR/ollama.pid"
                sleep 3
                if curl -s "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null; then
                    ok "Servidor Ollama iniciado (PID $(cat "$SCRIPT_DIR/ollama.pid"))"
                else
                    warn "Ollama pode não ter iniciado — verifique: $SCRIPT_DIR/ollama.log"
                fi
            else
                ok "Servidor Ollama já está rodando na porta ${OLLAMA_PORT}"
            fi
        else
            echo "  [DRY-RUN] OLLAMA_HOST=0.0.0.0:${OLLAMA_PORT} ollama serve &"
        fi
        ;;

    # ── vLLM ────────────────────────────────────────────────────────────────
    vllm)
        if ! $DRY_RUN; then
            if ! "$PYTHON" -c "import vllm" 2>/dev/null; then
                info "Instalando vLLM (requer CUDA)..."
                run "$PYTHON -m pip install vllm -q 2>&1 | tail -5"
                ok "vLLM instalado"
            else
                ok "vLLM já instalado"
            fi
        else
            echo "  [DRY-RUN] pip install vllm"
        fi

        info "Para iniciar o servidor vLLM, use:"
        echo ""
        echo -e "  ${BOLD}# Modelo 7B (recomendado para GPU <16GB):${NC}"
        echo -e "  vllm serve Qwen/Qwen2.5-Coder-7B-Instruct \\"
        echo -e "    --port ${VLLM_PORT} --gpu-memory-utilization 0.85 &"
        echo ""
        echo -e "  ${BOLD}# Modelo 14B (GPU ≥16GB):${NC}"
        echo -e "  vllm serve Qwen/Qwen2.5-Coder-14B-Instruct \\"
        echo -e "    --port ${VLLM_PORT} --gpu-memory-utilization 0.85 &"
        echo ""
        echo -e "  ${BOLD}# Modelo 32B (GPU ≥40GB):${NC}"
        echo -e "  vllm serve Qwen/Qwen2.5-Coder-32B-Instruct \\"
        echo -e "    --port ${VLLM_PORT} --tensor-parallel-size 2 --gpu-memory-utilization 0.85 &"
        echo ""
        warn "vLLM precisa ser iniciado manualmente — o setup não inicia automaticamente"
        ;;
esac

# ── FASE 4: Configuração .env ─────────────────────────────────────────────────
hdr "FASE 4 — Gerando .env para máquina GPU"

ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    info ".env existente encontrado — criando backup"
    ! $DRY_RUN && cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
fi

if ! $DRY_RUN; then
    case "$BACKEND" in
        ollama)
            LLM_BASE_URL="http://localhost:${OLLAMA_PORT}/v1"
            OLLAMA_BASE="http://localhost:${OLLAMA_PORT}" ;;
        vllm)
            LLM_BASE_URL="http://localhost:${VLLM_PORT}/v1"
            OLLAMA_BASE="http://localhost:${OLLAMA_PORT}" ;;
    esac

    cat > "$ENV_FILE" << EOF
# .env gerado por remote_setup.sh em $(date '+%Y-%m-%d %H:%M:%S')
# Máquina: $(hostname)  |  GPU: ${GPU_INFO:-"não detectada"}  |  Backend: ${BACKEND}

# ── Backend LLM ───────────────────────────────────────────────────────────────
LLM_BACKEND=${BACKEND}
LLM_BASE_URL=${LLM_BASE_URL}

# Ollama (se usar como backend)
OLLAMA_HOST=${OLLAMA_BASE}

# vLLM (se usar como backend)
VLLM_BASE_URL=http://localhost:${VLLM_PORT}/v1

# ── Configurações do scan ─────────────────────────────────────────────────────
SCAN_WORKERS=${WORKERS}
SCAN_TIMEOUT=120
MIN_CONSENSUS=2

# ── Dataset ───────────────────────────────────────────────────────────────────
DATASET_ROOT=${SCRIPT_DIR}/dataset
SNAPSHOTS_DIR=${SCRIPT_DIR}/dataset/snapshots
RESULTS_DIR=${SCRIPT_DIR}/dataset/results
CATALOG_FILE=${SCRIPT_DIR}/dataset/catalog/projects.yaml

# ── Experimentos ──────────────────────────────────────────────────────────────
EXPERIMENT_OUTPUT=${SCRIPT_DIR}/experiment-results
MAX_RETRIES=3
LOG_LEVEL=INFO
EOF
    ok ".env gerado: $ENV_FILE"
else
    echo "  [DRY-RUN] cat > $ENV_FILE << EOF ..."
fi

# ── FASE 5: Download de modelos LLM ──────────────────────────────────────────
if ! $SKIP_MODELS && [[ "$BACKEND" == "ollama" ]]; then

hdr "FASE 5 — Download de modelos LLM (grupo: $MODELS_GROUP)"

# Mapear grupos para modelos
case "$MODELS_GROUP" in
    small)       MODEL_LIST=("qwen2.5-coder:7b" "codellama:7b-instruct") ;;
    medium)      MODEL_LIST=("qwen2.5-coder:14b" "deepseek-coder-v2:16b") ;;
    large)       MODEL_LIST=("qwen2.5-coder:32b") ;;
    recommended) MODEL_LIST=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b") ;;
    all)         MODEL_LIST=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b"
                             "codellama:7b-instruct" "llama3.1:8b-instruct-q4_K_M") ;;
    *) warn "Grupo desconhecido: $MODELS_GROUP — usando 'recommended'";
       MODEL_LIST=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b") ;;
esac

# Filtrar modelos por VRAM disponível
FILTERED_LIST=()
for model in "${MODEL_LIST[@]}"; do
    SIZE_B=$(echo "$model" | grep -oE '[0-9]+b' | grep -oE '[0-9]+' || echo "7")
    VRAM_NEEDED=$(( SIZE_B * 2 ))   # heurística: 2GB VRAM por bilhão de parâmetros (Q4)
    if $GPU_DETECTED && [[ "$GPU_VRAM_GB" -gt 0 ]] && [[ "$VRAM_NEEDED" -gt "$GPU_VRAM_GB" ]]; then
        warn "Pulando $model (estimado ~${VRAM_NEEDED}GB VRAM > ${GPU_VRAM_GB}GB disponível)"
    else
        FILTERED_LIST+=("$model")
    fi
done

if [[ ${#FILTERED_LIST[@]} -eq 0 ]]; then
    warn "Nenhum modelo é compatível com a VRAM disponível (${GPU_VRAM_GB}GB)"
    warn "Tente --models small ou instale mais VRAM"
else
    info "Modelos a baixar: ${FILTERED_LIST[*]}"
    for model in "${FILTERED_LIST[@]}"; do
        info "Baixando $model..."
        if ! $DRY_RUN; then
            if ollama pull "$model" 2>&1 | tail -3; then
                ok "$model pronto"
            else
                warn "$model falhou no download — verifique conexão"
            fi
        else
            echo "  [DRY-RUN] ollama pull $model"
        fi
    done

    # Verificar modelos instalados
    if ! $DRY_RUN; then
        INSTALLED=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' ' ')
        ok "Modelos instalados: $INSTALLED"
    fi
fi

elif $SKIP_MODELS; then
    hdr "FASE 5 — Download de modelos (pulado)"
    info "Use: ollama pull qwen2.5-coder:7b"

elif [[ "$BACKEND" == "vllm" ]]; then
    hdr "FASE 5 — Modelos vLLM"
    info "Os modelos vLLM são baixados automaticamente ao iniciar o servidor."
    info "Para baixar antecipadamente via HuggingFace Hub:"
    echo ""
    echo -e "  ${BOLD}# Instalar huggingface_hub:${NC}"
    echo -e "  pip install huggingface-hub"
    echo ""
    echo -e "  ${BOLD}# Baixar modelo:${NC}"
    echo -e "  huggingface-cli download Qwen/Qwen2.5-Coder-7B-Instruct"
    echo -e "  huggingface-cli download Qwen/Qwen2.5-Coder-14B-Instruct"
    echo ""
    echo -e "  ${BOLD}# Iniciar servidor:${NC}"
    echo -e "  vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --port ${VLLM_PORT} --gpu-memory-utilization 0.85"
fi

# ── FASE 6: Fix, rescan e clone de projetos ───────────────────────────────────
if ! $SKIP_SCAN; then

hdr "FASE 6 — Restaurar catálogo, clonar projetos e escanear"

FIX_CMD="bash '$SCRIPT_DIR/fix_and_rescan.sh' --with-snapshot --workers $WORKERS"
[[ -n "$GITHUB_TOKEN" ]] && FIX_CMD+=" --github-token '$GITHUB_TOKEN'"

info "Executando fix_and_rescan.sh --with-snapshot (pode demorar horas para clonar todos os repos)..."
info "Acompanhe em tempo real: python dataset/scripts/watch_scan.py"

if ! $DRY_RUN; then
    eval "$FIX_CMD" \
        && ok "fix_and_rescan.sh concluído" \
        || warn "fix_and_rescan.sh terminou com avisos — verifique os logs"
else
    echo "  [DRY-RUN] $FIX_CMD"
fi

else
    hdr "FASE 6 — Clone + scan (pulado)"
    info "Para executar manualmente:"
    echo -e "  bash fix_and_rescan.sh --with-snapshot --workers $WORKERS"
fi

# ── FASE 7: Validação final ───────────────────────────────────────────────────
hdr "FASE 7 — Validação do ambiente"

VALIDATION_ERRORS=0

# Python
if ! $DRY_RUN; then
    $PYTHON -c "import a11y_autofix" 2>/dev/null \
        && ok "a11y_autofix importável" \
        || { warn "a11y_autofix não importável — verifique a instalação"; (( VALIDATION_ERRORS++ )); }

    # CLI entry point
    if "$VENV_DIR/bin/a11y-autofix" --help &>/dev/null 2>&1; then
        ok "CLI a11y-autofix funcionando"
    else
        warn "CLI a11y-autofix não encontrado — verifique: pip install -e ."
        (( VALIDATION_ERRORS++ ))
    fi

    # Node.js tools
    command -v pa11y &>/dev/null      && ok "pa11y"      || { warn "pa11y não encontrado";      (( VALIDATION_ERRORS++ )); }
    command -v axe &>/dev/null        && ok "axe-core"   || { warn "@axe-core/cli não encontrado"; (( VALIDATION_ERRORS++ )); }
    command -v lighthouse &>/dev/null && ok "lighthouse" || warn "lighthouse não encontrado (opcional)"

    # Backend
    case "$BACKEND" in
        ollama)
            curl -s "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null \
                && ok "Servidor Ollama respondendo na porta ${OLLAMA_PORT}" \
                || warn "Ollama não está respondendo — inicie: ollama serve"
            ;;
        vllm)
            curl -s "http://localhost:${VLLM_PORT}/v1/models" &>/dev/null \
                && ok "Servidor vLLM respondendo na porta ${VLLM_PORT}" \
                || warn "vLLM não está respondendo — inicie o servidor manualmente"
            ;;
    esac

    # Catálogo
    $PYTHON -c "
import yaml
from pathlib import Path
f = Path('${SCRIPT_DIR}/dataset/catalog/projects.yaml')
if f.exists():
    d = yaml.safe_load(f) or {}
    ps = d.get('projects', [])
    inc = sum(1 for p in ps if p.get('status') not in ('excluded',))
    sn  = sum(1 for p in ps if p.get('status') == 'snapshotted')
    sc  = sum(1 for p in ps if p.get('status') == 'scanned')
    print(f'catalog=ok n_total={len(ps)} n_included={inc} n_snapshotted={sn} n_scanned={sc}')
else:
    print('catalog=MISSING')
" 2>/dev/null | while IFS=' ' read -r fields; do
        CATALOG_OK=$(echo "$fields" | grep -oE 'catalog=[^ ]+' | cut -d= -f2)
        if [[ "$CATALOG_OK" == "ok" ]]; then
            N_TOTAL=$(echo "$fields" | grep -oE 'n_total=[^ ]+' | cut -d= -f2)
            N_INC=$(echo "$fields" | grep -oE 'n_included=[^ ]+' | cut -d= -f2)
            N_SC=$(echo "$fields" | grep -oE 'n_scanned=[^ ]+' | cut -d= -f2)
            ok "Catálogo: $N_TOTAL projetos ($N_INC incluídos, $N_SC escaneados)"
        else
            warn "Catálogo não encontrado"
        fi
    done
fi

# ── RESUMO FINAL ──────────────────────────────────────────────────────────────
hdr "SETUP CONCLUÍDO"
echo ""
echo -e "  ${BOLD}Ambiente:${NC}     $(hostname) | $OS_NAME | ${GPU_INFO:-CPU-only}"
echo -e "  ${BOLD}Backend LLM:${NC}  $BACKEND (porta: $( [[ "$BACKEND" == "ollama" ]] && echo $OLLAMA_PORT || echo $VLLM_PORT))"
echo -e "  ${BOLD}Virtualenv:${NC}   $VENV_DIR"
echo -e "  ${BOLD}Tempo total:${NC}  $(elapsed)"
echo ""

if [[ "$VALIDATION_ERRORS" -gt 0 ]]; then
    warn "$VALIDATION_ERRORS problema(s) encontrado(s) na validação — revise os avisos acima"
fi

echo -e "  ${BOLD}Próximos passos:${NC}"
echo ""
echo -e "  ${CYAN}1. Ativar o ambiente:${NC}"
echo -e "     source $VENV_DIR/bin/activate"
echo ""
echo -e "  ${CYAN}2. Ver status do corpus:${NC}"
echo -e "     python dataset/scripts/scan.py --status --pending"
echo ""
echo -e "  ${CYAN}3. Monitorar scan em andamento (terminal separado):${NC}"
echo -e "     python dataset/scripts/watch_scan.py"
echo ""
echo -e "  ${CYAN}4. Rodar experimento de correção LLM:${NC}"
echo -e "     a11y-autofix experiment experiments/qwen_vs_deepseek.yaml"
echo ""
echo -e "  ${CYAN}5. Corrigir um projeto específico:${NC}"
echo -e "     a11y-autofix fix dataset/snapshots/<projeto>/src \\"
echo -e "       --model qwen2.5-coder-7b --agent auto"
echo ""
echo -e "  ${CYAN}6. Validar qualidade do dataset:${NC}"
echo -e "     python dataset/scripts/validate.py --catalog dataset/catalog/projects.yaml"
echo ""

if [[ "$BACKEND" == "vllm" ]]; then
    echo -e "  ${YELLOW}⚠️  vLLM precisa ser iniciado antes dos experimentos:${NC}"
    echo -e "     vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --port ${VLLM_PORT} --gpu-memory-utilization 0.85 &"
    echo ""
fi

if [[ "$BACKEND" == "ollama" ]]; then
    echo -e "  ${CYAN}7. Testar conexão com o modelo:${NC}"
    echo -e "     a11y-autofix models test qwen2.5-coder-7b"
    echo ""
fi

$DRY_RUN && echo -e "  ${YELLOW}⚠️  Modo DRY-RUN — nenhuma ação foi executada${NC}\n"
