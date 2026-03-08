"""Runner ESLint jsx-a11y — análise estática de acessibilidade em JSX/TSX.

Diferente dos outros runners (pa11y, axe, playwright), este runner NÃO usa harness HTML.
Analisa o AST do JSX/TSX diretamente via ESLint, sem precisar renderizar o componente.

Compatibilidade:
  - ESLint 8.x: usa formato legado (.eslintrc.json + --no-eslintrc)
  - ESLint 9.x/10.x+: usa flat config (.cjs + NODE_PATH para resolve global plugins)

Por que flat config no ESLint 9+:
  O ESLint 9 removeu o formato .eslintrc por padrão e o ESLint 10 removeu completamente
  o suporte legado. O flat config (.cjs) com NODE_PATH=$(npm root -g) resolve os plugins
  instalados globalmente sem precisar que eles estejam no projeto escaneado.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
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

# Configuração legada (.eslintrc.json) — ESLint 8
_LEGACY_ESLINT_CONFIG = {
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


def _build_flat_config_cjs(rules: dict[str, str]) -> str:
    """
    Gera conteúdo do flat config ESLint (.cjs) para ESLint 9+/10.

    Usa require() para carregar plugins — funciona com NODE_PATH apontando
    para o npm root global, sem precisar instalar plugins no projeto escaneado.
    """
    rules_json = json.dumps(rules, indent=4)
    return (
        '"use strict";\n'
        "/* Generated by a11y-autofix — ESLint flat config */\n"
        "\n"
        "// Carrega plugins globais (requer NODE_PATH=$(npm root -g))\n"
        'let jsxA11y, tsParser;\n'
        'try {\n'
        '  jsxA11y = require("eslint-plugin-jsx-a11y");\n'
        '} catch (e) {\n'
        '  console.error("[a11y] eslint-plugin-jsx-a11y não encontrado:", e.message);\n'
        '  jsxA11y = { rules: {}, flatConfigs: {} };\n'
        '}\n'
        'try {\n'
        '  tsParser = require("@typescript-eslint/parser");\n'
        '} catch (e) {\n'
        '  console.error("[a11y] @typescript-eslint/parser não encontrado:", e.message);\n'
        '  tsParser = null;\n'
        '}\n'
        "\n"
        "const langOptions = {\n"
        "  parserOptions: {\n"
        "    ecmaVersion: 2022,\n"
        "    ecmaFeatures: { jsx: true },\n"
        "    sourceType: 'module',\n"
        "  },\n"
        "};\n"
        "if (tsParser) langOptions.parser = tsParser;\n"
        "\n"
        "module.exports = [\n"
        "  {\n"
        '    files: ["**/*.tsx", "**/*.jsx", "**/*.ts", "**/*.js"],\n'
        "    plugins: { 'jsx-a11y': jsxA11y },\n"
        "    languageOptions: langOptions,\n"
        f"    rules: {rules_json},\n"
        "  },\n"
        "];\n"
    )


class EslintRunner:
    """
    Runner ESLint jsx-a11y.

    Análise estática de acessibilidade diretamente no código-fonte JSX/TSX.
    Não depende de harness HTML, Chrome, ou rendering.

    Compatibilidade de versão:
        - ESLint 8.x: formato legado (.eslintrc.json + --no-eslintrc + --ext)
        - ESLint 9.x/10.x: flat config (.cjs) + NODE_PATH=$(npm root -g)

    Interface: recebe source_file (o arquivo .tsx original) em vez de harness_path.
    """

    tool = ScanTool.ESLINT

    def __init__(self) -> None:
        # Cache de versão e npm root para evitar subprocess repetido
        self._eslint_major: int | None = None
        self._npm_root_g: str | None = None

    # ─── Detecção de ambiente ─────────────────────────────────────────────────

    async def _get_eslint_major(self) -> int:
        """Retorna major version do ESLint (com cache)."""
        if self._eslint_major is None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "npx", "eslint", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
                version_str = stdout.decode().strip().lstrip("v")  # "10.0.3"
                self._eslint_major = int(version_str.split(".")[0])
                log.debug("eslint_version_detected", major=self._eslint_major, full=version_str)
            except Exception as e:
                log.warning("eslint_version_detection_failed", error=str(e))
                self._eslint_major = 8  # Fallback conservador
        return self._eslint_major

    def _get_npm_root_g_sync(self) -> str:
        """Retorna o caminho do npm root global (síncrono, com cache)."""
        if self._npm_root_g is None:
            try:
                result = subprocess.run(
                    ["npm", "root", "-g"],
                    capture_output=True, text=True, timeout=10,
                )
                self._npm_root_g = result.stdout.strip()
            except Exception:
                self._npm_root_g = ""
        return self._npm_root_g

    # ─── Geração de configs temporárias ──────────────────────────────────────

    def _write_legacy_config(self, target_dir: Path) -> Path:
        """Escreve .eslintrc.json legado (ESLint 8)."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".eslintrc.json",
            dir=target_dir,
            delete=False,
            prefix=".tmp_a11y_",
        ) as cfg_file:
            json.dump(_LEGACY_ESLINT_CONFIG, cfg_file)
            return Path(cfg_file.name)

    def _write_flat_config(self, target_dir: Path) -> Path:
        """Escreve eslint.config.cjs flat config (ESLint 9+/10)."""
        rules = {rule: "error" for rule in _RULE_META}
        content = _build_flat_config_cjs(rules)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".cjs",
            dir=target_dir,
            delete=False,
            prefix="a11y_eslint_cfg_",
        ) as cfg_file:
            cfg_file.write(content)
            return Path(cfg_file.name)

    # ─── Interface pública ────────────────────────────────────────────────────

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

        Detecta a versão do ESLint e usa o formato de config adequado:
          - ESLint 8: legado (.eslintrc.json)
          - ESLint 9+/10: flat config (.cjs) com NODE_PATH para plugins globais

        Args:
            source_file: Caminho do arquivo .tsx/.jsx original.
            wcag: Nível WCAG (não usado diretamente; regras são fixas pelo plugin).

        Returns:
            Lista de ToolFinding com os problemas encontrados.
        """
        eslint_major = await self._get_eslint_major()
        version = await self.version()

        if eslint_major >= 9:
            findings = await self._run_flat_config(source_file, version)
        else:
            findings = await self._run_legacy_config(source_file, version)

        return findings

    # ─── Implementações por versão ────────────────────────────────────────────

    async def _run_flat_config(self, source_file: Path, version: str) -> list[ToolFinding]:
        """
        Executa com flat config para ESLint 9+/10.

        Estratégia de resolução de plugins:
          NODE_PATH=$(npm root -g) faz com que require() no config .cjs
          encontre os plugins instalados globalmente, sem precisar que
          o projeto escaneado tenha eslint-plugin-jsx-a11y em seus node_modules.
        """
        npm_root = self._get_npm_root_g_sync()
        cfg_path = self._write_flat_config(source_file.parent)

        cmd = [
            "npx", "--yes", "eslint",
            "--format", "json",
            "--config", str(cfg_path),
            str(source_file),
        ]

        env = {
            **os.environ,
            "FORCE_COLOR": "0",
            # NODE_PATH permite que o .cjs config encontre plugins globais
            "NODE_PATH": npm_root,
        }

        log.debug(
            "eslint_flat_config_run",
            file=source_file.name,
            eslint_major=self._eslint_major,
            npm_root=npm_root,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                log.warning("eslint_timeout", file=str(source_file))
                return []
        finally:
            cfg_path.unlink(missing_ok=True)

        output = stdout.decode(errors="replace")
        err_output = stderr.decode(errors="replace")

        # Logar stderr para debug (pode conter avisos de plugin não encontrado)
        if err_output.strip():
            log.debug(
                "eslint_stderr",
                file=source_file.name,
                stderr=err_output[:300],
            )

        return self._parse_output(output, err_output, source_file, version)

    async def _run_legacy_config(self, source_file: Path, version: str) -> list[ToolFinding]:
        """Executa com config legado (.eslintrc.json) para ESLint 8."""
        cfg_path = self._write_legacy_config(source_file.parent)

        cmd = [
            "npx", "--yes", "eslint",
            "--format", "json",
            "--no-eslintrc",
            "--config", str(cfg_path),
            "--ext", ".tsx,.jsx,.ts,.js",
            str(source_file),
        ]

        env = {**os.environ, "FORCE_COLOR": "0"}

        log.debug("eslint_legacy_config_run", file=source_file.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                log.warning("eslint_timeout", file=str(source_file))
                return []
        finally:
            cfg_path.unlink(missing_ok=True)

        output = stdout.decode(errors="replace")
        err_output = stderr.decode(errors="replace")

        return self._parse_output(output, err_output, source_file, version)

    def _parse_output(
        self,
        output: str,
        err_output: str,
        source_file: Path,
        version: str,
    ) -> list[ToolFinding]:
        """Converte saída JSON do ESLint em ToolFindings."""
        if not output.strip():
            if err_output.strip():
                log.warning(
                    "eslint_empty_stdout",
                    file=source_file.name,
                    stderr=err_output[:300],
                )
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            log.warning(
                "eslint_json_parse_error",
                output=output[:300],
                stderr=err_output[:200],
            )
            return []

        return self._parse_results(data, version)

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
