#!/usr/bin/env bash
# Ablation study do agente de IA — executa as 6 células sequencialmente.
#
# Design (one-factor-at-a-time, baseline compartilhada A1):
#   Eixo A (prompting, IV2): A1 few-shot · A2 zero-shot · A3 chain-of-thought
#   Eixo B (scaffolding):    B1 direct-llm · B2 swe-agent · B3 openhands
#   Valor do router:         A1 (auto) vs melhor célula B
#
# Pré-requisitos:
#   - Ollama rodando com qwen2.5-coder:3b (`ollama pull qwen2.5-coder:3b`)
#   - Scanners instalados (pa11y, axe-core, playwright)
#
# Uso:
#   ./experiments/ablation/run_ablation.sh            # todas as células
#   ./experiments/ablation/run_ablation.sh cell_A2    # uma célula específica

set -uo pipefail
cd "$(dirname "$0")/../.."

PY="${PYTHON:-python3}"
FILTER="${1:-cell_}"

# Pré-requisito: o modelo único da ablação (qwen2.5-coder:3b) precisa estar
# baixado. O runner também garante isso em cold-start, mas checar aqui dá um
# erro imediato e claro antes de iniciar o scan.
if command -v ollama >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "qwen2.5-coder:3b"; then
    echo "⬇ Baixando qwen2.5-coder:3b (ausente)…"
    ollama pull qwen2.5-coder:3b || { echo "✗ Falha ao baixar qwen2.5-coder:3b"; exit 1; }
  fi
else
  echo "⚠ 'ollama' não encontrado no PATH — o runner tentará baixar/servir mesmo assim."
fi

FAILED_CELLS=()
for cfg in experiments/ablation/${FILTER}*.yaml; do
  echo "════════════════════════════════════════════════════════"
  echo "▶ $cfg"
  echo "════════════════════════════════════════════════════════"
  # Não abortamos tudo na 1ª falha: registramos a célula que falhou e seguimos,
  # para que as demais ainda produzam resultados parciais.
  if ! "$PY" -m a11y_autofix.cli experiment run "$cfg"; then
    rc=$?
    echo "✗ Célula FALHOU: $cfg (rc=$rc) — veja o erro acima"
    FAILED_CELLS+=("$cfg")
  fi
done

echo ""
if [[ ${#FAILED_CELLS[@]} -gt 0 ]]; then
  echo "⚠ Ablation incompleto — ${#FAILED_CELLS[@]} célula(s) falharam:"
  printf '   ✗ %s\n' "${FAILED_CELLS[@]}"
  echo "  Resultados parciais em experiment-results/ablation/"
  echo "  Compare as concluídas: $PY experiments/ablation/summarize_ablation.py"
  exit 1
fi

echo "✓ Ablation completo. Resultados em experiment-results/ablation/"
echo "  Compare as células com: $PY experiments/ablation/summarize_ablation.py"
