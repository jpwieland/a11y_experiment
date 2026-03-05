# Guia: Adicionar Novas Ferramentas de Scan

Este guia explica como adicionar novas ferramentas de scan de acessibilidade ao a11y-autofix.

---

## Índice

1. [Arquitetura dos Scanners](#arquitetura-dos-scanners)
2. [Criar um Scanner](#criar-um-scanner)
3. [Registrar o Scanner](#registrar-o-scanner)
4. [Testar o Scanner](#testar-o-scanner)
5. [Boas Práticas](#boas-práticas)
6. [Exemplo Completo](#exemplo-completo)

---

## Arquitetura dos Scanners

Todos os scanners herdam de `BaseScanner` e são executados em paralelo pelo `MultiToolScanner`:

```
MultiToolScanner
    │
    ├── Pa11yRunner
    ├── AxeRunner
    ├── LighthouseRunner
    ├── PlaywrightAxeRunner
    └── MeuScanner           ← você adiciona aqui
```

### BaseScanner

Definido em `a11y_autofix/scanner/base.py`:

```python
class BaseScanner(ABC):
    tool_name: str           # Nome único da ferramenta
    tool_version: str        # Versão (populada por check_available)

    @abstractmethod
    async def run(self, html_path: Path) -> list[ToolFinding]:
        """Executa o scan no arquivo HTML e retorna findings."""
        ...

    @abstractmethod
    async def check_available(self) -> bool:
        """Verifica se a ferramenta está disponível no sistema."""
        ...
```

### ToolFinding

```python
@dataclass
class ToolFinding:
    tool: str                  # Nome da ferramenta
    selector: str              # Seletor CSS do elemento
    message: str               # Descrição do problema
    wcag_criteria: str | None  # Ex: "1.4.3"
    rule_id: str | None        # ID da regra (ex: "color-contrast")
    impact: str | None         # critical | serious | moderate | minor
    help_url: str | None       # URL de documentação
    html_snippet: str | None   # HTML do elemento problemático
```

---

## Criar um Scanner

### 1. Criar o arquivo

Crie `a11y_autofix/scanner/meu_scanner.py`:

```python
"""Scanner baseado em Minha Ferramenta."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from .base import BaseScanner, ToolFinding

log = structlog.get_logger(__name__)


class MeuScanner(BaseScanner):
    """Runner para Minha Ferramenta de acessibilidade."""

    tool_name = "minha-ferramenta"

    async def check_available(self) -> bool:
        """Verifica se minha-ferramenta está instalada."""
        try:
            result = subprocess.run(
                ["minha-ferramenta", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                self.tool_version = result.stdout.strip()
                return True
            return False
        except FileNotFoundError:
            return False

    async def run(self, html_path: Path) -> list[ToolFinding]:
        """Executa minha-ferramenta no arquivo HTML.

        Args:
            html_path: Caminho para o arquivo HTML harness gerado.

        Returns:
            Lista de findings encontrados.
        """
        log.debug("meu_scanner.run", file=str(html_path))

        try:
            result = subprocess.run(
                [
                    "minha-ferramenta",
                    "--format", "json",
                    str(html_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("meu_scanner.timeout", file=str(html_path))
            return []
        except FileNotFoundError:
            log.error("meu_scanner.not_found")
            return []

        # Parsear saída JSON da ferramenta
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.error("meu_scanner.json_parse_error", stdout=result.stdout[:200])
            return []

        findings = []
        for issue in data.get("issues", []):
            finding = self._parse_issue(issue)
            if finding:
                findings.append(finding)

        log.info("meu_scanner.complete", findings=len(findings))
        return findings

    def _parse_issue(self, issue: dict) -> ToolFinding | None:
        """Converte um issue da ferramenta para ToolFinding."""
        selector = issue.get("selector", "")
        message = issue.get("message", "")
        if not selector or not message:
            return None

        return ToolFinding(
            tool=self.tool_name,
            selector=selector,
            message=message,
            wcag_criteria=self._map_wcag(issue.get("rule_id", "")),
            rule_id=issue.get("rule_id"),
            impact=issue.get("severity"),
            help_url=issue.get("help_url"),
            html_snippet=issue.get("html"),
        )

    def _map_wcag(self, rule_id: str) -> str | None:
        """Mapeia rule IDs da ferramenta para critérios WCAG."""
        RULE_TO_WCAG = {
            "color-contrast-check": "1.4.3",
            "image-alt-check": "1.1.1",
            "label-check": "3.3.2",
            "aria-check": "4.1.2",
            # Adicione seus mapeamentos aqui
        }
        return RULE_TO_WCAG.get(rule_id)
```

### 2. Registrar no Orchestrator

Edite `a11y_autofix/scanner/orchestrator.py`:

```python
from .meu_scanner import MeuScanner

# No método _build_runners():
def _build_runners(self, settings: Settings) -> list[BaseScanner]:
    runners = []

    if settings.use_pa11y:
        runners.append(Pa11yRunner())
    if settings.use_axe:
        runners.append(AxeRunner())
    if settings.use_lighthouse:
        runners.append(LighthouseRunner())
    if settings.use_playwright:
        runners.append(PlaywrightAxeRunner())

    # Adicionar sua ferramenta:
    if settings.use_minha_ferramenta:
        runners.append(MeuScanner())

    return runners
```

### 3. Adicionar configuração em `config.py`

```python
class Settings(BaseSettings):
    # ... campos existentes ...

    # Adicionar:
    use_minha_ferramenta: bool = Field(
        default=False,
        description="Ativar Minha Ferramenta de scan"
    )
```

### 4. Adicionar ao `.env.example`

```env
# Ferramentas de scan
USE_PA11Y=true
USE_AXE=true
USE_LIGHTHOUSE=false
USE_PLAYWRIGHT=true
USE_MINHA_FERRAMENTA=false    # Adicionar aqui
```

---

## Registrar o Scanner

Edite `a11y_autofix/scanner/__init__.py` para exportar o novo scanner:

```python
from .meu_scanner import MeuScanner

__all__ = [
    ...,
    "MeuScanner",
]
```

---

## Testar o Scanner

### Teste Unitário

Crie `tests/unit/test_meu_scanner.py`:

```python
"""Testes para MeuScanner."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a11y_autofix.scanner.meu_scanner import MeuScanner


class TestMeuScanner:
    def test_tool_name(self):
        scanner = MeuScanner()
        assert scanner.tool_name == "minha-ferramenta"

    @patch("subprocess.run")
    async def test_check_available_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0\n")
        scanner = MeuScanner()
        assert await scanner.check_available() is True
        assert scanner.tool_version == "1.0.0"

    @patch("subprocess.run")
    async def test_check_available_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        scanner = MeuScanner()
        assert await scanner.check_available() is False

    @patch("subprocess.run")
    async def test_run_returns_findings(self, mock_run, tmp_path):
        output = {
            "issues": [
                {
                    "selector": ".btn",
                    "message": "Contraste insuficiente",
                    "rule_id": "color-contrast-check",
                    "severity": "critical",
                }
            ]
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(output),
        )

        html_file = tmp_path / "test.html"
        html_file.write_text("<html></html>")

        scanner = MeuScanner()
        findings = await scanner.run(html_file)

        assert len(findings) == 1
        assert findings[0].tool == "minha-ferramenta"
        assert findings[0].selector == ".btn"
        assert findings[0].wcag_criteria == "1.4.3"

    @patch("subprocess.run")
    async def test_run_handles_timeout(self, mock_run, tmp_path):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)

        html_file = tmp_path / "test.html"
        html_file.write_text("<html></html>")

        scanner = MeuScanner()
        findings = await scanner.run(html_file)
        assert findings == []
```

### Executar testes

```bash
pytest tests/unit/test_meu_scanner.py -v
```

### Teste Manual

```bash
# Verificar disponibilidade
a11y-autofix scanners list

# Testar em um arquivo
USE_MINHA_FERRAMENTA=true a11y-autofix fix src/components/Button.tsx --tools minha-ferramenta
```

---

## Boas Práticas

### 1. Tratamento de Erros

Sempre capture exceções e retorne lista vazia em caso de falha:

```python
async def run(self, html_path: Path) -> list[ToolFinding]:
    try:
        result = subprocess.run(...)
    except subprocess.TimeoutExpired:
        log.warning("scanner.timeout", ...)
        return []
    except FileNotFoundError:
        log.error("scanner.not_found")
        return []
    except Exception as e:
        log.error("scanner.unexpected_error", error=str(e))
        return []
```

### 2. Logging Estruturado

Use `structlog` com contexto:

```python
log.debug("scanner.start", file=str(html_path), tool=self.tool_name)
log.info("scanner.complete", findings=len(findings), duration=elapsed)
log.warning("scanner.partial_failure", reason="json_parse_error")
```

### 3. Mapeamentos WCAG

Mapeie rule IDs para critérios WCAG usando os critérios definidos em `config.py`:

```python
# Critérios WCAG comuns
WCAG_PERCEIVABLE = {
    "1.1.1",  # Texto alternativo
    "1.3.1",  # Informação e relações
    "1.4.1",  # Uso de cor
    "1.4.3",  # Contraste mínimo
    "1.4.11", # Contraste de componentes
}

WCAG_OPERABLE = {
    "2.1.1",  # Teclado
    "2.4.3",  # Ordem de foco
    "2.4.7",  # Foco visível
}

WCAG_UNDERSTANDABLE = {
    "3.3.1",  # Identificação de erro
    "3.3.2",  # Labels e instruções
}

WCAG_ROBUST = {
    "4.1.1",  # Parsing
    "4.1.2",  # Nome, função, valor
}
```

### 4. Seletores CSS

Prefira seletores estáveis e específicos:
- **Bom**: `#login-button`, `[data-testid="submit"]`, `form > button[type="submit"]`
- **Ruim**: `.css-1a2b3c`, `:nth-child(3)`, `div > div > div > span`

Se a ferramenta não fornecer seletores, tente extrair do HTML snippet:

```python
def _extract_selector(self, html: str) -> str:
    """Tenta extrair seletor do snippet HTML."""
    import re

    # Tentar ID
    id_match = re.search(r'id=["\']([^"\']+)["\']', html)
    if id_match:
        return f"#{id_match.group(1)}"

    # Tentar data-testid
    testid_match = re.search(r'data-testid=["\']([^"\']+)["\']', html)
    if testid_match:
        return f'[data-testid="{testid_match.group(1)}"]'

    # Fallback: tag + primeira classe
    tag_match = re.match(r'<(\w+)', html)
    class_match = re.search(r'class=["\']([^\s"\']+)', html)
    if tag_match and class_match:
        return f"{tag_match.group(1)}.{class_match.group(1)}"

    return "unknown"
```

### 5. Impacto (Impact)

Normalize os níveis de impacto para o padrão do sistema:

```python
IMPACT_NORMALIZE = {
    # Mapeamentos comuns
    "error": "critical",
    "warning": "serious",
    "notice": "moderate",
    "info": "minor",
    # axe/WCAG
    "critical": "critical",
    "serious": "serious",
    "moderate": "moderate",
    "minor": "minor",
    # outros
    "high": "critical",
    "medium": "serious",
    "low": "minor",
}

impact = IMPACT_NORMALIZE.get(raw_impact, "moderate")
```

---

## Exemplo Completo

Scanner para a ferramenta hipotética `html-a11y`:

```python
"""Scanner baseado em html-a11y CLI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from .base import BaseScanner, ToolFinding

log = structlog.get_logger(__name__)

_RULE_TO_WCAG = {
    "contrast": "1.4.3",
    "alt-text": "1.1.1",
    "label": "3.3.2",
    "aria-name": "4.1.2",
    "landmark": "1.3.1",
    "keyboard-access": "2.1.1",
}

_IMPACT_MAP = {
    "error": "critical",
    "warning": "serious",
    "notice": "moderate",
}


class HtmlA11yScanner(BaseScanner):
    """Runner para html-a11y CLI."""

    tool_name = "html-a11y"

    async def check_available(self) -> bool:
        try:
            result = subprocess.run(
                ["npx", "html-a11y", "--version"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                self.tool_version = result.stdout.strip()
                return True
            return False
        except FileNotFoundError:
            return False

    async def run(self, html_path: Path) -> list[ToolFinding]:
        try:
            result = subprocess.run(
                ["npx", "html-a11y", "--json", str(html_path)],
                capture_output=True, text=True,
                timeout=60, check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("html_a11y.timeout", file=str(html_path))
            return []
        except FileNotFoundError:
            return []

        if not result.stdout.strip():
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        findings = []
        for violation in data.get("violations", []):
            for node in violation.get("nodes", []):
                finding = ToolFinding(
                    tool=self.tool_name,
                    selector=node.get("selector", "unknown"),
                    message=violation.get("description", ""),
                    wcag_criteria=_RULE_TO_WCAG.get(violation.get("id", "")),
                    rule_id=violation.get("id"),
                    impact=_IMPACT_MAP.get(violation.get("impact", ""), "moderate"),
                    help_url=violation.get("helpUrl"),
                    html_snippet=node.get("html"),
                )
                findings.append(finding)

        log.info("html_a11y.complete", file=str(html_path), findings=len(findings))
        return findings
```
