#!/usr/bin/env bash
# =============================================================================
#  server_setup.sh — Setup COMPLETO sem root para máquinas GPU (sem conda, sem tmux)
#
#  Testado para: Debian/Ubuntu (sem python3-venv instalado, sem conda, sem sudo)
#
#  O que este script instala — TUDO sem root:
#    1.  Python venv via virtualenv.pyz (bypassa python3-venv do sistema)
#    2.  Dependências Python do projeto (pydantic, httpx, playwright, etc.)
#    3.  Node.js 20 LTS via nvm (sem root, ~/.nvm)
#    4.  Ferramentas de scan: pa11y, @axe-core/cli (via npm local do nvm)
#    5.  Playwright Chromium (sem --with-deps; avisa libs faltantes)
#    6.  Ollama binary (download direto do GitHub, sem sudo, em ~/.local/bin)
#    7.  Modelos LLM via ollama pull
#    8.  Arquivo .env configurado para a máquina
#
#  Itens que PODEM precisar de root (veja seção AUDIT mais abaixo):
#    - Playwright: libs de sistema (libatk, libnss3, etc.) — pode já estar instalado
#    - python3-pip (se nem pip nem virtualenv.pyz funcionarem — raro)
#
#  Uso:
#    bash server_setup.sh                     # setup completo
#    bash server_setup.sh --skip-node         # pula Node.js/scan (só Python + Ollama)
#    bash server_setup.sh --skip-models       # pula download de modelos
#    bash server_setup.sh --skip-playwright   # pula Playwright (se scan já feito)
#    bash server_setup.sh --models medium     # qwen2.5-coder:14b + deepseek-coder-v2:16b
#    bash server_setup.sh --dry-run           # mostra o que faria
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Cores ─────────────────────────────────────────────────────────────────────
R=$'\033[0m'; B=$'\033[1m'; DIM=$'\033[2m'
G=$'\033[92m'; Y=$'\033[93m'; C=$'\033[96m'; RED=$'\033[91m'

ok()   { echo "  ${G}✔${R}  $*"; }
warn() { echo "  ${Y}⚠${R}  $*"; }
info() { echo "  ${C}→${R}  $*"; }
die()  { echo "${RED}${B}✘  ERRO: $*${R}" >&2; exit 1; }
hdr()  { echo ""; echo "${B}${C}══ $* ══${R}"; echo ""; }

# ── Flags ─────────────────────────────────────────────────────────────────────
SKIP_NODE=false
SKIP_MODELS=false
SKIP_PLAYWRIGHT=false
MODELS_GROUP="recommended"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-node)       SKIP_NODE=true ;;
        --skip-models)     SKIP_MODELS=true ;;
        --skip-playwright) SKIP_PLAYWRIGHT=true ;;
        --models)          MODELS_GROUP="${2:?}"; shift ;;
        --dry-run)         DRY_RUN=true ;;
        --help|-h)
            sed -n '4,20p' "$0" | sed 's/^#  \{0,2\}//'
            exit 0 ;;
        *) die "Flag desconhecida: $1" ;;
    esac
    shift
done

run() { $DRY_RUN && echo "  [DRY-RUN] $*" || eval "$@"; }

# ── Helper: download ──────────────────────────────────────────────────────────
_dl() {
    local url="$1" dest="$2"
    if command -v curl &>/dev/null; then
        curl -fsSL --retry 3 "$url" -o "$dest"
    elif command -v wget &>/dev/null; then
        wget -q --tries=3 "$url" -O "$dest"
    else
        die "curl e wget ausentes — impossível baixar arquivos"
    fi
    [[ -s "$dest" ]] || die "Download falhou ou arquivo vazio: $url"
}

echo ""
echo "${B}${C}╔══════════════════════════════════════════════════════════╗${R}"
echo "${B}${C}║  ♿ a11y-autofix — Server Setup (sem root)               ║${R}"
echo "${B}${C}╚══════════════════════════════════════════════════════════╝${R}"
echo ""
echo "  Diretório:  $SCRIPT_DIR"
echo "  Modelos:    $MODELS_GROUP"
$DRY_RUN && echo "  ${Y}Modo: DRY-RUN (nada será executado)${R}"
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# FASE 1 — Python: encontrar base e criar venv sem python3-venv do sistema
# ═════════════════════════════════════════════════════════════════════════════
hdr "FASE 1 — Ambiente Python (sem python3-venv)"

# Localizar Python ≥3.10
PYTHON_BASE=""
for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null; then
        ver=$("$py" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo "False")
        if [[ "$ver" == "True" ]]; then
            PYTHON_BASE="$(command -v "$py")"
            break
        fi
    fi
done
[[ -n "$PYTHON_BASE" ]] || die "Python ≥3.10 não encontrado no PATH"
ok "Python base: $PYTHON_BASE ($("$PYTHON_BASE" --version 2>&1))"

VENV_DIR="${SCRIPT_DIR}/.venv"

if [[ -x "${VENV_DIR}/bin/a11y-autofix" ]] && ! $DRY_RUN; then
    ok "Venv já configurado: ${VENV_DIR}"
else
    echo ""
    info "Criando venv via virtualenv.pyz (não requer python3-venv do sistema)..."

    VENV_PYZ="$(mktemp /tmp/virtualenv-XXXX.pyz)"
    run "_dl 'https://bootstrap.pypa.io/virtualenv.pyz' '$VENV_PYZ'"

    if ! $DRY_RUN; then
        "$PYTHON_BASE" "$VENV_PYZ" -q "${VENV_DIR}" \
            || die "virtualenv.pyz falhou — tente pedir ao admin: sudo apt-get install python3-venv"
        rm -f "$VENV_PYZ"
        ok "Venv criado: ${VENV_DIR}"
    fi

    info "Instalando dependências Python..."
    run "${VENV_DIR}/bin/pip install --quiet --upgrade pip"
    run "${VENV_DIR}/bin/pip install --quiet -e '${SCRIPT_DIR}'"
    ok "Pacotes Python instalados"
fi

PYTHON="${VENV_DIR}/bin/python"

# ═════════════════════════════════════════════════════════════════════════════
# FASE 2 — Node.js via nvm (sem root, instala em ~/.nvm)
# ═════════════════════════════════════════════════════════════════════════════
if ! $SKIP_NODE; then

hdr "FASE 2 — Node.js 20 LTS via nvm (sem root)"

NVM_DIR="${HOME}/.nvm"
NVM_SCRIPT="${NVM_DIR}/nvm.sh"

# Verificar se Node.js 18+ já existe
NODE_OK=false
if command -v node &>/dev/null; then
    NVER=$(node -e "process.stdout.write(process.version.slice(1).split('.')[0])" 2>/dev/null || echo "0")
    [[ "${NVER:-0}" -ge 18 ]] && NODE_OK=true
fi

if $NODE_OK; then
    ok "Node.js já disponível: $(node --version)"
else
    if [[ ! -f "$NVM_SCRIPT" ]]; then
        info "Instalando nvm (Node Version Manager)..."
        NVM_INSTALL="$(mktemp /tmp/nvm-install-XXXX.sh)"
        run "_dl 'https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh' '$NVM_INSTALL'"
        if ! $DRY_RUN; then
            bash "$NVM_INSTALL" >/dev/null 2>&1
            rm -f "$NVM_INSTALL"
        fi
    fi

    # Carregar nvm
    if ! $DRY_RUN && [[ -f "$NVM_SCRIPT" ]]; then
        export NVM_DIR
        # shellcheck source=/dev/null
        source "$NVM_SCRIPT"
        nvm install 20 --silent 2>&1 | tail -3
        nvm use 20 --silent
        nvm alias default 20 --silent >/dev/null
        ok "Node.js instalado via nvm: $(node --version)"
    else
        echo "  [DRY-RUN] source ~/.nvm/nvm.sh && nvm install 20 && nvm use 20"
    fi
fi

# Garantir que npm do nvm está no PATH (se nvm foi usado)
if [[ -f "$NVM_SCRIPT" ]] && ! $DRY_RUN; then
    export NVM_DIR
    source "$NVM_SCRIPT" 2>/dev/null || true
    nvm use default --silent 2>/dev/null || true
fi

ok "npm: $(npm --version 2>/dev/null || echo 'n/a')"

# Instalar ferramentas de scan via npm local do nvm (sem -g root)
# Com nvm, npm -g vai para ~/.nvm/versions/node/<ver>/lib/node_modules — sem root
info "Instalando pa11y, @axe-core/cli..."
run "npm install -g --quiet pa11y @axe-core/cli 2>&1 | tail -3"
ok "pa11y e axe-core instalados"

# Playwright Chromium (sem --with-deps)
if ! $SKIP_PLAYWRIGHT; then
    info "Instalando Playwright Chromium (sem --with-deps)..."
    info "${DIM}Se falhar na execução com erro de libs: veja AVISO no final${R}"
    run "${PYTHON} -m playwright install chromium 2>&1 | tail -5"
    ok "Playwright Chromium instalado"
fi

fi  # fim skip_node

# ═════════════════════════════════════════════════════════════════════════════
# FASE 3 — Ollama: download binário direto (sem root)
# ═════════════════════════════════════════════════════════════════════════════
hdr "FASE 3 — Ollama (binário, sem root)"

LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"
OLLAMA_BIN="${LOCAL_BIN}/ollama"

# Detectar GPU
GPU_VRAM_GB=0
GPU_INFO=""
if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "")
    if [[ -n "$GPU_INFO" ]]; then
        GPU_VRAM_GB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
                     | head -1 | awk '{printf "%.0f", $1/1024}' || echo "0")
        ok "GPU: $GPU_INFO  (~${GPU_VRAM_GB}GB VRAM)"
    fi
fi

if command -v ollama &>/dev/null || [[ -x "$OLLAMA_BIN" ]]; then
    OLLAMA_CMD="$(command -v ollama 2>/dev/null || echo "$OLLAMA_BIN")"
    ok "Ollama já disponível: $OLLAMA_CMD"
else
    info "Baixando binário Ollama para ${OLLAMA_BIN} ..."
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  OL_ARCH="amd64" ;;
        aarch64) OL_ARCH="arm64" ;;
        *)        die "Arquitetura não suportada: $ARCH" ;;
    esac
    OL_URL="https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${OL_ARCH}"
    run "_dl '$OL_URL' '$OLLAMA_BIN'"
    run "chmod +x '$OLLAMA_BIN'"
    ok "Ollama instalado em ${OLLAMA_BIN}"
    echo ""
    info "Adicione ao seu shell para persistir:"
    echo "  ${C}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc${R}"
fi

# Garantir que ollama está no PATH desta sessão
export PATH="${LOCAL_BIN}:${PATH}"

# Iniciar servidor Ollama (se não estiver rodando)
OLLAMA_PORT=11434
if ! $DRY_RUN; then
    if ! curl -sf "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null; then
        info "Iniciando Ollama na porta ${OLLAMA_PORT} (background)..."
        OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" nohup ollama serve \
            > "${SCRIPT_DIR}/ollama.log" 2>&1 &
        echo $! > "${SCRIPT_DIR}/ollama.pid"
        sleep 4
        if curl -sf "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null; then
            ok "Servidor Ollama rodando (PID $(cat "${SCRIPT_DIR}/ollama.pid"))"
        else
            warn "Ollama pode não ter iniciado — veja: ${SCRIPT_DIR}/ollama.log"
        fi
    else
        ok "Servidor Ollama já está respondendo"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# FASE 4 — Download de modelos LLM
# ═════════════════════════════════════════════════════════════════════════════
if ! $SKIP_MODELS; then

hdr "FASE 4 — Modelos LLM (grupo: ${MODELS_GROUP})"

# Mapear VRAM → modelo recomendado automaticamente
if [[ "$MODELS_GROUP" == "recommended" ]]; then
    if [[ "$GPU_VRAM_GB" -ge 40 ]]; then
        MODELS_GROUP="large"
    elif [[ "$GPU_VRAM_GB" -ge 16 ]]; then
        MODELS_GROUP="medium"
    else
        MODELS_GROUP="small"
        warn "VRAM ${GPU_VRAM_GB}GB: usando modelos 7B (small)"
    fi
    info "Grupo selecionado automaticamente: ${MODELS_GROUP} (VRAM ~${GPU_VRAM_GB}GB)"
fi

case "$MODELS_GROUP" in
    small)  MODELS=("qwen2.5-coder:7b") ;;
    medium) MODELS=("qwen2.5-coder:14b" "deepseek-coder-v2:16b") ;;
    large)  MODELS=("qwen2.5-coder:32b") ;;
    all)    MODELS=("qwen2.5-coder:7b" "qwen2.5-coder:14b" "deepseek-coder-v2:16b") ;;
    *)
        warn "Grupo desconhecido '${MODELS_GROUP}' — usando qwen2.5-coder:14b"
        MODELS=("qwen2.5-coder:14b")
        ;;
esac

info "Modelos: ${MODELS[*]}"
echo ""

for model in "${MODELS[@]}"; do
    info "Baixando ${model} ..."
    if ! $DRY_RUN; then
        ollama pull "$model" && ok "$model pronto" || warn "$model falhou — verifique conexão"
    else
        echo "  [DRY-RUN] ollama pull $model"
    fi
done

fi  # fim skip_models

# ═════════════════════════════════════════════════════════════════════════════
# FASE 5 — .env configurado para o servidor
# ═════════════════════════════════════════════════════════════════════════════
hdr "FASE 5 — Arquivo .env"

ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "$ENV_FILE" ]]; then
    info ".env existente — não sobrescrevendo. Backup em ${ENV_FILE}.bak"
    ! $DRY_RUN && cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)" || true
else
    if ! $DRY_RUN; then
        cat > "$ENV_FILE" << EOF
# .env gerado por server_setup.sh em $(date '+%Y-%m-%d %H:%M:%S')
# Servidor: $(hostname) | GPU: ${GPU_INFO:-"n/a"} | VRAM: ~${GPU_VRAM_GB}GB

# ── Backend LLM ───────────────────────────────────────────────────────────────
LLM_BACKEND=ollama
LLM_BASE_URL=http://localhost:${OLLAMA_PORT}/v1
OLLAMA_HOST=http://localhost:${OLLAMA_PORT}

# ── Performance (ajuste conforme VRAM disponível) ─────────────────────────────
# 14B em RTX 4090 (24GB): 1 modelo por vez, 2 agentes em paralelo
MAX_CONCURRENT_MODELS=1
MAX_CONCURRENT_AGENTS=2
MAX_CONCURRENT_SCANS=4

SCAN_TIMEOUT=120
AGENT_TIMEOUT=300
MAX_RETRIES=3
LOG_LEVEL=INFO

# ── Paths ─────────────────────────────────────────────────────────────────────
DATASET_ROOT=${SCRIPT_DIR}/dataset
SNAPSHOTS_DIR=${SCRIPT_DIR}/dataset/snapshots
RESULTS_DIR=${SCRIPT_DIR}/dataset/results
CATALOG_FILE=${SCRIPT_DIR}/dataset/catalog/projects.yaml
EXPERIMENT_OUTPUT=${SCRIPT_DIR}/experiment-results
EOF
        ok ".env gerado: ${ENV_FILE}"
    else
        echo "  [DRY-RUN] cat > ${ENV_FILE} ..."
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# FASE 6 — Validação final
# ═════════════════════════════════════════════════════════════════════════════
hdr "FASE 6 — Validação"

ERRORS=0

if ! $DRY_RUN; then
    "$PYTHON" -c "import a11y_autofix" 2>/dev/null \
        && ok "a11y_autofix importável" \
        || { warn "a11y_autofix não importável"; (( ERRORS++ )) || true; }

    [[ -x "${VENV_DIR}/bin/a11y-autofix" ]] \
        && ok "CLI a11y-autofix presente" \
        || { warn "a11y-autofix não encontrado"; (( ERRORS++ )) || true; }

    command -v ollama &>/dev/null || [[ -x "$OLLAMA_BIN" ]] \
        && ok "ollama presente" \
        || { warn "ollama não encontrado"; (( ERRORS++ )) || true; }

    curl -sf "http://localhost:${OLLAMA_PORT}/api/version" &>/dev/null \
        && ok "Ollama respondendo na porta ${OLLAMA_PORT}" \
        || warn "Ollama não está respondendo (inicie: ollama serve &)"

    command -v node &>/dev/null \
        && ok "node: $(node --version)" \
        || warn "node não encontrado (opcional — necessário para scan)"

    command -v pa11y &>/dev/null \
        && ok "pa11y presente" \
        || warn "pa11y não encontrado (opcional — necessário para scan)"

    command -v git &>/dev/null \
        && ok "git presente" \
        || { warn "git não encontrado — necessário para auto-clone de snapshots"; (( ERRORS++ )) || true; }

    # Catálogo
    CATALOG="${SCRIPT_DIR}/dataset/catalog/projects.yaml"
    if [[ -f "$CATALOG" ]]; then
        N=$("$PYTHON" -c "
import yaml; d=yaml.safe_load(open('${CATALOG}'))
ps=d.get('projects',[])
print(f'{len(ps)} projetos ({sum(1 for p in ps if p.get(\"status\")==\"scanned\")} escaneados)')
" 2>/dev/null || echo "erro ao ler")
        ok "Catálogo: $N"
    else
        warn "Catálogo não encontrado: ${CATALOG}"
        (( ERRORS++ )) || true
    fi

    FINDINGS="${SCRIPT_DIR}/dataset/results"
    if [[ -d "$FINDINGS" ]] && [[ $(find "$FINDINGS" -name "findings.jsonl" | wc -l) -gt 0 ]]; then
        NF=$(find "$FINDINGS" -name "findings.jsonl" | wc -l)
        ok "Findings: ${NF} projetos com findings.jsonl"
    else
        warn "dataset/results/ vazio ou ausente — copie do Windows antes de rodar o experimento"
        (( ERRORS++ )) || true
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# RESUMO E PRÓXIMOS PASSOS
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "${B}${C}══════════════════════════════════════════════════════════${R}"
echo "${B}  SETUP CONCLUÍDO${R}"
echo "${B}${C}══════════════════════════════════════════════════════════${R}"
echo ""

if [[ "$ERRORS" -gt 0 ]]; then
    warn "${ERRORS} problema(s) encontrado(s) — revise os avisos acima"
    echo ""
fi

echo "  ${B}Próximos passos:${R}"
echo ""
echo "  ${C}1. Ativar o ambiente:${R}"
echo "     source ${VENV_DIR}/bin/activate"
echo ""
echo "  ${C}2. Verificar que Ollama está rodando:${R}"
echo "     curl http://localhost:${OLLAMA_PORT}/api/version"
echo "     # ou iniciar: OLLAMA_HOST=0.0.0.0:${OLLAMA_PORT} nohup ollama serve > ollama.log 2>&1 &"
echo ""
echo "  ${C}3. Rodar experimento (com resistência a desconexão SSH):${R}"
echo "     ./start_experiment.sh"
echo "     # ou em background puro:"
echo "     nohup ./start_experiment.sh --config experiments/qwen_vs_deepseek.yaml > logs/exp.log 2>&1 &"
echo ""
echo "  ${C}4. Monitorar progresso (qualquer sessão SSH):${R}"
echo "     python watch_experiment.py"
echo "     # ou ver log bruto:"
echo "     tail -f logs/experiment_current.log"
echo ""
echo "  ${C}5. Reconectar ao screen (se start_experiment.sh usou screen):${R}"
echo "     screen -r a11y-exp"
echo ""

# ── AVISO: libs Playwright ────────────────────────────────────────────────────
if ! $SKIP_NODE && ! $SKIP_PLAYWRIGHT; then
    echo ""
    echo "${Y}${B}  ATENÇÃO — Playwright pode precisar de libs do sistema:${R}"
    echo "  Se o browser falhar com erro de shared library (.so), peça ao admin:"
    echo ""
    echo "    ${C}sudo apt-get install -y \\${R}"
    echo "    ${C}  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \\${R}"
    echo "    ${C}  libcups2 libdrm2 libxkbcommon0 libxcomposite1 \\${R}"
    echo "    ${C}  libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2${R}"
    echo ""
    echo "  Nota: para o experimento LLM, Playwright só é necessário se"
    echo "  o scan não foi copiado do Windows. A validação (layers 1-4)"
    echo "  é 100% estática (sem browser)."
fi

$DRY_RUN && echo "" && warn "Modo DRY-RUN — nenhuma ação foi executada"
echo ""
