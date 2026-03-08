"""Runner ESLint jsx-a11y — análise estática de acessibilidade em JSX/TSX.

Diferente dos outros runners (pa11y, axe, playwright), este runner NÃO usa harness HTML.
Analisa o AST do JSX/TSX diretamente via ESLint, sem precisar renderizar o componente.

Isso resolve o problema fundamental do harness: componentes React não renderizam
corretamente quando seus imports são removidos e dependências são mockadas.

Regras cobertas (jsx-a11y):
  - alt-text              → img, area, input[type=image] sem alt
  - aria-props            → propriedades aria-* inválidas
  - aria-role             → role= com valores inválidos
  - aria-hidden-body      → aria-hidden no body
  - click-events-have-key-events → onClick sem onKeyDown/onKeyUp/onKeyPress
  - interactive-supports-focus  → elementos interativos sem foco
  - label                 → inputs sem labels associados
  - no-autofocus          → autoFocus em elementos
  - no-distracting-elements → <marquee>, <blink>
  - tabindex              → tabIndex > 0 (problema de ordem de foco)
  - anchor-is-valid       → <a> sem href ou role válido
  - button-has-type       → <button> sem type=
  - heading-has-content   → headings vazios
  - html-has-lang         → <html> sem lang
  - img-redundant-alt     → alt com "image" ou "photo"
  - interactive-supports-focus → elementos interativos focáveis
  - no-access-key         → accessKey perigosos

Mapeamento WCAG dos resultados:
  - alt-text              → 1.1.1 (Non-text Content)
  - label                 → 1.3.1, 4.1.2
  - color-contrast        → 1.4.3 (requer runtime, não coberto aqui)
  - keyboard              → 2.1.1
  - focus-order           → 2.4.3
  - language              → 3.1.1
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding

log = structlog.get_logger(__name__)

# Regras jsx-a11y e seus mapeamentos WCAG + impacto
_RULE_META: dict[str, dict] = {
    "jsx-a11y/alt-text": {
        "wcag": "1.1.1",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content",
    },
    "jsx-a11y/aria-props": {
        "wcag": "4.1.2",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/aria-role": {
        "wcag": "4.1.2",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/aria-hidden-body": {
        "wcag": "4.1.2",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/click-events-have-key-events": {
        "wcag": "2.1.1",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    "jsx-a11y/interactive-supports-focus": {
        "wcag": "2.1.1",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    "jsx-a11y/label-has-associated-control": {
        "wcag": "1.3.1",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
    "jsx-a11y/no-autofocus": {
        "wcag": "2.4.3",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/focus-order",
    },
    "jsx-a11y/no-distracting-elements": {
        "wcag": "2.2.2",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/pause-stop-hide",
    },
    "jsx-a11y/tabindex-no-positive": {
        "wcag": "2.4.3",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/focus-order",
    },
    "jsx-a11y/anchor-is-valid": {
        "wcag": "4.1.2",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/button-has-type": {
        "wcag": "4.1.2",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/heading-has-content": {
        "wcag": "1.3.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
    "jsx-a11y/html-has-lang": {
        "wcag": "3.1.1",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/language-of-page",
    },
    "jsx-a11y/img-redundant-alt": {
        "wcag": "1.1.1",
        "impact": "minor",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content",
    },
    "jsx-a11y/no-access-key": {
        "wcag": "2.1.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    "jsx-a11y/mouse-events-have-key-events": {
        "wcag": "2.1.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    "jsx-a11y/role-has-required-aria-props": {
        "wcag": "4.1.2",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/role-supports-aria-props": {
        "wcag": "4.1.2",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/scope": {
        "wcag": "1.3.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
}

_ESLINT_CONFIG = {
    "root": True,
    "parser": "@typescript-eslint/parser",
    "parserOptions": {
        "ecmaVersion": 2022,
        "ecmaFeatures": {"jsx": True},
        "sourceType": "module",
    },
    "plugins": ["jsx-a11y"],
    "rules": {rule: "error" for rule in _RULE_META},
}


class EslintRunner:
    """
    Runner ESLint jsx-a11y.

    Análise estática de acessibilidade diretamente no código-fonte JSX/TSX.
    Não depende de harness HTML, Chrome, ou rendering.

    Interface levemente diferente dos outros runners: recebe source_file (o arquivo
    .tsx original) em vez de harness_path. O orchestrator deve detectar isso e
    passar o arquivo correto.
    """

    tool = ScanTool.ESLINT

    async def available(self) -> bool:
        """Verifica se ESLint e jsx-a11y estão instalados."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--yes", "eslint", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    async def version(self) -> str:
        """Retorna versão do ESLint."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "eslint", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode == 0:
                return stdout.decode().strip()
        except (FileNotFoundError, asyncio.TimeoutError):
            pass
        return "unknown"

    async def run_on_source(self, source_file: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa ESLint jsx-a11y diretamente no arquivo fonte TSX/JSX.

        Args:
            source_file: Caminho do arquivo .tsx/.jsx original.
            wcag: Nível WCAG (não usado diretamente, regras são fixas).

        Returns:
            Lista de ToolFinding com os problemas encontrados.
        """
        version = await self.version()

        # Criar config temporário no diretório do arquivo
        # (garante que o parser TypeScript funcione corretamente)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".eslintrc.json",
            dir=source_file.parent,
            delete=False,
            prefix=".tmp_a11y_",
        ) as cfg_file:
            json.dump(_ESLINT_CONFIG, cfg_file)
            cfg_path = Path(cfg_file.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "--yes", "eslint",
                "--format", "json",
                "--no-eslintrc",
                "--config", str(cfg_path),
                "--ext", ".tsx,.jsx,.ts,.js",
                str(source_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "FORCE_COLOR": "0"},
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30
                )
            except asyncio.TimeoutError:
                proc.kill()
                log.warning("eslint_timeout", file=str(source_file))
                return []

            output = stdout.decode(errors="replace")
            if not output.strip():
                return []

            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                log.warning(
                    "eslint_json_parse_error",
                    output=output[:300],
                    stderr=stderr.decode(errors="replace")[:200],
                )
                return []

            return self._parse_results(data, version)

        finally:
            cfg_path.unlink(missing_ok=True)

    # Mantém compatibilidade com interface BaseRunner (harness não é usado)
    async def run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """Stub: ESLint não usa harness. Chame run_on_source() diretamente."""
        return []

    async def safe_run_on_source(self, source_file: Path, wcag: str) -> list[ToolFinding]:
        """run_on_source() com tratamento de erros."""
        try:
            return await self.run_on_source(source_file, wcag)
        except Exception as e:
            log.warning(
                "eslint_safe_run_failed",
                file=str(source_file),
                error=str(e),
            )
            return []

    def _parse_results(self, data: list, version: str) -> list[ToolFinding]:
        """Converte saída JSON do ESLint em ToolFindings."""
        findings: list[ToolFinding] = []

        for file_result in data:
            if not isinstance(file_result, dict):
                continue

            messages = file_result.get("messages", [])
            file_path = file_result.get("filePath", "")

            for msg in messages:
                if not isinstance(msg, dict):
                    continue

                rule_id = msg.get("ruleId") or "unknown"
                meta = _RULE_META.get(rule_id, {})

                line = msg.get("line", 1)
                col = msg.get("column", 1)
                selector = f"{Path(file_path).name}:{line}:{col}"

                severity = msg.get("severity", 1)
                impact = meta.get("impact", "moderate")
                if severity == 2 and impact == "minor":
                    impact = "moderate"

                finding = ToolFinding(
                    tool=self.tool,
                    tool_version=version,
                    rule_id=rule_id,
                    wcag_criteria=meta.get("wcag", ""),
                    message=msg.get("message", ""),
                    selector=selector,
                    context=msg.get("source", "") or "",
                    impact=impact,
                    help_url=meta.get("help", ""),
                )
                findings.append(finding)

        log.debug("eslint_findings", count=len(findings))
        return findings
