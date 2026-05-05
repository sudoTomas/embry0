"""POST /v2/auth/dashboard/login — browser-friendly login.

Exchanges the configured ATHANOR_API_KEY for a 24h JWT in both an HttpOnly
cookie (`dashboard_session`) and the JSON body. Same secret powers both
the existing Bearer-token API and the new cookie-based dashboard auth.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response

from athanor.api.auth import issue_dashboard_jwt
from athanor.api.v2.schemas import LoginRequest, LoginResponse

router = APIRouter()


@router.post("/auth/dashboard/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
) -> LoginResponse:
    """Exchange the shared API key for a 24h dashboard JWT cookie + body.

    Verification:
      - In auth_dev_mode: accepts any non-empty api_key.
      - Otherwise: constant-time compare against the configured ATHANOR_API_KEY.

    Cookie attributes:
      - HttpOnly: prevents JS access (XSS mitigation).
      - SameSite=Lax: allows top-level navigations from the dashboard host.
      - Secure: NOT set in dev mode (the dashboard runs on http://private-server:3030).
        Operators behind TLS termination should set ATHANOR_DASHBOARD_COOKIE_SECURE=1.
    """
    config = request.app.state.config
    api_key = getattr(config, "api_key", "") or ""
    auth_dev_mode = bool(getattr(config, "auth_dev_mode", False))

    if not auth_dev_mode:
        if not hmac.compare_digest(body.api_key, api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    token, expires_at = issue_dashboard_jwt(
        api_key=body.api_key,
        secret=api_key or "dev-mode-fallback-secret-32-chars-min",
    )

    secure_cookie = bool(getattr(config, "dashboard_cookie_secure", False))
    response.set_cookie(
        key="dashboard_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=int((expires_at - datetime.now(UTC)).total_seconds()),
        path="/",
    )

    return LoginResponse(token=token, expires_at=expires_at)
