# ♿ a11y-autofix

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![WCAG 2.1/2.2](https://img.shields.io/badge/WCAG-2.1%2F2.2-green.svg)](https://www.w3.org/WAI/WCAG22/quickref/)
[![100% Local](https://img.shields.io/badge/LLM-100%25%20local-orange.svg)](docs/ADDING_MODELS.md)

Sistema de **detecção e correção automática de problemas de acessibilidade** em projetos React/TypeScript.

Projetado para pesquisa científica: reprodutível, multi-ferramenta, multi-modelo, 100% local (sem APIs pagas).

---

## Funcionalidades

- **Multi-ferramenta**: pa11y, axe-core, Lighthouse, Playwright+axe em paralelo
- **Consenso científico**: problemas detectados por ≥2 ferramentas → alta confiança
- **Multi-modelo**: compare Qwen, DeepSeek, CodeLlama, Llama e qualquer modelo OpenAI-compatible
- **Pluggável**: adicione novos modelos via `models.yaml` — zero código
- **Agentes múltiplos**: OpenHands, SWE-agent, DirectLLM com fallback automático
- **Router inteligente**: seleciona o agente ideal baseado na complexidade das issues
- **Reprodutibilidade**: SHA-256 em todos os artefatos, IDs estáveis, timestamps
- **Relatórios científicos**: JSON (audit trail) + HTML visual + CSV comparativo
- **CLI completa**: `fix`, `experiment`, `models`, `scanners`, `analyze`, `setup`

---

## Quick Start

```bash
# 1. Instalar o sistema
pip install -e .

# 2. Setup em 1 comando (verifica dependências, instala ferramentas Node.js)
a11y-autofix setup

# 3. Baixar um modelo LLM local
ollama pull qwen2.5-coder:7b

# 4. Corrigir acessibilidade em um projeto
a11y-autofix fix ./src --model qwen2.5-coder:7b

# 5. Comparar múltiplos modelos
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml
```

Para documentação completa em português, veja [COMO_RODAR.md](COMO_RODAR.md).

---

## Pré-requisitos

| Dependência | Versão | Uso |
|-------------|--------|-----|
| Python | ≥ 3.10 | Core do sistema |
| Node.js | ≥ 18 | Ferramentas de scan |
| [Ollama](https://ollama.com) | qualquer | Backend LLM padrão |
| [pa11y](https://pa11y.org/) | ≥ 6 | Scanner WCAG (npm) |
| [@axe-core/cli](https://github.com/dequelabs/axe-core) | ≥ 4 | Scanner axe (npm) |
| [lighthouse](https://github.com/GoogleChrome/lighthouse) | ≥ 12 | Scanner (npm) |
| [Playwright](https://playwright.dev/) | ≥ 1.45 | Scanner dinâmico |

---

## Instalação

```bash
# Clone o repositório
git clone <repo-url>
cd a11y-autofix

# Instalar dependências Python
pip install -e ".[dev]"

# Setup completo (Node.js tools + Playwright)
a11y-autofix setup

# Verificar instalação
a11y-autofix scanners list
a11y-autofix models list
```

---

## Uso

### Correção de um arquivo ou projeto

```bash
# Um arquivo
a11y-autofix fix src/components/Button.tsx

# Um diretório completo
a11y-autofix fix ./src

# Especificar modelo
a11y-autofix fix ./src --model deepseek-coder-v2:16b

# Apenas scan (sem correção)
a11y-autofix fix ./src --dry-run

# Especificar nível WCAG
a11y-autofix fix ./src --wcag-level AA

# Apenas ferramentas específicas
a11y-autofix fix ./src --tools pa11y axe

# Salvar relatório
a11y-autofix fix ./src --output ./reports
```

### Experimentos comparativos

```bash
# Executar experimento definido em YAML
a11y-autofix experiment experiments/qwen_vs_deepseek.yaml

# Com saída em diretório específico
a11y-autofix experiment experiments/all_models.yaml --output ./results

# Ver formato do arquivo de experimento
cat experiments/qwen_vs_deepseek.yaml
```

### Gerenciar modelos

```bash
# Listar todos os modelos registrados
a11y-autofix models list

# Filtrar por família
a11y-autofix models list --family qwen

# Testar conectividade com um modelo
a11y-autofix models test qwen2.5-coder:7b

# Informações detalhadas
a11y-autofix models info qwen2.5-coder:7b

# Adicionar modelo ao registro
a11y-autofix models add meu-modelo --backend ollama --base-url http://localhost:11434

# Auto-descobrir modelos disponíveis no Ollama
a11y-autofix models discover
```

### Analisar resultados

```bash
# Analisar relatório JSON
a11y-autofix analyze reports/report.json

# Comparar dois relatórios
a11y-autofix analyze reports/run1.json reports/run2.json
```

---

## Configuração

Copie `.env.example` para `.env` e ajuste:

```bash
cp .env.example .env
```

Variáveis principais:

```env
# Modelo padrão
DEFAULT_MODEL=qwen2.5-coder:7b

# Ferramentas de scan (todas ativas por padrão)
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false
USE_PLAYWRIGHT=true

# Consenso mínimo (quantas ferramentas devem concordar)
MIN_TOOL_CONSENSUS=2

# Limite de issues para usar OpenHands vs SWE-agent
SWE_MAX_ISSUES=5
```

---

## Adicionar Modelos

Edite `models.yaml` para registrar novos modelos — nenhuma mudança de código necessária:

```yaml
models:
  - id: meu-modelo:13b
    backend: ollama
    family: minha-familia
    size_b: 13
    tags: [coding, portuguese]
    context_length: 8192
```

Veja o guia completo: [docs/ADDING_MODELS.md](docs/ADDING_MODELS.md)

---

## Adicionar Ferramentas de Scan

Crie uma subclasse de `BaseScanner` e registre-a:

```python
from a11y_autofix.scanner.base import BaseScanner, ToolFinding

class MyScanner(BaseScanner):
    tool_name = "my-tool"

    async def run(self, html_path: Path) -> list[ToolFinding]:
        ...
```

Veja o guia completo: [docs/ADDING_TOOLS.md](docs/ADDING_TOOLS.md)

---

## Experimentos

Configure experimentos comparativos em YAML:

```yaml
name: qwen_vs_deepseek
models:
  - qwen2.5-coder:7b
  - deepseek-coder-v2:16b
files:
  - src/components/
wcag_level: AA
runs_per_model: 3
```

Veja o guia completo: [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)

---

## Protocolo Científico

Cada execução registra:

- **SHA-256** do arquivo original (antes e depois)
- **Versões** de todas as ferramentas de scan usadas
- **IDs estáveis** por issue (deterministicamente gerados)
- **Timestamps** ISO 8601 com timezone
- **Diffs unificados** de todas as correções
- **Metadados de modelo** (temperatura, tokens usados)

Veja detalhes: [docs/PROTOCOL.md](docs/PROTOCOL.md)

---

## Estrutura do Projeto

```
a11y-autofix/
├── a11y_autofix/
│   ├── config.py           # Modelos de dados centrais (Pydantic)
│   ├── pipeline.py         # Orquestrador principal
│   ├── cli.py              # Interface de linha de comando (Typer)
│   ├── protocol/           # Protocolo científico de detecção
│   ├── scanner/            # Runners de ferramentas (pa11y, axe, etc.)
│   ├── llm/                # Clientes LLM e registro de modelos
│   ├── agents/             # Agentes de correção (OpenHands, SWE, DirectLLM)
│   ├── router/             # Roteador inteligente de agentes
│   ├── experiments/        # Framework de experimentos
│   ├── reporter/           # Geradores de relatório (JSON, HTML, CSV)
│   └── utils/              # Utilitários (hashing, files, git, ui)
├── tests/
│   ├── unit/               # Testes unitários
│   ├── integration/        # Testes de integração
│   └── fixtures/           # Componentes React de exemplo
├── experiments/            # Configurações YAML de experimentos
├── docs/                   # Documentação técnica
├── scripts/                # Scripts auxiliares
├── models.yaml             # Registro de modelos LLM
├── .env.example            # Template de configuração
├── pyproject.toml          # Configuração do projeto
└── COMO_RODAR.md           # Guia em português
```

---

## Testes

```bash
# Todos os testes
pytest

# Com cobertura
pytest --cov=a11y_autofix --cov-report=html

# Apenas unitários (rápido)
pytest tests/unit/

# Apenas integração
pytest tests/integration/
```

---

## Backends LLM Suportados

| Backend | URL Padrão | Configuração |
|---------|-----------|--------------|
| Ollama | `http://localhost:11434` | `ollama pull <model>` |
| LM Studio | `http://localhost:1234` | GUI do LM Studio |
| vLLM | `http://localhost:8000` | `vllm serve <model>` |
| llama.cpp | `http://localhost:8080` | `llama-server -m model.gguf` |
| LocalAI | `http://localhost:8080` | Docker ou binário |
| Custom | Qualquer URL | `--base-url <url>` |

---

## Licença

MIT — veja [LICENSE](LICENSE).

---

## Citação

Se usar este sistema em pesquisa, cite:

```bibtex
@software{a11y_autofix_2024,
  title  = {a11y-autofix: Sistema de Correção Automática de Acessibilidade},
  year   = {2024},
  note   = {Experimento de mestrado — 100\% local, multi-modelo, WCAG 2.1/2.2}
}
```
