#!/usr/bin/env bash
# run_arch_v5_functional.sh
# ─────────────────────────────────────────────────────────────────────────────
# Re-run of the architectural-ablation V5 condition (arch_no_internal_validation)
# with the Layer-2 FUNCTIONAL CHECK now active, then regenerate the report.
#
# WHY: the validation pipeline is bypassed as a GATE in V5, but Layer 2
# (structural functional preservation) is now MEASURED, so we can tell whether a
# Pa11y-credited fix also broke the component. The original V5 runs predate this
# instrumentation, so they must be re-executed to populate the new fields
# (functional_regression / resolved_functional_clean).
#
# This script ONLY re-runs V5 (3 reps). The other four conditions do not need a
# re-run: Layer 2 was an enforced gate for them, so resolved_functional_clean
# equals resolved by construction (the report falls back to that automatically).
#
# Must be run on the machine that HAS the corpus source (dataset/snapshots/) and
# a working Ollama + Pa11y/Chromium harness — i.e. NOT a results-only checkout.
#
# Usage:
#   ./run_arch_v5_functional.sh             # backup old V5, re-run, report
#   ./run_arch_v5_functional.sh --dry-run   # show the runner plan, do nothing
#   ./run_arch_v5_functional.sh --report-only   # skip the re-run, just rebuild report
#   PYTHON=python3.12 ./run_arch_v5_functional.sh   # pin the interpreter
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="ablation_study/config/arch_ablation_pipeline_config.yaml"
RESULTS_DIR="ablation_study/results/arch_ablation_pipeline"
V5_DIR="$RESULTS_DIR/arch_no_internal_validation"
REPORT_DIR="$RESULTS_DIR/report"
MODEL="qwen2.5-coder-7b"
CONDITION="arch_no_internal_validation"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info(){ echo -e "${CYAN}[INFO]${NC} $*"; }
ok(){ echo -e "${GREEN}[OK]${NC}   $*"; }
warn(){ echo -e "${YELLOW}[WARN]${NC} $*"; }
err(){ echo -e "${RED}[ERR]${NC}  $*" >&2; }

DRY_RUN=false
REPORT_ONLY=false
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=true ;;
    --report-only) REPORT_ONLY=true ;;
    --help|-h) sed -n '2,30p' "$0"; exit 0 ;;
    *) err "unknown option: $a"; exit 1 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { err "Python not found (set PYTHON=)"; exit 1; }
# Prefer an activated venv if present
[[ -f .venv/bin/activate ]] && { source .venv/bin/activate; info "venv: .venv"; }

echo -e "${BOLD}== PRE-FLIGHT ==${NC}"
"$PYTHON" --version
[[ -f "$CONFIG" ]] || { err "config not found: $CONFIG"; exit 1; }; ok "config: $CONFIG"

# Corpus source must be present (this is the whole point of running here).
if [[ ! -d dataset/snapshots ]] || [[ -z "$(ls -A dataset/snapshots 2>/dev/null)" ]]; then
  err "dataset/snapshots/ is missing or EMPTY — the corpus source is required."
  err "This script must run on the machine that holds the cloned project snapshots."
  exit 1
fi
ok "corpus snapshots present: $(find dataset/snapshots -maxdepth 1 -mindepth 1 -type d | wc -l) projects"
[[ -d dataset/results ]] || { err "dataset/results/ (scan_results.json) missing"; exit 1; }; ok "scan results present"

# Ollama up + model pulled
if ! curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  err "Ollama server is not reachable at http://localhost:11434 — start it: 'ollama serve'"
  exit 1
fi
ok "Ollama reachable"
if ! ollama list 2>/dev/null | grep -q "qwen2.5-coder:7b\|$MODEL"; then
  warn "model '$MODEL' not found in 'ollama list' — pull it: ollama pull qwen2.5-coder:7b"
fi

if [[ "$REPORT_ONLY" == false ]]; then
  echo -e "\n${BOLD}== RE-RUN V5 (arch_no_internal_validation, 3 reps) ==${NC}"
  if [[ "$DRY_RUN" == true ]]; then
    "$PYTHON" -m ablation_study.src.arch_ablation_pipeline_runner \
      --config "$CONFIG" --condition "$CONDITION" --model "$MODEL" --dry-run
    info "dry-run only — nothing re-run, no report rebuilt."
    exit 0
  fi

  # Back up and remove the old V5 results so the runner regenerates them
  # (the runner resumes by skipping cells whose summary.json already exists).
  if [[ -d "$V5_DIR" ]]; then
    BACKUP="${V5_DIR}.pre_functional.$(date +%Y%m%d_%H%M%S)"
    mv "$V5_DIR" "$BACKUP"
    ok "old V5 results backed up → $BACKUP"
  fi

  time "$PYTHON" -m ablation_study.src.arch_ablation_pipeline_runner \
    --config "$CONFIG" --condition "$CONDITION" --model "$MODEL"
  ok "V5 re-run complete"
fi

echo -e "\n${BOLD}== REPORT ==${NC}"
"$PYTHON" -m ablation_study.analysis.arch_report \
  --results-dir "$RESULTS_DIR" --output-dir "$REPORT_DIR"

echo -e "\n${BOLD}== DONE ==${NC}"
ok "report: $REPORT_DIR"
echo "  - ablation_table_ifr.tex / ablation_table_tests.tex   (file-level, Holm-Bonferroni)"
echo "  - arch_table_functional_clean.tex + functional_clean_ifr.json  (NEW V5 decomposition)"
echo "  - stats_results.json (metadata.pairing_unit should read 'file')"
echo ""
echo "  Verify the functional check populated:"
echo "    grep has_functional $REPORT_DIR/functional_clean_ifr.json   # expect true"
