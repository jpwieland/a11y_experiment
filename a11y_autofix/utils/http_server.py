"""Servidor HTTP local para servir arquivos de harness HTML.

Por que usar HTTP em vez de file://?
- Scripts CDN externos (React, Babel) carregam normalmente via http://localhost
- Chromium aplica políticas de segurança mais restritivas em file:// pages
- Evita timeouts causados por CDN lento ou indisponível em contexto file://
- Pa11y e Playwright funcionam melhor com http:// (mesmo fluxo de produção)
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path


class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Handler silencioso — suprime todos os logs de acesso ao console."""

    def log_message(self, format: str, *args: object) -> None:
        """Suprime logs de acesso HTTP."""
        pass

    def log_error(self, format: str, *args: object) -> None:
        """Suprime logs de erro HTTP."""
        pass


class HarnessServer:
    """
    Servidor HTTP local para servir harness HTML via localhost.

    Serve arquivos de um diretório via http://127.0.0.1:PORT/, resolvendo
    os problemas de timeout quando o harness carrega CDN via file://.

    Uso como context manager:
        with HarnessServer(directory) as server:
            url = server.url_for("harness.html")
            # Usar url com pa11y, playwright, axe, etc.

    Uso manual:
        server = HarnessServer(directory)
        server.start()
        url = server.url_for("harness.html")
        # ...
        server.stop()
    """

    def __init__(self, directory: Path) -> None:
        """
        Inicializa o servidor no diretório especificado.

        Args:
            directory: Diretório raiz para servir os arquivos.
        """
        handler = functools.partial(
            _QuietHTTPHandler,
            directory=str(directory),
        )
        # Porta 0 → sistema operacional escolhe porta livre automaticamente
        self._server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self._port: int = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,  # Thread daemon: encerra junto com o processo principal
        )

    def start(self) -> None:
        """Inicia o servidor em background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Para o servidor e libera a porta."""
        self._server.shutdown()

    @property
    def port(self) -> int:
        """Porta em que o servidor está escutando."""
        return self._port

    def url_for(self, filename: str) -> str:
        """
        Retorna a URL http://localhost para um arquivo no diretório.

        Args:
            filename: Nome do arquivo (ex: 'harness.html').

        Returns:
            URL completa (ex: 'http://127.0.0.1:54321/harness.html').
        """
        return f"http://127.0.0.1:{self._port}/{filename}"

    def __enter__(self) -> "HarnessServer":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
