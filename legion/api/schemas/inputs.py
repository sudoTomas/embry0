"""Pydantic request/response models for issue inputs."""

from datetime import datetime

from pydantic import BaseModel, Field


class InputResponse(BaseModel):
    id: str
    issue_id: str
    job_id: str
    asking_node: str
    question: str
    importance: str
    auto_answer: str | None
    answer: str | None
    answered_by: str | None
    status: str
    created_at: datetime
    answered_at: datetime | None


class AnswerInputRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=10000)
