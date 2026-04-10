"""
Configuração global do sistema a11y-autofix.

Todos os modelos Pydantic e enums estão centralizados aqui para garantir
consistência e facilitar a serialização de experimentos.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class LLMBackend(str, Enum):
    """Backends LLM suportados (todos locais, OpenAI-compatible)."""

    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    JAN = "jan"
    LOCALAI = "localai"
    CUSTOM = "custom"


class ScanTool(str, Enum):
    """Ferramentas de acessibilidade suportadas."""

    PA11Y = "pa11y"
    AXE = "axe-core"
    LIGHTHOUSE = "lighthouse"
    PLAYWRIGHT = "playwright+axe"
    ESLINT = "eslint-jsx-a11y"


class WCAGLevel(str, Enum):
    """Nível WCAG para validação."""

    A = "WCAG2A"
    AA = "WCAG2AA"
    AAA = "WCAG2AAA"


class IssueType(str, Enum):
    """Tipo de issue de acessibilidade."""

    ARIA = "aria"
    CONTRAST = "contrast"
    KEYBOARD = "keyboard"
    LABEL = "label"
    SEMANTIC = "semantic"
    ALT_TEXT = "alt-text"
    FOCUS = "focus"
    OTHER = "other"


class Complexity(str, Enum):
    """Complexidade de correção do issue."""

    SIMPLE = "simple"       # ex: adicionar aria-label
    MODERATE = "moderate"   # ex: reestruturar navegação por teclado
    COMPLEX = "complex"     # ex: redesign de contraste de cores


class Confidence(str, Enum):
    """Nível de confiança científica baseado em consenso multi-tool."""

    HIGH = "high"       # ≥2 ferramentas independentes concordam
    MEDIUM = "medium"   # 1 ferramenta, alto impacto
    LOW = "low"         # 1 ferramenta, baixo impacto


class AgentType(str, Enum):
    """Tipo de agente de correção."""

    AUTO = "auto"           # Router decide
    OPENHANDS = "openhands"
    SWE_AGENT = "swe-agent"
    DIRECT_LLM = "direct-llm"


# ═══════════════════════════════════════════════════════════════════════════════
# Configuração de Modelos LLM
# ═══════════════════════════════════════════════════════════════════════════════


class ModelConfig(BaseModel):
    """Configuração de um modelo LLM específico."""

    name: str = Field(description="Nome amigável do modelo, ex: qwen2.5-coder-7b")
    backend: LLMBackend = Field(description="Backend LLM a usar")
    base_url: str = Field(default="", description="URL base (vazio = default do backend)")
    api_key: str = Field(default="local", description="Chave de API ('local' para backends locais)")
    model_id: str = Field(description="ID do modelo no backend, ex: qwen2.5-coder:7b")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, gt=0)
    timeout: int = Field(default=120, gt=0, description="Timeout em segundos")

    # Metadata para experimentos
    family: str = Field(default="", description="Família do modelo, ex: qwen, deepseek")
    size: str = Field(default="", description="Tamanho, ex: 7b, 13b, 34b")
    quantization: str = Field(default="", description="Quantização, ex: q4_k_m, fp16")
    tags: list[str] = Field(default_factory=list, description="Tags: coding, instruct, etc.")


# ═══════════════════════════════════════════════════════════════════════════════
# Settings principal
# ═══════════════════════════════════════════════════════════════════════════════


class Settings(BaseSettings):
    """Configuração global do sistema lida de .env e variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ═══ LLM Models Registry ═══
    default_model: str = Field(default="qwen2.5-coder-7b")

    # ═══ Scanner Tools ═══
    use_pa11y: bool = Field(default=True)
    use_axe: bool = Field(default=True)
    use_lighthouse: bool = Field(default=False)
    use_playwright: bool = Field(default=True)
    use_eslint: bool = Field(default=True)

    # ═══ Protocolo Científico ═══
    min_tool_consensus: int = Field(default=2, ge=1, description="Mínimo de ferramentas para HIGH confidence")

    # ═══ Performance ═══
    max_concurrent_scans: int = Field(default=4, ge=1)
    max_concurrent_agents: int = Field(default=2, ge=1)
    max_concurrent_models: int = Field(default=3, ge=1)
    scan_timeout: int = Field(default=60, gt=0)
    agent_timeout: int = Field(default=180, gt=0)

    # ═══ Router ═══
    swe_max_issues: int = Field(default=4, ge=1)
    openhands_complexity_threshold: int = Field(default=3, ge=1)

    # ═══ Experiments ═══
    experiments_dir: Path = Field(default=Path("./experiments"))
    results_dir: Path = Field(default=Path("./experiment-results"))
    enable_benchmarking: bool = Field(default=False)

    # ═══ Agents ═══
    openhands_url: str = Field(default="http://localhost:3000")
    swe_cli_path: str = Field(default="sweagent")
    max_retries_per_agent: int = Field(default=3, ge=1, le=10)

    # ═══ Output ═══
    output_dir: Path = Field(default=Path("./a11y-report"))
    log_level: str = Field(default="INFO")


# ═══════════════════════════════════════════════════════════════════════════════
# Modelos de Dados Científicos
# ═══════════════════════════════════════════════════════════════════════════════


class ToolFinding(BaseModel):
    """Finding cru de uma ferramenta de acessibilidade (pa11y, axe, etc)."""

    tool: ScanTool
    tool_version: str = Field(default="unknown")
    rule_id: str = Field(description="ID da regra, ex: color-contrast")
    wcag_criteria: str | None = Field(default=None, description="Critério WCAG, ex: 1.4.3")
    message: str = Field(description="Descrição do problema")
    selector: str = Field(description="Seletor CSS do elemento")
    context: str = Field(default="", description="Snippet HTML")
    impact: str = Field(default="moderate", description="critical, serious, moderate, minor")
    help_url: str = Field(default="")


class A11yIssue(BaseModel):
    """Issue de acessibilidade deduplificado com metadados científicos."""

    issue_id: str = Field(default="", description="ID estável SHA-256[:16]")
    file: str = Field(description="Caminho do arquivo")
    selector: str = Field(description="Seletor CSS")

    # Classificação
    issue_type: IssueType
    complexity: Complexity
    wcag_criteria: str | None = Field(default=None)
    impact: str = Field(default="moderate")

    # Metadados científicos
    confidence: Confidence
    found_by: list[ScanTool] = Field(default_factory=list)
    tool_consensus: int = Field(default=1)
    findings: list[ToolFinding] = Field(default_factory=list)

    # Contexto
    message: str
    context: str = Field(default="")
    resolved: bool = Field(default=False)

    def compute_id(self) -> "A11yIssue":
        """Gera ID estável baseado em conteúdo (content-addressed)."""
        key = f"{self.file}:{self.selector}:{self.wcag_criteria}:{self.issue_type}"
        self.issue_id = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self


class ScanResult(BaseModel):
    """Resultado de scan de UM arquivo."""

    file: Path
    file_hash: str = Field(description="SHA-256 do conteúdo")
    issues: list[A11yIssue] = Field(default_factory=list)
    scan_time: float = Field(default=0.0)
    tools_used: list[ScanTool] = Field(default_factory=list)
    tool_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = Field(default=None)

    @property
    def has_issues(self) -> bool:
        """Verifica se há issues."""
        return len(self.issues) > 0

    def high_confidence_issues(self) -> list[A11yIssue]:
        """Retorna issues de alta confiança."""
        return [i for i in self.issues if i.confidence == Confidence.HIGH]


class AgentTask(BaseModel):
    """Tarefa enviada a um agente de correção."""

    file: Path
    file_content: str
    issues: list[A11yIssue]
    wcag_level: str = Field(default="WCAG2AA")
    context: dict[str, Any] = Field(default_factory=dict)


class PatchResult(BaseModel):
    """Resultado de uma operação de patch de um agente."""

    success: bool
    new_content: str = Field(default="")
    diff: str = Field(default="")
    error: str | None = Field(default=None)
    tokens_used: int | None = Field(default=None, description="Total tokens (prompt+completion)")
    tokens_prompt: int | None = Field(default=None, description="Tokens de input/prompt")
    tokens_completion: int | None = Field(default=None, description="Tokens de output/completion")
    time_seconds: float = Field(default=0.0)


class FixAttempt(BaseModel):
    """Uma tentativa de correção de um arquivo."""

    attempt_number: int
    agent: str
    model: str
    timestamp: datetime
    success: bool
    diff: str = Field(default="")
    new_content: str = Field(default="")
    tokens_used: int | None = Field(default=None, description="Total tokens (prompt+completion)")
    tokens_prompt: int | None = Field(default=None, description="Tokens de input/prompt")
    tokens_completion: int | None = Field(default=None, description="Tokens de output/completion")
    time_seconds: float = Field(default=0.0)
    error: str | None = Field(default=None)


class FixResult(BaseModel):
    """Resultado completo da correção de UM arquivo."""

    file: Path
    scan_result: ScanResult
    attempts: list[FixAttempt] = Field(default_factory=list)
    final_success: bool = Field(default=False)
    issues_fixed: int = Field(default=0)
    issues_pending: int = Field(default=0)
    total_time: float = Field(default=0.0)

    @property
    def best_attempt(self) -> FixAttempt | None:
        """Retorna a primeira tentativa bem-sucedida."""
        successful = [a for a in self.attempts if a.success]
        return successful[0] if successful else None


class RouterDecision(BaseModel):
    """Decisão do router sobre qual agente usar."""

    agent: str
    score: int
    reason: str


class ExperimentResult(BaseModel):
    """Resultado de um experimento multi-modelo completo."""

    experiment_id: str
    experiment_name: str
    timestamp: datetime
    models_tested: list[str]
    files_processed: int

    results_by_model: dict[str, list[FixResult]] = Field(default_factory=dict)
    success_rate_by_model: dict[str, float] = Field(default_factory=dict)
    avg_time_by_model: dict[str, float] = Field(default_factory=dict)
    issues_fixed_by_model: dict[str, int] = Field(default_factory=dict)

    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    tool_versions: dict[str, str] = Field(default_factory=dict)
