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
PER_DOMAIN=5; SEED=42; SCAN_WORKERS=4; SNAP_WORKERS=8
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-discover) SKIP_DISCOVER=1 ;;
    --quick)         QUICK=1 ;;
    --from)          FROM_STAGE="$2"; shift ;;
    --per-domain)    PER_DOMAIN="$2"; shift ;;
    --seed)          SEED="$2"; shift ;;
    --workers)       SCAN_WORKERS="$2"; shift ;;
    --snap-workers)  SNAP_WORKERS="$2"; shift ;;
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

# Grava etapa atual em logs/current_stage.txt (sobrescreve) e no log geral.
# Permite ver em que etapa o processo está mesmo após reconectar:
#   cat logs/current_stage.txt
STAGE_FILE="$LOG_DIR/current_stage.txt"
stage_status() {  # stage_status <mensagem>
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" \
    | tee "$STAGE_FILE" >> "$RUN_LOG"
}

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

# Poller DETALHADO do experimento: bloco multi-linha que se atualiza no lugar,
# mostrando repetição atual e, por modelo, status / progresso / sucesso / falha /
# issues corrigidos / tempo médio / ETA / arquivo atual. Permite acompanhar o
# experimento por completo sem precisar abrir o log gigante.
experiment_poller() {  # experiment_poller <pid>
  local pid=$1 prev=0 tty=0
  [[ -t 1 ]] && tty=1
  [[ $tty -eq 1 ]] && { tput civis 2>/dev/null || true; }
  while kill -0 "$pid" 2>/dev/null; do
    local block
    block=$(python3 - <<'PYEOF' 2>/dev/null
import json, glob, os
files = glob.glob("experiment-results/*/experiment_progress.json")
if not files:
    raise SystemExit
d = json.load(open(max(files, key=os.path.getmtime)))
models = d.get("models", {})
rep = d.get("current_repetition", 1); treps = d.get("total_repetitions", 1)
tf = d.get("total_files", 0)
def eta(s):
    s = int(s or 0)
    if s <= 0: return "—"
    h, r = divmod(s, 3600); m = r // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"
gdone = sum(m.get("done", 0) for m in models.values())
gtot = tf * max(1, len(models))
gpct = (gdone * 100 // gtot) if gtot else 0
print(f"  Rep {rep}/{treps} · progresso global {gpct}% ({gdone}/{gtot} arquivos-modelo)")
icons = {"done": "✓", "running": "▶", "loading": "…", "pending": "·", "error": "✗"}
for name, m in models.items():
    st = m.get("status", "pending")
    ic = icons.get(st, "·")
    done = m.get("done", 0); ok = m.get("success", 0); fail = m.get("failed", 0)
    fix = m.get("issues_fixed", 0); avg = m.get("avg_time_per_file_s")
    avg_s = f"{avg:.0f}s/arq" if avg else "—"
    line = f"    {ic} {name:20s} {st:8s} {done}/{tf}  ok={ok} fail={fail} fix={fix}  {avg_s}"
    if st == "running":
        cur = (m.get("current_file") or "")[:36]
        line += f"  ETA {eta(m.get('eta_seconds'))}  {cur}"
    print(line[:170])
PYEOF
)
    if [[ -n "$block" ]]; then
      local n; n=$(printf '%s\n' "$block" | wc -l)
      if [[ $tty -eq 1 && $prev -gt 0 ]]; then printf "\033[%dA\033[J" "$prev"; fi
      printf '%s\n' "$block"
      prev=$n
    fi
    sleep 5
  done
  [[ $tty -eq 1 ]] && { tput cnorm 2>/dev/null || true; }
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
stage_status "[1/7] preflight — rodando"
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

# Ollama: servidor + modelos do template.
# Resolve cada NOME de modelo do template (ex.: codellama-7b) para o model_id
# REAL definido em models.yaml (ex.: codellama:7b-instruct) e confere o tag
# EXATO baixado. A versão antiga fazia match difuso pelo nome (regex em '-'),
# o que dava falso-positivo: codellama:7b (base) "casava" com codellama-7b
# mesmo sem codellama:7b-instruct → 404 no experimento (run dffbe9ec).
if curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  ok "ollama server"
  # Lista "nome_no_template<TAB>model_id<TAB>backend" para modelos ollama.
  MODEL_MAP=$(python3 - "$TEMPLATE_YAML" <<'PYEOF'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
try:
    reg = yaml.safe_load(open("models.yaml")).get("models", {})
except Exception:
    reg = {}
for name in cfg.get("models", []):
    m = reg.get(name, {})
    backend = m.get("backend", "ollama")
    model_id = m.get("model_id", name)
    print(f"{name}\t{model_id}\t{backend}")
PYEOF
)
  TAGS_NOW=$(curl -s http://localhost:11434/api/tags | python3 -c "
import json,sys
try:
    [print(m['name']) for m in json.load(sys.stdin).get('models',[])]
except Exception: pass" 2>/dev/null || echo "")
  while IFS=$'\t' read -r m_name m_id m_backend; do
    [[ -z "$m_name" ]] && continue
    if [[ "$m_backend" != "ollama" ]]; then
      ok "modelo $m_name ($m_id, backend=$m_backend — não-ollama, pulado)"
      continue
    fi
    if echo "$TAGS_NOW" | grep -qx "$m_id"; then
      ok "modelo $m_name → $m_id"
    else
      warn "modelo '$m_name' → tag '$m_id' ausente no Ollama — baixando…"
      if ollama pull "$m_id" 2>&1 | tail -1; then
        ok "modelo $m_name → $m_id (baixado)"
      else
        echo "  ${R}✗${N} falha ao baixar '$m_id' — rode manualmente: ollama pull $m_id"
        FAILED=1
      fi
    fi
  done <<< "$MODEL_MAP"
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
stage_status "[1/7] preflight — OK ($(elapsed $T0))"
fi

# ═════════════════════════════ 2. DESCOBERTA ════════════════════════════════
if stage_enabled discover; then
banner 2 "Descoberta — GitHub Search estratificada"
stage_status "[2/7] discover — rodando"
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
stage_status "[2/7] discover — OK ($(elapsed $T0))  |  retomar: --from snapshot"
fi

# ═════════════════════════════ 3. SNAPSHOT ══════════════════════════════════
if stage_enabled snapshot; then
banner 3 "Snapshot — clone raso + pin de commit  (workers=$SNAP_WORKERS)"
stage_status "[3/7] snapshot — rodando  (workers=$SNAP_WORKERS)"
T0=$(date +%s)
# Roda em foreground com tee: cada clone aparece ao vivo no terminal e vai para o log.
# python3 -u garante saída sem buffer mesmo dentro do pipe.
python3 -u dataset/scripts/snapshot.py --catalog "$CATALOG" --workers "$SNAP_WORKERS" \
  2>&1 | tee -a "$RUN_LOG"
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "snapshot falhou. Log: $RUN_LOG"
ok "snapshot em $(elapsed $T0)"
stage_status "[3/7] snapshot — OK ($(elapsed $T0))  |  retomar: --from scan"
fi

# ═════════════════════════════ 4. SCAN ══════════════════════════════════════
if stage_enabled scan; then
banner 4 "Scan — detecção multi-ferramenta  (workers=$SCAN_WORKERS, etapa longa)"
stage_status "[4/7] scan — rodando  (workers=$SCAN_WORKERS)"
T0=$(date +%s)
SCAN_ARGS=(--catalog "$CATALOG" --workers "$SCAN_WORKERS")
[[ $QUICK -eq 1 ]] && SCAN_ARGS+=(--max-files 5)
# Roda em foreground com tee: cada projeto aparece ao vivo (scan.py é async internamente).
python3 -u dataset/scripts/scan.py "${SCAN_ARGS[@]}" 2>&1 | tee -a "$RUN_LOG"
[[ ${PIPESTATUS[0]} -eq 0 ]] || die "scan falhou. Log: $RUN_LOG  |  retomar: --from scan"
ok "scan em $(elapsed $T0)"
stage_status "[4/7] scan — OK ($(elapsed $T0))  |  retomar: --from validate"
fi

# ═════════════════════════════ 5. VALIDAÇÃO ═════════════════════════════════
if stage_enabled validate; then
banner 5 "Validação — consistência do dataset"
stage_status "[5/7] validate — rodando"
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
stage_status "[5/7] validate — OK ($(elapsed $T0))  |  retomar: --from select"
fi

# ═════════════════════════════ 6. SELEÇÃO ═══════════════════════════════════
if stage_enabled select; then
banner 6 "Seleção — estratificada e seedada"
stage_status "[6/7] select — rodando"
T0=$(date +%s)
SEL_PER_DOMAIN=$PER_DOMAIN
[[ $QUICK -eq 1 ]] && SEL_PER_DOMAIN=1
run_logged "select.py (seed=$SEED, $SEL_PER_DOMAIN/domínio)" \
  python3 dataset/scripts/select.py \
    --per-domain "$SEL_PER_DOMAIN" --seed "$SEED" \
    --template "$TEMPLATE_YAML" --output "$EXPERIMENT_YAML"
ok "seleção em $(elapsed $T0) → $EXPERIMENT_YAML"
stage_status "[6/7] select — OK ($(elapsed $T0))  |  retomar: --from experiment"
fi

# ═════════════════════════════ 7. EXPERIMENTO ═══════════════════════════════
if stage_enabled experiment; then
banner 7 "Experimento — modelos × repetições"
stage_status "[7/7] experiment — rodando"
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

# A saída crua do runner vai para o log (gigante); o acompanhamento ao vivo é
# feito pelo experiment_poller, que lê experiment_progress.json e mostra o
# progresso completo por modelo/repetição no terminal.
echo "  Acompanhe também: tail -f $RUN_LOG   |   python3 watch_experiment.py"
python3 -m a11y_autofix.cli experiment run "$RUN_YAML" >> "$RUN_LOG" 2>&1 &
EXP_PID=$!
experiment_poller $EXP_PID
wait $EXP_PID || die "experimento falhou. Log: $RUN_LOG (veja o erro/erros acima; checkpoints preservados — re-execute com --from experiment)"
ok "experimento em $(elapsed $T0)"
stage_status "[7/7] experiment — OK ($(elapsed $T0))  |  PIPELINE COMPLETO"
fi

# ═════════════════════════════ RESULTADOS ═══════════════════════════════════
echo ""
echo "${B}${G}══════════════════ PIPELINE COMPLETO em $(elapsed $T0_GLOBAL) ══════════════════${N}"
LATEST=$(ls -td experiment-results/*/ 2>/dev/null | head -1)
if [[ -n "$LATEST" ]]; then
  # Status por modelo — torna visível qualquer condição que processou 0 arquivos
  # (mesmo que o experimento como um todo tenha terminado).
  if [[ -f "$LATEST/experiment_progress.json" ]]; then
    echo "${B}Status por modelo:${N}"
    python3 - "$LATEST/experiment_progress.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
for name, m in d.get("models", {}).items():
    done, ok_, failed = m.get("done", 0), m.get("success", 0), m.get("failed", 0)
    if done == 0:
        print(f"  \033[31m✗ {name}: 0 arquivos processados — CONDIÇÃO FALHOU\033[0m")
    elif failed:
        print(f"  \033[33m⚠ {name}: {ok_}/{done} ok, {failed} falha(s)\033[0m")
    else:
        print(f"  \033[32m✓ {name}: {ok_}/{done} ok\033[0m")
PYEOF
    echo ""
  fi
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
