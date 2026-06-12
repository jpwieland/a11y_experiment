"""Testes do DeepReportGenerator (relatório profundo de fim de experimento)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from a11y_autofix.config import (
    A11yIssue,
    Complexity,
    Confidence,
    FixAttempt,
    FixResult,
    IssueType,
    ScanResult,
)
from a11y_autofix.reporter.deep_report import DeepReportGenerator


def _issue(file: str, itype: IssueType, wcag: str, selector: str = "div") -> A11yIssue:
    return A11yIssue(
        file=file, selector=selector, issue_type=itype,
        complexity=Complexity.SIMPLE, wcag_criteria=wcag,
        confidence=Confidence.HIGH, message=f"violação {wcag}",
    ).compute_id()


def _attempt(n: int, success: bool, error: str | None = None,
             layer: int | None = None, tokens: int = 500) -> FixAttempt:
    return FixAttempt(
        attempt_number=n, agent="swe-agent", model="m1",
        timestamp=datetime.now(tz=timezone.utc), success=success,
        diff="+ fix" if success else "", error=error,
        validation_passed=success if (success or layer) else None,
        validation_rejected_layer=layer,
        tokens_prompt=tokens, tokens_used=tokens + 200, time_seconds=2.0,
    )


def _results_for_model(seed: int) -> list[FixResult]:
    """3 arquivos: sucesso total, rejeição L2 + retry ok, falha total."""
    f1 = f"/p/Comp{seed}A.tsx"
    r1 = FixResult(
        file=Path(f1),
        scan_result=ScanResult(
            file=Path(f1), file_hash="h",
            issues=[_issue(f1, IssueType.ALT_TEXT, "1.1.1"),
                    _issue(f1, IssueType.ARIA, "4.1.2", "button")],
            tools_used=[], tool_versions={},
        ),
        attempts=[_attempt(1, True)],
        final_success=True, issues_fixed=2, issues_pending=0, total_time=4.0,
    )

    f2 = f"/p/Comp{seed}B.tsx"
    r2 = FixResult(
        file=Path(f2),
        scan_result=ScanResult(
            file=Path(f2), file_hash="h",
            issues=[_issue(f2, IssueType.CONTRAST, "1.4.3", "p")],
            tools_used=[], tool_versions={},
        ),
        attempts=[
            _attempt(1, False, error="functional_regression:export_signature", layer=2),
            _attempt(2, True),
        ],
        final_success=True, issues_fixed=1, issues_pending=0, total_time=8.0,
    )

    f3 = f"/p/Comp{seed}C.tsx"
    r3 = FixResult(
        file=Path(f3),
        scan_result=ScanResult(
            file=Path(f3), file_hash="h",
            issues=[_issue(f3, IssueType.KEYBOARD, "2.1.1", "div.menu")],
            tools_used=[], tool_versions={},
        ),
        attempts=[
            _attempt(1, False, error="SWE: no valid code or patches found in response"),
            _attempt(2, False, error="llm_refusal", layer=1),
            _attempt(3, False, error="timeout after 180s"),
        ],
        final_success=False, issues_fixed=0, issues_pending=1, total_time=30.0,
    )
    return [r1, r2, r3]


class TestDeepReport:
    def _generate(self, tmp_path: Path) -> dict:
        reps = [
            {"model-a": _results_for_model(1), "model-b": _results_for_model(2)},
            {"model-a": _results_for_model(1), "model-b": _results_for_model(2)},
        ]
        json_path = DeepReportGenerator().generate(
            all_rep_results=reps,
            output_dir=tmp_path,
            experiment_name="Teste Deep",
            experiment_id="abc123",
            config_snapshot={"strategy": "few-shot", "seed": 42},
            tool_versions={"axe-core": "4.9"},
            files_count=3,
        )
        return json.loads(json_path.read_text(encoding="utf-8"))

    def test_generates_three_formats(self, tmp_path: Path):
        self._generate(tmp_path)
        assert (tmp_path / "deep_report.json").exists()
        assert (tmp_path / "deep_report.md").exists()
        assert (tmp_path / "deep_report.html").exists()

    def test_stability_across_repetitions(self, tmp_path: Path):
        report = self._generate(tmp_path)
        stab = report["stability_across_repetitions"]["model-a"]
        # 2 reps idênticas → sd = 0
        assert stab["sr"]["n"] == 2
        assert stab["sr"]["sd"] == 0.0
        # ρ = 1 rejeição L2 / 6 tentativas ≈ 0.1667
        assert abs(stab["rho"]["mean"] - 1 / 6) < 0.001

    def test_validation_layer_breakdown(self, tmp_path: Path):
        report = self._generate(tmp_path)
        layers = report["metrics_last_repetition"]["validation_layers"]["model-a"]
        assert layers["passed"] == 2     # r1.a1 + r2.a2
        assert layers["layer_2"] == 1    # r2.a1
        assert layers["layer_1"] == 1    # r3.a2 (llm_refusal com layer=1)

    def test_failure_taxonomy(self, tmp_path: Path):
        report = self._generate(tmp_path)
        tax = report["failure_taxonomy"]["model-a"]
        assert tax["validacao_camada_2"] == 1
        assert tax["validacao_camada_1"] == 1
        assert tax["timeout"] == 1
        assert tax["llm_sem_codigo_valido"] == 1

    def test_file_drilldown(self, tmp_path: Path):
        report = self._generate(tmp_path)
        rows = [d for d in report["file_drilldown"] if d["model"] == "model-a"]
        assert len(rows) == 3
        failed = next(d for d in rows if not d["final_success"])
        assert failed["issues_fixed"] == 0
        assert failed["attempts"] == 3
        assert "2.1.1" in failed["wcag_criteria"]

    def test_markdown_contains_key_sections(self, tmp_path: Path):
        self._generate(tmp_path)
        md = (tmp_path / "deep_report.md").read_text(encoding="utf-8")
        for section in [
            "# Deep Report — Teste Deep",
            "## 1. Métricas primárias",
            "## 2. Validação em 4 camadas",
            "## 3. Correção por princípio WCAG",
            "## 4. Correção por tipo de issue",
            "## 5. Tentativas, tokens e diffs",
            "## 6. Taxonomia de modos de falha",
            "## 7. Resultados por repetição",
            "## 8. Drill-down por arquivo",
        ]:
            assert section in md, f"seção ausente: {section}"

    def test_html_is_standalone(self, tmp_path: Path):
        self._generate(tmp_path)
        html = (tmp_path / "deep_report.html").read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "<table>" in html
        assert "Validação em 4 camadas" in html
