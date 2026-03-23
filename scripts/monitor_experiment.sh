#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# monitor_experiment.sh — Dashboard de acompanhamento do experimento
#
# Exibe em tempo real:
#   • Uso da GPU (VRAM, temperatura, utilização)
#   • Modelo ollama carregado
#   • Progresso do experimento (checkpoints)
#   • Logs recentes
#
# Uso:
#   bash scripts/monitor_experiment.sh              # dashboard completo
#   bash scripts/monitor_experiment.sh --gpu-only   # só GPU
#   bash scripts/monitor_experiment.sh --logs-only  # só logs
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DIR="/scratch/jpvbwieland/a11y_experiment"
RESULTS_DIR="$PROJECT_DIR/experiment-results/14b_comparison"
CHECKPOINTS_DIR="$RESULTS_DIR/checkpoints"
LOGS_DIR="/scratch/jpvbwieland/logs"
OLLAMA_LOG="$LOGS_DIR/ollama.log"

MODE="${1:-all}"
INTERVAL=5   # segundos entre atualizações

# ── Cores ────────────────────────────────────────────────────────────────────
R='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'; GREEN='\033[92m'
YELLOW='\033[93m'; RED='\033[91m'; CYAN='\033[96m'; BLUE='\033[94m'

clear_screen() {
    printf '\033[2J\033[H'
}

separator() {
    echo -e "${DIM}$(printf '─%.0s' $(seq 1 70))${R}"
}

# ── GPU Info ──────────────────────────────────────────────────────────────────
gpu_info() {
    echo -e "${BOLD}  GPU — NVIDIA RTX 4090${R}"
    separator
    if ! command -v nvidia-smi &>/dev/null; then
        echo -e "  ${RED}nvidia-smi não encontrado${R}"; return
    fi
    nvidia-smi \
        --query-gpu=name,driver_version,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit \
        --format=csv,noheader,nounits \
        | awk -F', ' '{
            printf "  Modelo    : %s\n", $1
            printf "  Driver    : %s\n", $2
            printf "  Temp      : %s°C\n", $3
            printf "  GPU util  : %s%%\n", $4
            printf "  MEM util  : %s%%\n", $5
            printf "  VRAM      : %s / %s MB  (%.1f%%)\n", $6, $7, ($6/$7)*100
            printf "  Potência  : %s / %s W\n", $8, $9
        }'
    echo ""
    echo -e "  ${CYAN}Processos na GPU:${R}"
    nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv,noheader \
        | awk -F', ' '{printf "    PID %-8s  %-30s  %s MB\n", $1, $2, $3}' \
        || echo "  (nenhum processo)"
}

# ── Ollama Status ─────────────────────────────────────────────────────────────
ollama_status() {
    echo -e "\n${BOLD}  Ollama${R}"
    separator
    if ! pgrep -x "ollama" &>/dev/null; then
        echo -e "  ${RED}✘ Ollama não está rodando${R}"
        echo -e "  Iniciar: nohup ~/bin/ollama serve > $OLLAMA_LOG 2>&1 &"
        return
    fi
    echo -e "  ${GREEN}✔ Rodando (PID $(pgrep -x ollama | head -1))${R}"

    # Modelo carregado no momento
    LOADED=$(curl -sf http://localhost:11434/api/ps 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  Modelo    : {m[\"name\"]} ({m.get(\"size_vram\",\"?\")} bytes VRAM)') for m in d.get('models',[])]" 2>/dev/null \
        || echo "  (sem modelo carregado)")
    echo "$LOADED"

    # Modelos disponíveis
    echo -e "  ${CYAN}Modelos disponíveis:${R}"
    ~/bin/ollama list 2>/dev/null | tail -n +2 | awk '{printf "    %-30s %s %s\n", $1, $3, $4}' \
        || echo "    (nenhum)"
}

# ── Progresso do Experimento ───────────────────────────────────────────────────
experiment_progress() {
    echo -e "\n${BOLD}  Progresso do Experimento${R}"
    separator

    if [ ! -d "$RESULTS_DIR" ]; then
        echo -e "  ${YELLOW}⚠${R}  Experimento ainda não iniciado"
        echo "    Iniciar: cd $PROJECT_DIR && source .venv/bin/activate"
        echo "             a11y-autofix experiment experiments/experiment_14b_comparison.yaml"
        return
    fi

    # Contar checkpoints por modelo
    MODELS=("qwen2.5-coder-14b" "deepseek-coder-v2-16b" "starcoder2-15b")
    echo -e "  ${CYAN}Checkpoints por modelo:${R}"

    TOTAL_DONE=0
    TOTAL_FILES=0
    for model in "${MODELS[@]}"; do
        count=0
        if [ -d "$CHECKPOINTS_DIR" ]; then
            count=$(find "$CHECKPOINTS_DIR" -name "*${model}*" -name "*.json" 2>/dev/null | wc -l)
        fi
        model_dir="$RESULTS_DIR/$model"
        files_done=0
        if [ -d "$model_dir" ]; then
            files_done=$(find "$model_dir" -name "*.json" 2>/dev/null | wc -l)
        fi
        TOTAL_DONE=$((TOTAL_DONE + files_done))

        # Status indicator
        if [ "$files_done" -gt 0 ]; then
            status="${GREEN}◉ em andamento${R}"
        else
            status="${DIM}○ aguardando${R}"
        fi
        printf "    %-28s  checkpoints: %-4s  arquivos: %-4s  %b\n" \
               "$model" "$count" "$files_done" "$status"
    done

    # Verificar experiment_result.json (experimento concluído)
    if [ -f "$RESULTS_DIR/experiment_result.json" ]; then
        echo ""
        echo -e "  ${GREEN}${BOLD}✔ Experimento concluído!${R}"
        python3 - <<'EOF' "$RESULTS_DIR/experiment_result.json" 2>/dev/null || true
import sys, json
with open(sys.argv[1]) as f:
    d = json.load(f)
print(f"  Modelos testados: {', '.join(d.get('models_tested', []))}")
print(f"  Arquivos processados: {d.get('files_processed', '?')}")
sr = d.get('success_rate_by_model', {})
at = d.get('avg_time_by_model', {})
ifix = d.get('issues_fixed_by_model', {})
print(f"\n  {'Modelo':<30} {'Taxa sucesso':>12} {'Issues fixadas':>15} {'Tempo médio':>12}")
print(f"  {'-'*30} {'-'*12} {'-'*15} {'-'*12}")
for m in d.get('models_tested', []):
    print(f"  {m:<30} {sr.get(m, 0):>11.1%} {ifix.get(m, 0):>15} {at.get(m, 0):>10.1f}s")
EOF
    fi
}

# ── Logs recentes ──────────────────────────────────────────────────────────────
recent_logs() {
    echo -e "\n${BOLD}  Logs recentes${R}"
    separator

    # Log do experimento (structlog)
    exp_log=$(find "$RESULTS_DIR" -name "*.log" 2>/dev/null | head -1)
    if [ -n "$exp_log" ] && [ -f "$exp_log" ]; then
        echo -e "  ${CYAN}Experimento ($exp_log):${R}"
        tail -10 "$exp_log" | sed 's/^/    /'
    fi

    # Log do ollama
    if [ -f "$OLLAMA_LOG" ]; then
        echo -e "\n  ${CYAN}Ollama (últimas 5 linhas):${R}"
        tail -5 "$OLLAMA_LOG" | sed 's/^/    /'
    fi
}

# ── Dashboard principal ────────────────────────────────────────────────────────
dashboard() {
    while true; do
        clear_screen
        TS=$(date '+%H:%M:%S')
        echo -e "${BOLD}════════════════════════════════════════════════════════════════════${R}"
        echo -e "${BOLD}  ♿  a11y-autofix — Monitor GPU Experiment     $TS${R}"
        echo -e "${BOLD}════════════════════════════════════════════════════════════════════${R}"

        gpu_info
        ollama_status
        experiment_progress
        recent_logs

        echo ""
        echo -e "${DIM}  Atualiza a cada ${INTERVAL}s | Ctrl+C para sair${R}"
        sleep "$INTERVAL"
    done
}

# ── Entry point ────────────────────────────────────────────────────────────────
case "$MODE" in
    --gpu-only)
        while true; do clear_screen; gpu_info; sleep "$INTERVAL"; done ;;
    --logs-only)
        while true; do clear_screen; recent_logs; sleep "$INTERVAL"; done ;;
    --progress-only)
        while true; do clear_screen; experiment_progress; sleep "$INTERVAL"; done ;;
    *)
        dashboard ;;
esac
