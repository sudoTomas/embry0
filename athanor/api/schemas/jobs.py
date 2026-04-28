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
