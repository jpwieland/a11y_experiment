"""Interface base para runners de ferramentas de acessibilidade."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import structlog

from a11y_autofix.config import ScanTool, ToolFinding

log = structlog.get_logger(__name__)


class BaseRunner(ABC):
    """
    Interface abstrata para runners de ferramentas de acessibilidade.

    Cada ferramenta (pa11y, axe, lighthouse, playwright, eslint) implementa
    esta interface, garantindo que o orquestrador possa trocá-las de forma
    transparente.

    Subclasses devem definir o atributo de classe `tool: ScanTool`.

    Parâmetro harness_url:
        Quando fornecido em safe_run/run, o runner usa esta URL diretamente
        em vez de derivar uma URL file:// do harness_path. Isso permite
        servir o harness via HTTP local (HarnessServer) evitando timeouts
        de CDN em contexto file://.
    """

    tool: ScanTool  # Definido na subclasse

    @abstractmethod
    async def available(self) -> bool:
        """
        Verifica se a ferramenta está instalada e disponível.

        Returns:
            True se a ferramenta pode ser executada.
        """
        ...

    @abstractmethod
    async def version(self) -> str:
        """
        Retorna a versão da ferramenta.

        Returns:
            String de versão, ex: '8.0.0'.
        """
        ...

    @abstractmethod
    async def run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
    ) -> list[ToolFinding]:
        """
        Executa scan no harness HTML e retorna findings crus.

        Args:
            harness_path: Caminho do arquivo HTML harness temporário.
            wcag: Nível WCAG, ex: 'WCAG2AA'.
            harness_url: URL opcional para acessar o harness via HTTP local.
                         Se fornecida, usa esta URL em vez de file://harness_path.
                         Recomendado para evitar timeouts de CDN em file:// context.

        Returns:
            Lista de ToolFinding com os problemas encontrados.
        """
        ...

    async def safe_run(
        self,
        harness_path: Path,
        wcag: str,
        harness_url: str | None = None,
    ) -> list[ToolFinding]:
        """
        Executa scan com tratamento de erros completo.

        Wraps run() capturando todas as exceções e retornando lista vazia
        em caso de falha, garantindo que o orquestrador continue funcionando
        mesmo quando ferramentas individuais falham.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG.
            harness_url: URL HTTP opcional para servir o harness.

        Returns:
            Lista de findings ou lista vazia se houve erro.
        """
        try:
            return await self.run(harness_path, wcag, harness_url)
        except Exception as e:
            log.warning(
                "runner_safe_run_failed",
                tool=self.tool.value,
                error=str(e)[:300],
            )
            return []
