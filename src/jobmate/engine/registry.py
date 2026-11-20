"""워크플로/에이전트 레지스트리 — 버전 고정 spec 조회."""

from __future__ import annotations

from ..models import AgentSpec, ModelConfig, WorkflowSpec


class Registry:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowSpec] = {}
        self._agents: dict[str, AgentSpec] = {}

    def register_workflow(self, spec: WorkflowSpec) -> None:
        self._workflows[spec.key] = spec

    def register_agent(self, agent: AgentSpec) -> None:
        self._agents[agent.key] = agent

    def workflow(self, key: str) -> WorkflowSpec:
        return self._workflows[key]

    def agent(self, key: str) -> AgentSpec:
        return self._agents[key]

    def workflows(self) -> list[WorkflowSpec]:
        return list(self._workflows.values())


def default_agents(
    model_default: ModelConfig, model_important: ModelConfig
) -> dict[str, AgentSpec]:
    """dev_review용 기본 에이전트 스펙. 모델은 Settings에서 주입."""
    return {
        "analysis": AgentSpec(
            key="analysis",
            role="analysis",
            prompt="analysis",
            output_schema="AnalysisResult",
            model=model_default,
            tier="default",
        ),
        "implementer": AgentSpec(
            key="implementer",
            role="implementer",
            prompt="implementer",
            output_schema="ChangeProposal",
            model=model_important,
            tier="important",
            tools=["read_file", "read_diff"],
        ),
        "security": AgentSpec(
            key="security",
            role="security",
            prompt="security_review",
            output_schema="Verdict",
            model=model_important,
            tier="important",
            tools=["read_diff"],
        ),
        "reviewer": AgentSpec(
            key="reviewer",
            role="reviewer",
            prompt="code_review",
            output_schema="Verdict",
            model=model_important,
            tier="important",
            tools=["read_diff"],
        ),
        "qa": AgentSpec(
            key="qa",
            role="qa",
            prompt="qa",
            output_schema="QaVerdict",
            model=model_important,
            tier="important",
            tools=["read_diff", "run_tests"],
        ),
        "extractor": AgentSpec(
            key="extractor",
            role="extractor",
            prompt="event_extract",
            output_schema="ExtractedEvent",
            model=model_default,
            tier="default",
        ),
    }
