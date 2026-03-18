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
import sys
import threading
from pathlib import Path


class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Handler silencioso — suprime logs e responde favicon.ico sem erro."""

    def log_message(self, format: str, *args: object) -> None:
        """Suprime logs de acesso HTTP."""
        pass

    def log_error(self, format: str, *args: object) -> None:
        """Suprime logs de erro HTTP."""
        pass

    def do_GET(self) -> None:
        """Intercepta favicon.ico (requisitado automaticamente por browsers/Chromium).

        Sem este handler, o servidor retornaria 404 → tentaria escrever o body
        de erro → conexão já fechada pelo cliente → BrokenPipeError no stderr.
        Responder 204 No Content encerra a troca de forma limpa.
        """
        if self.path in ("/favicon.ico", "/favicon.png"):
            self.send_response(204)  # No Content — sem body, sem erro
            self.end_headers()
            return
        super().do_GET()


class _QuietHTTPServer(http.server.HTTPServer):
    """HTTPServer que silencia erros de conexão benignos.

    BrokenPipeError e ConnectionResetError ocorrem quando o cliente (pa11y,
    Chromium) fecha a conexão antes de receber a resposta — comportamento
    normal de browsers e ferramentas de scan. O handle_error() padrão do
    Python imprime o traceback completo no stderr via traceback.print_exc(),
    bypass­ando o log_error() do handler. Sobrescrevemos aqui para silenciá-los.
    """

    def handle_error(self, request: object, client_address: object) -> None:
        exc_type, _, _ = sys.exc_info()
        # Erros de pipe/conexão são benignos: o cliente simplesmente fechou
        # a conexão antes de receber a resposta (comportamento normal de browsers)
        if exc_type in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        # Demais erros: delegar ao handler padrão (preserva rastreabilidade)
        super().handle_error(request, client_address)


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
        self._server = _QuietHTTPServer(("127.0.0.1", 0), handler)
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
