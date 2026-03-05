# Guia: Adicionar Novos Modelos LLM

Este guia explica como adicionar novos modelos ao a11y-autofix. O sistema foi projetado para suportar novos modelos **sem modificar código** — apenas editando arquivos de configuração.

---

## Índice

1. [Método Rápido: models.yaml](#método-rápido-modelsyaml)
2. [Método CLI](#método-cli)
3. [Auto-descoberta](#auto-descoberta)
4. [Grupos de Modelos](#grupos-de-modelos)
5. [Backends Suportados](#backends-suportados)
6. [Adicionar Novo Backend](#adicionar-novo-backend)
7. [Parâmetros de Configuração](#parâmetros-de-configuração)
8. [Testar o Modelo](#testar-o-modelo)

---

## Método Rápido: models.yaml

A forma mais simples é editar `models.yaml` diretamente:

```yaml
models:
  # Adicione ao final da lista de modelos existentes:

  - id: meu-modelo:7b
    backend: ollama
    family: minha-familia
    size_b: 7
    context_length: 8192
    temperature: 0.1
    tags: [coding]
    description: "Meu modelo customizado 7B"
```

### Campos Obrigatórios

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | string | Identificador único (ex: `family:size`) |
| `backend` | enum | Backend LLM (ver lista abaixo) |
| `family` | string | Família do modelo (ex: `qwen`, `deepseek`) |

### Campos Opcionais

| Campo | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `size_b` | float | null | Parâmetros em bilhões |
| `context_length` | int | 4096 | Janela de contexto |
| `temperature` | float | 0.1 | Temperatura para geração |
| `base_url` | string | null | URL personalizada (sobrescreve padrão do backend) |
| `tags` | list[str] | [] | Tags para filtragem (ex: `[coding, portuguese]`) |
| `description` | string | "" | Descrição humana |

---

## Método CLI

```bash
# Adicionar modelo básico
a11y-autofix models add meu-modelo:7b \
  --backend ollama \
  --family minha-familia \
  --size 7

# Adicionar com todas as opções
a11y-autofix models add meu-modelo:13b \
  --backend lm_studio \
  --family minha-familia \
  --size 13 \
  --context 16384 \
  --base-url http://localhost:1234 \
  --tags coding portuguese \
  --description "Modelo para testes em português"
```

O comando CLI atualiza automaticamente o `models.yaml`.

---

## Auto-descoberta

Para Ollama, você pode descobrir todos os modelos instalados automaticamente:

```bash
# Descobre e registra todos os modelos no Ollama local
a11y-autofix models discover

# Verificar o que foi adicionado
a11y-autofix models list
```

A descoberta automática:
1. Consulta `GET http://localhost:11434/v1/models`
2. Para cada modelo encontrado, tenta inferir família e tamanho pelo nome
3. Adiciona ao `models.yaml` se ainda não estiver registrado

---

## Grupos de Modelos

Grupos permitem usar múltiplos modelos em experimentos com um único nome:

```yaml
# models.yaml
model_groups:
  # Grupo de modelos pequenos (rápidos)
  small_models:
    - qwen2.5-coder:7b
    - codellama:7b-instruct
    - llama3.1:8b-instruct-q4_K_M

  # Meus modelos customizados
  meus_modelos:
    - meu-modelo:7b
    - meu-modelo:13b

  # Todos os modelos de código
  all_coding:
    - qwen2.5-coder:7b
    - qwen2.5-coder:14b
    - deepseek-coder-v2:16b
    - codellama:7b-instruct
```

Usar em experimentos:

```yaml
# experiments/meu_experimento.yaml
models:
  - group:meus_modelos
  - group:small_models
```

Usar na CLI:

```bash
# Listar modelos de um grupo
a11y-autofix models list --group meus_modelos

# Testar todos os modelos de um grupo
a11y-autofix models test --group meus_modelos
```

---

## Backends Suportados

### `ollama`

Backend padrão. Requer Ollama rodando localmente.

```yaml
- id: qwen2.5-coder:7b
  backend: ollama
  # URL padrão: http://localhost:11434
```

```bash
# Instalar e baixar modelo
ollama pull qwen2.5-coder:7b
```

### `lm_studio`

Para modelos servidos pelo LM Studio.

```yaml
- id: qwen-coder-7b-instruct
  backend: lm_studio
  family: qwen
  # URL padrão: http://localhost:1234
```

### `vllm`

Para modelos servidos pelo vLLM (alta performance).

```yaml
- id: Qwen/Qwen2.5-Coder-7B-Instruct
  backend: vllm
  family: qwen
  size_b: 7
  # URL padrão: http://localhost:8000
```

```bash
# Servir com vLLM
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --port 8000
```

### `llamacpp`

Para modelos GGUF servidos pelo llama.cpp.

```yaml
- id: qwen2.5-coder-7b-q4
  backend: llamacpp
  family: qwen
  # URL padrão: http://localhost:8080
```

```bash
# Servir com llama.cpp
./llama-server -m qwen2.5-coder-7b-instruct-q4_k_m.gguf \
  --port 8080 \
  --ctx-size 8192
```

### `custom`

Para qualquer backend compatível com a API OpenAI.

```yaml
- id: meu-servidor:custom
  backend: custom
  family: custom
  base_url: http://meu-servidor:9000
```

Requisitos do servidor:
- Endpoint `POST /v1/chat/completions` compatível com OpenAI
- Endpoint `GET /v1/models` para health check

---

## Adicionar Novo Backend

Se nenhum dos backends existentes atender sua necessidade, crie um novo.

### 1. Criar o backend

Crie `a11y_autofix/llm/backends/meu_backend.py`:

```python
"""Backend para Meu Servidor LLM."""
from __future__ import annotations

import httpx
from a11y_autofix.llm.base import BaseLLMClient
from a11y_autofix.config import ModelConfig


class MeuBackendClient(BaseLLMClient):
    """Cliente para Meu Backend LLM."""

    def __init__(self, model_config: ModelConfig) -> None:
        super().__init__(model_config)
        base_url = model_config.base_url or "http://localhost:9000"
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """Gera resposta usando a API do meu backend."""
        payload = {
            "model": self.model_config.id,
            "messages": messages,
            "temperature": temperature or self.model_config.temperature,
            "max_tokens": max_tokens,
        }
        response = await self._client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def health_check(self) -> bool:
        """Verifica se o servidor está disponível."""
        try:
            response = await self._client.get("/v1/models", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    async def get_model_info(self) -> dict[str, object]:
        """Retorna informações sobre o modelo."""
        return {
            "id": self.model_config.id,
            "backend": "meu_backend",
        }
```

### 2. Registrar o backend

Edite `a11y_autofix/llm/backends/__init__.py`:

```python
from .meu_backend import MeuBackendClient

__all__ = [
    ...,
    "MeuBackendClient",
]
```

Edite `a11y_autofix/llm/client.py` para adicionar o backend ao registry:

```python
from .backends.meu_backend import MeuBackendClient

_BACKEND_MAP = {
    LLMBackend.OLLAMA: OllamaClient,
    LLMBackend.LM_STUDIO: LMStudioClient,
    ...
    LLMBackend.MEU_BACKEND: MeuBackendClient,  # Adicionar aqui
}
```

Edite `a11y_autofix/config.py` para adicionar o enum:

```python
class LLMBackend(str, Enum):
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    CUSTOM = "custom"
    MEU_BACKEND = "meu_backend"  # Adicionar aqui
```

### 3. Usar o novo backend

```yaml
# models.yaml
- id: meu-modelo:7b
  backend: meu_backend
  family: custom
```

---

## Parâmetros de Configuração

### Temperatura

Controla a aleatoriedade da geração:

```yaml
temperature: 0.0   # Determinístico (ideal para reprodutibilidade)
temperature: 0.1   # Quase determinístico (padrão recomendado)
temperature: 0.7   # Criativo (não recomendado para código)
```

Para experimentos reprodutíveis, use `temperature: 0.0` ou `temperature: 0.1`.

### Contexto

Tamanho da janela de contexto do modelo:

```yaml
context_length: 4096    # Mínimo viável para código
context_length: 8192    # Recomendado
context_length: 32768   # Modelos maiores (ex: Qwen2.5-Coder 14B)
context_length: 131072  # Modelos com contexto longo
```

Um contexto maior permite processar arquivos mais complexos, mas usa mais memória.

### URL Personalizada

Para modelos em servidores remotos ou portas não-padrão:

```yaml
- id: modelo-remoto:7b
  backend: vllm
  family: qwen
  base_url: http://192.168.1.100:8000
```

---

## Testar o Modelo

Após adicionar, teste o modelo:

```bash
# Health check
a11y-autofix models test meu-modelo:7b

# Ver informações detalhadas
a11y-autofix models info meu-modelo:7b

# Teste de geração (prompt simples)
a11y-autofix models test meu-modelo:7b --verbose
```

Saída esperada de um modelo funcionando:
```
✓ meu-modelo:7b
  Backend:  ollama
  Status:   disponível
  Resposta: 0.8s
  Tokens:   128
```

### Troubleshooting de Modelos

```bash
# Modelo não aparece na lista
a11y-autofix models list  # Verificar se foi salvo

# Erro de conexão
curl http://localhost:11434/v1/models  # Verificar se backend está rodando

# Modelo lento
a11y-autofix models test meu-modelo:7b --timeout 120
```
