"""Pydantic request models for the jobs API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobResumeRequest(BaseModel):
    """POST /api/v1/jobs/{job_id}/resume body.

    `choice` drives the executor's resume action — only the three documented
    values are accepted. `guidance` is free-form prose appended to the
    developer's prompt.
    """

    model_config = ConfigDict(extra="forbid")

    choice: Literal["continue_retrying", "merge_as_is", "abandon"]
    guidance: str = Field(default="", max_length=50000)
    #: With continue_retrying on a job over its budget: how much more it may spend.
    #: Absent, a job over its budget may spend one more cap.
    extra_budget_usd: float | None = Field(default=None, gt=0, le=1000)


class DoneWhenItem(BaseModel):
    """One condition of a job's finish line, and how it is shown."""

    model_config = ConfigDict(extra="forbid")

    done: str = Field(min_length=1, max_length=300)
    shown_by: str = Field(default="", max_length=300)


class DoneWhenEditRequest(BaseModel):
    """PUT /api/v1/jobs/{job_id}/done-when body: a person's finish line for the job (spec 2026-10-26)."""

    model_config = ConfigDict(extra="forbid")

    items: list[DoneWhenItem] = Field(min_length=1, max_length=6)
    out_of_scope: list[str] = Field(default_factory=list, max_length=6)
