#!/usr/bin/env bash
# =============================================================================
# reset_all.sh — limpa todos os artefatos do dataset e deixa o projeto
#                pronto para recomeçar o pipeline do zero.
#
# O QUE É APAGADO:
#   ✗  dataset/snapshots/*          repos shallow-clonados
#   ✗  dataset/results/*            profile.json, validation_report.json,
#                                   scan JSONs e JSONL gerados por scan.py
#   ✗  dataset/collect.log          log da rodada anterior
#   ✗  a11y-report/*                relatórios HTML de acessibilidade
#   ✗  experiment-results/*         outputs de experimentos
#   ✗  **/__pycache__ e *.pyc       bytecode Python
#   ✗  **/.DS_Store                 artefatos de macOS
#
# O QUE É PRESERVADO:
#   ✓  dataset/catalog/projects.yaml  → backup timestampado; depois zerado
#   ✓  dataset/scripts/*.py           scripts do pipeline
#   ✓  dataset/schema/                modelos Pydantic
#   ✓  dataset/PROTOCOL.md, README*   documentação
#   ✓  .venv/                         ambiente Python (caro de reinstalar)
#   ✓  models.yaml                    registro de modelos LLM
#   ✓  experiments/*.yaml             configurações de experimentos
#   ✓  pyproject.toml, .env.example   configuração do projeto
#   ✓  tests/                         suite de testes
#
# USO:
#   ./reset_all.sh            executa o reset
#   ./reset_all.sh --dry-run  mostra o que seria apagado, sem apagar nada
#   ./reset_all.sh --help     exibe esta mensagem
# =============================================================================

set -euo pipefail

# ── Cores ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Caminhos (relativos à localização deste script) ──────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASET_DIR="$SCRIPT_DIR/dataset"
SNAPSHOTS_DIR="$DATASET_DIR/snapshots"
RESULTS_DIR="$DATASET_DIR/results"
CATALOG="$DATASET_DIR/catalog/projects.yaml"
COLLECT_LOG="$DATASET_DIR/collect.log"
A11Y_REPORT_DIR="$SCRIPT_DIR/a11y-report"
EXPERIMENT_RESULTS_DIR="$SCRIPT_DIR/experiment-results"

# ── Parse de argumentos ──────────────────────────────────────────────────────
DRY_RUN=false

for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN=true ;;
    --help|-h)
      head -30 "$0" | grep "^#" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo -e "${RED}Argumento desconhecido: $arg${RESET}"
      echo "Use --dry-run ou --help"
      exit 1
      ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}  →${RESET} $*"; }
ok()      { echo -e "${GREEN}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}  !${RESET} $*"; }
section() { echo -e "\n${BOLD}$*${RESET}"; }

dry_rm_rf() {
  # $1 = caminho, $2 = descrição legível
  local path="$1" desc="$2"
  if [[ ! -e "$path" ]]; then
    info "Não existe (ignorado): $desc"
    return
  fi
  local size
  size=$(du -sh "$path" 2>/dev/null | cut -f1 || echo "?")
  if $DRY_RUN; then
    warn "[DRY-RUN] removeria: $desc  ($size)"
  else
    rm -rf "$path"
    ok "Removido: $desc  (era $size)"
  fi
}

dry_truncate() {
  # $1 = arquivo, $2 = descrição
  local file="$1" desc="$2"
  if [[ ! -f "$file" ]]; then
    info "Não existe (ignorado): $desc"
    return
  fi
  if $DRY_RUN; then
    warn "[DRY-RUN] zeraria: $desc"
  else
    : > "$file"
    ok "Zerado: $desc"
  fi
}

dry_write() {
  # $1 = arquivo, $2 = conteúdo, $3 = descrição
  local file="$1" content="$2" desc="$3"
  if $DRY_RUN; then
    warn "[DRY-RUN] escreveria: $desc"
  else
    printf '%s\n' "$content" > "$file"
    ok "Resetado: $desc"
  fi
}

count_items() {
  # conta arquivos/dirs dentro de um caminho (não recursivo no primeiro nível)
  local path="$1"
  [[ -d "$path" ]] || { echo 0; return; }
  find "$path" -maxdepth 1 -mindepth 1 | wc -l | tr -d ' '
}

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║         a11y-autofix  —  reset_all         ║${RESET}"
echo -e "${BOLD}╚════════════════════════════════════════════╝${RESET}"
if $DRY_RUN; then
  echo -e "\n${YELLOW}  Modo DRY-RUN: nada será apagado de verdade.${RESET}"
fi

# ── 1. Backup do catálogo ─────────────────────────────────────────────────────
section "1/6  Backup do catálogo de projetos"

if [[ -f "$CATALOG" ]]; then
  N_PROJECTS=$(grep -c "^- id:" "$CATALOG" 2>/dev/null || echo 0)
  BACKUP_PATH="$DATASET_DIR/catalog/projects_backup_$(date +%Y%m%d_%H%M%S).yaml"
  if $DRY_RUN; then
    warn "[DRY-RUN] copiaria $CATALOG → $BACKUP_PATH  ($N_PROJECTS projetos)"
  else
    cp "$CATALOG" "$BACKUP_PATH"
    ok "Backup criado: $BACKUP_PATH  ($N_PROJECTS projetos preservados)"
  fi
else
  info "Catálogo não encontrado — será criado do zero pelo discover.py"
fi

# ── 2. Reset do catálogo ──────────────────────────────────────────────────────
section "2/6  Reset do catálogo para estado vazio"

dry_write "$CATALOG" "projects: []" "dataset/catalog/projects.yaml → projects: []"

# ── 3. Snapshots (repos clonados) ────────────────────────────────────────────
section "3/6  Snapshots de repositórios"

N_SNAPS=$(count_items "$SNAPSHOTS_DIR")
if [[ "$N_SNAPS" -eq 0 ]]; then
  info "Nenhum snapshot encontrado em dataset/snapshots/"
else
  info "Encontrados $N_SNAPS snapshots em dataset/snapshots/"
  # Remove cada subdiretório individualmente para feedback mais legível
  if ! $DRY_RUN; then
    find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -not -name '.DS_Store' | \
    while read -r snap; do
      snap_name=$(basename "$snap")
      snap_size=$(du -sh "$snap" 2>/dev/null | cut -f1 || echo "?")
      rm -rf "$snap"
      ok "Removido snapshot: $snap_name  ($snap_size)"
    done
  else
    find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -not -name '.DS_Store' | \
    while read -r snap; do
      snap_name=$(basename "$snap")
      snap_size=$(du -sh "$snap" 2>/dev/null | cut -f1 || echo "?")
      warn "[DRY-RUN] removeria snapshot: $snap_name  ($snap_size)"
    done
  fi
fi

# Garante que o diretório existe e está vazio
if ! $DRY_RUN; then
  mkdir -p "$SNAPSHOTS_DIR"
  find "$SNAPSHOTS_DIR" -name '.DS_Store' -delete 2>/dev/null || true
  ok "dataset/snapshots/ está vazio e pronto"
fi

# ── 4. Resultados do dataset ──────────────────────────────────────────────────
section "4/6  Resultados e logs do dataset"

# Apaga todos os arquivos dentro de results/ (não o diretório em si)
if [[ -d "$RESULTS_DIR" ]]; then
  N_RESULTS=$(find "$RESULTS_DIR" -type f | wc -l | tr -d ' ')
  if [[ "$N_RESULTS" -eq 0 ]]; then
    info "dataset/results/ já está vazio"
  else
    if ! $DRY_RUN; then
      find "$RESULTS_DIR" -type f -delete
      ok "Removidos $N_RESULTS arquivo(s) em dataset/results/"
    else
      warn "[DRY-RUN] removeria $N_RESULTS arquivo(s) em dataset/results/"
      find "$RESULTS_DIR" -type f | while read -r f; do
        warn "[DRY-RUN]   $f"
      done
    fi
  fi
else
  info "dataset/results/ não existe — será criado pelos scripts"
fi

dry_truncate "$COLLECT_LOG" "dataset/collect.log"

# ── 5. Diretórios de output do experimento ───────────────────────────────────
section "5/6  Outputs de relatórios e experimentos"

dry_rm_rf "$A11Y_REPORT_DIR"   "a11y-report/"
dry_rm_rf "$EXPERIMENT_RESULTS_DIR" "experiment-results/"

# ── 6. Cache Python e artefatos de SO ────────────────────────────────────────
section "6/6  Cache Python e artefatos de macOS"

N_PYCACHE=$(find "$SCRIPT_DIR" -type d -name '__pycache__' \
            -not -path '*/.venv/*' | wc -l | tr -d ' ')
N_PYC=$(find "$SCRIPT_DIR" -name '*.pyc' \
        -not -path '*/.venv/*' | wc -l | tr -d ' ')
N_DS=$(find "$SCRIPT_DIR" -name '.DS_Store' | wc -l | tr -d ' ')

if $DRY_RUN; then
  warn "[DRY-RUN] removeria $N_PYCACHE diretórios __pycache__ e $N_PYC arquivos .pyc"
  warn "[DRY-RUN] removeria $N_DS arquivos .DS_Store"
else
  find "$SCRIPT_DIR" -type d -name '__pycache__' \
    -not -path '*/.venv/*' -exec rm -rf {} + 2>/dev/null || true
  find "$SCRIPT_DIR" -name '*.pyc' \
    -not -path '*/.venv/*' -delete 2>/dev/null || true
  ok "Removidos $N_PYCACHE __pycache__ e $N_PYC .pyc (fora do .venv)"

  find "$SCRIPT_DIR" -name '.DS_Store' -delete 2>/dev/null || true
  ok "Removidos $N_DS .DS_Store"
fi

# Recria os diretórios de output vazios (para evitar erros na próxima rodada)
if ! $DRY_RUN; then
  mkdir -p "$SNAPSHOTS_DIR"
  mkdir -p "$RESULTS_DIR"
  mkdir -p "$A11Y_REPORT_DIR"
  mkdir -p "$EXPERIMENT_RESULTS_DIR"
  ok "Diretórios de output recriados vazios"
fi

# ── Resumo ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════${RESET}"
if $DRY_RUN; then
  echo -e "${YELLOW}  DRY-RUN concluído — nada foi alterado.${RESET}"
  echo -e "  Execute sem --dry-run para aplicar o reset."
else
  echo -e "${GREEN}  Reset concluído com sucesso.${RESET}"
fi
echo -e "${BOLD}════════════════════════════════════════════${RESET}"

echo ""
echo -e "${BOLD}Próximos passos:${RESET}"
echo -e "  1. ${CYAN}Descoberta${RESET}  →  python dataset/scripts/discover.py \\"
echo -e "                         --token \$GITHUB_TOKEN \\"
echo -e "                         --output dataset/catalog/projects.yaml"
echo -e "  2. ${CYAN}Pipeline${RESET}    →  bash collect.sh"
echo ""
echo -e "  (Backup do catálogo anterior está em ${CYAN}dataset/catalog/${RESET} com timestamp)"
echo ""
