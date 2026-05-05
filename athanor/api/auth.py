"""API authentication helpers."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from jose import JWTError
from jose import jwt as _jwt


def verify_api_key(api_key: str, authorization: str, auth_dev_mode: bool) -> None:
    """Verify the API key from explicit parameters.

    Raises HTTPException 401 if the key is missing or invalid.
    Skips verification when auth_dev_mode is True or no key is configured.
    """
    if auth_dev_mode or not api_key:
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    if not hmac.compare_digest(token, api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def verify_webhook_signature(body: bytes, signature: str, secret: str, webhook_dev_mode: bool = False) -> None:
    """Verify a GitHub webhook HMAC-SHA256 signature.

    - If ``secret`` is set: always require and verify a valid signature (401 on mismatch).
    - If ``secret`` is empty and ``webhook_dev_mode`` is True: skip verification (smee.io local relay flow).
    - If ``secret`` is empty and ``webhook_dev_mode`` is False: raise 503 (not configured for production).
    """
    if not secret:
        if webhook_dev_mode:
            return
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = "sha256=" + hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


# ── Phase 4: dashboard JWT issuance + verification ──

_JWT_ALG = "HS256"
_MIN_SECRET_LEN = 32


class DashboardAuthError(RuntimeError):
    """Raised when the dashboard auth surface is misconfigured.

    Distinct from HTTP-401 (which is raised at the route handler / dependency
    layer). DashboardAuthError surfaces server-side configuration problems
    (e.g. a too-short JWT secret).
    """


def issue_dashboard_jwt(
    *,
    api_key: str,
    secret: str,
    ttl_seconds: int = 24 * 60 * 60,
) -> tuple[str, datetime]:
    """Mint a short-lived dashboard JWT.

    The token's `sub` is the literal string "dashboard" — Phase-4 runs single-tenant
    and doesn't track per-user identity. Future SSO work will populate `sub`
    with the user's identifier and `iss` with the IdP.

    `api_key` is currently unused in the JWT body (kept as a positional for
    future-proofing). The caller is responsible for verifying it BEFORE
    calling this function.
    """
    if len(secret) < _MIN_SECRET_LEN:
        raise DashboardAuthError(
            f"dashboard JWT secret must be at least {_MIN_SECRET_LEN} characters"
        )
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": "dashboard",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = _jwt.encode(payload, secret, algorithm=_JWT_ALG)
    return token, expires_at


def verify_dashboard_jwt(token: str, *, secret: str) -> dict | None:
    """Decode + verify a dashboard JWT. Returns the payload dict or None.

    Returns None on every failure path — bad signature, expired, malformed,
    wrong algorithm. Callers raise HTTP 401 themselves; this fn never raises
    for client-controllable input.

    Server-side misconfiguration (e.g. secret too short to verify) propagates
    as DashboardAuthError, since the operator should fix it.
    """
    if len(secret) < _MIN_SECRET_LEN:
        raise DashboardAuthError(
            f"dashboard JWT secret must be at least {_MIN_SECRET_LEN} characters"
        )
    if not token:
        return None
    try:
        payload = _jwt.decode(token, secret, algorithms=[_JWT_ALG])
    except JWTError:
        return None
    return payload
