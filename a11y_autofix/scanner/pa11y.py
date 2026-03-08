"""Runner para Pa11y — ferramenta de acessibilidade baseada em Node.js."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# Candidatos de comando para pa11y — tenta cada um em ordem até encontrar um que funcione.
# Isso resolve o problema de pa11y instalado mas não no PATH do subprocess Python
# (comum ao rodar dentro de .venv ou com PATH customizado pelo npm).
_PA11Y_CANDIDATES = [
    ["pa11y"],                    # Instalado no PATH padrão
    ["npx", "pa11y"],             # Via npx (encontra globals npm sem precisar do PATH)
    ["npx", "--yes", "pa11y"],    # Via npx com download automático se ausente
]


async def _find_pa11y_cmd() -> list[str] | None:
    """
    Descobre qual comando pa11y funciona no ambiente atual.

    Testa candidatos em ordem e retorna o primeiro que responde com sucesso.
    Resolve o problema de pa11y instalado mas fora do PATH do subprocess Python
    (frequente dentro de .venv ou quando npm usa prefix customizado).

    Returns:
        Lista de tokens de comando (ex: ['npx', 'pa11y']) ou None se nenhum funcionar.
    """
    for cmd in _PA11Y_CANDIDATES:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                log.debug("pa11y_cmd_found", cmd=cmd)
                return cmd
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            continue
    return None


class Pa11yRunner(BaseRunner):
    """
    Runner para Pa11y (https://pa11y.org/).

    Executa pa11y via subprocess com saída JSON e mapeia os resultados
    para o formato interno ToolFinding.

    Pa11y retorna código 2 quando há issues (comportamento esperado, não erro).

    Estratégia de resolução de comando:
        Tenta ['pa11y'], depois ['npx', 'pa11y'], depois ['npx', '--yes', 'pa11y'].
        Isso garante que funcione mesmo dentro de .venv onde o PATH não herda
        o npm prefix customizado do usuário.
    """

    tool = ScanTool.PA11Y

    def __init__(self) -> None:
        # Cache do comando resolvido para evitar redescoberta a cada scan
        self._cmd: list[str] | None = None
        self._cmd_resolved = False

    async def _get_cmd(self) -> list[str] | None:
        """Retorna o comando pa11y resolvido (com cache)."""
        if not self._cmd_resolved:
            self._cmd = await _find_pa11y_cmd()
            self._cmd_resolved = True
            if self._cmd is None:
                log.warning(
                    "pa11y_not_found",
                    candidates=[c[0] for c in _PA11Y_CANDIDATES],
                    hint="Execute: npm install -g pa11y",
                )
        return self._cmd

    async def available(self) -> bool:
        """Verifica se pa11y está disponível (via PATH ou npx)."""
        return (await self._get_cmd()) is not None

    async def version(self) -> str:
        """Retorna versão do pa11y."""
        cmd = await self._get_cmd()
        if cmd is None:
            return "unknown"
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                return stdout.decode().strip()
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            pass
        return "unknown"

    async def run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa pa11y no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (ex: 'WCAG2AA').

        Returns:
            Lista de ToolFinding.
        """
        cmd = await self._get_cmd()
        if cmd is None:
            raise RuntimeError(
                "pa11y não encontrado. Execute: npm install -g pa11y"
            )

        version = await self.version()
        url = f"file://{harness_path.resolve()}"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            "--reporter", "json",
            "--standard", wcag,
            "--timeout", "30000",
            "--wait", "2000",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Pa11y timeout after 60s")

        output = stdout.decode(errors="replace")

        # pa11y retorna código 2 quando há issues (não é erro)
        if proc.returncode not in (0, 2):
            log.warning(
                "pa11y_non_zero_exit",
                code=proc.returncode,
                stderr=stderr.decode(errors="replace")[:200],
            )
            return []

        if not output.strip():
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            log.warning("pa11y_json_parse_error", output=output[:200])
            return []

        findings = []
        items = data if isinstance(data, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            finding = ToolFinding(
                tool=self.tool,
                tool_version=version,
                rule_id=item.get("code", "unknown"),
                wcag_criteria=self._extract_wcag(item.get("code", "")),
                message=item.get("message", ""),
                selector=item.get("selector", ""),
                context=item.get("context", ""),
                impact=self._map_type_to_impact(item.get("type", "error")),
                help_url=item.get("helpUrl", ""),
            )
            findings.append(finding)

        log.debug("pa11y_findings", count=len(findings))
        return findings

    def _extract_wcag(self, code: str) -> str | None:
        """
        Extrai critério WCAG do código pa11y.

        Ex: 'WCAG2AA.Principle1.Guideline1_4.1_4_3.G18' → '1.4.3'
        """
        match = re.search(r'(\d+_\d+_\d+)', code)
        if match:
            return match.group(1).replace("_", ".")
        match2 = re.search(r'(\d+_\d+)(?!\d)', code)
        if match2:
            return match2.group(1).replace("_", ".")
        return None

    def _map_type_to_impact(self, pa11y_type: str) -> str:
        """Mapeia tipo pa11y para impacto axe-core."""
        mapping = {
            "error": "serious",
            "warning": "moderate",
            "notice": "minor",
        }
        return mapping.get(pa11y_type, "moderate")
