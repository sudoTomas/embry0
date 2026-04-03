"""Base state schemas for LangGraph orchestration."""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict


class TriageAction(StrEnum):
    PROCEED = "proceed"
    NEEDS_INFO = "needs_info"
    SPLIT = "split"


class PipelineConfig(TypedDict, total=False):
    sandbox_profile: str
    max_feedback_loops: int
    reviewer_enabled: bool
    validator_modes: list[str]
    agent_models: dict[str, str]
    budget_usd: float


_PIPELINE_DEFAULTS: dict[str, Any] = {
    "max_feedback_loops": 2,
    "reviewer_enabled": True,
    "validator_modes": ["test", "lint", "typecheck"],
}


def make_pipeline_config(**kwargs: Any) -> PipelineConfig:
    merged = {**_PIPELINE_DEFAULTS, **kwargs}
    return PipelineConfig(**merged)


class TriageDecision(TypedDict, total=False):
    action: str
    confidence: float
    pipeline_template: str
    pipeline_config: PipelineConfig
    questions: list[str]
    sub_tasks: list[dict[str, Any]]
    reasoning: str


class AgentOutputEntry(TypedDict, total=False):
    agent_type: str
    is_error: bool
    error_message: str
    output: str
    cost_usd: float
    duration_ms: int
    tools_called: dict[str, int]


class JobState(TypedDict, total=False):
    job_id: str
    repo: str
    task: str
    issue_number: int | None
    sandbox_container_id: str | None
    pipeline_config: TriageDecision
    global_context: str | None
    repo_context: str | None
    additional_context: str | None
    agent_outputs: Annotated[list[AgentOutputEntry], operator.add]
    errors: Annotated[list[str], operator.add]
    current_stage: str
    validation_result: dict[str, Any] | None
    feedback_context: str | None
    retry_count: int
    total_cost_usd: float
    budget_overrun_usd: float
    branch_name: str | None
    pr_url: str | None
    result_summary: str | None
