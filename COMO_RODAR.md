# Como Rodar o a11y-autofix

Guia completo em português para configurar e usar o sistema de correção automática de acessibilidade.

---

## Índice

1. [Pré-requisitos](#pré-requisitos)
2. [Setup em 1 Comando](#setup-em-1-comando)
3. [Configurar Backend LLM](#configurar-backend-llm)
4. [Adicionar Modelos](#adicionar-modelos)
5. [Uso Básico](#uso-básico)
6. [Executar Experimentos](#executar-experimentos)
7. [Flags Completas da CLI](#flags-completas-da-cli)
8. [Troubleshooting](#troubleshooting)

---

## Pré-requisitos

Antes de começar, instale:

### Python 3.10+

```bash
# Verificar versão
python --version  # Deve ser 3.10 ou superior

# macOS com Homebrew
brew install python@3.12

# Ubuntu/Debian
sudo apt install python3.12 python3.12-venv
```

### Node.js 18+

```bash
# Verificar versão
node --version  # Deve ser 18 ou superior

# macOS com Homebrew
brew install node

# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### Ollama (recomendado para iniciar)

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Verificar instalação
ollama --version
```

---

## Setup em 1 Comando

Após instalar Python e Node.js:

```bash
# Clonar o repositório
git clone <repo-url>
cd a11y-autofix

# Instalar dependências Python
pip install -e ".[dev]"

# Executar setup completo
a11y-autofix setup
```

O comando `setup` faz automaticamente:

1. ✅ Verifica versão do Python (≥3.10)
2. ✅ Verifica Node.js
3. ✅ Instala `pa11y` via npm
4. ✅ Instala `@axe-core/cli` via npm
5. ✅ Instala `lighthouse` via npm
6. ✅ Instala Playwright Chromium
7. ✅ Cria arquivo `.env` a partir de `.env.example`

Para verificar o que foi instalado:

```bash
a11y-autofix scanners list
```

Saída esperada:
```
┌──────────────────┬──────────────┬─────────┐
│ Scanner          │ Disponível   │ Versão  │
├──────────────────┼──────────────┼─────────┤
│ pa11y            │ ✓            │ 6.2.3   │
│ axe              │ ✓            │ 4.9.1   │
│ lighthouse       │ ✓            │ 12.0.0  │
│ playwright_axe   │ ✓            │ 1.45.0  │
└──────────────────┴──────────────┴─────────┘
```

---

## Configurar Backend LLM

O sistema suporta qualquer backend compatível com a API OpenAI. Escolha o de sua preferência:

### Opção 1: Ollama (recomendado)

```bash
# Instalar
curl -fsSL https://ollama.com/install.sh | sh

# Baixar modelo recomendado (4.7 GB)
ollama pull qwen2.5-coder:7b

# Verificar que está rodando
curl http://localhost:11434/v1/models

# Testar via a11y-autofix
a11y-autofix models test qwen2.5-coder:7b
```

O `.env` padrão já aponta para Ollama em `http://localhost:11434`.

### Opção 2: LM Studio

1. Baixe o [LM Studio](https://lmstudio.ai/)
2. Baixe um modelo GGUF (ex: Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf)
3. Inicie o servidor local em `Settings > Local Server`
4. Configure o `.env`:

```env
LLM_BASE_URL=http://localhost:1234
```

### Opção 3: vLLM

```bash
# Instalar
pip install vllm

# Servir modelo
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --port 8000

# Configurar .env
LLM_BASE_URL=http://localhost:8000
```

### Opção 4: llama.cpp

```bash
# Compilar e servir
./llama-server -m qwen2.5-coder-7b.gguf --port 8080

# Configurar .env
LLM_BASE_URL=http://localhost:8080
```

### Verificar Configuração

```bash
# Testar modelo padrão
a11y-autofix models test

# Testar modelo específico
a11y-autofix models test qwen2.5-coder:14b

# Ver informações do modelo
a11y-autofix models info qwen2.5-coder:7b
```

---

## Adicionar Modelos

Há dois jeitos de adicionar um novo modelo — **sem modificar código**.

### Método 1: Editar `models.yaml`

Abra `models.yaml` e adicione um novo modelo:

```yaml
models:
  # ... modelos existentes ...

  - id: meu-modelo:13b
    backend: ollama        # ollama | lm_studio | vllm | llamacpp | custom
    family: minha-familia
    size_b: 13             # tamanho em bilhões de parâmetros
    context_length: 8192
    temperature: 0.1
    tags: [coding, portuguese]
    description: "Meu modelo customizado"
```

Depois registre:

```bash
# Recarregar registro (automático no próximo comando)
a11y-autofix models list

# Testar o novo modelo
a11y-autofix models test meu-modelo:13b
```

### Método 2: Via CLI

```bash
a11y-autofix models add meu-modelo:13b \
  --backend ollama \
  --family minha-familia \
  --size 13 \
  --context 8192 \
  --tags coding portuguese \
  --description "Meu modelo customizado"
```

### Auto-descoberta (Ollama)

Para descobrir automaticamente todos os modelos instalados no Ollama:

```bash
a11y-autofix models discover
```

Isso adiciona todos os modelos encontrados ao `models.yaml`.

### Criar Grupos de Modelos

Para agrupar modelos e usá-los em experimentos:

```yaml
model_groups:
  meus_modelos:
    - meu-modelo:7b
    - meu-modelo:13b

  modelos_pequenos:
    - qwen2.5-coder:7b
    - codellama:7b-instruct
    - llama3.1:8b-instruct-q4_K_M
```

Uso em experimentos:

```yaml
# experiments/meu_experimento.yaml
models:
  - group:meus_modelos
```

---

## Uso Básico

### Corrigir um Arquivo

```bash
# Arquivo único
a11y-autofix fix src/components/Button.tsx

# Com modelo específico
a11y-autofix fix src/components/Button.tsx --model qwen2.5-coder:14b
```

### Corrigir um Diretório

```bash
# Todos os arquivos .tsx/.jsx/.ts/.js
a11y-autofix fix ./src

# Subdiretório específico
a11y-autofix fix src/components/
```

### Só Escanear (sem correção)

```bash
# Ver problemas sem modificar arquivos
a11y-autofix fix ./src --dry-run

# Scan com relatório JSON
a11y-autofix fix ./src --dry-run --output ./scan-results
```

### Escolher Ferramentas de Scan

```bash
# Usar apenas pa11y e axe
a11y-autofix fix ./src --tools pa11y axe

# Usar todas as ferramentas
a11y-autofix fix ./src --tools pa11y axe lighthouse playwright

# Desabilitar uma ferramenta específica (via .env)
# USE_LIGHTHOUSE=false
```

### Filtrar por Nível WCAG

```bash
# WCAG 2.1 nível AA (padrão)
a11y-autofix fix ./src --wcag-level AA

# Apenas nível A (mais permissivo)
a11y-autofix fix ./src --wcag-level A

# Nível AAA (mais rigoroso)
a11y-autofix fix ./src --wcag-level AAA
```

### Salvar Relatórios

```bash
# Salvar em diretório
a11y-autofix fix ./src --output ./reports/

# Arquivos gerados:
# reports/report_<timestamp>.json    → audit trail completo
# reports/report_<timestamp>.html    → relatório visual
```

### Usar com Git

```bash
# Criar branch automática com as correções
a11y-autofix fix ./src --create-branch a11y-fixes

# Criar PR no GitHub após correção
a11y-autofix fix ./src --create-pr
```

---

## Executar Experimentos

Experimentos comparam múltiplos modelos nas mesmas condições, gerando relatórios científicos.

### Experimento Pré-configurado

```bash
# Qwen vs DeepSeek
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml

# Todos os modelos
a11y-autofix experiment experiments/all_models_comparison.yaml

# Estudo de ablação
a11y-autofix experiment experiments/ablation_study.yaml
```

### Criar um Experimento

Crie um arquivo YAML em `experiments/`:

```yaml
# experiments/meu_experimento.yaml
name: meu_experimento
description: Comparar modelos pequenos no meu projeto

models:
  - qwen2.5-coder:7b
  - codellama:7b-instruct
  - llama3.1:8b-instruct-q4_K_M

files:
  - src/components/

wcag_level: AA
tools:
  - pa11y
  - axe

runs_per_model: 3           # Repetições para robustez estatística
max_concurrent_models: 2    # Modelos em paralelo
temperature: 0.1            # Temperatura reprodutível

output_dir: results/meu_experimento
```

Executar:

```bash
a11y-autofix experiment experiments/meu_experimento.yaml
```

### Resultados do Experimento

Os resultados são salvos em `output_dir`:

```
results/meu_experimento/
├── experiment_<timestamp>.json    → dados brutos completos
├── comparison.html                → relatório visual comparativo
└── metrics.csv                    → dados para análise estatística
```

Visualizar o relatório:

```bash
open results/meu_experimento/comparison.html
```

### Analisar Resultados

```bash
# Analisar um experimento
a11y-autofix analyze results/meu_experimento/experiment_*.json

# Comparar dois experimentos
a11y-autofix analyze results/exp1/experiment_*.json results/exp2/experiment_*.json
```

---

## Flags Completas da CLI

### `a11y-autofix fix`

```
ARGUMENTOS:
  target                 Arquivo, diretório ou glob (ex: src/components/)

OPÇÕES:
  --model       -m TEXT  Modelo LLM (ex: qwen2.5-coder:7b) [padrão: DEFAULT_MODEL]
  --output      -o PATH  Diretório de saída para relatórios
  --tools       -t TEXT  Ferramentas de scan [múltiplos: --tools pa11y --tools axe]
  --wcag-level  -w TEXT  Nível WCAG: A, AA, AAA [padrão: AA]
  --dry-run              Apenas escanear, sem corrigir
  --create-branch TEXT   Criar git branch com as correções
  --create-pr            Criar PR no GitHub após correção
  --max-retries INT      Tentativas por arquivo [padrão: 2]
  --workers     INT      Arquivos em paralelo [padrão: 4]
  --verbose     -v       Saída detalhada
  --help                 Mostrar ajuda
```

### `a11y-autofix experiment`

```
ARGUMENTOS:
  config                 Arquivo YAML de configuração do experimento

OPÇÕES:
  --output      -o PATH  Sobrescrever diretório de saída
  --verbose     -v       Saída detalhada
  --help                 Mostrar ajuda
```

### `a11y-autofix models`

```
SUBCOMANDOS:
  list                   Listar todos os modelos registrados
    --family TEXT        Filtrar por família (ex: qwen, deepseek)
    --backend TEXT       Filtrar por backend (ex: ollama)
    --tag TEXT           Filtrar por tag
    --available          Mostrar apenas disponíveis

  test [MODEL]           Testar conectividade com modelo
    --all                Testar todos os modelos

  info MODEL             Informações detalhadas do modelo

  add MODEL              Registrar novo modelo
    --backend TEXT       Backend (ollama, lm_studio, vllm, llamacpp, custom)
    --family TEXT        Família do modelo
    --size FLOAT         Tamanho em bilhões de parâmetros
    --context INT        Comprimento do contexto
    --base-url TEXT      URL base personalizada
    --tags TEXT          Tags (múltiplos: --tags coding --tags portuguese)
    --description TEXT   Descrição do modelo

  discover               Auto-descobrir modelos no Ollama
```

### `a11y-autofix scanners`

```
SUBCOMANDOS:
  list                   Listar scanners disponíveis com status
```

### `a11y-autofix analyze`

```
ARGUMENTOS:
  reports...             Um ou mais arquivos JSON de relatório

OPÇÕES:
  --format TEXT          Formato de saída: table, json [padrão: table]
```

### `a11y-autofix setup`

```
OPÇÕES:
  --skip-node            Pular instalação de ferramentas Node.js
  --skip-playwright      Pular instalação do Playwright
  --yes        -y        Confirmar tudo automaticamente
```

---

## Troubleshooting

### Erro: `ollama: command not found`

```bash
# Instalar Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Verificar que está rodando
ollama serve &
curl http://localhost:11434/api/tags
```

### Erro: `Model not found` ou `Connection refused`

```bash
# Verificar se Ollama está rodando
ps aux | grep ollama

# Iniciar Ollama
ollama serve

# Verificar modelo
ollama list
ollama pull qwen2.5-coder:7b
```

### Erro: `pa11y: command not found`

```bash
# Instalar globalmente
npm install -g pa11y

# Ou instalar localmente (se problemas de permissão)
npm install pa11y --prefix ~/.local
export PATH="$HOME/.local/bin:$PATH"
```

### Erro: `axe: command not found`

```bash
npm install -g @axe-core/cli
```

### Erro: `playwright: browser not installed`

```bash
# Instalar navegadores
playwright install chromium

# Ou via Python
python -m playwright install chromium
```

### Erros de permissão no npm

```bash
# Configurar diretório npm sem sudo
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc

# Reinstalar ferramentas
npm install -g pa11y @axe-core/cli lighthouse
```

### Timeout no scan (arquivo grande)

```bash
# Aumentar timeout via .env
SCAN_TIMEOUT=120  # segundos

# Ou via variável de ambiente
SCAN_TIMEOUT=120 a11y-autofix fix ./src
```

### Modelo responde lentamente / timeout no agente

```bash
# Aumentar timeout do agente
AGENT_TIMEOUT=300  # segundos

# Usar modelo menor e mais rápido
a11y-autofix fix ./src --model qwen2.5-coder:7b

# Reduzir paralelismo
a11y-autofix fix ./src --workers 1
```

### Problema de memória com modelos grandes

```bash
# Verificar memória disponível
free -h  # Linux
vm_stat  # macOS

# Usar modelo quantizado menor
ollama pull qwen2.5-coder:7b  # 4.7 GB
# em vez de
ollama pull qwen2.5-coder:14b  # 9 GB
```

### Relatório HTML não abre / CSS quebrado

```bash
# Verificar se o arquivo foi gerado
ls -la reports/

# Abrir diretamente no navegador
open reports/report_*.html   # macOS
xdg-open reports/report_*.html  # Linux
```

### Issues não aparecem no scan

Possíveis causas:

1. **Componente não renderiza**: O harness HTML pode falhar para componentes muito complexos
   ```bash
   # Verificar log detalhado
   a11y-autofix fix src/Component.tsx --verbose
   ```

2. **Ferramentas desabilitadas no `.env`**:
   ```env
   USE_PA11Y=true
   USE_AXE=true
   USE_PLAYWRIGHT=true
   ```

3. **Nível WCAG muito permissivo**:
   ```bash
   # Usar nível mais rigoroso
   a11y-autofix fix ./src --wcag-level A
   ```

### Logs Estruturados

Para debug avançado:

```bash
# Ativar logging estruturado JSON
LOG_LEVEL=DEBUG a11y-autofix fix ./src --verbose 2>&1 | jq .
```

### Abrir Issue

Se encontrar um bug, colete as informações:

```bash
# Versão do sistema
a11y-autofix --version
python --version
node --version
ollama --version

# Estado dos scanners
a11y-autofix scanners list

# Estado dos modelos
a11y-autofix models list
```

---

## Variáveis de Ambiente (.env)

Referência completa:

```env
# === MODELO PADRÃO ===
DEFAULT_MODEL=qwen2.5-coder:7b

# === FERRAMENTAS DE SCAN ===
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false      # Mais lento, desabilitado por padrão
USE_PLAYWRIGHT=true

# === PROTOCOLO DE DETECÇÃO ===
MIN_TOOL_CONSENSUS=2      # Ferramentas que devem concordar para "alta confiança"
WCAG_LEVEL=AA             # A | AA | AAA

# === TIMEOUTS ===
SCAN_TIMEOUT=60           # Timeout por arquivo por ferramenta (segundos)
AGENT_TIMEOUT=180         # Timeout total do agente (segundos)

# === PARALELISMO ===
MAX_CONCURRENT_FILES=4    # Arquivos em paralelo no pipeline
MAX_CONCURRENT_MODELS=2   # Modelos em paralelo no experimento

# === ROTEADOR DE AGENTES ===
SWE_MAX_ISSUES=5          # Issues abaixo deste → SWE-agent; acima → OpenHands

# === REPRODUTIBILIDADE ===
TEMPERATURE=0.1           # Temperatura padrão (0.0 para determinístico)
SEED=42                   # Seed para reprodutibilidade

# === DIRETÓRIOS ===
OUTPUT_DIR=./reports
TEMP_DIR=/tmp/a11y-autofix

# === LOGGING ===
LOG_LEVEL=INFO            # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=text           # text | json
```

---

## Próximos Passos

- [Adicionar novos modelos](docs/ADDING_MODELS.md)
- [Adicionar novas ferramentas de scan](docs/ADDING_TOOLS.md)
- [Configurar experimentos avançados](docs/EXPERIMENTS.md)
- [Entender o protocolo científico](docs/PROTOCOL.md)
