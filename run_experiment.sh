#!/usr/bin/env bash
# =============================================================================
#  run_experiment.sh — Executa o experimento LLM via venv (Linux, sem root)
#
#  Uso:
#    ./run_experiment.sh
#    ./run_experiment.sh --config experiments/experiment_strong_linux.yaml
#    ./run_experiment.sh --output experiment-results/meu_run_1
#    ./run_experiment.sh --skip-preflight
#    ./run_experiment.sh --vllm-port 8001   # porta alternativa do vLLM
#
#  Pré-requisito: ./setup_linux.sh já executado (cria o .venv)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

# ── Defaults ──────────────────────────────────────────────────────────────────
CONFIG="experiments/experiment_strong_linux.yaml"
OUTPUT=""
SKIP_PREFLIGHT=0
VLLM_PORT=8000

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)         CONFIG="$2";     shift 2 ;;
        --output)         OUTPUT="$2";     shift 2 ;;
        --skip-preflight) SKIP_PREFLIGHT=1; shift   ;;
        --vllm-port)      VLLM_PORT="$2";  shift 2 ;;
        *) echo "Argumento desconhecido: $1" >&2; exit 1 ;;
    esac
done

# ── Cores ─────────────────────────────────────────────────────────────────────
R='\033[0m'; BOLD='\033[1m'
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; GRAY='\033[0;37m'

# ── Verificar .venv ───────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo -e "${RED}[ERRO] .venv não encontrado em $SCRIPT_DIR/.venv${R}"
    echo ""
    echo "Execute primeiro:"
    echo -e "  ${CYAN}./setup_linux.sh${R}"
    exit 1
fi

# ── Verificar config ──────────────────────────────────────────────────────────
CONFIG_PATH="$SCRIPT_DIR/$CONFIG"
if [[ ! -f "$CONFIG_PATH" ]]; then
    echo -e "${RED}[ERRO] Config não encontrado: $CONFIG_PATH${R}"
    echo ""
    echo "Configs disponíveis:"
    find "$SCRIPT_DIR/experiments" -name "*.yaml" -printf "  experiments/%f\n" 2>/dev/null || \
        ls "$SCRIPT_DIR/experiments/"*.yaml 2>/dev/null | xargs -I{} basename {} | sed 's/^/  experiments\//'
    exit 1
fi

# ── Detectar backend LLM ──────────────────────────────────────────────────────
# Lê LLM_BACKEND do .env se existir
LLM_BACKEND="ollama"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    LLM_BACKEND=$(grep -E "^LLM_BACKEND=" "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 | xargs || echo "ollama")
fi

# ── Verificar / iniciar backend ───────────────────────────────────────────────
LOCAL_BIN="$HOME/.local/bin"
OLLAMA_BIN="${LOCAL_BIN}/ollama"
[[ -x "$OLLAMA_BIN" ]] || OLLAMA_BIN="$(command -v ollama 2>/dev/null || echo '')"

if [[ "$LLM_BACKEND" == "vllm" ]]; then
    echo -e "${CYAN}-->${R} Backend: vLLM"
    if ! curl -sf "http://localhost:${VLLM_PORT}/health" > /dev/null 2>&1; then
        echo -e "${YELLOW}[AVISO] vLLM não detectado em localhost:${VLLM_PORT}${R}"
        echo ""
        echo "Inicie o servidor vLLM antes de continuar:"
        echo -e "  ${CYAN}.venv/bin/python -m vllm.entrypoints.openai.api_server \\"
        echo -e "    --model Qwen/Qwen2.5-Coder-32B-Instruct \\"
        echo -e "    --port ${VLLM_PORT} \\"
        echo -e "    --tensor-parallel-size \$(nvidia-smi -L | wc -l)${R}"
        echo ""
        read -rp "Continuar mesmo assim? (s/N) " resp
        [[ "$resp" =~ ^[sS]$ ]] || exit 1
    else
        echo -e "  ${GREEN}[OK] vLLM rodando em localhost:${VLLM_PORT}${R}"
    fi
else
    echo -e "${CYAN}-->${R} Backend: Ollama"
    if ! curl -sf "http://localhost:11434/" > /dev/null 2>&1; then
        echo -e "${YELLOW}[AVISO] Ollama não detectado em localhost:11434${R}"
        if [[ -x "$OLLAMA_BIN" ]]; then
            echo -e "${CYAN}-->${R} Iniciando Ollama em background..."
            OLLAMA_MODELS="$HOME/.ollama/models" \
                nohup "$OLLAMA_BIN" serve > "$SCRIPT_DIR/ollama.log" 2>&1 &
            OLLAMA_PID=$!
            echo "$OLLAMA_PID" > "$SCRIPT_DIR/.ollama.pid"
            sleep 4
            if curl -sf "http://localhost:11434/" > /dev/null 2>&1; then
                echo -e "  ${GREEN}[OK] Ollama iniciado (PID $OLLAMA_PID)${R}"
            else
                echo -e "${YELLOW}[AVISO] Ollama pode estar iniciando — verifique ollama.log${R}"
            fi
        else
            echo ""
            echo "Inicie o Ollama antes de continuar:"
            echo -e "  ${CYAN}~/.local/bin/ollama serve${R}"
            echo ""
            read -rp "Continuar mesmo assim? (s/N) " resp
            [[ "$resp" =~ ^[sS]$ ]] || exit 1
        fi
    else
        echo -e "  ${GREEN}[OK] Ollama rodando${R}"
    fi
fi

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo -e "${CYAN}${BOLD}  a11y-autofix — Experimento LLM (Linux)${R}"
echo "========================================================"
echo "  Config  : $CONFIG"
echo "  Backend : $LLM_BACKEND"
echo "  Python  : $VENV_PYTHON"
echo "  Início  : $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo -e "  ${YELLOW}Para monitorar em outro terminal:${R}"
echo -e "  ${CYAN}./watch.sh${R}"
echo "========================================================"
echo ""

# ── Montar argumentos ────────────────────────────────────────────────────────
CMD_ARGS=("-m" "a11y_autofix.cli" "experiment" "run" "$CONFIG_PATH")

if [[ -n "$OUTPUT" ]]; then
    CMD_ARGS+=("--output" "$SCRIPT_DIR/$OUTPUT")
fi

if [[ $SKIP_PREFLIGHT -eq 1 ]]; then
    CMD_ARGS+=("--skip-preflight")
fi

# ── Recarregar PATH para nvm/npm/ollama ──────────────────────────────────────
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
export OLLAMA_MODELS="$HOME/.ollama/models"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Carregar .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env" 2>/dev/null || true
    set +a
fi

# ── Executar ──────────────────────────────────────────────────────────────────
echo -e "  ${GREEN}Iniciando...${R}"
echo ""

set +e
"$VENV_PYTHON" "${CMD_ARGS[@]}"
EXIT_CODE=$?
set -e

# ── Resultado ─────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "  ${GREEN}Experimento concluído com sucesso!${R}"
    echo "  Resultados em: experiment-results/"
    echo "  Monitorar: ./watch.sh --once"
else
    echo -e "  ${YELLOW}Experimento encerrado com código $EXIT_CODE${R}"
    echo "  Verifique os logs acima."
    echo "  Para retomar, execute o mesmo comando novamente."
fi
echo "========================================================"
echo ""

# ── Parar Ollama se foi iniciado por este script ──────────────────────────────
if [[ -f "$SCRIPT_DIR/.ollama.pid" ]]; then
    PID=$(cat "$SCRIPT_DIR/.ollama.pid")
    if kill -0 "$PID" 2>/dev/null; then
        read -rp "Parar Ollama (PID $PID)? (s/N) " resp
        [[ "$resp" =~ ^[sS]$ ]] && kill "$PID" && echo "Ollama parado." || true
    fi
    rm -f "$SCRIPT_DIR/.ollama.pid"
fi

exit $EXIT_CODE
