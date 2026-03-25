#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# transfer_data.sh — Transfere snapshots e resultados para o servidor GPU
#
# Uso (a partir da raiz do projeto):
#   bash scripts/transfer_data.sh
#   bash scripts/transfer_data.sh --dry-run   # simular sem transferir
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE_USER="jpvbwieland"
REMOTE_HOST="host.cos.ufrj.br"
REMOTE_PORT="12341"
REMOTE_BASE="/scratch/jpvbwieland/a11y_experiment"
LOCAL_BASE="$(cd "$(dirname "$0")/.." && pwd)"

DRY_RUN=""
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN="--dry-run"

SSH_OPTS="-p $REMOTE_PORT -o StrictHostKeyChecking=accept-new -o Compression=yes"
RSYNC_OPTS="-avz --progress --stats $DRY_RUN"

echo "═══════════════════════════════════════════════════════════"
echo "  ♿  a11y-autofix — Transferência de dados para GPU server"
echo "  Local : $LOCAL_BASE"
echo "  Remoto: $REMOTE_USER@$REMOTE_HOST:$REMOTE_PORT"
echo "═══════════════════════════════════════════════════════════"
[[ -n "$DRY_RUN" ]] && echo "  [DRY RUN — nenhum arquivo será transferido]"
echo ""

# ── 1. Código-fonte do projeto ────────────────────────────────────────────────
echo "→ [1/4] Sincronizando código-fonte..."
rsync $RSYNC_OPTS \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='node_modules/' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='experiment-results/' \
  -e "ssh $SSH_OPTS" \
  "$LOCAL_BASE/" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_BASE/"
echo "   ✔ Código sincronizado"

# ── 2. Snapshots (clones dos repositórios) ────────────────────────────────────
echo ""
echo "→ [2/4] Transferindo snapshots..."
echo "   Atenção: pode demorar bastante (vários GB). Use Ctrl+C para cancelar."
rsync $RSYNC_OPTS \
  --exclude='.git/' \
  --exclude='node_modules/' \
  -e "ssh $SSH_OPTS" \
  "$LOCAL_BASE/dataset/snapshots/" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_BASE/dataset/snapshots/"
echo "   ✔ Snapshots transferidos"

# ── 3. Resultados do scan ─────────────────────────────────────────────────────
echo ""
echo "→ [3/4] Transferindo resultados do scan..."
rsync $RSYNC_OPTS \
  -e "ssh $SSH_OPTS" \
  "$LOCAL_BASE/dataset/results/" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_BASE/dataset/results/"
echo "   ✔ Resultados transferidos"

# ── 4. Catálogo ───────────────────────────────────────────────────────────────
echo ""
echo "→ [4/4] Transferindo catálogo..."
rsync $RSYNC_OPTS \
  -e "ssh $SSH_OPTS" \
  "$LOCAL_BASE/dataset/catalog/" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_BASE/dataset/catalog/"
echo "   ✔ Catálogo transferido"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✔ Transferência concluída!"
echo ""
echo "  Próximo passo — conectar ao servidor:"
echo "  ssh -p $REMOTE_PORT $REMOTE_USER@$REMOTE_HOST"
echo "  cd $REMOTE_BASE"
echo "  bash scripts/setup_gpu_server.sh"
echo "═══════════════════════════════════════════════════════════"
