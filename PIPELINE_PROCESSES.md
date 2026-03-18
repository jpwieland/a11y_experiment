# a11y-autofix — Processos do Pipeline e Análise Científica

> Documento técnico descrevendo cada processo do pipeline, seus formatos de saída,
> invariantes científicos e contribuição para análise de pesquisa.

---

## Visão Geral

O pipeline `a11y-autofix` é composto por **8 estágios sequenciais** que transformam
código-fonte React/TypeScript com problemas de acessibilidade em código corrigido,
com rastreabilidade científica completa. Cada estágio pode ser executado de forma
isolada em contêineres Docker independentes.

```
┌─────────────────────────────────────────────────────────────┐
│               Pipeline a11y-autofix v2.0.0                  │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ Stage 1  │ Stage 2  │ Stage 3  │ Stage 4  │   Stage 5       │
│Discovery │  Scan    │Protocol  │  Router  │  Fix (LLM)      │
│  (FS)    │(5 tools) │(dedup+   │  (score) │  (agent)        │
│          │parallel  │confidence│          │                 │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│               Stage 6          Stage 7         Stage 8      │
│          Validation (4L)    Reporting        Statistics      │
│          (syntactic,        (JSON+HTML)      (metrics)      │
│           functional,                                       │
│           domain, quality)                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Stage 1 — Descoberta de Arquivos (`utils/files.py`)

### Descrição
Identifica todos os arquivos React/TypeScript (`.tsx`, `.jsx`) em um diretório alvo,
recursivamente, excluindo `node_modules`, `dist`, `build` e arquivos de teste.

### Entrada
| Campo | Tipo | Exemplo |
|-------|------|---------|
| `target` | `Path \| str` | `./src` ou `/workspace/project` |

### Processo
```
target_dir
    → glob("**/*.tsx") + glob("**/*.jsx")
    → filtrar node_modules/, dist/, build/, __tests__/
    → ordenar por caminho (determinístico)
    → deduplicar (set de caminhos absolutos)
    → retornar lista[Path]
```

### Saída
```python
list[Path]  # lista de caminhos absolutos, ordenada deterministicamente
```

### Invariantes Científicos
- **Determinismo**: mesma entrada → mesma lista na mesma ordem
- **Reprodutibilidade**: não depende de estado externo (sem cache, sem rede)
- **Completude**: todos os arquivos `.tsx`/`.jsx` são incluídos

### Contribuição para Análise Científica
Permite definir o **corpus de análise** de forma precisa e reprodutível. A lista
de arquivos é incluída no relatório JSON para rastreabilidade completa do dataset
utilizado em cada execução experimental.

---

## Stage 2 — Scan Multi-Ferramenta (`scanner/orchestrator.py`)

### Descrição
Executa múltiplas ferramentas de acessibilidade em paralelo sobre cada arquivo.
Cada ferramenta roda em processo isolado; falhas de uma ferramenta não bloqueiam
as demais. Em ambiente Docker, cada scanner pode rodar em contêiner separado.

### Ferramentas Suportadas

| Ferramenta | Tecnologia | Tipo | WCAG Cobertos |
|------------|-----------|------|---------------|
| **Pa11y** | Node.js + Puppeteer | Harness HTML | 1.x, 2.x, 3.x, 4.x |
| **axe-core** | JavaScript in-browser | Harness HTML | 1.x, 2.x, 3.x, 4.x |
| **Lighthouse** | Chrome DevTools | Harness HTML | 1.4, 2.4, 4.1 |
| **Playwright+axe** | Playwright + axe-core | Harness HTML | 1.x, 2.x, 4.x |
| **ESLint jsx-a11y** | Static analysis | Fonte TSX/JSX | 1.1, 1.3, 2.1, 4.1 |

### Arquitetura de Harness HTTP
Para evitar problemas com `file://` e timeouts de CDN, os scanners baseados em
HTML recebem um harness servido via servidor HTTP local (`http://127.0.0.1:PORT/`):

```
arquivo.tsx
    → build_html_harness()     # gera HTML com React via CDN
    → tempfile (harness.html)  # escrito em /tmp/a11y_harness_XXXX/
    → HarnessServer (porta)    # HTTP server local (asyncio)
    → runners em paralelo      # asyncio.gather(*[runner.safe_run(url)])
    → ESLint diretamente       # roda no arquivo fonte (não precisa de harness)
    → coleta findings          # dict[ScanTool, list[ToolFinding]]
    → cleanup                  # shutil.rmtree(harness_dir)
```

### Formato de Saída — `ToolFinding`

```json
{
  "tool": "axe-core",
  "tool_version": "4.9.0",
  "rule_id": "color-contrast",
  "wcag_criteria": "1.4.3",
  "message": "Elements must have sufficient color contrast",
  "selector": "button.submit",
  "context": "<button class=\"submit\">Submit</button>",
  "impact": "serious",
  "help_url": "https://dequeuniversity.com/rules/axe/4.9/color-contrast"
}
```

### Formato de Saída — `ScanResult`

```json
{
  "file": "/workspace/src/Button.tsx",
  "file_hash": "sha256:3a7bd3e2360a3...",
  "scan_time": 4.82,
  "tools_used": ["pa11y", "axe-core", "playwright+axe", "eslint-jsx-a11y"],
  "tool_versions": {
    "pa11y": "8.0.0",
    "axe-core": "4.9.0",
    "playwright+axe": "1.44.0",
    "eslint-jsx-a11y": "6.8.0"
  },
  "error": null,
  "issues": [...]
}
```

### Invariantes Científicos
- **Hash de arquivo**: `sha256:` do conteúdo antes do scan — rastreia versão exata analisada
- **Versões das ferramentas**: capturadas para reprodutibilidade
- **Isolamento**: cada arquivo tem seu próprio servidor HTTP e diretório temporário
- **Graceful failure**: falha de um runner retorna `[]` sem abortar os demais

### Contribuição para Análise Científica
- Permite **comparação entre ferramentas** (qual ferramenta detecta quê)
- O campo `found_by` em cada issue registra quais ferramentas detectaram cada problema
- Suporta **baselines por ferramenta individual** (Seção 3.7.3 da metodologia)
- Versões capturadas garantem **reprodutibilidade do ambiente**

---

## Stage 3 — Protocolo Científico de Detecção (`protocol/detection.py`)

### Descrição
Aplica o protocolo de detecção científica sobre os findings brutos das ferramentas:
deduplicação cross-tool, cálculo de confiança por consenso e mapeamento WCAG→tipo.

### Algoritmo de Deduplicação

```
findings_by_tool: dict[ScanTool, list[ToolFinding]]
    ↓
Para cada finding:
    → chave = (wcag_criteria, selector_normalizado, issue_type)
    → agregar por chave → grupo de findings
    ↓
Para cada grupo:
    → tool_consensus = len(ferramentas_distintas)
    → confidence = calcular(tool_consensus, impact)
    → A11yIssue com ID estável (SHA-256[:16])
    ↓
Ordenar por: confidence DESC, impact DESC, issue_id ASC
    ↓
ScanResult.issues
```

### Cálculo de Confiança

| Condição | Confidence |
|----------|-----------|
| `tool_consensus >= min_tool_consensus` (padrão: 2) | `HIGH` |
| 1 ferramenta, `impact in {critical, serious}` | `MEDIUM` |
| 1 ferramenta, `impact in {moderate, minor}` | `LOW` |

### Mapeamento WCAG → IssueType

| WCAG | IssueType | Exemplos de regras |
|------|-----------|-------------------|
| 1.1.x | `alt-text` | image-alt, input-image-alt |
| 1.3.x | `semantic` | landmark-*, heading-order |
| 1.4.3, 1.4.6, 1.4.11 | `contrast` | color-contrast |
| 2.1.x | `keyboard` | keyboard, no-onmousedown |
| 2.4.x | `focus` | focus-trap, bypass |
| 4.1.2, 4.1.3 | `aria` | aria-*, role-* |
| 1.3.1, 2.4.6 | `label` | label, label-title-only |
| outros | `other` | — |

### Complexidade por Tipo

| IssueType | Complexity | Justificativa |
|-----------|-----------|--------------|
| `alt-text`, `aria`, `label` | `simple` | Adição de atributo |
| `semantic`, `keyboard`, `focus` | `moderate` | Reestruturação de markup |
| `contrast` | `complex` | Requer análise de paleta de cores |

### Formato de Saída — `A11yIssue`

```json
{
  "issue_id": "3a7bd3e2360a3f1c",
  "file": "/workspace/src/Button.tsx",
  "selector": "button.submit",
  "issue_type": "contrast",
  "complexity": "complex",
  "wcag_criteria": "1.4.3",
  "impact": "serious",
  "confidence": "high",
  "found_by": ["pa11y", "axe-core"],
  "tool_consensus": 2,
  "message": "Insufficient color contrast ratio",
  "context": "<button class=\"submit\">Submit</button>",
  "resolved": false,
  "findings": [
    {
      "tool": "pa11y",
      "tool_version": "8.0.0",
      "rule_id": "WCAG2AA.Principle1.Guideline1_4.1_4_3_F24",
      "message": "...",
      "selector": "button.submit"
    }
  ]
}
```

### Invariantes Científicos
- **ID estável**: `SHA-256(file:selector:wcag:type)[:16]` — mesmo issue = mesmo ID em runs diferentes
- **Ordenação determinística**: HIGH→MEDIUM→LOW, depois impact, depois issue_id
- **Deduplicação sem perda**: findings originais preservados em `findings[]`
- **Rastreabilidade**: cada issue aponta para os findings brutos que o geraram

### Contribuição para Análise Científica
- **H1 (taxa de detecção)**: `high_confidence_issues / total_issues`
- **H2 (concordância entre ferramentas)**: `tool_consensus` por issue
- **Cohen's κ**: calculável a partir de `found_by` por ferramenta
- **Análise de cobertura WCAG**: distribuição por `wcag_criteria`
- **Matriz de co-ocorrência**: quais ferramentas detectam os mesmos issues

---

## Stage 4 — Roteamento de Agentes (`router/engine.py`)

### Descrição
Seleciona automaticamente o agente de correção mais adequado para cada arquivo,
com base em pontuação heurística sobre o tipo e quantidade de issues.

### Algoritmo de Pontuação

```python
score = 0
score += len(issues) * peso_por_quantidade
score += sum(pesos_por_complexidade[i.complexity] for i in issues)
score += sum(pesos_por_tipo[i.issue_type] for i in issues)

if score >= openhands_complexity_threshold:
    agent = "openhands"     # alterações estruturais complexas
elif len(issues) <= swe_max_issues:
    agent = "swe-agent"     # correções cirúrgicas simples
else:
    agent = "direct-llm"    # geração direta de patch
```

### Formato de Saída — `RouterDecision`

```json
{
  "agent": "direct-llm",
  "score": 2,
  "reason": "2 issues simples (alt-text, label) → direct-llm adequado"
}
```

### Contribuição para Análise Científica
- **H4 (eficácia por agente)**: qual agente resolve mais issues por tipo
- **Análise de roteamento**: distribuição de casos por agente
- **Correlação complexidade→agente→taxa de sucesso**

---

## Stage 5 — Correção com Agente LLM (`agents/`)

### Descrição
Gera patches de código para corrigir os issues detectados, usando um LLM local
(Ollama, vLLM ou outro backend OpenAI-compatible). Suporta retry automático.

### Agentes Disponíveis

| Agente | Módulo | Estratégia |
|--------|--------|-----------|
| `DirectLLMAgent` | `agents/direct_llm.py` | Prompt com issues → gera novo conteúdo completo |
| `SWEAgent` | `agents/swe.py` | FIND/REPLACE patches cirúrgicos |
| `OpenHandsAgent` | `agents/openhands.py` | IDE simulation completa (requer serviço externo) |

### Formato de Saída — `PatchResult`

```json
{
  "success": true,
  "new_content": "import React...\n\nconst Button = ...",
  "diff": "--- a/Button.tsx\n+++ b/Button.tsx\n@@ ... @@",
  "error": null,
  "tokens_used": 1024,
  "time_seconds": 4.82
}
```

### Formato de Saída — `FixAttempt`

```json
{
  "attempt_number": 1,
  "agent": "direct-llm",
  "model": "qwen2.5-coder:7b",
  "timestamp": "2025-03-18T10:30:00.000Z",
  "success": true,
  "diff": "...",
  "new_content": "...",
  "tokens_used": 1024,
  "time_seconds": 4.82,
  "error": null
}
```

### Formato de Saída — `FixResult`

```json
{
  "file": "/workspace/src/Button.tsx",
  "final_success": true,
  "issues_fixed": 3,
  "issues_pending": 0,
  "total_time": 5.1,
  "attempts": [...],
  "scan_result": { ... }
}
```

### Invariantes Científicos
- **Modo dry-run**: nunca modifica arquivos em disco quando `dry_run=True`
- **Retry controlado**: máximo `max_retries_per_agent` tentativas (padrão: 3)
- **Tokens registrados**: para análise de custo computacional
- **Timestamps UTC**: para análise temporal de desempenho

### Contribuição para Análise Científica
- **H3 (taxa de correção)**: `issues_fixed / total_issues`
- **H4 (eficácia por modelo)**: sucesso por modelo em multi-model experiments
- **Análise de custo**: tokens por correção, tempo por issue
- **Curva de retry**: qual tentativa resolve a maioria dos issues
- **Comparação de modelos**: `ExperimentResult.success_rate_by_model`

---

## Stage 6 — Validação de Patches em 4 Camadas (`validation/pipeline.py`)

### Descrição
Valida cada patch gerado pelo agente em 4 camadas sequenciais, do mais barato
ao mais caro. Rejeitado em qualquer camada = patch descartado.

### Camadas de Validação

| Camada | Nome | Verificação | Custo |
|--------|------|-------------|-------|
| **1** | Sintática | Conteúdo não-vazio, sem blocos de código incompletos, sem marcadores de recusa de LLM, tem JSX | O(n) string |
| **2** | Preservação Funcional | Props interface mantida, exports preservados, event handlers existentes | O(n) regex |
| **3** | Verificação de Domínio | `<img>` com `alt`, `<input>` com label, ARIA patterns | O(n) regex |
| **4** | Qualidade de Código | `tabIndex >= -1`, sem `dangerouslySetInnerHTML` | O(n) string |

### Critérios de Rejeição

**Camada 1** (`rejected_at_layer: 1`):
- `empty_patch` — conteúdo vazio
- `unclosed_code_block` — bloco ` ``` ` não fechado
- `llm_refusal` — LLM recusou (padrões "I cannot", "As an AI", etc.)
- `no_jsx_found` — não contém nenhuma tag JSX

**Camada 2** (`rejected_at_layer: 2`):
- `prop_interface_removed` — interface de props removida
- `default_export_removed` — export default ausente
- `event_handler_removed` — handler de evento removido

**Camada 3** (`rejected_at_layer: 3`):
- `missing_alt_on_img` — `<img>` sem `alt` quando issue é ALT_TEXT
- `missing_form_labels` — `<input>` sem label quando issue é LABEL

**Camada 4** (`rejected_at_layer: 4`):
- `invalid_tabIndex:{valor}` — `tabIndex < -1`
- `dangerouslySetInnerHTML_present` — XSS/a11y risk

### Formato de Saída — `ValidationResult`

```json
{
  "passed": true,
  "rejected_at_layer": null,
  "failure_reason": null,
  "layer2_detail": {
    "passed": true,
    "failed_check": null,
    "checks_run": ["prop_interface", "default_export", "event_handlers"]
  },
  "layer_timings_ms": {
    "1": 0.12,
    "2": 0.45,
    "3": 0.31,
    "4": 0.08
  }
}
```

### Invariantes Científicos
- **Camada 1 sempre executada** — sem atalhos
- **Falha antecipada** — para na primeira camada que falha (custo mínimo)
- **Timings registrados** — para análise de overhead de validação
- **`rejected_at_layer`** — rastreia causa raiz de cada rejeição

### Contribuição para Análise Científica
- **H5 (taxa de regressão ρ)**: rejeições na camada 2 = regressões funcionais
- **Distribuição de falhas**: em qual camada a maioria dos patches falha
- **Overhead de validação**: `sum(layer_timings_ms)` por patch
- **Correlação modelo→qualidade**: patches de qual modelo passam mais camadas

---

## Stage 7 — Geração de Relatórios (`reporter/`)

### Descrição
Gera relatórios em múltiplos formatos a partir dos resultados de scan e correção.

### Relatório JSON (`reporter/json_reporter.py`)

Formato científico principal com audit trail completo.

**Localização**: `a11y-report/report.json`

**Estrutura completa**:

```json
{
  "schema_version": "2.0",
  "protocol_version": "1.0",
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2025-03-18T10:30:00.000000+00:00",
  "wcag_level": "WCAG2AA",
  "environment": {
    "python_version": "3.11.9",
    "os": "Linux 6.5.0",
    "llm_model": "qwen2.5-coder:7b",
    "tool_versions": {
      "pa11y": "8.0.0",
      "axe-core": "4.9.0",
      "playwright+axe": "1.44.0",
      "eslint-jsx-a11y": "6.8.0"
    }
  },
  "configuration": {
    "min_tool_consensus": 2,
    "swe_max_issues": 4,
    "max_retries": 3
  },
  "summary": {
    "total_files": 10,
    "files_with_issues": 7,
    "total_issues": 23,
    "high_confidence_issues": 15,
    "issues_fixed": 18,
    "issues_pending": 5,
    "success_rate": 78.3,
    "openhands_used": 2,
    "swe_agent_used": 3,
    "total_time_seconds": 142.7
  },
  "files": [
    {
      "file": "/workspace/src/Button.tsx",
      "file_hash": "sha256:3a7bd3e2...",
      "scan_time_seconds": 4.82,
      "tools_used": ["axe-core", "eslint-jsx-a11y"],
      "tool_versions": { ... },
      "error": null,
      "issues": [ ... ],
      "fix": {
        "success": true,
        "issues_fixed": 2,
        "issues_pending": 0,
        "total_time_seconds": 5.1,
        "attempts": [ ... ]
      }
    }
  ]
}
```

### Relatório HTML (`reporter/html_reporter.py`)

Relatório visual interativo para humanos.

**Localização**: `a11y-report/report.html`

Contém:
- Métricas resumidas com gráficos
- Lista de arquivos com issues colapsáveis
- Diffs de correção (antes/depois)
- Tabela de confiança por issue

### Relatório de Comparação (`reporter/comparison_reporter.py`)

Para experimentos multi-modelo.

**Localização**: `experiment-results/comparison_{timestamp}.csv`

```csv
model,files,issues_detected,issues_fixed,success_rate,avg_tokens,avg_time_s
qwen2.5-coder-7b,10,23,18,78.3,892,4.82
deepseek-coder-v2,10,23,20,87.0,1024,6.31
```

### Invariantes Científicos
- **`execution_id`**: UUID v4 único por run (nunca reutilizado)
- **`schema_version`**: permite parse correto de versões futuras
- **`timestamp`**: ISO 8601 UTC (timezone-aware)
- **`file_hash`**: permite verificar que o arquivo não mudou entre runs
- **`tool_versions`**: ambiente completamente documentado

### Contribuição para Análise Científica
- **Dataset principal para análise estatística**: `report.json` é a fonte de verdade
- **Reprodutibilidade**: `execution_id` + `file_hash` + `tool_versions` = run único
- **Meta-análise**: múltiplos `report.json` podem ser agrupados por `model`
- **Comparação cruzada**: `comparison_reporter` gera CSV para análise estatística (R, Python)

---

## Stage 8 — Análise Estatística (`analysis/statistical_analyser.py`)

### Descrição
Agrega resultados de múltiplos relatórios JSON para análise estatística científica.

### Métricas Calculadas

| Métrica | Símbolo | Fórmula | Hipótese |
|---------|---------|---------|----------|
| Taxa de detecção | δ | `high_conf_issues / total_issues` | H1 |
| Taxa de correção | τ | `issues_fixed / total_issues` | H3 |
| Taxa de regressão | ρ | `layer2_rejections / total_patches` | H5 |
| Concordância inter-ferramenta | κ | Cohen's κ por par de ferramentas | H2 |
| Overhead de validação | ω | `mean(sum(layer_timings_ms))` | — |
| Custo médio (tokens) | — | `mean(tokens_used)` por modelo | H4 |

### Exports Suportados

- **JSON**: dados brutos agregados
- **CSV**: para análise em R ou Python (pandas)
- **LaTeX**: tabelas prontas para publicação científica

---

## Execução em Docker — Isolamento e Segurança

### Contêineres Isolados por Função

```
docker-compose.yml
├── ollama               → Backend LLM (GPU ou CPU)
├── scanner-pa11y        → Pa11y isolado (net: scanner-net)
├── scanner-axe          → axe-core isolado (net: scanner-net)
├── scanner-lighthouse   → Lighthouse isolado (net: scanner-net)
├── scanner-playwright   → Playwright+axe isolado (net: scanner-net)
├── scanner-eslint       → ESLint isolado (net: scanner-net, sem HTTP)
├── scanner-orchestrator → Orquestrador completo
├── validator            → Validação 4-camadas (net: report-net, interna)
├── reporter             → Geração de relatórios
├── pipeline             → Pipeline completo (todas as redes)
├── test-runner          → Testes unitários
├── e2e-test-runner      → Testes E2E
└── experiment           → Experimentos multi-modelo
```

### Redes Isoladas

```
scanner-net  → acesso HTTP externo (CDN para React/Babel)
llm-net      → rede interna (Ollama ↔ pipeline)
report-net   → rede interna (validator ↔ reporter)
```

### Segurança por Contêiner

Todos os contêineres de scanner aplicam:
- `--security-opt no-new-privileges:true`
- `--cap-drop ALL`
- `--cap-add NET_BIND_SERVICE` (apenas scanners HTTP)
- `--read-only` no volume do código-fonte
- `--tmpfs /tmp:size=512m` para dados temporários

### Suporte a GPU

Detecção automática via `scripts/docker_validate.sh`:

```bash
# Com GPU disponível:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

# Variáveis de ambiente para GPU:
NVIDIA_VISIBLE_DEVICES=all
OLLAMA_NUM_GPU=-1        # usar todas as GPUs
OLLAMA_NUM_PARALLEL=4   # inferência paralela
MAX_CONCURRENT_AGENTS=4 # mais agentes simultâneos
```

### VRAM vs Modelo Recomendado

| VRAM Disponível | Modelo Recomendado | Backend |
|----------------|-------------------|---------|
| < 4 GB | qwen2.5-coder-3b (Q4_K_M) | Ollama |
| 4–8 GB | qwen2.5-coder-7b (Q4_K_M) | Ollama |
| 8–16 GB | qwen2.5-coder-14b (Q4_K_M) | Ollama |
| 16–24 GB | deepseek-coder-v2-16b | Ollama |
| 24–48 GB | qwen2.5-coder-32b | vLLM |
| 80+ GB | deepseek-coder-v2-236b | vLLM (multi-GPU) |

---

## Testes E2E — Cobertura e Verificações

### Suíte de Testes (`tests/e2e/test_pipeline_e2e.py`)

| Stage | Classe de Teste | Verificações |
|-------|----------------|-------------|
| 1 | `TestStage1Discovery` | extensões, completude, determinismo, edge cases |
| 2 | `TestStage2Protocol` | dedup, IDs estáveis, hashes, WCAG mapping, sorting |
| 3 | `TestStage3ScanOrchestrator` | paralelo, graceful failure, hash, múltiplos arquivos |
| 4 | `TestStage4Router` | auto routing, force override, campos obrigatórios |
| 5 | `TestStage5FixWithMockLLM` | geração de patch, dry-run isolation |
| 6 | `TestStage6ValidationPipeline` | todas as 4 camadas, timings |
| 7 | `TestStage7Reporting` | estrutura JSON, UUID, métricas, unicidade |
| 8 | `TestStage8FullPipelineE2E` | pipeline completo, determinismo, artefatos |
| 9 | `TestStage9ExecutionReport` | relatório de execução agregado |

### Executar Testes

```bash
# Todos os testes E2E (sem Docker):
cd a11y_experiment
pytest tests/e2e/ -v -s

# Stage específico:
pytest tests/e2e/test_pipeline_e2e.py::TestStage6ValidationPipeline -v

# Em Docker (modo completo):
./scripts/docker_validate.sh --only-e2e

# Em Docker com GPU:
./scripts/docker_validate.sh --only-e2e   # detecta GPU automaticamente

# Testes unitários + E2E:
./scripts/docker_validate.sh --only-tests
```

---

## Reprodutibilidade Científica — Checklist

Para garantir que os resultados de um experimento sejam reprodutíveis:

- [ ] `execution_id` registrado no relatório principal
- [ ] `tool_versions` de todos os scanners capturadas
- [ ] `file_hash` de todos os arquivos analisados registrado
- [ ] `model` e `backend` do LLM documentados
- [ ] `wcag_level` especificado
- [ ] `min_tool_consensus` configurado e registrado
- [ ] `schema_version` para parsing futuro
- [ ] Versão do Python e OS no relatório
- [ ] Semente aleatória do modelo (temperatura) documentada
- [ ] Contêineres Docker com versões fixadas (não `:latest`)

### Como Reproduzir um Experimento

```bash
# 1. Capturar configuração atual:
cat a11y-report/report.json | jq '.environment, .configuration'

# 2. Fixar versões no docker-compose.yml (substituir :latest):
#    ollama/ollama:0.3.14 ao invés de :latest

# 3. Executar com os mesmos parâmetros:
./scripts/docker_validate.sh \
    --model qwen2.5-coder-7b \
    --wcag AA \
    --target ./dataset/snapshots/projeto-X

# 4. Comparar execution_ids e file_hashes:
diff <(cat run1/report.json | jq '.files[].file_hash') \
     <(cat run2/report.json | jq '.files[].file_hash')
```

---

## Referências

- WCAG 2.1: https://www.w3.org/TR/WCAG21/
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- axe-core rules: https://dequeuniversity.com/rules/axe/
- Pa11y: https://pa11y.org/
- Lighthouse accessibility: https://web.dev/lighthouse-accessibility/
- Cohen's κ: https://en.wikipedia.org/wiki/Cohen%27s_kappa
- Protocolo científico detalhado: `docs/PROTOCOL.md`
- Guia de modelos: `docs/ADDING_MODELS.md`
- Guia de scanners: `docs/ADDING_TOOLS.md`
