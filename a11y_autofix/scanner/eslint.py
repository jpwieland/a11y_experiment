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
import platform
import subprocess
import tempfile
from pathlib import Path

import structlog


def _npx_cmd() -> list[str]:
    """Retorna o prefixo de comando para npx com suporte a Windows.

    No Windows, asyncio.create_subprocess_exec não resolve arquivos .cmd
    do PATH (CreateProcess não usa PATHEXT). Usar 'cmd /c npx' delega ao
    shell do Windows que resolve npx.cmd automaticamente.
    """
    if platform.system() == "Windows":
        return ["cmd", "/c", "npx"]
    return ["npx"]

from a11y_autofix.config import ScanTool, ToolFinding

log = structlog.get_logger(__name__)

# Regras jsx-a11y e seus mapeamentos WCAG + impacto.
#
# IMPORTANTE: inclua apenas regras que existem oficialmente no eslint-plugin-jsx-a11y.
# Lista de referência: https://github.com/jsx-eslint/eslint-plugin-jsx-a11y#supported-rules
#
# Regras removidas intencionalmente:
#   ❌ jsx-a11y/aria-hidden-body  — nunca foi uma regra oficial do plugin
#   ❌ jsx-a11y/button-has-type   — pertence ao plugin 'eslint-plugin-react', não jsx-a11y
#
# O ESLint 10 lança TypeError ao encontrar qualquer regra desconhecida,
# abortando o lint do arquivo inteiro antes de produzir qualquer saída.
_RULE_META: dict[str, dict] = {
    # ── Conteúdo não-textual ──────────────────────────────────────────────────
    "jsx-a11y/alt-text": {
        "wcag": "1.1.1",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content",
    },
    "jsx-a11y/img-redundant-alt": {
        "wcag": "1.1.1",
        "impact": "minor",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content",
    },
    # ── Semântica e estrutura ─────────────────────────────────────────────────
    "jsx-a11y/heading-has-content": {
        "wcag": "1.3.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
    "jsx-a11y/label-has-associated-control": {
        "wcag": "1.3.1",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
    "jsx-a11y/scope": {
        "wcag": "1.3.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
    },
    # ── Teclado e foco ────────────────────────────────────────────────────────
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
    "jsx-a11y/mouse-events-have-key-events": {
        "wcag": "2.1.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    "jsx-a11y/no-access-key": {
        "wcag": "2.1.1",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/keyboard",
    },
    # ── Distrações / animações ────────────────────────────────────────────────
    "jsx-a11y/no-distracting-elements": {
        "wcag": "2.2.2",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/pause-stop-hide",
    },
    # ── Ordem de foco / tabindex ──────────────────────────────────────────────
    "jsx-a11y/tabindex-no-positive": {
        "wcag": "2.4.3",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/focus-order",
    },
    "jsx-a11y/no-autofocus": {
        "wcag": "2.4.3",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/focus-order",
    },
    # ── Idioma ────────────────────────────────────────────────────────────────
    "jsx-a11y/html-has-lang": {
        "wcag": "3.1.1",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/language-of-page",
    },
    # ── ARIA ──────────────────────────────────────────────────────────────────
    "jsx-a11y/aria-props": {
        "wcag": "4.1.2",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/aria-proptypes": {
        "wcag": "4.1.2",
        "impact": "serious",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/aria-role": {
        "wcag": "4.1.2",
        "impact": "critical",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/aria-unsupported-elements": {
        "wcag": "4.1.2",
        "impact": "minor",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
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
    # ── Links e navegação ─────────────────────────────────────────────────────
    "jsx-a11y/anchor-is-valid": {
        "wcag": "4.1.2",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
    },
    "jsx-a11y/anchor-has-content": {
        "wcag": "4.1.2",
        "impact": "moderate",
        "help": "https://www.w3.org/WAI/WCAG21/Understanding/name-role-value",
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

    Estratégia de resiliência de versão:
        O config filtra dinamicamente as regras para incluir apenas as que
        existem na versão instalada do plugin. Isso evita TypeError no ESLint 10
        quando uma regra não existe no plugin (comportamento que abortava o lint
        inteiro antes de produzir qualquer saída JSON).
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
        '  process.stderr.write("[a11y] eslint-plugin-jsx-a11y nao encontrado: " + e.message + "\\n");\n'
        '  jsxA11y = { rules: {} };\n'
        '}\n'
        'try {\n'
        '  tsParser = require("@typescript-eslint/parser");\n'
        '} catch (e) {\n'
        '  tsParser = null;\n'
        '}\n'
        "\n"
        "// Filtra apenas regras que existem na versao instalada do plugin.\n"
        "// ESLint 10 lanca TypeError se qualquer regra declarada nao existir no plugin,\n"
        "// abortando o lint inteiro antes de gerar qualquer saida JSON.\n"
        "const availableRules = new Set(Object.keys(jsxA11y.rules || {}));\n"
        f"const allRules = {rules_json};\n"
        "const rules = Object.fromEntries(\n"
        "  Object.entries(allRules).filter(([key]) => {\n"
        "    const name = key.replace('jsx-a11y/', '');\n"
        "    const ok = availableRules.has(name);\n"
        "    if (!ok) process.stderr.write('[a11y] regra ignorada (nao existe no plugin): ' + key + '\\n');\n"
        "    return ok;\n"
        "  })\n"
        ");\n"
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
        "    rules,\n"
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
                    *_npx_cmd(), "eslint", "--version",
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
        """Retorna o caminho do npm root global (síncrono, com cache).

        Usa shell=True para garantir que npm.cmd seja resolvido no Windows,
        onde subprocess sem shell não resolve arquivos .cmd do PATH.
        """
        if self._npm_root_g is None:
            try:
                result = subprocess.run(
                    "npm root -g",          # string + shell=True → cmd.exe no Windows
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
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
        """Verifica se ESLint e jsx-a11y estão instalados.

        Estratégia multi-fallback (suporte a Windows e ambientes com PATH incompleto):
          1. npx eslint --version        (instalação global padrão)
          2. eslint --version            (binário no PATH diretamente)
          3. node_modules/.bin/eslint    (instalação local, caso exista)
        Retorna True se qualquer um responder com returncode 0.
        """
        is_windows = platform.system() == "Windows"

        candidates: list[list[str]] = []

        # No Windows, asyncio.create_subprocess_exec não resolve .cmd do PATH.
        # 'cmd /c' delega ao shell do Windows que conhece PATHEXT (.cmd, .bat, etc.)
        if is_windows:
            candidates += [
                ["cmd", "/c", "eslint", "--version"],
                ["cmd", "/c", "npx", "--no-install", "eslint", "--version"],
            ]

        candidates += [
            ["npx", "--no-install", "eslint", "--version"],
            ["eslint", "--version"],
        ]

        # Adicionar npm bin explícito via caminho absoluto (mais confiável)
        npm_root = self._get_npm_root_g_sync()
        if npm_root:
            # npm_root = .../node_modules  → binários globais ficam um nível acima
            # No Windows: eslint.cmd ; no Unix: eslint (sem extensão)
            npm_bin_dir = os.path.dirname(npm_root)  # .../npm  (Windows) ou /usr/local/bin equiv
            eslint_bin = os.path.join(
                npm_bin_dir,
                "eslint.cmd" if is_windows else "eslint",
            )
            if os.path.exists(eslint_bin):
                candidates.insert(0, [eslint_bin, "--version"])

        for cmd in candidates:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
                if proc.returncode == 0:
                    ver = stdout.decode().strip()
                    log.debug("eslint_available", cmd=cmd[0], version=ver)
                    return True
                # ESLint instalado mas incompatível com Node (returncode != 0)
                err = (stdout.decode() + stderr.decode()).lower()
                if "unsupported engine" in err or "node" in err:
                    log.warning(
                        "eslint_node_incompatible",
                        cmd=cmd[0],
                        hint="Atualize Node.js (≥18) ou instale eslint@8",
                    )
            except (FileNotFoundError, asyncio.TimeoutError, OSError):
                continue
        return False

    async def version(self) -> str:
        """Retorna versão do ESLint."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *_npx_cmd(), "eslint", "--version",
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
            *_npx_cmd(), "--yes", "eslint",
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
            *_npx_cmd(), "--yes", "eslint",
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

                # ── Filtro de escopo ────────────────────────────────────────
                # O ESLint pode capturar regras do projeto (TypeScript, React,
                # import, etc.) mesmo com flat config. Mantemos APENAS regras
                # jsx-a11y — as únicas relevantes para acessibilidade.
                if not rule_id.startswith("jsx-a11y/"):
                    log.debug("eslint_non_a11y_rule_skipped", rule_id=rule_id)
                    continue

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
