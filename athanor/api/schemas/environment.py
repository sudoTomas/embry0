"""Environment API request/response models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from athanor.execution.auth_provider import RESERVED_ENV_KEYS  # noqa: F401  # re-exported


class EnvVarInput(BaseModel):
    """Request model for a single env var being set."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str
    var_type: str = Field(default="config", pattern=r"^(config|secret)$")
    description: str = ""
    required: bool = False

    @field_validator("key")
    @classmethod
    def _key_not_reserved(cls, v: str) -> str:
        # Reserved keys would let a user hijack orchestrator-injected infrastructure
        # variables. Reject at the API boundary.
        if v in RESERVED_ENV_KEYS:
            raise ValueError(
                f"Key {v!r} is reserved for Legion infrastructure. Reserved keys: {sorted(RESERVED_ENV_KEYS)}"
            )
        return v


class EnvironmentSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: list[EnvVarInput]


class EnvVarResponse(BaseModel):
    key: str
    value: str  # secrets are replaced with "****" in response
    var_type: str
    description: str
    required: bool = False


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
