"""Per-IP API rate limiting middleware."""
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_EXEMPT_PREFIXES = ("/health", "/webhook", "/api/v1/webhook", "/ws/", "/api/v1/telegram/callback")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self._max_rpm = requests_per_minute
        self._window = 60.0
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._request_count = 0

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        self._requests[ip] = [t for t in self._requests[ip] if t > now - self._window]
        if len(self._requests[ip]) >= self._max_rpm:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "60"},
            )
        self._requests[ip].append(now)
        self._request_count += 1
        if self._request_count >= 1000:
            self._request_count = 0
            stale = [k for k, v in self._requests.items() if not v]
            for k in stale:
                del self._requests[k]
        return await call_next(request)
