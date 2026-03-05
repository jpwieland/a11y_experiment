#!/usr/bin/env bash
# ============================================================
#  a11y-autofix — Coleta do Dataset
#
#  Executa o pipeline completo de coleta em sequência:
#    1. discover   — busca projetos no GitHub (requer GITHUB_TOKEN)
#    2. snapshot   — clona repos e registra commit SHA
#    3. scan       — escaneia acessibilidade com pa11y/axe/lighthouse
#    4. annotate   — aceita achados com consenso ≥2 (auto)
#                    + anotação manual (se --annotator for fornecido)
#    5. validate   — verifica métricas QM1-QM8
#    6. profile    — gera dataset_profile.json
#
#  Checkpoint: o status de cada projeto (PENDING → SNAPSHOTTED →
#  SCANNED → ANNOTATED) persiste no catalog. Se a coleta for
#  interrompida, reexecutar retoma do ponto onde parou.
#
#  Uso:
#    bash collect.sh                           # pipeline completo
#    bash collect.sh --phase snapshot          # só uma fase
#    bash collect.sh --from scan               # a partir de uma fase
#    bash collect.sh --annotator alice         # ativa anotação manual
#    bash collect.sh --dry-run                 # simula sem executar
#    bash collect.sh --status                  # mostra estado atual
#
#  Variáveis de ambiente:
#    GITHUB_TOKEN   Personal Access Token (obrigatório para discover)
#    ANNOTATOR_ID   Alternativa a --annotator
# ============================================================
set -euo pipefail
IFS=$'\n\t'

# ── Flags ─────────────────────────────────────────────────────────────────────
PHASE=""           # vazio = todas as fases; ou: discover|snapshot|scan|annotate|validate|profile
FROM_PHASE=""      # executar a partir desta fase
TOKEN="${GITHUB_TOKEN:-}"
ANNOTATOR="${ANNOTATOR_ID:-}"
WORKERS=2
SCAN_TIMEOUT=60
DRY_RUN=false
SHOW_STATUS=false

for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=true ;;
        --status)       SHOW_STATUS=true ;;
        *)              ;;
    esac
done

# Parsing de argumentos com valor (--key value)
i=0; args=("$@")
while [ $i -lt ${#args[@]} ]; do
    case "${args[$i]}" in
        --phase)     i=$((i+1)); PHASE="${args[$i]:-}" ;;
        --from)      i=$((i+1)); FROM_PHASE="${args[$i]:-}" ;;
        --token)     i=$((i+1)); TOKEN="${args[$i]:-}" ;;
        --annotator) i=$((i+1)); ANNOTATOR="${args[$i]:-}" ;;
        --workers)   i=$((i+1)); WORKERS="${args[$i]:-2}" ;;
        --timeout)   i=$((i+1)); SCAN_TIMEOUT="${args[$i]:-60}" ;;
    esac
    i=$((i+1))
done

# ── Caminhos ──────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
CATALOG="$PROJECT_ROOT/dataset/catalog/projects.yaml"
RESULTS_DIR="$PROJECT_ROOT/dataset/results"
LOG_FILE="$PROJECT_ROOT/dataset/collect.log"
ENV_FILE="$PROJECT_ROOT/.env"

# ── Cores ─────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

info()    { echo -e "${BLUE}  →${NC} $*" | tee -a "$LOG_FILE"; }
ok()      { echo -e "${GREEN}  ✓${NC} $*" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}  ⚠${NC} $*" | tee -a "$LOG_FILE"; }
fail()    { echo -e "${RED}  ✗${NC} $*" | tee -a "$LOG_FILE"; }
section() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}" | tee -a "$LOG_FILE"; }
die()     { echo -e "\n${RED}${BOLD}ERRO:${NC} $*" | tee -a "$LOG_FILE"; exit 1; }

# ── Verificações iniciais ─────────────────────────────────────────────────────
[ -f "$VENV_PYTHON" ] || die ".venv não encontrado — execute primeiro: bash setup.sh"
[ -f "$CATALOG"     ] || die "Catálogo não encontrado: $CATALOG"

# Carregar GITHUB_TOKEN do .env se não estiver no ambiente
if [ -f "$ENV_FILE" ] && [ -z "$TOKEN" ]; then
    TOKEN=$(grep -E '^GITHUB_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" || true)
fi

mkdir -p "$RESULTS_DIR" "$(dirname "$LOG_FILE")"

# ── Python helper: contar projetos por status ─────────────────────────────────
count_status() {
    "$VENV_PYTHON" - "$CATALOG" <<'PYEOF' 2>/dev/null || echo "  (catálogo vazio)"
import sys, yaml
from collections import Counter
data = yaml.safe_load(open(sys.argv[1])) or {}
projects = data.get("projects", [])
c = Counter(p.get("status","?") for p in projects)
total = len(projects)
if not total:
    print("  (nenhum projeto no catálogo)")
else:
    for status, n in sorted(c.items()):
        bar = "█" * min(n, 30)
        print(f"    {status:<15} {n:>3}  {bar}")
    print(f"    {'TOTAL':<15} {total:>3}")
PYEOF
}

# ── Banner ────────────────────────────────────────────────────────────────────
: > "$LOG_FILE"
echo -e "\n${BOLD}${CYAN}♿ a11y-autofix — Coleta do Dataset${NC}" | tee -a "$LOG_FILE"
echo "══════════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo "Projeto  : $PROJECT_ROOT"  | tee -a "$LOG_FILE"
echo "Catálogo : $CATALOG"       | tee -a "$LOG_FILE"
echo "Log      : $LOG_FILE"      | tee -a "$LOG_FILE"
echo "Data     : $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
[ "$DRY_RUN" = "true" ] && echo -e "${YELLOW}${BOLD}MODO SIMULAÇÃO — nada será executado${NC}" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── --status: só mostra estado atual e sai ────────────────────────────────────
if [ "$SHOW_STATUS" = "true" ]; then
    section "Estado Atual do Catálogo"
    count_status
    exit 0
fi

# ── Determinar quais fases executar ──────────────────────────────────────────
ALL_PHASES=(discover snapshot scan annotate validate profile)

if [ -n "$PHASE" ]; then
    PHASES_TO_RUN=("$PHASE")
elif [ -n "$FROM_PHASE" ]; then
    PHASES_TO_RUN=()
    found=false
    for p in "${ALL_PHASES[@]}"; do
        [ "$p" = "$FROM_PHASE" ] && found=true
        [ "$found" = "true" ] && PHASES_TO_RUN+=("$p")
    done
    [ ${#PHASES_TO_RUN[@]} -eq 0 ] && die "Fase desconhecida: $FROM_PHASE  (válidas: ${ALL_PHASES[*]})"
else
    PHASES_TO_RUN=("${ALL_PHASES[@]}")
fi

# ── Validar fase conhecida ────────────────────────────────────────────────────
for p in "${PHASES_TO_RUN[@]}"; do
    valid=false
    for q in "${ALL_PHASES[@]}"; do [ "$p" = "$q" ] && valid=true && break; done
    [ "$valid" = "false" ] && die "Fase desconhecida: $p  (válidas: ${ALL_PHASES[*]})"
done

# ── Função: executar script Python ───────────────────────────────────────────
run_phase() {
    local label="$1"; shift
    if [ "$DRY_RUN" = "true" ]; then
        echo -e "  ${YELLOW}DRY-RUN:${NC} $VENV_PYTHON $*" | tee -a "$LOG_FILE"
        return 0
    fi
    echo -e "  ${BLUE}\$${NC} $VENV_PYTHON $*" | tee -a "$LOG_FILE"
    "$VENV_PYTHON" "$@" 2>&1 | tee -a "$LOG_FILE"
}

# ── Função: verificar se há projetos para uma fase ───────────────────────────
has_projects_at() {
    local status="$1"
    "$VENV_PYTHON" - "$CATALOG" "$status" <<'PYEOF' 2>/dev/null
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
n = sum(1 for p in data.get("projects",[]) if p.get("status") == sys.argv[2])
sys.exit(0 if n > 0 else 1)
PYEOF
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 1 — DISCOVER
# ─────────────────────────────────────────────────────────────────────────────
run_discover() {
    section "1/6 · Discover — busca no GitHub"

    if [ -z "$TOKEN" ]; then
        die "GITHUB_TOKEN não definido.
  Defina no ambiente: export GITHUB_TOKEN=ghp_...
  Ou adicione ao .env: GITHUB_TOKEN=ghp_...
  Ou passe como flag: --token ghp_..."
    fi

    info "Buscando projetos React/TypeScript com ≥100 stars em 7 domínios..."
    run_phase "discover" \
        dataset/scripts/discover.py \
        --token "$TOKEN" \
        --output "$CATALOG" \
        || { warn "Discovery terminou com erro — verifique o log"; return 1; }

    ok "Discovery concluída"
    echo -e "\n  Status após discovery:" | tee -a "$LOG_FILE"
    count_status
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 2 — SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────
run_snapshot() {
    section "2/6 · Snapshot — clone e pin de commit"

    if ! has_projects_at "pending" 2>/dev/null; then
        warn "Nenhum projeto com status 'pending' — fase ignorada"
        warn "  (projetos já foram clonados ou catálogo está vazio)"
        return 0
    fi

    info "Clonando repos e registrando SHA... (workers=$WORKERS)"
    run_phase "snapshot" \
        dataset/scripts/snapshot.py \
        --catalog "$CATALOG" \
        --workers "$WORKERS" \
        || { warn "Snapshot terminou com erro — verifique o log"; return 1; }

    ok "Snapshot concluído"
    echo -e "\n  Status após snapshot:" | tee -a "$LOG_FILE"
    count_status
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 3 — SCAN
# ─────────────────────────────────────────────────────────────────────────────
run_scan() {
    section "3/6 · Scan — pa11y + axe-core + lighthouse"

    if ! has_projects_at "snapshotted" 2>/dev/null; then
        warn "Nenhum projeto com status 'snapshotted' — fase ignorada"
        return 0
    fi

    info "Escaneando acessibilidade... (workers=$WORKERS, timeout=${SCAN_TIMEOUT}s)"
    run_phase "scan" \
        dataset/scripts/scan.py \
        --catalog "$CATALOG" \
        --workers "$WORKERS" \
        --timeout "$SCAN_TIMEOUT" \
        || { warn "Scan terminou com erro — verifique o log"; return 1; }

    ok "Scan concluído"
    echo -e "\n  Status após scan:" | tee -a "$LOG_FILE"
    count_status
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 4 — ANNOTATE
# ─────────────────────────────────────────────────────────────────────────────
run_annotate() {
    section "4/6 · Annotate — ground truth"

    if ! has_projects_at "scanned" 2>/dev/null; then
        warn "Nenhum projeto com status 'scanned' — fase ignorada"
        return 0
    fi

    # Passo 4a: auto-aceitar achados com consenso ≥2 (não-interativo)
    info "Auto-aceitando achados com consenso ≥2 (não-interativo)..."
    run_phase "annotate-auto" \
        dataset/scripts/annotate.py \
        --catalog "$CATALOG" \
        --auto-accept-only

    # Passo 4b: anotação manual (só se --annotator for fornecido)
    if [ -n "$ANNOTATOR" ]; then
        info "Anotação manual — pass 1 (annotator: $ANNOTATOR)"
        warn "Modo interativo iniciando — responda C=Confirmado / F=Falso positivo / U=Incerto"
        run_phase "annotate-pass1" \
            dataset/scripts/annotate.py \
            --catalog "$CATALOG" \
            --annotator "$ANNOTATOR" \
            --pass 1
    else
        warn "Anotação manual ignorada — sem --annotator"
        info "  Para anotar manualmente: bash collect.sh --phase annotate --annotator <seu-nome>"
    fi

    ok "Anotação concluída"
    echo -e "\n  Status após anotação:" | tee -a "$LOG_FILE"
    count_status
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 5 — VALIDATE
# ─────────────────────────────────────────────────────────────────────────────
run_validate() {
    section "5/6 · Validate — métricas QM1-QM8"

    info "Verificando qualidade do dataset..."
    run_phase "validate" \
        dataset/scripts/validate.py \
        --catalog "$CATALOG"

    # Não usa --strict aqui para não abortar o pipeline;
    # o relatório JSON terá os detalhes dos checks que passaram/falharam.
    ok "Validação concluída — veja: dataset/results/dataset_validation_report.json"
}

# ─────────────────────────────────────────────────────────────────────────────
#  FASE 6 — PROFILE
# ─────────────────────────────────────────────────────────────────────────────
run_profile() {
    section "6/6 · Profile — estatísticas do dataset"

    run_phase "profile" \
        dataset/scripts/describe_dataset.py \
        --catalog "$CATALOG"

    ok "Perfil gerado — veja: dataset/results/dataset_profile.json"
}

# ─────────────────────────────────────────────────────────────────────────────
#  STATUS INICIAL
# ─────────────────────────────────────────────────────────────────────────────
section "Estado do Catálogo"
count_status

# ─────────────────────────────────────────────────────────────────────────────
#  EXECUTAR FASES
# ─────────────────────────────────────────────────────────────────────────────
FAILED_PHASES=()

for phase in "${PHASES_TO_RUN[@]}"; do
    case "$phase" in
        discover)  run_discover  || FAILED_PHASES+=("discover") ;;
        snapshot)  run_snapshot  || FAILED_PHASES+=("snapshot") ;;
        scan)      run_scan      || FAILED_PHASES+=("scan") ;;
        annotate)  run_annotate  || FAILED_PHASES+=("annotate") ;;
        validate)  run_validate  || FAILED_PHASES+=("validate") ;;
        profile)   run_profile   || FAILED_PHASES+=("profile") ;;
    esac
done

# ─────────────────────────────────────────────────────────────────────────────
#  RESUMO FINAL
# ─────────────────────────────────────────────────────────────────────────────
section "Resumo"

count_status

echo "" | tee -a "$LOG_FILE"
if [ ${#FAILED_PHASES[@]} -gt 0 ]; then
    fail "Fases com erro: ${FAILED_PHASES[*]}"
    info "Para retomar a partir da primeira fase com erro:"
    info "  bash collect.sh --from ${FAILED_PHASES[0]}"
    echo "" | tee -a "$LOG_FILE"
    exit 1
fi

if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}${BOLD}Simulação concluída — nenhum arquivo foi modificado.${NC}" | tee -a "$LOG_FILE"
else
    ok "Pipeline concluído"
    info "Resultados em: dataset/results/"
    info "Log completo : $LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
fi
