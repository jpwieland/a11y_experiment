#!/usr/bin/env bash
# ============================================================
#  a11y-autofix — Limpeza do Ambiente
#
#  Remove configurações de execução do experimento,
#  preservando todos os arquivos de resultados.
#
#  O que É removido (por padrão):
#    • .venv/                       — virtual environment Python
#    • .env                         — variáveis de ambiente
#    • ~/.ollama/ollama.env         — config GPU do Ollama
#    • setup.log                    — log do setup
#    • __pycache__/ e *.egg-info/   — artefatos de build Python
#    • .pytest_cache/ .coverage     — artefatos de testes
#    • node_modules/                — dependências Node.js (se existir)
#
#  O que NÃO é removido (resultados preservados):
#    • experiment-results/          — resultados do experimento
#    • dataset/results/             — perfil, validação, calibração
#    • dataset/catalog/             — catálogo de projetos
#    • a11y-report/                 — relatórios de scan
#    • experiments/                 — configs YAML dos experimentos
#    • dataset/annotations/         — anotações ground-truth
#
#  Flags opcionais:
#    --snapshots       Remove clones de repositórios (dataset/snapshots/)
#    --models          Remove modelos Ollama baixados
#    --browsers        Remove browsers do Playwright
#    --npm-tools       Remove pacotes npm globais (pa11y, axe, lighthouse)
#    --all             Equivale a todas as flags acima juntas
#    --dry-run         Exibe o que seria removido sem remover nada
#    --yes             Pula confirmação interativa
#
#  Uso:
#    bash cleanup.sh                 # limpeza padrão (pede confirmação)
#    bash cleanup.sh --dry-run       # simulação sem remover nada
#    bash cleanup.sh --yes           # limpeza padrão sem confirmação
#    bash cleanup.sh --all --yes     # limpeza completa sem confirmação
#    bash cleanup.sh --models        # também remove modelos Ollama
# ============================================================
set -euo pipefail
IFS=$'\n\t'

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false
AUTO_YES=false
REMOVE_SNAPSHOTS=false
REMOVE_MODELS=false
REMOVE_BROWSERS=false
REMOVE_NPM_TOOLS=false

for arg in "$@"; do
    case "$arg" in
        --dry-run)    DRY_RUN=true ;;
        --yes|-y)     AUTO_YES=true ;;
        --snapshots)  REMOVE_SNAPSHOTS=true ;;
        --models)     REMOVE_MODELS=true ;;
        --browsers)   REMOVE_BROWSERS=true ;;
        --npm-tools)  REMOVE_NPM_TOOLS=true ;;
        --all)
            REMOVE_SNAPSHOTS=true
            REMOVE_MODELS=true
            REMOVE_BROWSERS=true
            REMOVE_NPM_TOOLS=true
            ;;
        --help|-h)
            sed -n '3,40p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "Flag desconhecida: $arg  (use --help)"
            exit 1
            ;;
    esac
done

# ── Caminhos ──────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
ENV_FILE="$PROJECT_ROOT/.env"
LOG_FILE="$PROJECT_ROOT/setup.log"
OLLAMA_ENV_FILE="$HOME/.ollama/ollama.env"

# ── Cores ─────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

# ── Contadores ────────────────────────────────────────────────────────────────
N_REMOVED=0
N_SKIPPED=0
FREED_MB=0

# ── Helpers ───────────────────────────────────────────────────────────────────
removed()  { echo -e "${RED}  ✗${NC} $*"; N_REMOVED=$((N_REMOVED+1)); }
skipped()  { echo -e "${GREEN}  ✓${NC} $* ${CYAN}[preservado]${NC}"; N_SKIPPED=$((N_SKIPPED+1)); }
would_rm() { echo -e "${YELLOW}  ~${NC} $* ${YELLOW}[seria removido]${NC}"; }
info()     { echo -e "${BLUE}  →${NC} $*"; }
section()  { echo -e "\n${BOLD}${CYAN}── $* ──${NC}"; }
has()      { command -v "$1" &>/dev/null; }

# Calcula tamanho em MB de um path (arquivo ou diretório)
size_mb() {
    local path="$1"
    if [ -e "$path" ]; then
        du -sm "$path" 2>/dev/null | awk '{print $1}' || echo 0
    else
        echo 0
    fi
}

# Remove um path (arquivo ou diretório) com suporte a dry-run
do_remove() {
    local path="$1"
    local label="${2:-$path}"
    if [ ! -e "$path" ]; then
        return 0
    fi
    local mb
    mb=$(size_mb "$path")
    FREED_MB=$((FREED_MB + mb))
    if [ "$DRY_RUN" = "true" ]; then
        would_rm "$label  (${mb} MB)"
        N_REMOVED=$((N_REMOVED+1))
    else
        rm -rf "$path"
        removed "$label  (${mb} MB liberados)"
    fi
}

# Remove múltiplos paths com glob recursivo
do_remove_glob() {
    local label="$1"; shift
    local total_mb=0
    local paths=()
    while IFS= read -r -d '' p; do
        paths+=("$p")
        total_mb=$((total_mb + $(size_mb "$p")))
    done < <(find "$PROJECT_ROOT" -name "$1" -prune -print0 2>/dev/null)

    if [ ${#paths[@]} -eq 0 ]; then
        return 0
    fi

    FREED_MB=$((FREED_MB + total_mb))
    if [ "$DRY_RUN" = "true" ]; then
        would_rm "$label  (${#paths[@]} encontrados, ${total_mb} MB)"
        N_REMOVED=$((N_REMOVED+1))
    else
        for p in "${paths[@]}"; do
            rm -rf "$p"
        done
        removed "$label  (${#paths[@]} removidos, ${total_mb} MB liberados)"
    fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${CYAN}♿ a11y-autofix — Limpeza do Ambiente${NC}"
echo "══════════════════════════════════════════════════════"
echo "Projeto : $PROJECT_ROOT"
echo "Data    : $(date '+%Y-%m-%d %H:%M:%S')"
if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}${BOLD}MODO SIMULAÇÃO — nada será removido${NC}"
fi
echo ""

# ── Confirmação ───────────────────────────────────────────────────────────────
if [ "$DRY_RUN" = "false" ] && [ "$AUTO_YES" = "false" ]; then
    echo -e "${BOLD}Será removido:${NC}"
    echo "  • .venv/                  (virtual environment Python)"
    echo "  • .env                    (variáveis de ambiente)"
    echo "  • ~/.ollama/ollama.env    (config GPU Ollama)"
    echo "  • setup.log               (log do setup)"
    echo "  • __pycache__/, *.egg-info/, .pytest_cache/"
    [ "$REMOVE_SNAPSHOTS" = "true" ] && echo "  • dataset/snapshots/      (clones de repositórios)"
    [ "$REMOVE_MODELS"    = "true" ] && echo "  • modelos Ollama baixados"
    [ "$REMOVE_BROWSERS"  = "true" ] && echo "  • browsers Playwright"
    [ "$REMOVE_NPM_TOOLS" = "true" ] && echo "  • pa11y, @axe-core/cli, lighthouse (npm global)"
    echo ""
    echo -e "${BOLD}Será preservado:${NC}"
    echo "  • experiment-results/     (resultados dos experimentos)"
    echo "  • dataset/results/        (perfil, validação, calibração)"
    echo "  • dataset/catalog/        (catálogo de projetos)"
    echo "  • dataset/annotations/    (anotações ground-truth)"
    echo "  • a11y-report/            (relatórios de scan)"
    echo "  • experiments/            (configs YAML)"
    echo ""
    read -r -p "Confirmar limpeza? [s/N] " CONFIRM
    case "$CONFIRM" in
        [sS][iI][mM]|[sS]) : ;;
        *) echo "Cancelado."; exit 0 ;;
    esac
    echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Virtual environment
# ═══════════════════════════════════════════════════════════════════════════════
section "Virtual Environment"

# Desativar venv se ativo neste processo
if [ -n "${VIRTUAL_ENV:-}" ]; then
    info "Desativando venv atual antes de remover..."
    deactivate 2>/dev/null || true
fi

do_remove "$VENV_DIR" ".venv/"

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Arquivo .env
# ═══════════════════════════════════════════════════════════════════════════════
section "Arquivo .env"

do_remove "$ENV_FILE" ".env"
skipped ".env.example  (template preservado)"

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Config Ollama GPU
# ═══════════════════════════════════════════════════════════════════════════════
section "Configuração Ollama GPU"

do_remove "$OLLAMA_ENV_FILE" "~/.ollama/ollama.env"

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Logs do setup
# ═══════════════════════════════════════════════════════════════════════════════
section "Logs"

do_remove "$LOG_FILE" "setup.log"

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Artefatos de build Python
# ═══════════════════════════════════════════════════════════════════════════════
section "Artefatos de Build Python"

do_remove_glob "__pycache__/" "__pycache__"
do_remove_glob "*.egg-info/" "*.egg-info"
do_remove "$PROJECT_ROOT/build"   "build/"
do_remove "$PROJECT_ROOT/dist"    "dist/"
do_remove "$PROJECT_ROOT/.mypy_cache" ".mypy_cache/"

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Artefatos de testes
# ═══════════════════════════════════════════════════════════════════════════════
section "Artefatos de Testes"

do_remove "$PROJECT_ROOT/.pytest_cache" ".pytest_cache/"
do_remove "$PROJECT_ROOT/.coverage"     ".coverage"
do_remove "$PROJECT_ROOT/htmlcov"       "htmlcov/"

# Remove arquivos .coverage.* individualmente
while IFS= read -r -d '' f; do
    do_remove "$f" "$(basename "$f")"
done < <(find "$PROJECT_ROOT" -maxdepth 2 -name ".coverage.*" -print0 2>/dev/null)

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Node modules (se existir)
# ═══════════════════════════════════════════════════════════════════════════════
section "Node.js"

do_remove "$PROJECT_ROOT/node_modules" "node_modules/"

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Snapshots (opcional — --snapshots)
# ═══════════════════════════════════════════════════════════════════════════════
section "Dataset Snapshots"

SNAPSHOTS_DIR="$PROJECT_ROOT/dataset/snapshots"
if [ "$REMOVE_SNAPSHOTS" = "true" ]; then
    if [ -d "$SNAPSHOTS_DIR" ] && [ -n "$(ls -A "$SNAPSHOTS_DIR" 2>/dev/null)" ]; then
        do_remove "$SNAPSHOTS_DIR" "dataset/snapshots/"
        # Recriar diretório vazio para manter estrutura
        if [ "$DRY_RUN" = "false" ]; then
            mkdir -p "$SNAPSHOTS_DIR"
            info "dataset/snapshots/ recriado (vazio)"
        fi
    else
        info "dataset/snapshots/ já vazio"
    fi
else
    if [ -d "$SNAPSHOTS_DIR" ]; then
        snap_mb=$(size_mb "$SNAPSHOTS_DIR")
        skipped "dataset/snapshots/  (${snap_mb} MB — use --snapshots para remover)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 9. Modelos Ollama (opcional — --models)
# ═══════════════════════════════════════════════════════════════════════════════
section "Modelos Ollama"

if [ "$REMOVE_MODELS" = "true" ]; then
    if ! has ollama; then
        info "Ollama não instalado — nada a remover"
    elif ! curl -s --max-time 3 http://localhost:11434/ &>/dev/null; then
        info "Ollama daemon não está rodando — inicie para remover modelos"
        info "  Execute: ollama serve  e depois: bash cleanup.sh --models"
    else
        MODELS_LIST=$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' || true)
        if [ -z "$MODELS_LIST" ]; then
            info "Nenhum modelo Ollama instalado"
        else
            while IFS= read -r model; do
                [ -z "$model" ] && continue
                if [ "$DRY_RUN" = "true" ]; then
                    would_rm "ollama model: $model"
                    N_REMOVED=$((N_REMOVED+1))
                else
                    if ollama rm "$model" 2>/dev/null; then
                        removed "ollama rm $model"
                    else
                        echo -e "${YELLOW}  ⚠${NC} Falha ao remover: $model"
                    fi
                fi
            done <<< "$MODELS_LIST"
        fi
    fi
else
    if has ollama && curl -s --max-time 2 http://localhost:11434/ &>/dev/null; then
        MODEL_COUNT=$(ollama list 2>/dev/null | awk 'NR>1' | wc -l | tr -d ' ')
        skipped "modelos Ollama ($MODEL_COUNT instalados — use --models para remover)"
    else
        info "Ollama daemon não está rodando — modelos preservados"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 10. Browsers Playwright (opcional — --browsers)
# ═══════════════════════════════════════════════════════════════════════════════
section "Playwright Browsers"

if [ "$REMOVE_BROWSERS" = "true" ]; then
    # Diretório padrão dos browsers do Playwright
    if [ "$(uname)" = "Darwin" ]; then
        PW_DIR="$HOME/Library/Caches/ms-playwright"
    else
        PW_DIR="$HOME/.cache/ms-playwright"
    fi

    if [ -d "$PW_DIR" ]; then
        do_remove "$PW_DIR" "ms-playwright browsers (~$(size_mb "$PW_DIR") MB)"
    else
        info "Playwright browsers não encontrados em $PW_DIR"
    fi
else
    if [ "$(uname)" = "Darwin" ]; then
        PW_DIR="$HOME/Library/Caches/ms-playwright"
    else
        PW_DIR="$HOME/.cache/ms-playwright"
    fi
    if [ -d "$PW_DIR" ]; then
        pw_mb=$(size_mb "$PW_DIR")
        skipped "Playwright browsers  (${pw_mb} MB — use --browsers para remover)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 11. Ferramentas npm globais (opcional — --npm-tools)
# ═══════════════════════════════════════════════════════════════════════════════
section "Ferramentas npm Globais"

if [ "$REMOVE_NPM_TOOLS" = "true" ]; then
    if ! has npm; then
        info "npm não encontrado — nada a remover"
    else
        for pkg_bin in "pa11y:pa11y" "@axe-core/cli:axe" "lighthouse:lighthouse"; do
            pkg="${pkg_bin%%:*}"
            bin="${pkg_bin##*:}"
            if has "$bin"; then
                if [ "$DRY_RUN" = "true" ]; then
                    would_rm "npm uninstall -g $pkg"
                    N_REMOVED=$((N_REMOVED+1))
                else
                    if npm uninstall -g "$pkg" >/dev/null 2>&1; then
                        removed "npm -g $pkg"
                    else
                        echo -e "${YELLOW}  ⚠${NC} Falha ao remover $pkg  →  sudo npm uninstall -g $pkg"
                    fi
                fi
            else
                info "$pkg não instalado globalmente"
            fi
        done
    fi
else
    INSTALLED_TOOLS=()
    has pa11y     && INSTALLED_TOOLS+=("pa11y")
    has axe       && INSTALLED_TOOLS+=("axe")
    has lighthouse && INSTALLED_TOOLS+=("lighthouse")
    if [ ${#INSTALLED_TOOLS[@]} -gt 0 ]; then
        TOOLS_STR=$(printf '%s, ' "${INSTALLED_TOOLS[@]}"); TOOLS_STR="${TOOLS_STR%, }"
        skipped "npm global: $TOOLS_STR  (use --npm-tools para remover)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 12. Preservar resultados (verificação explícita)
# ═══════════════════════════════════════════════════════════════════════════════
section "Verificação de Resultados Preservados"

PRESERVED=(
    "experiment-results"
    "dataset/results"
    "dataset/catalog"
    "dataset/annotations"
    "a11y-report"
    "experiments"
)
for d in "${PRESERVED[@]}"; do
    path="$PROJECT_ROOT/$d"
    if [ -d "$path" ]; then
        count=$(find "$path" -type f 2>/dev/null | wc -l | tr -d ' ')
        mb=$(size_mb "$path")
        skipped "$d/  ($count arquivos, ${mb} MB)"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════════
# Resumo
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════════"
echo -e "${BOLD}Resumo${NC}"
echo "══════════════════════════════════════════════════════"

if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}${BOLD}SIMULAÇÃO — nenhum arquivo foi removido${NC}"
    echo ""
    echo -e "  Seriam removidos : ${YELLOW}$N_REMOVED itens (~${FREED_MB} MB)${NC}"
    echo -e "  Preservados      : ${GREEN}$N_SKIPPED${NC}"
    echo ""
    echo "Para executar a limpeza:"
    echo "  bash cleanup.sh --yes"
else
    echo -e "  Removidos  : ${RED}$N_REMOVED itens (~${FREED_MB} MB liberados)${NC}"
    echo -e "  Preservados: ${GREEN}$N_SKIPPED${NC}"
fi

echo ""
echo -e "${BOLD}Para reconfigurar o ambiente:${NC}"
echo "  bash setup.sh"
echo ""

if [ "$DRY_RUN" = "false" ] && [ "$N_REMOVED" -gt 0 ]; then
    echo -e "${GREEN}${BOLD}✓ Limpeza concluída — resultados preservados.${NC}"
fi
