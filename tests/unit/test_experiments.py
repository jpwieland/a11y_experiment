"""Testes unitários do sistema de experimentação."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from a11y_autofix.config import FixResult, ScanResult, ScanTool, Settings
from a11y_autofix.experiments.config_schema import ExperimentConfig, load_experiment_config
from a11y_autofix.experiments.metrics import compute_experiment_metrics, rank_models


# ─── Fixtures ────────────────────────────────────────────────────────────────


def make_fix_result(success: bool, issues_fixed: int = 0, time_s: float = 5.0) -> FixResult:
    """Factory de FixResult para testes."""
    scan = ScanResult(
        file=Path("test.tsx"),
        file_hash="sha256:abc",
        issues=[],
        tools_used=[ScanTool.PA11Y],
        tool_versions={},
    )
    return FixResult(
        file=Path("test.tsx"),
        scan_result=scan,
        final_success=success,
        issues_fixed=issues_fixed,
        issues_pending=0 if success else 2,
        total_time=time_s,
    )


# ─── ExperimentConfig ─────────────────────────────────────────────────────────


class TestExperimentConfig:
    """Testes de schema de configuração de experimento."""

    def test_load_from_yaml(self) -> None:
        """Carrega configuração válida de YAML."""
        config_data = {
            "name": "Test Experiment",
            "models": ["qwen2.5-coder-7b", "deepseek-coder-v2-16b"],
            "files": ["./src/**/*.tsx"],
            "wcag_level": "AA",
            "repetitions": 2,
        }
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False, encoding="utf-8"
        ) as f:
            yaml.dump(config_data, f)
            path = Path(f.name)

        try:
            config = load_experiment_config(path)
            assert config.name == "Test Experiment"
            assert len(config.models) == 2
            assert config.wcag_level == "WCAG2AA"
            assert config.repetitions == 2
        finally:
            path.unlink(missing_ok=True)

    def test_wcag_level_normalized(self) -> None:
        """WCAG 'AA' é normalizado para 'WCAG2AA'."""
        config = ExperimentConfig(
            name="test",
            models=["m1"],
            files=["./src"],
            wcag_level="AA",
        )
        assert config.wcag_level == "WCAG2AA"

    def test_wcag_level_a_normalized(self) -> None:
        """WCAG 'A' é normalizado para 'WCAG2A'."""
        config = ExperimentConfig(name="test", models=["m1"], files=["./src"], wcag_level="A")
        assert config.wcag_level == "WCAG2A"

    def test_invalid_wcag_raises(self) -> None:
        """Nível WCAG inválido levanta ValueError."""
        with pytest.raises(ValueError):
            ExperimentConfig(name="test", models=["m1"], files=["./src"], wcag_level="INVALID")

    def test_file_not_found_raises(self) -> None:
        """Arquivo inexistente levanta FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_experiment_config(Path("nonexistent.yaml"))


# ─── Metrics ──────────────────────────────────────────────────────────────────


class TestMetrics:
    """Testes de cálculo de métricas."""

    def test_success_rate_100_percent(self) -> None:
        """100% de sucesso se todos tiveram sucesso."""
        results = {
            "model-a": [
                make_fix_result(True, issues_fixed=3),
                make_fix_result(True, issues_fixed=2),
            ]
        }
        metrics = compute_experiment_metrics(results)
        assert metrics["model-a"]["success_rate"] == 100.0

    def test_success_rate_0_percent(self) -> None:
        """0% de sucesso se nenhum teve sucesso."""
        results = {"model-a": [make_fix_result(False), make_fix_result(False)]}
        metrics = compute_experiment_metrics(results)
        assert metrics["model-a"]["success_rate"] == 0.0

    def test_success_rate_mixed(self) -> None:
        """50% de sucesso se metade teve sucesso."""
        results = {"model-a": [make_fix_result(True), make_fix_result(False)]}
        metrics = compute_experiment_metrics(results)
        assert metrics["model-a"]["success_rate"] == 50.0

    def test_avg_time_computed(self) -> None:
        """Tempo médio calculado corretamente."""
        results = {
            "model-a": [
                make_fix_result(True, time_s=10.0),
                make_fix_result(True, time_s=20.0),
            ]
        }
        metrics = compute_experiment_metrics(results)
        assert metrics["model-a"]["avg_time"] == 15.0

    def test_issues_fixed_summed(self) -> None:
        """Total de issues corrigidos somado corretamente."""
        results = {
            "model-a": [
                make_fix_result(True, issues_fixed=5),
                make_fix_result(True, issues_fixed=3),
            ]
        }
        metrics = compute_experiment_metrics(results)
        assert metrics["model-a"]["issues_fixed"] == 8

    def test_empty_results(self) -> None:
        """Resultado vazio → métricas zeradas."""
        metrics = compute_experiment_metrics({"model-a": []})
        assert metrics["model-a"]["success_rate"] == 0.0
        assert metrics["model-a"]["avg_time"] == 0.0

    def test_rank_models_by_success_rate(self) -> None:
        """rank_models ordena por taxa de sucesso (maior primeiro)."""
        metrics = {
            "model-a": {"success_rate": 80.0},
            "model-b": {"success_rate": 95.0},
            "model-c": {"success_rate": 60.0},
        }
        ranked = rank_models(metrics, "success_rate")
        names = [name for name, _ in ranked]
        assert names[0] == "model-b"
        assert names[-1] == "model-c"

    def test_rank_models_by_avg_time_ascending(self) -> None:
        """rank_models por avg_time ordena ascendente (menor é melhor)."""
        metrics = {
            "fast": {"avg_time": 3.0},
            "slow": {"avg_time": 15.0},
            "medium": {"avg_time": 8.0},
        }
        ranked = rank_models(metrics, "avg_time")
        names = [name for name, _ in ranked]
        assert names[0] == "fast"
        assert names[-1] == "slow"
