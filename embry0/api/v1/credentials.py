"""Credential registry endpoints (spec 2026-09-29 §3.8). No reveal endpoint, by design."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import asyncpg
import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from embry0.api.deps import request_actor, require_admin
from embry0.api.schemas.credentials import (
    BindingsSet,
    CredentialCreate,
    CredentialOut,
    CredentialRotate,
    CredentialUpdate,
    ImportEnvRequest,
    ResolveOut,
    ScopedBindingsSet,
    XaiDeviceLoginPoll,
    XaiDeviceLoginStart,
    XaiDeviceLoginStarted,
)
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.safety.redaction import register_known_secrets
from embry0.services.credentials.import_env import import_from_env
from embry0.services.credentials.preview import masked_preview
from embry0.services.credentials.providers import base_url_host, default_host, llm_providers
from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError
from embry0.services.hosts import TAILNET_API_BASE
from embry0.storage.repositories.credentials import (
    BindingValidationError,
    CredentialBoundError,
    normalize_scope_value,
)
from embry0.storage.repositories.repos import UnregisteredRepoError

router = APIRouter()

TAILNET_API_HOST = urlparse(TAILNET_API_BASE).hostname or ""


def _state(request: Request) -> Any:
    return request.app.state


def _repo_path(value: str) -> str:
    """The validated path as given (the ``repos`` registry is keyed on it); 422 when malformed."""
    try:
        return validate_repo_path(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _expected_host(config: Any, provider: str, metadata: Mapping[str, Any] | None = None) -> str:
    if provider == "custom":
        return base_url_host(str((metadata or {}).get("base_url") or ""))
    if provider in llm_providers():
        return default_host(provider)
    if provider == "tailscale":
        return TAILNET_API_HOST
    if provider == "gitlab":
        return (urlparse(str(getattr(config, "gitlab_url", "") or "")).hostname or "gitlab.com").lower()
    return "github.com"


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    await emit_audit(
        _state(request).db,
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=_state(request).config.audit_log_path,
    )


def _invalidate_resolver(request: Request) -> None:
    resolver = getattr(_state(request), "credential_resolver", None)
    if resolver is not None:
        resolver.invalidate()


async def _refresh_scrub_set(request: Request) -> None:
    resolver = getattr(_state(request), "credential_resolver", None)
    if resolver is not None:
        resolver.invalidate()
        register_known_secrets(await resolver.known_secret_values(refresh=True))


# Words the device-login handlers may put in an HTTP body. Anything else is "error".
_DEVICE_LOGIN_REASONS = frozenset(
    {"endpoint_rejected", "unreachable", "error", "unknown", "expired_token", "access_denied"}
)


def _device_login_http(reason: str) -> HTTPException:
    shown = reason if reason in _DEVICE_LOGIN_REASONS else "error"
    return HTTPException(status_code=502 if shown == "unreachable" else 400, detail=shown)


def _device_logins(st: Any) -> dict[str, dict[str, Any]]:
    logins = getattr(st, "xai_device_logins", None)
    if logins is None:
        logins = {}
        st.xai_device_logins = logins
    return logins


async def _drop_device_login(st: Any, login_id: str) -> None:
    pending = _device_logins(st).pop(login_id, None)
    if pending is None:
        return
    http = pending.get("http")
    if http is not None:
        await http.aclose()


def _xai_oauth_secret(refresh_token: str, client_id: str, token_endpoint: str) -> str:
    """The plaintext ``RegistryXaiStore`` decrypts. The access token is not part of it."""
    return json.dumps({"refresh_token": refresh_token, "client_id": client_id, "token_endpoint": token_endpoint})


# Path fragments whose request bodies carry secrets; their 422s never echo input.
_NO_ECHO_PATH_MARKERS: tuple[str, ...] = ("/credentials", "/environment")


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422 echoes the offending ``input``, which for a credential body is the secret.

    Credential and environment routes get the same error list without ``input``: an env
    var's ``value`` may be a secret, and a model-level error's input is the whole variable,
    value included. Every other route keeps FastAPI's default. Registered app-wide in
    ``create_app`` (handlers cannot be per-router).
    """
    if not any(marker in request.url.path for marker in _NO_ECHO_PATH_MARKERS):
        return await request_validation_exception_handler(request, exc)
    errors = [{k: v for k, v in err.items() if k not in ("input", "ctx")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def _out(row: dict[str, Any]) -> CredentialOut:
    return CredentialOut(**{k: v for k, v in row.items() if k in CredentialOut.model_fields})


async def _out_with_count(request: Request, row: dict[str, Any]) -> CredentialOut:
    bound = await _state(request).credentials_repo.bindings_for_credential(row["id"])
    return _out(dict(row, binding_count=len(bound)))


# Literal paths first: /credentials/resolve, /credentials/bindings,
# /credentials/import-env and /credentials/xai/device-login must never be
# captured by /credentials/{credential_id}.


@router.get("/credentials", response_model=list[CredentialOut])
async def list_credentials(request: Request) -> list[CredentialOut]:
    return [_out(r) for r in await _state(request).credentials_repo.list_all()]


@router.post("/credentials", response_model=CredentialOut, status_code=201, dependencies=[require_admin])
async def create_credential(req: CredentialCreate, request: Request) -> CredentialOut:
    st = _state(request)
    expected = _expected_host(st.config, req.provider, req.metadata)
    host = (req.host.strip() or expected).lower()
    if host != expected:
        raise HTTPException(status_code=422, detail=f"host for {req.provider} must be {expected!r} in this deployment")
    try:
        row = await st.credentials_repo.create(
            kind=req.kind,
            provider=req.provider,
            host=host,
            label=req.label,
            secret_ciphertext=await st.secrets_provider.encrypt(req.secret),
            masked_preview=masked_preview(req.secret),
            created_by=request_actor(request),
            git_author_name=req.git_author_name,
            git_author_email=req.git_author_email,
            metadata=req.metadata,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409, detail=f"a {req.provider} credential labelled {req.label!r} already exists"
        ) from None
    await _audit(
        request, "credential.created", {"credential_id": row["id"], "provider": req.provider, "label": req.label}
    )
    await _refresh_scrub_set(request)
    return _out(dict(row, binding_count=0))


def _resolve_out(r: Any) -> ResolveOut:
    return ResolveOut(id=r.id, label=r.label, provider=r.provider, host=r.host, source=r.source, trace=list(r.trace))


@router.get("/credentials/resolve", response_model=ResolveOut)
async def resolve_credential(
    request: Request,
    repo: str = Query(...),
    role: str = Query(default="forge"),
    provider: str = Query(default=""),
) -> ResolveOut:
    st = _state(request)
    repo = _repo_path(repo)
    if role == "llm":
        if not provider:
            raise HTTPException(status_code=422, detail="provider is required when role=llm")
        try:
            resolved = await st.credential_resolver.resolve_llm(repo.lower(), provider)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except CredentialMissingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except CredentialUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return _resolve_out(resolved)
    if role != "forge":
        raise HTTPException(status_code=422, detail="role must be forge or llm")
    try:
        forge = await st.repos_repo.forge_for(repo)
    except UnregisteredRepoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    try:
        r = await st.credential_resolver.resolve_forge(repo.lower(), forge)
    except CredentialMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except CredentialUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _resolve_out(r)


@router.get("/credentials/bindings")
async def get_bindings(
    request: Request,
    scope_kind: Literal["repo", "owner", "host", "global"] = Query(...),
    scope_value: str = Query(default=""),
) -> dict[str, Any]:
    return {"bindings": await _state(request).credentials_repo.bindings_for_scope(scope_kind, scope_value.strip())}


@router.put("/credentials/bindings", dependencies=[require_admin])
async def set_bindings(req: ScopedBindingsSet, request: Request) -> dict[str, Any]:
    st = _state(request)
    scope_value = normalize_scope_value(req.scope_kind, req.scope_value.strip())
    # The resolver only ever asks for '<forge>:<owner>' and the forge's own host
    # (CredentialResolver.resolve_forge); a binding anywhere else is never read.
    owner_forge = ""
    if req.scope_kind == "owner":
        owner_forge, sep, owner = scope_value.partition(":")
        if not sep or owner_forge not in ("github", "gitlab") or not owner:
            raise HTTPException(
                status_code=422,
                detail=f"owner scope_value must be 'github:<owner>' or 'gitlab:<owner>', got {scope_value!r}",
            )
    for b in req.bindings if req.scope_kind in ("owner", "host") else []:
        cred = await st.credentials_repo.get(b.credential_id)
        if b.role != "forge" or cred is None:
            continue  # replace_bindings reports a missing credential or a wrong kind
        provider = str(cred["provider"])
        if owner_forge and provider != owner_forge:
            raise HTTPException(
                status_code=422,
                detail=f"owner scope is on forge '{owner_forge}' but credential '{cred['label']}' is for '{provider}'",
            )
        if req.scope_kind == "host" and provider in ("github", "gitlab"):
            expected = _expected_host(st.config, provider)
            if scope_value != expected:
                raise HTTPException(
                    status_code=422,
                    detail=f"host scope for {provider} credential '{cred['label']}' must be {expected!r}",
                )
    try:
        rows = await st.credentials_repo.replace_bindings(
            req.scope_kind, scope_value, [b.model_dump() for b in req.bindings], created_by=request_actor(request)
        )
    except BindingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _audit(
        request,
        "credential.bound",
        {
            "scope_kind": req.scope_kind,
            "scope_value": scope_value,
            "credential_ids": [b.credential_id for b in req.bindings],
        },
    )
    _invalidate_resolver(request)
    return {"bindings": rows}


@router.post("/credentials/import-env", dependencies=[require_admin])
async def import_env(req: ImportEnvRequest, request: Request) -> dict[str, list[str]]:
    st = _state(request)
    report = await import_from_env(
        st.credentials_repo, st.secrets_provider, st.config, replace=req.replace, actor=request_actor(request)
    )
    await _audit(request, "credential.imported", report.as_dict())
    await _refresh_scrub_set(request)
    return report.as_dict()


@router.post(
    "/credentials/xai/device-login",
    response_model=XaiDeviceLoginStarted,
    dependencies=[require_admin],
)
async def start_xai_device_login(req: XaiDeviceLoginStart, request: Request) -> XaiDeviceLoginStarted:
    """RFC 8628. ``device_code`` stays in ``app.state.xai_device_logins``."""
    from embry0.execution.xai_device_login import XAI_DEVICE_CLIENT_ID, XaiDeviceLogin, XaiDeviceLoginError

    st = _state(request)
    http = httpx.AsyncClient(timeout=30.0)
    stored = False
    try:
        login = XaiDeviceLogin(http)
        started = await login.start(XAI_DEVICE_CLIENT_ID)
        device_code = str(started.get("device_code") or "")
        user_code = str(started.get("user_code") or "")
        verification = str(started.get("verification_uri_complete") or "")
        if not device_code or not user_code or not verification:
            raise HTTPException(status_code=400, detail="error")
        try:
            expires_in = int(started.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        try:
            interval = int(started.get("interval") or 5)
        except (TypeError, ValueError):
            interval = 5
        logins = _device_logins(st)
        login_id = secrets.token_urlsafe(16)
        while login_id in logins:
            login_id = secrets.token_urlsafe(16)
        expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in, 0))
        logins[login_id] = {
            "label": req.label,
            "device_code": device_code,
            "expires_at": expires_at,
            "login": login,
            "http": http,
        }
        stored = True
        return XaiDeviceLoginStarted(
            login_id=login_id,
            verification_uri_complete=verification,
            user_code=user_code,
            expires_at=expires_at,
            interval=interval,
        )
    except XaiDeviceLoginError as exc:
        raise _device_login_http(exc.reason) from None
    finally:
        if not stored:
            await http.aclose()


@router.get(
    "/credentials/xai/device-login/{login_id}",
    response_model=XaiDeviceLoginPoll,
    response_model_exclude_none=True,
    dependencies=[require_admin],
)
async def poll_xai_device_login(login_id: str, request: Request) -> XaiDeviceLoginPoll:
    from embry0.execution.xai_device_login import XaiDeviceLoginError, _pin_xai

    st = _state(request)
    pending = _device_logins(st).get(login_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="device login not found")
    if datetime.now(UTC) >= pending["expires_at"]:
        await _drop_device_login(st, login_id)
        return XaiDeviceLoginPoll(status="expired")
    try:
        result = await pending["login"].poll(pending["device_code"])
    except XaiDeviceLoginError as exc:
        await _drop_device_login(st, login_id)
        raise _device_login_http(exc.reason) from None
    if result.get("status") == "pending":
        return XaiDeviceLoginPoll(status="pending")
    refresh = str(result.get("refresh_token") or "")
    client_id = str(result.get("client_id") or "")
    token_endpoint = str(result.get("token_endpoint") or "")
    if not refresh or not client_id or not token_endpoint:
        await _drop_device_login(st, login_id)
        raise HTTPException(status_code=400, detail="error")
    try:
        # The refresher will POST this URL later. Refuse anything off x.ai.
        _pin_xai(token_endpoint)
    except XaiDeviceLoginError as exc:
        await _drop_device_login(st, login_id)
        raise _device_login_http(exc.reason) from None
    label = str(pending["label"])
    await _drop_device_login(st, login_id)
    register_known_secrets([refresh])
    try:
        row = await st.credentials_repo.create(
            kind="llm_oauth",
            provider="xai",
            host=default_host("xai"),
            label=label,
            secret_ciphertext=await st.secrets_provider.encrypt(_xai_oauth_secret(refresh, client_id, token_endpoint)),
            masked_preview=masked_preview(refresh),
            created_by=request_actor(request),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"a xai credential labelled {label!r} already exists") from None
    await _audit(
        request,
        "credential.created",
        {"credential_id": row["id"], "provider": "xai", "kind": "llm_oauth", "label": label},
    )
    existing = await st.credentials_repo.bindings_for_scope("global", "")
    # replace_bindings deletes every binding of the scope, so the existing list goes back in.
    if not any(b.get("role") == "llm" and b.get("provider") == "xai" for b in existing):
        keep = [
            {
                "credential_id": b["credential_id"],
                "role": b["role"],
                "pinned": bool(b.get("pinned", False)),
                "priority": int(b.get("priority") or 0),
            }
            for b in existing
        ]
        await st.credentials_repo.replace_bindings(
            "global",
            "",
            [*keep, {"credential_id": row["id"], "role": "llm", "pinned": False, "priority": 0}],
            created_by=request_actor(request),
        )
        await _audit(
            request,
            "credential.bound",
            {
                "scope_kind": "global",
                "scope_value": "",
                "credential_ids": [b["credential_id"] for b in keep] + [row["id"]],
            },
        )
    await _refresh_scrub_set(request)
    return XaiDeviceLoginPoll(status="done", credential=await _out_with_count(request, row))


@router.put("/credentials/{credential_id}", response_model=CredentialOut, dependencies=[require_admin])
async def update_credential(credential_id: str, req: CredentialUpdate, request: Request) -> CredentialOut:
    repo = _state(request).credentials_repo
    try:
        # #440: an author field present in the body is written, null included
        # (null clears it back to branding); an omitted one is kept.
        author = req.model_dump(include={"git_author_name", "git_author_email"}, exclude_unset=True)
        row = await repo.update_meta(credential_id, label=req.label, **author)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"a credential labelled {req.label!r} already exists") from None
    if row is None:
        raise HTTPException(status_code=404, detail="credential not found")
    if req.status is not None:
        row = await repo.set_status(credential_id, req.status)
        await _audit(request, "credential.status_changed", {"credential_id": credential_id, "status": req.status})
    meta_fields = sorted([*author, *(["label"] if req.label is not None else [])])
    if meta_fields:
        await _audit(request, "credential.updated", {"credential_id": credential_id, "fields": meta_fields})
    await _refresh_scrub_set(request)
    return await _out_with_count(request, row)


@router.post("/credentials/{credential_id}/rotate", response_model=CredentialOut, dependencies=[require_admin])
async def rotate_credential(credential_id: str, req: CredentialRotate, request: Request) -> CredentialOut:
    st = _state(request)
    row = await st.credentials_repo.rotate(
        credential_id,
        secret_ciphertext=await st.secrets_provider.encrypt(req.secret),
        masked_preview=masked_preview(req.secret),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="credential not found")
    await _audit(request, "credential.rotated", {"credential_id": credential_id})
    await _refresh_scrub_set(request)
    return await _out_with_count(request, row)


_PROBE_STATUS = re.compile(r"HTTP \d{3}")
# Status words probe_llm_secret returns. Anything else in an exception can quote the secret.
_SAFE_PROBE_REASONS = frozenset({"unauthorized", "entitlement_refused", "unreachable", "not_configured", "error"})


async def _probe_login(secret: str, provider: str, config: Any) -> str:
    """Ask the forge who this token is. Raises RuntimeError with the status on rejection."""
    client: Any
    if provider == "gitlab":
        from embry0.execution.gitlab_client import GitLabClient
        from embry0.execution.tls import load_ca_pem

        client = GitLabClient(
            default_token=secret,
            gitlab_url=str(config.gitlab_url),
            ca_pem=load_ca_pem(str(getattr(config, "gitlab_ca_bundle", "") or "")),
            max_attempts=1,
        )
        field = "username"
    else:
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(default_token=secret, max_attempts=1)
        field = "login"
    resp = await client.get("/user")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return str(resp.json().get(field) or "")


@router.post("/credentials/{credential_id}/verify", response_model=CredentialOut, dependencies=[require_admin])
async def verify_credential(credential_id: str, request: Request) -> CredentialOut:
    st = _state(request)
    row = await st.credentials_repo.get(credential_id)
    if row is None:
        raise HTTPException(status_code=404, detail="credential not found")
    if row["kind"] not in ("forge_token", "llm_api_key", "llm_oauth"):
        raise HTTPException(
            status_code=422, detail="only forge tokens and llm accounts can be verified in this release"
        )
    if row["provider"] == "xai" and row["kind"] == "llm_oauth":
        registry = getattr(request.app.state, "xai_refresher_registry", None)
        if registry is None:
            raise HTTPException(
                status_code=422,
                detail="xAI llm_oauth verify needs the token refresher, which is not wired yet",
            )
        secret = ""
    else:
        registry = None
        try:
            secret = await st.secrets_provider.decrypt(row["secret_ciphertext"])
        except InvalidToken:
            raise HTTPException(
                status_code=409, detail="credential cannot be decrypted (decryption_failed); rotate it"
            ) from None
    try:
        if registry is not None:
            # The row holds the refresh payload. Probe with the minted access token.
            secret, _expires_at = await registry.refresh_credential(credential_id)
        if row["kind"] == "forge_token":
            login = await _probe_login(secret, row["provider"], st.config)
        else:
            from embry0.services.provider_probe import STATUS_OK, probe_llm_secret

            base_url = str((row.get("metadata") or {}).get("base_url") or "")
            check = await probe_llm_secret(row["provider"], row["kind"], secret, base_url=base_url)
            if check.status != STATUS_OK:
                raise RuntimeError(check.status)
            login = "ok"
    except Exception as exc:  # noqa: BLE001
        # Never str(exc): transport and protocol errors quote the request, and so the token.
        # Only the probe's own "HTTP <status>" or a known status word is safe to show.
        text = str(exc) if isinstance(exc, RuntimeError) else ""
        matched = _PROBE_STATUS.fullmatch(text)
        reason = matched.group(0) if matched else (text if text in _SAFE_PROBE_REASONS else type(exc).__name__)
        raise HTTPException(status_code=502, detail=f"{row['provider']} rejected the token: {reason}") from None
    await st.credentials_repo.set_verified_login(credential_id, login)
    await _audit(request, "credential.verified", {"credential_id": credential_id, "login": login})
    fresh = await st.credentials_repo.get(credential_id)
    return await _out_with_count(request, fresh)


@router.delete("/credentials/{credential_id}", status_code=204, dependencies=[require_admin])
async def delete_credential(credential_id: str, request: Request) -> Response:
    try:
        deleted = await _state(request).credentials_repo.delete(credential_id)
    except CredentialBoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="credential not found")
    await _audit(request, "credential.deleted", {"credential_id": credential_id})
    await _refresh_scrub_set(request)
    return Response(status_code=204)


@router.get("/repos/{full_repo:path}/credentials")
async def get_repo_credentials(full_repo: str, request: Request) -> dict[str, Any]:
    st = _state(request)
    full_repo = _repo_path(full_repo)
    scope_value = full_repo.lower()
    bindings = await st.credentials_repo.bindings_for_scope("repo", scope_value)
    effective: dict[str, Any] = {}
    try:
        r = await st.credential_resolver.resolve_forge(scope_value, await st.repos_repo.forge_for(full_repo))
        effective["forge"] = {
            "id": r.id,
            "label": r.label,
            "provider": r.provider,
            "source": r.source,
            "trace": list(r.trace),
        }
    except (CredentialMissingError, CredentialUnavailableError, UnregisteredRepoError) as exc:
        effective["forge"] = {"error": str(exc)}
    llm: dict[str, Any] = {}
    for provider in llm_providers():
        try:
            resolved = await st.credential_resolver.resolve_llm(scope_value, provider)
        except CredentialMissingError:
            continue
        except CredentialUnavailableError as exc:
            llm[provider] = {"error": str(exc)}
            continue
        llm[provider] = {
            "id": resolved.id,
            "label": resolved.label,
            "provider": resolved.provider,
            "host": resolved.host,
            "source": resolved.source,
            "trace": list(resolved.trace),
        }
    if llm:
        effective["llm"] = llm
    return {"repo": scope_value, "bindings": bindings, "effective": effective}


@router.put("/repos/{full_repo:path}/credentials", dependencies=[require_admin])
async def set_repo_credentials(full_repo: str, req: BindingsSet, request: Request) -> dict[str, Any]:
    st = _state(request)
    full_repo = _repo_path(full_repo)
    row = await st.repos_repo.get(full_repo)
    if row is None:
        try:
            row = await st.repos_repo.register(full_repo)  # same auto-registration the QA config write does
        except ValueError as exc:  # DEFAULT_FORGE empty: the repo needs an explicit forge first
            raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        await bind_repo_credentials(
            request,
            full_repo,
            [b.model_dump() for b in req.bindings],
            repo_forge=row["forge"],
            actor=request_actor(request),
        )
    except BindingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return await get_repo_credentials(full_repo, request)


async def bind_repo_credentials(
    request: Request, full_repo: str, bindings: list[dict[str, Any]], *, repo_forge: str, actor: str
) -> None:
    """Replace one repository's credential bindings, audit it and drop the
    resolver's cache.

    The one path a repository binding takes: this API, and a webhook delivery
    that auto-registers a repository under an organization (ADR 0012). Raises
    :class:`BindingValidationError`, having written nothing, when a binding
    does not fit the repository.
    """
    scope_value = full_repo.lower()
    st = _state(request)
    await st.credentials_repo.replace_bindings("repo", scope_value, bindings, created_by=actor, repo_forge=repo_forge)
    await emit_audit(
        st.db,
        "credential.bound",
        actor=actor,
        details={
            "scope_kind": "repo",
            "scope_value": scope_value,
            "credential_ids": [b["credential_id"] for b in bindings],
        },
        audit_log_path=st.config.audit_log_path,
    )
    _invalidate_resolver(request)
