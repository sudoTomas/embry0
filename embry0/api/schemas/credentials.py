"""Request/response shapes for /api/v1/credentials (spec 2026-09-29 §3.8)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embry0.api.schemas.repo_preferences import validate_git_author_email

_LABEL = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
# A tailnet's name as the tailnet API takes it in a path: "-" (the key's own
# tailnet) or an organization name such as "example.com".
_TAILNET = re.compile(r"^(-|[A-Za-z0-9][A-Za-z0-9._@+-]{0,252})$")


def _stripped_secret(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("secret must not be blank")
    # A token goes into an HTTP header: whitespace or a control character in it
    # makes httpx refuse the header, and that error quotes the header value.
    if any(c.isspace() or not c.isprintable() for c in v):
        raise ValueError("secret must not contain whitespace or control characters")
    return v


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["forge_token", "llm_oauth", "llm_api_key", "tailnet_api_key"]  # ssh_key opens in a later slice
    provider: Literal["github", "gitlab", "anthropic", "xai", "tailscale"]
    host: str = ""  # "" means the provider's default host
    label: str = Field(min_length=1, max_length=64, pattern=_LABEL)
    secret: str = Field(min_length=1)
    git_author_name: str | None = None
    git_author_email: str | None = None
    # Non-secret settings stored with the secret. Only tailnet_api_key takes
    # any: {"tailnet": "<name>"}, "-" (the key's own tailnet) when omitted.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("secret")
    @classmethod
    def _secret(cls, v: str) -> str:
        return _stripped_secret(v)

    @field_validator("git_author_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return validate_git_author_email(v)

    @model_validator(mode="after")
    def _kind_matches_provider(self) -> CredentialCreate:
        forge = self.provider in ("github", "gitlab")
        llm = self.provider in ("anthropic", "xai")
        if self.kind == "forge_token" and not forge:
            raise ValueError("forge_token credentials must use provider github or gitlab")
        if self.kind in ("llm_oauth", "llm_api_key") and not llm:
            raise ValueError(f"{self.kind} credentials must use provider anthropic or xai")
        if (self.kind == "tailnet_api_key") != (self.provider == "tailscale"):
            raise ValueError("tailnet_api_key credentials use provider tailscale, and tailscale takes no other kind")
        if self.kind == "tailnet_api_key":
            unknown = sorted(set(self.metadata) - {"tailnet"})
            if unknown:
                raise ValueError(f"tailnet_api_key metadata takes only 'tailnet', got {unknown}")
            tailnet = self.metadata.get("tailnet", "-")
            if not isinstance(tailnet, str) or not _TAILNET.match(tailnet):
                raise ValueError("metadata.tailnet must be '-' or a tailnet name of letters, digits and . _ @ + -")
            self.metadata = {"tailnet": tailnet}
        elif self.metadata:
            raise ValueError("metadata is only accepted for tailnet_api_key credentials")
        # The secret is a JSON refresh payload; the single-line rule rejects it.
        # Device-code login and the importer are the only writers.
        if self.provider == "xai" and self.kind == "llm_oauth":
            raise ValueError(
                "xAI llm_oauth accounts are created by device-code login "
                "(POST /credentials/xai/device-login) or `embry0 credentials import-env`"
            )
        return self


class CredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=64, pattern=_LABEL)
    git_author_name: str | None = None
    git_author_email: str | None = None
    status: Literal["ok", "disabled"] | None = None

    @field_validator("git_author_name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        # Blank clears, as null does (#440); the email validator does the same.
        return (v.strip() or None) if v is not None else None

    @field_validator("git_author_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return validate_git_author_email(v)


class CredentialRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1)

    @field_validator("secret")
    @classmethod
    def _secret(cls, v: str) -> str:
        return _stripped_secret(v)


class BindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str = Field(pattern=r"^cred-[0-9a-f]{12}$")
    role: Literal["forge", "llm", "deploy", "tailnet"]
    pinned: bool = False
    priority: int = 0


class BindingsSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[BindingInput]


class ScopedBindingsSet(BindingsSet):
    scope_kind: Literal["owner", "host", "global"]
    scope_value: str = ""


class ImportEnvRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replace: bool = False


class CredentialOut(BaseModel):
    id: str
    kind: str
    provider: str
    host: str
    label: str
    git_author_name: str | None = None
    git_author_email: str | None = None
    masked_preview: str
    verified_login: str | None = None
    status: str
    status_reason: str | None = None
    quarantined_until: datetime | str | None = None
    binding_count: int = 0
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    rotated_at: datetime | str | None = None
    last_used_at: datetime | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolveOut(BaseModel):
    id: str | None
    label: str
    provider: str
    host: str
    source: str
    trace: list[str]


class XaiDeviceLoginStart(BaseModel):
    """Body of ``POST /credentials/xai/device-login``. The client id is not a request field."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64, pattern=_LABEL)


class XaiDeviceLoginStarted(BaseModel):
    login_id: str
    verification_uri_complete: str
    user_code: str
    expires_at: datetime
    interval: int


class XaiDeviceLoginPoll(BaseModel):
    status: Literal["pending", "expired", "done"]
    credential: CredentialOut | None = None
