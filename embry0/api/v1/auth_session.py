"""Auth0 SSO endpoints (RAV-1069): server-side OIDC, no frontend SDK.

A confidential client: authorization-code + PKCE against the deployment's
own Auth0 tenant, with roles read off the ID token claim named by
``SSO_ROLES_CLAIM``. Built on FastAPI + joserfc. Mounted WITHOUT the router-level API-key
dependency — /login and /callback are the unauthenticated entry, /check is
the frontend's cheap probe, /me and /logout act on the session cookie.

The transient OIDC round-trip state (state, nonce, PKCE verifier) rides a
short-lived signed cookie rather than a server-side session store.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from embry0.api.deps import resolve_bearer_principal
from embry0.api.session import (
    SESSION_COOKIE,
    STATE_COOKIE,
    STATE_MAX_AGE_SECONDS,
    TIER_NONE,
    SessionData,
    decode_state,
    encode_session,
    encode_state,
    fold_tier,
    request_session,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth")


def _issuer(config: Any) -> str:
    return str(config.auth0_issuer).rstrip("/") + "/"


def _callback_url(config: Any) -> str:
    return str(config.sso_public_origin).rstrip("/") + "/api/v1/auth/callback"


async def _oidc_metadata(request: Request) -> dict[str, Any]:
    """Fetch + cache the tenant's OIDC discovery document."""
    cached: dict[str, Any] | None = getattr(request.app.state, "oidc_metadata", None)
    if cached:
        return cached
    config = request.app.state.config
    url = _issuer(config) + ".well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        meta: dict[str, Any] = resp.json()
    request.app.state.oidc_metadata = meta
    return meta


async def _jwks(request: Request, refresh: bool = False) -> dict[str, Any]:
    cached: dict[str, Any] | None = getattr(request.app.state, "oidc_jwks", None)
    if cached and not refresh:
        return cached
    meta = await _oidc_metadata(request)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(meta["jwks_uri"])
        resp.raise_for_status()
        jwks: dict[str, Any] = resp.json()
    request.app.state.oidc_jwks = jwks
    return jwks


def _set_session_cookie(response: Any, config: Any, value: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=config.sso_session_max_age_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _on_public_origin(request: Request) -> bool:
    """Whether this request arrived via the public dashboard hostname."""
    config = request.app.state.config
    if not config.sso_enabled:
        return False
    public_host = urlparse(str(config.sso_public_origin)).netloc.split(":")[0].lower()
    request_host = str(request.headers.get("host", "")).split(":")[0].lower()
    return bool(public_host) and request_host == public_host


@router.get("/check")
async def check(request: Request) -> dict[str, Any]:
    """Unauthenticated probe for the AuthGate: is SSO on, am I in?

    ``api_key_login`` gates the dashboard's key-entry FORM only (owner
    decision: the public origin is SSO-only for humans). API-key Bearer
    HEADERS keep working on every origin — MCP/CLI are not "login".

    ``key_tier`` is the tier of the key in the ``Authorization: Bearer``
    header, when there is one and it is valid (``admin`` for the master
    key, else the tier of the key's scope), so the dashboard can hide the
    admin-only controls from a key that cannot use them (#101). ``null``
    without a header or for an invalid, revoked or expired key: the probe
    stays unauthenticated and never answers 401. ``authenticated`` and
    ``tier`` keep describing the session cookie only.
    """
    config = request.app.state.config
    session = request_session(request)
    key_tier: str | None = None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        principal = await resolve_bearer_principal(request.app, authorization[7:])
        key_tier = principal.tier if principal else None
    return {
        "sso_enabled": config.sso_enabled,
        "authenticated": session is not None,
        "tier": session.tier if session else None,
        "api_key_login": not _on_public_origin(request),
        "key_tier": key_tier,
    }


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    config = request.app.state.config
    if not config.sso_enabled:
        raise HTTPException(status_code=404, detail="SSO not configured")
    meta = await _oidc_metadata(request)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": config.auth0_client_id,
        "redirect_uri": _callback_url(config),
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(meta["authorization_endpoint"] + "?" + urlencode(params), status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        encode_state(config.session_secret, {"state": state, "nonce": nonce, "verifier": verifier}),
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/v1/auth",
    )
    return response


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    from joserfc import jwt
    from joserfc.jwk import KeySet
    from joserfc.jwt import JWTClaimsRegistry

    config = request.app.state.config
    if not config.sso_enabled:
        raise HTTPException(status_code=404, detail="SSO not configured")
    stored = decode_state(config.session_secret, request.cookies.get(STATE_COOKIE, ""))
    if not code or not state or not stored or not secrets.compare_digest(stored.get("state", ""), state):
        logger.warning("sso_callback_state_mismatch")
        raise HTTPException(status_code=400, detail="Invalid or expired login state — retry /api/v1/auth/login")

    meta = await _oidc_metadata(request)
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_url(config),
                "code_verifier": stored["verifier"],
            },
            auth=(config.auth0_client_id, config.auth0_client_secret),
        )
    if token_resp.status_code != 200:
        logger.warning("sso_token_exchange_failed", status=token_resp.status_code)
        raise HTTPException(status_code=400, detail="Token exchange failed")
    id_token = token_resp.json().get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token in token response")

    # #506: joserfc, not the deprecated authlib.jose. decode() checks the
    # signature; the registry checks iss, aud and the time claims.
    registry = JWTClaimsRegistry(
        iss={"essential": True, "value": _issuer(config)},
        aud={"essential": True, "value": config.auth0_client_id},
    )
    claims: dict[str, Any] | None = None
    for refresh in (False, True):
        key_set = KeySet.import_key_set(await _jwks(request, refresh=refresh))  # type: ignore[arg-type]
        try:
            token = jwt.decode(id_token, key_set)
            registry.validate(token.claims)
            claims = token.claims
            break
        except Exception:  # noqa: BLE001 — one retry with fresh JWKS (key rotation)
            if refresh:
                logger.warning("sso_id_token_validation_failed")
                raise HTTPException(status_code=400, detail="ID token validation failed") from None
    assert claims is not None
    if not secrets.compare_digest(str(claims.get("nonce", "")), stored.get("nonce", "")):
        raise HTTPException(status_code=400, detail="Nonce mismatch")

    roles = claims.get(config.sso_roles_claim) or []
    if not isinstance(roles, list):
        roles = [roles]
    sub = str(claims.get("sub", ""))
    email = str(claims.get("email", ""))
    name = str(claims.get("name", "") or claims.get("nickname", ""))
    tier = fold_tier([str(r) for r in roles])

    # RAV-1056 Phase A: JIT-mirror the identity into the users table. The
    # session cookie is minted under the row's current epoch; a disabled
    # user gets tier forced to none (lands on the access-denied screen).
    # Fail-open when the repo is absent (unit-test stub apps).
    epoch = 0
    users_repo = getattr(request.app.state, "users_repo", None)
    if users_repo is not None:
        user_row = await users_repo.upsert_login(auth0_sub=sub, email=email, name=name, tier=tier)
        epoch = user_row.session_epoch
        if user_row.disabled:
            tier = TIER_NONE
        from embry0.api.deps import bust_user_gate_cache
        from embry0.audit.helpers import emit_audit

        bust_user_gate_cache(sub)
        await emit_audit(
            getattr(request.app.state, "db", None),
            "user.login",
            actor=email,
            details={"tier": tier, "sub": sub, "disabled": user_row.disabled},
            audit_log_path=config.audit_log_path,
        )

    data = SessionData(
        sub=sub,
        email=email,
        name=name,
        roles=[str(r) for r in roles],
        tier=tier,
        epoch=epoch,
    )
    logger.info("sso_login", email=data.email, tier=data.tier)

    # Relative redirect on purpose — the session cookie is host-scoped and an
    # absolute redirect to another hostname loses it.
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, config, encode_session(config.session_secret, data))
    response.delete_cookie(STATE_COOKIE, path="/api/v1/auth")
    return response


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    session = request_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="No active session")
    return {
        "email": session.email,
        "name": session.name,
        "roles": session.roles,
        "tier": session.tier,
        "access": session.tier != TIER_NONE,
    }


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie and hand back the Auth0 RP-initiated logout URL."""
    config = request.app.state.config
    logout_url = ""
    if config.sso_enabled:
        logout_url = (
            _issuer(config)
            + "v2/logout?"
            + f"client_id={quote(config.auth0_client_id)}&returnTo={quote(config.sso_public_origin)}"
        )
    response = JSONResponse({"logoutUrl": logout_url})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
