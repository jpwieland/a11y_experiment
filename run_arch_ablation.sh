#!/usr/bin/env bash
# run_arch_ablation.sh
# ─────────────────────────────────────────────────────────────────────────────
# Architectural Ablation Study runner
#
# Executes all 5 conditions × N_MODELS × 3 repetitions sequentially.
# Skips already-completed cells (summary.json exists) automatically,
# so re-running after an interruption resumes from where it left off.
#
# Usage:
#   ./run_arch_ablation.sh                          # full study
#   ./run_arch_ablation.sh --dry-run                # list cells without running
#   ./run_arch_ablation.sh --condition arch_full    # single condition
#   ./run_arch_ablation.sh --condition arch_no_reflection arch_full
#   ./run_arch_ablation.sh --model qwen2.5-coder-7b
#   ./run_arch_ablation.sh --rep 1                  # only repetition 1
#   ./run_arch_ablation.sh --reset                  # wipe results and restart
#   ./run_arch_ablation.sh --list-conditions        # print all condition IDs
#   ./run_arch_ablation.sh --analyze-only           # run stats on existing results
#   ./run_arch_ablation.sh --help
#
# Prerequisites:
#   - Python ≥ 3.11 with packages from pyproject.toml installed
#   - Ollama running locally (or adjust base_url in models.yaml)
#   - dataset/results/ populated with scan_results.json files
#   - experiments/chosen_experiment.yaml present (for project selection)
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
CONFIG="$REPO_ROOT/ablation_study/config/arch_ablation_config.yaml"
RESULTS_DIR="$REPO_ROOT/ablation_study/results/arch_ablation"
STATS_OUT="$RESULTS_DIR/stats_report.json"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${BOLD}════════════════════════════════════════${NC}"; \
             echo -e "${BOLD}  $*${NC}"; \
             echo -e "${BOLD}════════════════════════════════════════${NC}\n"; }

# ── Help ──────────────────────────────────────────────────────────────────────
usage() {
cat <<EOF
${BOLD}Architectural Ablation Study Runner${NC}

Usage: $0 [OPTIONS]

Options:
  --condition ID [ID...]   Run only specific condition(s)
  --model NAME             Run only specific model
  --rep N [N...]           Run only specific repetition(s) (1, 2, 3)
  --dry-run                List planned runs without executing
  --reset                  Delete results directory and restart from scratch
  --list-conditions        Print all available condition IDs and exit
  --analyze-only           Run statistical analysis on existing results
  --skip-ollama-check      Skip Ollama pre-flight check (not recommended)
  --no-auto-pull           Do not auto-pull missing Ollama models
  --corpus-dir DIR         Override corpus directory (default: dataset/results/)
  --help                   Show this help

Conditions:
  arch_full                   Full pipeline (control)
  arch_no_reflection          No retry feedback loop (V2)
  arch_no_few_shot            No few-shot examples / zero-shot (V3)
  arch_no_wcag_guidelines     No WCAG semantic descriptions (V4)
  arch_no_internal_validation No L1-L3 validation (V5)

Examples:
  # Run only the control and V2 to test reflection quickly
  $0 --condition arch_full arch_no_reflection --rep 1

  # Full study on a single model
  $0 --model qwen2.5-coder-7b

  # Run analysis only (study already completed)
  $0 --analyze-only

EOF
  exit 0
}

# ── Parse arguments ───────────────────────────────────────────────────────────
RUNNER_ARGS=()
ANALYZE_ONLY=false
CONDITION_ARGS=()
MODEL_ARGS=()
REP_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)         usage ;;
    --dry-run)         RUNNER_ARGS+=("--dry-run"); shift ;;
    --reset)           RUNNER_ARGS+=("--reset"); shift ;;
    --list-conditions) RUNNER_ARGS+=("--list-conditions"); shift ;;
    --skip-ollama-check) RUNNER_ARGS+=("--skip-ollama-check"); shift ;;
    --no-auto-pull)    RUNNER_ARGS+=("--no-auto-pull"); shift ;;
    --analyze-only)    ANALYZE_ONLY=true; shift ;;
    --condition)
      shift
      while [[ $# -gt 0 && ! "$1" == --* ]]; do
        CONDITION_ARGS+=("$1"); shift
      done ;;
    --model)
      shift
      while [[ $# -gt 0 && ! "$1" == --* ]]; do
        MODEL_ARGS+=("$1"); shift
      done ;;
    --rep)
      shift
      while [[ $# -gt 0 && ! "$1" == --* ]]; do
        REP_ARGS+=("$1"); shift
      done ;;
    --corpus-dir)
      shift; RUNNER_ARGS+=("--corpus-dir" "$1"); shift ;;
    *)
      error "Unknown option: $1"; exit 1 ;;
  esac
done

# Build condition/model/rep filters
[[ ${#CONDITION_ARGS[@]} -gt 0 ]] && RUNNER_ARGS+=("--condition" "${CONDITION_ARGS[@]}")
[[ ${#MODEL_ARGS[@]}    -gt 0 ]] && RUNNER_ARGS+=("--model"     "${MODEL_ARGS[@]}")
[[ ${#REP_ARGS[@]}      -gt 0 ]] && RUNNER_ARGS+=("--rep"       "${REP_ARGS[@]}")

# ── Environment ───────────────────────────────────────────────────────────────
cd "$REPO_ROOT"

# Activate virtual environment if present
if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source ".venv/bin/activate"
  info "Virtual environment activated: .venv"
elif [[ -f "venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "venv/bin/activate"
  info "Virtual environment activated: venv"
fi

# Verify Python
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
  error "Python not found. Set PYTHON= or activate a virtualenv."; exit 1
fi
PYTHON_VERSION=$("$PYTHON" --version 2>&1)
info "Python: $PYTHON_VERSION"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
section "PRE-FLIGHT"

if [[ ! -f "$CONFIG" ]]; then
  error "Config not found: $CONFIG"
  exit 1
fi
success "Config found: $CONFIG"

DATASET_DIR="$REPO_ROOT/dataset/results"
if [[ ! -d "$DATASET_DIR" ]]; then
  error "dataset/results/ not found at $DATASET_DIR"
  error "Run the main scan pipeline first to populate scan results."
  exit 1
fi
N_PROJECTS=$(find "$DATASET_DIR" -name "scan_results.json" | wc -l)
success "Corpus: $N_PROJECTS projects with scan_results.json"

SELECTION_FILE="$REPO_ROOT/experiments/chosen_experiment.yaml"
if [[ -f "$SELECTION_FILE" ]]; then
  success "Project selection: $SELECTION_FILE"
else
  warn "chosen_experiment.yaml not found — will use full corpus"
fi

# ── Analyze-only mode ─────────────────────────────────────────────────────────
if [[ "$ANALYZE_ONLY" == true ]]; then
  section "STATISTICAL ANALYSIS"
  if [[ ! -d "$RESULTS_DIR" ]]; then
    error "Results directory not found: $RESULTS_DIR"
    error "Run the study first before analyzing."
    exit 1
  fi
  info "Running statistical analysis on: $RESULTS_DIR"
  "$PYTHON" -m ablation_study.src.statistical_analysis \
    --results-dir "$RESULTS_DIR" \
    --baseline "arch_full" \
    --output "$STATS_OUT" \
    --n-bootstrap 10000 \
    --alpha 0.05
  success "Analysis complete: $STATS_OUT"
  exit 0
fi

# ── Run ablation study ────────────────────────────────────────────────────────
section "ARCHITECTURAL ABLATION STUDY"
info "Config:      $CONFIG"
info "Results dir: $RESULTS_DIR"
info "Runner args: ${RUNNER_ARGS[*]:-<none>}"

START_TIME=$(date +%s)

"$PYTHON" -m ablation_study.src.ablation_runner \
  --config "$CONFIG" \
  "${RUNNER_ARGS[@]}"

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
ELAPSED_MIN=$((ELAPSED / 60))
ELAPSED_SEC=$((ELAPSED % 60))

# ── Post-run analysis ─────────────────────────────────────────────────────────
section "POST-RUN ANALYSIS"

if [[ -d "$RESULTS_DIR" ]] && find "$RESULTS_DIR" -name "violations.jsonl" | grep -q .; then
  info "Running statistical analysis..."
  "$PYTHON" -m ablation_study.src.statistical_analysis \
    --results-dir "$RESULTS_DIR" \
    --baseline "arch_full" \
    --output "$STATS_OUT" \
    --n-bootstrap 10000 \
    --alpha 0.05 \
  && success "Stats report: $STATS_OUT" \
  || warn "Statistical analysis failed — run manually with --analyze-only"
else
  warn "No violations.jsonl found — skipping analysis"
  warn "Run manually: $0 --analyze-only"
fi

section "DONE"
success "Wall clock: ${ELAPSED_MIN}m ${ELAPSED_SEC}s"
success "Results: $RESULTS_DIR"
echo ""
echo -e "  Next steps:"
echo -e "  1. Review stats report:  ${CYAN}cat $STATS_OUT | python3 -m json.tool${NC}"
echo -e "  2. View condition IFRs:  ${CYAN}$0 --analyze-only${NC}"
echo -e "  3. Open progress dashboard (if running): ${CYAN}python3 ablation_study/src/progress_dashboard.py${NC}"
