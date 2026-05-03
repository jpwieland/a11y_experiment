# Execução do Experimento a11y-autofix — Documentação Técnica Detalhada

> **Objetivo:** Descrever com precisão técnica todas as etapas do pipeline de correção automática de acessibilidade, desde a carga de configuração até a geração de relatórios finais. Este documento é a referência canônica de implementação para replicação e auditoria do experimento.

---

## Índice

1. [Visão Geral da Arquitetura](#1-visão-geral-da-arquitetura)
2. [Etapa 1 — Carga e Validação de Configuração](#2-etapa-1--carga-e-validação-de-configuração)
3. [Etapa 2 — Cache de Scan e Importação de Resultados Pré-compilados](#3-etapa-2--cache-de-scan-e-importação-de-resultados-pré-compilados)
4. [Etapa 3 — Auto-clone de Snapshots](#4-etapa-3--auto-clone-de-snapshots)
5. [Etapa 4 — Descoberta de Arquivos](#5-etapa-4--descoberta-de-arquivos)
6. [Etapa 5 — Loop de Modelos com Cold-Start](#6-etapa-5--loop-de-modelos-com-cold-start)
7. [Etapa 6 — Pipeline Streaming: Scan + Correção Concorrentes](#7-etapa-6--pipeline-streaming-scan--correção-concorrentes)
8. [Etapa 7 — Protocolo de Detecção (DetectionProtocol)](#8-etapa-7--protocolo-de-detecção-detectionprotocol)
9. [Etapa 8 — Roteamento de Agentes (Router)](#9-etapa-8--roteamento-de-agentes-router)
10. [Etapa 9 — Agentes de Correção](#10-etapa-9--agentes-de-correção)
11. [Etapa 10 — Pipeline de Validação de Patches (4 camadas)](#11-etapa-10--pipeline-de-validação-de-patches-4-camadas)
12. [Etapa 11 — Checkpointing por Arquivo](#12-etapa-11--checkpointing-por-arquivo)
13. [Etapa 12 — Cálculo de Métricas](#13-etapa-12--cálculo-de-métricas)
14. [Etapa 13 — Geração de Relatórios](#14-etapa-13--geração-de-relatórios)
15. [Diagrama Completo do Pipeline de Correção](#15-diagrama-completo-do-pipeline-de-correção)
16. [Paralelismo e Controle de Concorrência](#16-paralelismo-e-controle-de-concorrência)
17. [Configuração de Hardware e Detecção Automática](#17-configuração-de-hardware-e-detecção-automática)
18. [Retomada Automática via Checkpoints](#18-retomada-automática-via-checkpoints)

---

## 1. Visão Geral da Arquitetura

O sistema é um pipeline de **correção automática de problemas de acessibilidade WCAG** em código React/TypeScript. Ele usa um LLM local (via Ollama ou vLLM) como motor de correção e múltiplas ferramentas de scan como fonte de verdade.

### Componentes principais

```
run_experiment.ps1 / run_experiment.sh
        │
        ▼
  CLI: a11y_autofix.cli experiment run <config.yaml>
        │
        ▼
  ExperimentRunner.run_from_config()
        │
        ├── ScanResultCache       ← cache compartilhado entre modelos
        ├── ensure_snapshots()    ← auto-clone de projetos ausentes
        │
        └── Para cada modelo:
                │
                ▼
          Pipeline.run()          ← scan + fix concorrentes (streaming)
                │
                ├── MultiToolScanner   ← pa11y, axe-core, playwright+axe, eslint
                ├── DetectionProtocol  ← dedup + confiança + mapeamento WCAG
                ├── Router             ← scoring matrix → agente
                ├── Agent              ← DirectLLM / OpenHands / SWE-agent
                └── ValidationPipeline ← 4 camadas de validação
```

### Filosofia de design

- **Streaming produtor-consumidor:** scan e fix rodam concorrentemente, não sequencialmente. O LLM começa a trabalhar nos primeiros arquivos enquanto os demais ainda estão sendo escaneados.
- **Cache compartilhado entre modelos:** o scan é executado apenas uma vez. Os modelos 2 e 3 reutilizam os resultados do modelo 1, economizando `(N_models - 1) × scan_time` por arquivo.
- **Checkpointing atômico:** cada arquivo processado gera um checkpoint imediato. Uma interrupção perde no máximo 1 arquivo de progresso por slot paralelo.
- **Reproducibilidade:** seed fixo (42), temperatura baixa (0.1), amostragem determinística de arquivos.

---

## 2. Etapa 1 — Carga e Validação de Configuração

**Arquivo:** `a11y_autofix/experiments/config_schema.py`  
**Ponto de entrada:** `load_experiment_config(path: Path) → ExperimentConfig`

### O que acontece

1. O arquivo YAML é lido e parseado com `yaml.safe_load()`.
2. O bloco `advanced:` — que pode conter `max_files_per_project`, `seed`, `save_diffs`, `typescript_validation`, `auto_clone_missing_snapshots`, `checkpoint_per_project` — é **achatado** no nível raiz do dict. Isso garante compatibilidade com YAMLs legados.
3. Campos desconhecidos pelo schema Pydantic são silenciosamente removidos (sem erro de validação).
4. `ExperimentConfig` é instanciado via Pydantic com validação automática de tipos e ranges.

### Campo crítico: `wcag_level`

O validador normaliza automaticamente:
- `"A"` → `"WCAG2A"`
- `"AA"` → `"WCAG2AA"`
- `"AAA"` → `"WCAG2AAA"`

### Campo crítico: `max_files_per_project`

Quando definido (ex: `60`), limita quantos arquivos são amostrados por diretório de projeto. A amostragem é **determinística**: usa `random.Random(seed=42)` para embaralhar e pega os primeiros N. O resultado é então reordenado por path para garantir ordem estável entre execuções.

### Schema principal (`ExperimentConfig`)

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `name` | `str` | — | Nome do experimento |
| `models` | `list[str]` | — | Modelos do registry Ollama/vLLM |
| `files` | `list[str]` | — | Paths de diretórios de snapshot |
| `wcag_level` | `str` | `"AA"` | Nível WCAG alvo |
| `agents` | `list[str]` | `["openhands","swe-agent"]` | Agentes habilitados |
| `scanners` | `list[str]` | `["pa11y","axe-core","playwright+axe"]` | Scanners habilitados |
| `strategy` | `Literal` | `"few-shot"` | Estratégia de prompting |
| `repetitions` | `int` | `1` | Repetições para estabilidade estatística |
| `max_files_per_project` | `int\|None` | `None` | Cap de arquivos por projeto |
| `seed` | `int` | `42` | Seed para reproducibilidade |
| `execution.temperature` | `float` | `0.1` | Temperatura de sampling |
| `execution.cold_start` | `bool` | `True` | Reiniciar servidor entre modelos |
| `execution.max_concurrent_models` | `int` | `3` | Modelos simultâneos |

---

## 3. Etapa 2 — Cache de Scan e Importação de Resultados Pré-compilados

**Arquivo:** `a11y_autofix/experiments/runner.py`  
**Classes:** `ScanResultCache`  
**Chamado em:** `ExperimentRunner.run_from_config()`

### Por que existe o cache

O scan de acessibilidade é custoso: para cada arquivo, até 4 ferramentas diferentes são executadas (pa11y, axe-core, playwright+axe, eslint-jsx-a11y), cada uma iniciando um processo separado (Playwright chega a usar ~150 MB por instância). Sem cache, um experimento com 3 modelos executaria o scan 3 vezes para cada arquivo.

O `ScanResultCache` resolve isso mantendo um dict compartilhado entre todos os modelos. O scan de cada arquivo é executado **exatamente uma vez** em todo o experimento.

### Estrutura em disco

O cache é salvo em `<output_dir>/scan_cache.json`:

```json
{
  "version": 2,
  "scans": {
    "/caminho/absoluto/resolvido/arquivo.tsx": { ...ScanResult serializado... },
    ...
  }
}
```

A chave é **sempre** `str(file.resolve())` — path absoluto canônico. Isso evita colisões entre caminhos relativos e absolutos que apontam para o mesmo arquivo.

### Fluxo de carga

```
ScanResultCache.load()
  ├── Se scan_cache.json não existe → cache vazio, retorna 0
  ├── Se version < 2 → descarta (formato antigo incompatível)
  └── Carrega dict "scans" na memória → retorna N entradas
```

### Importação de resultados pré-compilados

O dataset já contém resultados de scan de uma fase anterior em `dataset/results/<project_id>/scan_results.json`. O método `import_from_dataset_results()` importa esses dados, evitando qualquer re-scan.

**Problema:** os paths nos JSONs são absolutos da máquina onde o scan foi feito — podem ser Windows (`C:\Users\joao\...`) enquanto a máquina atual é Linux, ou ter estruturas de diretório diferentes.

**Solução de normalização:**

```
1. Substituir '\' por '/' → caminho normalizado
2. Encontrar o marcador "dataset/snapshots/<project_id>/"
3. Extrair o sufixo relativo após o marcador
   Ex: "C:\Users\joao\...\snapshots\arco-design__arco-design\src\Button.tsx"
       → sufixo = "src/Button.tsx"
4. Reconstruir: <repo_root>/dataset/snapshots/<project_id>/src/Button.tsx
5. Reescrever o campo "file" em todos os issues com o path local
```

Se o marcador principal não for encontrado, há um fallback que tenta localizar o sufixo pelo nome do projeto diretamente.

### Fluxo completo de inicialização do cache

```python
scan_cache = ScanResultCache(output_dir / "scan_cache.json")
cached_count = scan_cache.load()                    # tenta carregar do disco

repo_root = Path(__file__).parent.parent.parent
project_ids = self._extract_project_ids(config)     # extrai IDs dos paths do YAML

if project_ids:
    imported = scan_cache.import_from_dataset_results(repo_root, project_ids)
    if imported > 0:
        scan_cache.save()                           # persiste as novas importações

scan_dict = scan_cache.to_dict()                    # dict {str(path) → ScanResult}
```

O `scan_dict` é um **dict mutável** injetado no `Pipeline.run()`. O pipeline escreve novos resultados de scan neste dict conforme processa arquivos não-cacheados. Ao final de cada modelo, `_flush_scan_dict_to_cache()` sincroniza as novas entradas de volta para o `ScanResultCache` e salva em disco.

---

## 4. Etapa 3 — Auto-clone de Snapshots

**Arquivo:** `a11y_autofix/experiments/runner.py`  
**Função:** `ensure_snapshots(config, output_dir)`

### O que faz

Percorre os padrões `files:` do YAML e verifica se cada diretório de snapshot existe e não está vazio. Para os que faltam:

1. Lê o catálogo em `dataset/catalog/projects.yaml` para encontrar a URL do GitHub.
2. Executa `git clone --depth 1 --single-branch <url> <snapshot_dir>`.
3. Registra o evento em `<output_dir>/auto_clone.jsonl` (JSONL append-only).
4. Retorna lista de `project_id`s clonados nesta chamada.

Esse comportamento é controlado pelo campo `auto_clone_missing_snapshots: true` no YAML (default: `true`).

---

## 5. Etapa 4 — Descoberta de Arquivos

**Arquivo:** `a11y_autofix/experiments/config_schema.py`  
**Método:** `ExperimentConfig.resolve_files(base_dir)`

### O que faz

Para cada entrada em `config.files` (ex: `dataset/snapshots/arco-design__arco-design`):

1. Resolve o path absoluto (relativo à raiz do repositório).
2. Chama `find_react_files(path)` que percorre recursivamente buscando arquivos `.tsx`, `.jsx`, `.ts`, `.js` com componentes React.
3. Exclui automaticamente: `node_modules/`, `.next/`, `dist/`, `build/`, `coverage/`, arquivos de teste (`*.test.*`, `*.spec.*`), arquivos de configuração, arquivos de declaração de tipos (`*.d.ts`).
4. Se `max_files_per_project` está definido e o projeto tem mais arquivos que o limite:
   - Embaralha com `random.Random(seed=42)`.
   - Pega os primeiros `max_files_per_project`.
   - Reordena por path para ordem determinística.
5. Deduplica o resultado global mantendo ordem de inserção.

### Escala

| Configuração | Projetos | Arquivos por projeto | Total |
|---|---|---|---|
| Sem limite (`null`) | 35 | variável (até 1.000+) | ~22.810 |
| Com limite (`60`) | 35 | ≤ 60 | ≤ 2.100 |

---

## 6. Etapa 5 — Loop de Modelos com Cold-Start

**Arquivo:** `a11y_autofix/experiments/runner.py`  
**Classe:** `ExperimentRunner`

### Cold-start (metodologia Seção 3.1.3)

Quando `execution.cold_start: true`, cada condição experimental (modelo × estratégia) inicia com um servidor LLM **novo e isolado**. Isso previne:
- Acumulação implícita de estado entre condições (ex: KV-cache contaminando outputs).
- Variações causadas por contexto residual de iterações anteriores.

O processo:
1. Para o servidor Ollama anterior (se existir).
2. Inicia uma nova instância via `ollama serve` ou `vllm serve`.
3. Aguarda o modelo carregar (timeout: `model_load_timeout_s`).
4. Só então inicia o pipeline de arquivos.

### Monitor de GPU

`GpuMonitor` amostra o uso de VRAM a cada 30 segundos via `nvidia-smi`. Os dados são salvos em `<output_dir>/gpu_usage.jsonl`. Permite auditar se a GPU estava sendo utilizada durante o experimento.

### Loop principal

```
Para cada modelo em config.models:
  Para cada repetição em range(config.repetitions):
    │
    ├── cold_start: reinicia servidor LLM
    ├── _run_single_model(model, strategy, files, scan_dict, ...)
    │     ├── Separa arquivos em: pendentes vs. já checkpointed
    │     ├── Reconstrói FixResults dos checkpoints existentes (sem LLM)
    │     └── Executa Pipeline.run() apenas nos arquivos pendentes
    │
    ├── _flush_scan_dict_to_cache()   ← sincroniza novos scans no cache
    └── compute_experiment_metrics()  ← métricas desta condição
```

### Separação de pendentes vs. checkpointed

Antes de chamar o pipeline, o runner verifica quais arquivos já foram processados nesta condição:

```python
def is_condition_complete(model_id, strategy, file_id, checkpoints_dir) -> bool:
    checkpoint_path = checkpoints_dir / model_id / strategy / f"{file_id}.json"
    return checkpoint_path.exists()
```

Para arquivos já checkpointed, `_checkpoint_to_fix_result()` reconstrói o `FixResult` lendo o JSON do disco — o LLM **não é chamado novamente**. Isso garante retomada eficiente após interrupções.

---

## 7. Etapa 6 — Pipeline Streaming: Scan + Correção Concorrentes

**Arquivo:** `a11y_autofix/pipeline.py`  
**Classe:** `Pipeline`  
**Método:** `Pipeline.run(targets, wcag_level, output_dir, on_file_done, scan_cache)`

### Arquitetura produtor-consumidor

Esta é a otimização central do sistema. O pipeline **não** escaneia todos os arquivos antes de começar a corrigir — scan e correção acontecem **ao mesmo tempo**, controlados por dois semáforos independentes:

```
scan_sem = asyncio.Semaphore(settings.max_concurrent_scans)   # ex: 4
fix_sem  = asyncio.Semaphore(settings.max_concurrent_agents)  # ex: 1
```

Cada arquivo entra no fix imediatamente após seu scan terminar:

```python
async def process_file(file: Path) -> FixResult:
    # FASE 1: Scan (ou hit no cache — instantâneo)
    if scan_cache e arquivo está no cache:
        scan_result = scan_cache[str(file)]   # cache hit: zero I/O
    else:
        async with scan_sem:
            scan_result = await scanner.scan_file(file, wcag_level)
        # Escreve no cache com DUAS chaves para lookup consistente:
        scan_cache[str(file)] = scan_result
        scan_cache[str(file.resolve())] = scan_result

    # FASE 2: Fix (imediatamente após o scan)
    if dry_run:
        return FixResult(success=False, ...)
    elif not scan_result.has_issues:
        return FixResult(success=True, issues_fixed=0, ...)  # sem problemas
    else:
        async with fix_sem:
            return await _fix_file(scan_result, wcag_level)

# Todos os arquivos executados concorrentemente:
await asyncio.gather(*[process_file(f) for f in files])
```

### Por que isso resolve o problema de GPU ociosa

Sem streaming (arquitetura anterior):
```
[scan arquivo 1] → [scan arquivo 2] → ... → [scan arquivo 22810]
                                                                  → [fix arquivo 1] → ...
GPU ociosa por horas
```

Com streaming:
```
[scan 1] → [fix 1]
    [scan 2] → [fix 2]
        [scan 3] → [fix 3]
            ...
GPU ativa desde o início
```

### Lookup dual no cache

O cache usa `str(file.resolve())` como chave canônica. O pipeline escreve **duas** chaves ao cachear (`str(file)` e `str(file.resolve())`), pois o lookup pode vir de paths relativos ou absolutos:

```python
cached_sr = scan_cache.get(str(file))           # tenta relativo primeiro
if cached_sr is None:
    cached_sr = scan_cache.get(str(file.resolve()))  # fallback absoluto
```

---

## 8. Etapa 7 — Protocolo de Detecção (DetectionProtocol)

**Arquivo:** `a11y_autofix/protocol/detection.py`  
**Classe:** `DetectionProtocol`

### O que faz

Recebe findings brutos de múltiplos scanners e produz uma lista de `A11yIssue` deduplicada, enriquecida e classificada. É o ponto de convergência entre as ferramentas brutas e o sistema de agentes.

### Deduplicação por chave semântica

A chave de dedup é: `selector|wcag_criteria`

Dois findings são considerados o mesmo problema se o seletor CSS/elemento e o critério WCAG são idênticos — independente de qual scanner os reportou. Isso evita que o mesmo problema apareça 4 vezes (uma por scanner).

### Confiança baseada em consenso

```
1 scanner reportou  → Confidence.LOW
2+ scanners         → Confidence.HIGH
```

Issues com `Confidence.HIGH` têm maior probabilidade de serem verdadeiros positivos. A métrica de detecção δ pode ser segmentada por nível de confiança.

### Mapeamento WCAG → tipo de issue

Cada critério WCAG é mapeado para um `IssueType` e uma `Complexity`:

| Tipo (`IssueType`) | Exemplos de critérios WCAG | Complexidade |
|---|---|---|
| `ALT_TEXT` | 1.1.1 (Non-text Content) | SIMPLE |
| `LABEL` | 1.3.1, 3.3.2 (Labels/Instructions) | SIMPLE |
| `ARIA` | 4.1.2 (Name, Role, Value) | SIMPLE |
| `CONTRAST` | 1.4.3, 1.4.11 (Contrast) | COMPLEX |
| `SEMANTIC` | 1.3.1, 2.4.6 (Semantics/Headings) | COMPLEX |
| `KEYBOARD` | 2.1.1 (Keyboard) | MEDIUM |
| `FOCUS` | 2.4.7 (Focus Visible) | MEDIUM |
| `FORM` | 3.3.1, 3.3.3 (Error handling) | MEDIUM |
| `LANGUAGE` | 3.1.1 (Language of Page) | SIMPLE |

### Exclusão de regras page-level

Regras que se aplicam à página inteira (`html-has-lang`, `document-title`, `landmark-*`, `region`, `bypass`) são excluídas do set de issues. Essas regras não podem ser corrigidas em componentes React individuais.

---

## 9. Etapa 8 — Roteamento de Agentes (Router)

**Arquivo:** `a11y_autofix/router/engine.py`  
**Classe:** `Router`

### Scoring matrix

O Router analisa os issues de um arquivo e calcula um score para decidir qual agente usar:

| Condição | Pontos |
|---|---|
| Tem issues do tipo CONTRAST ou SEMANTIC | +4 |
| Volume ≥ `swe_max_issues` (padrão: 4) | +4 |
| Volume ≥ 2 × `swe_max_issues` (padrão: 8) | +5 adicional |
| Tem issues com `Complexity.COMPLEX` | +3 |
| ≥ 3 tipos de issue distintos | +3 |
| Todos os issues são ARIA/LABEL/ALT_TEXT E volume < threshold | -3 |

**Decisão:** `score ≥ 3` → OpenHands | `score < 3` → SWE-agent

### Lógica de override manual

Se o campo `agent_preference` não for `AUTO`, o router ignora o scoring e usa o agente especificado diretamente. No YAML de experimento, `agents: [auto]` usa o router automático.

### Exemplos práticos

```
Arquivo com 2 issues de alt-text:
  score = -3 (todos simples, pouco volume)
  → SWE-agent (correção cirúrgica)

Arquivo com 6 issues mistos incluindo contrast:
  score = 4 (contrast) + 4 (≥4 issues) + 3 (complex) = +11
  → OpenHands (contexto amplo necessário)

Arquivo com 4 issues de aria-label:
  score = 4 (≥4 issues)
  → OpenHands
```

---

## 10. Etapa 9 — Agentes de Correção

**Arquivos:** `a11y_autofix/agents/`

### DirectLLMAgent (fallback)

**Arquivo:** `agents/direct_llm.py`

O agente mais simples. Envia um único prompt ao LLM com o código-fonte completo e a lista de issues. Extrai o bloco de código da resposta e calcula o diff.

Fluxo:
```
1. build_direct_llm_prompt(task)     → prompt few-shot com código e issues
2. llm.complete_with_metrics(...)    → chama Ollama/vLLM
3. extract_code_block(response)      → extrai bloco ```tsx...``` da resposta
4. validate_tsx_basic(new_content)   → validação mínima de sintaxe
5. get_unified_diff(original, new)   → calcula diff unificado
6. Retorna PatchResult
```

### Estratégias de prompting

Controladas pelo campo `strategy` do YAML:

- **`zero-shot`**: Prompt apenas com instruções e issues (sem exemplos). Componentes 1-4 + 6 do template.
- **`few-shot`** (padrão): Prompt completo incluindo exemplos de correção (componentes 1-6). Melhor qualidade mas mais tokens.
- **`chain-of-thought`**: Few-shot + instrução explícita para o modelo raciocinar passo a passo antes de gerar o código.

### Formato do prompt (few-shot)

```
[Componente 1] System: você é um especialista em acessibilidade WCAG...
[Componente 2] Contexto: arquivo, linguagem, framework...
[Componente 3] Lista de issues com seletor, critério WCAG, severidade...
[Componente 4] Código-fonte original completo...
[Componente 5] Exemplos de correções similares (few-shot)...
[Componente 6] Instrução: retorne apenas o arquivo corrigido completo...
```

### Retry automático

O pipeline tenta corrigir cada arquivo até `settings.max_retries_per_agent` vezes (padrão: 1). Em cada tentativa, apenas os issues ainda não resolvidos são incluídos no prompt:

```python
for attempt_num in range(1, max_retries + 1):
    issues_pendentes = [i for i in task.issues if not i.resolved]
    if not issues_pendentes:
        break
    patch = await agent.run(AgentTask(..., issues=issues_pendentes))
    if patch.success:
        file.write_text(patch.new_content)
        break
```

---

## 11. Etapa 10 — Pipeline de Validação de Patches (4 camadas)

**Arquivo:** `a11y_autofix/validation/pipeline.py`  
**Classe:** `ValidationPipeline`

Cada patch gerado pelo agente passa por 4 camadas de validação em sequência. Uma falha em qualquer camada rejeita o patch imediatamente.

### Camada 1 — Validação Sintática

**O que verifica:** O conteúdo gerado é código TSX/JSX/JS sintaticamente válido?

**Como:** Tentativa de parse com `ast` (Python) ou heurísticas de estrutura de arquivo (balanceamento de chaves, blocos JSX, exports).

**Falha → `rejected_at_layer=1`**

Se o LLM gerou código truncado, misturou código com texto explicativo, ou retornou conteúdo inválido, é rejeitado aqui.

### Camada 2 — Preservação Funcional

**Arquivo:** `a11y_autofix/validation/layer2.py`

**O que verifica:** O patch não introduziu regressões funcionais?

**Checks heurísticos:**

| Check | O que verifica |
|---|---|
| `prop_interface` | Props do componente (interface TypeScript) não foram removidas ou alteradas incompativelmente |
| `exports` | Exports do arquivo (default export, named exports) permanecem os mesmos |
| `event_handlers` | Handlers de eventos (`onClick`, `onChange`, etc.) não foram removidos ou renomeados |
| `hooks` | Hooks React usados (`useState`, `useEffect`, etc.) não foram eliminados |
| `component_structure` | O componente principal ainda existe e tem a mesma assinatura |

**Falha → `rejected_at_layer=2`** (conta para a **taxa de regressão ρ**, métrica H5)

Esta é a camada mais importante do ponto de vista metodológico: uma rejeição aqui significa que o patch foi funcionalmente destrutivo.

### Camada 3 — Verificação de Domínio

**O que verifica:** O patch realmente corrigiu o problema de acessibilidade reportado?

**Como:** Heurísticas por tipo de issue:
- Para `ALT_TEXT`: verifica presença de atributo `alt` nos elementos `<img>` reportados.
- Para `LABEL`: verifica presença de `aria-label`, `aria-labelledby`, ou `<label for>`.
- Para `ARIA`: verifica que atributos ARIA obrigatórios foram adicionados.
- Para `CONTRAST`: verifica que valores de cor foram modificados (verificação por diferença de conteúdo).

**Falha → `rejected_at_layer=3`**

Esta verificação é heurística, não uma re-execução completa do scanner (que seria muito custosa em tempo de inferência).

### Camada 4 — Qualidade de Código

**O que verifica:** O patch não introduziu anti-padrões de acessibilidade?

**Padrões proibidos verificados:**
- `tabIndex` com valor positivo (ex: `tabIndex={1}`) — viola WCAG 2.4.3 Focus Order.
- `dangerouslySetInnerHTML` sem sanitização visível.
- Remoção de `role` ou `aria-*` existentes sem substituição equivalente.

**Falha → `rejected_at_layer=4`**

### Saída da validação

```python
@dataclass
class ValidationResult:
    passed: bool
    rejected_at_layer: int | None   # None = passou tudo
    failure_reason: str | None
    layer2_detail: Layer2Result | None
    layer_timings_ms: dict[int, float]  # tempo em ms por camada
```

---

## 12. Etapa 11 — Checkpointing por Arquivo

**Arquivo:** `a11y_autofix/experiments/runner.py`

### Estrutura de diretórios

```
<output_dir>/checkpoints/
  <model_id>/
    <strategy>/
      <file_id>.json
```

O `file_id` é um hash determinístico derivado do path do arquivo. Isso garante nomes de arquivo consistentes entre execuções.

### Conteúdo do checkpoint

```json
{
  "file": "/caminho/absoluto/arquivo.tsx",
  "model": "qwen2.5-coder-7b",
  "strategy": "few-shot",
  "timestamp": "2025-10-15T14:32:00Z",
  "final_success": true,
  "issues_fixed": 3,
  "issues_pending": 0,
  "total_time": 45.2,
  "attempts": [
    {
      "attempt_number": 1,
      "agent": "swe-agent",
      "success": true,
      "diff": "--- a/Button.tsx\n+++ ...",
      "tokens_used": 1847,
      "time_seconds": 44.1
    }
  ]
}
```

### Escrita atômica

O checkpoint é escrito **imediatamente** após o processamento de cada arquivo, antes de iniciar o próximo. Em caso de interrupção (Ctrl+C, timeout, crash), no máximo os arquivos em processamento paralelo no momento da interrupção são perdidos.

### Retomada

Na próxima execução, `is_condition_complete()` detecta o checkpoint e `_checkpoint_to_fix_result()` reconstrói o `FixResult` sem chamar o LLM:

```python
if is_condition_complete(model, strategy, file_id, checkpoints_dir):
    cp = load_checkpoint(model, strategy, file_id)
    result = _checkpoint_to_fix_result(cp, file, scan_result)
    all_results.append(result)
    continue  # pula para o próximo arquivo
```

---

## 13. Etapa 12 — Cálculo de Métricas

**Arquivo:** `a11y_autofix/experiments/metrics.py`  
**Função:** `compute_experiment_metrics(results: list[FixResult])`

### Métricas primárias (metodologia Seção 3.7.1)

**SR — Success Rate (nível de arquivo, binário)**

```
SR = |{f ∈ F : patch(f) passou nas 4 camadas}| / |F|
```

Um arquivo contribui `1` **somente** se o patch passou em **todas** as 4 camadas de validação. Correções parciais dentro de um arquivo contam como `SR=0` para aquele arquivo.

**IFR — Issue Fix Rate (nível de issue, com crédito parcial)**

```
IFR = Σ |issues_corrigidos(f)| / Σ |issues(f)|
```

Um arquivo com 3 issues onde 2 foram corrigidos contribui `2/3` para o IFR. Permite distinguir modelos que corrigem mais issues mesmo sem atingir correção total.

**MTTR — Mean Time To Repair**

```
MTTR = média de total_time apenas nos arquivos com final_success=True
```

Tempo de parede (wall-clock) por arquivo corrigido com sucesso. Arquivos com falha de reparo **não** são incluídos.

**TE — Token Efficiency**

```
TE = (IFR × |I|) / (C_total / 1000)
```

Onde `C_total` é o total de tokens de input consumidos na condição. Mede quantos issues foram corrigidos por cada 1.000 tokens gastos.

### Métricas secundárias

**ρ — Taxa de Regressão (H5)**

```
ρ = |patches rejeitados na Camada 2| / |patches tentados|
```

Mede com que frequência o modelo introduz regressões funcionais ao tentar corrigir acessibilidade.

**TPF — Tokens Per Fix**

```
TPF = total_input_tokens / issues_fixed
```

Alternativa mais precisa ao TE quando os tokens de prompt estão disponíveis por tentativa.

**δ — Detection Rate**

```
δ = |issues detectados pelo conjunto de scanners| / |issues no ground truth|
```

Requer arquivo de ground truth para referência. Calculado separadamente.

---

## 14. Etapa 13 — Geração de Relatórios

**Arquivos:** `a11y_autofix/reporter/`

### JSONReporter

Gera `<output_dir>/report.json` com:
- Metadados do experimento (modelo, estratégia, timestamp, wcag_level).
- Array de resultados por arquivo (issues encontrados, issues corrigidos, tentativas, tokens usados).
- Métricas agregadas (SR, IFR, MTTR, TE).
- Resumo por tipo de issue e por princípio WCAG.

### HTMLReporter

Gera `<output_dir>/report.html` a partir do JSON:
- Dashboard visual com gráficos de barra por tipo de issue.
- Tabela de resultados por arquivo com status de correção.
- Comparação entre modelos (quando múltiplos modelos).
- Seção de diffs para inspeção manual.

### ComparisonReporter

Quando `repetitions > 1` ou há múltiplos modelos, gera `experiment_summary.json` com:
- Médias e desvios padrão das métricas entre repetições.
- Ranking de modelos por SR e IFR.
- Análise de variância entre condições.

### Formato CSV

Gera tabela flat com uma linha por (modelo × arquivo × tentativa) para análise estatística em R/Python/Excel.

---

## 15. Diagrama Completo do Pipeline de Correção

```
╔══════════════════════════════════════════════════════════════════════════╗
║                    PIPELINE DE CORREÇÃO (por arquivo)                    ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ┌─────────────────────────────────────────────────────────────┐         ║
║  │  FASE 1 — SCAN (máx. N_scan_sem arquivos concorrentes)      │         ║
║  │                                                              │         ║
║  │  scan_cache hit? ─── YES ──→ ScanResult (instantâneo)       │         ║
║  │       │                                                      │         ║
║  │       NO                                                     │         ║
║  │       │                                                      │         ║
║  │       ▼                                                      │         ║
║  │  MultiToolScanner.scan_file()                                │         ║
║  │       │                                                      │         ║
║  │       ├── [1] HarnessServer ← HTML harness temporário        │         ║
║  │       │         http://127.0.0.1:PORT/                       │         ║
║  │       │                                                      │         ║
║  │       ├── [2] Pa11yRunner       ─┐                           │         ║
║  │       ├── [3] AxeRunner          ├── asyncio.gather()        │         ║
║  │       ├── [4] PlaywrightAxeRunner─┘  (paralelos)             │         ║
║  │       └── [5] EslintRunner (direto no fonte, sem harness)    │         ║
║  │                                                              │         ║
║  │       ▼ findings brutos de cada scanner                      │         ║
║  │                                                              │         ║
║  │  DetectionProtocol.process()                                 │         ║
║  │       ├── Dedup por chave (selector|wcag_criteria)           │         ║
║  │       ├── Excluir regras page-level                          │         ║
║  │       ├── Confidence: 1 scanner=LOW, 2+=HIGH                 │         ║
║  │       ├── Mapear WCAG → IssueType (ALT_TEXT, ARIA, ...)      │         ║
║  │       └── Mapear IssueType → Complexity (SIMPLE/COMPLEX)     │         ║
║  │                                                              │         ║
║  │       ▼ ScanResult(file, issues[], has_issues, ...)          │         ║
║  └─────────────────────────────────────────────────────────────┘         ║
║                                                                          ║
║  has_issues? ── NO ──→ FixResult(success=True, fixed=0)  [FIM]          ║
║       │                                                                  ║
║       YES                                                                ║
║       │                                                                  ║
║  ┌─────────────────────────────────────────────────────────────┐         ║
║  │  FASE 2 — FIX (máx. N_fix_sem arquivos concorrentes)        │         ║
║  │                                                              │         ║
║  │  Router.decide(scan_result)                                  │         ║
║  │       │                                                      │         ║
║  │       ├── Scoring Matrix:                                    │         ║
║  │       │     +4 tipos complexos (CONTRAST, SEMANTIC)          │         ║
║  │       │     +4 volume ≥ swe_max_issues (4)                   │         ║
║  │       │     +5 volume ≥ 2× threshold (8)                     │         ║
║  │       │     +3 issues com Complexity.COMPLEX                 │         ║
║  │       │     +3 ≥3 tipos de issue distintos                   │         ║
║  │       │     -3 todos simples (ARIA/LABEL/ALT) E poucos       │         ║
║  │       │                                                      │         ║
║  │       ├── score ≥ 3 → OpenHandsAgent                         │         ║
║  │       └── score < 3 → SWEAgent                              │         ║
║  │              (fallback: DirectLLMAgent)                      │         ║
║  │                                                              │         ║
║  │  Para cada tentativa (até max_retries):                      │         ║
║  │       │                                                      │         ║
║  │       ├── AgentTask(file, content, issues_pendentes)         │         ║
║  │       │                                                      │         ║
║  │       ├── build_prompt(strategy)                             │         ║
║  │       │     zero-shot: componentes 1-4+6                     │         ║
║  │       │     few-shot:  componentes 1-6  ← padrão            │         ║
║  │       │     chain-of-thought: few-shot + CoT                 │         ║
║  │       │                                                      │         ║
║  │       ├── LLMClient.complete() → Ollama / vLLM               │         ║
║  │       │     temperatura: 0.1 (baixa variância)               │         ║
║  │       │                                                      │         ║
║  │       ├── extract_code_block(response)                       │         ║
║  │       │     → bloco ```tsx...``` da resposta                 │         ║
║  │       │                                                      │         ║
║  │       └── ValidationPipeline.validate(patch)                 │         ║
║  │               │                                              │         ║
║  │               ├── [L1] Sintaxe válida?                       │         ║
║  │               │     FAIL → rejected_at_layer=1               │         ║
║  │               │                                              │         ║
║  │               ├── [L2] Preservação funcional?                │         ║
║  │               │     props, exports, eventos, hooks           │         ║
║  │               │     FAIL → rejected_at_layer=2  (conta ρ)   │         ║
║  │               │                                              │         ║
║  │               ├── [L3] Issue realmente corrigido?            │         ║
║  │               │     heurística por tipo de issue             │         ║
║  │               │     FAIL → rejected_at_layer=3               │         ║
║  │               │                                              │         ║
║  │               └── [L4] Sem anti-padrões?                     │         ║
║  │                     tabIndex positivo, dangerouslySetInner.. │         ║
║  │                     FAIL → rejected_at_layer=4               │         ║
║  │                                                              │         ║
║  │  patch.success && passou validação?                          │         ║
║  │       YES → file.write_text(new_content)  [APLICA PATCH]     │         ║
║  │       NO  → próxima tentativa (se houver) ou FAIL           │         ║
║  └─────────────────────────────────────────────────────────────┘         ║
║                                                                          ║
║  ┌─────────────────────────────────────────────────────────────┐         ║
║  │  FASE 3 — CHECKPOINT & CALLBACK                              │         ║
║  │                                                              │         ║
║  │  Salva checkpoint atômico:                                   │         ║
║  │    checkpoints/<model>/<strategy>/<file_id>.json             │         ║
║  │                                                              │         ║
║  │  Chama on_file_done(FixResult)                               │         ║
║  │    → atualiza _ProgressTracker → experiment_progress.json   │         ║
║  │    → watch.ps1 / watch.sh pode ler em tempo real             │         ║
║  └─────────────────────────────────────────────────────────────┘         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 16. Paralelismo e Controle de Concorrência

O sistema usa três níveis independentes de controle de concorrência:

### Nível 1 — Scans paralelos (`scan_sem`)

```
max_concurrent_scans = min(cpu_cores / 2, 8)
```

Cada scan inicia um processo Playwright (~150 MB RAM) e processos Node.js para pa11y/axe. O limite evita saturação de memória.

Variável de ambiente: `MAX_CONCURRENT_SCANS`

### Nível 2 — Agentes LLM paralelos (`fix_sem`)

```
VRAM livre ≥ 20 GB → 4 agentes paralelos
VRAM livre ≥ 12 GB → 3 agentes paralelos
VRAM livre ≥  6 GB → 2 agentes paralelos
VRAM livre <  6 GB → 1 agente  (serializado)
sem GPU            → 1 agente  (modo CPU)
```

O número de agentes paralelos é limitado pela VRAM disponível após o modelo estar carregado.

Variável de ambiente: `MAX_CONCURRENT_AGENTS`

### Nível 3 — Modelos sequenciais (`MAX_CONCURRENT_MODELS=1`)

Devido ao cold-start, apenas um modelo roda por vez. Isso é intencional pela metodologia (previne contaminação de estado).

### Interação entre os semáforos

```
asyncio.gather(process_file_1, process_file_2, ..., process_file_N)
       │
       ├── Todos os process_file() disputam scan_sem
       │     (máx. MAX_CONCURRENT_SCANS scanning ao mesmo tempo)
       │
       └── Todos os process_file() disputam fix_sem
             (máx. MAX_CONCURRENT_AGENTS fixing ao mesmo tempo)

Cenário típico (GPU 4GB, 8 cores):
  scan_sem=4 (4 scans simultâneos)
  fix_sem=1  (1 agente LLM de cada vez)
  → 4 arquivos sendo escaneados + 1 sendo corrigido ao mesmo tempo
```

---

## 17. Configuração de Hardware e Detecção Automática

### Windows (`run_experiment.ps1`)

```powershell
# Detecta VRAM via nvidia-smi
$VramFreeMb = nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits
$Jobs = if ($VramFreeGb -ge 20) { 4 }
        elseif ($VramFreeGb -ge 12) { 3 }
        elseif ($VramFreeGb -ge 6)  { 2 }
        else                        { 1 }

# Detecta cores via WMI
$CpuLogical = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$ScanWorkers = [math]::Min([int]($CpuLogical / 2), 8)

# Exporta para o Python
$env:MAX_CONCURRENT_AGENTS = $Jobs
$env:MAX_CONCURRENT_SCANS  = $ScanWorkers
$env:MAX_CONCURRENT_MODELS = "1"
```

### Linux (`run_experiment.sh`)

Mesma lógica via `nvidia-smi` e `nproc`. Detecta também o backend LLM:

```bash
if grep -q "^LLM_BACKEND=vllm" .env 2>/dev/null; then
    # vLLM: mais rápido para modelos ≥14B
    vllm serve $MODEL_NAME --port 8000 &
else
    # Ollama: padrão
    ollama serve &
fi
```

### Overrides manuais

```bash
# Windows
.\run_experiment.ps1 -Jobs 2 -ScanWorkers 4

# Linux
./run_experiment.sh --jobs 2 --scan-workers 4
```

---

## 18. Retomada Automática via Checkpoints

O sistema é projetado para ser interrompível a qualquer momento sem perda de trabalho significativa.

### Cenário típico de retomada

```
Execução 1 (interrompida no arquivo 847/2100):
  checkpoints/qwen2.5-coder-7b/few-shot/
    arquivo_001.json ✓
    arquivo_002.json ✓
    ...
    arquivo_847.json ✓

  scan_cache.json: 1.200 entradas (mais que 847 pois o scan era mais rápido)

Execução 2 (retomada):
  Runner detecta 847 checkpoints existentes
  Reconstrói FixResults dos 847 arquivos sem chamar o LLM
  Executa apenas os 1.253 arquivos restantes
  scan_cache.json já tem entradas para muitos desses arquivos → scan pulado
```

### Informações exibidas ao retomar

```
[RETOMADA] Checkpoints encontrados:
  Arquivos já processados : 847
  Scan cache              : 1.200 arquivos
  Scan será pulado para arquivos em cache
  O runner retomará automaticamente do ponto de interrupção
```

### Como detectar a retomada no progresso

O `experiment_progress.json` atualizado a cada arquivo pode ser monitorado com:

```bash
# Linux
watch -n 5 ./watch.sh

# Windows
.\watch.ps1
```

---

*Documento gerado em: 2026-04-11*  
*Versão do sistema: pipeline v2 (streaming), ScanResultCache v2*  
*Metodologia: Seções 3.1.3, 3.4.3, 3.6.2, 3.7.1, 3.7.2 da dissertação*
