#!/usr/bin/env bash
# =============================================================================
#  watch.sh — Monitor ao vivo do experimento LLM (Linux, sem root)
#
#  Uso:
#    ./watch.sh                        # busca automática, atualiza a cada 4s
#    ./watch.sh --dir experiment-results/meu_run_1
#    ./watch.sh --interval 5           # atualiza a cada 5s
#    ./watch.sh --once                 # imprime uma vez e sai
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
WATCH_SCRIPT="$SCRIPT_DIR/watch_experiment.py"

# ── Verificar .venv ───────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "[ERRO] .venv não encontrado. Execute ./setup_linux.sh primeiro."
    exit 1
fi

# ── Parse args ────────────────────────────────────────────────────────────────
DIR_ARG=""
INTERVAL=4
ONCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)      DIR_ARG="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        --once)     ONCE=1; shift ;;
        *) echo "Argumento desconhecido: $1" >&2; exit 1 ;;
    esac
done

# ── Montar argumentos ─────────────────────────────────────────────────────────
CMD_ARGS=("$WATCH_SCRIPT" "--interval" "$INTERVAL")

if [[ -n "$DIR_ARG" ]]; then
    CMD_ARGS=("$WATCH_SCRIPT" "$SCRIPT_DIR/$DIR_ARG" "--interval" "$INTERVAL")
fi

[[ $ONCE -eq 1 ]] && CMD_ARGS+=("--once")

# ── Variáveis de ambiente ─────────────────────────────────────────────────────
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# ── Executar ──────────────────────────────────────────────────────────────────
exec "$VENV_PYTHON" "${CMD_ARGS[@]}"
