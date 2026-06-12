"""Utilitários para descoberta e manipulação de arquivos React/TypeScript."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Extensões de arquivo React/TypeScript suportadas
REACT_EXTENSIONS = {".tsx", ".jsx", ".ts", ".js"}

# Diretórios a excluir na busca.
# Inclui dotdirs de ferramentas (.claude, .idea, .vscode, etc.) que podem
# conter arquivos .ts/.tsx de configuração/fixtures sem relação com o projeto.
_EXCLUDE_DIRS = {
    # Artefatos de build e dependências
    "node_modules", "dist", "build", ".next", ".nuxt",
    "__pycache__", "coverage", ".cache", ".turbo",
    "out", ".svelte-kit", ".expo", "storybook-static",
    # Arquivos estáticos sem componentes
    "public", "static", "__fixtures__", "__mocks__",
    # Controle de versão e dotdirs de ferramentas
    ".git", ".github", ".gitlab",
    ".claude",        # Claude Code — configuração e transcripts
    ".idea",          # JetBrains IDEs
    ".vscode",        # VS Code
    ".cursor",        # Cursor IDE
    ".nx",            # Nx monorepo cache
    ".angular",       # Angular CLI cache
    ".husky",         # Git hooks
    "e2e",            # testes end-to-end (fora do padrão .spec)
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

    # NOTA: a remoção de type annotations via regex foi REMOVIDA (06/2026).
    # A regex `:\s*\w+(?=[=,){\[])` corrompia objetos literais JavaScript
    # legítimos — `style={{ padding: 20 }}` virava `style={{ padding }}` e
    # `{ enabled: true }` virava `{ enabled }` — causando ReferenceError no
    # mount e renderização em branco (axe via página vazia → 0 issues).
    # A remoção de TypeScript agora é feita corretamente pelo próprio Babel
    # com preset-typescript (ver build_html_harness: Babel.transform manual
    # com isTSX). Interfaces, type aliases, enums, casts e annotations são
    # tratados pelo parser real em vez de regex.
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
  <div id="root" role="region" aria-label="Component preview"></div>

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

  <!--
    Código do componente em text/plain: transformado MANUALMENTE via
    Babel.transform com preset-typescript (isTSX) + preset-react.
    O type="text/babel" automático não suporta TSX (preset-typescript
    exige filename .tsx, que scripts inline não têm). A transformação
    manual elimina a antiga remoção de tipos via regex, que corrompia
    objetos literais JS legítimos.
  -->
  <script type="text/plain" id="__component_source">
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

  <script>
    /*
      Transformação manual: TSX → JS via Babel com preset-typescript real.
      filename: 'component.tsx' ativa o modo TSX do preset-typescript;
      annotations, interfaces, enums e casts são removidos pelo parser
      (não por regex), preservando objetos literais JavaScript.
    */
    (function() {{
      function fail(msg) {{
        console.error('[a11y-harness] Babel transform error:', msg);
        var root = document.getElementById('root');
        if (root) {{
          root.innerHTML = '<p role="alert" aria-live="assertive">Transform error: ' +
            String(msg).slice(0, 300) + '</p>';
        }}
      }}
      if (typeof Babel === 'undefined') {{
        fail('Babel standalone not loaded (CDN failure)');
        return;
      }}
      var src = document.getElementById('__component_source').textContent;
      var out;
      try {{
        out = Babel.transform(src, {{
          filename: 'component.tsx',
          presets: [
            ['typescript', {{ isTSX: true, allExtensions: true }}],
            ['react'],
          ],
        }}).code;
      }} catch (err) {{
        fail(err && err.message ? err.message : err);
        return;
      }}
      try {{
        (0, eval)(out);
      }} catch (err) {{
        fail('runtime: ' + (err && err.message ? err.message : err));
      }}
    }})();
  </script>
</body>
</html>"""


def build_angular_harness(template_html: str, filename: str) -> str:
    """
    Gera HTML harness self-contained para escanear um template Angular.

    Serve o template HTML estático extraído do @Component, sem compilação
    Angular em runtime. Isso permite que os scanners de acessibilidade
    analisem a estrutura semântica e ARIA do template sem depender de
    React, Babel ou do compilador Ivy.

    O container raiz usa role="main" para que regras de landmark do axe
    não gerem falsos positivos no harness (análogo ao que o harness React
    já faz via regras desabilitadas no playwright_axe.py).

    Args:
        template_html: HTML do template extraído do @Component.
        filename: Nome do arquivo .component.ts (para o título da página).

    Returns:
        String HTML do harness pronto para ser salvo em disco.
    """
    safe_filename = filename.replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>a11y harness: {safe_filename}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 1rem; background: #fff; }}
    #root {{ padding: 1rem; }}
  </style>
</head>
<body>
  <main id="root" role="main">
    {template_html}
  </main>
</body>
</html>"""


def strip_angular_template_syntax(template: str) -> str:
    """
    Converte template Angular em HTML estático exibível no browser.

    Estratégia:
    - Converte marcadores de controle de fluxo (@if/@for etc.) em comentários
      HTML em vez de removê-los → o conteúdo interno NUNCA é perdido.
    - Substitui {{ expr }} por texto simplificado.
    - Remove atributos Angular dinâmicos ([binding], (event)).
    - Converte mat-icon → span estilizado offline (sem CDN).
    - Converte tags Angular personalizadas → div/span.
    - Guarda fallback para o template original se o resultado ficar vazio.
    """
    import re

    result = template

    # ── Passo 0: Normalizar elements estruturais Angular sem saída HTML ──────
    # ng-container é transparente — remove as tags, mantém o conteúdo.
    result = re.sub(r'<ng-container[^>]*>', '', result)
    result = re.sub(r'</ng-container>', '', result)

    # ng-template → conteúdo oculto por padrão, mas mantido no DOM
    result = re.sub(r'<ng-template[^>]*>', '<div class="ng-tpl-preview" style="border-left:3px solid #ccc;padding:4px;margin:2px;opacity:0.6">', result)
    result = re.sub(r'</ng-template>', '</div>', result)

    # router-outlet, ng-content → placeholder visual
    result = re.sub(
        r'<router-outlet[^>]*/?>(?:\s*</router-outlet>)?',
        '<div class="ng-outlet-placeholder" style="min-height:32px;background:#f0f4ff;border:1px dashed #90a4ae;border-radius:3px;padding:4px 8px;color:#607d8b;font-size:11px">[ router-outlet ]</div>',
        result,
    )
    result = re.sub(
        r'<ng-content[^>]*/?>(?:\s*</ng-content>)?',
        '<div class="ng-content-placeholder" style="min-height:24px;background:#f5f5f5;border:1px dashed #bdbdbd;border-radius:3px;padding:4px 8px;color:#9e9e9e;font-size:11px">[ ng-content ]</div>',
        result,
    )

    # ── Passo 1: Comentar marcadores de controle de fluxo ───────────────────
    # COMENTAR (não remover) preserva o conteúdo interno garantidamente.
    # Ordem importa: padrão `} @else {` deve vir ANTES de `@if {` para não
    # deixar o `}` inicial isolado.
    #
    # Usamos [^{]* em vez de \([^)]*\) para suportar parênteses aninhados
    # dentro de condições Angular: @if (!fn()) { tem () dentro de ().

    # } @else { / } @else if (...) { / } @case (...) { etc.
    result = re.sub(
        r'(\}\s*@(?:else|case|default|empty)[^{]*\{)',
        r'<!-- \1 -->',
        result,
    )
    # @if/@for/@switch etc. com seus { de abertura
    result = re.sub(
        r'(@(?:if|else\s*if|for|switch|case|default|empty|defer|loading|error|placeholder)'
        r'[^{]*\{)',
        r'<!-- \1 -->',
        result,
    )
    # Linhas que são somente } ou } } (fechamento de bloco de controle)
    result = re.sub(r'^(\s*\}+\s*)$', r'<!-- \1-->', result, flags=re.MULTILINE)

    # } no início de linha seguido apenas de espaços/comentários HTML
    # (sobram depois de comentar @if blocks inline)
    result = re.sub(
        r'^(\s*\}+)(\s*(?:<!--[^>]*>\s*)*)$',
        r'<!-- \1-->\2',
        result,
        flags=re.MULTILINE,
    )

    # ── Passo 2: Substituir {{ expr }} por texto legível ─────────────────────
    def _simplify_binding(m: re.Match) -> str:
        expr = m.group(1).strip()

        # 1. Remover pipes Angular: | translate | async | uppercase etc.
        #    NÃO usar DOTALL aqui — remove só "| pipeName", não o resto da expr.
        #    Ex: ("chave" | translate) + ": " → ("chave") + ": "
        expr = re.sub(r'\s*\|\s*\w+', '', expr)

        # 2. Remover string literals usados como prefixo/sufixo em concatenações
        #    "prefix" + var → var        |        var + "suffix" → var
        expr = re.sub(r"['\"][^'\"]*['\"]\s*\+\s*", '', expr)   # "str" + → remove
        expr = re.sub(r"\s*\+\s*['\"][^'\"]*['\"]", '', expr)   # + "str" → remove

        # 3. Substituir string literals restantes pelo seu conteúdo
        expr = re.sub(r"['\"]([^'\"]*)['\"]", r'\1', expr)

        # 4. Remover operadores de concatenação restantes
        expr = re.sub(r'\s*\+\s*', ' ', expr).strip()

        # 5. Remover parênteses/colchetes/chaves avulsos
        expr = re.sub(r'[()[\]{}]', '', expr).strip()

        # 6. Normalizar optional chaining: a?.b → a.b
        expr = expr.replace('?.', '.')

        # 7. Se houver múltiplos tokens separados por espaço, pegar o último
        #    identificador significativo (ex: "header.key empresa.titulo" → "empresa.titulo")
        tokens = [t for t in expr.split() if re.match(r'^[\w.]+$', t) and not t.isdigit()]
        if tokens:
            expr = tokens[-1]

        # 8. Para acesso encadeado simples mostrar as 2 partes finais para contexto
        #    Ex: item.button.textLong → button.textLong
        if re.match(r'^[\w.]+$', expr):
            parts = [p for p in expr.split('.') if p]
            expr = '.'.join(parts[-2:]) if len(parts) > 2 else expr

        # 9. Truncar expressões longas
        if len(expr) > 40:
            expr = expr[:40]

        return expr.strip() if expr.strip() else '…'

    # Processar interpolações {{ ... }} respeitando chaves aninhadas.
    # O regex [^}]+? falha quando a expressão contém objetos literais { k: v }.
    # Usamos um scanner de caracteres para encontrar o }} de fechamento correto.
    def _apply_interpolations(text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            # Procura abertura {{
            if text[i] == '{' and i + 1 < n and text[i + 1] == '{':
                depth = 0
                j = i + 2
                while j < n:
                    if text[j] == '{':
                        depth += 1
                    elif text[j] == '}':
                        if depth == 0:
                            # Verifica se é }} (fecha interpolação)
                            if j + 1 < n and text[j + 1] == '}':
                                inner = text[i + 2:j].strip()
                                # Cria um Match-like object para reusar _simplify_binding
                                class _M:
                                    def __init__(self, s: str) -> None:
                                        self._s = s
                                    def group(self, n: int) -> str:
                                        return self._s
                                out.append(_simplify_binding(_M(inner)))
                                i = j + 2
                                break
                            else:
                                depth -= 1
                        else:
                            depth -= 1
                    j += 1
                else:
                    # Interpolação não fechada — manter como estava
                    out.append(text[i])
                    i += 1
            else:
                out.append(text[i])
                i += 1
        return ''.join(out)

    result = _apply_interpolations(result)

    # ── Passo 3: Remover atributos Angular dinâmicos ─────────────────────────
    # Ordem importa: two-way primeiro, depois property, depois event.
    # Usar re.DOTALL para capturar valores multi-linha.

    # [(two-way)]="var"
    result = re.sub(r'\s*\[\([^\)]+\)\]="[^"]*"', '', result, flags=re.DOTALL)
    # [property]="expr" — incluindo [attr.xxx], [style.xxx], [class.xxx]
    result = re.sub(r'\s*\[[^\]]+\]="[^"]*"', '', result, flags=re.DOTALL)
    # (event)="handler()"
    result = re.sub(r'\s*\([^)]+\)="[^"]*"', '', result, flags=re.DOTALL)
    # *ngIf *ngFor *cdkVirtualFor etc.
    result = re.sub(r'\s*\*\w+="[^"]*"', '', result)
    # #templateRef local variables
    result = re.sub(r'\s*#\w+', '', result)
    # Atributos diretivos Angular sem valor (matInput, cdkScrollable, matSort, etc.)
    # Remove apenas os reconhecidos para não afetar atributos HTML legítimos
    _DIRECTIVE_ATTRS = re.compile(
        r'\s+(?:matInput|matSort|matSortHeader|matRipple|matTooltip'
        r'|cdkScrollable|cdkVirtualScrollViewport|cdkOverlayOrigin'
        r'|matAutocomplete|matPrefix|matSuffix|matStepperNext|matStepperPrevious'
        r'|mat-button|mat-raised-button|mat-flat-button|mat-stroked-button'
        r'|mat-icon-button|mat-fab|mat-mini-fab|mat-menu-item'
        r'|routerLinkActive|routerLinkActiveOptions'
        r'|translate|appTranslate|ngTranslate'   # i18n directives (bare attr)
        r'|vertical|horizontal'                   # mat-divider orientation
        r'|mat-list-icon|mat-body-1|mat-body-2|mat-body-3'
        r'|mat-subtitle-1|mat-subtitle-2|mat-caption|mat-typography'
        r'|mat-h1|mat-h2|mat-h3|mat-h4)\b',
        re.IGNORECASE,
    )
    result = _DIRECTIVE_ATTRS.sub('', result)

    # ── Passo 4: mat-icon → span com ícone inline (sem CDN) ─────────────────
    # Map dos ícones mais comuns para Unicode/emoji.
    # Ícones sem mapeamento exibem o nome do ícone entre [ ] estilizado.
    _ICON_MAP = {
        'menu': '☰', 'close': '✕', 'search': '🔍', 'home': '⌂',
        'settings': '⚙', 'more_vert': '⋮', 'more_horiz': '⋯',
        'person': '👤', 'person_outline': '👤', 'person_outlined': '👤',
        'arrow_back': '←', 'arrow_forward': '→', 'arrow_drop_down': '▾',
        'arrow_drop_up': '▴', 'arrow_upward': '↑', 'arrow_downward': '↓',
        'expand_more': '▾', 'expand_less': '▴', 'chevron_right': '›',
        'chevron_left': '‹', 'add': '+', 'remove': '−', 'edit': '✏',
        'delete': '🗑', 'check': '✓', 'check_circle': '✓', 'cancel': '✕',
        'info': 'ℹ', 'warning': '⚠', 'error': '✗', 'help': '?',
        'visibility': '👁', 'visibility_off': '👁', 'lock': '🔒',
        'logout': '⎋', 'login': '⎆', 'notifications': '🔔',
        'star': '★', 'star_border': '☆', 'favorite': '♥',
        'share': '↗', 'download': '↓', 'upload': '↑',
        'filter_list': '≡', 'sort': '↕', 'refresh': '↺',
        # Acessibilidade / zoom
        'zoom_in': '⊕', 'zoom_out': '⊖', 'zoom_in_map': '⊕', 'zoom_out_map': '⊖',
        'accessibility': '♿', 'accessibility_new': '♿', 'accessible': '♿',
        'accessible_forward': '♿', 'hearing': '👂', 'sign_language': '🤟',
        'contrast': '◑', 'brightness_high': '☀', 'brightness_low': '☀',
        'format_size': 'A↕', 'text_fields': 'T', 'text_increase': 'A+',
        'text_decrease': 'A−', 'font_download': 'F',
        # Navegação / interface
        'open_in_new': '↗', 'open_in_full': '⤢', 'close_fullscreen': '⤡',
        'fullscreen': '⤢', 'fullscreen_exit': '⤡',
        'first_page': '⏮', 'last_page': '⏭', 'navigate_before': '‹',
        'navigate_next': '›', 'skip_previous': '⏮', 'skip_next': '⏭',
        'play_arrow': '▶', 'pause': '⏸', 'stop': '⏹',
        'volume_up': '🔊', 'volume_off': '🔇', 'mic': '🎤', 'mic_off': '🎤',
        # Layout / conteúdo
        'dashboard': '⊞', 'grid_view': '⊞', 'list': '☰', 'view_list': '☰',
        'table_chart': '▦', 'bar_chart': '▨', 'pie_chart': '◑',
        'attach_file': '📎', 'link': '🔗', 'image': '🖼', 'photo': '🖼',
        'folder': '📁', 'folder_open': '📂', 'file_copy': '📄',
        'print': '🖨', 'send': '✉', 'mail': '✉', 'email': '✉',
        'phone': '📞', 'smartphone': '📱', 'computer': '💻',
        'language': '🌐', 'public': '🌐', 'place': '📍', 'location_on': '📍',
        'calendar_today': '📅', 'event': '📅', 'schedule': '🕐',
        'account_circle': '👤', 'group': '👥', 'groups': '👥',
        'flag': '🚩', 'label': '🏷', 'bookmark': '🔖',
    }

    def _mat_icon_to_span(m: re.Match) -> str:
        attrs_raw = m.group(1)
        name = m.group(2).strip()
        # Se o conteúdo foi limpo (binding removido), tenta atributo fontIcon/svgIcon
        if not name:
            fn = re.search(r'fontIcon=["\']([^"\']+)["\']', attrs_raw)
            sv = re.search(r'svgIcon=["\']([^"\']+)["\']', attrs_raw)
            name = (fn or sv or type('', (), {'group': lambda s, i: ''})()).group(1) if (fn or sv) else ''
        if not name:
            return ''  # mat-icon vazio/sem nome → remover
        # Unicode fallback para harness offline (axe harness)
        symbol = _ICON_MAP.get(name, f'[{name}]')
        # Manter apenas aria-* e title attrs; descartar fontSet, svgIcon, fontIcon etc.
        safe_attrs = ''
        for am in re.finditer(r'(aria-\w+)="([^"]*)"', attrs_raw):
            safe_attrs += f' {am.group(1)}="{am.group(2)}"'
        # Usar "material-icons a11y-icon" para que:
        #   - Harness com material-icons.woff2 carregado → ícone real via ligatura
        #   - Harness offline → mostra símbolo Unicode via .a11y-icon + data-icon
        return (
            f'<span class="material-icons a11y-icon" title="{name}"'
            f' data-icon="{name}"{safe_attrs}>'
            f'{name}</span>'
        )

    # \s* antes do > final cobre closing tags estilo Prettier: </mat-icon\n  >
    result = re.sub(
        r'<mat-icon([^>]*)>(.*?)</mat-icon\s*>',
        _mat_icon_to_span,
        result,
        flags=re.DOTALL,
    )

    # ── Passo 5: Converter tags Angular custom → elementos HTML neutros ──────
    # Componentes app-*/lib-* recebem classe + data-component para placeholder visual.
    # Tags mat-*, cdk-*, etc. viram div neutro.
    _NG_BLOCK = re.compile(
        r'<(mat-(?:toolbar|toolbar-row|sidenav(?:-container|-content)?|nav-bar'
        r'|menu(?:-item)?|divider|list(?:-item)?|card(?:-content|-header|-footer|-title|-subtitle|-actions)?'
        r'|expansion-panel(?:-header|-content)?|tab-group|tab(?:-body|-label)?'
        r'|chip(?:-list|-set|-option)?|autocomplete'
        r'|option|select|form-field|label|hint|error|prefix|suffix|input|textarea'
        r'|slide-toggle|checkbox|radio(?:-group|-button)?'
        r'|progress-(?:bar|spinner)|badge|tooltip|paginator'
        r'|table|row|cell|header-row|footer-row'
        r'|header-cell|footer-cell|column-def|sort-header|no-data-row)'
        r'|app-[\w-]+|nz-[\w-]+|p-[\w-]+|cdk-[\w-]+|lib-[\w-]+)(\s[^>]*)?>',
        re.IGNORECASE,
    )

    def _ng_tag_to_div(m: re.Match) -> str:
        tag = m.group(1).lower()
        attrs = m.group(2) or ''
        # app-* e lib-* são sub-componentes Angular: mostrar badge com o nome
        if re.match(r'^(app|lib)-', tag):
            return (
                f'<div class="ng-app-component" data-component="{tag}"{attrs}>'
            )
        # mat-*, nz-*, cdk-*, p-*: adicionar o nome original da tag como classe CSS
        # para que as regras .mat-toolbar, .mat-card etc. funcionem no harness.
        mat_class = tag  # e.g. "mat-toolbar"
        _class_re = re.compile(r'\bclass=["\']([^"\']*)["\']', re.IGNORECASE)
        _cm = _class_re.search(attrs)
        if _cm:
            existing = _cm.group(1)
            if mat_class not in existing.split():
                q = _cm.group(0)[6]  # quotechar: " ou '
                new_cls = f'{mat_class} {existing}'.strip()
                attrs = attrs[:_cm.start()] + f'class={q}{new_cls}{q}' + attrs[_cm.end():]
        else:
            attrs = f' class="{mat_class}"' + attrs
        return f'<div{attrs}>'

    result = _NG_BLOCK.sub(_ng_tag_to_div, result)

    # \s* antes do > final cobre closing tags formatados com > em linha própria
    _NG_BLOCK_CLOSE = re.compile(
        r'</(mat-[\w-]+|app-[\w-]+|nz-[\w-]+|p-[\w-]+|cdk-[\w-]+|lib-[\w-]+)\s*>',
        re.IGNORECASE,
    )
    result = _NG_BLOCK_CLOSE.sub('</div>', result)

    # ── Passo 6: Adicionar onerror inline nas imagens ───────────────────────
    # O handler JS é invocado tarde demais quando as imagens já falharam.
    # Injetar onerror diretamente na tag garante disparo confiável.
    # Se a imagem falhar: mostra placeholder cinza com nome do arquivo.
    _IMG_ONERROR = (
        r"this.onerror=null;"
        r"var n=this.src.split('/').pop();"
        r"var p=document.createElement('div');"
        r"p.className='img-broken-placeholder';"
        r"p.textContent=n;"
        r"this.parentNode&&this.parentNode.replaceChild(p,this)"
    )

    def _add_img_onerror(m: re.Match) -> str:
        tag = m.group(0)
        if 'onerror' in tag:
            return tag
        # Inserir onerror antes do fechamento da tag
        tag = re.sub(r'\s*/?>$', f' onerror="{_IMG_ONERROR}">', tag)
        return tag

    result = re.sub(r'<img\b[^>]*/?>',  _add_img_onerror, result, flags=re.DOTALL)

    # ── Fallback: se o resultado ficou completamente vazio ───────────────────
    if not result.strip():
        return template

    return result


# Mapeamento de atributos Angular de roles/comportamento para atributos HTML
_MAT_BUTTON_ATTRS = re.compile(
    r'\b(mat-button|mat-raised-button|mat-flat-button|mat-stroked-button'
    r'|mat-icon-button|mat-fab|mat-mini-fab)\b',
    re.IGNORECASE,
)


def find_angular_project_root(start_path: Path) -> Path | None:
    """
    Sobe na hierarquia de diretórios a partir de *start_path* procurando
    o root do projeto Angular (diretório que contém `angular.json`).

    Retorna None se não encontrar em até 10 níveis acima.
    """
    current = start_path if start_path.is_dir() else start_path.parent
    for _ in range(10):
        if (current / "angular.json").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_angular_assets_dir(project_root: Path) -> Path | None:
    """
    Localiza o diretório `assets/` dentro de um projeto Angular.

    Lê o `angular.json` para encontrar o path correto (monorepos têm assets
    em `projects/<name>/src/assets/`). Fallback para `src/assets/`.
    """
    angular_json = project_root / "angular.json"
    if angular_json.exists():
        try:
            data = json.loads(angular_json.read_text(encoding="utf-8"))
            for proj in data.get("projects", {}).values():
                options = (
                    proj.get("architect", {})
                    .get("build", {})
                    .get("options", {})
                )
                for entry in options.get("assets", []):
                    # entry pode ser string "projects/web/src/assets" ou dict
                    if isinstance(entry, str):
                        candidate = project_root / entry
                        if candidate.is_dir() and "assets" in candidate.parts:
                            return candidate
        except Exception:
            pass

    # Fallback: tenta localizações comuns
    for candidate in [
        project_root / "src" / "assets",
        project_root / "projects" / "web" / "src" / "assets",
    ]:
        if candidate.is_dir():
            return candidate

    return None


def _extract_scss_vars(content: str) -> dict[str, str]:
    """Extract SCSS variable definitions $name: value; from content."""
    vars: dict[str, str] = {}
    for m in re.finditer(r'^\$([a-zA-Z0-9_-]+)\s*:\s*([^;]+);', content, re.MULTILINE):
        vars[m.group(1)] = m.group(2).strip()
    return vars


def _resolve_scss_interpolation(text: str, vars: dict[str, str]) -> str:
    """Replace #{$varname} with the SCSS variable value."""
    def replace(m: re.Match) -> str:
        name = m.group(1)
        val = vars.get(name, '')
        # If the value itself references another variable, resolve it
        if val.startswith('$'):
            val = vars.get(val[1:], val)
        return val
    return re.sub(r'#\{\$([a-zA-Z0-9_-]+)\}', replace, text)


def _extract_braced_block(content: str, start: int) -> tuple[str, int]:
    """
    Extract the balanced {...} block starting at `start` (must be '{').
    Returns (block_text_including_braces, index_after_closing_brace).
    """
    assert content[start] == '{', f"Expected '{{' at position {start}"
    depth = 0
    i = start
    while i < len(content):
        c = content[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return content[start:i + 1], i + 1
        i += 1
    return content[start:], len(content)


def _expand_scss_media_mixins(content: str) -> str:
    """
    Expand @include media-breakpoint-up/down($bp) { ... } to @media (...) { ... }.
    Also expands @include media-apply-on-theme(_theme) { ... } to .theme-class { ... }.
    Removes @mixin definitions and simple @include calls (no block).
    """
    min_bps = {
        'xs': 0, 'sm': 576, 'md': 768, 'lg': 992, 'xl': 1200, 'xxl': 1400, '3xl': 1800
    }
    max_bps = {
        'xs': 0, 'sm': 575.98, 'md': 767.98, 'lg': 991.98, 'xl': 1199.98, 'xxl': 1399.98
    }
    themes = {'_dark': '.dark-theme', '_light': '.light-theme', '_blue': '.blue-theme'}

    result: list[str] = []
    i = 0
    n = len(content)

    while i < n:
        # Try @include media-breakpoint-up/down(bp)
        m = re.match(
            r'@include\s+media-breakpoint-(up|down)\(\s*(\w+)\s*\)\s*',
            content[i:]
        )
        if m:
            direction = m.group(1)
            bp = m.group(2)
            i += m.end()
            # Consume any whitespace to reach '{'
            ws = re.match(r'\s*', content[i:])
            if ws:
                i += ws.end()
            if i < n and content[i] == '{':
                block, end = _extract_braced_block(content, i)
                if direction == 'up' and bp in min_bps:
                    px = min_bps[bp]
                    if px == 0:
                        result.append(block[1:-1])  # xs means always, just emit content
                    else:
                        result.append(f'@media (min-width: {px}px) {block}')
                elif direction == 'down' and bp in max_bps:
                    px = max_bps[bp]
                    result.append(f'@media (max-width: {px}px) {block}')
                else:
                    result.append(block[1:-1])  # unknown bp, keep content inline
                i = end
            continue

        # Try @include media-apply-on-theme(_theme)
        m2 = re.match(
            r'@include\s+media-apply-on-theme\(\s*(_\w+)\s*\)\s*',
            content[i:]
        )
        if m2:
            theme_key = m2.group(1)
            theme_cls = themes.get(theme_key, '')
            i += m2.end()
            ws = re.match(r'\s*', content[i:])
            if ws:
                i += ws.end()
            if i < n and content[i] == '{':
                block, end = _extract_braced_block(content, i)
                if theme_cls:
                    result.append(f'{theme_cls} {block}')
                # else skip unknown theme
                i = end
            continue

        # Skip @mixin definitions entirely
        m3 = re.match(r'@mixin\s+[\w-]+[^{]*', content[i:])
        if m3:
            i += m3.end()
            ws = re.match(r'\s*', content[i:])
            if ws:
                i += ws.end()
            if i < n and content[i] == '{':
                _, end = _extract_braced_block(content, i)
                i = end
            continue

        # Skip @include calls without a block (simple calls like @include underline-link();)
        m4 = re.match(r'@include\s+[\w-]+\s*\([^)]*\)\s*;', content[i:])
        if m4:
            i += m4.end()
            continue

        # @include with empty parens followed by a block — expand inline
        m5 = re.match(r'@include\s+[\w-]+\s*\(\s*\)\s*', content[i:])
        if m5:
            ahead = content[i + m5.end():]
            ws = re.match(r'\s*', ahead)
            if ws and i + m5.end() + ws.end() < n and content[i + m5.end() + ws.end()] == '{':
                i += m5.end() + ws.end()
                block, end = _extract_braced_block(content, i)
                result.append(block[1:-1])  # inline the block
                i = end
                continue

        result.append(content[i])
        i += 1

    return ''.join(result)


def compile_component_scss(scss_path: Path, project_root: "Path | None" = None) -> str:
    """
    Compile an Angular component SCSS file to plain CSS for harness injection.

    Handles:
    - Stripping @use / @forward directives
    - Expanding @include media-breakpoint-up/down(...) { }
    - Expanding @include media-apply-on-theme(...) { }
    - Removing ::ng-deep and :host() encapsulation
    - Resolving simple SCSS variables ($spacing-1 etc.)
    - Skipping @mixin definitions
    """
    try:
        css = scss_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return ''

    # Remove @use / @forward lines
    css = re.sub(r'@(?:use|forward)\s+[^;]+;', '', css)

    # Extract and resolve simple SCSS variables
    all_vars = {
        'spacing-1': '1rem',
        'spacing-2': '2rem',
        'max-width': '100%',
        'max-content-width': '984px',
        'max-content-width-sm': '656px',
    }
    file_vars = _extract_scss_vars(css)
    all_vars.update(file_vars)

    # Replace $var-name usage (not in #{...} which is interpolation)
    def replace_var(m: re.Match) -> str:
        name = m.group(1)
        return all_vars.get(name, m.group(0))

    # Replace standalone $var references (not inside #{...})
    css = re.sub(r'(?<!#\{)\$([a-zA-Z0-9_-]+)', replace_var, css)

    # Expand @include media-breakpoint-* and @mixin
    css = _expand_scss_media_mixins(css)

    # Remove ::ng-deep (component encapsulation piercing)
    css = re.sub(r'::ng-deep\s*', '', css)

    # Remove :host pseudo-class
    css = re.sub(r':host\s*(?:\([^)]*\))?\s*', '', css)

    # Remove remaining @include without block (simple mixin calls)
    css = re.sub(r'@include\s+[\w-]+\s*(?:\([^)]*\))?\s*;', '', css)

    return css


def load_component_css(template_path: Path, project_root: "Path | None" = None) -> str:
    """
    Find the .ts file for *template_path*, extract styleUrls, compile each SCSS.

    Returns combined CSS string (may be empty if no styles found).
    """
    # The template is foo.component.html → look for foo.component.ts
    ts_path = template_path.with_suffix('.ts')
    if not ts_path.exists():
        return ''

    try:
        ts_src = ts_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return ''

    # Extract styleUrls: ['./foo.component.scss']
    style_urls: list[str] = []
    for m in re.finditer(
        r'styleUrls?\s*:\s*\[([^\]]+)\]',
        ts_src,
        re.DOTALL,
    ):
        for url_m in re.finditer(r"['\"]([^'\"]+)['\"]", m.group(1)):
            style_urls.append(url_m.group(1))

    if not style_urls:
        return ''

    css_parts: list[str] = []
    base_dir = ts_path.parent
    for url in style_urls:
        # Resolve relative path
        scss_path = (base_dir / url).resolve()
        if scss_path.exists():
            css_parts.append(compile_component_scss(scss_path, project_root))

    return '\n'.join(css_parts)


def extract_project_css_vars(project_root: Path) -> str:
    """
    Extract CSS custom properties (:root { ... } and .light-theme { ... }) from
    the project's tokens SCSS file, resolving SCSS variable interpolations.

    Returns a plain CSS string ready for injection into a <style> block.
    """
    # Known token file locations (relative to project root)
    candidates = [
        project_root / "projects" / "web" / "src" / "app" / "styles" / "tokens" / "_tokens.scss",
        project_root / "src" / "app" / "styles" / "tokens" / "_tokens.scss",
        project_root / "src" / "styles" / "tokens" / "_tokens.scss",
    ]

    tokens_path: Path | None = None
    for c in candidates:
        if c.exists():
            tokens_path = c
            break

    if tokens_path is None:
        return ''

    try:
        content = tokens_path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return ''

    # Extract SCSS variable values
    scss_vars = _extract_scss_vars(content)

    # Resolve nested variable references (e.g. $var: $other-var)
    for key in list(scss_vars.keys()):
        val = scss_vars[key]
        if val.startswith('$'):
            scss_vars[key] = scss_vars.get(val[1:], val)

    # Find and resolve all :root { ... } and .theme { ... } blocks
    result_blocks: list[str] = []
    i = 0
    while i < len(content):
        # Match block selector
        m = re.match(r'(:root|\.[\w-]+-theme)\s*', content[i:])
        if m:
            selector = m.group(1)
            j = i + m.end()
            ws = re.match(r'\s*', content[j:])
            if ws:
                j += ws.end()
            if j < len(content) and content[j] == '{':
                block, end = _extract_braced_block(content, j)
                resolved = _resolve_scss_interpolation(block, scss_vars)
                result_blocks.append(f'{selector} {resolved}')
                i = end
                continue
        i += 1

    return '\n'.join(result_blocks)


def load_bootstrap_utilities(project_root: Path) -> str:
    """
    Load the project's Bootstrap-like utilities CSS file.
    Returns the CSS string, or empty string if not found.
    """
    candidates = [
        project_root / "projects" / "web" / "src" / "app" / "styles" / "_bootstrap_utilities.scss",
        project_root / "src" / "app" / "styles" / "_bootstrap_utilities.scss",
        project_root / "src" / "styles" / "_bootstrap_utilities.scss",
    ]
    for c in candidates:
        if c.exists():
            try:
                css = c.read_text(encoding='utf-8', errors='ignore')
                # This file is mostly plain CSS; strip any @use lines just in case
                css = re.sub(r'@(?:use|forward)\s+[^;]+;', '', css)
                return css
            except OSError:
                pass
    return ''


# CSS Angular Material completo para injetar no harness de screenshot.
# Definido fora do f-string para evitar escaping de { e }.
# Usa os class names que _ng_tag_to_div agora injeta nos <div> convertidos.
_HARNESS_MAT_CSS = """
/* ════════════════════════════════════════════════════════════════
   Angular Material Component Styles — harness estático
   Os class names abaixo são injetados por _ng_tag_to_div:
     <mat-toolbar> → <div class="mat-toolbar ...">
   ════════════════════════════════════════════════════════════════ */

/* Toolbar */
.mat-toolbar, .mat-toolbar-row, .mat-toolbar-single-row,
.mat-toolbar-multiple-rows {
  display: flex !important; flex-direction: row; align-items: center;
  gap: 8px; min-height: 64px; padding: 0 16px; width: 100%;
  box-sizing: border-box;
  background: var(--odrtj-color-primary-8, #1565c0) !important;
  color: #fff !important;
  box-shadow: 0 2px 4px rgba(0,0,0,0.25);
  font-size: 20px; font-weight: 500;
}
.mat-toolbar-row, .mat-toolbar-multiple-rows { min-height: 48px; }
.mat-toolbar button, .mat-toolbar .mat-icon-button,
.mat-toolbar .mat-button {
  color: #fff !important; background: transparent !important; border: none;
}

/* Nav bar */
.mat-nav-bar { background: var(--odrtj-color-primary-8, #1565c0); }

/* Sidenav */
.mat-sidenav-container, .mat-drawer-container {
  display: flex !important; width: 100%; min-height: 200px;
  background: #fafafa; overflow: hidden;
}
.mat-sidenav, .mat-drawer {
  min-width: 256px; max-width: 320px; background: #fff;
  border-right: 1px solid rgba(0,0,0,0.12); padding: 8px 0;
}
.mat-sidenav-content, .mat-drawer-content { flex: 1; overflow: auto; padding: 16px; }

/* Cards */
.mat-card, .mat-mdc-card {
  display: block !important; background: #fff !important;
  border-radius: 4px; padding: 16px; margin: 8px 0; overflow: hidden;
  box-shadow: 0 2px 1px -1px rgba(0,0,0,.2), 0 1px 1px rgba(0,0,0,.14), 0 1px 3px rgba(0,0,0,.12);
}
.mat-card-header { display: flex; align-items: flex-start; gap: 16px; padding-bottom: 8px; }
.mat-card-title { font-size: 1.25rem; font-weight: 500; margin: 0; }
.mat-card-subtitle { font-size: .875rem; color: rgba(0,0,0,.54); margin: 0; }
.mat-card-content { font-size: .875rem; padding-top: 8px; }
.mat-card-actions {
  display: flex; align-items: center; gap: 8px; padding: 8px 0 0; flex-wrap: wrap;
}

/* Buttons */
.mat-raised-button, .mat-mdc-raised-button,
.mat-flat-button, .mat-mdc-unelevated-button {
  background: var(--odrtj-color-primary-8, #1565c0) !important;
  color: #fff !important;
  box-shadow: 0 3px 1px -2px rgba(0,0,0,.2), 0 2px 2px rgba(0,0,0,.14), 0 1px 5px rgba(0,0,0,.12);
}
.mat-stroked-button, .mat-mdc-outlined-button {
  border: 1px solid rgba(0,0,0,.12) !important;
  background: transparent !important;
  color: var(--odrtj-color-primary-8, #1565c0) !important;
}
.mat-icon-button, .mat-mdc-icon-button {
  padding: 8px; min-width: 40px; min-height: 40px;
  border-radius: 50%; background: transparent;
}
.mat-fab, .mat-mdc-fab {
  width: 56px; height: 56px; border-radius: 50%;
  background: var(--odrtj-color-primary-8, #1565c0) !important; color: #fff !important;
}

/* Form fields */
.mat-form-field, .mat-mdc-form-field {
  display: inline-flex !important; flex-direction: column;
  min-width: 180px; font-size: 14px; margin: 6px 0;
}
.mat-form-field-wrapper, .mat-mdc-text-field-wrapper {
  background: rgba(0,0,0,.04); border-radius: 4px 4px 0 0;
  padding: 8px 12px 4px; border-bottom: 1px solid rgba(0,0,0,.42);
}
.mat-form-field-label, .mat-mdc-floating-label, .mdc-floating-label {
  font-size: 12px; color: rgba(0,0,0,.6); display: block; margin-bottom: 4px;
}
.mat-input-element, .mat-mdc-input-element {
  border: none; background: transparent; font-size: 14px;
  font-family: inherit; padding: 4px 0; width: 100%; outline: none;
}
.mat-select-trigger, .mat-mdc-select-trigger {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; padding: 4px 0; font-size: 14px;
}
.mat-error, .mat-mdc-form-field-error { font-size: 12px; color: #b00020; margin-top: 4px; }
.mat-hint, .mat-mdc-form-field-hint { font-size: 12px; color: rgba(0,0,0,.6); margin-top: 4px; }

/* Lists */
.mat-nav-list, .mat-list, .mat-mdc-list { padding: 0; list-style: none; }
.mat-list-item, .mat-mdc-list-item {
  display: flex !important; align-items: center; gap: 16px;
  min-height: 48px; padding: 0 16px; cursor: pointer;
  transition: background .15s; border-radius: 4px; margin: 2px 0;
}
.mat-list-item:hover, .mat-mdc-list-item:hover { background: rgba(0,0,0,.04); }
.mat-list-icon { color: rgba(0,0,0,.54); flex-shrink: 0; }
.mat-line, .mdc-list-item__primary-text {
  font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mat-line:last-child:not(:first-child) { font-size: 12px; color: rgba(0,0,0,.54); }

/* Tables */
.mat-table, .mat-mdc-table {
  display: table !important; width: 100%; border-collapse: collapse;
  background: #fff; border-radius: 4px; overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,.1);
}
.mat-header-row, .mat-mdc-header-row { display: table-row !important; background: #fafafa; }
.mat-row, .mat-mdc-row { display: table-row !important; transition: background .1s; }
.mat-row:hover, .mat-mdc-row:hover { background: rgba(0,0,0,.02); }
.mat-cell, .mat-mdc-cell, .mat-header-cell, .mat-mdc-header-cell {
  display: table-cell !important; padding: 12px 16px;
  border-bottom: 1px solid rgba(0,0,0,.08); font-size: 14px; text-align: left;
}
.mat-header-cell, .mat-mdc-header-cell {
  font-size: 12px; font-weight: 500; color: rgba(0,0,0,.54);
  border-bottom: 2px solid rgba(0,0,0,.12);
}
.mat-footer-row, .mat-mdc-footer-row { display: table-row !important; font-weight: 500; }
.mat-no-data-row { display: table-row !important; }

/* Tabs */
.mat-tab-group, .mat-mdc-tab-group {
  display: flex !important; flex-direction: column; width: 100%;
}
.mat-tab-header, .mat-mdc-tab-header {
  display: flex !important; border-bottom: 2px solid rgba(0,0,0,.12); overflow: hidden;
}
.mat-tab-label, .mat-mdc-tab {
  display: inline-flex !important; align-items: center; justify-content: center;
  padding: 0 24px; min-width: 80px; height: 48px; cursor: pointer;
  font-size: 14px; font-weight: 500; letter-spacing: .089em; text-transform: uppercase;
  color: rgba(0,0,0,.6); border-bottom: 2px solid transparent; margin-bottom: -2px;
}
.mat-tab-label:first-child {
  color: var(--odrtj-color-primary-8, #1565c0);
  border-bottom-color: var(--odrtj-color-primary-8, #1565c0);
}
.mat-tab-body { padding: 16px 0; }

/* Expansion panels */
.mat-expansion-panel, .mat-mdc-expansion-panel {
  display: block !important; background: #fff; border-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 4px; overflow: hidden;
}
.mat-expansion-panel-header, .mat-mdc-expansion-panel-header {
  display: flex !important; align-items: center; justify-content: space-between;
  padding: 0 24px; min-height: 48px; cursor: pointer; font-weight: 500;
  background: #fff; transition: background .15s;
}
.mat-expansion-panel-header:hover { background: rgba(0,0,0,.04); }
.mat-expansion-panel-body { padding: 0 24px 16px; font-size: 14px; }
.mat-expansion-indicator::after { content: '▾'; font-size: 12px; color: rgba(0,0,0,.54); }

/* Chips */
.mat-chip-list, .mat-mdc-chip-set, .mat-chip-listbox {
  display: flex !important; flex-wrap: wrap; gap: 8px; padding: 4px 0; align-items: center;
}
.mat-chip, .mat-mdc-chip, .mat-basic-chip {
  display: inline-flex !important; align-items: center; padding: 6px 12px;
  border-radius: 16px; font-size: 13px; background: rgba(0,0,0,.08); gap: 4px;
}
.mat-chip-selected, .mat-mdc-chip-selected {
  background: var(--odrtj-color-primary-8, #1565c0) !important; color: #fff !important;
}

/* Menus */
.mat-menu-panel, .mat-mdc-menu-panel {
  background: #fff; border-radius: 4px; min-width: 112px; max-width: 280px;
  overflow: hidden;
  box-shadow: 0 5px 5px -3px rgba(0,0,0,.2), 0 8px 10px 1px rgba(0,0,0,.14), 0 3px 14px 2px rgba(0,0,0,.12);
}
.mat-menu-item, .mat-mdc-menu-item {
  display: flex !important; align-items: center; gap: 16px;
  padding: 0 16px; min-height: 48px; font-size: 14px; cursor: pointer;
  background: transparent; border: none; width: 100%; transition: background .15s;
}
.mat-menu-item:hover { background: rgba(0,0,0,.04); }

/* Progress */
.mat-progress-bar, .mat-mdc-progress-bar {
  display: block !important; height: 4px; width: 100%;
  background: rgba(21,101,192,.25); border-radius: 2px; overflow: hidden; position: relative;
}
.mat-progress-bar::after, .mat-mdc-progress-bar::after {
  content: ''; display: block; height: 100%;
  background: var(--odrtj-color-primary-8, #1565c0); width: 60%;
}
.mat-progress-spinner, .mat-mdc-progress-spinner {
  display: inline-block !important; width: 40px; height: 40px;
  border: 4px solid rgba(0,0,0,.1);
  border-top-color: var(--odrtj-color-primary-8, #1565c0);
  border-radius: 50%;
}

/* Paginator */
.mat-paginator, .mat-mdc-paginator {
  display: flex !important; align-items: center; justify-content: flex-end;
  padding: 0 8px; min-height: 56px; font-size: 12px;
  color: rgba(0,0,0,.54); background: #fff;
  border-top: 1px solid rgba(0,0,0,.12);
}

/* Slide toggle / Checkbox / Radio */
.mat-slide-toggle, .mat-mdc-slide-toggle {
  display: inline-flex !important; align-items: center; gap: 8px; cursor: pointer; font-size: 14px;
}
.mat-slide-toggle-bar {
  display: inline-block; width: 36px; height: 20px;
  background: rgba(0,0,0,.38); border-radius: 10px;
}
.mat-checkbox, .mat-mdc-checkbox {
  display: inline-flex !important; align-items: center; gap: 8px; cursor: pointer; font-size: 14px;
}
.mat-checkbox-inner-container {
  width: 18px; height: 18px; border: 2px solid rgba(0,0,0,.54); border-radius: 2px; flex-shrink: 0;
}
.mat-checkbox-checked .mat-checkbox-inner-container {
  background: var(--odrtj-color-primary-8, #1565c0);
  border-color: var(--odrtj-color-primary-8, #1565c0);
}
.mat-radio-button, .mat-mdc-radio-button {
  display: inline-flex !important; align-items: center; gap: 8px; cursor: pointer; font-size: 14px;
}
.mat-radio-outer-circle {
  width: 20px; height: 20px; border: 2px solid rgba(0,0,0,.54); border-radius: 50%; flex-shrink: 0;
}

/* Divider */
.mat-divider, .mat-mdc-divider {
  display: block !important; border-top: 1px solid rgba(0,0,0,.12); margin: 8px 0;
}
.mat-divider-vertical {
  border-top: none; border-left: 1px solid rgba(0,0,0,.12); height: 100%; margin: 0 8px;
}

/* Options / Select */
.mat-option, .mat-mdc-option {
  display: flex !important; align-items: center; gap: 12px;
  padding: 0 16px; min-height: 48px; font-size: 14px; cursor: pointer; transition: background .15s;
}
.mat-option:hover { background: rgba(0,0,0,.04); }
.mat-option-selected { background: rgba(21,101,192,.08) !important; color: var(--odrtj-color-primary-8, #1565c0); }

/* Stepper */
.mat-stepper-horizontal, .mat-mdc-stepper-horizontal { display: flex !important; flex-direction: column; }
.mat-horizontal-stepper-header-container {
  display: flex; align-items: center; padding: 0 16px; border-bottom: 1px solid rgba(0,0,0,.12);
}
.mat-step-header { display: flex; align-items: center; gap: 8px; padding: 16px; cursor: pointer; }
.mat-step-icon {
  width: 24px; height: 24px; border-radius: 50%; background: rgba(0,0,0,.38);
  color: #fff; display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; flex-shrink: 0;
}
.mat-step-icon-selected { background: var(--odrtj-color-primary-8, #1565c0) !important; }
.mat-step-label { font-size: 14px; }
.mat-step-label-selected { font-weight: 500; }

/* Autocomplete */
.mat-autocomplete-panel, .mat-mdc-autocomplete-panel {
  background: #fff; border-radius: 0 0 4px 4px;
  box-shadow: 0 4px 8px rgba(0,0,0,.2); overflow: auto;
}

/* Badge */
.mat-badge-content {
  background: var(--odrtj-color-primary-8, #1565c0); color: #fff; border-radius: 50%;
  font-size: 11px; font-weight: 600; width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
}

/* Tooltip */
.mat-tooltip, .mdc-tooltip {
  background: rgba(97,97,97,.9); color: #fff; border-radius: 4px;
  padding: 6px 8px; font-size: 12px; max-width: 250px;
}

/* Sort header */
.mat-sort-header { display: flex !important; align-items: center; gap: 4px; }
.mat-sort-header-arrow { font-size: 10px; color: rgba(0,0,0,.54); }

/* Preview header (identifica o componente para designers) */
.a11y-preview-header {
  position: sticky; top: 0; z-index: 1000;
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 12px; background: #fff3e0; border-bottom: 2px solid #ff9800;
  font-family: monospace; font-size: 12px; gap: 12px; flex-wrap: wrap;
}
.a11y-preview-title { font-weight: 600; color: #e65100; }
.a11y-preview-badge {
  background: #ff9800; color: #fff; padding: 2px 8px;
  border-radius: 10px; font-size: 11px; white-space: nowrap;
}
"""


def build_angular_screenshot_harness(
    template_html: str,
    filename: str,
    assets_dir: "Path | None" = None,
    extra_css: str = "",
) -> str:
    """
    Gera HTML harness otimizado para screenshot de componentes Angular.

    Todo o CSS é embutido inline (sem CDN) para que o Playwright possa capturar
    o screenshot com `domcontentloaded` em vez de `networkidle`, eliminando
    o timeout de 45s que ocorria quando CDN estava indisponível ou lento.

    Quando *assets_dir* é fornecido:
      - A Material Icons font é carregada localmente via HTTP server symlink
      - Imagens src="assets/..." resolvem via http://127.0.0.1:PORT/assets/...
        (orquestrador cria symlink `assets/` → pasta real de assets do projeto)

    *extra_css*: CSS adicional a injetar (CSS vars do projeto, Bootstrap utilities,
    SCSS compilado dos componentes). Injeta como bloco <style> separado no final do <head>.
    """
    clean = strip_angular_template_syntax(template_html)
    clean = _MAT_BUTTON_ATTRS.sub('', clean)
    safe_filename = filename.replace("<", "&lt;").replace(">", "&gt;")

    # Detectar se assets locais estão disponíveis
    has_material_icons_font = (
        assets_dir is not None
        and (assets_dir / "material-icons.woff2").exists()
    )

    # ── Sem CDN: todo CSS embutido inline, sem dependências de rede ─────────
    # Elimina o timeout de 45s que o networkidle causava quando CDN estava
    # indisponível ou lento. Material Icons carregadas localmente via HTTP server.
    resource_links = ""
    if has_material_icons_font:
        resource_links = """<style>
  @font-face {
    font-family: "Material Icons";
    font-style: normal;
    font-weight: 400;
    src: url("assets/material-icons.woff2") format("woff2");
  }
  .material-icons {
    font-family: "Material Icons";
    font-weight: normal; font-style: normal; font-size: 24px; line-height: 1;
    letter-spacing: normal; text-transform: none; display: inline-block;
    white-space: nowrap; word-wrap: normal; direction: ltr;
    -webkit-font-feature-settings: "liga"; font-feature-settings: "liga";
    vertical-align: middle;
  }
</style>"""
    body_font = (
        "font-family: Roboto, -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;"
    )

    # Build extra_css block outside the f-string to avoid escaping issues
    extra_css_block = (
        f'<style id="extra-css">\n{extra_css}\n</style>'
        if extra_css.strip()
        else ''
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>a11y preview: {safe_filename}</title>
  {resource_links}
  <style>
    /* ── Reset ── */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    /* ── Tipografia ── */
    body {{
      {body_font}
      font-size: 14px;
      line-height: 1.5;
      background: #fafafa;
      color: #212121;
    }}

    #root {{ padding: 16px 20px; min-height: 40px; }}

    /* ── Toolbar / Header ── minimal fallback only; component CSS takes priority */
    mat-toolbar, .mat-toolbar {{
      display: flex;
      align-items: center;
      flex-shrink: 0;
    }}

    /* ── Botões ── */
    button, a[role="button"] {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: inherit;
      cursor: pointer;
    }}

    /* ── Inputs ── */
    input, select, textarea {{
      display: block;
      width: 100%;
      padding: 8px 12px;
      border: 1px solid #9e9e9e;
      border-radius: 4px;
      font-family: inherit;
      font-size: 14px;
      background: #fff;
    }}
    label {{
      display: block;
      font-size: 12px;
      color: #616161;
      margin-bottom: 4px;
    }}

    /* ── Links / Nav ── */
    a {{ color: #1565c0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    nav {{
      display: flex;
      gap: 4px;
      align-items: center;
      flex-wrap: wrap;
    }}

    /* ── Listas ── */
    ul, ol {{ padding-left: 24px; }}
    li {{ margin: 4px 0; }}

    /* ── Cards ── */
    .mat-card, mat-card, [class*="card"] {{
      background: #fff;
      border-radius: 4px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      padding: 16px;
      margin: 8px 0;
    }}

    /* ── Divider ── */
    mat-divider, .mat-divider, hr {{
      display: block;
      border: none;
      border-top: 1px solid #e0e0e0;
      margin: 8px 0;
    }}

    /* ── Chips ── */
    mat-chip, .mat-chip {{
      display: inline-flex;
      align-items: center;
      padding: 4px 12px;
      background: #e0e0e0;
      border-radius: 16px;
      font-size: 13px;
    }}

    /* ── Menu items ── */
    .mat-menu-item, [mat-menu-item] {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      cursor: pointer;
    }}
    .mat-menu-item:hover {{ background: #f5f5f5; }}

    /* ── Ícones Material (gerados pelo preprocessador como .material-icons.a11y-icon) ── */
    /* Quando material-icons.woff2 está carregado: ligatura renderiza o ícone real.   */
    /* Quando offline: font-family fallback não tem a ligatura; o texto do nome fica  */
    /* visível (zoom_in, close, etc.) com estilo compacto monospace.                  */
    .material-icons.a11y-icon,
    .a11y-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      font-size: 18px;
      font-family: "Material Icons", monospace;
      font-feature-settings: "liga";
      -webkit-font-feature-settings: "liga";
      font-style: normal;
      font-weight: normal;
      color: currentColor;
      vertical-align: middle;
      flex-shrink: 0;
      line-height: 1;
      letter-spacing: normal;
      text-transform: none;
      overflow: hidden;
      white-space: nowrap;
    }}

    /* ── Placeholders de estrutura Angular ── */
    .ng-outlet-placeholder,
    .ng-content-placeholder {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 32px;
    }}
    .ng-tpl-preview {{
      border-left: 3px solid #e0e0e0;
      padding: 4px;
      margin: 2px;
      opacity: 0.6;
    }}

    /* ── Imagens ── */
    img {{
      max-width: 100%;
      max-height: 160px;
      width: auto;
      height: auto;
      display: inline-block;
      min-width: 24px;
      min-height: 24px;
      object-fit: contain;
    }}
    /* Esconder imagens sem src (binding [src]="..." foi removido) */
    img:not([src]), img[src=""] {{ display: none !important; }}

    /* ── Placeholder para imagem quebrada (injetado pelo onerror inline) ── */
    .img-broken-placeholder {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 80px;
      min-height: 48px;
      padding: 6px 10px;
      background: #f5f5f5;
      border: 1px dashed #bdbdbd;
      border-radius: 4px;
      color: #9e9e9e;
      font-size: 10px;
      font-family: monospace;
      text-align: center;
      vertical-align: middle;
    }}
    .img-broken-placeholder::before {{
      content: "🖼 ";
    }}

    /* ── Sub-componentes Angular (app-*, lib-*) ──
       Quando a tag é convertida a <div class="ng-app-component"> e está
       vazia (sem filhos textuais) exibe um badge com o nome do componente.
       Quando tem conteúdo interno (slots/templates preenchidos) o badge
       some e deixa o conteúdo mostrar normalmente.                       */
    .ng-app-component {{
      position: relative;
    }}
    .ng-app-component > .ng-app-label {{
      display: inline-block;
      font-size: 10px;
      font-family: monospace;
      color: #0277bd;
      background: #e1f5fe;
      border: 1px dashed #0288d1;
      border-radius: 3px;
      padding: 2px 6px;
      margin: 2px;
      white-space: nowrap;
    }}

    /* ── Classes de alto contraste (hc-hide/hc-show) ── */
    /* Em modo normal: hc-hide está visível, hc-show está oculto.
       Sem o CSS da aplicação ambos aparecem — forçamos o comportamento correto. */
    .hc-show {{ display: none !important; }}

    /* ── Visibilidade ── */
    [hidden] {{ display: none !important; }}

    /* ── Bootstrap display utilities (viewport xl ~ 1280px) ─────────────────
       Sem o CSS do Bootstrap, todos os spans responsivos (d-none d-xl-block,
       d-sm-none etc.) ficam visíveis simultaneamente. Reproduzimos as regras
       de display para uma viewport desktop (xl ≥ 1200px), do menos ao mais
       específico — regras xl sobrescrevem as anteriores (mesma especificidade,
       ordem importa no CSS).                                                  */

    /* base */
    .d-none    {{ display: none         !important; }}
    .d-inline  {{ display: inline       !important; }}
    .d-block   {{ display: block        !important; }}
    .d-flex    {{ display: flex         !important; }}
    .d-grid    {{ display: grid         !important; }}
    .d-inline-flex  {{ display: inline-flex  !important; }}
    .d-inline-block {{ display: inline-block !important; }}

    /* sm */
    .d-sm-none {{ display: none !important; }}
    .d-sm-inline  {{ display: inline  !important; }}
    .d-sm-block   {{ display: block   !important; }}
    .d-sm-flex    {{ display: flex    !important; }}
    .d-sm-inline-flex  {{ display: inline-flex  !important; }}
    .d-sm-inline-block {{ display: inline-block !important; }}

    /* md */
    .d-md-none {{ display: none !important; }}
    .d-md-inline  {{ display: inline  !important; }}
    .d-md-block   {{ display: block   !important; }}
    .d-md-flex    {{ display: flex    !important; }}
    .d-md-inline-flex  {{ display: inline-flex  !important; }}
    .d-md-inline-block {{ display: inline-block !important; }}

    /* lg */
    .d-lg-none {{ display: none !important; }}
    .d-lg-inline  {{ display: inline  !important; }}
    .d-lg-block   {{ display: block   !important; }}
    .d-lg-flex    {{ display: flex    !important; }}
    .d-lg-inline-flex  {{ display: inline-flex  !important; }}
    .d-lg-inline-block {{ display: inline-block !important; }}

    /* xl — maior prioridade na folha; sobrescreve regras lg/md/sm acima */
    .d-xl-none {{ display: none !important; }}
    .d-xl-inline  {{ display: inline  !important; }}
    .d-xl-block   {{ display: block   !important; }}
    .d-xl-flex    {{ display: flex    !important; }}
    .d-xl-inline-flex  {{ display: inline-flex  !important; }}
    .d-xl-inline-block {{ display: inline-block !important; }}

    /* Flexbox utilities comuns */
    .flex-fill  {{ flex: 1 1 auto !important; }}
    .flex-grow-1 {{ flex-grow: 1 !important; }}
    .align-items-center  {{ align-items: center  !important; }}
    .align-items-start   {{ align-items: flex-start !important; }}
    .justify-content-between {{ justify-content: space-between !important; }}
    .justify-content-center  {{ justify-content: center !important; }}
    .flex-column {{ flex-direction: column !important; }}
    .flex-wrap   {{ flex-wrap: wrap !important; }}
    .gap-1 {{ gap: 0.25rem !important; }}
    .gap-2 {{ gap: 0.5rem  !important; }}
    .gap-3 {{ gap: 1rem    !important; }}

    /* Width/height utilities */
    .w-100 {{ width: 100% !important; }}
    .h-100 {{ height: 100% !important; }}

    /* Spacing utilities (margin/padding) */
    .m-0  {{ margin:  0     !important; }}
    .p-0  {{ padding: 0     !important; }}
    .mx-2 {{ margin-left: 0.5rem !important; margin-right: 0.5rem !important; }}
    .ml-2 {{ margin-left: 0.5rem !important; }}
    .mr-2 {{ margin-right: 0.5rem !important; }}
    .mt-2 {{ margin-top: 0.5rem !important; }}
    .mb-2 {{ margin-bottom: 0.5rem !important; }}
    .px-2 {{ padding-left: 0.5rem !important; padding-right: 0.5rem !important; }}
    .py-2 {{ padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; }}
  </style>

  <!-- Angular Material component styles (inline, sem CDN) -->
  <style id="mat-components-css">
  {_HARNESS_MAT_CSS}
  </style>

  {extra_css_block}
</head>
<body class="mat-typography light-theme">
  <!-- Cabeçalho de identificação do componente (para referência do designer) -->
  <div class="a11y-preview-header">
    <span class="a11y-preview-title">{safe_filename}</span>
    <span class="a11y-preview-badge">Preview estático Angular</span>
  </div>
  <main id="root" role="main">
    {clean}
  </main>

  <script>
  (function() {{

    var root = document.getElementById('root');
    if (!root) return;

    // ── 1. Nós de texto que são só artefatos de controle Angular ─────────
    var ARTIFACT = /^\s*[\}}\{{@][\s\}}\{{@]*$/;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var empties = [];
    var n;
    while ((n = walker.nextNode())) {{
      if (ARTIFACT.test(n.textContent)) empties.push(n);
    }}
    empties.forEach(function(n) {{ n.textContent = ''; }});

    // ── 2. Injetar badge de nome nos sub-componentes Angular vazios ───────
    // Quando <app-xxx> é convertido a <div class="ng-app-component"> e fica
    // sem conteúdo visível (apenas whitespace/comentários), insere um badge
    // mostrando o nome do componente para que designers vejam a estrutura.
    document.querySelectorAll('.ng-app-component').forEach(function(el) {{
      var comp = el.getAttribute('data-component') || '';
      if (!comp) return;
      // Verifica se tem conteúdo significativo (texto ou imagens/ícones visíveis)
      var hasContent = false;
      el.childNodes.forEach(function(c) {{
        if (c.nodeType === 1) {{ // Element
          var s = window.getComputedStyle(c);
          if (s.display !== 'none' && s.visibility !== 'hidden') hasContent = true;
        }} else if (c.nodeType === 3 && c.textContent.trim()) {{ // Text
          hasContent = true;
        }}
      }});
      if (!hasContent) {{
        var badge = document.createElement('span');
        badge.className = 'ng-app-label';
        badge.textContent = '<' + comp + '>';
        el.appendChild(badge);
      }}
    }});
  }})();
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
