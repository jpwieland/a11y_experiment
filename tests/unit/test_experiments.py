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
from a11y_autofix.experiments.runner import _stable_file_id


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


class TestTokenMetricsFromResults:
    """Bug do run dffbe9ec: agentes swe/openhands não gravavam tokens_prompt,
    então input_tokens=0 → TE caía no fallback de tokens de saída e TPF=None.

    Agora compute_experiment_metrics deriva os tokens de input dos próprios
    resultados (FixAttempt.tokens_prompt), tornando TE/TPF métricas funcionais.
    """

    def _result_with_tokens(self, tokens_prompt: int, tokens_out: int,
                            fixed: int = 1) -> FixResult:
        from datetime import datetime, timezone

        from a11y_autofix.config import (A11yIssue, Complexity, Confidence,
                                         FixAttempt, IssueType)
        issue = A11yIssue(file="a.tsx", selector="img", issue_type=IssueType.ARIA,
                          complexity=Complexity.SIMPLE, confidence=Confidence.HIGH,
                          message="m")
        scan = ScanResult(file=Path("a.tsx"), file_hash="sha256:x", issues=[issue],
                          tools_used=[ScanTool.AXE], tool_versions={})
        att = FixAttempt(attempt_number=1, agent="swe-agent", model="m",
                         timestamp=datetime.now(tz=timezone.utc), success=fixed > 0,
                         tokens_used=tokens_out, tokens_prompt=tokens_prompt,
                         tokens_completion=tokens_out - tokens_prompt, time_seconds=5.0)
        return FixResult(file=Path("a.tsx"), scan_result=scan, attempts=[att],
                         final_success=fixed > 0, issues_fixed=fixed,
                         issues_pending=1 - fixed, total_time=5.0)

    def test_te_uses_input_tokens_from_results(self) -> None:
        results = [self._result_with_tokens(1500, 2000, fixed=1)]
        m = compute_experiment_metrics({"mdl": results})["mdl"]
        assert m["te"] is not None and m["te"] > 0

    def test_tpf_populated_when_prompt_tokens_present(self) -> None:
        from a11y_autofix.experiments.metrics import compute_tpf
        results = [self._result_with_tokens(1200, 1700, fixed=1),
                   self._result_with_tokens(800, 1300, fixed=1)]
        # 2000 tokens de input / 2 fixes = 1000.0
        assert compute_tpf(results) == 1000.0


# ─── Disponibilidade de modelo (auto-pull) ─────────────────────────────────────


class TestEnsureModelAvailable:
    """Cobre a correção do bug dffbe9ec: codellama:7b-instruct não baixado → 404.

    O runner agora baixa o tag exato de models.yaml em cold-start e falha
    de forma dura (RuntimeError) se o pull não funcionar — em vez de registrar
    silenciosamente 0 arquivos processados.
    """

    def _runner(self):
        from a11y_autofix.experiments.runner import ExperimentRunner
        from a11y_autofix.llm.registry import ModelRegistry

        settings = Settings()
        registry = ModelRegistry(settings)
        return ExperimentRunner(settings, registry, pipeline_factory=lambda *a, **k: None)

    @pytest.mark.asyncio
    async def test_noop_for_non_ollama_backend(self, monkeypatch) -> None:
        """Backends não-ollama (vLLM, etc.) são no-op — não tentam pull."""
        called = {"exec": False}

        async def _fake_exec(*args, **kwargs):
            called["exec"] = True
            raise AssertionError("não deveria chamar subprocess para backend não-ollama")

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
        runner = self._runner()
        # qwen2.5-coder-32b é backend vllm em models.yaml
        await runner._ensure_model_available("qwen2.5-coder-32b")
        assert called["exec"] is False

    @pytest.mark.asyncio
    async def test_already_installed_skips_pull(self, monkeypatch) -> None:
        """Se o tag exato já está em `ollama list`, não há pull."""
        cmds: list[tuple] = []

        class _Proc:
            returncode = 0

            async def communicate(self):
                # Simula saída de `ollama list` com o tag exato presente.
                return (b"NAME                  ID  SIZE\ncodellama:7b-instruct  abc  4 GB\n", b"")

        async def _fake_exec(*args, **kwargs):
            cmds.append(args)
            return _Proc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
        runner = self._runner()
        await runner._ensure_model_available("codellama-7b")
        # Apenas o `ollama list` deve ter rodado, nunca `ollama pull`.
        assert all("pull" not in c for c in cmds), cmds

    @pytest.mark.asyncio
    async def test_pull_failure_is_fatal(self, monkeypatch) -> None:
        """Tag ausente + pull falha → RuntimeError com o tag exato na mensagem."""

        class _ListProc:
            returncode = 0

            async def communicate(self):
                return (b"NAME  ID  SIZE\nqwen2.5-coder:3b  x  2 GB\n", b"")  # sem codellama

        class _PullProc:
            returncode = 1

            async def communicate(self):
                return (b"Error: pull model manifest: file does not exist", b"")

        seq = [_ListProc(), _PullProc()]

        async def _fake_exec(*args, **kwargs):
            return seq.pop(0)

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
        runner = self._runner()
        with pytest.raises(RuntimeError, match="codellama:7b-instruct"):
            await runner._ensure_model_available("codellama-7b")


# ─── _stable_file_id ─────────────────────────────────────────────────────────


class TestStableFileId:
    """Garante que _stable_file_id não produz colisões entre projetos distintos."""

    def test_different_projects_same_stem_produce_different_ids(self) -> None:
        """index.tsx em dois projetos distintos → IDs distintos."""
        p1 = Path("/repo/dataset/snapshots/projectA/src/index.tsx")
        p2 = Path("/repo/dataset/snapshots/projectB/src/index.tsx")
        assert _stable_file_id(p1) != _stable_file_id(p2)

    def test_same_file_same_id(self) -> None:
        """Mesma chamada com mesmo path → mesmo ID (determinístico)."""
        p = Path("/repo/dataset/snapshots/projectA/src/index.tsx")
        assert _stable_file_id(p) == _stable_file_id(p)

    def test_id_contains_stem_prefix(self) -> None:
        """ID começa com o stem do arquivo para legibilidade nos logs."""
        p = Path("/repo/dataset/snapshots/projectA/components/Button.tsx")
        assert _stable_file_id(p).startswith("Button_")

    def test_different_paths_same_project_produce_different_ids(self) -> None:
        """index.tsx em dirs distintos do mesmo projeto → IDs distintos."""
        p1 = Path("/repo/dataset/snapshots/projectA/src/index.tsx")
        p2 = Path("/repo/dataset/snapshots/projectA/components/index.tsx")
        assert _stable_file_id(p1) != _stable_file_id(p2)

    def test_no_snapshots_in_path_uses_full_path(self) -> None:
        """Fallback para path sem 'snapshots/' não lança exceção."""
        p = Path("/tmp/some_file.tsx")
        result = _stable_file_id(p)
        assert result.startswith("some_file_")
        assert len(result) > 5


# ─── ScanResultCache.get_tool_versions ───────────────────────────────────────


class TestScanResultCacheGetToolVersions:
    """Testa que get_tool_versions extrai versões reais do cache em vez de {}."""

    def _make_cache(self, versions: dict) -> "ScanResultCache":
        from a11y_autofix.experiments.runner import ScanResultCache
        import tempfile, json
        tmp = Path(tempfile.mktemp(suffix=".json"))
        entry = {
            "file": "/fake/snapshots/proj/src/Button.tsx",
            "file_hash": "sha256:abc",
            "issues": [],
            "tools_used": ["pa11y"],
            "tool_versions": versions,
            "scan_time": 0.1,
        }
        tmp.write_text(json.dumps({
            "version": 2,
            "scans": {"/fake/snapshots/proj/src/Button.tsx": entry},
        }))
        cache = ScanResultCache(tmp)
        cache.load()
        tmp.unlink(missing_ok=True)
        return cache

    def test_returns_versions_from_cache(self) -> None:
        cache = self._make_cache({"pa11y": "6.7.0", "axe-core": "4.9.1"})
        v = cache.get_tool_versions()
        assert v["pa11y"] == "6.7.0"
        assert v["axe-core"] == "4.9.1"

    def test_returns_empty_when_cache_empty(self) -> None:
        import tempfile
        from a11y_autofix.experiments.runner import ScanResultCache
        tmp = Path(tempfile.mktemp(suffix=".json"))
        cache = ScanResultCache(tmp)
        assert cache.get_tool_versions() == {}

    def test_returns_empty_when_all_versions_empty(self) -> None:
        cache = self._make_cache({})
        assert cache.get_tool_versions() == {}

    def test_result_is_independent_copy(self) -> None:
        """Mutação do resultado não altera o cache interno."""
        cache = self._make_cache({"pa11y": "6.7.0"})
        v = cache.get_tool_versions()
        v["injected"] = "hacked"
        assert "injected" not in cache.get_tool_versions()
