"""Environment API request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from embry0.execution.auth_provider import (  # re-exported
    RESERVED_ENV_KEYS,
    RESERVED_ENV_PREFIXES,
    env_key_refusal,
    env_value_refusal,
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
        # variables, or hook every sandbox process (#524). Reject at the API
        # boundary; the sandbox injection filters run the same check.
        refusal = env_key_refusal(v)
        if refusal is not None:
            raise ValueError(refusal)
        return v

    @field_validator("value")
    @classmethod
    def _value_allowed_for_key(cls, v: str, info: ValidationInfo) -> str:
        # #524: NODE_OPTIONS and the JVM option variables are settable, but
        # only to memory flags. `key` is missing from info.data when it failed
        # its own validation, which has already reported the error.
        key = info.data.get("key")
        if key is not None:
            refusal = env_value_refusal(key, v)
            if refusal is not None:
                raise ValueError(refusal)
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
    # RAV-1294: a secret preview that shows at most its last 2 characters (e.g. "••••••••2F").
    # None for config vars (value is already plain) and when decryption fails.
    masked_preview: str | None = None


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
