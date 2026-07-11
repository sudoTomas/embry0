"""Pydantic models for the structured tool calls triage emits.

Triage's role expanded over time: pipeline routing decisions, ask_user
prompts, and (Phase 5) QA decisions and QA-failure handling. Each
distinct action is a Pydantic model that the agent SDK serializes as a
tool call schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SetQADecision(BaseModel):
    """Triage's QA decision for issue→PR jobs."""

    model_config = ConfigDict(extra="forbid")

    needs_qa: bool
    reason: str = Field(min_length=1, max_length=500)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)


class RetryDeveloper(BaseModel):
    """After a QA failure, ask developer to fix with specific guidance."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=10, max_length=4000)
    focus_files: list[str] = Field(default_factory=list, max_length=20)


class RerunQA(BaseModel):
    """After a QA failure, declare it environmental/flaky and rerun unchanged."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class AskUser(BaseModel):
    """After a QA failure, escalate to the user via the existing ask_user channel."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=10, max_length=2000)


# Discriminated union for the QA failure response — exactly one action.
# Consumers parse incoming payloads via ``pydantic.TypeAdapter(QAFailureAction).validate_python(...)``.
QAFailureAction = RetryDeveloper | RerunQA | AskUser
