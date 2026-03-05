# Protocolo Científico de Detecção

Documentação técnica do protocolo de detecção de problemas de acessibilidade do a11y-autofix, projetado para garantir reprodutibilidade e rigor científico.

---

## Índice

1. [Visão Geral](#visão-geral)
2. [Identificação de Artefatos](#identificação-de-artefatos)
3. [Pipeline de Detecção](#pipeline-de-detecção)
4. [Deduplicação Cross-Tool](#deduplicação-cross-tool)
5. [Sistema de Confiança](#sistema-de-confiança)
6. [Mapeamentos WCAG](#mapeamentos-wcag)
7. [Ordenação Determinística](#ordenação-determinística)
8. [Rastreabilidade](#rastreabilidade)
9. [Formato de Saída](#formato-de-saída)

---

## Visão Geral

O protocolo científico garante que:

1. **Reprodutibilidade**: a mesma entrada sempre gera a mesma saída
2. **Rastreabilidade**: cada decisão é registrada com justificativa
3. **Comparabilidade**: resultados de diferentes runs podem ser comparados
4. **Integridade**: SHA-256 previne adulteração silenciosa de artefatos

O protocolo é implementado em `a11y_autofix/protocol/detection.py`.

---

## Identificação de Artefatos

### Hash de Arquivo

Todo arquivo processado recebe um hash SHA-256 calculado sobre seu conteúdo em bytes:

```python
# Formato: sha256:<64 hex chars>
file_hash = hash_file(path)
# Exemplo: sha256:a3f2c1...
```

O hash é calculado **antes** de qualquer modificação e incluído no audit trail.

### ID Estável de Issue

Cada issue recebe um ID de 16 caracteres derivado deterministicamente de:

```
id = SHA-256(file_path + ":" + selector + ":" + wcag_criteria + ":" + issue_type)[:16]
```

O mesmo problema no mesmo arquivo **sempre** gera o mesmo ID entre diferentes runs, permitindo rastrear se um problema foi corrigido em uma execução subsequente.

### ID de Execução

Cada execução do pipeline recebe um UUID v4 único:

```python
execution_id = str(uuid.uuid4())
```

---

## Pipeline de Detecção

```
Arquivo React (.tsx/.jsx/.ts/.js)
         │
         ▼
  build_html_harness()           → HTML com React 18 UMD + Babel
         │
         ▼
  ┌──────┴──────────────────────────────────┐
  │        asyncio.gather (paralelo)        │
  │  Pa11yRunner  AxeRunner  LighthouseRunner  PlaywrightAxeRunner  │
  └──────┬──────────────────────────────────┘
         │  list[ToolFinding] por ferramenta
         ▼
  DetectionProtocol.merge_findings()
         │
         ├─ _dedup_key() para cada finding
         ├─ Agrupa por chave
         ├─ Calcula confiança por consenso
         ├─ Mapeia WCAG → IssueType
         ├─ Mapeia WCAG → Complexity
         └─ Ordena deterministicamente
         │
         ▼
  list[A11yIssue]  →  ScanResult
```

---

## Deduplicação Cross-Tool

Issues do mesmo elemento detectadas por ferramentas diferentes são mescladas usando uma chave de deduplicação:

```python
def _dedup_key(finding: ToolFinding) -> str:
    wcag = finding.wcag_criteria or finding.rule_id or "unknown"
    return f"{finding.selector}::{wcag}"
```

Exemplo: se pa11y e axe detectam `aria-label` faltando no mesmo seletor `.login-btn`:

```
pa11y: { selector: ".login-btn", wcag: "4.1.2", rule: "aria-allowed-attr" }
axe:   { selector: ".login-btn", wcag: "4.1.2", rule: "aria-required-attr" }
```

Chave: `".login-btn"::4.1.2` → **um único issue** com `detected_by = ["pa11y", "axe"]`

### Seletor Normalizado

Seletores são normalizados antes da deduplicação:
- Espaços extras removidos
- Atributos de ID preferidos sobre classes dinâmicas

---

## Sistema de Confiança

A confiança de um issue é determinada pelo número de ferramentas que o detectaram:

| Ferramentas | Confiança | Prioridade |
|-------------|-----------|------------|
| ≥ 2 | `HIGH` | Tratado primeiro |
| 1 (ferramenta primária: pa11y/axe) | `MEDIUM` | Tratado em seguida |
| 1 (ferramenta secundária) | `LOW` | Tratado por último |

Ferramentas "primárias" são aquelas com maior precision histórica para WCAG:
- `pa11y` — baseado em HTML CodeSniffer, alta precision para estrutura
- `axe` — engine do Deque Systems, padrão da indústria

Lighthouse e Playwright+axe são consideradas ferramentas de confirmação.

```python
# Lógica de confiança
if len(detected_by) >= 2:
    confidence = Confidence.HIGH
elif detected_by[0] in ("pa11y", "axe"):
    confidence = Confidence.MEDIUM
else:
    confidence = Confidence.LOW
```

---

## Mapeamentos WCAG

### WCAG → Tipo de Issue

O sistema mapeia critérios WCAG para tipos semanticamente ricos:

| Critério WCAG | Tipo de Issue | Descrição |
|---------------|---------------|-----------|
| 1.1.1 | `ALT_TEXT` | Imagens sem texto alternativo |
| 1.3.1 | `SEMANTIC_HTML` | Informação transmitida apenas por formatação |
| 1.4.1 | `COLOR_CONTRAST` | Uso de cor como única pista |
| 1.4.3 | `COLOR_CONTRAST` | Contraste de texto insuficiente |
| 1.4.11 | `COLOR_CONTRAST` | Contraste de componentes de interface |
| 2.1.1 | `KEYBOARD_NAV` | Funcionalidade não acessível por teclado |
| 2.4.3 | `FOCUS_MANAGEMENT` | Ordem de foco inadequada |
| 2.4.7 | `FOCUS_MANAGEMENT` | Sem indicador visual de foco |
| 3.3.1 | `FORM_LABEL` | Identificação de erros em formulários |
| 3.3.2 | `FORM_LABEL` | Labels ou instruções ausentes |
| 4.1.1 | `ARIA` | Parsing de markup inválido |
| 4.1.2 | `ARIA` | Nome, função, valor não programáticos |
| 4.1.3 | `ARIA` | Mensagens de status |

Regras específicas de ferramentas (ex: axe rule IDs) também são mapeadas:

| Rule ID | Tipo de Issue |
|---------|---------------|
| `color-contrast` | `COLOR_CONTRAST` |
| `image-alt` | `ALT_TEXT` |
| `label` | `FORM_LABEL` |
| `aria-*` | `ARIA` |
| `button-name` | `ARIA` |
| `keyboard` | `KEYBOARD_NAV` |
| `focus-order-semantics` | `FOCUS_MANAGEMENT` |
| `landmark-*` | `SEMANTIC_HTML` |

### WCAG → Complexidade

A complexidade determina o agente de correção mais adequado:

| Critério | Complexidade | Justificativa |
|----------|-------------|---------------|
| 1.1.1 (alt text) | `SIMPLE` | Adicionar atributo `alt` |
| 1.3.1 (semantic) | `COMPLEX` | Reestruturar HTML |
| 1.4.3 (contrast) | `COMPLEX` | Alterar tokens de design |
| 2.1.1 (keyboard) | `COMPLEX` | Adicionar handlers e focus |
| 2.4.7 (focus) | `MODERATE` | CSS de outline |
| 3.3.2 (labels) | `SIMPLE` | Adicionar `<label>` |
| 4.1.2 (aria) | `MODERATE` | Adicionar atributos ARIA |

---

## Ordenação Determinística

Issues são ordenados por uma chave composta que garante ordem idêntica entre runs:

```python
issues.sort(key=lambda i: (
    -_CONFIDENCE_ORDER[i.confidence],  # HIGH primeiro (-2, -1, 0)
    -_IMPACT_ORDER.get(i.impact, 0),   # Critical primeiro
    i.wcag_criteria or "9.9.9",        # Ordem numérica WCAG
    i.selector,                         # Desempate alfanumérico
    i.issue_type.value,                # Desempate final
))
```

Isso garante que:
- Issues de HIGH confidence sempre precedem MEDIUM e LOW
- Dentro de cada nível de confiança, issues críticos têm prioridade
- Desempates são resolvidos de forma determinística (não por ordem de chegada)

---

## Rastreabilidade

### O que é Registrado

Para cada execução, o audit trail JSON registra:

```json
{
  "schema_version": "2.0",
  "execution_id": "uuid-v4",
  "timestamp": "2024-01-15T14:32:00+00:00",
  "environment": {
    "python_version": "3.12.0",
    "os": "darwin",
    "tools": {
      "pa11y": "6.2.3",
      "axe": "4.9.1",
      "lighthouse": "12.0.0",
      "playwright": "1.45.0"
    }
  },
  "configuration": {
    "model": "qwen2.5-coder:7b",
    "wcag_level": "AA",
    "min_tool_consensus": 2,
    "temperature": 0.1
  },
  "files": [
    {
      "path": "src/components/Button.tsx",
      "file_hash": "sha256:a3f2c1...",
      "issues": [...],
      "fix_attempts": [
        {
          "agent": "swe_agent",
          "model": "qwen2.5-coder:7b",
          "success": true,
          "duration_s": 12.3,
          "tokens_used": 1842,
          "diff": "--- a/Button.tsx\n+++ b/Button.tsx\n...",
          "final_hash": "sha256:b4f3d2..."
        }
      ]
    }
  ]
}
```

### Verificação de Integridade

Para verificar que um arquivo não foi modificado fora do sistema:

```python
from a11y_autofix.utils.hashing import hash_file
from pathlib import Path

recorded_hash = "sha256:a3f2c1..."
current_hash = hash_file(Path("src/components/Button.tsx"))

if current_hash != recorded_hash:
    print("AVISO: arquivo modificado fora do sistema")
```

---

## Formato de Saída

### ScanResult

```python
@dataclass
class ScanResult:
    file: Path
    file_hash: str           # SHA-256 do arquivo original
    issues: list[A11yIssue]  # Issues deduplicados e ordenados
    tool_versions: dict      # Versões das ferramentas usadas
    scan_duration_s: float   # Tempo total do scan
    timestamp: datetime      # ISO 8601 com timezone
```

### A11yIssue

```python
@dataclass
class A11yIssue:
    id: str                  # 16-char hash estável
    file: str                # Caminho do arquivo
    selector: str            # Seletor CSS do elemento
    issue_type: IssueType    # Tipo semântico
    wcag_criteria: str | None # Ex: "1.4.3"
    message: str             # Descrição humana
    confidence: Confidence   # HIGH | MEDIUM | LOW
    complexity: Complexity   # SIMPLE | MODERATE | COMPLEX
    detected_by: list[str]   # Ferramentas que detectaram
    impact: str | None       # critical | serious | moderate | minor
```

---

## Limitações Conhecidas

1. **Componentes dinâmicos**: issues que aparecem apenas após interação do usuário podem não ser detectados pelo harness estático

2. **Mocking limitado**: o harness mockeia apenas hooks e componentes comuns (react-router, clsx). Componentes que dependem de contextos complexos podem falhar na renderização

3. **Seletores CSS**: seletores gerados dinamicamente (ex: CSS-in-JS com hashes) podem variar entre renders, dificultando a deduplicação

4. **Falsos positivos de contraste**: ferramentas de scan às vezes reportam problemas de contraste em elementos com backgrounds complexos (gradientes, imagens) que são difíceis de calcular estaticamente
