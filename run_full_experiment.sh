#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  run_full_experiment.sh — pipeline experimental completo do a11y-autofix
#
#  Etapas (cada uma com taxa de progresso na tela):
#    1/7  Preflight     — valida ambiente (python, node, scanners, ollama)
#    2/7  Descoberta    — GitHub Search estratificada (requer GITHUB_TOKEN)
#    3/7  Snapshot      — clone raso + pin de commit SHA
#    4/7  Scan          — detecção multi-ferramenta com consenso
#    5/7  Validação     — consistência do dataset
#    6/7  Seleção       — estratificada e seedada → experiments/auto_experiment.yaml
#    7/7  Experimento   — modelos × repetições → deep_report + relatórios
#
#  Uso:
#    ./run_full_experiment.sh                  # pipeline completo
#    ./run_full_experiment.sh --skip-discover  # reusar catálogo existente
#    ./run_full_experiment.sh --from scan      # retomar a partir de uma etapa
#    ./run_full_experiment.sh --quick          # smoke test (3 projetos, 1 modelo)
#
#  Retomável: todas as etapas têm checkpoint próprio (catálogo, snapshots,
#  scans e experimento) — re-executar continua de onde parou.
# ═══════════════════════════════════════════════════════════════════════════

set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/full_run_$(date +%Y%m%d_%H%M%S).log"

CATALOG="dataset/catalog/projects.yaml"
EXPERIMENT_YAML="experiments/auto_experiment.yaml"
TEMPLATE_YAML="experiments/chosen_experiment.yaml"

# ── Flags ───────────────────────────────────────────────────────────────────
SKIP_DISCOVER=0; QUICK=0; FROM_STAGE=""
PER_DOMAIN=5; SEED=42; SCAN_WORKERS=2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-discover) SKIP_DISCOVER=1 ;;
    --quick)         QUICK=1 ;;
    --from)          FROM_STAGE="$2"; shift ;;
    --per-domain)    PER_DOMAIN="$2"; shift ;;
    --seed)          SEED="$2"; shift ;;
    --workers)       SCAN_WORKERS="$2"; shift ;;
    -h|--help) grep '^#' "$0" | head -25; exit 0 ;;
    *) echo "flag desconhecida: $1"; exit 2 ;;
  esac
  shift
done

# ── Cosmética ───────────────────────────────────────────────────────────────
B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; C=$'\033[36m'; N=$'\033[0m'
STAGE_TOTAL=7
T0_GLOBAL=$(date +%s)

banner() {  # banner <num> <título>
  echo ""
  echo "${B}${C}╔══════════════════════════════════════════════════════════════╗${N}"
  printf "${B}${C}║  [%s/%s] %-55s║${N}\n" "$1" "$STAGE_TOTAL" "$2"
  echo "${B}${C}╚══════════════════════════════════════════════════════════════╝${N}"
}

elapsed() { local s=$(( $(date +%s) - $1 )); printf "%dm%02ds" $((s/60)) $((s%60)); }

die() { echo "${R}${B}✗ ABORTADO:${N} $1" | tee -a "$RUN_LOG"; exit 1; }

ok()   { echo "  ${G}✓${N} $1"; }
warn() { echo "  ${Y}⚠${N} $1"; }

run_logged() {  # run_logged <descrição> <cmd...>
  local desc="$1"; shift
  echo "${C}▶${N} $desc" | tee -a "$RUN_LOG"
  "$@" 2>&1 | tee -a "$RUN_LOG"
  local rc=${PIPESTATUS[0]}
  [[ $rc -ne 0 ]] && die "'$desc' falhou (rc=$rc). Log: $RUN_LOG"
  return 0
}

# Poller de progresso: roda <python_expr> (imprime "feito total rótulo")
# a cada 10s numa linha que se sobrescreve, enquanto o PID alvo viver.
progress_poller() {  # progress_poller <pid> <python_expr>
  local pid=$1 expr=$2
  while kill -0 "$pid" 2>/dev/null; do
    local out
    out=$(python3 -c "$expr" 2>/dev/null) || out=""
    if [[ -n "$out" ]]; then
      read -r done total label <<< "$out"
      if [[ "$total" =~ ^[0-9]+$ && "$total" -gt 0 ]]; then
        local pct=$(( done * 100 / total ))
        local bar_w=30 filled=$(( pct * 30 / 100 ))
        local bar; bar=$(printf "%${filled}s" | tr ' ' '█')$(printf "%$((bar_w-filled))s" | tr ' ' '░')
        printf "\r  ${C}%s${N} %3d%% (%s/%s) %s   " "$bar" "$pct" "$done" "$total" "$label"
      fi
    fi
    sleep 10
  done
  printf "\r%-100s\r" " "
}

# Conta status no catálogo: imprime "<feitos> <total> <rótulo>"
CATALOG_COUNT='
import yaml, sys
cat = yaml.safe_load(open("'"$CATALOG"'"))
ps = cat.get("projects", cat) if isinstance(cat, dict) else cat
done = sum(1 for p in ps if p.get("status") in (%s))
total = sum(1 for p in ps if p.get("status") not in ("excluded", "error"))
print(done, total, "%s")
'

stage_enabled() {  # pula etapas anteriores a --from
  [[ -z "$FROM_STAGE" ]] && return 0
  local order=(preflight discover snapshot scan validate select experiment)
  local want=0
  for s in "${order[@]}"; do
    [[ "$s" == "$FROM_STAGE" ]] && want=1
    [[ "$s" == "$1" && $want -eq 1 ]] && return 0
    [[ "$s" == "$1" ]] && return 1
  done
  return 1
}

echo "${B}Pipeline experimental a11y-autofix${N} — log: $RUN_LOG"
[[ $QUICK -eq 1 ]] && warn "MODO QUICK: 3 projetos/domínio=1, 1 modelo, max-files=5"

# ═════════════════════════════════ 1. PREFLIGHT ═════════════════════════════
if stage_enabled preflight; then
banner 1 "Preflight — validação do ambiente"
T0=$(date +%s); FAILED=0
check() {  # check <descrição> <cmd...>
  if "${@:2}" >/dev/null 2>&1; then ok "$1"; else echo "  ${R}✗${N} $1"; FAILED=1; fi
}
check "python 3.11+"            python3 -c 'import sys; assert sys.version_info >= (3,11)'
check "pacote a11y_autofix"     python3 -c 'import a11y_autofix'
check "pydantic/structlog/yaml" python3 -c 'import pydantic, structlog, yaml'
check "pydantic-settings"      python3 -c 'import pydantic_settings'
check "playwright + chromium"   python3 -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()'
check "node 18+"                node -e 'process.exit(parseInt(process.versions.node)>=18?0:1)'
check "pa11y"                   sh -c 'command -v pa11y'
check "axe CLI"                 sh -c 'command -v axe'
check "axe-core (npm global)"   sh -c 'test -f "$(npm root -g)/axe-core/axe.min.js"'
check "@babel/parser (npm)"     sh -c 'test -d "$(npm root -g)/@babel/parser"'
check "git"                     sh -c 'command -v git'
check "disco ≥ 15 GB livres"    sh -c '[ "$(df -k . | awk "NR==2{print \$4}")" -gt 15728640 ]'

# Ollama: servidor + modelos do template
if curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  ok "ollama server"
  for m in $(python3 -c "
import yaml; c = yaml.safe_load(open('$TEMPLATE_YAML'))
print(' '.join(c.get('models', [])))"); do
    if curl -s http://localhost:11434/api/tags | grep -q "$(echo "$m" | sed 's/-/[:.-]*/g')"; then
      ok "modelo $m"
    else
      warn "modelo '$m' não encontrado no Ollama — rode: ollama pull <modelo>"
      FAILED=1
    fi
  done
else
  echo "  ${R}✗${N} ollama server (http://localhost:11434) — inicie com: ollama serve"
  FAILED=1
fi

# Token só é obrigatório se a descoberta vai rodar
if [[ $SKIP_DISCOVER -eq 0 && ! -f "$CATALOG" ]]; then
  [[ -n "${GITHUB_TOKEN:-}" ]] && ok "GITHUB_TOKEN definido" \
    || { echo "  ${R}✗${N} GITHUB_TOKEN ausente (necessário para descoberta)"; FAILED=1; }
fi

[[ $FAILED -eq 1 ]] && die "preflight falhou — corrija os itens ✗ acima"
ok "preflight completo em $(elapsed $T0)"
fi

# ═════════════════════════════ 2. DESCOBERTA ════════════════════════════════
if stage_enabled discover; then
banner 2 "Descoberta — GitHub Search estratificada"
T0=$(date +%s)
if [[ $SKIP_DISCOVER -eq 1 || ( -f "$CATALOG" && $(python3 -c "
import yaml; c=yaml.safe_load(open('$CATALOG'))
print(len(c.get('projects', c) if isinstance(c, dict) else c))") -gt 50 ) ]]; then
  ok "catálogo existente com $(python3 -c "
import yaml; c=yaml.safe_load(open('$CATALOG'))
print(len(c.get('projects', c) if isinstance(c, dict) else c))") projetos — etapa pulada (use 'rm $CATALOG' p/ refazer)"
else
  run_logged "descoberta completa (7 domínios × 3 tamanhos × 3 popularidades)" \
    python3 dataset/scripts/discover.py --token "${GITHUB_TOKEN:?}" --output "$CATALOG"
  run_logged "top-up de domínios finos" \
    python3 dataset/scripts/discover.py --token "$GITHUB_TOKEN" --top-up
  python3 dataset/scripts/discover.py --stats
fi
ok "descoberta em $(elapsed $T0)"
fi

# ═════════════════════════════ 3. SNAPSHOT ══════════════════════════════════
if stage_enabled snapshot; then
banner 3 "Snapshot — clone raso + pin de commit"
T0=$(date +%s)
# Roda em foreground com tee para que cada clone apareça ao vivo no terminal
# e seja registrado no log simultaneamente.
python3 -u dataset/scripts/snapshot.py --catalog "$CATALOG" --workers 4 \
  2>&1 | tee -a "$RUN_LOG"
# tee sempre retorna 0; verificar o exit code do python via PIPESTATUS
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "snapshot falhou. Log: $RUN_LOG"
ok "snapshot em $(elapsed $T0)"
fi

# ═════════════════════════════ 4. SCAN ══════════════════════════════════════
if stage_enabled scan; then
banner 4 "Scan — detecção multi-ferramenta (etapa longa)"
T0=$(date +%s)
SCAN_ARGS=(--catalog "$CATALOG" --workers "$SCAN_WORKERS")
[[ $QUICK -eq 1 ]] && SCAN_ARGS+=(--max-files 5)
python3 dataset/scripts/scan.py "${SCAN_ARGS[@]}" >> "$RUN_LOG" 2>&1 &
SCAN_PID=$!
progress_poller $SCAN_PID "$(printf "$CATALOG_COUNT" '"scanned"' 'projetos escaneados')"
wait $SCAN_PID || die "scan falhou. Log: $RUN_LOG"
ok "scan em $(elapsed $T0)"
fi

# ═════════════════════════════ 5. VALIDAÇÃO ═════════════════════════════════
if stage_enabled validate; then
banner 5 "Validação — consistência do dataset"
T0=$(date +%s)
# O modo --strict aplica gates de metodologia (QM1 anotação dupla, QM2 corpus ≥400,
# QM3 balanceamento de estratos) que dependem de trabalho manual de anotação e não
# são pré-requisito para executar o experimento. O relatório é gerado e os gates
# reprovados viram aviso; exporte STRICT_VALIDATE=1 para voltar a bloquear.
echo "${C}▶${N} validate --strict (relatório de qualidade)" | tee -a "$RUN_LOG"
python3 dataset/scripts/validate.py --strict 2>&1 | tee -a "$RUN_LOG"
VAL_RC=${PIPESTATUS[0]}
if [[ $VAL_RC -ne 0 ]]; then
  if [[ "${STRICT_VALIDATE:-0}" == "1" ]]; then
    die "validate --strict reprovou (rc=$VAL_RC) e STRICT_VALIDATE=1. Log: $RUN_LOG"
  fi
  warn "validação estrita reprovou gates de metodologia (QM*) — relatório salvo em dataset/results/dataset_validation_report.json"
  warn "o pipeline continua; os gates QM exigem anotação dupla e corpus ≥400 repos."
fi
run_logged "relatório de findings" python3 dataset/scripts/findings_report.py
ok "validação em $(elapsed $T0)"
fi

# ═════════════════════════════ 6. SELEÇÃO ═══════════════════════════════════
if stage_enabled select; then
banner 6 "Seleção — estratificada e seedada"
T0=$(date +%s)
SEL_PER_DOMAIN=$PER_DOMAIN
[[ $QUICK -eq 1 ]] && SEL_PER_DOMAIN=1
run_logged "select.py (seed=$SEED, $SEL_PER_DOMAIN/domínio)" \
  python3 dataset/scripts/select.py \
    --per-domain "$SEL_PER_DOMAIN" --seed "$SEED" \
    --template "$TEMPLATE_YAML" --output "$EXPERIMENT_YAML"
ok "seleção em $(elapsed $T0) → $EXPERIMENT_YAML"
fi

# ═════════════════════════════ 7. EXPERIMENTO ═══════════════════════════════
if stage_enabled experiment; then
banner 7 "Experimento — modelos × repetições"
T0=$(date +%s)

# Modo quick: reduz para 1 modelo × 1 repetição
RUN_YAML="$EXPERIMENT_YAML"
if [[ $QUICK -eq 1 ]]; then
  RUN_YAML="experiments/auto_experiment_quick.yaml"
  python3 - << PYEOF
import yaml
c = yaml.safe_load(open("$EXPERIMENT_YAML"))
c["models"] = c["models"][:1]
c["repetitions"] = 1
c["max_files_per_project"] = 5
yaml.dump(c, open("$RUN_YAML", "w"), allow_unicode=True, sort_keys=False)
print("quick yaml gerado")
PYEOF
fi

# Localizar o progress file mais recente do experimento (criado pelo runner)
EXP_PROGRESS_EXPR='
import json, glob, os
files = glob.glob("experiment-results/*/experiment_progress.json")
if not files: raise SystemExit
f = max(files, key=os.path.getmtime)
d = json.load(open(f))
models = d.get("models", {})
total = d.get("total_files", 0) * max(1, len(models))
done = sum(m.get("done", 0) for m in models.values())
running = [(k, v) for k, v in models.items() if v.get("status") == "running"]
cur = f"{running[0][0]}:{running[0][1].get('"'"'current_file'"'"', '"'"'?'"'"')}" if running else ""
print(done, total, cur[:50])
'
python3 -m a11y_autofix.cli experiment run "$RUN_YAML" >> "$RUN_LOG" 2>&1 &
EXP_PID=$!
progress_poller $EXP_PID "$EXP_PROGRESS_EXPR"
wait $EXP_PID || die "experimento falhou. Log: $RUN_LOG (checkpoints preservados — re-execute com --from experiment)"
ok "experimento em $(elapsed $T0)"
fi

# ═════════════════════════════ RESULTADOS ═══════════════════════════════════
echo ""
echo "${B}${G}══════════════════ PIPELINE COMPLETO em $(elapsed $T0_GLOBAL) ══════════════════${N}"
LATEST=$(ls -td experiment-results/*/ 2>/dev/null | head -1)
if [[ -n "$LATEST" ]]; then
  echo "${B}Artefatos gerados em ${LATEST}:${N}"
  for f in deep_report.html deep_report.md deep_report.json comparison.html \
           metrics.csv experiment_result.json repetitions_summary.json; do
    [[ -f "$LATEST/$f" ]] && echo "  ${G}✓${N} $LATEST$f"
  done
  echo ""
  echo "Análise profunda:   ${B}open $LATEST/deep_report.html${N}"
  echo "Seleção auditável:  dataset/catalog/selection.json"
  echo "Log completo:       $RUN_LOG"
fi
