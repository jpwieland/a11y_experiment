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

    Cada ferramenta (pa11y, axe, lighthouse, playwright) implementa esta interface,
    garantindo que o orquestrador possa trocá-las de forma transparente.

    Subclasses devem definir o atributo de classe `tool: ScanTool`.
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
    async def run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa scan no harness HTML e retorna findings crus.

        Args:
            harness_path: Caminho do arquivo HTML harness temporário.
            wcag: Nível WCAG, ex: 'WCAG2AA'.

        Returns:
            Lista de ToolFinding com os problemas encontrados.
        """
        ...

    async def safe_run(self, harness_path: Path, wcag: str) -> list[ToolFinding]:
        """
        Executa scan com tratamento de erros.

        Wraps run() capturando exceções e retornando lista vazia em caso de falha.

        Args:
            harness_path: Caminho do arquivo HTML harness.
            wcag: Nível WCAG.

        Returns:
            Lista de findings ou lista vazia se houve erro.
        """
        try:
            return await self.run(harness_path, wcag)
        except Exception as e:
            log.warning(
                "runner_safe_run_failed",
                tool=self.tool.value,
                error=str(e),
            )
            return []
