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
    "out", ".svelte-kit", ".expo", "storybook-static",
    "public", "static", "__fixtures__", "__mocks__",
}

# Padrões de arquivo a excluir (testes, mocks, stories)
_EXCLUDE_PATTERNS = re.compile(
    r"(\.(test|spec|stories|story)\.(tsx?|jsx?)|"
    r"\.d\.ts|"
    r"/__tests__/|"
    r"/test/|"
    r"setupTests\.(ts|js))"
)


def find_react_files(target: Path, recursive: bool = True) -> list[Path]:
    """
    Descobre arquivos React/TypeScript em um diretório.

    Exclui automaticamente arquivos de teste, stories, type definitions
    e diretórios gerados (node_modules, dist, build, etc.).

    Args:
        target: Arquivo, diretório ou glob pattern.
        recursive: Se True, busca recursivamente (default).

    Returns:
        Lista de arquivos encontrados, ordenada deterministicamente.
    """
    if target.is_file():
        if target.suffix in REACT_EXTENSIONS and not _EXCLUDE_PATTERNS.search(str(target)):
            return [target]
        return []

    if not target.is_dir():
        # Tentar como glob pattern
        parent = target.parent
        pattern = target.name
        files = sorted(parent.glob(pattern))
        return [
            f for f in files
            if f.suffix in REACT_EXTENSIONS
            and f.is_file()
            and not _EXCLUDE_PATTERNS.search(str(f))
        ]

    # Diretório: busca recursiva
    files: list[Path] = []
    glob_fn = target.rglob if recursive else target.glob
    for ext in REACT_EXTENSIONS:
        files.extend(glob_fn(f"*{ext}"))

    filtered = [
        f for f in files
        if not any(part in _EXCLUDE_DIRS for part in f.parts)
        and f.is_file()
        and not _EXCLUDE_PATTERNS.search(str(f))
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
    # Remove import statements ES6 (com e sem multiline)
    source = re.sub(
        r"^import\s+[\s\S]*?from\s+['\"][^'\"]+['\"];?\s*\n",
        "",
        source,
        flags=re.MULTILINE,
    )
    # Remove import side-effect (import 'css', import 'polyfill', etc.)
    source = re.sub(
        r"^import\s+['\"][^'\"]+['\"];?\s*\n",
        "",
        source,
        flags=re.MULTILINE,
    )
    # Remove require() calls
    source = re.sub(
        r"^(?:const|let|var)\s+\w+\s*=\s*require\([^)]+\);?\s*\n",
        "",
        source,
        flags=re.MULTILINE,
    )

    # Transforma `export default function Foo` em `function __Component`
    source = re.sub(
        r"^export\s+default\s+function\s+\w+",
        "function __Component",
        source,
        flags=re.MULTILINE,
        count=1,
    )
    # Transforma `export default class Foo` em `class __ComponentClass`
    source = re.sub(
        r"^export\s+default\s+class\s+\w+",
        "class __ComponentClass",
        source,
        flags=re.MULTILINE,
        count=1,
    )
    # Transforma `export default Foo` em `const __Component = Foo`
    source = re.sub(
        r"^export\s+default\s+",
        "const __Component = ",
        source,
        flags=re.MULTILINE,
        count=1,
    )
    # Remove outros exports mantendo a declaração
    source = re.sub(r"^export\s+(?:default\s+)?", "", source, flags=re.MULTILINE)

    # Remove type annotations simples: `: Type` antes de `=`, `,`, `)`, `{`
    source = re.sub(r":\s*\w+(?:<[^>]*>)?(?=\s*[=,){\[])", "", source)

    # Remove interfaces TypeScript
    source = re.sub(r"interface\s+\w+(?:\s+extends\s+[^{]+)?\s*\{[^}]*\}", "", source, flags=re.DOTALL)

    # Remove type aliases
    source = re.sub(r"^type\s+\w+\s*=\s*[^;]+;", "", source, flags=re.MULTILINE)

    # Remove `as Type` casts
    source = re.sub(r"\s+as\s+\w+(?:<[^>]*>)?", "", source)

    # Remove enums TypeScript
    source = re.sub(r"^(?:const\s+)?enum\s+\w+\s*\{[^}]*\}", "", source, flags=re.MULTILINE | re.DOTALL)

    return source


def build_html_harness(component_code: str, filename: str) -> str:
    """
    Gera HTML harness self-contained para escanear um componente React.

    Usa builds de produção minificadas (menores e mais rápidas de baixar que
    as development builds). Inclui mocks das dependências mais comuns para
    garantir renderização mesmo sem o projeto completo instalado.

    O harness é servido via HTTP local pelo orquestrador (HarnessServer),
    o que elimina restrições de cross-origin que afetam o protocolo file://.

    Args:
        component_code: Código do componente original (com TypeScript).
        filename: Nome do arquivo (para o título da página).

    Returns:
        String HTML do harness pronto para ser salvo em disco.
    """
    cleaned = clean_tsx_for_harness(component_code)

    # Escapar filename para uso em HTML/JS
    safe_filename = filename.replace("'", "\\'").replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>a11y harness: {safe_filename}</title>

  <!--
    Usando builds de PRODUÇÃO (minificadas) em vez de development:
    - react.production.min.js: ~11KB vs ~144KB development
    - react-dom.production.min.js: ~42KB vs ~1.1MB development
    - Carregamento ~10x mais rápido, reduz timeout risk
  -->
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>

  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 1rem; background: #fff; }}
    #root {{ padding: 1rem; }}
    .a11y-error {{ color: #c00; font-size: 0.9rem; padding: 0.5rem; border: 1px solid #c00; }}
  </style>
</head>
<body>
  <div id="root" role="main" aria-label="Component preview"></div>

  <!--
    Script inline (sem Babel) para:
    1. Detectar falha de CDN e mostrar placeholder estático
    2. Sinalizar quando o componente React renderizou com sucesso
  -->
  <script>
    // Marcador: será true após React renderizar com sucesso
    window.__a11yHarnessReady = false;

    // Detectar falha de carregamento de scripts CDN
    var _cdnErrors = 0;
    function _handleScriptError(e) {{
      _cdnErrors++;
      console.warn('[a11y-harness] CDN script failed to load:', e.target && e.target.src);
    }}
    document.querySelectorAll('script[src]').forEach(function(s) {{
      s.addEventListener('error', _handleScriptError);
    }});

    // Fallback: se após 8 segundos o root ainda estiver vazio,
    // insere placeholder estático para que axe ainda detecte issues estruturais
    setTimeout(function() {{
      var root = document.getElementById('root');
      if (root && root.children.length === 0) {{
        root.innerHTML =
          '<main><h1>Component: {safe_filename}</h1>' +
          '<p role="status" aria-live="polite">Static analysis mode (React/CDN unavailable)</p>' +
          '<nav aria-label="placeholder"><a href="#">Link placeholder</a></nav>' +
          '<form>' +
          '<label for="f1">Input field</label>' +
          '<input id="f1" type="text">' +
          '<button type="submit">Submit</button>' +
          '</form>' +
          '</main>';
        console.warn('[a11y-harness] Using static placeholder (CDN may have failed)');
      }}
    }}, 8000);
  </script>

  <script type="text/babel">
    /* ── React hooks (disponíveis no UMD global) ── */
    const {{
      useState, useEffect, useRef, useCallback,
      useMemo, useContext, useReducer, useId,
      forwardRef, memo, createContext,
    }} = React;

    /* ── Mocks de dependências comuns ── */
    // React Router v6
    const useNavigate = () => () => {{}};
    const useParams = () => ({{}});
    const useLocation = () => ({{ pathname: '/', search: '', hash: '', state: null }});
    const useMatch = () => null;
    const useSearchParams = () => [new URLSearchParams(), () => {{}}];
    const Link = ({{to, children, ...props}}) => <a href={{to || '#'}} {{...props}}>{{children}}</a>;
    const NavLink = Link;
    const Navigate = () => null;
    const Route = () => null;
    const Routes = ({{children}}) => <div>{{children}}</div>;
    const Outlet = () => null;
    const MemoryRouter = ({{children}}) => <div>{{children}}</div>;
    const BrowserRouter = ({{children}}) => <div>{{children}}</div>;

    // Utilitários de classname
    const clsx = (...args) => args.flat().filter(Boolean).join(' ');
    const cn = clsx;
    const classNames = clsx;
    const classnames = clsx;

    // i18n mocks
    const useTranslation = () => ({{ t: (k) => k, i18n: {{ language: 'en' }} }});
    const Trans = ({{children}}) => <span>{{children}}</span>;

    // Zustand / Redux mocks
    const useSelector = (fn) => {{
      try {{ return fn({{}}); }} catch {{{{ return undefined; }}}}
    }};
    const useDispatch = () => () => {{}};
    const useStore = () => ({{}});

    // Styled-components / emotion mocks
    const styled = new Proxy({{}}, {{
      get: (_, tag) => (strings, ...values) => {{
        const Component = ({{children, ...props}}) =>
          React.createElement(tag, props, children);
        Component.displayName = `styled.${{tag}}`;
        return Component;
      }}
    }});
    const css = (...args) => args.join('');
    const keyframes = (...args) => args.join('');
    const ThemeProvider = ({{children}}) => <div>{{children}}</div>;
    const useTheme = () => ({{}});

    // Next.js mocks
    const useRouter = () => ({{
      push: () => {{}}, replace: () => {{}}, back: () => {{}},
      pathname: '/', query: {{}}, asPath: '/', locale: 'en'
    }});
    const Image = ({{src, alt, ...props}}) => <img src={{src}} alt={{alt || ''}} {{...props}} />;
    const NextLink = Link;

    // Form libraries mocks
    const useForm = () => ({{
      register: (name) => ({{ name, onChange: () => {{}}, onBlur: () => {{}}, ref: () => {{}} }}),
      handleSubmit: (fn) => (e) => {{ e && e.preventDefault(); fn({{}}); }},
      formState: {{ errors: {{}}, isSubmitting: false }},
      control: {{}},
      watch: () => undefined,
      setValue: () => {{}},
      getValues: () => ({{}}),
    }});
    const Controller = ({{render}}) => render({{ field: {{ value: '', onChange: () => {{}} }}, fieldState: {{}} }});
    const FormProvider = ({{children}}) => <div>{{children}}</div>;

    // Context mocks comuns
    const AuthContext = createContext({{ user: null, isAuthenticated: false }});
    const ThemeContext = createContext({{ theme: 'light', toggleTheme: () => {{}} }});

    /* ── Código do componente ── */
    {cleaned}

    /* ── Resolver __Component ── */
    // Tenta exportação padrão, depois procura por class herdada
    let __ComponentResolved = (typeof __Component !== 'undefined') ? __Component
      : (typeof __ComponentClass !== 'undefined') ? __ComponentClass
      : null;

    /* ── Mount ── */
    try {{
      const rootEl = document.getElementById('root');
      const reactRoot = ReactDOM.createRoot(rootEl);
      reactRoot.render(
        <React.StrictMode>
          {{__ComponentResolved
            ? <__ComponentResolved />
            : <p role="alert" aria-live="assertive">
                Component export not detected in: {safe_filename}
              </p>
          }}
        </React.StrictMode>
      );
      window.__a11yHarnessReady = true;
    }} catch (err) {{
      console.error('[a11y-harness] React render error:', err);
      document.getElementById('root').innerHTML =
        '<p role="alert" aria-live="assertive">Render error: ' + err.message + '</p>';
    }}
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
