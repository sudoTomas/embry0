"""Environment configuration endpoints — global and per-repo env vars with secret masking."""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas.environment import (
    DetectedEnvVar,
    DetectResponse,
    EnvironmentResponse,
    EnvironmentSetRequest,
    EnvVarResponse,
    RevealResponse,
)
from embry0.execution.github_client import GitHubClient
from embry0.storage.encryption import encrypt_vars

logger = structlog.get_logger(__name__)

router = APIRouter()

_SECRET_MASK = "****"
# Fixed-length glyph run so a preview never leaks the secret's true length.
_MASK_GLYPHS = "•" * 8


def partial_mask(plaintext: str) -> str:
    """RAV-1294: a preview that tells two secrets apart without giving one away.

    Every caller that can list env vars sees it, read keys included, so it
    shows at most the last 2 characters, and only of a secret of 12 or more;
    anything shorter is all glyphs. The glyph run is fixed-length so a
    preview does not leak the value's length.
    """
    if len(plaintext) < 12:
        return _MASK_GLYPHS
    return f"{_MASK_GLYPHS}{plaintext[-2:]}"


def _restore_unchanged_secrets(incoming: list[dict[str, Any]], stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A masked secret means leave the stored ciphertext alone.

    List responses replace secret values with ``****``. A client that PUTs
    that list back would otherwise encrypt the mask and wipe the secret.
    """
    stored_by_key = {row["key"]: row for row in stored}
    prepared: list[dict[str, Any]] = []
    for var in incoming:
        row = dict(var)
        if row.get("var_type") == "secret" and row.get("value") == _SECRET_MASK:
            existing = stored_by_key.get(row["key"])
            if existing is None or existing.get("var_type") != "secret":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Secret {row['key']!r} has no stored value to keep. "
                        "Send a new value. The mask means leave the stored secret unchanged."
                    ),
                )
            row["value"] = existing["value"]
            row["_ciphertext"] = True
        prepared.append(row)
    return prepared


async def _encrypt_new_secrets(variables: list[dict[str, Any]], provider: Any) -> list[dict[str, Any]]:
    fresh = [row for row in variables if not row.get("_ciphertext")]
    encrypted = iter(await encrypt_vars(fresh, provider))
    out: list[dict[str, Any]] = []
    for row in variables:
        if row.get("_ciphertext"):
            out.append({key: value for key, value in row.items() if key != "_ciphertext"})
        else:
            out.append(next(encrypted))
    return out


def _mask_var(var: dict[str, Any], preview: str | None = None) -> EnvVarResponse:
    return EnvVarResponse(
        key=var["key"],
        value=_SECRET_MASK if var.get("var_type") == "secret" else var["value"],
        var_type=var.get("var_type", "config"),
        description=var.get("description", ""),
        required=bool(var.get("required", False)),
        scope=var.get("scope", "app"),
        masked_preview=preview,
    )


async def _mask_vars(rows: list[dict[str, Any]], provider: Any) -> list[EnvVarResponse]:
    """Mask a row list, decrypting each secret only to derive its preview.

    Fail-open per row: an undecryptable value (rotated-away key, legacy
    plaintext) just gets no preview — the listing itself must never 500.
    """
    out: list[EnvVarResponse] = []
    for var in rows:
        preview: str | None = None
        if var.get("var_type") == "secret" and provider is not None:
            try:
                preview = partial_mask(await provider.decrypt(var["value"]))
            except Exception:
                preview = None
        out.append(_mask_var(var, preview))
    return out


# ----- Global endpoints -----


@router.get("/environment/global", response_model=EnvironmentResponse)
async def get_global_environment(request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_global()
    provider = getattr(request.app.state, "secrets_provider", None)
    return EnvironmentResponse(variables=await _mask_vars(rows, provider))


@router.put("/environment/global", response_model=EnvironmentResponse)
async def set_global_environment(req: EnvironmentSetRequest, request: Request) -> EnvironmentResponse:
    provider = request.app.state.secrets_provider
    env_repo = request.app.state.env_repo
    raw = [v.model_dump() for v in req.variables]
    prepared = _restore_unchanged_secrets(raw, await env_repo.get_global())
    encrypted = await _encrypt_new_secrets(prepared, provider)
    await env_repo.set_global(encrypted)
    rows = await env_repo.get_global()
    return EnvironmentResponse(variables=await _mask_vars(rows, provider))


@router.get("/environment/global/{key}/reveal", response_model=RevealResponse, dependencies=[require_admin])
async def reveal_global_secret(key: str, request: Request) -> RevealResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_global()
    var = next((r for r in rows if r["key"] == key), None)
    if not var:
        raise HTTPException(status_code=404, detail=f"Variable '{key}' not found")
    if var.get("var_type") == "secret":
        provider = request.app.state.secrets_provider
        try:
            var["value"] = await provider.decrypt(var["value"])
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt") from None

    # Audit trail: who unmasked what secret.
    from embry0.audit.helpers import emit_audit

    actor = request_actor(request)
    await emit_audit(
        request.app.state.db,
        "environment.secret_revealed",
        actor=actor,
        details={"scope": "global", "key": key},
        audit_log_path=request.app.state.config.audit_log_path,
    )
    return RevealResponse(key=var["key"], value=var["value"])


# ----- Per-repo endpoints -----


@router.get("/repos/{full_repo:repo}/-/environment", response_model=EnvironmentResponse)
async def get_repo_environment(full_repo: str, request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_repo(full_repo)
    provider = getattr(request.app.state, "secrets_provider", None)
    return EnvironmentResponse(variables=await _mask_vars(rows, provider))


@router.get("/repos/{full_repo:repo}/-/environment/resolve", response_model=EnvironmentResponse)
async def resolve_repo_environment(full_repo: str, request: Request) -> EnvironmentResponse:
    env_repo = request.app.state.env_repo
    merged: dict[str, dict[str, object]] = {}
    for v in await env_repo.get_global():
        merged[v["key"]] = dict(v)
    for v in await env_repo.get_repo(full_repo):
        merged[v["key"]] = dict(v)
    provider = getattr(request.app.state, "secrets_provider", None)
    return EnvironmentResponse(variables=await _mask_vars(list(merged.values()), provider))


@router.put("/repos/{full_repo:repo}/-/environment", response_model=EnvironmentResponse)
async def set_repo_environment(full_repo: str, req: EnvironmentSetRequest, request: Request) -> EnvironmentResponse:
    await enforce_repo_allowlist(request, full_repo, action="change a repository's variables")
    provider = request.app.state.secrets_provider
    env_repo = request.app.state.env_repo
    raw = [v.model_dump() for v in req.variables]
    prepared = _restore_unchanged_secrets(raw, await env_repo.get_repo(full_repo))
    encrypted = await _encrypt_new_secrets(prepared, provider)
    await env_repo.set_repo(full_repo, encrypted)
    rows = await env_repo.get_repo(full_repo)
    return EnvironmentResponse(variables=await _mask_vars(rows, provider))


@router.get(
    "/repos/{full_repo:repo}/-/environment/{key}/reveal", response_model=RevealResponse, dependencies=[require_admin]
)
async def reveal_repo_secret(full_repo: str, key: str, request: Request) -> RevealResponse:
    env_repo = request.app.state.env_repo
    rows = await env_repo.get_repo(full_repo)
    var = next((r for r in rows if r["key"] == key), None)
    if not var:
        raise HTTPException(status_code=404, detail=f"Variable '{key}' not found")
    if var.get("var_type") == "secret":
        provider = request.app.state.secrets_provider
        try:
            var["value"] = await provider.decrypt(var["value"])
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt") from None

    from embry0.audit.helpers import emit_audit

    actor = request_actor(request)
    await emit_audit(
        request.app.state.db,
        "environment.secret_revealed",
        actor=actor,
        details={"scope": "repo", "repo": full_repo, "key": key},
        audit_log_path=request.app.state.config.audit_log_path,
    )
    return RevealResponse(key=var["key"], value=var["value"])


@router.delete("/repos/{full_repo:repo}/-/environment/{key}", status_code=204, response_model=None)
async def delete_repo_env_var(full_repo: str, key: str, request: Request) -> None:
    await enforce_repo_allowlist(request, full_repo, action="change a repository's variables")
    env_repo = request.app.state.env_repo
    await env_repo.delete_repo_var(full_repo, key)


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


@router.get("/repos/{full_repo:repo}/-/environment/detect", response_model=DetectResponse)
async def detect_repo_environment(full_repo: str, request: Request) -> DetectResponse:
    full_repo = full_repo
    # Route by owner: fine-grained PATs are single-owner, so the default token
    # 404s on GITHUB_TOKEN__<OWNER> repos. Default still comes from config
    # (single source of truth), not a raw env read.
    from embry0.services.credentials.resolver import CredentialUnavailableError, forge_token

    resolver = getattr(request.app.state, "credential_resolver", None)
    try:
        github_token = await forge_token(
            resolver, full_repo, "github", getattr(request.app.state.config, "github_token", "") or ""
        )
    except CredentialUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    content: str | None = None
    source_file = ""

    # Bounded timeout — a stuck GitHub response must not hold a request worker.
    client = GitHubClient(default_token=github_token, timeout=10.0, credential_resolver=resolver)
    for filename in _ENV_TEMPLATE_FILES:
        try:
            resp = await client.get(
                f"/repos/{full_repo}/contents/{filename}",
                repo=full_repo,
                accept="application/vnd.github.raw+json",
            )
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
