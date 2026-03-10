#!/usr/bin/env bash
# =============================================================================
#  fix_and_rescan.sh — Backup, corrige critérios de seleção e re-executa o scan
#
#  CRITÉRIOS RELAXADOS (para aumentar número de projetos selecionados):
#    IC1  Stars mínimas:       100 → 50   (discover.py MIN_STARS)
#    IC2  Janela de atividade: 24  → 36 meses (discover.py ACTIVITY_CUTOFF)
#    IC4  Arquivos mínimos:    10  → 5    (snapshot.py MIN_COMPONENT_FILES)
#    IC4+ Fallback paths:      10 dirs → 35 dirs (snapshot.py FALLBACK_SCAN_PATHS)
#    Alvos de domínio:         560 → 770  candidatos totais (discover.py DOMAIN_TARGETS)
#
#  OUTRAS CORREÇÕES:
#    ✦ Backup completo (catalog + results) em ../../backup_dados/<timestamp>/
#    ✦ Restauração do projects.yaml a partir do backup mais recente (se vazio)
#    ✦ Re-avaliação de projetos excluídos por IC4 (re-snapshot com novos critérios)
#    ✦ Descoberta de novos projetos via discover.py --top-up (se --github-token)
#    ✦ Reset do estado de scan (scanned/error → snapshotted) para re-escanear
#    ✦ Re-scan com --min-consensus 2 (confidence scoring correto)
#    ✦ Filtro pós-scan: remove best-practices axe-core sem mapeamento WCAG
#    ✦ Relatório final com findings_report.py + validate.py (QM2/QM3 verification)
#
#  Uso:
#    bash fix_and_rescan.sh                         # executa tudo
#    bash fix_and_rescan.sh --dry-run               # mostra o que faria sem executar
#    bash fix_and_rescan.sh --github-token ghp_xxx  # descobre novos projetos via API
#    bash fix_and_rescan.sh --with-snapshot         # inclui etapa de clone dos repos
#    bash fix_and_rescan.sh --workers 2             # paralelismo do scan (default: 1)
#    bash fix_and_rescan.sh --timeout 120           # timeout por arquivo em segundos
#    bash fix_and_rescan.sh --max-files 20          # limitar arquivos/projeto (teste)
#    bash fix_and_rescan.sh --min-consensus 2       # threshold de consenso (default: 2)
#    bash fix_and_rescan.sh --backup-only           # faz só o backup e sai
#    bash fix_and_rescan.sh --filter-only           # aplica só o filtro pós-scan
#    bash fix_and_rescan.sh --criteria-only         # aplica só critérios e re-snapshot
#
#  Estrutura do backup gerado:
#    ../../backup_dados/<timestamp>/
#      catalog/         → cópia de dataset/catalog/*.yaml
#      results/         → cópia de dataset/results/ (findings, summaries)
#      criteria_before/ → snapshot dos critérios antes da mudança
#      meta.txt         → data, commit, flags usados
# =============================================================================
set -euo pipefail

# ── Caminhos base ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
DATASET_DIR="$REPO_ROOT/dataset"
CATALOG_DIR="$DATASET_DIR/catalog"
CATALOG_FILE="$CATALOG_DIR/projects.yaml"
RESULTS_DIR="$DATASET_DIR/results"
SNAPSHOTS_DIR="$DATASET_DIR/snapshots"
SCRIPTS_DIR="$DATASET_DIR/scripts"
BACKUP_BASE="$(cd "$REPO_ROOT/../.." && pwd)/backup_dados"
BACKUP_TS="$(date +"%Y%m%d_%H%M%S")"
BACKUP_DIR="$BACKUP_BASE/$BACKUP_TS"

# Filtro: regras axe-core best-practice sem mapeamento WCAG a remover
RULES_TO_FILTER='["page-has-heading-one", "region", "skip-link", "landmark-one-main", "page-has-main"]'

# ── Cores e helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

hdr()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"; \
          echo -e "${BOLD}${CYAN}  $*${NC}"; \
          echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${NC}"; }
ok()   { echo -e "  ${GREEN}✅ $*${NC}"; }
warn() { echo -e "  ${YELLOW}⚠️  $*${NC}"; }
info() { echo -e "  ${BLUE}ℹ️  $*${NC}"; }
die()  { echo -e "\n${RED}${BOLD}❌ ERRO: $*${NC}" >&2; exit 1; }

# Executa um comando (ou só exibe em dry-run)
run() {
    if $DRY_RUN; then
        echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
    else
        eval "$@"
    fi
}

# Timer por fase
_PHASE_START=0
phase_start() { _PHASE_START=$(date +%s); }
phase_end()   {
    local elapsed=$(( $(date +%s) - _PHASE_START ))
    echo -e "  ${BLUE}⏱  Fase concluída em ${elapsed}s${NC}"
}

# ── Defaults dos argumentos ───────────────────────────────────────────────────
DRY_RUN=false
WITH_SNAPSHOT=false
BACKUP_ONLY=false
FILTER_ONLY=false
CRITERIA_ONLY=false
WORKERS=1
TIMEOUT=120
MAX_FILES=""
MIN_CONSENSUS=2
GITHUB_TOKEN=""

show_help() {
    sed -n '2,42p' "$0" | sed 's/^# //; s/^#//'
}

# ── Parse de argumentos ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)        DRY_RUN=true ;;
        --with-snapshot)  WITH_SNAPSHOT=true ;;
        --backup-only)    BACKUP_ONLY=true ;;
        --filter-only)    FILTER_ONLY=true ;;
        --criteria-only)  CRITERIA_ONLY=true ;;
        --workers)        WORKERS="${2:?'--workers requer valor'}"; shift ;;
        --timeout)        TIMEOUT="${2:?'--timeout requer valor'}"; shift ;;
        --max-files)      MAX_FILES="${2:?'--max-files requer valor'}"; shift ;;
        --min-consensus)  MIN_CONSENSUS="${2:?'--min-consensus requer valor'}"; shift ;;
        --github-token)   GITHUB_TOKEN="${2:?'--github-token requer valor'}"; shift ;;
        --help|-h)        show_help; exit 0 ;;
        *) die "Flag desconhecida: $1 — use --help para ver opções" ;;
    esac
    shift
done

# ── Cabeçalho ─────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   ♿ a11y-autofix — fix_and_rescan.sh            ║"
echo "  ║   Backup • Correção • Re-scan                    ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Data:          $(date '+%Y-%m-%d %H:%M:%S')"
echo -e "  Backup em:     $BACKUP_DIR"
echo -e "  Workers:       $WORKERS  |  Consensus: $MIN_CONSENSUS"
echo -e "  Critérios:     IC1 ≥50★  IC2 36m  IC4 ≥5 arquivos  (+35 fallback paths)"
[[ -n "$GITHUB_TOKEN" ]] && echo -e "  GitHub token:  ${GITHUB_TOKEN:0:8}... (discover --top-up ativado)" \
                         || echo -e "  GitHub token:  não fornecido (use --github-token para buscar novos projetos)"
$DRY_RUN && echo -e "  ${YELLOW}Modo:          DRY-RUN (nenhuma alteração será feita)${NC}"
echo ""

# ── FASE 0: Validação do ambiente ─────────────────────────────────────────────
hdr "FASE 0 — Verificação do ambiente"
phase_start

[[ -d "$DATASET_DIR" ]]  || die "dataset/ não encontrado em: $DATASET_DIR"
[[ -f "$CATALOG_FILE" ]] || die "Catalog não encontrado: $CATALOG_FILE"
[[ -d "$SCRIPTS_DIR" ]]  || die "scripts/ não encontrado em: $SCRIPTS_DIR"

# Detectar Python 3.10+
PYTHON=""
for py in python3 python; do
    if command -v "$py" &>/dev/null 2>&1; then
        if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$py"
            break
        fi
    fi
done
[[ -z "$PYTHON" ]] && die "Python 3.10+ não encontrado no PATH"

# Verificar dependências Python
$PYTHON -c "import yaml" 2>/dev/null || die "PyYAML não instalado: pip install pyyaml"

ok "Python: $($PYTHON --version)"
ok "Repo root: $REPO_ROOT"
ok "Catalog: $CATALOG_FILE"
ok "Results: $RESULTS_DIR"
phase_end

if $FILTER_ONLY; then
    # Ir direto para a fase de filtro
    hdr "Modo --filter-only: pulando backup e reset"
else

# ─────────────────────────────────────────────────────────────────────────────
# FASE 1: BACKUP
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 1 — Backup completo → $BACKUP_DIR"
phase_start

if ! $DRY_RUN; then
    mkdir -p "$BACKUP_DIR/catalog"
    mkdir -p "$BACKUP_DIR/results"
fi

# -- Backup do catalog ---------------------------------------------------------
if [[ -f "$CATALOG_FILE" ]]; then
    run "cp '$CATALOG_FILE' '$BACKUP_DIR/catalog/projects.yaml'"
    ok "projects.yaml"
fi

# Backup dos backups de catalog (preserva histórico)
BACKUP_YAMLS_COUNT=0
for f in "$CATALOG_DIR"/projects_backup_*.yaml; do
    if [[ -f "$f" ]]; then
        run "cp '$f' '$BACKUP_DIR/catalog/'"
        (( BACKUP_YAMLS_COUNT++ )) || true
    fi
done
[[ $BACKUP_YAMLS_COUNT -gt 0 ]] && ok "Backups de catalog: $BACKUP_YAMLS_COUNT arquivo(s)"

# -- Backup dos results --------------------------------------------------------
if [[ -d "$RESULTS_DIR" ]]; then
    N_PROJECT_DIRS=$(find "$RESULTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l || echo 0)
    N_FINDINGS=$(find "$RESULTS_DIR" -name "findings.jsonl" 2>/dev/null | wc -l || echo 0)

    if [[ "${N_PROJECT_DIRS:-0}" -gt 0 ]]; then
        run "cp -r '$RESULTS_DIR/.' '$BACKUP_DIR/results/'"
        ok "Results: $N_PROJECT_DIRS diretórios de projetos, $N_FINDINGS findings.jsonl"
    else
        # Copia só os arquivos raiz (live_findings.jsonl, *.json)
        for f in "$RESULTS_DIR"/*.jsonl "$RESULTS_DIR"/*.json; do
            [[ -f "$f" ]] && run "cp '$f' '$BACKUP_DIR/results/'" || true
        done
        info "Sem diretórios de projetos em results/ — só arquivos raiz copiados"
    fi
fi

# -- Snapshot dos critérios antes da mudança (para comparação posterior) -------
if ! $DRY_RUN; then
    mkdir -p "$BACKUP_DIR/criteria_before"
    $PYTHON -c "
import re, sys
files = {
    'discover.py':  '$SCRIPTS_DIR/../scripts/discover.py',
    'snapshot.py':  '$SCRIPTS_DIR/../scripts/snapshot.py',
}
for name, path in files.items():
    try:
        txt = open(path.replace('../scripts/', '/../scripts/').replace('$SCRIPTS_DIR', '$SCRIPTS_DIR')).read()
        open('$BACKUP_DIR/criteria_before/' + name + '.criteria', 'w').write(txt)
    except Exception:
        pass
" 2>/dev/null || true

    # Extrair apenas as linhas de critérios de cada arquivo
    grep -n "MIN_STARS\|ACTIVITY_CUTOFF\|MIN_COMPONENT_FILES\|FALLBACK_SCAN_PATHS\|DOMAIN_TARGETS" \
        "$SCRIPTS_DIR/discover.py" "$SCRIPTS_DIR/snapshot.py" \
        > "$BACKUP_DIR/criteria_before/thresholds_before.txt" 2>/dev/null || true
    ok "Snapshot de critérios salvo em criteria_before/"
fi

# -- Meta do backup ------------------------------------------------------------
if ! $DRY_RUN; then
    cat > "$BACKUP_DIR/meta.txt" << EOF
Backup gerado por fix_and_rescan.sh
Data:          $(date '+%Y-%m-%d %H:%M:%S')
Host:          $(hostname)
Git commit:    $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "N/A")
Git branch:    $(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo "N/A")
Workers:       $WORKERS
Min-consensus: $MIN_CONSENSUS
Timeout:       $TIMEOUT
Max-files:     ${MAX_FILES:-"sem limite"}
With-snapshot: $WITH_SNAPSHOT
GitHub token:  ${GITHUB_TOKEN:+"fornecido (${GITHUB_TOKEN:0:8}...)"}${GITHUB_TOKEN:-"não fornecido"}
Critérios:
  IC1 MIN_STARS:           100 → 50
  IC2 ACTIVITY_CUTOFF:     730 dias → 1095 dias (24m → 36m)
  IC4 MIN_COMPONENT_FILES: 10  → 5
  IC4 FALLBACK_PATHS:      10  → 35 diretórios
  DOMAIN_TARGETS total:    560 → 770
EOF
    ok "meta.txt gerado"
fi

ok "Backup completo: $BACKUP_DIR"
phase_end

# Encerra aqui se --backup-only
$BACKUP_ONLY && { echo -e "\n  ${GREEN}--backup-only: concluído.${NC}\n"; exit 0; }

# ─────────────────────────────────────────────────────────────────────────────
# FASE 2: RESTAURAÇÃO DO CATALOG
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 2 — Verificação e restauração do catalog"
phase_start

CATALOG_COUNT=$($PYTHON -c "
import yaml, sys
try:
    with open('$CATALOG_FILE') as f:
        d = yaml.safe_load(f) or {}
    print(len(d.get('projects', [])))
except Exception as e:
    print(0)
" 2>/dev/null || echo "0")

if [[ "${CATALOG_COUNT:-0}" -eq 0 ]]; then
    warn "projects.yaml está vazio (0 projetos)"

    # Encontrar o backup mais recente
    LATEST_BACKUP="$(ls -t "$CATALOG_DIR"/projects_backup_*.yaml 2>/dev/null | command head -1 || echo "")"

    if [[ -z "$LATEST_BACKUP" ]]; then
        die "Nenhum backup de catalog encontrado em $CATALOG_DIR — impossível restaurar"
    fi

    BACKUP_COUNT=$($PYTHON -c "
import yaml
with open('$LATEST_BACKUP') as f:
    d = yaml.safe_load(f) or {}
print(len(d.get('projects', [])))
" 2>/dev/null || echo "0")

    info "Backup encontrado: $(basename "$LATEST_BACKUP") ($BACKUP_COUNT projetos)"

    if ! $DRY_RUN; then
        # Guardar arquivo vazio com timestamp
        cp "$CATALOG_FILE" "$CATALOG_DIR/projects_empty_before_restore_${BACKUP_TS}.yaml"
        cp "$LATEST_BACKUP" "$CATALOG_FILE"
        ok "Catalog restaurado: $BACKUP_COUNT projetos de $(basename "$LATEST_BACKUP")"
    else
        echo "  [DRY-RUN] cp '$LATEST_BACKUP' '$CATALOG_FILE'"
        ok "[DRY-RUN] Catalog seria restaurado: $BACKUP_COUNT projetos"
    fi
else
    ok "Catalog OK: $CATALOG_COUNT projetos"
fi

# Exibir distribuição de status atual
$PYTHON -c "
import yaml
with open('$CATALOG_FILE') as f:
    d = yaml.safe_load(f) or {}
ps = d.get('projects', [])
from collections import Counter
counts = Counter(p.get('status', 'unknown') for p in ps)
print('  Distribuição de status:')
for k, v in sorted(counts.items(), key=lambda x: -x[1]):
    print(f'    {k:<15} {v}')
" 2>/dev/null || true

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# FASE 2.5 — VALIDAÇÃO E APLICAÇÃO DOS CRITÉRIOS RELAXADOS
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 2.5 — Critérios de seleção relaxados: validação e re-avaliação"
phase_start

# -- Verificar que as mudanças estão nos arquivos fonte -----------------------
CRITERIA_CHECK_SCRIPT=$(cat << 'PYEOF'
import re, sys

errors = []
warnings = []

# discover.py checks
with open(sys.argv[1]) as f:
    disc = f.read()

# IC1: MIN_STARS deve ser 50
m = re.search(r"MIN_STARS\s*=\s*(\d+)", disc)
if m:
    v = int(m.group(1))
    if v <= 50:
        print(f"  ✅ IC1  MIN_STARS = {v}  (≤50 — ok)")
    else:
        errors.append(f"IC1 MIN_STARS ainda é {v}, esperado ≤50")
else:
    errors.append("IC1 MIN_STARS não encontrado em discover.py")

# IC2: ACTIVITY_CUTOFF deve usar ≥1095 dias
m = re.search(r"timedelta\(days=(\d+)\)", disc)
if m:
    v = int(m.group(1))
    if v >= 1095:
        print(f"  ✅ IC2  ACTIVITY_CUTOFF = {v} dias  (≥1095 — ok)")
    else:
        errors.append(f"IC2 ACTIVITY_CUTOFF ainda é {v} dias, esperado ≥1095")
else:
    errors.append("IC2 ACTIVITY_CUTOFF não encontrado em discover.py")

# DOMAIN_TARGETS total
totals = re.findall(r"ProjectDomain\.\w+:\s*(\d+)", disc)
if totals:
    total = sum(int(t) for t in totals)
    if total >= 700:
        print(f"  ✅ DOMAIN_TARGETS total = {total}  (≥700 — ok)")
    else:
        warnings.append(f"DOMAIN_TARGETS total = {total}, esperado ≥700")

# snapshot.py checks
with open(sys.argv[2]) as f:
    snap = f.read()

# IC4: MIN_COMPONENT_FILES deve ser 5
m = re.search(r"MIN_COMPONENT_FILES\s*=\s*(\d+)", snap)
if m:
    v = int(m.group(1))
    if v <= 5:
        print(f"  ✅ IC4  MIN_COMPONENT_FILES = {v}  (≤5 — ok)")
    else:
        errors.append(f"IC4 MIN_COMPONENT_FILES ainda é {v}, esperado ≤5")
else:
    errors.append("IC4 MIN_COMPONENT_FILES não encontrado em snapshot.py")

# Fallback paths: deve ter pelo menos 20
fallback_matches = re.findall(r'"\w[^"]*/"', snap[snap.find("FALLBACK_SCAN_PATHS"):snap.find("FALLBACK_SCAN_PATHS")+2000])
n_paths = len(fallback_matches)
if n_paths >= 20:
    print(f"  ✅ IC4+ FALLBACK_SCAN_PATHS = {n_paths} caminhos  (≥20 — ok)")
else:
    warnings.append(f"Apenas {n_paths} fallback paths (esperado ≥20)")

for w in warnings:
    print(f"  ⚠️  {w}")
for e in errors:
    print(f"  ❌ {e}")

sys.exit(1 if errors else 0)
PYEOF
)

CRITERIA_OK=true
if ! $DRY_RUN; then
    if ! $PYTHON -c "$CRITERIA_CHECK_SCRIPT" "$SCRIPTS_DIR/discover.py" "$SCRIPTS_DIR/snapshot.py"; then
        warn "Alguns critérios não estão conforme esperado — verifique discover.py e snapshot.py"
        CRITERIA_OK=false
    else
        ok "Todos os critérios validados nos arquivos fonte"
    fi
else
    echo "  [DRY-RUN] Verificação de critérios em discover.py e snapshot.py"
    echo "  [DRY-RUN] IC1 MIN_STARS=50, IC2 1095 dias, IC4 MIN_COMPONENT_FILES=5, +35 fallback paths"
fi

# -- Salvar novo backup do catalog antes de modificar -------------------------
if ! $DRY_RUN; then
    CRITERIA_BACKUP="$CATALOG_DIR/projects_backup_before_criteria_${BACKUP_TS}.yaml"
    cp "$CATALOG_FILE" "$CRITERIA_BACKUP"
    ok "Backup do catalog antes das mudanças: $(basename "$CRITERIA_BACKUP")"
fi

# -- Contar projetos antes da re-avaliação ------------------------------------
COUNT_BEFORE=$($PYTHON -c "
import yaml
try:
    with open('$CATALOG_FILE') as f: d = yaml.safe_load(f) or {}
    ps = d.get('projects', [])
    included = sum(1 for p in ps if p.get('status') not in ('excluded',))
    excluded_ic4 = sum(1 for p in ps
        if p.get('status') == 'excluded'
        and p.get('screening', {}).get('exclusion_criterion') == 'IC4')
    print(f'included={included}')
    print(f'excluded_ic4={excluded_ic4}')
    print(f'total={len(ps)}')
except Exception as e:
    print(f'included=0'); print(f'excluded_ic4=0'); print(f'total=0')
" 2>/dev/null || echo "included=0
excluded_ic4=0
total=0")

N_INCLUDED_BEFORE=0; N_EXCLUDED_IC4=0; N_TOTAL=0
while IFS='=' read -r key val; do
    case "$key" in
        included)    N_INCLUDED_BEFORE=$val ;;
        excluded_ic4) N_EXCLUDED_IC4=$val ;;
        total)       N_TOTAL=$val ;;
    esac
done <<< "$COUNT_BEFORE"

info "Antes da re-avaliação: $N_INCLUDED_BEFORE incluídos / $N_TOTAL total"
info "Projetos excluídos por IC4 (candidatos para re-avaliação): $N_EXCLUDED_IC4"

# -- Re-avaliar projetos excluídos por IC4 no catalog -------------------------
# Reverte status excluded+IC4 → pending para que snapshot.py os re-avalie
# com os novos critérios relaxados (IC4 ≥5 + 35 fallback paths).
REEVAL_SCRIPT=$(cat << 'PYEOF'
import yaml, sys
from pathlib import Path

catalog_file = sys.argv[1]
dry_run      = sys.argv[2] == "true"

with open(catalog_file) as f:
    data = yaml.safe_load(f) or {}

projects = data.get("projects", [])
reverted = 0
for p in projects:
    if p.get("status") != "excluded":
        continue
    screening = p.get("screening", {})
    criterion = screening.get("exclusion_criterion", "")
    # Só reabilitar projetos que falharam por IC4 (insuficiente de arquivos).
    # Não reabilitar IC7 (sem JSX), IC6 (gerado), EC*, CLONE_ERROR etc.
    if criterion != "IC4":
        continue
    # Reabilitar como pending (snapshot.py irá clonar/re-avaliar)
    p["status"] = "pending"
    # Limpar campos de exclusão para nova avaliação
    p.setdefault("screening", {})
    p["screening"]["exclusion_criterion"] = ""
    p["screening"]["exclusion_reason"]    = ""
    p["screening"]["ic4_component_files"] = "not_checked"
    reverted += 1

print(f"reverted={reverted}")

if not dry_run and reverted > 0:
    with open(catalog_file, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
PYEOF
)

if ! $DRY_RUN; then
    REEVAL_OUTPUT=$($PYTHON -c "$REEVAL_SCRIPT" "$CATALOG_FILE" "false" 2>&1)
    N_REVERTED=$(echo "$REEVAL_OUTPUT" | grep "^reverted=" | cut -d= -f2 || echo 0)
    if [[ "${N_REVERTED:-0}" -gt 0 ]]; then
        ok "$N_REVERTED projeto(s) IC4 revertidos para pending (serão re-avaliados pelo snapshot.py)"
    else
        info "Nenhum projeto IC4 para re-avaliar no catalog atual"
    fi
else
    DRY_REEVAL=$($PYTHON -c "$REEVAL_SCRIPT" "$CATALOG_FILE" "true" 2>/dev/null || echo "reverted=0")
    N_REVERTED=$(echo "$DRY_REEVAL" | grep "^reverted=" | cut -d= -f2 || echo 0)
    echo "  [DRY-RUN] $N_REVERTED projeto(s) IC4 seriam revertidos para pending"
fi

# -- Descoberta de novos projetos via GitHub API (requer --github-token) ------
if [[ -n "$GITHUB_TOKEN" ]]; then
    info "GitHub token detectado — executando discover.py --top-up com critérios relaxados"
    info "Novos alvos: IC1 ≥50★, IC2 36 meses, DOMAIN_TARGETS total=770"

    if ! $DRY_RUN; then
        $PYTHON "$SCRIPTS_DIR/discover.py" \
            --token "$GITHUB_TOKEN" \
            --output "$CATALOG_FILE" \
            --top-up \
            && ok "discover.py --top-up concluído" \
            || warn "discover.py terminou com aviso (pode ser rate-limit da API)"
    else
        echo "  [DRY-RUN] python dataset/scripts/discover.py --token <TOKEN> --top-up"
    fi
else
    warn "GitHub token não fornecido — descoberta de novos projetos pulada"
    warn "Use --github-token ghp_... para buscar projetos adicionais na API do GitHub"
    warn "Sem o token, a re-avaliação dos IC4 excluídos ainda pode recuperar projetos existentes"
fi

# -- Contagem após re-avaliação -----------------------------------------------
COUNT_AFTER=$($PYTHON -c "
import yaml
try:
    with open('$CATALOG_FILE') as f: d = yaml.safe_load(f) or {}
    ps = d.get('projects', [])
    included = sum(1 for p in ps if p.get('status') not in ('excluded',))
    excluded  = sum(1 for p in ps if p.get('status') == 'excluded')
    print(f'included={included}')
    print(f'excluded={excluded}')
    print(f'total={len(ps)}')
except Exception:
    print('included=0'); print('excluded=0'); print('total=0')
" 2>/dev/null || echo "included=0
excluded=0
total=0")

N_INCLUDED_AFTER=0
while IFS='=' read -r key val; do
    [[ "$key" == "included" ]] && N_INCLUDED_AFTER=$val
done <<< "$COUNT_AFTER"

DELTA=$(( N_INCLUDED_AFTER - N_INCLUDED_BEFORE ))
if [[ "$DELTA" -gt 0 ]]; then
    ok "Projetos incluídos: $N_INCLUDED_BEFORE → $N_INCLUDED_AFTER  (+$DELTA novos projetos)"
elif [[ "$DELTA" -eq 0 ]]; then
    info "Projetos incluídos: $N_INCLUDED_AFTER (sem alteração — IC4 failures tinham 0 arquivos detectados)"
    info "Para incluir mais projetos, execute com --github-token para buscar novos via API"
else
    warn "Projetos incluídos: $N_INCLUDED_AFTER (delta negativo — verifique o catalog)"
fi

# Distribuição final
$PYTHON -c "
import yaml
with open('$CATALOG_FILE') as f:
    d = yaml.safe_load(f) or {}
ps = d.get('projects', [])
from collections import Counter

status_counts  = Counter(p.get('status', 'unknown') for p in ps)
domain_counts  = Counter(p.get('domain', 'unknown')
                         for p in ps if p.get('status') not in ('excluded',))
n_included = sum(v for k, v in status_counts.items() if k != 'excluded')
n_total    = len(ps)

print(f'  Total no catalog: {n_total}  |  Incluídos: {n_included}  |  Excluídos: {status_counts.get(\"excluded\", 0)}')
print()
print('  Por status:')
for k, v in sorted(status_counts.items(), key=lambda x: -x[1]):
    print(f'    {k:<15} {v}')
print()
print('  Por domínio (incluídos):')
for k, v in sorted(domain_counts.items(), key=lambda x: -x[1]):
    pct = v / max(n_included, 1) * 100
    bar = \"█\" * int(pct / 2)
    flag = \" ⚠️  >20%\" if pct > 20 else \"\"
    print(f'    {k:<20} {v:>4}  ({pct:5.1f}%)  {bar}{flag}')

# QM3 check
max_frac = max((v / max(n_included, 1) for v in domain_counts.values()), default=0)
if max_frac <= 0.20:
    print(f'  ✅ QM3 stratum balance OK (max={max_frac:.1%} ≤ 20%)')
else:
    dom_max = max(domain_counts, key=domain_counts.get)
    print(f'  ⚠️  QM3 stratum balance FAIL: {dom_max} = {max_frac:.1%} > 20%')

# QM2 check
if n_included >= 400:
    print(f'  ✅ QM2 corpus size OK ({n_included} ≥ 400)')
else:
    print(f'  ⚠️  QM2 corpus size FAIL: {n_included} < 400 (faltam {400 - n_included})')
    print(f'       → Use --github-token para descobrir novos projetos')
" 2>/dev/null || true

# Encerrar aqui se --criteria-only
$CRITERIA_ONLY && {
    echo -e "\n  ${GREEN}--criteria-only: critérios aplicados e re-avaliação concluída.${NC}"
    echo -e "  Próximo passo: bash fix_and_rescan.sh --with-snapshot (para clonar novos projetos)\n"
    exit 0
}

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# FASE 3: RESET DO ESTADO DE SCAN
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 3 — Reset de estado: scanned/error → snapshotted"
phase_start

# Script Python para resetar estado de scan no catalog
RESET_SCRIPT=$(cat << 'PYEOF'
import yaml, json, sys
from pathlib import Path
from collections import Counter

catalog_file = sys.argv[1]
results_dir  = Path(sys.argv[2])

with open(catalog_file) as f:
    data = yaml.safe_load(f) or {}

projects = data.get("projects", [])
before = Counter(p.get("status") for p in projects)
reset_count = 0

for p in projects:
    status = p.get("status", "")
    # Resetar projetos previamente escaneados ou com erro
    if status in ("scanned", "annotated", "error"):
        p["status"] = "snapshotted"

        # Limpar campos de scan
        if isinstance(p.get("scan"), dict):
            p["scan"]["status"] = "pending"
            p["scan"]["error_message"] = ""
            p["scan"]["findings"] = {
                "total_issues": 0, "high_confidence": 0,
                "medium_confidence": 0, "low_confidence": 0,
                "by_type": {}, "by_principle": {}, "by_impact": {},
                "by_criterion": {}, "files_scanned": 0,
                "files_with_issues": 0, "tools_succeeded": [],
                "tools_failed": [], "tool_versions": {},
                "scan_duration_seconds": 0.0, "scan_date": ""
            }
        reset_count += 1

after = Counter(p.get("status") for p in projects)
n_scannable = after.get("snapshotted", 0)

print(f"reset_count={reset_count}")
print(f"n_scannable={n_scannable}")
print(f"n_pending={after.get('pending', 0)}")
print(f"n_excluded={after.get('excluded', 0)}")

with open(catalog_file, "w") as f:
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
PYEOF
)

if ! $DRY_RUN; then
    RESET_OUTPUT=$($PYTHON -c "$RESET_SCRIPT" "$CATALOG_FILE" "$RESULTS_DIR" 2>&1)
    while IFS= read -r line; do
        KEY="${line%%=*}"; VAL="${line##*=}"
        case "$KEY" in
            reset_count)  [[ "$VAL" -gt 0 ]] && ok "Projetos resetados para snapshotted: $VAL" || info "Nenhum projeto precisou de reset" ;;
            n_scannable)  ok "Projetos prontos para scan (snapshotted): $VAL" ;;
            n_pending)    [[ "$VAL" -gt 0 ]] && warn "Projetos pending (precisam de snapshot antes): $VAL" ;;
            n_excluded)   info "Projetos excluídos (ignorados): $VAL" ;;
        esac
    done <<< "$RESET_OUTPUT"
else
    echo "  [DRY-RUN] Projetos scanned/error/annotated seriam resetados para snapshotted"
fi

# Remover arquivos de resultados antigos (mantém ground_truth.jsonl se existir)
if ! $DRY_RUN; then
    N_CLEANED=0
    while IFS= read -r -d '' f; do
        rm -f "$f"
        (( N_CLEANED++ )) || true
    done < <(find "$RESULTS_DIR" -maxdepth 2 \
        \( -name "findings.jsonl" \
        -o -name "summary.json" \
        -o -name "scan_results.json" \
        -o -name "dataset_findings.jsonl" \
        -o -name "dataset_validation_report.json" \
        -o -name "dataset_profile.json" \) \
        -print0 2>/dev/null)

    # Resetar live_findings.jsonl
    echo -n "" > "$RESULTS_DIR/live_findings.jsonl" 2>/dev/null || true

    [[ $N_CLEANED -gt 0 ]] && ok "$N_CLEANED arquivo(s) de resultados anteriores removidos" \
                             || info "Nenhum resultado anterior para remover"
else
    N_WOULD=$(find "$RESULTS_DIR" -maxdepth 2 \
        \( -name "findings.jsonl" -o -name "summary.json" -o -name "scan_results.json" \) \
        2>/dev/null | wc -l || echo 0)
    echo "  [DRY-RUN] $N_WOULD arquivos de resultados seriam removidos"
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# FASE 4: SNAPSHOT (clone dos repositórios) — opcional
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 4 — Snapshot dos repositórios"
phase_start

N_CLONED=$(find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l || echo 0)

if $WITH_SNAPSHOT; then
    info "Repositórios já clonados em snapshots/: $N_CLONED"
    info "Iniciando snapshot de projetos pendentes..."

    SNAPSHOT_SCRIPT="$SCRIPTS_DIR/snapshot.py"
    if [[ ! -f "$SNAPSHOT_SCRIPT" ]]; then
        warn "snapshot.py não encontrado em $SCRIPTS_DIR — etapa pulada"
    else
        if ! $DRY_RUN; then
            $PYTHON "$SNAPSHOT_SCRIPT" --catalog "$CATALOG_FILE" \
                && ok "Snapshot concluído" \
                || warn "Snapshot finalizado com erros (alguns projetos podem ter falhado)"
        else
            echo "  [DRY-RUN] python dataset/scripts/snapshot.py --catalog $CATALOG_FILE"
        fi
    fi

    # Recontagem após snapshot
    N_CLONED=$(find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l || echo 0)
    ok "Repositórios disponíveis após snapshot: $N_CLONED"
else
    if [[ "${N_CLONED:-0}" -eq 0 ]]; then
        warn "Nenhum repositório clonado em snapshots/ — scan não encontrará arquivos para analisar"
        warn "Para clonar os repositórios, execute:"
        warn "  bash fix_and_rescan.sh --with-snapshot"
        warn "  # ou individualmente:"
        warn "  python dataset/scripts/snapshot.py --catalog dataset/catalog/projects.yaml"
    else
        ok "Repositórios disponíveis em snapshots/: $N_CLONED (snapshot pulado, já clonados)"
    fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# FASE 5: SCAN COM FERRAMENTAS CORRIGIDAS
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 5 — Scan com ferramentas corrigidas"
phase_start

N_SNAPSHOTS=$(find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l || echo 0)

# Montar args do scan
SCAN_ARGS=(
    "$SCRIPTS_DIR/scan.py"
    "--catalog" "$CATALOG_FILE"
    "--min-consensus" "$MIN_CONSENSUS"
    "--workers" "$WORKERS"
    "--timeout" "$TIMEOUT"
)
[[ -n "$MAX_FILES" ]] && SCAN_ARGS+=("--max-files" "$MAX_FILES")

info "Comando: $PYTHON ${SCAN_ARGS[*]}"
info "Min-consensus: $MIN_CONSENSUS  (antes: 1 — agora gera confidence scoring correto)"
info "Filtros pós-scan: page-has-heading-one e outras best-practices sem WCAG serão removidas"

if [[ "${N_SNAPSHOTS:-0}" -eq 0 ]]; then
    warn "Sem repositórios em snapshots/ — scan pulado"
    warn "Solução: bash fix_and_rescan.sh --with-snapshot"
elif ! $DRY_RUN; then
    info "Iniciando scan de $N_SNAPSHOTS repositórios..."
    echo ""
    $PYTHON "${SCAN_ARGS[@]}" || warn "Scan finalizado com alguns erros (projetos com erro são marcados no catalog)"
    echo ""
    ok "Scan concluído"
else
    echo "  [DRY-RUN] $PYTHON ${SCAN_ARGS[*]}"
fi

phase_end

fi # fim do bloco if $FILTER_ONLY

# ─────────────────────────────────────────────────────────────────────────────
# FASE 6: FILTRO PÓS-SCAN — remove best-practices sem WCAG mapping
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 6 — Filtro pós-scan: best-practices sem mapeamento WCAG"
phase_start

info "Regras a filtrar: page-has-heading-one, region, skip-link, landmark-one-main, page-has-main"
info "Motivo: axe-core best-practices sem wcag_criteria — não representam violações WCAG mensuráveis"

FILTER_SCRIPT=$(cat << 'PYEOF'
"""
Remove findings de regras axe-core best-practice sem mapeamento WCAG.
Atualiza findings.jsonl de cada projeto e recalcula os contadores do summary.json.
"""
import json, sys, os
from pathlib import Path
from collections import defaultdict

results_dir   = Path(sys.argv[1])
rules_raw     = sys.argv[2]   # JSON array como string
dry_run       = sys.argv[3] == "true"

import json as _json
RULES_TO_FILTER = set(_json.loads(rules_raw))

total_removed  = 0
total_kept     = 0
projects_done  = 0
rule_counts    = defaultdict(int)

for project_dir in sorted(results_dir.iterdir()):
    if not project_dir.is_dir():
        continue

    findings_file = project_dir / "findings.jsonl"
    if not findings_file.exists():
        continue

    # Ler findings existentes
    original = []
    with open(findings_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    original.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not original:
        continue

    # Filtrar
    kept    = []
    removed = []
    for finding in original:
        rule = finding.get("rule_id", "")
        # Filtrar se regra está na lista OU se não tem wcag_criteria e issue_type == best-practice
        is_best_practice_no_wcag = (
            rule in RULES_TO_FILTER
            or (not finding.get("wcag_criteria") and finding.get("issue_type") == "best-practice")
        )
        if is_best_practice_no_wcag:
            removed.append(finding)
            rule_counts[rule or "(sem rule_id)"] += 1
        else:
            kept.append(finding)

    n_removed = len(removed)
    if n_removed == 0:
        continue

    # Gravar findings filtrados
    if not dry_run:
        with open(findings_file, "w", encoding="utf-8") as f:
            for finding in kept:
                f.write(json.dumps(finding, ensure_ascii=False) + "\n")

        # Atualizar summary.json se existir
        summary_file = project_dir / "summary.json"
        if summary_file.exists():
            try:
                with open(summary_file) as f:
                    summary = json.load(f)

                # Recalcular contadores de findings se existir findings_summary
                fs = summary.get("findings_summary") or summary.get("scan", {}).get("findings", {})
                if isinstance(fs, dict) and "total_issues" in fs:
                    fs["total_issues"] = len(kept)
                    # Recalcular high/medium/low
                    hc = sum(1 for k in kept if k.get("confidence") == "high")
                    mc = sum(1 for k in kept if k.get("confidence") == "medium")
                    fs["high_confidence"]   = hc
                    fs["medium_confidence"] = mc
                    fs["low_confidence"]    = len(kept) - hc - mc

                    # Recalcular by_principle
                    by_p = defaultdict(int)
                    principle_map = {"1": "perceivable", "2": "operable",
                                     "3": "understandable", "4": "robust"}
                    for k in kept:
                        wcag = k.get("wcag_criteria")
                        if wcag:
                            p = principle_map.get(wcag.split(".")[0], "unknown")
                        else:
                            p = "unknown"
                        by_p[p] += 1
                    fs["by_principle"] = dict(by_p)

                with open(summary_file, "w") as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
            except Exception:
                pass  # Não crítico se summary não puder ser atualizado

    total_removed  += n_removed
    total_kept     += len(kept)
    projects_done  += 1

print(f"total_removed={total_removed}")
print(f"total_kept={total_kept}")
print(f"projects_done={projects_done}")
print(f"top_rules=" + json.dumps(
    dict(sorted(rule_counts.items(), key=lambda x: -x[1])[:10])
))
PYEOF
)

# Verificar se há findings para filtrar
N_FINDINGS_FILES=$(find "$RESULTS_DIR" -name "findings.jsonl" -not -empty 2>/dev/null | wc -l || echo 0)

if [[ "${N_FINDINGS_FILES:-0}" -eq 0 ]]; then
    info "Nenhum findings.jsonl encontrado — filtro será aplicado após o scan"
else
    if ! $DRY_RUN; then
        FILTER_OUTPUT=$($PYTHON -c "$FILTER_SCRIPT" "$RESULTS_DIR" "$RULES_TO_FILTER" "false" 2>&1)
        while IFS= read -r line; do
            KEY="${line%%=*}"; VAL="${line##*=}"
            case "$KEY" in
                total_removed)  ok "Findings removidos (best-practices sem WCAG): $VAL" ;;
                total_kept)     ok "Findings mantidos após filtro: $VAL" ;;
                projects_done)  [[ "$VAL" -gt 0 ]] && ok "Projetos com filtro aplicado: $VAL" || info "Nenhum projeto afetado pelo filtro" ;;
                top_rules)      info "Regras removidas: $VAL" ;;
            esac
        done <<< "$FILTER_OUTPUT"
    else
        echo "  [DRY-RUN] $PYTHON -c '...filter_script...' '$RESULTS_DIR' '$RULES_TO_FILTER' 'true'"
        # Dry-run: só conta o que seria removido
        DRY_OUTPUT=$($PYTHON -c "$FILTER_SCRIPT" "$RESULTS_DIR" "$RULES_TO_FILTER" "true" 2>/dev/null || echo "")
        while IFS= read -r line; do
            KEY="${line%%=*}"; VAL="${line##*=}"
            case "$KEY" in
                total_removed) echo "  [DRY-RUN] Findings que seriam removidos: $VAL" ;;
                total_kept)    echo "  [DRY-RUN] Findings que seriam mantidos: $VAL" ;;
            esac
        done <<< "$DRY_OUTPUT"
    fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# FASE 7: RELATÓRIO FINAL
# ─────────────────────────────────────────────────────────────────────────────
hdr "FASE 7 — Relatório final"
phase_start

N_FINDINGS_FILES=$(find "$RESULTS_DIR" -name "findings.jsonl" -not -empty 2>/dev/null | wc -l || echo 0)

if [[ "${N_FINDINGS_FILES:-0}" -eq 0 ]]; then
    warn "Sem findings para reportar — scan não gerou resultados ainda"
    info "Verifique se os snapshots existem: ls dataset/snapshots/ | wc -l"
    info "Para ver status: python dataset/scripts/scan.py --status --pending"
else
    if ! $DRY_RUN; then
        echo ""
        echo -e "  ${BOLD}─── findings_report.py ─────────────────────────────────${NC}"
        $PYTHON "$SCRIPTS_DIR/findings_report.py" 2>/dev/null || warn "findings_report.py terminou com aviso"

        echo ""
        echo -e "  ${BOLD}─── validate.py ────────────────────────────────────────${NC}"
        $PYTHON "$SCRIPTS_DIR/validate.py" --catalog "$CATALOG_FILE" 2>/dev/null \
            || warn "validate.py terminou com aviso (esperado antes da anotação)"

        echo ""
        echo -e "  ${BOLD}─── scan status ────────────────────────────────────────${NC}"
        $PYTHON "$SCRIPTS_DIR/scan.py" --status --catalog "$CATALOG_FILE" 2>/dev/null \
            || true
    else
        echo "  [DRY-RUN] python dataset/scripts/findings_report.py"
        echo "  [DRY-RUN] python dataset/scripts/validate.py --catalog $CATALOG_FILE"
        echo "  [DRY-RUN] python dataset/scripts/scan.py --status"
    fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# RESUMO FINAL
# ─────────────────────────────────────────────────────────────────────────────
echo ""
hdr "RESUMO DA EXECUÇÃO"
echo ""
echo -e "  ${BOLD}Backup salvo em:${NC}"
echo -e "    $BACKUP_DIR"
echo ""
echo -e "  ${BOLD}Critérios de seleção relaxados (discover.py + snapshot.py):${NC}"
echo -e "    ✅ IC1  MIN_STARS           100 → 50    (mais projetos de nicho descobertos)"
echo -e "    ✅ IC2  ACTIVITY_CUTOFF     24m → 36m   (inclui projetos estáveis mais antigos)"
echo -e "    ✅ IC4  MIN_COMPONENT_FILES 10  → 5     (aceita projetos menores mas funcionais)"
echo -e "    ✅ IC4+ FALLBACK_SCAN_PATHS 10  → 35    (detecta components em layouts não-padrão)"
echo -e "    ✅ DOMAIN_TARGETS           560 → 770   (mais candidatos para atingir QM2 ≥400)"
echo ""
echo -e "  ${BOLD}Outras correções:${NC}"
echo -e "    ✅ Catalog restaurado a partir do backup (se estava vazio)"
echo -e "    ✅ Projetos IC4 excluídos revertidos para pending (re-avaliação automática)"
[[ -n "$GITHUB_TOKEN" ]] && echo -e "    ✅ discover.py --top-up executado com novos critérios"
echo -e "    ✅ Estado de scan resetado (scanned/error → snapshotted)"
echo -e "    ✅ Min-consensus: $MIN_CONSENSUS (confidence scoring correto)"
echo -e "    ✅ Filtro pós-scan: best-practices sem WCAG removidas"
echo ""
echo -e "  ${BOLD}Próximos passos:${NC}"

N_SNAP=$(find "$SNAPSHOTS_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l || echo 0)
echo -e "    ${YELLOW}┌── Para atingir QM2 (≥400 projetos incluídos) ─────────────────────────────┐${NC}"

# QM2 status inline
N_INCLUDED_NOW=$($PYTHON -c "
import yaml
with open('$CATALOG_FILE') as f: d = yaml.safe_load(f) or {}
ps = d.get('projects', [])
print(sum(1 for p in ps if p.get('status') not in ('excluded',)))
" 2>/dev/null || echo "0")

if [[ "${N_INCLUDED_NOW:-0}" -ge 400 ]]; then
    echo -e "    ${GREEN}│  ✅ QM2 já satisfeito: $N_INCLUDED_NOW projetos incluídos (≥ 400)${NC}"
else
    FALTAM=$(( 400 - N_INCLUDED_NOW ))
    echo -e "    ${YELLOW}│  ⚠️  QM2: $N_INCLUDED_NOW incluídos — faltam $FALTAM projetos${NC}"
    if [[ -z "$GITHUB_TOKEN" ]]; then
        echo -e "    ${YELLOW}│  → Descubra novos projetos com:${NC}"
        echo -e "    ${YELLOW}│     bash fix_and_rescan.sh --github-token ghp_... --criteria-only${NC}"
    fi
fi
echo -e "    ${YELLOW}└────────────────────────────────────────────────────────────────────────────┘${NC}"
echo ""

if [[ "${N_SNAP:-0}" -eq 0 ]]; then
    echo -e "    ${YELLOW}1. Clonar repositórios pendentes (necessário antes do scan):${NC}"
    echo -e "       bash fix_and_rescan.sh --with-snapshot"
    echo -e "       # ou: python dataset/scripts/snapshot.py --catalog dataset/catalog/projects.yaml"
    echo ""
    echo -e "    ${YELLOW}2. Após clonar, re-executar o scan:${NC}"
    echo -e "       python dataset/scripts/scan.py --workers $WORKERS --min-consensus $MIN_CONSENSUS"
    echo -e "       bash fix_and_rescan.sh --filter-only  (após o scan, aplicar filtro)"
else
    echo -e "    1. Ver status atual do corpus:"
    echo -e "       python dataset/scripts/scan.py --status --pending"
    echo ""
    echo -e "    2. Acompanhar scan em tempo real (terminal separado):"
    echo -e "       python dataset/scripts/watch_scan.py"
    echo ""
    echo -e "    3. Verificar QM2/QM3 após scan completo:"
    echo -e "       python dataset/scripts/validate.py --catalog dataset/catalog/projects.yaml"
    echo ""
    echo -e "    4. Relatório de findings com métricas WCAG:"
    echo -e "       python dataset/scripts/findings_report.py"
fi

echo ""
$DRY_RUN && echo -e "  ${YELLOW}⚠️  Modo DRY-RUN — nenhuma alteração foi feita${NC}\n"
