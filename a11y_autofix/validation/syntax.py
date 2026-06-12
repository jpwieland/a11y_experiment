"""Parse sintático real de TSX/JSX via @babel/parser (Node.js).

Substitui a heurística de regex da Camada 1 por um parse de AST real
quando o Node + @babel/parser global estão disponíveis. Sem eles, o
chamador usa o fallback heurístico (graceful degradation, registrada
na metodologia como limitação de ambiente).

Por que @babel/parser e não tsc: o parser do Babel aceita TSX parcial
sem projeto/tsconfig, é ~100ms por arquivo, e é exatamente o mesmo
parser usado pelo harness de renderização — consistência entre o que
"compila" na validação e o que renderiza no scan.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_PARSE_TIMEOUT_S = 15

# Script Node: lê o arquivo, tenta parsear como TSX, imprime JSON.
# NOTA: com `node -e <script> a b`, process.argv = [node, a, b] —
# os argumentos posicionais começam em argv[1] (não há slot de filename).
_PARSER_JS = r"""
const fs = require('fs');
const parser = require(process.argv[1]);
const src = fs.readFileSync(process.argv[2], 'utf-8');
try {
  parser.parse(src, {
    sourceType: 'module',
    errorRecovery: false,
    plugins: ['jsx', 'typescript', 'decorators-legacy', 'classProperties'],
  });
  console.log(JSON.stringify({ ok: true }));
} catch (e) {
  console.log(JSON.stringify({
    ok: false,
    error: String(e.message || e).slice(0, 300),
    line: e.loc ? e.loc.line : null,
  }));
}
"""


@lru_cache(maxsize=1)
def _find_babel_parser() -> str | None:
    """Localiza @babel/parser no npm global (cacheado por processo)."""
    try:
        npm_root = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        candidate = Path(npm_root) / "@babel" / "parser"
        if (candidate / "package.json").exists():
            return str(candidate)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    log.debug("babel_parser_not_found", hint="npm install -g @babel/parser")
    return None


def babel_parse_available() -> bool:
    """True se o parse real via Node + @babel/parser está disponível."""
    return _find_babel_parser() is not None


def parse_tsx(content: str) -> tuple[bool | None, str | None]:
    """
    Parseia o conteúdo como TSX com o @babel/parser real.

    Returns:
        (True, None)        — parse OK (sintaxe válida)
        (False, motivo)     — erro de sintaxe real, com linha
        (None, None)        — parser indisponível (use fallback heurístico)
    """
    parser_path = _find_babel_parser()
    if parser_path is None:
        return None, None

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".tsx", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        try:
            proc = subprocess.run(
                ["node", "-e", _PARSER_JS, parser_path, tmp],
                capture_output=True, text=True, timeout=_PARSE_TIMEOUT_S,
            )
        finally:
            Path(tmp).unlink(missing_ok=True)

        if proc.returncode != 0 or not proc.stdout.strip():
            log.debug("babel_parse_subprocess_failed", stderr=proc.stderr[:200])
            return None, None

        data = json.loads(proc.stdout.strip().splitlines()[-1])
        if data.get("ok"):
            return True, None
        line = data.get("line")
        loc = f" (linha {line})" if line else ""
        return False, f"syntax_error:{data.get('error', 'unknown')}{loc}"

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.debug("babel_parse_error", error=str(e)[:200])
        return None, None
