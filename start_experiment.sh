#!/usr/bin/env bash
# start_experiment.sh — Inicia (ou retoma) o experimento LLM em sessão tmux.
#
# Uso:
#   ./start_experiment.sh                          # inicia com config padrão
#   ./start_experiment.sh --config experiments/experiment_14b_comparison.yaml
#   ./start_experiment.sh --output experiment-results/14b_run_2
#   ./start_experiment.sh --resume                 # retoma a partir dos checkpoints
#   ./start_experiment.sh --attach                 # apenas reconecta (não reinicia)
#   ./start_experiment.sh --watch                  # abre watch_experiment.py no painel inferior
#
# Sobrevive a desconexões SSH: o experimento continua em tmux.
# Para reconectar:  tmux attach -t a11y-exp
# Para monitorar:   python watch_experiment.py

set -euo pipefail

# ── Configurações ─────────────────────────────────────────────────────────────
SESSION="a11y-exp"
WINDOW="experiment"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${SCRIPT_DIR}/.venv"
PYTHON="${VENV_PATH}/bin/python"
LOG_DIR="${SCRIPT_DIR}/logs"
LOG_FILE="${LOG_DIR}/experiment_$(date +%Y%m%d_%H%M%S).log"

# Defaults
CONFIG="${SCRIPT_DIR}/experiments/experiment_14b_comparison.yaml"
OUTPUT_DIR=""
RESUME=false
ATTACH_ONLY=false
OPEN_WATCH=false

# ── ANSI ──────────────────────────────────────────────────────────────────────
R=$'\033[0m'; BOLD=$'\033[1m'; DIM=$'\033[2m'
GREEN=$'\033[92m'; YELLOW=$'\033[93m'; CYAN=$'\033[96m'; RED=$'\033[91m'

# ── Parsear argumentos ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config|-c)   CONFIG="$2"; shift 2 ;;
        --output|-o)   OUTPUT_DIR="$2"; shift 2 ;;
        --resume|-r)   RESUME=true; shift ;;
        --attach|-a)   ATTACH_ONLY=true; shift ;;
        --watch|-w)    OPEN_WATCH=true; shift ;;
        --help|-h)
            head -20 "$0" | grep -E "^#" | sed 's/^# \{0,2\}//'
            exit 0
            ;;
        *) echo "${RED}Argumento desconhecido: $1${R}"; exit 1 ;;
    esac
done

# ── Só reconectar? ────────────────────────────────────────────────────────────
if $ATTACH_ONLY; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "${CYAN}Reconectando à sessão tmux '${SESSION}'...${R}"
        exec tmux attach -t "$SESSION"
    else
        echo "${RED}Sessão '${SESSION}' não encontrada.${R}"
        echo "Use ./start_experiment.sh para iniciar uma nova sessão."
        exit 1
    fi
fi

# ── Verificações básicas ──────────────────────────────────────────────────────
echo ""
echo "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
echo "${BOLD}  ♿  a11y-autofix — Inicializador do Experimento LLM${R}"
echo "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
echo ""

# Verificar tmux
if ! command -v tmux &>/dev/null; then
    echo "${RED}✘  tmux não encontrado. Instale com:${R}"
    echo "   sudo apt-get install tmux   # Debian/Ubuntu"
    echo "   conda install -c conda-forge tmux   # sem root"
    exit 1
fi
echo "${GREEN}✔${R}  tmux $(tmux -V | cut -d' ' -f2)"

# Verificar Python / venv
if [[ ! -f "$PYTHON" ]]; then
    echo "${YELLOW}⚠  venv não encontrado em ${VENV_PATH}${R}"
    echo "   Tentando python3 do sistema..."
    PYTHON="$(command -v python3 || true)"
    if [[ -z "$PYTHON" ]]; then
        echo "${RED}✘  Python3 não encontrado.${R}"
        exit 1
    fi
fi
echo "${GREEN}✔${R}  Python: $($PYTHON --version)"

# Verificar config
if [[ ! -f "$CONFIG" ]]; then
    echo "${RED}✘  Arquivo de config não encontrado: ${CONFIG}${R}"
    exit 1
fi
echo "${GREEN}✔${R}  Config: ${CONFIG}"

# Verificar ollama
if command -v ollama &>/dev/null; then
    echo "${GREEN}✔${R}  ollama $(ollama --version 2>/dev/null | head -1 || echo '(versão desconhecida)')"
else
    echo "${YELLOW}⚠  ollama não encontrado no PATH — verifique se está instalado.${R}"
fi

echo ""

# ── Preparar diretórios ───────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Montar comando do experimento ─────────────────────────────────────────────
CMD="cd '${SCRIPT_DIR}'"

# Ativar venv se existir
if [[ -f "${VENV_PATH}/bin/activate" ]]; then
    CMD="${CMD} && source '${VENV_PATH}/bin/activate'"
fi

# Comando base
CMD="${CMD} && a11y-autofix experiment '${CONFIG}'"

# Output dir
if [[ -n "$OUTPUT_DIR" ]]; then
    CMD="${CMD} --output '${OUTPUT_DIR}'"
fi

# Resume
if $RESUME; then
    # Detectar checkpoint dir a partir do output dir ou config padrão
    if [[ -n "$OUTPUT_DIR" ]]; then
        CKPT_DIR="${OUTPUT_DIR}/checkpoints"
    else
        # Extrair output_dir do yaml
        CKPT_DIR=$(grep -E '^output_dir:' "$CONFIG" | sed "s/output_dir: *//" | tr -d '"' | tr -d "'" || true)
        CKPT_DIR="${SCRIPT_DIR}/${CKPT_DIR}/checkpoints"
    fi
    if [[ -d "$CKPT_DIR" ]]; then
        CMD="${CMD} --resume '${CKPT_DIR}'"
        echo "${CYAN}↩  Retomando a partir de: ${CKPT_DIR}${R}"
    else
        echo "${YELLOW}⚠  --resume solicitado mas checkpoint dir não encontrado: ${CKPT_DIR}${R}"
        echo "   Iniciando do zero..."
    fi
fi

# Logging ao arquivo (tee para ver no tmux E salvar)
CMD="${CMD} 2>&1 | tee '${LOG_FILE}'"

# Mensagem de conclusão
CMD="${CMD}; echo ''; echo '${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}'; echo '${GREEN}  Experimento finalizado.  $(date)${R}'; echo '${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}'; read -p 'Pressione Enter para sair...'"

# ── Sessão tmux ───────────────────────────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "${YELLOW}⚠  Sessão tmux '${SESSION}' já existe.${R}"
    echo ""
    echo "  Opções:"
    echo "  ${CYAN}[1]${R} Reconectar (sem reiniciar o experimento)"
    echo "  ${CYAN}[2]${R} Matar e reiniciar"
    echo "  ${CYAN}[3]${R} Cancelar"
    echo ""
    read -r -p "  Escolha [1/2/3]: " choice
    case "$choice" in
        1) exec tmux attach -t "$SESSION" ;;
        2) tmux kill-session -t "$SESSION" ;;
        3) echo "Cancelado."; exit 0 ;;
        *) echo "Escolha inválida. Reconectando..."; exec tmux attach -t "$SESSION" ;;
    esac
fi

# Criar nova sessão (desanexada)
echo "${CYAN}  Criando sessão tmux '${SESSION}'...${R}"
tmux new-session -d -s "$SESSION" -n "$WINDOW" -x 220 -y 50

# Configurar tmux: histórico generoso, status bar informativa
tmux set-option -t "$SESSION" history-limit 50000
tmux set-option -t "$SESSION" status on
tmux set-option -t "$SESSION" status-right "#[fg=cyan]%H:%M  #[fg=green]a11y-exp"
tmux set-option -t "$SESSION" status-right-length 40

# ── Painel de monitoramento (opcional) ───────────────────────────────────────
if $OPEN_WATCH; then
    # Dividir horizontalmente: topo = experimento, baixo = watch
    tmux split-window -t "${SESSION}:${WINDOW}" -v -p 30
    tmux send-keys -t "${SESSION}:${WINDOW}.1" \
        "sleep 5 && cd '${SCRIPT_DIR}' && ${PYTHON} watch_experiment.py --interval 4" Enter
    # Foco no painel principal
    tmux select-pane -t "${SESSION}:${WINDOW}.0"
fi

# ── Enviar comando ao painel principal ───────────────────────────────────────
tmux send-keys -t "${SESSION}:${WINDOW}.0" "$CMD" Enter

# ── Resumo e instruções ───────────────────────────────────────────────────────
echo ""
echo "${GREEN}✔  Experimento iniciado em background (tmux).${R}"
echo ""
echo "${BOLD}  Comandos úteis:${R}"
echo "  ${CYAN}tmux attach -t ${SESSION}${R}                  — reconectar ao experimento"
echo "  ${CYAN}tmux detach${R}  (dentro do tmux: Ctrl+B d)   — desanexar sem parar"
echo "  ${CYAN}python watch_experiment.py${R}                 — dashboard ao vivo"
echo "  ${CYAN}tail -f ${LOG_FILE}${R}"
echo "                                             — log em tempo real"
echo ""
echo "  ${DIM}Para parar o experimento:  tmux attach -t ${SESSION}  e  Ctrl+C${R}"
echo ""

# ── Anexar agora? ─────────────────────────────────────────────────────────────
read -r -t 10 -p "  Conectar ao tmux agora? [S/n] " yn || yn="s"
echo ""
case "${yn,,}" in
    n|no|não|nao) echo "${DIM}  OK. Use 'tmux attach -t ${SESSION}' para reconectar.${R}" ;;
    *)            exec tmux attach -t "$SESSION" ;;
esac
