"""Runner para Pa11y — ferramenta de acessibilidade baseada em Node.js.

Estratégia de robustez:
- Tenta pa11y, npx pa11y, npx --yes pa11y em ordem até encontrar um que funcione
- Timeout aumentado para 60s (CDN pode ser lento)
- --wait reduzido para 500ms (Babel renderiza síncronamente)
- --chromium-flags para permitir CDN em contexto file:// quando necessário
- Usa URL HTTP local quando fornecida pelo orquestrador (evita file://)
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)

# Candidatos de comando para pa11y — testa cada um em ordem até encontrar um que funcione.
# Resolve o problema de pa11y instalado mas não no PATH do subprocess Python
# (comum ao rodar dentro de .venv ou com PATH customizado pelo npm).
_PA11Y_CANDIDATES = [
    ["pa11y"],                    # Instalado no PATH padrão
    ["npx", "pa11y"],             # Via npx (encontra globals npm sem precisar do PATH)
    ["npx", "--yes", "pa11y"],    # Via npx com download automático se ausente
]

# Flags Chromium para melhor compatibilidade com file:// e CDN externo
_CHROMIUM_FLAGS = " ".join([
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-web-security",
    "--allow-file-access-from-files",
])

# Timeout total para o processo Pa11y (asyncio.wait_for)
_PROCESS_TIMEOUT_S = 90


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
            await asyncio.wait_for(proc.communicate(), timeout=20)
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

    async def run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
    ) -> list[ToolFinding]:
        """
        Executa pa11y no harness HTML.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG (ex: 'WCAG2AA').
            harness_url: URL HTTP para acessar o harness (preferido).
                         Evita timeouts de CDN em contexto file://.

        Returns:
            Lista de ToolFinding.
        """
        cmd = await self._get_cmd()
        if cmd is None:
            raise RuntimeError(
                "pa11y não encontrado. Execute: npm install -g pa11y"
            )

        version = await self.version()

        # Preferir URL HTTP local; fallback para file://
        url = harness_url or f"file://{harness_path.resolve()}"
        log.debug("pa11y_scanning", url=url[:80])

        # Construir comando pa11y completo
        pa11y_cmd = [
            *cmd,
            "--reporter", "json",
            "--standard", wcag,
            "--timeout", "60000",          # 60s timeout de navegação (CDN pode ser lento)
            "--wait", "500",               # 500ms após load (React/Babel renderizam sync via Babel)
            "--include-warnings",          # Incluir warnings além de errors
            "--chromium-flags", _CHROMIUM_FLAGS,
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *pa11y_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_PROCESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            log.warning("pa11y_process_timeout", timeout_s=_PROCESS_TIMEOUT_S)
            return []

        stderr_text = stderr.decode(errors="replace")
        output = stdout.decode(errors="replace")

        # pa11y retorna código 2 quando há issues (não é erro de execução)
        if proc.returncode not in (0, 2):
            log.warning(
                "pa11y_non_zero_exit",
                code=proc.returncode,
                stderr=stderr_text[:300],
            )
            return []

        # pa11y pode retornar stderr com avisos não-críticos mesmo com código 0/2
        if stderr_text.strip() and proc.returncode not in (0, 2):
            log.debug("pa11y_stderr", text=stderr_text[:200])

        if not output.strip():
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            log.warning("pa11y_json_parse_error", output=output[:300])
            return []

        findings: list[ToolFinding] = []
        items = data if isinstance(data, list) else []

        for item in items:
            if not isinstance(item, dict):
                continue

            pa11y_type = item.get("type", "error")
            # Só incluir errors e warnings (ignorar notices — muito noise)
            if pa11y_type == "notice":
                continue

            finding = ToolFinding(
                tool=self.tool,
                tool_version=version,
                rule_id=item.get("code", "unknown"),
                wcag_criteria=self._extract_wcag(item.get("code", "")),
                message=item.get("message", ""),
                selector=item.get("selector", ""),
                context=item.get("context", "")[:500],
                impact=self._map_type_to_impact(pa11y_type),
                help_url=item.get("helpUrl", ""),
            )
            findings.append(finding)

        log.debug("pa11y_findings", count=len(findings))
        return findings

    def _extract_wcag(self, code: str) -> str | None:
        """
        Extrai critério WCAG do código pa11y.

        Ex: 'WCAG2AA.Principle1.Guideline1_4.1_4_3.G18' → '1.4.3'
        Ex: 'WCAG2AA.Principle2.Guideline2_4.2_4_1' → '2.4.1'
        """
        # Padrão de 3 números: 1_4_3 → 1.4.3
        match = re.search(r"(\d+)_(\d+)_(\d+)", code)
        if match:
            return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
        # Padrão de 2 números: 2_4 → 2.4
        match2 = re.search(r"(\d+)_(\d+)(?!\d)", code)
        if match2:
            return f"{match2.group(1)}.{match2.group(2)}"
        return None

    def _map_type_to_impact(self, pa11y_type: str) -> str:
        """Mapeia tipo pa11y para nível de impacto axe-core."""
        return {
            "error": "serious",
            "warning": "moderate",
            "notice": "minor",
        }.get(pa11y_type, "moderate")
