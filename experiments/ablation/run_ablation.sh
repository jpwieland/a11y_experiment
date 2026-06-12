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

set -euo pipefail
cd "$(dirname "$0")/../.."

FILTER="${1:-cell_}"

for cfg in experiments/ablation/${FILTER}*.yaml; do
  echo "════════════════════════════════════════════════════════"
  echo "▶ $cfg"
  echo "════════════════════════════════════════════════════════"
  python -m a11y_autofix.cli experiment run "$cfg"
done

echo ""
echo "✓ Ablation completo. Resultados em experiment-results/ablation/"
echo "  Compare as células com: python experiments/ablation/summarize_ablation.py"
