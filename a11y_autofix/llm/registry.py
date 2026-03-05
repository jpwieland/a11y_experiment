"""
Registro centralizado de modelos LLM.

Permite adicionar novos modelos sem modificar código — apenas editar models.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

from a11y_autofix.config import LLMBackend, ModelConfig, Settings
from a11y_autofix.llm.client import LocalLLMClient

if TYPE_CHECKING:
    from a11y_autofix.llm.base import BaseLLMClient

log = structlog.get_logger(__name__)


class ModelRegistry:
    """
    Registro centralizado de todos os modelos LLM disponíveis.

    Modelos são carregados de:
    1. settings.available_models (se definido)
    2. models.yaml (configuração completa de experimentos)
    3. Descoberta automática (ollama list, etc.)

    Exemplo de uso:
        registry = ModelRegistry(settings)
        client = registry.get_client("qwen2.5-coder-7b")
        response = await client.complete(system, user)
    """

    def __init__(self, settings: Settings) -> None:
        """
        Args:
            settings: Configuração global do sistema.
        """
        self.settings = settings
        self._models: dict[str, ModelConfig] = {}
        self._groups: dict[str, list[str]] = {}
        self._load_from_yaml()
        self._register_defaults()

    def register(self, name: str, config: ModelConfig) -> None:
        """
        Registra um modelo manualmente.

        Args:
            name: Nome amigável do modelo.
            config: Configuração do modelo.
        """
        self._models[name] = config
        log.debug("model_registered", name=name, backend=config.backend.value)

    def get(self, name: str) -> ModelConfig:
        """
        Obtém configuração de um modelo por nome.

        Args:
            name: Nome do modelo.

        Returns:
            ModelConfig do modelo.

        Raises:
            ValueError: Se o modelo não estiver registrado.
        """
        if name not in self._models:
            available = list(self._models.keys())
            raise ValueError(
                f"Model '{name}' not found in registry.\n"
                f"Available models: {', '.join(available) or 'none'}\n"
                f"Add it to models.yaml or use: a11y-autofix models add {name} --backend ollama --model-id ..."
            )
        return self._models[name]

    def get_client(self, name: str) -> "BaseLLMClient":
        """
        Obtém cliente LLM para um modelo por nome.

        Args:
            name: Nome do modelo.

        Returns:
            Instância de BaseLLMClient pronto para uso.
        """
        config = self.get(name)
        return LocalLLMClient(config)

    def list_models(
        self,
        family: str | None = None,
        backend: LLMBackend | None = None,
        size: str | None = None,
        tag: str | None = None,
    ) -> list[str]:
        """
        Lista modelos disponíveis com filtros opcionais.

        Args:
            family: Filtrar por família (qwen, deepseek, codellama, etc.).
            backend: Filtrar por backend LLM.
            size: Filtrar por tamanho (7b, 13b, etc.).
            tag: Filtrar por tag (coding, instruct, etc.).

        Returns:
            Lista de nomes de modelos que passaram nos filtros.
        """
        result = []
        for name, m in self._models.items():
            if family and m.family != family:
                continue
            if backend and m.backend != backend:
                continue
            if size and m.size != size:
                continue
            if tag and tag not in m.tags:
                continue
            result.append(name)
        return sorted(result)

    def get_group(self, group_name: str) -> list[str]:
        """
        Obtém lista de modelos de um grupo.

        Args:
            group_name: Nome do grupo definido em models.yaml.

        Returns:
            Lista de nomes de modelos do grupo.

        Raises:
            ValueError: Se o grupo não existir.
        """
        if group_name not in self._groups:
            available = list(self._groups.keys())
            raise ValueError(
                f"Group '{group_name}' not found.\n"
                f"Available groups: {', '.join(available) or 'none'}"
            )
        return self._groups[group_name]

    def list_groups(self) -> list[str]:
        """Retorna nomes de todos os grupos definidos."""
        return list(self._groups.keys())

    async def auto_discover(self, backend: LLMBackend) -> list[ModelConfig]:
        """
        Descobre modelos disponíveis automaticamente via API do backend.

        Args:
            backend: Backend para descobrir modelos.

        Returns:
            Lista de ModelConfig dos modelos descobertos.
        """
        import httpx

        from a11y_autofix.llm.client import _DEFAULT_URLS

        base_url = _DEFAULT_URLS.get(backend, "http://localhost:11434/v1")
        discovered: list[ModelConfig] = []

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(f"{base_url}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    for model in data.get("data", []):
                        model_id = model.get("id", "")
                        if not model_id:
                            continue
                        name = model_id.replace(":", "-").replace("/", "-")
                        config = ModelConfig(
                            name=name,
                            backend=backend,
                            model_id=model_id,
                            tags=["auto-discovered"],
                        )
                        discovered.append(config)
                        self.register(name, config)
            except Exception as e:
                log.warning("auto_discover_failed", backend=backend.value, error=str(e))

        return discovered

    def save_to_yaml(self, path: Path | None = None) -> None:
        """
        Salva modelos registrados em models.yaml.

        Args:
            path: Caminho do arquivo (default: models.yaml).
        """
        yaml_path = path or Path("models.yaml")
        data: dict[str, object] = {"models": {}, "model_groups": self._groups}

        for name, model in self._models.items():
            data["models"][name] = {  # type: ignore[index]
                "backend": model.backend.value,
                "model_id": model.model_id,
                "base_url": model.base_url,
                "temperature": model.temperature,
                "max_tokens": model.max_tokens,
                "family": model.family,
                "size": model.size,
                "quantization": model.quantization,
                "tags": model.tags,
            }

        with open(yaml_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        log.info("registry_saved", path=str(yaml_path), models=len(self._models))

    def _load_from_yaml(self) -> None:
        """Carrega modelos de models.yaml."""
        yaml_path = Path("models.yaml")
        if not yaml_path.exists():
            log.debug("models_yaml_not_found", path=str(yaml_path))
            return

        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}

            # Carregar modelos
            for name, cfg in data.get("models", {}).items():
                try:
                    model = ModelConfig(name=name, **cfg)
                    self._models[name] = model
                except Exception as e:
                    log.warning("model_load_error", name=name, error=str(e))

            # Carregar grupos
            self._groups = data.get("model_groups", {})

            log.info(
                "models_loaded_from_yaml",
                models=len(self._models),
                groups=len(self._groups),
            )
        except Exception as e:
            log.error("models_yaml_parse_error", error=str(e))

    def _register_defaults(self) -> None:
        """Registra modelos padrão caso models.yaml não exista."""
        if self._models:
            return  # Já foram carregados do YAML

        defaults = [
            ModelConfig(
                name="qwen2.5-coder-7b",
                backend=LLMBackend.OLLAMA,
                model_id="qwen2.5-coder:7b",
                family="qwen",
                size="7b",
                temperature=0.1,
                max_tokens=8192,
                tags=["coding", "instruct", "multilingual"],
            ),
            ModelConfig(
                name="deepseek-coder-v2-16b",
                backend=LLMBackend.OLLAMA,
                model_id="deepseek-coder-v2:16b",
                family="deepseek",
                size="16b",
                temperature=0.1,
                max_tokens=8192,
                tags=["coding", "instruct"],
            ),
            ModelConfig(
                name="codellama-7b",
                backend=LLMBackend.OLLAMA,
                model_id="codellama:7b-instruct",
                family="codellama",
                size="7b",
                temperature=0.2,
                max_tokens=4096,
                tags=["coding", "instruct"],
            ),
        ]

        for model in defaults:
            self._models[model.name] = model

        log.debug("default_models_registered", count=len(defaults))
