"""Pydantic schema for `.athanor/qa.yaml` — the per-repo QA contract.

Source of truth for "how does the QA agent boot and validate this repo".
Lives in the target repo (not in Athanor) so it travels with the code.
"""

from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class QAReadyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http: str
    expect_status: int = Field(default=200, ge=100, le=599)
    expect_body_regex: str | None = None

    @field_validator("http")
    @classmethod
    def _http_url_only(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"http must be an http(s) URL, got {v!r}")
        return v


class QAStartup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    ready_checks: list[QAReadyCheck] = Field(min_length=1)
    boot_timeout_seconds: int = Field(default=300, gt=0, le=3600)


class QASeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=120, gt=0, le=1800)


class QAE2E(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=600, gt=0, le=3600)


class QAYamlConfig(BaseModel):
    """Top-level .athanor/qa.yaml contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: Literal["dind", "process"]
    sandbox_profile: str = Field(min_length=1)

    startup: QAStartup
    seed: QASeed | None = None
    frontend_url: str

    e2e: QAE2E | None = None

    acceptance_criteria_template: list[str] = Field(default_factory=list)
    qa_required: Literal["auto", "always", "never"] = "auto"


def parse_qa_yaml(raw: str) -> QAYamlConfig:
    """Parse + validate a YAML document. Raises ValidationError on bad content."""
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("qa.yaml must be a YAML mapping at the top level")
    return QAYamlConfig.model_validate(data)
