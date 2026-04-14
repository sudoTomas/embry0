"""Repo preferences API models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepoPreferencesResponse(BaseModel):
    repo: str
    sandbox_profile: str | None
    language_hint: str | None
    notes: str
    updated_at: datetime


class RepoPreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str | None = None
    language_hint: str | None = None
    notes: str = ""
