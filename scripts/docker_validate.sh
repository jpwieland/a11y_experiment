#!/usr/bin/env bash
# ─── a11y-autofix — Validação paralela em contêineres Docker isolados ─────────
#
# Executa todos os estágios do pipeline em contêineres Docker isolados,
# com suporte automático a GPU (NVIDIA CUDA) quando disponível.
#
# Uso:
#   ./scripts/docker_validate.sh [OPÇÕES] [DIRETÓRIO_ALVO]
#
# Opções:
#   --target DIR        Diretório com componentes React a analisar (padrão: tests/fixtures)
#   --wcag LEVEL        Nível WCAG: A, AA, AAA (padrão: AA)
#   --model MODEL       Modelo LLM (padrão: qwen2.5-coder-7b)
#   --no-gpu            Forçar modo CPU mesmo com GPU disponível
#   --skip-build        Não rebuildar imagens Docker
#   --parallel N        Número de scans em paralelo (padrão: 4)
#   --output DIR        Diretório de saída (padrão: ./a11y-report)
#   --only-tests        Executar apenas os testes (unit + e2e)
#   --only-scan         Executar apenas o scan
#   --only-e2e          Executar apenas os testes E2E
#   --clean             Limpar contêineres e volumes antes de iniciar
#   --help              Exibir esta ajuda
#
# Exemplos:
#   ./scripts/docker_validate.sh
#   ./scripts/docker_validate.sh --target ./src --wcag AA --model qwen2.5-coder-14b
#   ./scripts/docker_validate.sh --only-tests --no-gpu
#   ./scripts/docker_validate.sh --only-e2e
#   ./scripts/docker_validate.sh --clean --parallel 8
#
# Saída:
#   a11y-report/
#   ├── report.json              — Relatório JSON completo (audit trail)
#   ├── report.html              — Relatório HTML visual
#   ├── coverage.json            — Cobertura de testes
#   ├── e2e/                     — Artefatos dos testes E2E
#   │   └── pipeline_execution_report.json
#   └── docker_validation_run.json — Relatório desta execução Docker
#
set -euo pipefail

# ─── Cores para output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ─── Defaults ─────────────────────────────────────────────────────────────────
TARGET_DIR="${PWD}/tests/fixtures"
WCAG_LEVEL="AA"
MODEL="qwen2.5-coder-7b"
USE_GPU="auto"   # auto, yes, no
SKIP_BUILD=false
PARALLEL=4
OUTPUT_DIR="${PWD}/a11y-report"
RUN_MODE="full"  # full, tests, scan, e2e
CLEAN=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
START_TIME=$(date +%s)

# ─── Parse de argumentos ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --target)   TARGET_DIR="$2"; shift 2 ;;
        --wcag)     WCAG_LEVEL="$2"; shift 2 ;;
        --model)    MODEL="$2"; shift 2 ;;
        --no-gpu)   USE_GPU="no"; shift ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --parallel) PARALLEL="$2"; shift 2 ;;
        --output)   OUTPUT_DIR="$2"; shift 2 ;;
        --only-tests) RUN_MODE="tests"; shift ;;
        --only-scan)  RUN_MODE="scan"; shift ;;
        --only-e2e)   RUN_MODE="e2e"; shift ;;
        --clean)    CLEAN=true; shift ;;
        --help)
            head -45 "${BASH_SOURCE[0]}" | tail -44
            exit 0
            ;;
        *)
            # Argumento posicional = diretório alvo
            TARGET_DIR="$1"
            shift
            ;;
    esac
done

# ─── Funções utilitárias ──────────────────────────────────────────────────────
log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}   $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_stage()   { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════${NC}"; \
                echo -e "${BOLD}${CYAN}  $*${NC}"; \
                echo -e "${BOLD}${CYAN}══════════════════════════════════════${NC}"; }

elapsed() {
    local end_time=$(date +%s)
    local elapsed=$((end_time - START_TIME))
    echo "$((elapsed / 60))m$((elapsed % 60))s"
}

# ─── Verificações de prerequisitos ───────────────────────────────────────────
log_stage "Verificando prerequisitos"

# Docker
if ! command -v docker &>/dev/null; then
    log_error "Docker não encontrado. Instale: https://docs.docker.com/get-docker/"
    exit 1
fi
log_success "Docker: $(docker --version | head -1)"

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    log_error "Docker Compose não encontrado."
    exit 1
fi
log_success "Compose: $($COMPOSE_CMD version | head -1)"

# ─── Detecção de GPU ──────────────────────────────────────────────────────────
GPU_AVAILABLE=false
GPU_INFO=""

if [[ "$USE_GPU" != "no" ]]; then
    if command -v nvidia-smi &>/dev/null; then
        if nvidia-smi --query-gpu=name,memory.total,driver_version \
           --format=csv,noheader 2>/dev/null | head -1 > /tmp/gpu_info.txt; then
            GPU_INFO=$(cat /tmp/gpu_info.txt)
            GPU_AVAILABLE=true
            log_success "GPU detectada: ${GPU_INFO}"
        fi
    fi

    # Verificar nvidia-container-toolkit
    if $GPU_AVAILABLE; then
        if ! docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 \
             nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
            log_warn "GPU detectada mas nvidia-container-toolkit não está configurado."
            log_warn "Rodando em modo CPU. Para GPU: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"
            GPU_AVAILABLE=false
        fi
    fi
fi

if $GPU_AVAILABLE && [[ "$USE_GPU" != "no" ]]; then
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.gpu.yml"
    log_success "Modo: GPU (CUDA)"
else
    COMPOSE_FILES="-f docker-compose.yml"
    log_info "Modo: CPU"
fi

# ─── Exportar variáveis para o compose ────────────────────────────────────────
export TARGET_DIR OUTPUT_DIR WCAG_LEVEL MODEL PARALLEL
mkdir -p "$OUTPUT_DIR" "$OUTPUT_DIR/e2e"

# ─── Limpeza (se solicitada) ───────────────────────────────────────────────────
if $CLEAN; then
    log_stage "Limpeza de contêineres e volumes"
    cd "$PROJECT_DIR"
    $COMPOSE_CMD $COMPOSE_FILES down -v --remove-orphans 2>/dev/null || true
    log_success "Limpeza concluída"
fi

# ─── Build das imagens ────────────────────────────────────────────────────────
if ! $SKIP_BUILD; then
    log_stage "Build das imagens Docker"
    cd "$PROJECT_DIR"

    BUILD_ARGS=""
    if $GPU_AVAILABLE; then
        BUILD_ARGS="--build-arg CUDA_VERSION=12.1.1"
    fi

    log_info "Buildando imagem base..."
    $COMPOSE_CMD $COMPOSE_FILES build --parallel $BUILD_ARGS
    log_success "Build concluído"
fi

# ─── Iniciar Ollama ───────────────────────────────────────────────────────────
log_stage "Iniciando backend LLM (Ollama)"
cd "$PROJECT_DIR"

$COMPOSE_CMD $COMPOSE_FILES up -d ollama
log_info "Aguardando Ollama ficar pronto..."
OLLAMA_READY=false
for i in $(seq 1 30); do
    if $COMPOSE_CMD $COMPOSE_FILES exec -T ollama ollama list &>/dev/null 2>&1; then
        OLLAMA_READY=true
        break
    fi
    sleep 2
done

if $OLLAMA_READY; then
    log_success "Ollama pronto"
else
    log_warn "Ollama não respondeu em tempo — continuando sem LLM"
fi

# ─── Função: executar stage com timeout e log ─────────────────────────────────
run_stage() {
    local stage_name="$1"
    local service="$2"
    local profile="$3"
    local timeout="${4:-300}"

    log_stage "$stage_name"
    local stage_start=$(date +%s)

    if timeout "$timeout" \
       $COMPOSE_CMD $COMPOSE_FILES \
           --profile "$profile" \
           run --rm "$service" \
           2>&1 | tee -a "$OUTPUT_DIR/docker_${service}.log"; then
        local stage_end=$(date +%s)
        log_success "$stage_name concluído em $((stage_end - stage_start))s"
        return 0
    else
        log_error "$stage_name falhou (timeout: ${timeout}s)"
        return 1
    fi
}

# ─── Rastreamento de resultados ───────────────────────────────────────────────
STAGES_PASSED=()
STAGES_FAILED=()

run_and_track() {
    local name="$1"
    shift
    if run_stage "$name" "$@"; then
        STAGES_PASSED+=("$name")
    else
        STAGES_FAILED+=("$name")
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# EXECUÇÃO DOS STAGES
# ═══════════════════════════════════════════════════════════════════════════════

cd "$PROJECT_DIR"

case "$RUN_MODE" in

# ─── Modo: apenas testes unitários ────────────────────────────────────────────
"tests")
    run_and_track "Testes Unitários" test-runner test 180
    ;;

# ─── Modo: apenas E2E ─────────────────────────────────────────────────────────
"e2e")
    run_and_track "Testes E2E" e2e-test-runner e2e 300
    ;;

# ─── Modo: apenas scan ────────────────────────────────────────────────────────
"scan")
    # Executar scanners em paralelo em contêineres isolados
    log_stage "Scan paralelo em contêineres isolados"

    declare -A SCANNER_PIDS=()

    for scanner in scanner-pa11y scanner-axe scanner-playwright scanner-eslint; do
        (
            $COMPOSE_CMD $COMPOSE_FILES \
                --profile scanner \
                run --rm "$scanner" \
                2>&1 > "$OUTPUT_DIR/docker_${scanner}.log"
        ) &
        SCANNER_PIDS[$scanner]=$!
        log_info "Scanner iniciado: $scanner (PID: ${SCANNER_PIDS[$scanner]})"
    done

    # Aguardar todos os scanners
    SCAN_FAILED=false
    for scanner in "${!SCANNER_PIDS[@]}"; do
        if wait "${SCANNER_PIDS[$scanner]}"; then
            log_success "Scanner concluído: $scanner"
            STAGES_PASSED+=("scan:$scanner")
        else
            log_error "Scanner falhou: $scanner"
            STAGES_FAILED+=("scan:$scanner")
            SCAN_FAILED=true
        fi
    done

    if ! $SCAN_FAILED; then
        log_success "Todos os scanners concluídos"
    fi
    ;;

# ─── Modo: full pipeline ──────────────────────────────────────────────────────
"full")
    # Stage 1: Testes unitários
    run_and_track "Stage 1: Testes Unitários" test-runner test 180

    # Stage 2: Testes E2E
    run_and_track "Stage 2: Testes E2E" e2e-test-runner e2e 300

    # Stage 3: Scan paralelo em contêineres isolados
    log_stage "Stage 3: Scan paralelo em contêineres isolados"

    declare -A SCANNER_PIDS=()
    for scanner in scanner-pa11y scanner-axe scanner-playwright scanner-eslint; do
        (
            $COMPOSE_CMD $COMPOSE_FILES \
                --profile scanner \
                run --rm "$scanner" \
                2>&1 > "$OUTPUT_DIR/docker_${scanner}.log"
        ) &
        SCANNER_PIDS[$scanner]=$!
        log_info "  → $scanner iniciado (PID: ${SCANNER_PIDS[$scanner]})"
    done

    for scanner in "${!SCANNER_PIDS[@]}"; do
        if wait "${SCANNER_PIDS[$scanner]}"; then
            STAGES_PASSED+=("scan:$scanner")
            log_success "  → $scanner OK"
        else
            STAGES_FAILED+=("scan:$scanner")
            log_error "  → $scanner FALHOU"
        fi
    done

    # Stage 4: Orquestrador de scan (protocolo científico)
    run_and_track "Stage 4: Orquestrador + Protocolo Científico" \
        scanner-orchestrator orchestrator 300

    # Stage 5: Pipeline de correção (dry-run)
    run_and_track "Stage 5: Pipeline de Correção (dry-run)" \
        pipeline pipeline 600

    # Stage 6: Validação de patches
    run_and_track "Stage 6: Validação 4-Camadas" \
        validator validator 120

    # Stage 7: Geração de relatórios
    run_and_track "Stage 7: Geração de Relatórios" \
        reporter reporter 60
    ;;

*)
    log_error "Modo desconhecido: $RUN_MODE"
    exit 1
    ;;
esac

# ─── Relatório final ──────────────────────────────────────────────────────────
log_stage "Relatório de Execução Docker"
END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))

echo ""
echo -e "${BOLD}Resultado:${NC}"
echo -e "  Tempo total: ${BOLD}${TOTAL_TIME}s ($(elapsed))${NC}"
echo -e "  Modo: ${BOLD}$RUN_MODE${NC}"
echo -e "  GPU: ${BOLD}$(if $GPU_AVAILABLE; then echo "sim ($GPU_INFO)"; else echo "não (CPU)"; fi)${NC}"
echo ""

if [[ ${#STAGES_PASSED[@]} -gt 0 ]]; then
    echo -e "${GREEN}Stages bem-sucedidos (${#STAGES_PASSED[@]}):${NC}"
    for stage in "${STAGES_PASSED[@]}"; do
        echo -e "  ${GREEN}✓${NC} $stage"
    done
fi

if [[ ${#STAGES_FAILED[@]} -gt 0 ]]; then
    echo -e "\n${RED}Stages com falha (${#STAGES_FAILED[@]}):${NC}"
    for stage in "${STAGES_FAILED[@]}"; do
        echo -e "  ${RED}✗${NC} $stage"
    done
fi

# Gerar JSON de execução
EXECUTION_JSON="$OUTPUT_DIR/docker_validation_run.json"
cat > "$EXECUTION_JSON" << JSONEOF
{
  "execution_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "total_time_seconds": $TOTAL_TIME,
  "run_mode": "$RUN_MODE",
  "gpu_enabled": $GPU_AVAILABLE,
  "gpu_info": "$GPU_INFO",
  "wcag_level": "$WCAG_LEVEL",
  "model": "$MODEL",
  "parallel_scans": $PARALLEL,
  "stages_passed": [$(printf '"%s",' "${STAGES_PASSED[@]}" | sed 's/,$//')]
  ,
  "stages_failed": [$(printf '"%s",' "${STAGES_FAILED[@]}" | sed 's/,$//' )]
  ,
  "success_count": ${#STAGES_PASSED[@]},
  "failure_count": ${#STAGES_FAILED[@]},
  "output_dir": "$OUTPUT_DIR"
}
JSONEOF

echo ""
log_success "Relatório salvo em: $EXECUTION_JSON"
log_success "Artefatos em: $OUTPUT_DIR"

# Exit code baseado em falhas
if [[ ${#STAGES_FAILED[@]} -gt 0 ]]; then
    echo ""
    log_error "Pipeline concluído com ${#STAGES_FAILED[@]} falha(s)"
    exit 1
else
    echo ""
    log_success "Pipeline concluído com sucesso em $(elapsed)"
    exit 0
fi
