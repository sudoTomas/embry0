"""Base state schemas for LangGraph orchestration."""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


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
    agent_tools: dict[str, list[str]]
    agent_skills: dict[str, list[str]]
    budget_usd: float


_PIPELINE_DEFAULTS: dict[str, Any] = {
    "max_feedback_loops": 3,
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
    questions: list[Any]  # list of strings or dicts with question/importance/suggested_answer
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


class PipelineConfigModel(BaseModel):
    """Strict Pydantic equivalent of the PipelineConfig TypedDict.

    Used only at the triage-parse boundary; downstream code continues to
    interact with the dict form stored in JobState (LangGraph requires dict).
    """

    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str = "default"
    max_feedback_loops: int = Field(default=2, ge=0, le=20)
    reviewer_enabled: bool = True
    validator_modes: list[str] = Field(default_factory=list)
    agent_models: dict[str, str] = Field(default_factory=dict)
    agent_tools: dict[str, list[str]] = Field(default_factory=dict)
    agent_skills: dict[str, list[str]] = Field(default_factory=dict)
    budget_usd: float = Field(default=10.0, ge=0.0)


class TriageDecisionModel(BaseModel):
    """Strict Pydantic equivalent of the TriageDecision TypedDict."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(proceed|needs_info|split)$")
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_template: str = "standard"
    pipeline_config: PipelineConfigModel = Field(default_factory=PipelineConfigModel)
    questions: list[dict] = Field(default_factory=list)
    sub_tasks: list[dict] = Field(default_factory=list)
    reasoning: str = ""


class TriageParseError(ValueError):
    """Raised when LLM output can't be parsed into a TriageDecisionModel.

    Callers should mark the job failed with ErrorCode.TRIAGE_MALFORMED and
    log the raw LLM output for debugging.
    """


class JobState(TypedDict, total=False):
    job_id: str
    repo: str
    task: str
    issue_number: int | None
    issue_id: str | None
    sandbox_container_id: str | None
    pipeline_config: TriageDecision
    global_context: str | None
    repo_context: str | None
    additional_context: str | None
    auth_proxy_url: str | None
    git_proxy_url: str | None
    legion_proxy_url: str | None
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
    pending_agent_questions: list[dict[str, Any]]
    user_answers: Any
