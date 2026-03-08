#!/usr/bin/env bash
# =============================================================================
#  reset_scan.sh — reset parcial do pipeline de scan
#
#  Mantém os snapshots (repos clonados) e reseta APENAS os resultados
#  do scan, permitindo re-escanear tudo do zero com os bugs corrigidos.
#
#  O QUE É RESETADO:
#    ✗  dataset/results/<projeto>/findings.jsonl     findings por projeto
#    ✗  dataset/results/<projeto>/summary.json       sumário de scan
#    ✗  dataset/results/<projeto>/scan_results.json  resultados completos
#    ✗  dataset/results/<projeto>/ground_truth.jsonl anotações (se existir)
#    ✗  dataset/results/dataset_findings.jsonl       findings consolidados
#    ✗  dataset/results/dataset_validation_report.json
#    ✗  dataset/results/dataset_profile.json
#    ✗  dataset/collect.log
#    ✗  status dos projetos: scanned/annotated/error → snapshotted
#    ✗  campo scan.status/scan.findings/scan.error_message no catálogo
#
#  O QUE É PRESERVADO:
#    ✓  dataset/snapshots/           repos já clonados (não re-clona!)
#    ✓  dataset/catalog/projects.yaml  entradas dos projetos
#    ✓  dataset/catalog/projects_backup_*.yaml
#    ✓  .venv/                        ambiente Python
#    ✓  dataset/scripts/*.py          scripts do pipeline
#
#  USO:
#    bash reset_scan.sh                  executa o reset
#    bash reset_scan.sh --dry-run        mostra o que seria feito
#    bash reset_scan.sh --yes            não pede confirmação
#    bash reset_scan.sh --and-scan       reseta e já re-escaneia
#    bash reset_scan.sh --workers 4      workers para o re-scan
#    bash reset_scan.sh --help
# =============================================================================
set -euo pipefail

# ── Cores ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}  →${NC} $*"; }
ok()      { echo -e "${GREEN}  ✓${NC} $*"; }
warn()    { echo -e "${YELLOW}  ⚠${NC} $*"; }
fail()    { echo -e "${RED}  ✗${NC} $*"; }
section() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}"; }
die()     { echo -e "\n${RED}${BOLD}ERRO: $*${NC}" >&2; exit 1; }

# ── Parse de argumentos ───────────────────────────────────────────────────────
DRY_RUN=false
AUTO_YES=false
AND_SCAN=false
WORKERS=2
TIMEOUT=90

for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY_RUN=true ;;
        --yes|-y)   AUTO_YES=true ;;
        --and-scan) AND_SCAN=true ;;
        --help|-h)
            sed -n '/^# ====/,/^# ====/p' "$0" | grep "^#" | sed 's/^# \?//'
            exit 0 ;;
    esac
done

i=0; args=("$@")
while [ $i -lt ${#args[@]} ]; do
    case "${args[$i]}" in
        --workers) i=$((i+1)); WORKERS="${args[$i]:-2}" ;;
        --timeout) i=$((i+1)); TIMEOUT="${args[$i]:-90}" ;;
    esac
    i=$((i+1))
done

# ── Caminhos ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
CATALOG="$SCRIPT_DIR/dataset/catalog/projects.yaml"
RESULTS_DIR="$SCRIPT_DIR/dataset/results"
SNAPSHOTS_DIR="$SCRIPT_DIR/dataset/snapshots"
COLLECT_LOG="$SCRIPT_DIR/dataset/collect.log"

# ── Verificações iniciais ─────────────────────────────────────────────────────
[ -f "$VENV_PY" ]  || die ".venv não encontrado. Execute: bash setup.sh"
[ -f "$CATALOG" ]  || die "Catálogo não encontrado: $CATALOG"

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║     a11y-autofix  —  reset_scan (parcial)    ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Projeto  : $SCRIPT_DIR"
echo -e "  Catálogo : $CATALOG"
echo -e "  Data     : $(date '+%Y-%m-%d %H:%M:%S')"
if $DRY_RUN; then
    echo -e "\n${YELLOW}${BOLD}  MODO DRY-RUN — nada será modificado${NC}"
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
#  ESTADO ATUAL
# ══════════════════════════════════════════════════════════════════════════════
section "1/5  Estado atual do catálogo"

"$VENV_PY" - "$CATALOG" <<'PYEOF'
import sys, yaml
from collections import Counter
data = yaml.safe_load(open(sys.argv[1])) or {}
projects = data.get("projects", [])
c = Counter(p.get("status", "?") for p in projects)
total = len(projects)

# Contar por status
to_reset = sum(c.get(s, 0) for s in ["scanned", "annotated", "error"])
snapshotted = c.get("snapshotted", 0)
pending = c.get("pending", 0)
excluded = c.get("excluded", 0)

print(f"  Total de projetos : {total}")
print(f"  snapshotted       : {snapshotted}  (serão mantidos)")
print(f"  scanned           : {c.get('scanned', 0)}  ← será resetado → snapshotted")
print(f"  annotated         : {c.get('annotated', 0)}  ← será resetado → snapshotted")
print(f"  error             : {c.get('error', 0)}  ← será resetado → snapshotted")
print(f"  pending/excluded  : {pending + excluded}  (não afetados)")
print()
print(f"  Projetos que serão re-habilitados para scan: {to_reset}")
PYEOF

# Contar arquivos de resultado
N_RESULT_DIRS=$(find "$RESULTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
N_RESULT_FILES=$(find "$RESULTS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
RESULTS_SIZE=$(du -sh "$RESULTS_DIR" 2>/dev/null | cut -f1 || echo "0")
N_SNAPS=$(find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
SNAPS_SIZE=$(du -sh "$SNAPSHOTS_DIR" 2>/dev/null | cut -f1 || echo "0")

echo ""
echo -e "  Snapshots (serão MANTIDOS) : ${GREEN}${N_SNAPS} repos (${SNAPS_SIZE})${NC}"
echo -e "  Resultados (serão APAGADOS): ${YELLOW}${N_RESULT_DIRS} dirs / ${N_RESULT_FILES} arquivos (${RESULTS_SIZE})${NC}"

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIRMAÇÃO
# ══════════════════════════════════════════════════════════════════════════════
section "2/5  Confirmação"

if ! $DRY_RUN && ! $AUTO_YES; then
    echo ""
    echo -e "  ${YELLOW}Este script irá:${NC}"
    echo -e "    1. Resetar status scanned/annotated/error → snapshotted no catálogo"
    echo -e "    2. Limpar campo scan.* de cada entrada resetada"
    echo -e "    3. Apagar ${YELLOW}${N_RESULT_FILES} arquivos${NC} em dataset/results/"
    echo -e "    4. Apagar dataset/collect.log"
    echo ""
    echo -e "  ${GREEN}Os ${N_SNAPS} repos em dataset/snapshots/ NÃO serão tocados.${NC}"
    echo ""
    read -rp "  Confirmar reset? [s/N] " CONFIRM
    case "$CONFIRM" in
        s|S|y|Y|sim|yes) ;;
        *) echo -e "\n  Cancelado."; exit 0 ;;
    esac
fi

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3: RESETAR CATÁLOGO (status + scan summary)
# ══════════════════════════════════════════════════════════════════════════════
section "3/5  Resetando catálogo"

if $DRY_RUN; then
    warn "[DRY-RUN] resetaria status scanned/annotated/error → snapshotted"
    warn "[DRY-RUN] limparia campos scan.* de cada projeto afetado"
else
    "$VENV_PY" - "$CATALOG" <<'PYEOF'
import sys, yaml
from pathlib import Path
from collections import Counter

catalog_path = Path(sys.argv[1])
data = yaml.safe_load(catalog_path.read_text()) or {}
projects = data.get("projects", [])

RESET_STATUSES = {"scanned", "annotated", "error"}
EMPTY_SCAN = {
    "status": "pending",
    "findings": {
        "total_issues": 0,
        "high_confidence": 0,
        "files_scanned": 0,
        "files_with_issues": 0,
        "scan_duration_s": 0.0,
        "by_type": {},
        "by_principle": {},
        "by_impact": {},
        "by_criterion": {},
    },
    "error_message": "",
}

reset_count = 0
for p in projects:
    if p.get("status") in RESET_STATUSES:
        old = p["status"]
        p["status"] = "snapshotted"
        p["scan"] = EMPTY_SCAN.copy()
        # Limpar annotation_summary se existir
        if "annotation_summary" in p:
            p["annotation_summary"] = {}
        reset_count += 1
        print(f"  [{old:>10} → snapshotted] {p['id']}")

data["projects"] = projects
catalog_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
print(f"\n  Catálogo atualizado: {reset_count} projeto(s) resetados.")
PYEOF
    ok "Catálogo atualizado"
fi

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4: LIMPAR RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════
section "4/5  Limpando resultados de scan"

# Lista dos arquivos que serão removidos (com contexto)
declare -a SCAN_FILES=(
    "findings.jsonl"
    "ground_truth.jsonl"
    "summary.json"
    "scan_results.json"
    "auto_acceptance_calibration.json"
)

declare -a ROOT_RESULT_FILES=(
    "dataset_findings.jsonl"
    "dataset_validation_report.json"
    "dataset_profile.json"
    "dataset_stats.json"
    "quick_scan_report.json"
)

# Apagar resultados por projeto
N_REMOVED=0
if [ -d "$RESULTS_DIR" ]; then
    for proj_dir in "$RESULTS_DIR"/*/; do
        [ -d "$proj_dir" ] || continue
        proj_name=$(basename "$proj_dir")

        for fname in "${SCAN_FILES[@]}"; do
            fpath="$proj_dir$fname"
            if [ -f "$fpath" ]; then
                if $DRY_RUN; then
                    warn "[DRY-RUN] removeria: results/$proj_name/$fname"
                else
                    rm -f "$fpath"
                    N_REMOVED=$((N_REMOVED + 1))
                fi
            fi
        done

        # Remover diretório do projeto se ficou vazio
        if ! $DRY_RUN && [ -d "$proj_dir" ]; then
            remaining=$(find "$proj_dir" -type f 2>/dev/null | wc -l | tr -d ' ')
            if [ "$remaining" -eq 0 ]; then
                rmdir "$proj_dir"
            fi
        fi
    done

    # Apagar arquivos raiz do results/
    for fname in "${ROOT_RESULT_FILES[@]}"; do
        fpath="$RESULTS_DIR/$fname"
        if [ -f "$fpath" ]; then
            fsize=$(du -sh "$fpath" 2>/dev/null | cut -f1 || echo "?")
            if $DRY_RUN; then
                warn "[DRY-RUN] removeria: results/$fname  ($fsize)"
            else
                rm -f "$fpath"
                ok "Removido: results/$fname  ($fsize)"
                N_REMOVED=$((N_REMOVED + 1))
            fi
        fi
    done
fi

# Apagar collect.log
if [ -f "$COLLECT_LOG" ]; then
    if $DRY_RUN; then
        warn "[DRY-RUN] zeraria: dataset/collect.log"
    else
        : > "$COLLECT_LOG"
        ok "Zerado: dataset/collect.log"
    fi
fi

if ! $DRY_RUN; then
    ok "Removidos $N_REMOVED arquivo(s) de resultados de scan"
fi

# Garantir que o diretório results/ existe e está vazio (apenas com .gitkeep)
if ! $DRY_RUN; then
    mkdir -p "$RESULTS_DIR"
fi

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5: VERIFICAR ESTADO FINAL
# ══════════════════════════════════════════════════════════════════════════════
section "5/5  Estado após reset"

"$VENV_PY" - "$CATALOG" "$SNAPSHOTS_DIR" "$RESULTS_DIR" <<'PYEOF'
import sys, yaml
from pathlib import Path
from collections import Counter

catalog_path = sys.argv[1]
snapshots_dir = Path(sys.argv[2])
results_dir   = Path(sys.argv[3])

data = yaml.safe_load(open(catalog_path)) or {}
projects = data.get("projects", [])
c = Counter(p.get("status", "?") for p in projects)

print(f"  Catálogo:")
for status, n in sorted(c.items()):
    bar = "█" * min(n, 30)
    print(f"    {status:<15} {n:>3}  {bar}")
print(f"    {'TOTAL':<15} {len(projects):>3}")

# Verificar snapshots
snap_dirs = [d for d in snapshots_dir.iterdir() if d.is_dir()] if snapshots_dir.exists() else []
print(f"\n  Snapshots em disco : {len(snap_dirs)} repos (preservados)")

# Verificar results
result_files = list(results_dir.rglob("*.jsonl")) + list(results_dir.rglob("*.json")) if results_dir.exists() else []
print(f"  Arquivos de result : {len(result_files)} (após limpeza)")

# Alinhamento para scan
snapshotted = c.get("snapshotted", 0)
ready_pct   = snapshotted / max(len(projects) - c.get("excluded", 0) - c.get("pending", 0), 1)
print(f"\n  Prontos para scan  : {snapshotted} projetos ({ready_pct:.0%} dos ativos)")
PYEOF

# ══════════════════════════════════════════════════════════════════════════════
#  RESUMO + PRÓXIMOS PASSOS
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}══════════════════════════════════════════════${NC}"

if $DRY_RUN; then
    echo -e "${YELLOW}${BOLD}  DRY-RUN concluído — nenhum arquivo foi modificado.${NC}"
    echo -e "  Execute sem --dry-run para aplicar o reset."
else
    echo -e "${GREEN}${BOLD}  Reset parcial concluído com sucesso.${NC}"
    echo -e "  Snapshots preservados: ${GREEN}${N_SNAPS} repos${NC}"
    echo -e "  Projetos prontos para scan: todos os snapshotted"
fi

echo ""
echo -e "${BOLD}  Próximos passos:${NC}"
echo ""

if $AND_SCAN && ! $DRY_RUN; then
    # ── Auto-rodar o scan ─────────────────────────────────────────────────
    echo -e "  ${CYAN}--and-scan ativo: iniciando scan automaticamente...${NC}"
    echo ""
    exec bash "$SCRIPT_DIR/collect.sh" --from scan --workers "$WORKERS" --timeout "$TIMEOUT"
else
    echo -e "  ${CYAN}1. Instalar ESLint jsx-a11y (se ainda não instalado):${NC}"
    echo -e "     npm install -g eslint eslint-plugin-jsx-a11y @typescript-eslint/parser"
    echo ""
    echo -e "  ${CYAN}2. Validação rápida (sem modificar catálogo):${NC}"
    echo -e "     .venv/bin/python dataset/scripts/quick_scan_report.py \\"
    echo -e "         --max-projects 20 --max-files 100 --workers 4"
    echo ""
    echo -e "  ${CYAN}3. Re-escanear tudo (pipeline completo a partir do scan):${NC}"
    echo -e "     bash collect.sh --from scan --workers ${WORKERS} --timeout ${TIMEOUT}"
    echo ""
    echo -e "  ${CYAN}   Ou com flag --force para forçar re-scan de scanned existentes:${NC}"
    echo -e "     .venv/bin/python dataset/scripts/scan.py \\"
    echo -e "         --catalog dataset/catalog/projects.yaml \\"
    echo -e "         --force --workers ${WORKERS} --timeout ${TIMEOUT}"
    echo ""
    echo -e "  ${CYAN}   Ou tudo em um comando (reset + scan):${NC}"
    echo -e "     bash reset_scan.sh --yes --and-scan --workers ${WORKERS}"
fi

echo -e "${BOLD}${CYAN}══════════════════════════════════════════════${NC}"
echo ""
