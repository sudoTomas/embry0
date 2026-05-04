"""Environment API request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from athanor.execution.auth_provider import (  # re-exported
    RESERVED_ENV_KEYS,
    RESERVED_ENV_PREFIXES,
)

__all__ = [
    "RESERVED_ENV_KEYS",
    "RESERVED_ENV_PREFIXES",
    "DetectResponse",
    "DetectedEnvVar",
    "EnvVarInput",
    "EnvVarResponse",
    "EnvironmentResponse",
    "EnvironmentSetRequest",
    "RevealResponse",
]


class EnvVarInput(BaseModel):
    """Request model for a single env var being set."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str
    var_type: str = Field(default="config", pattern=r"^(config|secret)$")
    description: str = ""
    required: bool = False
    scope: Literal["app", "qa"] = "app"

    @field_validator("key")
    @classmethod
    def _key_not_reserved(cls, v: str) -> str:
        # Reserved keys would let a user hijack orchestrator-injected infrastructure
        # variables. Reject at the API boundary.
        if v in RESERVED_ENV_KEYS:
            raise ValueError(
                f"Key {v!r} is reserved for Athanor infrastructure. Reserved keys: {sorted(RESERVED_ENV_KEYS)}"
            )
        # Reserved prefixes — every key starting with these is server-controlled.
        for p in RESERVED_ENV_PREFIXES:
            if v.startswith(p):
                raise ValueError(
                    f"Key {v!r} starts with reserved prefix {p!r}; these are orchestrator-injected at sandbox start."
                )
        return v

    @model_validator(mode="after")
    def _qa_scope_requires_qa_prefix(self) -> EnvVarInput:
        if self.scope == "qa" and not self.key.startswith("QA_"):
            raise ValueError(
                f"Keys with scope='qa' must start with 'QA_' (got {self.key!r}). Use scope='app' for app config."
            )
        return self


class EnvironmentSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: list[EnvVarInput]


class EnvVarResponse(BaseModel):
    key: str
    value: str  # secrets are replaced with "****" in response
    var_type: str
    description: str
    required: bool = False
    scope: Literal["app", "qa"] = "app"


class EnvironmentResponse(BaseModel):
    variables: list[EnvVarResponse]


class RevealResponse(BaseModel):
    key: str
    value: str


class DetectedEnvVar(BaseModel):
    key: str
    default_value: str | None
    description: str
    suggested_type: str
    is_configured: bool
    source: str | None  # "repo" | "global" | None


class DetectResponse(BaseModel):
    source_file: str
    variables: list[DetectedEnvVar]
    unconfigured_count: int
