"""
Agente baseline determinístico — ESLint jsx-a11y com autofix.

Representa o estado-da-arte em correção automática de acessibilidade SEM LLM.
Deve ser executado como condição de controle em todos os experimentos para
contextualizar a contribuição real dos modelos LLM.

Limitações conhecidas (documentadas no paper, Seção 3.8):
  - Corrige apenas issues com autofix disponível no plugin eslint-plugin-jsx-a11y
  - Não infere alt-text semântico (apenas força presença do atributo)
  - Não resolve violações de contraste (requer análise CSS em cascata)
  - Não corrige problemas de foco dinâmico (apenas estrutura estática)
  - Cobertura estimada: ~20-35% das violações WCAG AA típicas

Metodologia: C1.3 do PLANO_CORRECAO_METODOLOGICA.md
"""

from __future__ import annotations

import asyncio
import difflib
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from a11y_autofix.config import FixAttempt, FixResult, PatchResult, ScanResult

log = structlog.get_logger(__name__)

# Regras jsx-a11y com suporte a --fix automático
_AUTOFIX_RULES: list[str] = [
    "jsx-a11y/alt-text",
    "jsx-a11y/aria-props",
    "jsx-a11y/aria-role",
    "jsx-a11y/aria-unsupported-elements",
    "jsx-a11y/label-has-associated-control",
    "jsx-a11y/button-has-type",
    "jsx-a11y/anchor-is-valid",
    "jsx-a11y/html-has-lang",
    "jsx-a11y/img-redundant-alt",
    "jsx-a11y/no-access-key",
    "jsx-a11y/no-autofocus",
    "jsx-a11y/no-distracting-elements",
    "jsx-a11y/scope",
    "jsx-a11y/tabindex-no-positive",
]


def _compute_unified_diff(original: str, patched: str, filename: str = "") -> str:
    """Calcula diff unificado entre original e patched."""
    return "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            patched.splitlines(),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )


class DeterministicBaselineAgent:
    """
    Agente baseline determinístico usando eslint --fix com regras jsx-a11y.

    NÃO requer LLM — zero tokens consumidos.
    Usado como condição de controle no ablation study (C1.3).
    """

    name: str = "eslint-autofix-baseline"
    model: str = "deterministic"

    async def fix(
        self,
        file: Path,
        scan_result: ScanResult,
    ) -> FixResult:
        """
        Aplica ESLint autofix no arquivo.

        Args:
            file: Caminho do arquivo TSX/JSX.
            scan_result: Resultado do scan (usado apenas para metadados).

        Returns:
            FixResult com resultado da correção.
        """
        t0 = time.monotonic()
        original = file.read_text(encoding="utf-8")

        patch_result = await self._run_eslint_fix(file, original)
        elapsed = time.monotonic() - t0

        attempt = FixAttempt(
            attempt_number=1,
            agent=self.name,
            model=self.model,
            timestamp=datetime.now(tz=timezone.utc),
            success=patch_result.success,
            diff=patch_result.diff,
            new_content=patch_result.new_content,
            tokens_used=0,       # determinístico — zero tokens
            tokens_prompt=0,
            tokens_completion=0,
            time_seconds=elapsed,
            error=patch_result.error,
        )

        return FixResult(
            file=file,
            scan_result=scan_result,
            attempts=[attempt],
            final_success=patch_result.success,
            issues_fixed=0,   # calculado pelo pipeline de validação (Camada 3)
            issues_pending=len(scan_result.issues),
            total_time=elapsed,
        )

    async def _run_eslint_fix(self, file: Path, original: str) -> PatchResult:
        """
        Executa `npx eslint --fix` no arquivo e retorna o PatchResult.

        Usa timeout de 30s. Se ESLint não estiver disponível, retorna falha
        sem levantar exceção (falha graceful para manter o experimento rodando).
        """
        # Construir argumentos de regras inline
        rule_args: list[str] = []
        for rule in _AUTOFIX_RULES:
            rule_args.extend(["--rule", f'{{"{rule}": "warn"}}'])

        cmd = [
            "npx", "--yes", "eslint",
            "--fix",
            "--parser", "@typescript-eslint/parser",
            "--plugin", "jsx-a11y",
            *rule_args,
            str(file),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(file.parent),
            )
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0
            )

            patched = file.read_text(encoding="utf-8")
            changed = patched != original

            if changed:
                diff = _compute_unified_diff(original, patched, file.name)
                log.info(
                    "eslint_baseline_fixed",
                    file=file.name,
                    diff_lines=len(diff.splitlines()),
                )
                return PatchResult(
                    success=True,
                    new_content=patched,
                    diff=diff,
                    tokens_used=0,
                    tokens_prompt=0,
                    tokens_completion=0,
                )
            else:
                log.debug("eslint_baseline_no_changes", file=file.name)
                return PatchResult(
                    success=False,
                    error="eslint_autofix: no changes made",
                    tokens_used=0,
                )

        except asyncio.TimeoutError:
            log.warning("eslint_baseline_timeout", file=file.name)
            # Garantir que o arquivo não ficou em estado parcial
            file.write_text(original, encoding="utf-8")
            return PatchResult(
                success=False,
                error="eslint_autofix: timeout after 30s",
                tokens_used=0,
            )
        except FileNotFoundError:
            log.warning(
                "eslint_baseline_not_found",
                hint="Install eslint + eslint-plugin-jsx-a11y via npm",
            )
            return PatchResult(
                success=False,
                error="eslint_autofix: npx/eslint not found in PATH",
                tokens_used=0,
            )
        except Exception as exc:
            log.error("eslint_baseline_error", file=file.name, error=str(exc))
            # Restaurar arquivo original em caso de erro inesperado
            try:
                file.write_text(original, encoding="utf-8")
            except Exception:
                pass
            return PatchResult(
                success=False,
                error=f"eslint_autofix: {exc}",
                tokens_used=0,
            )

    async def run_batch(
        self,
        files: list[Path],
        scan_results: dict[str, ScanResult],
    ) -> list[FixResult]:
        """
        Processa múltiplos arquivos em sequência (ESLint não é thread-safe em escrita).

        Args:
            files: Lista de arquivos a processar.
            scan_results: Dicionário path_str → ScanResult.

        Returns:
            Lista de FixResult na mesma ordem de files.
        """
        results: list[FixResult] = []
        for f in files:
            sr = scan_results.get(str(f.resolve())) or scan_results.get(str(f))
            if sr is None:
                log.warning("eslint_baseline_no_scan", file=f.name)
                continue
            result = await self.fix(f, sr)
            results.append(result)
        return results
