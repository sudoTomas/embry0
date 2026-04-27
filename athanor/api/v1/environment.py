"""Environment configuration endpoints — global and per-repo env vars with secret masking."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

from athanor.api.schemas.environment import (
    DetectedEnvVar,
    DetectResponse,
    EnvironmentResponse,
    EnvironmentSetRequest,
    EnvVarResponse,
    RevealResponse,
)
from athanor.storage.encryption import FernetSecretsProvider

logger = structlog.get_logger(__name__)

router = APIRouter()

_SECRET_MASK = "****"
_DEFAULT_SECRET_KEY = "athanor-dev-secret-key"


@lru_cache(maxsize=1)
def _warn_default_key_once() -> None:
    logger.warning(
        "using_default_secret_key",
        message="ENVIRONMENT_SECRET_KEY not set — using insecure default. Set in production!",
    )


def _get_secrets_provider(secret_key: str) -> FernetSecretsProvider:
    if not secret_key:
        _warn_default_key_once()
        secret_key = _DEFAULT_SECRET_KEY
    return FernetSecretsProvider(secret_key=secret_key)


def _mask_var(var: dict[str, Any]) -> EnvVarResponse:
    return EnvVarResponse(
        key=var["key"],
        value=_SECRET_MASK if var.get("var_type") == "secret" else var["value"],
        var_type=var.get("var_type", "config"),
        description=var.get("description", ""),
        required=bool(var.get("required", False)),
    )


async def _encrypt_vars(variables: list[dict[str, Any]], provider: FernetSecretsProvider) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in variables:
        d = dict(v)
        if d.get("var_type") == "secret":
            d["value"] = await provider.encrypt(d["value"])
        out.append(d)
    return out


async def _decrypt_vars(variables: list[dict[str, Any]], provider: FernetSecretsProvider) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in variables:
        d = dict(v)
        if d.get("var_type") == "secret":
            try:
                d["value"] = await provider.decrypt(d["value"])
            except Exception:
                logger.warning("secret_decryption_failed", key=d["key"])
                d["value"] = "[DECRYPTION_FAILED]"
        out.append(d)
    return out


# ----- Global endpoints -----


@router.get("/environment/global", response_model=EnvironmentResponse)
async def get_global_environment(request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_global()
    return EnvironmentResponse(variables=[_mask_var(r) for r in rows])


@router.put("/environment/global", response_model=EnvironmentResponse)
async def set_global_environment(req: EnvironmentSetRequest, request: Request) -> EnvironmentResponse:
    provider = _get_secrets_provider(request.app.state.config.environment_secret_key)
    env_repo = request.app.state.env_repo
    raw = [v.model_dump() for v in req.variables]
    encrypted = await _encrypt_vars(raw, provider)
    await env_repo.set_global(encrypted)
    rows = await env_repo.get_global()
    return EnvironmentResponse(variables=[_mask_var(r) for r in rows])


@router.get("/environment/global/{key}/reveal", response_model=RevealResponse)
async def reveal_global_secret(key: str, request: Request) -> RevealResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_global()
    var = next((r for r in rows if r["key"] == key), None)
    if not var:
        raise HTTPException(status_code=404, detail=f"Variable '{key}' not found")
    if var.get("var_type") == "secret":
        provider = _get_secrets_provider(request.app.state.config.environment_secret_key)
        try:
            var["value"] = await provider.decrypt(var["value"])
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt") from None

    # Audit trail: who unmasked what secret.
    from athanor.audit.helpers import emit_audit

    actor = request.client.host if request.client else "unknown"
    await emit_audit(
        request.app.state.db,
        "environment.secret_revealed",
        actor=actor,
        details={"scope": "global", "key": key},
        audit_log_path=request.app.state.config.audit_log_path,
    )
    return RevealResponse(key=var["key"], value=var["value"])


# ----- Per-repo endpoints -----


@router.get("/repos/{owner}/{repo}/environment", response_model=EnvironmentResponse)
async def get_repo_environment(owner: str, repo: str, request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_repo(f"{owner}/{repo}")
    return EnvironmentResponse(variables=[_mask_var(r) for r in rows])


@router.get("/repos/{owner}/{repo}/environment/resolve", response_model=EnvironmentResponse)
async def resolve_repo_environment(owner: str, repo: str, request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    merged: dict[str, dict] = {}
    for v in await env_repo.get_global():
        merged[v["key"]] = dict(v)
    for v in await env_repo.get_repo(f"{owner}/{repo}"):
        merged[v["key"]] = dict(v)
    return EnvironmentResponse(variables=[_mask_var(v) for v in merged.values()])


@router.put("/repos/{owner}/{repo}/environment", response_model=EnvironmentResponse)
async def set_repo_environment(
    owner: str, repo: str, req: EnvironmentSetRequest, request: Request
) -> EnvironmentResponse:
    provider = _get_secrets_provider(request.app.state.config.environment_secret_key)
    env_repo = request.app.state.env_repo
    raw = [v.model_dump() for v in req.variables]
    encrypted = await _encrypt_vars(raw, provider)
    await env_repo.set_repo(f"{owner}/{repo}", encrypted)
    rows = await env_repo.get_repo(f"{owner}/{repo}")
    return EnvironmentResponse(variables=[_mask_var(r) for r in rows])


@router.get("/repos/{owner}/{repo}/environment/{key}/reveal", response_model=RevealResponse)
async def reveal_repo_secret(owner: str, repo: str, key: str, request: Request) -> RevealResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_repo(f"{owner}/{repo}")
    var = next((r for r in rows if r["key"] == key), None)
    if not var:
        raise HTTPException(status_code=404, detail=f"Variable '{key}' not found")
    if var.get("var_type") == "secret":
        provider = _get_secrets_provider(request.app.state.config.environment_secret_key)
        try:
            var["value"] = await provider.decrypt(var["value"])
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt") from None

    from athanor.audit.helpers import emit_audit

    actor = request.client.host if request.client else "unknown"
    await emit_audit(
        request.app.state.db,
        "environment.secret_revealed",
        actor=actor,
        details={"scope": "repo", "repo": f"{owner}/{repo}", "key": key},
        audit_log_path=request.app.state.config.audit_log_path,
    )
    return RevealResponse(key=var["key"], value=var["value"])


@router.delete("/repos/{owner}/{repo}/environment/{key}", status_code=204, response_model=None)
async def delete_repo_env_var(owner: str, repo: str, key: str, request: Request) -> None:
    env_repo = request.app.state.env_repo
    await env_repo.delete_repo_var(f"{owner}/{repo}", key)


# ----- Auto-detection -----

_SECRET_KEYWORDS = re.compile(r"SECRET|KEY|TOKEN|PASSWORD|CREDENTIALS|PRIVATE", re.IGNORECASE)

_ENV_TEMPLATE_FILES = [
    ".env.agents.template",
    ".env.example",
    ".env.sample",
    ".env.template",
]


def _parse_env_file(content: str) -> list[dict[str, Any]]:
    """Parse KEY=VALUE lines from an .env template file.

    Lines starting with ``#`` immediately before a KEY=VALUE line are treated
    as the description for that variable.  Keys whose name contains SECRET,
    KEY, TOKEN, PASSWORD, CREDENTIALS, or PRIVATE are auto-classified as ``secret``.
    """
    results: list[dict[str, Any]] = []
    pending_comment = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            pending_comment = ""
            continue
        if line.startswith("#"):
            pending_comment = line.lstrip("#").strip()
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not m:
            pending_comment = ""
            continue
        key = m.group(1)
        value = m.group(2).strip().strip("\"'")
        suggested_type = "secret" if _SECRET_KEYWORDS.search(key) else "config"
        results.append(
            {
                "key": key,
                "default_value": value if value else None,
                "description": pending_comment,
                "suggested_type": suggested_type,
            }
        )
        pending_comment = ""
    return results


@router.get("/repos/{owner}/{repo}/environment/detect", response_model=DetectResponse)
async def detect_repo_environment(owner: str, repo: str, request: Request) -> DetectResponse:
    full_repo = f"{owner}/{repo}"
    # Source the token from config (single source of truth); env-var read was a
    # divergence that would silently break if config ever moved off env.
    github_token = getattr(request.app.state.config, "github_token", "") or ""

    headers: dict[str, str] = {"Accept": "application/vnd.github.raw+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    content: str | None = None
    source_file = ""

    # Bounded timeout — a stuck GitHub response must not hold a request worker.
    async with httpx.AsyncClient(timeout=10.0) as client:
        for filename in _ENV_TEMPLATE_FILES:
            url = f"https://api.github.com/repos/{full_repo}/contents/{filename}"
            try:
                resp = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning("env_detect_http_error", repo=full_repo, file=filename, error=str(exc))
                continue
            if resp.status_code == 200:
                content = resp.text
                source_file = filename
                break

    if content is None:
        return DetectResponse(source_file="", variables=[], unconfigured_count=0)

    detected = _parse_env_file(content)
    env_repo = request.app.state.env_repo
    global_keys = {v["key"] for v in await env_repo.get_global()}
    repo_keys = {v["key"] for v in await env_repo.get_repo(full_repo)}
    configured_keys = global_keys | repo_keys

    def _source(key: str) -> str | None:
        if key in repo_keys:
            return "repo"
        if key in global_keys:
            return "global"
        return None

    result_vars = [
        DetectedEnvVar(
            key=d["key"],
            default_value=d["default_value"],
            description=d["description"],
            suggested_type=d["suggested_type"],
            is_configured=d["key"] in configured_keys,
            source=_source(d["key"]),
        )
        for d in detected
    ]
    return DetectResponse(
        source_file=source_file,
        variables=result_vars,
        unconfigured_count=sum(1 for v in result_vars if not v.is_configured),
    )
