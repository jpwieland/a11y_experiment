"""Integração do ValidationPipeline no fix loop (_fix_file).

Antes de 06/2026 o ValidationPipeline existia mas nunca era executado:
patches eram gravados em disco e creditados como correções sem qualquer
verificação. Estes testes garantem que:

1. Patch que passa nas 4 camadas → arquivo gravado, issues creditados
   conforme re-scan (ou otimista quando verify_fixes_by_rescan=False)
2. Patch rejeitado (Camada 1/2/4) → arquivo NÃO modificado, tentativa
   registrada como falha com a camada e o motivo, retry acontece
3. Rejeição na Camada 2 produz error com prefixo "functional_regression:"
   (contrato da métrica ρ/H5 em experiments/metrics.py)
4. Re-scan credita apenas issues que desapareceram de fato
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_autofix.config import (
    A11yIssue,
    AgentType,
    Complexity,
    Confidence,
    IssueType,
    ModelConfig,
    PatchResult,
    ScanResult,
    Settings,
)
from a11y_autofix.pipeline import Pipeline

ORIGINAL = """export default function Card({ onClick }: { onClick: () => void }) {
  return (
    <div onClick={onClick}>
      <img src="/x.png" />
    </div>
  );
}
"""

# Patch válido: adiciona alt, preserva export/handler
GOOD_PATCH = ORIGINAL.replace('<img src="/x.png" />', '<img src="/x.png" alt="Product photo" />')

# Regressão funcional: remove o export default (Camada 2)
REGRESSION_PATCH = GOOD_PATCH.replace("export default function Card", "function Card")

# Lixo sintático: sem JSX (Camada 1)
GARBAGE_PATCH = "I cannot fix this file, as an AI I don't have access to the codebase."

# Violação de qualidade: dangerouslySetInnerHTML (Camada 4)
QUALITY_PATCH = GOOD_PATCH.replace(
    "<div onClick={onClick}>",
    '<div onClick={onClick} dangerouslySetInnerHTML={{__html: html}}>',
)


def _issue(file: Path, selector: str = "img", itype: IssueType = IssueType.ALT_TEXT,
           wcag: str = "1.1.1") -> A11yIssue:
    return A11yIssue(
        file=str(file),
        selector=selector,
        issue_type=itype,
        complexity=Complexity.SIMPLE,
        wcag_criteria=wcag,
        confidence=Confidence.HIGH,
        message="Images must have alternative text",
    ).compute_id()


class _StubAgent:
    """Agente que devolve uma sequência pré-definida de patches."""

    def __init__(self, patches: list[PatchResult]):
        self._patches = list(patches)
        self.calls = 0

    def name(self) -> str:
        return "stub"

    async def run(self, task) -> PatchResult:
        self.calls += 1
        if self._patches:
            return self._patches.pop(0)
        return PatchResult(success=False, error="exhausted")


def _make_pipeline(tmp_path: Path, rescan_issues_fn=None, verify_rescan=True) -> Pipeline:
    """Pipeline com LLM/scanner neutralizados para teste de _fix_file."""
    settings = Settings()
    settings = settings.model_copy(update={"verify_fixes_by_rescan": verify_rescan,
                                           "max_retries_per_agent": 3})
    pipeline = Pipeline(
        settings=settings,
        model_config=ModelConfig(name="mock", model_id="mock", backend="ollama"),
        agent_preference=AgentType.DIRECT_LLM,
    )

    if rescan_issues_fn is not None:
        class _StubScanner:
            async def scan_file(self, file, wcag, screenshot_dir=None):
                return ScanResult(
                    file=file, file_hash="sha256:stub",
                    issues=rescan_issues_fn(file),
                    tools_used=[], tool_versions={},
                )
        pipeline.scanner = _StubScanner()

    return pipeline


def _scan_result(file: Path) -> ScanResult:
    return ScanResult(
        file=file, file_hash="sha256:orig",
        issues=[_issue(file)],
        tools_used=[], tool_versions={},
    )


@pytest.fixture
def component(tmp_path: Path) -> Path:
    f = tmp_path / "Card.tsx"
    f.write_text(ORIGINAL, encoding="utf-8")
    return f


@pytest.mark.anyio
class TestValidationGate:
    async def test_good_patch_passes_and_is_written(self, component, monkeypatch):
        # Re-scan: nenhum issue restante → tudo verificado como corrigido
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=lambda f: [])
        agent = _StubAgent([PatchResult(success=True, new_content=GOOD_PATCH, diff="+alt")])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert result.final_success is True
        assert result.issues_fixed == 1
        assert result.attempts[0].validation_passed is True
        assert result.attempts[0].validation_rejected_layer is None
        assert component.read_text(encoding="utf-8") == GOOD_PATCH

    async def test_garbage_rejected_layer1_file_untouched(self, component, monkeypatch):
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=lambda f: [])
        agent = _StubAgent([
            PatchResult(success=True, new_content=GARBAGE_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert result.final_success is False
        assert result.issues_fixed == 0
        first = result.attempts[0]
        assert first.success is False
        assert first.validation_passed is False
        assert first.validation_rejected_layer == 1
        # Arquivo permanece intacto
        assert component.read_text(encoding="utf-8") == ORIGINAL
        # Retry aconteceu (agente foi chamado mais de uma vez até esgotar)
        assert agent.calls == 3

    async def test_regression_rejected_layer2_with_rho_contract(self, component, monkeypatch):
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=lambda f: [])
        agent = _StubAgent([
            PatchResult(success=True, new_content=REGRESSION_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        first = result.attempts[0]
        assert first.validation_rejected_layer == 2
        # Contrato da métrica ρ (H5): prefixo functional_regression:
        assert first.error.startswith("functional_regression:")
        assert component.read_text(encoding="utf-8") == ORIGINAL

    async def test_quality_rejected_layer4(self, component, monkeypatch):
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=lambda f: [])
        agent = _StubAgent([
            PatchResult(success=True, new_content=QUALITY_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert result.attempts[0].validation_rejected_layer == 4
        assert component.read_text(encoding="utf-8") == ORIGINAL

    async def test_retry_after_rejection_then_success(self, component, monkeypatch):
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=lambda f: [])
        agent = _StubAgent([
            PatchResult(success=True, new_content=REGRESSION_PATCH, diff="x"),
            PatchResult(success=True, new_content=GOOD_PATCH, diff="+alt"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert len(result.attempts) == 2
        assert result.attempts[0].validation_rejected_layer == 2
        assert result.attempts[1].validation_passed is True
        assert result.final_success is True
        assert component.read_text(encoding="utf-8") == GOOD_PATCH


@pytest.mark.anyio
class TestRescanCrediting:
    async def test_rescan_credits_only_resolved_issues(self, component, monkeypatch):
        """Patch validado mas re-scan mostra que o issue persiste → não credita."""
        # Re-scan devolve o MESMO issue (não foi corrigido de verdade)
        pipeline = _make_pipeline(
            component.parent,
            rescan_issues_fn=lambda f: [_issue(f)],
        )
        agent = _StubAgent([
            PatchResult(success=True, new_content=GOOD_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        # Patch passou na validação e foi gravado…
        assert result.attempts[0].validation_passed is True
        assert component.read_text(encoding="utf-8") == GOOD_PATCH
        # …mas o issue NÃO é creditado (re-scan ainda o encontra)
        assert result.issues_fixed == 0
        assert result.final_success is False

    async def test_optimistic_crediting_when_rescan_disabled(self, component, monkeypatch):
        pipeline = _make_pipeline(component.parent, verify_rescan=False)
        agent = _StubAgent([
            PatchResult(success=True, new_content=GOOD_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert result.issues_fixed == 1
        assert result.final_success is True

    async def test_rescan_failure_is_conservative(self, component, monkeypatch):
        """Se o re-scan explode, NÃO credita (conservador)."""
        def _boom(f):
            raise RuntimeError("scanner crashed")
        pipeline = _make_pipeline(component.parent, rescan_issues_fn=_boom)
        agent = _StubAgent([
            PatchResult(success=True, new_content=GOOD_PATCH, diff="x"),
        ])
        monkeypatch.setattr(pipeline, "_create_agent", lambda name: agent)

        result = await pipeline._fix_file(_scan_result(component), "WCAG2AA")

        assert result.issues_fixed == 0
