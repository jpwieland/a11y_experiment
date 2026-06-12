"""Utilitários para detecção e extração de templates de componentes Angular."""

from __future__ import annotations

import re
from pathlib import Path

# Sufixos de arquivo Angular que NÃO são componentes renderizáveis
_NON_COMPONENT_SUFFIXES = (
    ".service.ts",
    ".module.ts",
    ".pipe.ts",
    ".guard.ts",
    ".interceptor.ts",
    ".directive.ts",
    ".routing.ts",
    ".resolver.ts",
    ".model.ts",
    ".interface.ts",
    ".enum.ts",
    ".util.ts",
    ".helper.ts",
    ".spec.ts",
    ".test.ts",
    ".d.ts",
)

# Partes de path que indicam diretórios não-renderizáveis
_NON_COMPONENT_PATH_PARTS = {
    # Artefatos de build e dependências
    "node_modules", "dist", "build", "__pycache__",
    "coverage", ".cache",
    # Conteúdo sem templates renderizáveis
    "models", "interfaces", "enums", "i18n", "18n",
    # Controle de versão e dotdirs de ferramentas
    ".git", ".github", ".gitlab",
    ".claude", ".idea", ".vscode", ".cursor",
    ".nx", ".angular", ".husky",
    # Testes e2e
    "e2e",
}

_ANGULAR_COMPONENT_RE = re.compile(r"@Component\s*\(", re.MULTILINE)
_ANGULAR_IMPORT_RE = re.compile(r"from\s+['\"]@angular/", re.MULTILINE)
_NG_MODULE_RE = re.compile(r"@NgModule\s*\(", re.MULTILINE)

# templateUrl: './foo.component.html'  (aspas simples, duplas ou backtick)
_TEMPLATE_URL_RE = re.compile(
    r"templateUrl\s*:\s*['\"`]([^'\"`]+)['\"`]",
    re.MULTILINE,
)

# template inline com backtick — captura conteúdo entre backticks
_TEMPLATE_BACKTICK_RE = re.compile(
    r"template\s*:\s*`([\s\S]*?)`",
    re.MULTILINE,
)

# template inline com aspas simples ou duplas
_TEMPLATE_QUOTE_RE = re.compile(
    r"""template\s*:\s*(['"])([\s\S]*?)\1""",
    re.MULTILINE,
)


def detect_framework(file_path: Path, code: str) -> str:
    """
    Detecta o framework frontend de um arquivo TypeScript/JavaScript.

    Ordem de verificação: Angular tem precedência sobre React porque
    projetos Angular também usam .ts (que poderia confundir heurísticas React).

    Returns:
        'angular' | 'react' | 'unknown'
    """
    if (
        _ANGULAR_COMPONENT_RE.search(code)
        or _ANGULAR_IMPORT_RE.search(code)
        or _NG_MODULE_RE.search(code)
    ):
        return "angular"

    if (
        file_path.suffix in {".tsx", ".jsx"}
        or re.search(r"from\s+['\"]react['\"]", code)
        or re.search(r"React\.createElement", code)
        or re.search(r"<[A-Z][A-Za-z0-9]*[\s/>]", code)
    ):
        return "react"

    return "unknown"


def extract_angular_template(component_ts_code: str, component_file_path: Path) -> str | None:
    """
    Extrai o template HTML de um @Component Angular.

    Prioridade:
    1. templateUrl → lê o .html do disco relativo ao .ts
    2. template com backtick inline
    3. template com aspas inline

    Returns:
        String com o HTML do template, ou None se não encontrado.
    """
    if not _ANGULAR_COMPONENT_RE.search(component_ts_code):
        return None

    # 1. templateUrl
    url_match = _TEMPLATE_URL_RE.search(component_ts_code)
    if url_match:
        template_url = url_match.group(1).strip()
        template_path = component_file_path.parent / template_url
        content = _read_safe(template_path)
        if content:
            return content
        # templateUrl declarado mas arquivo não encontrado: continua para inline

    # 2. template backtick
    backtick_match = _TEMPLATE_BACKTICK_RE.search(component_ts_code)
    if backtick_match:
        return backtick_match.group(1).strip()

    # 3. template aspas
    quote_match = _TEMPLATE_QUOTE_RE.search(component_ts_code)
    if quote_match:
        return quote_match.group(2).strip()

    return None


def find_angular_components(base_path: Path, recursive: bool = True) -> list[Path]:
    """
    Descobre arquivos .ts que são componentes Angular com template detectável.

    Critérios de inclusão:
    - Contém @Component(
    - Tem templateUrl (com .html legível) ou template inline
    - NÃO é service, module, pipe, guard, interceptor, directive, routing, model, etc.

    Returns:
        Lista determinística (sorted) de Paths de componentes renderizáveis.
    """
    if base_path.is_file():
        if _is_renderable_candidate(base_path):
            code = _read_safe(base_path)
            if code and extract_angular_template(code, base_path) is not None:
                return [base_path]
        return []

    if not base_path.is_dir():
        return []

    glob_fn = base_path.rglob if recursive else base_path.glob
    candidates = sorted(glob_fn("*.ts"))

    results: list[Path] = []
    for candidate in candidates:
        if not _is_renderable_candidate(candidate):
            continue
        code = _read_safe(candidate)
        if not code:
            continue
        if extract_angular_template(code, candidate) is not None:
            results.append(candidate)

    return results


# ── Helpers privados ────────────────────────────────────────────────────────

def _is_renderable_candidate(path: Path) -> bool:
    """Retorna True se o arquivo pode ser um componente Angular pelo nome/path."""
    name = path.name
    if not name.endswith(".ts"):
        return False
    if any(name.endswith(suffix) for suffix in _NON_COMPONENT_SUFFIXES):
        return False
    if any(part in _NON_COMPONENT_PATH_PARTS for part in path.parts):
        return False
    return True


def _read_safe(path: Path) -> str:
    """Lê arquivo com fallback de encoding; retorna string vazia em erro."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""
