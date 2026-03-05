"""Testes unitários do registro de modelos LLM."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from a11y_autofix.config import LLMBackend, ModelConfig, Settings
from a11y_autofix.llm.registry import ModelRegistry


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def registry(settings: Settings) -> ModelRegistry:
    """Registry sem carregar models.yaml do filesystem."""
    r = ModelRegistry.__new__(ModelRegistry)
    r.settings = settings
    r._models = {}
    r._groups = {}
    return r


class TestRegisterAndGet:
    """Testes de registro e recuperação de modelos."""

    def test_register_model(self, registry: ModelRegistry) -> None:
        """Registra um modelo e recupera com sucesso."""
        config = ModelConfig(
            name="test-model",
            backend=LLMBackend.OLLAMA,
            model_id="test:7b",
        )
        registry.register("test-model", config)
        retrieved = registry.get("test-model")
        assert retrieved.model_id == "test:7b"

    def test_get_raises_if_not_found(self, registry: ModelRegistry) -> None:
        """get() levanta ValueError para modelo inexistente."""
        with pytest.raises(ValueError, match="not found"):
            registry.get("nonexistent-model")

    def test_get_client_returns_client(self, registry: ModelRegistry) -> None:
        """get_client() retorna instância de BaseLLMClient."""
        from a11y_autofix.llm.base import BaseLLMClient
        config = ModelConfig(
            name="test",
            backend=LLMBackend.OLLAMA,
            model_id="test:7b",
        )
        registry.register("test", config)
        client = registry.get_client("test")
        assert isinstance(client, BaseLLMClient)


class TestListModels:
    """Testes de listagem e filtros."""

    def test_list_all_models(self, registry: ModelRegistry) -> None:
        """Lista todos os modelos registrados."""
        registry.register("m1", ModelConfig(name="m1", backend=LLMBackend.OLLAMA, model_id="m1", family="qwen"))
        registry.register("m2", ModelConfig(name="m2", backend=LLMBackend.VLLM, model_id="m2", family="deepseek"))
        models = registry.list_models()
        assert "m1" in models
        assert "m2" in models

    def test_list_models_filters_by_family(self, registry: ModelRegistry) -> None:
        """Filtro por família retorna apenas os corretos."""
        registry.register("q1", ModelConfig(name="q1", backend=LLMBackend.OLLAMA, model_id="q1", family="qwen"))
        registry.register("d1", ModelConfig(name="d1", backend=LLMBackend.OLLAMA, model_id="d1", family="deepseek"))
        qwen_models = registry.list_models(family="qwen")
        assert "q1" in qwen_models
        assert "d1" not in qwen_models

    def test_list_models_filters_by_backend(self, registry: ModelRegistry) -> None:
        """Filtro por backend retorna apenas os corretos."""
        registry.register("o1", ModelConfig(name="o1", backend=LLMBackend.OLLAMA, model_id="o1"))
        registry.register("v1", ModelConfig(name="v1", backend=LLMBackend.VLLM, model_id="v1"))
        ollama_models = registry.list_models(backend=LLMBackend.OLLAMA)
        assert "o1" in ollama_models
        assert "v1" not in ollama_models

    def test_list_models_filters_by_tag(self, registry: ModelRegistry) -> None:
        """Filtro por tag retorna apenas os corretos."""
        registry.register(
            "coding-model",
            ModelConfig(name="coding-model", backend=LLMBackend.OLLAMA, model_id="c1", tags=["coding", "instruct"]),
        )
        registry.register(
            "general-model",
            ModelConfig(name="general-model", backend=LLMBackend.OLLAMA, model_id="g1", tags=["general"]),
        )
        coding = registry.list_models(tag="coding")
        assert "coding-model" in coding
        assert "general-model" not in coding


class TestYAMLLoading:
    """Testes de carregamento de models.yaml."""

    def test_load_from_yaml(self) -> None:
        """Carrega modelos de um arquivo YAML temporário."""
        yaml_content = {
            "models": {
                "test-qwen": {
                    "backend": "ollama",
                    "model_id": "qwen2.5-coder:7b",
                    "family": "qwen",
                    "size": "7b",
                    "temperature": 0.1,
                    "max_tokens": 8192,
                    "tags": ["coding"],
                }
            },
            "model_groups": {
                "small": ["test-qwen"],
            },
        }

        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False, encoding="utf-8"
        ) as f:
            yaml.dump(yaml_content, f)
            yaml_path = Path(f.name)

        try:
            settings = Settings()
            registry = ModelRegistry.__new__(ModelRegistry)
            registry.settings = settings
            registry._models = {}
            registry._groups = {}
            registry._load_from_yaml.__func__(registry)  # type: ignore[attr-defined]

            # Modificar o caminho para o arquivo temporário e recarregar
            # Hack: substituir models.yaml temporariamente
            import os
            old_cwd = os.getcwd()
            try:
                os.chdir(yaml_path.parent)
                import shutil
                shutil.copy(yaml_path, yaml_path.parent / "models.yaml")
                registry._models = {}
                registry._groups = {}
                registry._load_from_yaml()
                assert "test-qwen" in registry._models
                assert registry._groups.get("small") == ["test-qwen"]
            finally:
                os.chdir(old_cwd)
                (yaml_path.parent / "models.yaml").unlink(missing_ok=True)
        finally:
            yaml_path.unlink(missing_ok=True)

    def test_save_to_yaml(self) -> None:
        """Salva modelos em YAML e recarrega."""
        settings = Settings()
        registry = ModelRegistry.__new__(ModelRegistry)
        registry.settings = settings
        registry._models = {}
        registry._groups = {}

        registry.register(
            "my-model",
            ModelConfig(name="my-model", backend=LLMBackend.OLLAMA, model_id="my:7b"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "models.yaml"
            registry.save_to_yaml(save_path)

            assert save_path.exists()
            with open(save_path) as f:
                data = yaml.safe_load(f)
            assert "my-model" in data["models"]


class TestGroups:
    """Testes de grupos de modelos."""

    def test_get_group(self, registry: ModelRegistry) -> None:
        """get_group() retorna modelos do grupo."""
        registry._groups = {"small": ["m1", "m2"]}
        result = registry.get_group("small")
        assert result == ["m1", "m2"]

    def test_get_group_raises_if_not_found(self, registry: ModelRegistry) -> None:
        """get_group() levanta ValueError para grupo inexistente."""
        with pytest.raises(ValueError, match="not found"):
            registry.get_group("nonexistent")
