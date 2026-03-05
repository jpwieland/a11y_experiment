"""Utilitários para descoberta e manipulação de arquivos React/TypeScript."""

from __future__ import annotations

import re
from pathlib import Path

# Extensões de arquivo React/TypeScript suportadas
REACT_EXTENSIONS = {".tsx", ".jsx", ".ts", ".js"}

# Diretórios a excluir na busca
_EXCLUDE_DIRS = {
    "node_modules", "dist", "build", ".next", ".nuxt",
    "__pycache__", ".git", "coverage", ".cache", ".turbo",
    "out", ".svelte-kit",
}


def find_react_files(target: Path, recursive: bool = True) -> list[Path]:
    """
    Descobre arquivos React/TypeScript em um diretório.

    Args:
        target: Arquivo, diretório ou glob pattern.
        recursive: Se True, busca recursivamente (default).

    Returns:
        Lista de arquivos encontrados, ordenada deterministicamente.
    """
    if target.is_file():
        if target.suffix in REACT_EXTENSIONS:
            return [target]
        return []

    if not target.is_dir():
        # Tentar como glob pattern
        parent = target.parent
        pattern = target.name
        files = sorted(parent.glob(pattern))
        return [f for f in files if f.suffix in REACT_EXTENSIONS and f.is_file()]

    # Diretório: busca recursiva
    files: list[Path] = []
    glob_fn = target.rglob if recursive else target.glob
    for ext in REACT_EXTENSIONS:
        files.extend(glob_fn(f"*{ext}"))

    filtered = [
        f for f in files
        if not any(part in _EXCLUDE_DIRS for part in f.parts)
        and f.is_file()
    ]

    return sorted(set(filtered))


def clean_tsx_for_harness(source: str) -> str:
    """
    Remove imports, exports e type annotations TypeScript para Babel standalone.

    Permite que o componente seja renderizado em um harness HTML simples
    sem necessidade de bundler (webpack, vite, etc).

    Args:
        source: Código fonte TypeScript/TSX.

    Returns:
        Código limpo compatível com Babel standalone.
    """
    # Remove import statements ES6
    source = re.sub(
        r'^import\s+.*?from\s+[\'"][^\'"]+[\'"];?\s*\n',
        "",
        source,
        flags=re.MULTILINE,
    )
    # Remove import side-effect
    source = re.sub(
        r'^import\s+[\'"][^\'"]+[\'"];?\s*\n',
        "",
        source,
        flags=re.MULTILINE,
    )
    # Remove require() calls
    source = re.sub(
        r'^(?:const|let|var)\s+\w+\s*=\s*require\([^\)]+\);?\s*\n',
        "",
        source,
        flags=re.MULTILINE,
    )

    # Transforma `export default function Foo` em `function __Component`
    source = re.sub(
        r'^export\s+default\s+function\s+\w+',
        "function __Component",
        source,
        flags=re.MULTILINE,
        count=1,
    )
    # Transforma `export default Foo` em `const __Component = Foo`
    source = re.sub(
        r'^export\s+default\s+',
        "const __Component = ",
        source,
        flags=re.MULTILINE,
        count=1,
    )
    # Remove outros exports
    source = re.sub(r'^export\s+(?:default\s+)?', "", source, flags=re.MULTILINE)

    # Remove type annotations simples: `: Type` antes de `=`, `,`, `)`, `{`
    source = re.sub(r':\s*\w+(?:<[^>]*>)?(?=\s*[=,){\[])', "", source)

    # Remove interfaces TypeScript
    source = re.sub(r'interface\s+\w+\s*\{[^}]*\}', "", source, flags=re.DOTALL)

    # Remove type aliases
    source = re.sub(r'^type\s+\w+\s*=\s*[^;]+;', "", source, flags=re.MULTILINE)

    # Remove `as Type` casts
    source = re.sub(r'\s+as\s+\w+(?:<[^>]*>)?', "", source)

    return source


def build_html_harness(component_code: str, filename: str) -> str:
    """
    Gera HTML harness para escanear um componente React.

    O harness inclui React 18 UMD, ReactDOM e Babel standalone para processar
    JSX sem necessidade de bundler.

    Args:
        component_code: Código do componente original (com TypeScript).
        filename: Nome do arquivo (para o título da página).

    Returns:
        String HTML do harness pronto para ser salvo em disco.
    """
    cleaned = clean_tsx_for_harness(component_code)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>a11y harness: {filename}</title>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 1rem; }}
    #root {{ padding: 1rem; }}
  </style>
</head>
<body>
  <div id="root" role="main"></div>
  <script type="text/babel">
    const {{useState, useEffect, useRef, useCallback, useMemo, useContext, useReducer}} = React;

    /* ── Mocks de dependências comuns ── */
    const useNavigate = () => () => {{}};
    const useParams = () => ({{}});
    const useLocation = () => ({{ pathname: '/', search: '', hash: '' }});
    const Link = ({{to, children, ...props}}) => <a href={{to}} {{...props}}>{{children}}</a>;
    const NavLink = Link;
    const Navigate = () => null;
    const Route = () => null;
    const Routes = ({{children}}) => <div>{{children}}</div>;
    const clsx = (...args) => args.filter(Boolean).join(' ');
    const cn = clsx;
    const classNames = clsx;

    /* ── Código do componente ── */
    {cleaned}

    /* ── Mount ── */
    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(
      <React.StrictMode>
        {{typeof __Component !== 'undefined'
          ? <__Component />
          : <p role="alert">Component export not found in: {filename}</p>
        }}
      </React.StrictMode>
    );
  </script>
</body>
</html>"""


def read_file_safe(path: Path) -> tuple[str, str | None]:
    """
    Lê arquivo com tratamento de erros de encoding.

    Args:
        path: Caminho do arquivo.

    Returns:
        Tupla (conteúdo, erro). Se erro=None, leitura foi bem-sucedida.
    """
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding), None
        except UnicodeDecodeError:
            continue
    return "", f"Cannot decode file: {path}"
