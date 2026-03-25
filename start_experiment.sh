#!/usr/bin/env bash
# start_experiment.sh — Inicia o experimento LLM resistente a desconexões SSH.
#
# NÃO REQUER ROOT. Usa em ordem de preferência:
#   1. screen   (pré-instalado na maioria dos servidores Linux)
#   2. tmux     (se já disponível no servidor)
#   3. dtach    (compilado localmente, ~30 segundos)
#   4. nohup    (universal, sem dependências extras)
#
# Uso:
#   ./start_experiment.sh                          # inicia com config padrão
#   ./start_experiment.sh --config experiments/experiment_14b_comparison.yaml
#   ./start_experiment.sh --output experiment-results/14b_run_2
#   ./start_experiment.sh --resume                 # retoma dos checkpoints
#   ./start_experiment.sh --attach                 # reconecta à sessão existente
#   ./start_experiment.sh --status                 # mostra estado sem reconectar
#   ./start_experiment.sh --kill                   # para o experimento
#
# Reconectar após desconexão SSH:
#   screen: screen -r a11y-exp
#   tmux:   tmux attach -t a11y-exp
#   nohup:  tail -f logs/experiment_current.log

set -euo pipefail

# ── Configurações ─────────────────────────────────────────────────────────────
SESSION="a11y-exp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${SCRIPT_DIR}/.venv"
LOG_DIR="${SCRIPT_DIR}/logs"
PID_FILE="${LOG_DIR}/experiment.pid"
MUXER_FILE="${LOG_DIR}/experiment.muxer"   # qual multiplexer está em uso
LOG_CURRENT="${LOG_DIR}/experiment_current.log"

CONFIG="${SCRIPT_DIR}/experiments/experiment_14b_comparison.yaml"
OUTPUT_DIR=""
RESUME=false
ATTACH_ONLY=false
SHOW_STATUS=false
KILL_SESSION=false
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
        --status|-s)   SHOW_STATUS=true; shift ;;
        --kill|-k)     KILL_SESSION=true; shift ;;
        --watch|-w)    OPEN_WATCH=true; shift ;;
        --help|-h)
            grep -E "^#( |$)" "$0" | sed 's/^# \{0,2\}//' | head -30
            exit 0
            ;;
        *) echo "${RED}Argumento desconhecido: $1${R}"; exit 1 ;;
    esac
done

mkdir -p "$LOG_DIR"

# ── Detectar Python ───────────────────────────────────────────────────────────
if [[ -f "${VENV_PATH}/bin/python" ]]; then
    PYTHON="${VENV_PATH}/bin/python"
    ACTIVATE="source '${VENV_PATH}/bin/activate' && "
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
    ACTIVATE=""
else
    echo "${RED}✘  Python3 não encontrado.${R}"; exit 1
fi

# ── Detectar multiplexer disponível ──────────────────────────────────────────
detect_muxer() {
    if command -v screen &>/dev/null; then echo "screen"
    elif command -v tmux &>/dev/null;  then echo "tmux"
    elif [[ -x "${SCRIPT_DIR}/.local/bin/dtach" ]]; then echo "dtach"
    elif command -v dtach &>/dev/null; then echo "dtach"
    else echo "nohup"
    fi
}

MUXER="$(detect_muxer)"

# ── Funções por multiplexer ───────────────────────────────────────────────────

session_exists() {
    case "$MUXER" in
        screen) screen -list 2>/dev/null | grep -q "${SESSION}" ;;
        tmux)   tmux has-session -t "$SESSION" 2>/dev/null ;;
        dtach)  [[ -S "/tmp/dtach_${SESSION}.sock" ]] ;;
        nohup)
            [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
            ;;
    esac
}

attach_session() {
    case "$MUXER" in
        screen) exec screen -r "$SESSION" ;;
        tmux)   exec tmux attach -t "$SESSION" ;;
        dtach)  exec dtach -a "/tmp/dtach_${SESSION}.sock" ;;
        nohup)
            echo "${CYAN}Modo nohup — sem sessão interativa.${R}"
            echo "  Log ao vivo: ${CYAN}tail -f '${LOG_CURRENT}'${R}"
            echo "  Monitor:     ${CYAN}python watch_experiment.py${R}"
            ;;
    esac
}

kill_session() {
    case "$MUXER" in
        screen) screen -S "$SESSION" -X quit 2>/dev/null && echo "${GREEN}✔  Sessão screen encerrada.${R}" ;;
        tmux)   tmux kill-session -t "$SESSION" 2>/dev/null && echo "${GREEN}✔  Sessão tmux encerrada.${R}" ;;
        dtach)
            sock="/tmp/dtach_${SESSION}.sock"
            pid_file="${LOG_DIR}/experiment.pid"
            if [[ -f "$pid_file" ]]; then
                kill "$(cat "$pid_file")" 2>/dev/null && echo "${GREEN}✔  Processo dtach encerrado.${R}"
            fi
            rm -f "$sock"
            ;;
        nohup)
            if [[ -f "$PID_FILE" ]]; then
                kill "$(cat "$PID_FILE")" 2>/dev/null && echo "${GREEN}✔  Processo nohup encerrado.${R}"
                rm -f "$PID_FILE"
            fi
            ;;
    esac
}

start_session() {
    local cmd="$1"
    case "$MUXER" in
        screen)
            screen -dmS "$SESSION" bash -c "$cmd"
            ;;
        tmux)
            tmux new-session -d -s "$SESSION" -x 220 -y 50
            tmux set-option -t "$SESSION" history-limit 50000
            tmux send-keys -t "${SESSION}" "$cmd" Enter
            ;;
        dtach)
            local sock="/tmp/dtach_${SESSION}.sock"
            dtach -n "$sock" bash -c "$cmd"
            ;;
        nohup)
            # Redireciona stdout+stderr para log, salva PID
            bash -c "$cmd" >> "$LOG_CURRENT" 2>&1 &
            echo $! > "$PID_FILE"
            ;;
    esac
    echo "$MUXER" > "$MUXER_FILE"
}

# ── Instalar dtach sem root (fallback automático) ─────────────────────────────
install_dtach_no_root() {
    echo "${YELLOW}  Tentando compilar dtach localmente (~30s)...${R}"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    local install_dir="${SCRIPT_DIR}/.local/bin"
    mkdir -p "$install_dir"

    (
        cd "$tmp_dir"
        if command -v wget &>/dev/null; then
            wget -q "https://github.com/crigler/dtach/archive/refs/tags/v0.9.tar.gz" -O dtach.tar.gz
        elif command -v curl &>/dev/null; then
            curl -fsSL "https://github.com/crigler/dtach/archive/refs/tags/v0.9.tar.gz" -o dtach.tar.gz
        else
            echo "${RED}  ✘  wget/curl não disponíveis.${R}"
            return 1
        fi
        tar xzf dtach.tar.gz
        cd dtach-0.9
        ./configure --prefix="${SCRIPT_DIR}/.local" >/dev/null 2>&1
        make -j"$(nproc)" >/dev/null 2>&1
        make install >/dev/null 2>&1
    ) && {
        rm -rf "$tmp_dir"
        export PATH="${install_dir}:$PATH"
        echo "${GREEN}  ✔  dtach instalado em ${install_dir}${R}"
        MUXER="dtach"
        return 0
    } || {
        rm -rf "$tmp_dir"
        echo "${YELLOW}  ✘  dtach falhou — usando nohup.${R}"
        MUXER="nohup"
        return 1
    }
}

# ── Modo: status ──────────────────────────────────────────────────────────────
if $SHOW_STATUS; then
    echo ""
    echo "${BOLD}  Status do experimento${R}"
    echo ""
    saved_muxer="$(cat "$MUXER_FILE" 2>/dev/null || echo "$MUXER")"
    if session_exists; then
        echo "  ${GREEN}●  Experimento em execução${R}  (${saved_muxer})"
    else
        echo "  ${DIM}○  Nenhuma sessão ativa${R}"
    fi
    if [[ -f "$LOG_CURRENT" ]]; then
        echo ""
        echo "  ${DIM}Últimas 5 linhas do log:${R}"
        tail -5 "$LOG_CURRENT" | sed 's/^/    /'
    fi
    echo ""
    exit 0
fi

# ── Modo: kill ────────────────────────────────────────────────────────────────
if $KILL_SESSION; then
    MUXER="$(cat "$MUXER_FILE" 2>/dev/null || echo "$MUXER")"
    kill_session
    exit 0
fi

# ── Modo: attach ──────────────────────────────────────────────────────────────
if $ATTACH_ONLY; then
    MUXER="$(cat "$MUXER_FILE" 2>/dev/null || echo "$MUXER")"
    if session_exists; then
        attach_session
    else
        echo "${RED}Nenhuma sessão ativa encontrada.${R}"
        exit 1
    fi
    exit 0
fi

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
echo "${BOLD}  ♿  a11y-autofix — Inicializador do Experimento LLM${R}"
echo "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
echo ""

# ── Verificar: se já tem sessão ativa ─────────────────────────────────────────
if session_exists; then
    echo "${YELLOW}⚠  Sessão '${SESSION}' já existe (${MUXER}).${R}"
    echo ""
    echo "  [1] Reconectar"
    echo "  [2] Matar e reiniciar"
    echo "  [3] Cancelar"
    echo ""
    read -r -p "  Escolha [1/2/3]: " choice
    case "$choice" in
        1) attach_session; exit 0 ;;
        2) kill_session; sleep 1 ;;
        *) echo "Cancelado."; exit 0 ;;
    esac
fi

# ── Verificar multiplexer — tentar instalar dtach se nada disponível ──────────
echo "${GREEN}✔${R}  Python: $($PYTHON --version)"
echo "${GREEN}✔${R}  Config: ${CONFIG}"

case "$MUXER" in
    screen) echo "${GREEN}✔${R}  Multiplexer: screen $(screen --version 2>/dev/null | head -1 | grep -oP '\d+\.\d+\S*' || echo '')" ;;
    tmux)   echo "${GREEN}✔${R}  Multiplexer: tmux $(tmux -V 2>/dev/null | cut -d' ' -f2 || echo '')" ;;
    dtach)  echo "${GREEN}✔${R}  Multiplexer: dtach" ;;
    nohup)
        echo "${YELLOW}⚠  screen/tmux não encontrados.${R}"
        read -r -t 10 -p "  Tentar compilar dtach sem root? [s/N] " yn || yn="n"
        case "${yn,,}" in
            s|y|sim|yes) install_dtach_no_root || true ;;
        esac
        if [[ "$MUXER" == "nohup" ]]; then
            echo "${YELLOW}  Usando nohup — o experimento roda em background.${R}"
            echo "  ${DIM}Para monitorar: tail -f ${LOG_CURRENT}${R}"
        fi
        ;;
esac

if ! command -v ollama &>/dev/null; then
    echo "${YELLOW}⚠  ollama não encontrado — verifique a instalação.${R}"
fi

if [[ ! -f "$CONFIG" ]]; then
    echo "${RED}✘  Config não encontrado: ${CONFIG}${R}"
    exit 1
fi

# ── Montar comando do experimento ─────────────────────────────────────────────
EXP_CMD="${ACTIVATE}a11y-autofix experiment '${CONFIG}'"

if [[ -n "$OUTPUT_DIR" ]]; then
    EXP_CMD="${EXP_CMD} --output '${OUTPUT_DIR}'"
fi

if $RESUME; then
    if [[ -n "$OUTPUT_DIR" ]]; then
        CKPT="${OUTPUT_DIR}/checkpoints"
    else
        CKPT=$(grep -E '^output_dir:' "$CONFIG" 2>/dev/null \
               | sed "s/output_dir: *//" | tr -d '"' | tr -d "'" || true)
        CKPT="${SCRIPT_DIR}/${CKPT}/checkpoints"
    fi
    if [[ -d "$CKPT" ]]; then
        EXP_CMD="${EXP_CMD} --resume '${CKPT}'"
        echo "${CYAN}↩  Retomando de: ${CKPT}${R}"
    else
        echo "${YELLOW}⚠  Checkpoint dir não encontrado (${CKPT}) — iniciando do zero.${R}"
    fi
fi

# Prefixo: cd para o diretório do projeto (essencial para paths relativos)
FULL_CMD="cd '${SCRIPT_DIR}' && ${EXP_CMD} 2>&1 | tee -a '${LOG_CURRENT}'"
FULL_CMD="${FULL_CMD}; echo ''; echo '=== Experimento finalizado em '$(date)' ==='"

# Modo watch: abrir watch_experiment.py junto (só funciona com screen/tmux)
if $OPEN_WATCH && [[ "$MUXER" == "screen" ]]; then
    # screen: criar 2 janelas
    FULL_CMD="${FULL_CMD}; exec bash"
fi

echo ""

# ── Iniciar sessão ────────────────────────────────────────────────────────────
start_session "$FULL_CMD"

# Para tmux com --watch: abrir painel inferior
if $OPEN_WATCH && [[ "$MUXER" == "tmux" ]] && command -v tmux &>/dev/null; then
    sleep 1
    tmux split-window -t "${SESSION}" -v -p 28
    tmux send-keys -t "${SESSION}.1" \
        "sleep 8 && cd '${SCRIPT_DIR}' && ${PYTHON} watch_experiment.py --interval 4" Enter
    tmux select-pane -t "${SESSION}.0"
fi

# ── Resumo ────────────────────────────────────────────────────────────────────
echo "${GREEN}✔  Experimento iniciado em background (${MUXER}).${R}"
echo ""
echo "${BOLD}  Comandos úteis:${R}"

case "$MUXER" in
    screen)
        echo "  ${CYAN}screen -r ${SESSION}${R}              — reconectar"
        echo "  ${CYAN}screen -d${R}  (dentro: Ctrl+A D)    — desanexar sem parar"
        echo "  ${CYAN}screen -S ${SESSION} -X quit${R}      — parar experimento"
        ;;
    tmux)
        echo "  ${CYAN}tmux attach -t ${SESSION}${R}         — reconectar"
        echo "  ${CYAN}Ctrl+B D${R}  (dentro do tmux)        — desanexar sem parar"
        echo "  ${CYAN}tmux kill-session -t ${SESSION}${R}   — parar experimento"
        ;;
    dtach)
        echo "  ${CYAN}dtach -a /tmp/dtach_${SESSION}.sock${R} — reconectar"
        echo "  ${CYAN}./start_experiment.sh --kill${R}          — parar experimento"
        ;;
    nohup)
        echo "  ${CYAN}tail -f '${LOG_CURRENT}'${R}"
        echo "                                 — log ao vivo"
        echo "  ${CYAN}./start_experiment.sh --kill${R}  — parar experimento"
        echo "  ${DIM}(nohup: sem janela interativa — use watch_experiment.py)${R}"
        ;;
esac

echo "  ${CYAN}python watch_experiment.py${R}        — dashboard ao vivo (qualquer sessão SSH)"
echo "  ${CYAN}./start_experiment.sh --status${R}    — checar estado"
echo ""

# ── Anexar agora? ─────────────────────────────────────────────────────────────
if [[ "$MUXER" != "nohup" ]]; then
    read -r -t 10 -p "  Conectar agora? [S/n] " yn || yn="s"
    echo ""
    case "${yn,,}" in
        n|no|não|nao) echo "${DIM}  OK. Experimento rodando em background.${R}" ;;
        *) attach_session ;;
    esac
else
    echo "${DIM}  Log iniciado. Use 'tail -f ${LOG_CURRENT}' para acompanhar.${R}"
fi
