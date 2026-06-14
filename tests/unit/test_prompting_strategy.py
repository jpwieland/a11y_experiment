"""Testes do wiring da estratégia de prompting (IV2, Section 3.6.2).

Antes de 06/2026 a estratégia era um no-op: o ExperimentRunner a usava
apenas para nomear checkpoints e o PromptBuilder de 6 componentes era
código morto. Estes testes garantem que cada estratégia produz um prompt
materialmente diferente e que ela flui Pipeline → agente → prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_autofix.agents.direct_llm import DirectLLMAgent
from a11y_autofix.agents.prompts import (
    PromptBuilder,
    PromptingStrategy,
    build_direct_llm_prompt,
    build_swe_prompt,
)
from a11y_autofix.agents.swe import SWEAgent
from a11y_autofix.config import (
    A11yIssue,
    AgentTask,
    AgentType,
    Complexity,
    Confidence,
    IssueType,
    ModelConfig,
    Settings,
)


def _issue(issue_type: IssueType = IssueType.ARIA, wcag: str = "4.1.2") -> A11yIssue:
    return A11yIssue(
        file="Component.tsx",
        selector="button",
        issue_type=issue_type,
        complexity=Complexity.SIMPLE,
        wcag_criteria=wcag,
        confidence=Confidence.HIGH,
        message="Button without accessible name",
    ).compute_id()


def _task() -> AgentTask:
    return AgentTask(
        file=Path("Component.tsx"),
        file_content="export const C = () => <button onClick={f}><svg/></button>;",
        issues=[_issue()],
    )


class TestPromptBuilderStrategies:
    def test_zero_shot_has_no_component5(self):
        prompt = PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>",
            strategy=PromptingStrategy.ZERO_SHOT,
        )
        assert "Few-Shot Examples" not in prompt
        assert "<!-- CoT -->" not in prompt

    def test_few_shot_includes_tailored_examples(self):
        prompt = PromptBuilder().build(
            issues=[_issue(IssueType.ARIA)], file=Path("C.tsx"), file_content="<div/>",
            strategy=PromptingStrategy.FEW_SHOT,
        )
        assert "Few-Shot Examples" in prompt
        # Exemplos ARIA devem estar presentes (tipo do issue)
        assert "button-name" in prompt
        assert "<!-- CoT -->" not in prompt

    def test_chain_of_thought_appends_cot_instruction(self):
        prompt = PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>",
            strategy=PromptingStrategy.CHAIN_OF_THOUGHT,
        )
        assert "Few-Shot Examples" in prompt
        assert "<!-- CoT -->" in prompt
        assert "step-by-step" in prompt

    def test_strategies_produce_distinct_prompts(self):
        kwargs = dict(issues=[_issue()], file=Path("C.tsx"), file_content="<div/>")
        prompts = {
            s: PromptBuilder().build(strategy=s, **kwargs)
            for s in PromptingStrategy
        }
        assert len(set(prompts.values())) == 3
        # zero-shot é o menor; CoT o maior
        assert len(prompts[PromptingStrategy.ZERO_SHOT]) < len(prompts[PromptingStrategy.FEW_SHOT])
        assert len(prompts[PromptingStrategy.FEW_SHOT]) < len(prompts[PromptingStrategy.CHAIN_OF_THOUGHT])

    def test_all_six_components_present_in_few_shot(self):
        prompt = PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>",
            strategy=PromptingStrategy.FEW_SHOT,
        )
        assert "expert in React/TypeScript" in prompt          # C1: role
        assert "HARD CONSTRAINTS" in prompt                     # C2: constraints
        assert "## File: C.tsx" in prompt                       # C3: file
        assert "## Accessibility Issues" in prompt              # C4: issues
        assert "Few-Shot Examples" in prompt                    # C5: examples
        assert "## Output Format" in prompt                     # C6: output


class TestSwePromptStrategy:
    def test_swe_zero_shot_has_no_examples(self):
        prompt = build_swe_prompt(_task(), strategy=PromptingStrategy.ZERO_SHOT)
        assert "Reference correction patterns" not in prompt
        assert "PATCH blocks" in prompt  # identidade cirúrgica preservada

    def test_swe_few_shot_keeps_surgical_format(self):
        prompt = build_swe_prompt(_task(), strategy=PromptingStrategy.FEW_SHOT)
        assert "Reference correction patterns" in prompt
        assert "PATCH blocks" in prompt

    def test_swe_cot_adds_reasoning_block(self):
        prompt = build_swe_prompt(_task(), strategy=PromptingStrategy.CHAIN_OF_THOUGHT)
        assert "<!-- CoT -->" in prompt
        assert "PATCH blocks" in prompt


class TestRetryFeedbackLoop:
    """Auto-correção: a rejeição da tentativa anterior deve chegar ao prompt.

    Sem isso, a temperatura baixa reproduz o mesmo patch rejeitado e as
    tentativas 2-3 são desperdiçadas (SR do swe-agent foi 19% no run dffbe9ec).
    O feedback é uniforme entre estratégias/agentes (só muda tentativas ≥ 2),
    então não confunde as comparações de IV2/agente.
    """

    _PREV = {"error": "functional_regression:export_signature", "layer": 2,
             "excerpt": "export default Broken"}

    def test_first_attempt_has_no_feedback(self):
        assert "REJECTED" not in build_swe_prompt(_task())
        assert "REJECTED" not in build_direct_llm_prompt(_task())
        assert "REJECTED" not in PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>")

    def test_retry_injects_rejection_reason_all_agents(self):
        swe = build_swe_prompt(_task(), previous_attempt=self._PREV)
        direct = build_direct_llm_prompt(_task(), previous_attempt=self._PREV)
        oh = PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>",
            previous_attempt=self._PREV)
        for prompt in (swe, direct, oh):
            assert "REJECTED" in prompt
            assert "functional regression" in prompt  # camada 2 descrita

    def test_feedback_does_not_leak_fewshot_into_zero_shot(self):
        # Mesmo com feedback, zero-shot continua sem o Componente 5.
        prompt = PromptBuilder().build(
            issues=[_issue()], file=Path("C.tsx"), file_content="<div/>",
            strategy=PromptingStrategy.ZERO_SHOT, previous_attempt=self._PREV)
        assert "Component 5" not in prompt
        assert "REJECTED" in prompt


class TestStrategyFlowsThroughAgents:
    """A estratégia passada ao agente deve mudar o prompt enviado ao LLM."""

    class _CapturingLLM:
        """Mock que captura o prompt e devolve resposta vazia."""

        def __init__(self):
            self.config = ModelConfig(name="mock", model_id="mock", backend="ollama")
            self.captured: dict = {}

        async def complete_with_metrics(self, system: str, user: str):
            self.captured = {"system": system, "user": user}
            return "no code here", {"tokens_total": 0, "time_seconds": 0.0}

    @pytest.mark.anyio
    async def test_direct_llm_agent_respects_strategy(self):
        llm = self._CapturingLLM()
        agent_zero = DirectLLMAgent(llm, strategy="zero-shot")
        await agent_zero.run(_task())
        prompt_zero = llm.captured["user"]

        agent_few = DirectLLMAgent(llm, strategy="few-shot")
        await agent_few.run(_task())
        prompt_few = llm.captured["user"]

        assert "Few-Shot Examples" not in prompt_zero
        assert "Few-Shot Examples" in prompt_few

    @pytest.mark.anyio
    async def test_swe_agent_respects_strategy(self, monkeypatch):
        # Garantir que o caminho CLI não é usado
        monkeypatch.setattr("shutil.which", lambda *_: None)
        llm = self._CapturingLLM()
        agent = SWEAgent(llm, strategy="chain-of-thought")
        await agent.run(_task())
        assert "<!-- CoT -->" in llm.captured["user"]

    def test_default_strategy_is_few_shot(self):
        llm = self._CapturingLLM()
        agent = DirectLLMAgent(llm)
        assert agent.strategy == PromptingStrategy.FEW_SHOT


class TestPipelineStrategyPropagation:
    def test_pipeline_passes_strategy_to_agents(self):
        from a11y_autofix.pipeline import Pipeline

        pipeline = Pipeline(
            settings=Settings(),
            model_config=ModelConfig(name="mock", model_id="mock", backend="ollama"),
            agent_preference=AgentType.AUTO,
            strategy="chain-of-thought",
        )
        agent = pipeline._create_agent("direct-llm")
        assert agent.strategy == PromptingStrategy.CHAIN_OF_THOUGHT
        agent = pipeline._create_agent("swe-agent")
        assert agent.strategy == PromptingStrategy.CHAIN_OF_THOUGHT
        agent = pipeline._create_agent("openhands")
        assert agent.strategy == PromptingStrategy.CHAIN_OF_THOUGHT
