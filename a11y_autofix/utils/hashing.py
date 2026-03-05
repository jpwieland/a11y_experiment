"""Utilitários para hashing criptográfico de conteúdo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def hash_file(path: Path) -> str:
    """
    Calcula SHA-256 do conteúdo de um arquivo.

    Args:
        path: Caminho do arquivo.

    Returns:
        String hexadecimal SHA-256 prefixada com 'sha256:'.
    """
    return hash_content(path.read_bytes())


def hash_content(content: bytes | str) -> str:
    """
    Calcula SHA-256 de conteúdo arbitrário.

    Args:
        content: Bytes ou string a ser hasheado.

    Returns:
        String hexadecimal SHA-256 prefixada com 'sha256:'.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}"


def stable_issue_id(
    file: str,
    selector: str,
    wcag_criteria: str | None,
    issue_type: str,
) -> str:
    """
    Gera ID estável para um issue baseado em seu conteúdo.

    O ID é determinístico: mesma combinação → mesmo ID entre runs.

    Args:
        file: Caminho do arquivo.
        selector: Seletor CSS do elemento.
        wcag_criteria: Critério WCAG (pode ser None).
        issue_type: Tipo do issue.

    Returns:
        String hexadecimal de 16 caracteres.
    """
    key = f"{file}:{selector}:{wcag_criteria}:{issue_type}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def hash_dict(data: dict[str, object]) -> str:
    """
    Calcula SHA-256 de um dicionário serializado deterministicamente.

    Args:
        data: Dicionário a ser hasheado.

    Returns:
        String hexadecimal SHA-256.
    """
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(serialized.encode()).hexdigest()
