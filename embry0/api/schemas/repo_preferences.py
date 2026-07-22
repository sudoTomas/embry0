"""Repo preferences API models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class RepoPreferencesResponse(BaseModel):
    repo: str
    sandbox_profile: str | None
    language_hint: str | None
    notes: str
    execution_mode: str | None
    auth_mode: str | None
    git_author_name: str | None
    git_author_email: str | None
    updated_at: datetime


class RepoPreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str | None = None
    language_hint: str | None = None
    notes: str = ""
    execution_mode: str | None = None
    auth_mode: str | None = None
    git_author_name: str | None = None
    git_author_email: str | None = None

    @field_validator("git_author_email")
    @classmethod
    def _email_shape(cls, v: str | None) -> str | None:
        # Light shape check only — the point of the field is to satisfy the
        # target org's push rules, not to fully validate RFC 5322. An address
        # without "@" or with whitespace is the exact "fabricated email"
        # failure mode EMB-51 is fixing, so reject it here.
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if "@" not in v or any(c.isspace() for c in v):
            raise ValueError("git_author_email must be a plausible email address (contain '@', no whitespace)")
        return v
