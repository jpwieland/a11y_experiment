"""Runner para Pa11y — ferramenta de acessibilidade baseada em Node.js.

Estratégia de robustez:
- Tenta pa11y, npx pa11y, npx --yes pa11y em ordem até encontrar um que funcione
- Timeout aumentado para 60s (CDN pode ser lento)
- Detecção automática de versão major: pa11y 6.x usa --wait/--chromium-flags;
  pa11y 7+ removeu essas flags (migração de puppeteer → playwright interno)
- Retry com flags mínimas se o primeiro conjunto falhar
- Usa URL HTTP local quando fornecida pelo orquestrador (evita file://)
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import subprocess
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding
from a11y_autofix.scanner.base import BaseRunner

log = structlog.get_logger(__name__)


def _npm_global_bin() -> Path | None:
    """Retorna o diretório de binários globais do npm (com suporte a Windows)."""
    try:
        # No Windows, subprocess precisa de shell=True para resolver .cmd
        result = subprocess.run(
            "npm root -g",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        npm_root = result.stdout.strip()
        if npm_root:
            # npm_root = .../node_modules  → bin está um nível acima
            return Path(npm_root).parent
    except Exception:
        pass
    return None


def _build_pa11y_candidates() -> list[list[str]]:
    """Constrói lista de candidatos de comando pa11y com suporte a Windows."""
    candidates: list[list[str]] = []

    if platform.system() == "Windows":
        # No Windows: .cmd não é resolvido pelo CreateProcess sem shell=True.
        # Usar 'cmd /c' garante que o shell do Windows resolve pa11y.cmd / npx.cmd.
        candidates += [
            ["cmd", "/c", "pa11y"],
            ["cmd", "/c", "npx", "pa11y"],
        ]
        # Tentar caminho absoluto via npm root
        bin_dir = _npm_global_bin()
        if bin_dir:
            pa11y_cmd = bin_dir / "pa11y.cmd"
            if pa11y_cmd.exists():
                candidates.insert(0, [str(pa11y_cmd)])

    # Candidatos universais (funcionam no Linux/macOS e Windows com PATH correto)
    candidates += [
        ["pa11y"],
        ["npx", "pa11y"],
        ["npx", "--yes", "pa11y"],    # download automático se ausente
    ]
    return candidates


# Candidatos de comando para pa11y — testa cada um em ordem até encontrar um que funcione.
# Resolve o problema de pa11y instalado mas não no PATH do subprocess Python
# (comum ao rodar dentro de .venv ou com PATH customizado pelo npm).
_PA11Y_CANDIDATES = _build_pa11y_candidates()

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

        # Detectar versão major para selecionar flags compatíveis.
        # pa11y 6.x: suporta --wait e --chromium-flags (puppeteer API exposta).
        # pa11y 7+: flags de chromium removidas; migração para playwright interno.
        pa11y_major = 0
        try:
            pa11y_major = int(version.split(".")[0])
        except (ValueError, IndexError):
            pass

        if pa11y_major > 0 and pa11y_major <= 6:
            # pa11y 6.x: usa flags de chromium e --wait
            pa11y_cmd = [
                *cmd,
                "--reporter", "json",
                "--standard", wcag,
                "--timeout", "60000",
                "--wait", "500",
                "--include-warnings",
                "--chromium-flags", _CHROMIUM_FLAGS,
                url,
            ]
        else:
            # pa11y 7+ (incluindo 9.x): flags mínimas e seguras
            pa11y_cmd = [
                *cmd,
                "--reporter", "json",
                "--standard", wcag,
                "--timeout", "60000",
                "--include-warnings",
                url,
            ]

        log.debug("pa11y_cmd", major=pa11y_major, cmd=pa11y_cmd[:4])

        async def _run_pa11y(pa_cmd: list[str]) -> tuple[str, str, int]:
            """Executa pa11y e retorna (stdout, stderr, returncode)."""
            proc = await asyncio.create_subprocess_exec(
                *pa_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=_PROCESS_TIMEOUT_S
                )
                return out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode or 0
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                log.warning("pa11y_process_timeout", timeout_s=_PROCESS_TIMEOUT_S)
                return "", "", -1

        output, stderr_text, returncode = await _run_pa11y(pa11y_cmd)

        # Retry com flags absolutamente mínimas se o primeiro conjunto falhou.
        # Critério: returncode inválido, saída vazia, ou JSON inválido.
        if returncode == -1 or (
            returncode not in (0, 2)
            or not output.strip()
            or not output.strip().startswith("[")
        ):
            minimal_cmd = [*cmd, "--reporter", "json", url]
            log.debug("pa11y_retry_minimal_flags")
            output2, stderr_text2, returncode2 = await _run_pa11y(minimal_cmd)
            if output2.strip() and output2.strip().startswith("["):
                output, stderr_text, returncode = output2, stderr_text2, returncode2
                log.debug("pa11y_retry_succeeded")

        # pa11y retorna código 2 quando há issues (não é erro de execução)
        if returncode == -1:
            return []

        if returncode not in (0, 2):
            log.warning(
                "pa11y_non_zero_exit",
                code=returncode,
                stderr=stderr_text[:300],
            )
            return []

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
