"""Repo preferences API models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from embry0.workflows.verification.config import VerificationConfig

# Spec 1A §3.7. Same vocabulary as embry0/workspace_init/git.py::_REF_RE and
# embry0/execution/workspace_diff.py::_BRANCH_RE — the value is interpolated
# into sandbox shell lines, so the API is where the vocabulary is enforced.
BASE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


def validate_git_author_email(v: str | None) -> str | None:
    """Stripped address, None when blank; ValueError when implausible.

    Light shape check only — the point of the field is to satisfy the
    target org's push rules, not to fully validate RFC 5322. An address
    without "@" or with whitespace is the exact "fabricated email"
    failure mode EMB-51 is fixing, so reject it here.
    """
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if "@" not in v or any(c.isspace() for c in v):
        raise ValueError("git_author_email must be a plausible email address (contain '@', no whitespace)")
    return v


class DevCheck(BaseModel):
    """One pre-PR self-check: run ``command`` when the diff touches any of
    ``paths`` (repo-root-relative prefixes; empty = always)."""

    # RAV-1254.

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    paths: list[str] = Field(default_factory=list, max_length=20)
    command: str = Field(min_length=1, max_length=1000)

    @field_validator("paths")
    @classmethod
    def _clean_paths(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip().strip("/") for p in v]
        if any(not p for p in cleaned):
            raise ValueError("paths entries must be non-empty repo-relative prefixes")
        return cleaned


class PrReviewSettings(BaseModel):
    """#30: per-repo pull request review. Off until an operator turns it on."""

    model_config = ConfigDict(extra="forbid")

    # Strict: pydantic would read "yes" as true, and a typo must not turn reviews on.
    enabled: StrictBool = False
    depth: Literal["full", "targeted", "read_only"] = "full"
    # None = the deployment's heavy tier (MODEL_HEAVY).
    model: str | None = Field(default=None, max_length=200)
    # Whose ready pull requests start a review on their own: "team" (write
    # access, no bots) or "anyone". `/embry0 review` keeps its own check.
    ready_authors: Literal["team", "anyone"] = "team"

    @field_validator("model")
    @classmethod
    def _blank_is_the_default(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class RepoPreferencesResponse(BaseModel):
    repo: str
    sandbox_profile: str | None
    language_hint: str | None
    notes: str
    execution_mode: str | None
    auth_mode: str | None
    git_author_name: str | None
    git_author_email: str | None
    dev_checks: list[DevCheck] = Field(default_factory=list)
    base_branch: str | None = None
    # #298/#299. Untyped on the way OUT so one odd stored object can never
    # 500 the read; the request model below is where the shape is enforced.
    verification: dict[str, Any] | None = None
    # #30. Untyped on the way out for the same reason as verification.
    pr_review: dict[str, Any] | None = None
    updated_at: datetime


class RepoPreferencesUpdateRequest(BaseModel):
    """A partial update (#350): only the fields present in the body are
    written. Omit a field to keep it; send null (``""`` for ``notes``,
    ``[]`` for ``dev_checks``, null or ``{}`` for ``verification``) to clear
    it. The route reads ``model_dump(exclude_unset=True)``, so the defaults
    below never reach storage."""

    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str | None = None
    language_hint: str | None = None
    notes: str = ""
    execution_mode: str | None = None
    auth_mode: str | None = None
    git_author_name: str | None = None
    git_author_email: str | None = None
    dev_checks: list[DevCheck] = Field(default_factory=list, max_length=20)
    base_branch: str | None = None
    # #298/#299: the orchestrator-run test gate + tier path rules.
    verification: VerificationConfig | None = None
    # #30: pull request review settings, stored whole.
    pr_review: PrReviewSettings | None = None

    @field_validator("base_branch")
    @classmethod
    def _branch_shape(cls, v: str | None) -> str | None:
        # Blank means "no preference" (ask the forge), never the empty branch
        # name — `git fetch origin :` is a wildcard push refspec, not a no-op.
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not BASE_BRANCH_RE.match(v):
            raise ValueError(
                "base_branch must be 1-255 characters from [A-Za-z0-9._/-] (e.g. 'dev', 'release/2026.09')"
            )
        return v

    @field_validator("git_author_email")
    @classmethod
    def _email_shape(cls, v: str | None) -> str | None:
        return validate_git_author_email(v)
