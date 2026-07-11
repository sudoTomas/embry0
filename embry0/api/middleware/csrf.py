"""CSRF protection middleware."""

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# /api/v1/internal/ — sandbox-only endpoints authenticated via a per-sandbox
# bearer token in the request body. Sandboxes call these with curl (no
# X-Requested-With header), so CSRF cannot apply. The endpoints carry their
# own auth check (SandboxTokenRegistry lookup).
_EXEMPT_PREFIXES = (
    "/webhook",
    "/health",
    "/ws/",
    "/api/v1/webhook",
    "/api/v1/telegram/callback",
    "/api/v1/internal/",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method in _SAFE_METHODS:
            resp: Response = await call_next(request)
            return resp
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            resp = await call_next(request)
            return resp
        if not request.headers.get("X-Requested-With"):
            return JSONResponse(status_code=403, content={"detail": "Missing X-Requested-With header"})
        resp = await call_next(request)
        return resp
