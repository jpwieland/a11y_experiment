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

# ── Localizar Python base (≥3.9) ──────────────────────────────────────────────
_find_python() {
    for candidate in \
        "$(command -v python3.12 2>/dev/null)" \
        "$(command -v python3.11 2>/dev/null)" \
        "$(command -v python3.10 2>/dev/null)" \
        "$(command -v python3.9 2>/dev/null)" \
        "$(command -v python3 2>/dev/null)" \
        "$(command -v python 2>/dev/null)"; do
        [[ -z "$candidate" || ! -x "$candidate" ]] && continue
        local ver
        ver=$("$candidate" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null)
        [[ "$ver" == "True" ]] && { echo "$candidate"; return 0; }
    done
    return 1
}

PYTHON_BASE="$(_find_python)" || {
    echo "${RED}✘  Python ≥3.9 não encontrado no PATH.${R}"; exit 1
}

# ── Helper: baixar arquivo com curl ou wget ───────────────────────────────────
_download() {
    local url="$1" dest="$2"
    if command -v curl &>/dev/null; then
        curl -fsSL --retry 3 "$url" -o "$dest" 2>/dev/null
    elif command -v wget &>/dev/null; then
        wget -q --tries=3 "$url" -O "$dest" 2>/dev/null
    else
        return 1
    fi
    [[ -s "$dest" ]]
}

# ── Setup do ambiente virtual (sem root, sem conda, sem python3-venv) ─────────
#
# Estratégias em cascata:
#  A) virtualenv.pyz  — zipapp autossuficiente do pypa, não precisa de pip nem venv
#  B) pip --user virtualenv  — se pip existir no sistema
#  C) pip --user direto  — sem env isolado, instala em ~/.local
#  D) PYTHONPATH manual  — último recurso absoluto
# ──────────────────────────────────────────────────────────────────────────────
setup_venv() {
    if [[ -x "${VENV_PATH}/bin/a11y-autofix" ]]; then
        return 0  # já configurado
    fi

    echo ""
    echo "${YELLOW}  Configurando ambiente Python (sem root)...${R}"
    echo "  Python base: ${PYTHON_BASE} ($("$PYTHON_BASE" --version 2>&1))"
    echo ""

    mkdir -p "${VENV_PATH}/bin"
    local pip_in_venv="${VENV_PATH}/bin/pip"

    # ── A) virtualenv.pyz — não depende de venv/ensurepip do sistema ─────────
    if [[ ! -f "${VENV_PATH}/bin/python" ]]; then
        echo "  [A] virtualenv.pyz (autossuficiente) ..."
        local venv_pyz
        venv_pyz="$(mktemp /tmp/virtualenv-XXXX.pyz)"
        if _download "https://bootstrap.pypa.io/virtualenv.pyz" "$venv_pyz"; then
            "$PYTHON_BASE" "$venv_pyz" -q "${VENV_PATH}" 2>&1 \
                && echo "      ${GREEN}✔ env criado${R}" \
                || echo "      ${YELLOW}✘ falhou${R}"
            rm -f "$venv_pyz"
        else
            echo "      ${YELLOW}✘ download falhou (sem rede?)${R}"
            rm -f "$venv_pyz"
        fi
    fi

    # ── B) pip install --user virtualenv (se pip disponível no sistema) ───────
    if [[ ! -f "${VENV_PATH}/bin/python" ]]; then
        echo "  [B] pip --user virtualenv ..."
        local pip3_bin
        pip3_bin="$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null || true)"

        # Tentar via -m pip se não encontrou pip direto
        if [[ -z "$pip3_bin" ]]; then
            "$PYTHON_BASE" -m pip --version &>/dev/null && pip3_bin="$PYTHON_BASE -m pip"
        fi

        if [[ -n "$pip3_bin" ]]; then
            $pip3_bin install --quiet --user virtualenv 2>&1 | tail -2 || true
            local venv_bin="${HOME}/.local/bin/virtualenv"
            if [[ -x "$venv_bin" ]]; then
                "$venv_bin" -q -p "$PYTHON_BASE" "${VENV_PATH}" 2>&1 \
                    && echo "      ${GREEN}✔ env criado${R}" \
                    || echo "      ${YELLOW}✘ falhou${R}"
            fi
        else
            echo "      ${YELLOW}✘ pip não encontrado no sistema${R}"
        fi
    fi

    # ── Instalar pacote no env criado (A ou B) ────────────────────────────────
    if [[ -f "${VENV_PATH}/bin/python" ]] && [[ -f "$pip_in_venv" ]]; then
        echo "  Instalando a11y-autofix no env ..."
        "$pip_in_venv" install --quiet --upgrade pip 2>/dev/null || true
        "$pip_in_venv" install --quiet -e "${SCRIPT_DIR}" \
            && echo "  ${GREEN}✔ instalado com sucesso${R}" \
            || { echo "${RED}  ✘ pip install -e falhou${R}"; exit 1; }

    # ── C) Sem env isolado: pip install --user diretamente ────────────────────
    elif [[ ! -x "${VENV_PATH}/bin/a11y-autofix" ]]; then
        echo "  [C] Sem env isolado — pip install --user ..."
        echo "  ${DIM}(pacotes em ~/.local, pode conflitar com outros projetos)${R}"

        local sys_pip=""
        if "$PYTHON_BASE" -m pip --version &>/dev/null; then
            sys_pip="$PYTHON_BASE -m pip"
        elif command -v pip3 &>/dev/null; then
            sys_pip="pip3"
        fi

        if [[ -z "$sys_pip" ]]; then
            # ── D) Último recurso: instalar pip via get-pip.py ─────────────────
            echo "  [D] Bootstrapping pip via get-pip.py ..."
            local getpip
            getpip="$(mktemp /tmp/get-pip-XXXX.py)"
            if _download "https://bootstrap.pypa.io/get-pip.py" "$getpip"; then
                "$PYTHON_BASE" "$getpip" --user --quiet 2>&1 | tail -3 \
                    && sys_pip="$PYTHON_BASE -m pip" \
                    || echo "      ${YELLOW}✘ get-pip.py falhou${R}"
            fi
            rm -f "$getpip"
        fi

        if [[ -z "$sys_pip" ]]; then
            echo "${RED}  ✘  Nenhum pip disponível. Impossível instalar.${R}"
            echo ""
            echo "  Solicite ao administrador:"
            echo "    sudo apt-get install python3-pip python3-venv"
            exit 1
        fi

        $sys_pip install --quiet --user -e "${SCRIPT_DIR}" \
            || { echo "${RED}  ✘ pip install --user falhou${R}"; exit 1; }

        # Wrapper Python → sistema
        cat > "${VENV_PATH}/bin/python" <<PYWRAPPER
#!/usr/bin/env bash
exec "$PYTHON_BASE" "\$@"
PYWRAPPER
        chmod +x "${VENV_PATH}/bin/python"

        # Localizar binário instalado
        local user_bin
        user_bin="$("$PYTHON_BASE" -m site --user-base 2>/dev/null)/bin/a11y-autofix"
        if [[ -x "$user_bin" ]]; then
            ln -sf "$user_bin" "${VENV_PATH}/bin/a11y-autofix"
        else
            # Wrapper direto como último recurso
            cat > "${VENV_PATH}/bin/a11y-autofix" <<WRAPPER
#!/usr/bin/env bash
export PYTHONPATH="${SCRIPT_DIR}\${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BASE" -m a11y_autofix.cli "\$@"
WRAPPER
            chmod +x "${VENV_PATH}/bin/a11y-autofix"
        fi
    fi

    # ── Verificação final ─────────────────────────────────────────────────────
    if [[ ! -x "${VENV_PATH}/bin/a11y-autofix" ]]; then
        echo "${RED}  ✘  Falha ao configurar ambiente após todas as tentativas.${R}"
        exit 1
    fi

    echo "${GREEN}  ✔  Ambiente pronto.${R}"
}

setup_venv

# Caminhos absolutos — nunca dependem de PATH ou source activate
PYTHON="${VENV_PATH}/bin/python"
A11Y_BIN="${VENV_PATH}/bin/a11y-autofix"

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
echo "${GREEN}✔${R}  Python:    $($PYTHON --version)"
echo "${GREEN}✔${R}  a11y-bin:  ${A11Y_BIN}"
echo "${GREEN}✔${R}  Config:    ${CONFIG}"

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
# Usa caminho absoluto — não depende de PATH nem de source activate
EXP_CMD="'${A11Y_BIN}' experiment '${CONFIG}'"

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
        "sleep 8 && '${PYTHON}' '${SCRIPT_DIR}/watch_experiment.py' --interval 4" Enter
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
