# Guia: Experimentos Comparativos

Este guia explica como configurar e executar experimentos científicos comparando múltiplos modelos LLM na tarefa de correção de acessibilidade.

---

## Índice

1. [Visão Geral](#visão-geral)
2. [Estrutura do Arquivo de Experimento](#estrutura-do-arquivo-de-experimento)
3. [Executar Experimentos](#executar-experimentos)
4. [Resultados e Métricas](#resultados-e-métricas)
5. [Experimentos Pré-configurados](#experimentos-pré-configurados)
6. [Experimentos Avançados](#experimentos-avançados)
7. [Análise Estatística](#análise-estatística)
8. [Reprodutibilidade](#reprodutibilidade)

---

## Visão Geral

O sistema de experimentos permite comparar modelos LLM nas mesmas condições controladas:

- **Mesmos arquivos** de entrada para todos os modelos
- **Mesmo conjunto de ferramentas** de scan
- **Mesmo nível WCAG** para comparação justa
- **Múltiplas repetições** para robustez estatística
- **Métricas consistentes** (taxa de sucesso, tempo, tokens)

```
experiment.yaml
     │
     ▼
ExperimentRunner
     │
     ├── Modelo 1 ──→ Pipeline ──→ Métricas 1
     ├── Modelo 2 ──→ Pipeline ──→ Métricas 2
     └── Modelo N ──→ Pipeline ──→ Métricas N
     │
     ▼
comparison.html + metrics.csv + experiment.json
```

---

## Estrutura do Arquivo de Experimento

```yaml
# experiments/meu_experimento.yaml

# === IDENTIFICAÇÃO ===
name: qwen_vs_deepseek
description: |
  Comparação entre Qwen 2.5 Coder e DeepSeek Coder V2
  em componentes React com problemas de acessibilidade variados.

# === MODELOS A COMPARAR ===
models:
  # Modelos individuais
  - qwen2.5-coder:7b
  - qwen2.5-coder:14b
  - deepseek-coder-v2:16b

  # Grupos definidos em models.yaml
  - group:small_models

# === ARQUIVOS DE ENTRADA ===
files:
  # Arquivo específico
  - src/components/Button.tsx

  # Diretório (todos os .tsx/.jsx/.ts/.js)
  - src/components/

  # Glob pattern
  - "src/**/*.tsx"

  # Fixture de teste
  - tests/fixtures/sample_components/

# === CONFIGURAÇÃO DO SCAN ===
wcag_level: AA                  # A | AA | AAA
tools:                          # Ferramentas a usar (padrão: todas disponíveis)
  - pa11y
  - axe

min_tool_consensus: 1           # Consenso mínimo para incluir issue (padrão: 2)

# === CONFIGURAÇÃO DE GERAÇÃO ===
temperature: 0.1                # Temperatura para reprodutibilidade
max_tokens: 4096                # Tokens máximos por resposta

# === CONTROLE DE EXECUÇÃO ===
runs_per_model: 3               # Repetições por modelo (padrão: 1)
max_concurrent_models: 2        # Modelos em paralelo (padrão: 1)
agent_timeout: 180              # Timeout por agente em segundos

# === SAÍDA ===
output_dir: results/qwen_vs_deepseek
```

### Campos Obrigatórios

| Campo | Descrição |
|-------|-----------|
| `name` | Nome único do experimento |
| `models` | Lista de modelos ou grupos |
| `files` | Arquivos ou diretórios a processar |

### Campos Opcionais

| Campo | Padrão | Descrição |
|-------|--------|-----------|
| `description` | "" | Descrição do experimento |
| `wcag_level` | "AA" | Nível WCAG |
| `tools` | todas disponíveis | Ferramentas de scan |
| `min_tool_consensus` | 2 | Consenso mínimo |
| `temperature` | 0.1 | Temperatura LLM |
| `runs_per_model` | 1 | Repetições por modelo |
| `max_concurrent_models` | 1 | Paralelismo |
| `agent_timeout` | 180 | Timeout em segundos |
| `output_dir` | `results/<name>` | Diretório de saída |

---

## Executar Experimentos

### Básico

```bash
a11y-autofix experiment experiments/meu_experimento.yaml
```

### Com saída customizada

```bash
a11y-autofix experiment experiments/meu_experimento.yaml \
  --output results/run_2024_01_15
```

### Verbose

```bash
a11y-autofix experiment experiments/meu_experimento.yaml --verbose
```

### Saída durante execução

```
♿ a11y-autofix — Experimento: qwen_vs_deepseek
══════════════════════════════════════════════════

Modelos: 3  |  Arquivos: 12  |  Runs por modelo: 3

 Processando qwen2.5-coder:7b ...
  ████████████ 12/12 arquivos  [00:45]

 Processando deepseek-coder-v2:16b ...
  ████████████ 12/12 arquivos  [01:12]

══════════════════════════════════════════════════
Resultados do Experimento
┌──────────────────────┬──────────────────┬────────────────┬──────────────────┐
│ Modelo               │ Taxa de Sucesso  │ Tempo Médio(s) │ Issues Corrigidos│
├──────────────────────┼──────────────────┼────────────────┼──────────────────┤
│ qwen2.5-coder:7b     │ 78.3%           │ 8.2s           │ 47               │
│ deepseek-coder-v2:16b│ 82.1%           │ 14.7s          │ 52               │
└──────────────────────┴──────────────────┴────────────────┴──────────────────┘

Relatórios salvos em: results/qwen_vs_deepseek/
```

---

## Resultados e Métricas

### Arquivos Gerados

```
results/qwen_vs_deepseek/
├── experiment_20240115_143200.json    → dados brutos completos
├── comparison.html                    → relatório visual comparativo
└── metrics.csv                        → dados para análise estatística
```

### Métricas Calculadas

| Métrica | Descrição | Fórmula |
|---------|-----------|---------|
| `success_rate` | % de arquivos corrigidos com sucesso | `fixes / total * 100` |
| `avg_time` | Tempo médio por correção | `sum(times) / count` |
| `issues_fixed` | Total de issues corrigidos | `sum(issues_per_file)` |
| `total_tokens` | Tokens LLM consumidos | `sum(tokens_per_call)` |

### metrics.csv

```csv
model,success_rate,avg_time,issues_fixed,total_tokens,run
qwen2.5-coder:7b,78.3,8.2,47,42310,1
qwen2.5-coder:7b,79.1,8.5,48,43102,2
qwen2.5-coder:7b,77.8,8.0,46,41987,3
deepseek-coder-v2:16b,82.1,14.7,52,51243,1
...
```

### experiment.json (estrutura)

```json
{
  "experiment_name": "qwen_vs_deepseek",
  "timestamp": "2024-01-15T14:32:00+00:00",
  "execution_id": "uuid-v4",
  "config": { ... },
  "results": {
    "qwen2.5-coder:7b": {
      "success_rate": 78.3,
      "avg_time": 8.2,
      "issues_fixed": 47,
      "total_tokens": 42310,
      "per_file": [
        {
          "file": "src/Button.tsx",
          "file_hash": "sha256:...",
          "success": true,
          "duration_s": 7.8,
          "issues_found": 3,
          "issues_fixed": 3,
          "diff": "--- a/Button.tsx\n+++ b/Button.tsx\n..."
        }
      ]
    }
  }
}
```

---

## Experimentos Pré-configurados

### qwen_vs_deepseek.yaml

Compara os dois melhores modelos de código em português:

```bash
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml
```

Modelos: `qwen2.5-coder:7b`, `qwen2.5-coder:14b`, `deepseek-coder-v2:16b`

### all_models_comparison.yaml

Comparação abrangente de todos os modelos registrados:

```bash
a11y-autofix experiment experiments/all_models_comparison.yaml
```

Grupos: `small_models`, `medium_models`, `large_models`

### ablation_study.yaml

Estudo de ablação: impacto de cada ferramenta de scan:

```bash
a11y-autofix experiment experiments/ablation_study.yaml
```

Variantes:
- Apenas pa11y
- Apenas axe
- pa11y + axe
- Todas as ferramentas

---

## Experimentos Avançados

### Estudo de Temperatura

Compare o mesmo modelo em diferentes temperaturas:

```yaml
# experiments/temperature_study.yaml
name: temperatura_qwen_7b
description: Impacto da temperatura na qualidade das correções

models:
  - qwen2.5-coder:7b  # temperature definida abaixo

files:
  - tests/fixtures/sample_components/

# Sobrescrever temperatura por modelo seria ideal,
# mas como não é suportado diretamente, crie variações:
temperature: 0.0
runs_per_model: 5  # Mais runs para robustez estatística
```

Para comparar temperaturas diferentes, crie um arquivo por configuração:
```bash
TEMPERATURE=0.0 a11y-autofix experiment experiments/temp_study.yaml --output results/temp_0.0
TEMPERATURE=0.3 a11y-autofix experiment experiments/temp_study.yaml --output results/temp_0.3
```

### Estudo de Ablação de Ferramentas

```yaml
# experiments/ablation_tools.yaml
name: ablacao_ferramentas
description: Impacto de cada ferramenta de scan na detecção

models:
  - qwen2.5-coder:7b

files:
  - tests/fixtures/sample_components/

runs_per_model: 3
min_tool_consensus: 1   # Aceitar qualquer ferramenta

# Criar 4 variantes do experimento
```

Executar cada variante:

```bash
# Apenas pa11y
cat > /tmp/pa11y_only.yaml << EOF
$(cat experiments/ablation_tools.yaml)
tools: [pa11y]
name: ablacao_pa11y
EOF
a11y-autofix experiment /tmp/pa11y_only.yaml --output results/ablacao/pa11y

# Apenas axe
# ... (similar)
```

### Comparação de Agentes

Para estudar qual agente (OpenHands vs SWE-agent) é mais eficaz, ajuste os limites do router:

```bash
# Forçar OpenHands para tudo
SWE_MAX_ISSUES=0 a11y-autofix experiment experiments/qwen_vs_deepseek.yaml \
  --output results/always_openhands

# Forçar SWE-agent para tudo
SWE_MAX_ISSUES=999 a11y-autofix experiment experiments/qwen_vs_deepseek.yaml \
  --output results/always_swe
```

### Experimento em Projetos Reais

```yaml
# experiments/projeto_real.yaml
name: projeto_producao
description: Teste em componentes reais do projeto

models:
  - group:recommended

files:
  - ../../meu-projeto/src/components/   # Caminho relativo ao repo

wcag_level: AA
runs_per_model: 2
output_dir: results/projeto_real
```

---

## Análise Estatística

### Com o comando analyze

```bash
# Análise básica de um experimento
a11y-autofix analyze results/qwen_vs_deepseek/experiment_*.json

# Comparação entre dois experimentos
a11y-autofix analyze \
  results/run_baseline/experiment_*.json \
  results/run_improved/experiment_*.json
```

### Com Python e pandas

```python
import pandas as pd
import matplotlib.pyplot as plt

# Carregar métricas
df = pd.read_csv("results/qwen_vs_deepseek/metrics.csv")

# Estatísticas básicas
print(df.groupby("model")["success_rate"].describe())

# Gráfico de barras
df.groupby("model")["success_rate"].mean().plot(kind="bar")
plt.title("Taxa de Sucesso por Modelo")
plt.ylabel("Taxa de Sucesso (%)")
plt.tight_layout()
plt.savefig("results/comparison.png")

# Boxplot de tempo
df.boxplot(column="avg_time", by="model")
plt.title("Distribuição de Tempo por Modelo")
plt.savefig("results/time_boxplot.png")
```

### Testes Estatísticos

```python
from scipy import stats
import pandas as pd

df = pd.read_csv("results/qwen_vs_deepseek/metrics.csv")

# Teste t de Student (duas amostras)
model_a = df[df["model"] == "qwen2.5-coder:7b"]["success_rate"]
model_b = df[df["model"] == "deepseek-coder-v2:16b"]["success_rate"]

t_stat, p_value = stats.ttest_ind(model_a, model_b)
print(f"t={t_stat:.3f}, p={p_value:.4f}")
if p_value < 0.05:
    print("Diferença estatisticamente significativa (p < 0.05)")

# ANOVA (múltiplos modelos)
groups = [df[df["model"] == m]["success_rate"].values
          for m in df["model"].unique()]
f_stat, p_value = stats.f_oneway(*groups)
print(f"ANOVA: F={f_stat:.3f}, p={p_value:.4f}")
```

---

## Reprodutibilidade

### Garantias do Sistema

1. **Seed determinístico**: use `SEED=42` no `.env` para resultados reprodutíveis
2. **Temperatura baixa**: `temperature: 0.1` (ou `0.0` para máxima reprodutibilidade)
3. **Hash de arquivo**: cada arquivo é identificado por SHA-256, não por nome
4. **IDs estáveis**: issues têm IDs determinísticos entre runs
5. **Versões registradas**: versões de todas as ferramentas são salvas no JSON

### Reproduzir um Experimento

```bash
# Guardar configuração exata usada
cat results/qwen_vs_deepseek/experiment_*.json | \
  jq '.config' > results/config_reproducao.json

# Reproduzir com mesma configuração
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml \
  --output results/reproducao_$(date +%Y%m%d)
```

### Comparar Dois Runs

```bash
# Comparar taxa de sucesso entre dois runs
a11y-autofix analyze \
  results/run_original/experiment_*.json \
  results/run_reproducao/experiment_*.json
```

### Checklist de Reprodutibilidade

- [ ] `temperature: 0.0` ou `0.1` no YAML
- [ ] `SEED=42` no `.env`
- [ ] `runs_per_model: ≥3` para robustez estatística
- [ ] Versões de modelos fixadas (usar `model:tag` específico, não `latest`)
- [ ] Mesmo arquivo `.env` entre runs
- [ ] Versões das ferramentas Node.js registradas
- [ ] Arquivos de teste em controle de versão (git)
