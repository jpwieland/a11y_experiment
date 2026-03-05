"""Utilitários para operações Git (branches, commits, PRs)."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def is_git_repo(path: Path) -> bool:
    """Verifica se o diretório é um repositório Git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=path, check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def create_branch(branch_name: str, base: str = "HEAD", cwd: Path | None = None) -> bool:
    """
    Cria uma nova branch a partir de base.

    Args:
        branch_name: Nome da nova branch.
        base: Commit/branch base.
        cwd: Diretório de trabalho.

    Returns:
        True se criado com sucesso.
    """
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name, base],
            capture_output=True, text=True, cwd=cwd, check=True,
        )
        log.info("branch_created", branch=branch_name)
        return True
    except subprocess.CalledProcessError as e:
        log.error("branch_create_failed", branch=branch_name, error=e.stderr)
        return False


def commit_changes(message: str, files: list[Path], cwd: Path | None = None) -> bool:
    """
    Faz commit de arquivos específicos.

    Args:
        message: Mensagem de commit.
        files: Arquivos a commitar.
        cwd: Diretório de trabalho.

    Returns:
        True se commit foi bem-sucedido.
    """
    try:
        for f in files:
            subprocess.run(
                ["git", "add", str(f)],
                capture_output=True, cwd=cwd, check=True,
            )
        subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=cwd, check=True,
        )
        log.info("commit_created", files=len(files))
        return True
    except subprocess.CalledProcessError as e:
        stderr = getattr(e, "stderr", "")
        log.error("commit_failed", error=stderr)
        return False


def create_pr_gh(
    title: str, body: str, base: str = "main", cwd: Path | None = None
) -> str | None:
    """
    Cria Pull Request via GitHub CLI (gh).

    Args:
        title: Título do PR.
        body: Corpo/descrição do PR.
        base: Branch base para o PR.
        cwd: Diretório de trabalho.

    Returns:
        URL do PR criado, ou None se falhou.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--base", base],
            capture_output=True, text=True, cwd=cwd, check=True,
        )
        pr_url = result.stdout.strip()
        log.info("pr_created", url=pr_url)
        return pr_url
    except subprocess.CalledProcessError as e:
        log.error("pr_create_failed", error=e.stderr)
        return None
    except FileNotFoundError:
        log.error("gh_not_found", hint="Install GitHub CLI: https://cli.github.com/")
        return None


def get_unified_diff(original: str, modified: str, filename: str) -> str:
    """
    Gera diff em formato unified entre dois conteúdos.

    Args:
        original: Conteúdo original.
        modified: Conteúdo modificado.
        filename: Nome do arquivo (para o header do diff).

    Returns:
        String diff em formato unified.
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)
