#!/usr/bin/env bash
# =============================================================================
#  transfer_to_gpu.sh — Transfere o projeto para a máquina GPU via SSH/rsync
#
#  O que é transferido:
#    ✓  Todo o código fonte (a11y_autofix/, dataset/scripts/, dataset/schema/)
#    ✓  Catálogo e resultados (dataset/catalog/, dataset/results/)
#    ✓  Configurações (models.yaml, pyproject.toml, .env se existir)
#    ✓  Scripts de setup e experimentos (experiments/, scripts/)
#    ✓  remote_setup.sh (executado automaticamente no destino)
#
#  O que NÃO é transferido (recriado no destino):
#    ✗  dataset/snapshots/   → re-clonado via snapshot.py no destino
#    ✗  .venv/               → recriado via pip install
#    ✗  node_modules/        → recriado via npm install
#    ✗  __pycache__/         → gerado automaticamente
#    ✗  .git/                → não necessário para execução
#
#  Uso:
#    bash transfer_to_gpu.sh --host <ip_ou_hostname>
#    bash transfer_to_gpu.sh --host 192.168.1.50 --user ubuntu --key ~/.ssh/id_rsa
#    bash transfer_to_gpu.sh --host gpu.lab.com --remote-dir /home/joao/a11y --setup
#    bash transfer_to_gpu.sh --host gpu.lab.com --setup --github-token ghp_xxx
#    bash transfer_to_gpu.sh --host gpu.lab.com --transfer-only   # só sincroniza
#    bash transfer_to_gpu.sh --host gpu.lab.com --dry-run         # prévia sem executar
#
#  Flags:
#    --host HOST           IP ou hostname da máquina GPU (obrigatório)
#    --user USER           Usuário SSH (default: $USER do sistema)
#    --port PORT           Porta SSH (default: 22)
#    --key PATH            Caminho da chave SSH (default: ~/.ssh/id_rsa)
#    --remote-dir PATH     Diretório remoto (default: ~/a11y_autofix_experiment)
#    --setup               Executa remote_setup.sh após transferência
#    --transfer-only       Só transfere, não executa setup
#    --dry-run             Mostra o que faria sem executar
#    --github-token TOKEN  Token GitHub para discover.py --top-up no destino
#    --workers N           Workers do scan no destino (default: 2)
#    --skip-confirm        Não pede confirmação antes de transferir
# =============================================================================
set -euo pipefail

# ── Caminhos ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

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
step() { echo -e "\n  ${BOLD}$*${NC}"; }

# ── Defaults ──────────────────────────────────────────────────────────────────
REMOTE_HOST=""
REMOTE_USER="${USER:-ubuntu}"
REMOTE_PORT=22
SSH_KEY="${HOME}/.ssh/id_rsa"
REMOTE_DIR="~/a11y_autofix_experiment"
DO_SETUP=false
TRANSFER_ONLY=false
DRY_RUN=false
GITHUB_TOKEN=""
REMOTE_WORKERS=2
SKIP_CONFIRM=false

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)           REMOTE_HOST="${2:?'--host requer valor'}"; shift ;;
        --user)           REMOTE_USER="${2:?'--user requer valor'}"; shift ;;
        --port)           REMOTE_PORT="${2:?'--port requer valor'}"; shift ;;
        --key)            SSH_KEY="${2:?'--key requer valor'}"; shift ;;
        --remote-dir)     REMOTE_DIR="${2:?'--remote-dir requer valor'}"; shift ;;
        --setup)          DO_SETUP=true ;;
        --transfer-only)  TRANSFER_ONLY=true ;;
        --dry-run)        DRY_RUN=true ;;
        --github-token)   GITHUB_TOKEN="${2:?'--github-token requer valor'}"; shift ;;
        --workers)        REMOTE_WORKERS="${2:?'--workers requer valor'}"; shift ;;
        --skip-confirm)   SKIP_CONFIRM=true ;;
        --help|-h)
            sed -n '2,32p' "$0" | sed 's/^# //; s/^#//'
            exit 0 ;;
        *) die "Flag desconhecida: $1 — use --help" ;;
    esac
    shift
done

[[ -z "$REMOTE_HOST" ]] && die "Informe o host com --host <ip_ou_hostname>"

# ── SSH helper ────────────────────────────────────────────────────────────────
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -p "$REMOTE_PORT")
[[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")

ssh_run() { ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"; }
ssh_run_bg() { ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@" & }

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  ♿ a11y-autofix — Transferência para Máquina GPU        ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Origem:     $REPO_ROOT"
echo -e "  Destino:    ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}  (porta ${REMOTE_PORT})"
echo -e "  Chave SSH:  $SSH_KEY"
$DO_SETUP && echo -e "  Setup:      ${GREEN}ativado (remote_setup.sh será executado)${NC}"
$DRY_RUN  && echo -e "  ${YELLOW}Modo:       DRY-RUN${NC}"
echo ""

# ── FASE 0: Pré-verificações locais ───────────────────────────────────────────
hdr "FASE 0 — Verificação do ambiente local"

command -v rsync &>/dev/null || die "rsync não encontrado (brew install rsync)"
command -v ssh   &>/dev/null || die "ssh não encontrado"

# Verificar chave SSH
if [[ -f "$SSH_KEY" ]]; then
    ok "Chave SSH: $SSH_KEY"
else
    warn "Chave SSH não encontrada em $SSH_KEY"
    info "O rsync tentará autenticação por senha ou agente SSH"
fi

# Verificar remote_setup.sh existe
SETUP_SCRIPT="$REPO_ROOT/remote_setup.sh"
[[ -f "$SETUP_SCRIPT" ]] && ok "remote_setup.sh presente" \
                          || die "remote_setup.sh não encontrado em $REPO_ROOT — execute este script do diretório raiz do projeto"

# Estimar tamanho da transferência
step "Estimando tamanho da transferência..."
TRANSFER_SIZE=$(rsync -an --stats \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='node_modules/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='dataset/snapshots/' \
    --exclude='backup_dados/' \
    --exclude='*.log' \
    --exclude='.DS_Store' \
    --exclude='experiment-results/' \
    "$REPO_ROOT/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/" \
    2>/dev/null | grep "Total transferred file size" | awk '{print $NF}' || echo "desconhecido")

N_FILES=$(rsync -an \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='node_modules/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='dataset/snapshots/' \
    --exclude='backup_dados/' \
    --exclude='*.log' \
    --exclude='.DS_Store' \
    --exclude='experiment-results/' \
    "$REPO_ROOT/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/" \
    2>/dev/null | grep -c "^" || echo "?")

info "Arquivos a transferir: ~$N_FILES  |  Tamanho: $TRANSFER_SIZE"
info "Excluídos: dataset/snapshots/ (re-clonado no destino), .venv/, node_modules/, __pycache__/"

# ── FASE 1: Testar conexão SSH ────────────────────────────────────────────────
hdr "FASE 1 — Testando conexão SSH"

if $DRY_RUN; then
    echo "  [DRY-RUN] ssh ${SSH_OPTS[*]} ${REMOTE_USER}@${REMOTE_HOST} 'echo OK'"
else
    echo -n "  Conectando a ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT} ..."
    if ssh_run 'echo " OK"' 2>/dev/null; then
        ok "Conexão SSH estabelecida"
    else
        die "Falha na conexão SSH
  Verifique:
    • IP/hostname: $REMOTE_HOST
    • Usuário: $REMOTE_USER
    • Porta: $REMOTE_PORT
    • Chave: $SSH_KEY
  Teste manual: ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST}"
    fi

    # Detectar OS remoto e GPU
    REMOTE_INFO=$(ssh_run '
        OS=$(cat /etc/os-release 2>/dev/null | grep "^PRETTY_NAME" | cut -d= -f2 | tr -d "\"" || uname -s)
        ARCH=$(uname -m)
        RAM=$(free -h 2>/dev/null | awk "/^Mem:/{print \$2}" || sysctl -n hw.memsize 2>/dev/null | awk "{printf \"%.0fG\", \$1/1073741824}" || echo "?")
        GPU=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "GPU não detectada")
        DISK=$(df -h "$HOME" 2>/dev/null | awk "NR==2{print \$4}" || echo "?")
        echo "OS=$OS | ARCH=$ARCH | RAM=$RAM | DISK=$DISK | GPU=$GPU"
    ' 2>/dev/null || echo "info não disponível")
    info "Destino: $REMOTE_INFO"
fi

# ── Confirmação ───────────────────────────────────────────────────────────────
if ! $SKIP_CONFIRM && ! $DRY_RUN; then
    echo ""
    echo -e "  ${BOLD}Pronto para transferir:${NC}"
    echo -e "    De:  $REPO_ROOT"
    echo -e "    Para: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
    echo ""
    read -r -p "  Continuar? [s/N] " CONFIRM
    [[ "${CONFIRM,,}" == "s" || "${CONFIRM,,}" == "y" ]] || { echo "  Cancelado."; exit 0; }
fi

# ── FASE 2: Transferência via rsync ───────────────────────────────────────────
hdr "FASE 2 — Transferindo arquivos via rsync"

RSYNC_OPTS=(
    -avz
    --progress
    --compress-level=6
    --checksum
    --delete
    --exclude='.git/'
    --exclude='.venv/'
    --exclude='venv/'
    --exclude='node_modules/'
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='*.pyo'
    --exclude='*.egg-info/'
    --exclude='.pytest_cache/'
    --exclude='.mypy_cache/'
    --exclude='.ruff_cache/'
    --exclude='dataset/snapshots/'   # Re-clonado no destino
    --exclude='backup_dados/'        # Backup local, não necessário
    --exclude='experiment-results/'  # Resultados locais, fresh no destino
    --exclude='*.log'
    --exclude='.DS_Store'
    --exclude='Thumbs.db'
    -e "ssh ${SSH_OPTS[*]}"
)

if $DRY_RUN; then
    echo "  [DRY-RUN] rsync ${RSYNC_OPTS[*]} $REPO_ROOT/ ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
else
    # Criar diretório remoto primeiro
    ssh_run "mkdir -p ${REMOTE_DIR}" || true

    echo ""
    rsync "${RSYNC_OPTS[@]}" \
        "$REPO_ROOT/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/" \
        && ok "Transferência concluída" \
        || die "rsync falhou — verifique conexão e espaço em disco no destino"
fi

# Verificar integridade básica
if ! $DRY_RUN; then
    step "Verificando integridade no destino..."
    REMOTE_CHECK=$(ssh_run "
        cd ${REMOTE_DIR} 2>/dev/null || exit 1
        echo 'files_ok=true'
        [[ -f pyproject.toml ]]         && echo 'pyproject=ok'  || echo 'pyproject=MISSING'
        [[ -f models.yaml ]]            && echo 'models=ok'     || echo 'models=MISSING'
        [[ -d a11y_autofix/ ]]          && echo 'a11y_code=ok'  || echo 'a11y_code=MISSING'
        [[ -d dataset/scripts/ ]]       && echo 'dataset=ok'    || echo 'dataset=MISSING'
        [[ -f remote_setup.sh ]]        && echo 'setup=ok'      || echo 'setup=MISSING'
        [[ -f fix_and_rescan.sh ]]      && echo 'fix_script=ok' || echo 'fix_script=MISSING'
        N=\$(find . -name '*.py' | wc -l)
        echo \"python_files=\$N\"
    " 2>/dev/null || echo "check_failed")

    while IFS='=' read -r key val; do
        case "$key" in
            pyproject|models|a11y_code|dataset|setup|fix_script)
                [[ "$val" == "ok" ]] && ok "$key" || warn "$key — arquivo ausente no destino" ;;
            python_files) info "Arquivos .py no destino: $val" ;;
        esac
    done <<< "$REMOTE_CHECK"
fi

$TRANSFER_ONLY && {
    echo -e "\n  ${GREEN}--transfer-only: transferência concluída.${NC}"
    echo -e "  Para configurar o destino:"
    echo -e "    ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST}"
    echo -e "    cd ${REMOTE_DIR} && bash remote_setup.sh\n"
    exit 0
}

# ── FASE 3: Setup remoto ──────────────────────────────────────────────────────
hdr "FASE 3 — Setup e inicialização na máquina GPU"

if ! $DO_SETUP; then
    info "Setup remoto não ativado (use --setup para executar automaticamente)"
    echo ""
    echo -e "  ${BOLD}Para inicializar manualmente no destino:${NC}"
    echo -e "    ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST}"
    echo -e "    cd ${REMOTE_DIR}"
    echo -e "    bash remote_setup.sh"
    [[ -n "$GITHUB_TOKEN" ]] && \
    echo -e "    # ou com token GitHub:"
    echo -e "    bash remote_setup.sh --github-token <TOKEN> --workers $REMOTE_WORKERS"
    echo ""
    exit 0
fi

# Executar setup remoto
SETUP_CMD="cd ${REMOTE_DIR} && bash remote_setup.sh"
[[ -n "$GITHUB_TOKEN" ]] && SETUP_CMD+=" --github-token '${GITHUB_TOKEN}'"
SETUP_CMD+=" --workers ${REMOTE_WORKERS}"
SETUP_CMD+=" 2>&1 | tee ${REMOTE_DIR}/setup_$(date +%Y%m%d_%H%M%S).log"

if $DRY_RUN; then
    echo "  [DRY-RUN] ssh ... '${SETUP_CMD}'"
else
    info "Executando remote_setup.sh no destino (pode demorar ~30-60 min para download de modelos)..."
    info "Log salvo em: ${REMOTE_DIR}/setup_*.log"
    echo ""
    ssh_run "$SETUP_CMD" \
        && ok "remote_setup.sh concluído com sucesso" \
        || warn "remote_setup.sh terminou com avisos — verifique o log no destino"
fi

# ── RESUMO ────────────────────────────────────────────────────────────────────
hdr "RESUMO"
echo ""
echo -e "  ${BOLD}Destino:${NC}"
echo -e "    ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo ""
echo -e "  ${BOLD}Para conectar e monitorar:${NC}"
echo -e "    ssh -p ${REMOTE_PORT} ${REMOTE_USER}@${REMOTE_HOST}"
echo -e "    cd ${REMOTE_DIR}"
echo ""
echo -e "  ${BOLD}Comandos no destino após setup:${NC}"
echo -e "    # Ver status do corpus:"
echo -e "    python dataset/scripts/scan.py --status"
echo ""
echo -e "    # Monitorar scan em tempo real:"
echo -e "    python dataset/scripts/watch_scan.py"
echo ""
echo -e "    # Rodar experimento de correção com LLM:"
echo -e "    a11y-autofix experiment experiments/qwen_vs_deepseek.yaml"
echo ""
echo -e "    # Ou corrigir um projeto específico:"
echo -e "    a11y-autofix fix dataset/snapshots/<projeto>/src --model qwen2.5-coder-7b"
echo ""
echo -e "  ${BOLD}Resincronizar dados (sem re-setup):${NC}"
echo -e "    bash transfer_to_gpu.sh --host ${REMOTE_HOST} --user ${REMOTE_USER} --transfer-only"
echo ""
$DRY_RUN && echo -e "  ${YELLOW}⚠️  Modo DRY-RUN — nenhuma ação foi executada${NC}\n"
