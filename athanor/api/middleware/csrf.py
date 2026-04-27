"""CSRF protection middleware."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_EXEMPT_PREFIXES = ("/webhook", "/health", "/ws/", "/api/v1/webhook", "/api/v1/telegram/callback")


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)
        if not request.headers.get("X-Requested-With"):
            return JSONResponse(status_code=403, content={"detail": "Missing X-Requested-With header"})
        return await call_next(request)
