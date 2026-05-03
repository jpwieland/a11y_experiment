# Diagnóstico Técnico do Pipeline de Scan de Acessibilidade

> Documento técnico descrevendo como cada ferramenta detecta, classifica e emite
> erros de acessibilidade no pipeline a11y-autofix. Referência para análise científica
> e depuração do corpus.

---

## Sumário

1. [Arquitetura do Pipeline](#1-arquitetura-do-pipeline)
2. [Geração do HTML Harness](#2-geração-do-html-harness)
3. [Ferramenta 1 — Pa11y](#3-ferramenta-1--pa11y)
4. [Ferramenta 2 — axe-core CLI](#4-ferramenta-2--axe-core-cli)
5. [Ferramenta 3 — Playwright + axe-core](#5-ferramenta-3--playwright--axe-core)
6. [Ferramenta 4 — ESLint jsx-a11y](#6-ferramenta-4--eslint-jsx-a11y)
7. [Protocolo de Detecção (DetectionProtocol)](#7-protocolo-de-detecção-detectionprotocol)
8. [Sistema de Confiança e Consenso](#8-sistema-de-confiança-e-consenso)
9. [Mapeamento WCAG → IssueType](#9-mapeamento-wcag--issuetype)
10. [Mapeamento WCAG → Complexity](#10-mapeamento-wcag--complexity)
11. [Estrutura dos Outputs](#11-estrutura-dos-outputs)
12. [Diagnóstico de Falhas por Ferramenta](#12-diagnóstico-de-falhas-por-ferramenta)

---

## 1. Arquitetura do Pipeline

O pipeline executa 4 ferramentas em paralelo para cada arquivo `.tsx`/`.jsx`/`.ts`/`.js`.
O orquestrador (`MultiToolScanner`) coordena a execução e alimenta o `DetectionProtocol`
com os findings crus de cada ferramenta.

```
arquivo.tsx
    │
    ├─► build_html_harness()   ─── gera harness.html (stub React renderizável)
    │       │
    │       └─► HarnessServer  ─── serve via http://127.0.0.1:PORT/ (evita file://)
    │               │
    │               ├─► Pa11yRunner        ──────────────────────────┐
    │               ├─► AxeRunner          ──────────────────────────┤  asyncio.gather()
    │               └─► PlaywrightAxeRunner ─────────────────────────┤
    │                                                                 │
    └─► EslintRunner  (direto no fonte, sem harness)  ───────────────┘
                                                                      │
                                                        findings_by_tool: dict[ScanTool, list[ToolFinding]]
                                                                      │
                                                        DetectionProtocol.run()
                                                                      │
                                                        ScanResult  (issues deduplificados)
```

**Por que servidor HTTP local?**
Os harnesses carregam React e Babel via CDN externo. O protocolo `file://` bloqueia
requisições de rede por política de segurança do Chrome/Chromium. O servidor
`HarnessServer` (http.server Python) resolve isso servindo o harness em
`http://127.0.0.1:<porta_aleatória>/`, permitindo que CDN carregue normalmente.

---

## 2. Geração do HTML Harness

**Função:** `a11y_autofix/utils/files.py → build_html_harness(content, filename)`

O harness envolve o componente JSX/TSX em uma página HTML mínima que:
- Carrega React 18 + ReactDOM via CDN (`unpkg.com`)
- Carrega Babel standalone para transpilar JSX no browser
- Monta o componente no `<div id="root">`
- Define `data-testid` para facilitar seletores

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel">
    // [conteúdo do arquivo .tsx aqui]
    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  </script>
</body>
</html>
```

**Limitação técnica:** O harness renderiza o componente de forma estática, sem
props reais ou estado de aplicação. Problemas que só ocorrem em estados específicos
(modais abertos, tooltips visíveis) não serão detectados.

---

## 3. Ferramenta 1 — Pa11y

**Arquivo:** `a11y_autofix/scanner/pa11y.py`
**Tipo:** Ferramenta de teste de acessibilidade baseada em Chromium headless (Node.js)
**Site:** https://pa11y.org/

### Como funciona

Pa11y abre o harness HTML via Chromium e aplica regras HTML_CodeSniffer (padrão WCAG 2.1)
sobre o DOM renderizado. Opera no nível HTML — avalia tags, atributos, estrutura de cabeçalhos,
contrastes e ARIA do DOM final, não do código-fonte JSX.

### Descoberta do executável

O runner testa três variantes em ordem:
1. `pa11y` (PATH direto)
2. `npx pa11y` (via npm executor)
3. `npx --yes pa11y` (download automático se ausente)

Isso resolve o problema frequente de pa11y instalado globalmente mas fora do PATH
do subprocesso Python (comum dentro de `.venv`).

### Comando executado

**Pa11y 7+ (versão padrão):**
```bash
pa11y --reporter json --standard WCAG2AA --timeout 60000 --include-warnings http://127.0.0.1:PORT/harness.html
```

**Pa11y 6.x (versão legada — detectada automaticamente):**
```bash
pa11y --reporter json --standard WCAG2AA --timeout 60000 --wait 500 \
  --include-warnings --chromium-flags "..." http://127.0.0.1:PORT/harness.html
```

A detecção de versão é feita via `pa11y --version` com parse do major number.
Pa11y 7+ removeu as flags `--wait` e `--chromium-flags` (migração puppeteer → playwright interno).

### Retry automático

Se o primeiro conjunto de flags falhar (returncode inválido, stdout vazio, JSON inválido),
o runner tenta novamente com flags mínimas:
```bash
pa11y --reporter json http://127.0.0.1:PORT/harness.html
```

### Formato de saída (JSON)

Pa11y retorna um array JSON onde cada item é:

```json
{
  "type": "error",
  "code": "WCAG2AA.Principle1.Guideline1_4.1_4_3.G18",
  "message": "This element has insufficient colour contrast...",
  "context": "<button style='color:#aaa'>Click</button>",
  "selector": "html > body > button",
  "helpUrl": "https://www.w3.org/TR/WCAG21/#contrast-minimum"
}
```

**Tipos retornados:**
| `type`   | Mapeamento de impacto | Incluído? |
|----------|-----------------------|-----------|
| `error`  | `serious`             | Sim       |
| `warning`| `moderate`            | Sim       |
| `notice` | `minor`               | **Não** (filtrado — muito ruído) |

### Extração do critério WCAG

O `code` pa11y codifica o critério WCAG no formato `Principle1.Guideline1_4.1_4_3.G18`.
O runner extrai via regex:

```python
r"(\d+)_(\d+)_(\d+)"  # → "1.4.3"
r"(\d+)_(\d+)(?!\d)"  # fallback → "2.4"
```

### Retorno de exit code

Pa11y retorna **código 2** quando encontra issues — isso **não é erro de execução**.
O runner trata explicitamente `returncode in (0, 2)` como sucesso.

---

## 4. Ferramenta 2 — axe-core CLI

**Arquivo:** `a11y_autofix/scanner/axe.py`
**Tipo:** CLI do motor axe-core via ChromeDriver/Selenium
**Site:** https://github.com/dequelabs/axe-core-npm

### Como funciona

`@axe-core/cli` lança o ChromeDriver bundled, abre o harness e injeta o script `axe-core.js`
no DOM. O axe executa suas regras via JavaScript dentro do contexto da página e retorna
resultados em JSON. Opera sobre o **DOM renderizado**, não sobre o código-fonte.

### Descoberta do ChromeDriver

Ordem de busca:
1. `chromedriver` no PATH do sistema
2. ChromeDriver bundled em `npm root -g/@axe-core/cli/node_modules/chromedriver/bin/`
3. `node_modules` local (projetos com axe instalado como dependência)

O bundling do chromedriver com `@axe-core/cli` garante disponibilidade sem instalação separada.

### Comando executado

```bash
npx --yes @axe-core/cli http://127.0.0.1:PORT/harness.html \
  --stdout \
  --tags wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa \
  --chrome-options no-sandbox,disable-dev-shm-usage,disable-gpu \
  --chromedriver-path /path/to/chromedriver
```

**Nota crítica:** As flags `--chrome-options` usam a sintaxe sem `--` antes de cada argumento
e separadas por vírgula — diferente de como são passadas ao Chrome diretamente.

### Retry automático

Se o primeiro conjunto falhar (stdout vazio ou returncode ≠ 0/1), tenta sem `--chrome-options`:
```bash
npx --yes @axe-core/cli http://127.0.0.1:PORT/harness.html \
  --stdout --tags wcag2a,wcag2aa,...
```

### Tags WCAG por nível

| Nível WCAG | Tags axe usadas |
|------------|----------------|
| WCAG2A     | `wcag2a`, `wcag21a`, `wcag22a` |
| WCAG2AA    | `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` |
| WCAG2AAA   | todos acima + `wcag2aaa`, `wcag21aaa` |

### Formato de saída (JSON)

```json
[
  {
    "violations": [
      {
        "id": "image-alt",
        "impact": "critical",
        "description": "Ensures <img> elements have alternate text",
        "helpUrl": "https://dequeuniversity.com/rules/axe/4.9/image-alt",
        "tags": ["wcag2a", "wcag1.1.1"],
        "nodes": [
          {
            "target": ["img"],
            "html": "<img src=\"logo.png\">",
            "failureSummary": "Fix any of the following: Element does not have an alt attribute"
          }
        ]
      }
    ]
  }
]
```

**Extração do critério WCAG das tags:**

```python
r"wcag(\d)(\d)(\d)$"  # "wcag143" → "1.4.3"
r"wcag(\d)(\d{1,2})$" # "wcag14"  → "1.4"
```

**Impacto axe:**
| Valor axe  | Semântica |
|------------|-----------|
| `critical` | Bloqueante para usuários AT |
| `serious`  | Impacto significativo |
| `moderate` | Dificulta uso |
| `minor`    | Melhoria menor |

---

## 5. Ferramenta 3 — Playwright + axe-core

**Arquivo:** `a11y_autofix/scanner/playwright_axe.py`
**Tipo:** Browser automation (Playwright) + injeção de axe-core JS
**Site:** https://playwright.dev/

### Como funciona

Diferente do axe-core CLI (que usa ChromeDriver/Selenium), esta ferramenta usa a API
Python do Playwright para controlar o Chromium. O `axe-core.js` é injetado via
`page.add_script_tag()`. O axe executa no contexto da página via `page.evaluate()`.

**Vantagens sobre axe-core CLI:**
- Mais confiável em ambientes onde ChromeDriver não corresponde à versão do Chrome instalado
- Melhor suporte a páginas que usam ES modules
- Permite aguardar hidratação React antes de executar axe (`page.wait_for_selector`)

### Fluxo de execução

```python
async with async_playwright() as pw:
    browser = await pw.chromium.launch(args=["--no-sandbox", ...])
    page = await browser.new_page()
    await page.goto(harness_url)
    await page.wait_for_load_state("networkidle")  # aguarda CDN + hidratação
    await page.add_script_tag(path=axe_local_path)  # local preferred
    # ou
    await page.add_script_tag(url="https://cdnjs.cloudflare.com/..../axe.min.js")
    result = await page.evaluate("""
        async () => await window.axe.run(document, {
            runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] }
        })
    """)
```

### Resolução do axe-core local

O runner busca `axe.min.js` em:
1. `npm root -g/axe-core/axe.min.js`
2. `npm root -g/@axe-core/cli/node_modules/axe-core/axe.min.js`

Se não encontrar, usa o CDN como fallback com `wait_for_function('window.axe !== undefined')`.

### Diferença de resultados vs axe-core CLI

Playwright + axe tende a detectar mais issues porque aguarda a hidratação completa do React.
O axe-core CLI via ChromeDriver pode capturar a página antes de React terminar de renderizar,
resultando em fewer findings em componentes com renderização assíncrona.

---

## 6. Ferramenta 4 — ESLint jsx-a11y

**Arquivo:** `a11y_autofix/scanner/eslint.py`
**Tipo:** Análise estática de AST — único runner que **não usa harness HTML**
**Plugin:** https://github.com/jsx-eslint/eslint-plugin-jsx-a11y

### Como funciona

ESLint analisa o **código-fonte JSX/TSX diretamente**, sem renderizar o componente.
O parser `@typescript-eslint/parser` gera o AST (Abstract Syntax Tree) e o plugin
`jsx-a11y` aplica regras sobre os nós JSX do AST.

**Vantagem exclusiva:** Detecta padrões que não são visíveis no DOM renderizado:
- `onClick` em `<div>` sem `onKeyPress`/`onKeyDown` equivalente
- `tabIndex={2}` (valor positivo — interrompe ordem natural de foco)
- `<img>` sem `alt` **mesmo quando alt seria adicionado dinamicamente** (analisa o JSX literalmente)

**Limitação:** Não detecta problemas de contraste, semântica de heading ou ARIA dinâmica.

### Compatibilidade de versão ESLint

O runner detecta automaticamente a versão do ESLint e usa o formato correto:

| Versão ESLint | Formato de config | Mecanismo de resolução de plugins |
|---------------|-------------------|-----------------------------------|
| 8.x           | `.eslintrc.json` legado | `--no-eslintrc --config` |
| 9.x / 10.x+   | Flat config `.cjs` | `NODE_PATH=$(npm root -g)` |

**Por que isso importa para o ESLint 10:**
O ESLint 10 lança `TypeError` se qualquer regra declarada **não existir** na versão instalada
do plugin, abortando o lint inteiro antes de produzir qualquer saída JSON. O flat config
gerado dinamicamente filtra as regras para incluir apenas as disponíveis:

```javascript
const availableRules = new Set(Object.keys(jsxA11y.rules || {}));
const rules = Object.fromEntries(
  Object.entries(allRules).filter(([key]) => {
    const name = key.replace('jsx-a11y/', '');
    return availableRules.has(name);
  })
);
```

### Resolução de plugins globais

Os plugins (`eslint-plugin-jsx-a11y`, `@typescript-eslint/parser`) estão instalados
**globalmente** via npm, não dentro dos projetos escaneados. O flat config usa
`require()` com `NODE_PATH=$(npm root -g)` para encontrá-los:

```javascript
process.env.NODE_PATH = "/path/to/npm/global/lib/node_modules"
const jsxA11y = require("eslint-plugin-jsx-a11y");
const tsParser = require("@typescript-eslint/parser");
```

### Regras configuradas

| Regra                                    | WCAG  | Impacto    |
|------------------------------------------|-------|------------|
| `jsx-a11y/alt-text`                      | 1.1.1 | critical   |
| `jsx-a11y/img-redundant-alt`             | 1.1.1 | minor      |
| `jsx-a11y/heading-has-content`           | 1.3.1 | moderate   |
| `jsx-a11y/label-has-associated-control`  | 1.3.1 | critical   |
| `jsx-a11y/scope`                         | 1.3.1 | moderate   |
| `jsx-a11y/click-events-have-key-events`  | 2.1.1 | serious    |
| `jsx-a11y/interactive-supports-focus`    | 2.1.1 | serious    |
| `jsx-a11y/mouse-events-have-key-events`  | 2.1.1 | moderate   |
| `jsx-a11y/no-access-key`                 | 2.1.1 | moderate   |
| `jsx-a11y/no-distracting-elements`       | 2.2.2 | serious    |
| `jsx-a11y/tabindex-no-positive`          | 2.4.3 | serious    |
| `jsx-a11y/no-autofocus`                  | 2.4.3 | serious    |
| `jsx-a11y/html-has-lang`                 | 3.1.1 | serious    |
| `jsx-a11y/aria-props`                    | 4.1.2 | serious    |
| `jsx-a11y/aria-proptypes`                | 4.1.2 | serious    |
| `jsx-a11y/aria-role`                     | 4.1.2 | critical   |
| `jsx-a11y/aria-unsupported-elements`     | 4.1.2 | minor      |
| `jsx-a11y/role-has-required-aria-props`  | 4.1.2 | critical   |
| `jsx-a11y/role-supports-aria-props`      | 4.1.2 | critical   |
| `jsx-a11y/anchor-is-valid`               | 4.1.2 | moderate   |
| `jsx-a11y/anchor-has-content`            | 4.1.2 | moderate   |

**Regras excluídas intencionalmente:**
- `jsx-a11y/aria-hidden-body` — nunca foi regra oficial do plugin
- `jsx-a11y/button-has-type` — pertence ao `eslint-plugin-react`, não ao jsx-a11y

### Formato de saída (JSON)

```json
[
  {
    "filePath": "/path/to/Component.tsx",
    "messages": [
      {
        "ruleId": "jsx-a11y/alt-text",
        "severity": 2,
        "message": "img elements must have an alt prop...",
        "line": 5,
        "column": 3,
        "source": "<img src=\"logo.png\" />"
      }
    ]
  }
]
```

**Composição do `selector`:**
```python
selector = f"{Path(file_path).name}:{line}:{col}"
# ex: "Dashboard.tsx:5:3"
```

---

## 7. Protocolo de Detecção (DetectionProtocol)

**Arquivo:** `a11y_autofix/protocol/detection.py`

O `DetectionProtocol` recebe os findings crus de todas as ferramentas e produz uma
lista deduplificada de `A11yIssue` com metadados científicos.

### Fluxo completo

```
findings_by_tool: dict[ScanTool, list[ToolFinding]]
        │
        ▼ _group_findings()

grouped: dict[dedup_key → (list[ToolFinding], list[ScanTool])]
        │
        ▼ _build_issue() para cada grupo

issues: list[A11yIssue]  (com confiança, tipo, complexidade, ID estável)
        │
        ▼ _sort_issues()

ScanResult  (ordenado deterministicamente)
```

### Chave de deduplicação

```python
def _dedup_key(finding: ToolFinding) -> str:
    selector  = finding.selector.strip().lower()
    criteria  = finding.wcag_criteria if finding.wcag_criteria else finding.rule_id
    return f"{selector}|{criteria}"
```

**Exemplo:** Pa11y e axe detectam `<img>` sem alt no mesmo elemento:
- Pa11y:  `selector="html > body > img"`, `wcag_criteria="1.1.1"` → chave: `html > body > img|1.1.1`
- axe:    `selector="img"`, `wcag_criteria="1.1.1"` → chave: `img|1.1.1`

> **Nota:** Seletores diferentes para o mesmo elemento físico **não são deduplificados**.
> Pa11y usa seletores CSS completos enquanto axe usa seletores mais curtos.
> Isso pode resultar em contagem duplicada inter-ferramenta para o mesmo elemento.
> Esta é uma limitação conhecida documentada na metodologia (PROTOCOL.md §7.2).

### Seleção do finding primário

Quando múltiplos findings formam um grupo, o mais informativo é escolhido como
representante:

```python
def rank(f):
    has_wcag     = 1 if f.wcag_criteria else 0
    impact_score = 4 - ["critical","serious","moderate","minor"].index(f.impact)
    context_len  = len(f.context)
    return (has_wcag, impact_score, context_len)

primary = max(findings, key=rank)
```

Prioridades:
1. Tem `wcag_criteria` preenchido
2. Maior impacto
3. Mais contexto de código

### Ordenação determinística

```python
sorted(issues, key=lambda i: (
    -CONFIDENCE_PRIORITY[i.confidence],   # HIGH primeiro
    -IMPACT_PRIORITY[i.impact],            # critical primeiro
    i.wcag_criteria or "9.9.9",            # critério WCAG crescente
    i.selector,                            # seletor alfabético
))
```

A ordenação determinística é requisito de reprodutibilidade científica —
dois scans do mesmo arquivo devem produzir a mesma sequência de issues.

---

## 8. Sistema de Confiança e Consenso

### Regras de confiança

```
n_tools = número de ferramentas que detectaram o mesmo issue (após dedup)

SE n_tools >= min_tool_consensus:  → HIGH
SE n_tools == 1 AND impact in (critical, serious):  → MEDIUM
CASO CONTRÁRIO:  → LOW
```

**`min_tool_consensus` padrão:** 1 (todas as ferramentas contribuem para HIGH)

Com `--min-consensus 2`, apenas issues confirmados por pelo menos 2 ferramentas
são classificados como HIGH confidence. Útil para análises científicas que exigem
maior rigor na detecção.

### Interpretação científica

| Confidence | Significado para análise |
|------------|--------------------------|
| HIGH       | Problema confirmado (consenso multi-ferramenta ou critério único com min_consensus=1) |
| MEDIUM     | Detectado por 1 ferramenta, mas impacto crítico/sério — merecedor de revisão |
| LOW        | Detectado por 1 ferramenta, impacto baixo — pode ser falso positivo |

**Para hipóteses de pesquisa:**
- `high_confidence_rate = high / total` indica a qualidade do corpus para treinamento de LLM
- Uma taxa alta (>60%) sugere problemas reais e não ruidosos

### `tool_consensus` vs `confidence`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `tool_consensus` | `int` | Número exato de ferramentas que detectaram o issue |
| `confidence` | `Confidence` (enum) | Classificação resultante: HIGH / MEDIUM / LOW |
| `found_by` | `list[ScanTool]` | Lista das ferramentas específicas |

---

## 9. Mapeamento WCAG → IssueType

O `IssueType` é a taxonomia científica do corpus. Cada issue é classificado em uma
das 7 categorias:

| IssueType    | Critérios WCAG mapeados | Ferramentas que detectam |
|--------------|-------------------------|--------------------------|
| `ALT_TEXT`   | 1.1.1                   | Pa11y, axe, Playwright, ESLint |
| `CONTRAST`   | 1.4.1, 1.4.3, 1.4.6, 1.4.11 | Pa11y, axe, Playwright |
| `LABEL`      | 1.3.6, 2.4.2, 2.4.4, 2.5.3 | Pa11y, axe, Playwright, ESLint |
| `KEYBOARD`   | 2.1.1, 2.1.2, 2.1.3, 2.1.4, 2.4.1, 2.4.3, 2.4.12 | ESLint (primário), axe |
| `FOCUS`      | 2.4.7, 2.4.11           | axe, Playwright, ESLint |
| `SEMANTIC`   | 1.3.1–1.3.5, 3.1.1–3.2.2, 4.1.1 | Pa11y, axe, Playwright, ESLint |
| `ARIA`       | 4.1.2, 4.1.3            | Pa11y, axe, Playwright, ESLint |
| `OTHER`      | Demais critérios        | Qualquer ferramenta |

**Lógica de classificação (prioridade):**
1. Se `wcag_criteria` presente → `WCAG_TO_ISSUE_TYPE[wcag_criteria]`
2. Se `rule_id` presente → `RULE_TO_ISSUE_TYPE[rule_id]` (busca exata ou parcial)
3. Fallback → `OTHER`

---

## 10. Mapeamento WCAG → Complexity

A complexidade de correção é atribuída por critério WCAG:

| Complexity | Critérios WCAG | Descrição da correção |
|------------|----------------|-----------------------|
| `SIMPLE`   | 1.1.1, 2.4.2, 3.1.1, 4.1.1, 4.1.2, 4.1.3, 2.2.2 | Adicionar/corrigir 1 atributo |
| `MODERATE` | 1.3.1, 1.3.2, 2.1.1, 2.4.1, 2.4.3, 2.4.4, 2.4.6, 2.4.7 | Reestruturação parcial |
| `COMPLEX`  | 1.4.3, 1.4.6, 1.4.11, 1.4.10, 1.4.12, 1.3.4, 2.1.2, 2.4.11, 2.4.12 | Redesign substancial |

**Fallback:** Se WCAG não presente, usa impact:
- `critical` / `serious` → `MODERATE`
- `moderate` / `minor` → `SIMPLE`

---

## 11. Estrutura dos Outputs

### Por projeto: `dataset/results/{project_id}/`

#### `summary.json`
```json
{
  "total_issues": 42,
  "high_confidence": 28,
  "medium_confidence": 10,
  "low_confidence": 4,
  "files_scanned": 15,
  "files_with_issues": 11,
  "scan_duration_seconds": 87.3,
  "scan_date": "2026-03-18T14:22:00+00:00",
  "tools_succeeded": ["pa11y", "axe", "eslint"],
  "tool_versions": { "pa11y": "9.0.1", "axe": "4.9.1", "eslint": "v10.0.3" },
  "by_type": { "aria": 12, "label": 8, "alt_text": 6, "keyboard": 5 },
  "by_principle": { "robust": 20, "perceivable": 14, "operable": 8 },
  "by_impact": { "critical": 10, "serious": 18, "moderate": 14 },
  "by_criterion": { "4.1.2": 12, "1.1.1": 6, "2.1.1": 5 }
}
```

#### `scan_results.json`
Audit trail completo: um objeto por arquivo contendo todos os `ToolFinding` crus
e o `ScanResult` processado, incluindo `file_hash` (SHA-256) para rastreabilidade.

#### `findings.jsonl`
Um `ScanFinding` por linha — formato para análise com `pandas`, `jq`, ou scripts Python:

```jsonl
{"finding_id":"abc123","project_id":"saleor__storefront","file":"ProductCard.tsx","selector":"img","wcag_criteria":"1.1.1","rule_id":"image-alt","issue_type":"alt_text","impact":"critical","complexity":"simple","tool_consensus":3,"found_by":["pa11y","axe","playwright"],"confidence":"high","raw_findings":[...],"pinned_commit":"a3f9b2c","scan_date":"2026-03-18T14:22:01+00:00"}
```

### Consolidados: `dataset/results/`

#### `dataset_findings.jsonl`
Todos os findings de todos os projetos em um único arquivo JSONL. Entrada principal
para análise científica — pode ser carregado diretamente com pandas:

```python
import pandas as pd
df = pd.read_json("dataset/results/dataset_findings.jsonl", lines=True)
```

#### `dataset_stats.json`
Estatísticas agregadas do corpus:
```json
{
  "total_projects_in_catalog": 228,
  "total_projects_scanned": 74,
  "total_issues": 3421,
  "high_confidence_issues": 2187,
  "high_conf_rate_pct": 63.9,
  "by_type": { "aria": 890, "label": 670, "alt_text": 540 },
  "by_principle": { "robust": 1200, "perceivable": 1100 }
}
```

#### `live_findings.jsonl`
Arquivo atualizado em tempo real durante o scan. Monitorável com:
```bash
# Linux/macOS
python dataset/scripts/watch_scan.py

# Windows
.\.venv\Scripts\python.exe dataset\scripts\watch_scan.py
```

---

## 12. Diagnóstico de Falhas por Ferramenta

### "Nenhuma regra jsx-a11y encontrada nos findings"

**Causa mais comum:** ESLint não está encontrando o plugin `eslint-plugin-jsx-a11y` globalmente.

**Diagnóstico:**
```powershell
# Windows
.\fix_scanners.ps1 -CheckOnly

# Linux/macOS
bash fix_scanners.sh --check-only
```

**Soluções por causa:**

| Sintoma | Causa | Solução |
|---------|-------|---------|
| ESLint retorna 0 findings | Plugin não encontrado (silencia erros) | `npm install -g eslint-plugin-jsx-a11y` |
| ESLint 10 aborta com TypeError | Regra declarada não existe no plugin | Atualizar plugin: `npm install -g eslint-plugin-jsx-a11y@latest` |
| `NODE_PATH` não resolve | npm prefix customizado | Verificar `npm config get prefix` e PATH |
| ESLint 9 flat config falha | `.cjs` não carregado | Verificar `npx eslint --version` ≥ 9 |

### Pa11y não detecta nada

| Sintoma | Causa provável | Diagnóstico |
|---------|---------------|-------------|
| 0 findings em HTML com bugs óbvios | CDN bloqueado (firewall corporativo) | Verificar acesso a `unpkg.com` |
| Timeout frequente | CDN lento + timeout padrão insuficiente | Usar `--timeout 240` no `scan.py` |
| `returncode: 1` | Erro de Chromium (sandbox) | Rodar `fix_scanners.ps1` para instalar dependências |
| stdout vazio após retry | Versão de pa11y incompatível com Chrome | `npm install -g pa11y@latest` |

### axe-core CLI falha silenciosamente

| Sintoma | Causa provável | Solução |
|---------|---------------|---------|
| `ChromeDriver only supports Chrome version X` | Mismatch ChromeDriver/Chrome | `npx browser-driver-manager install chrome` |
| stdout vazio, sem erros | axe não consegue lançar Chrome | Instalar Chrome: `npx playwright install chromium` |
| Timeout em todo arquivo | chromedriver preso | `--timeout 240`, verificar se Chrome está instalado |

### Playwright + axe falha

| Sintoma | Causa | Solução |
|---------|-------|---------|
| `playwright_not_installed` | Pacote Python ausente | `pip install playwright` |
| `BrowserType.launch: Executable doesn't exist` | Chromium não instalado | `python -m playwright install chromium --with-deps` |
| `Error: page.wait_for_function: Timeout` | CDN axe não carregou (sem internet) | Instalar axe-core local: `npm install -g axe-core` |
| 0 violations no HTML com bugs | React não hidratou antes do axe | Normal para componentes com dados externos — limitação do harness |

### Scan com status `error` no catálogo

Causas e campos diagnósticos em `entry.scan`:

| `error_message`                  | Causa | Ação |
|----------------------------------|-------|------|
| `"Snapshot directory not found"` | snapshot.py não rodou para este projeto | Rodar `snapshot.py` antes |
| `"No component files found"`     | Projeto sem `.tsx`/`.jsx` nos `scan_paths` | Verificar `scan_paths` no YAML |
| `"No scan tools available."`     | Nenhuma ferramenta instalada | Rodar `fix_scanners.ps1` |
| Exceção Python qualquer          | Bug no runner ou timeout total | Ver logs com `--workers 1` para isolar |

### Verificação rápida do ambiente (Windows)

```powershell
# 1. Verificar todas as ferramentas de uma vez
.\fix_scanners.ps1 -CheckOnly

# 2. Testar scan em 1 projeto com 1 arquivo
.\.venv\Scripts\python.exe dataset\scripts\scan.py `
  --project saleor__storefront `
  --max-files 2 `
  --workers 1

# 3. Verificar se ESLint produz findings
.\.venv\Scripts\python.exe dataset\scripts\findings_report.py
```

---

## Referências Cruzadas

| Conceito | Arquivo de implementação |
|----------|--------------------------|
| Orquestrador | `a11y_autofix/scanner/orchestrator.py` |
| Runner Pa11y | `a11y_autofix/scanner/pa11y.py` |
| Runner axe-core CLI | `a11y_autofix/scanner/axe.py` |
| Runner Playwright+axe | `a11y_autofix/scanner/playwright_axe.py` |
| Runner ESLint | `a11y_autofix/scanner/eslint.py` |
| Protocolo de detecção | `a11y_autofix/protocol/detection.py` |
| Geração do harness | `a11y_autofix/utils/files.py` |
| Servidor HTTP local | `a11y_autofix/utils/http_server.py` |
| Modelos de dados | `dataset/schema/models.py` |
| Script de scan | `dataset/scripts/scan.py` |
| Relatório de findings | `dataset/scripts/findings_report.py` |
| Protocolo científico | `dataset/PROTOCOL.md` |
