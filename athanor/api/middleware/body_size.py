"""Body-size middleware for public-facing endpoints.

Rejects requests whose Content-Length exceeds a configured ceiling (413) or
is missing on POST (411). Applied selectively to the webhook + Telegram
callback paths (the two surfaces exposed past the LAN boundary). Other
endpoints are not affected.
"""

from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = structlog.get_logger(__name__)

DEFAULT_MAX_BYTES = 1_048_576  # 1 MiB
PROTECTED_PATHS = ("/api/v1/webhook", "/api/v1/telegram/callback")


class BodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized or unbounded POST bodies on protected paths."""

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = DEFAULT_MAX_BYTES,
        protected_paths: tuple[str, ...] = PROTECTED_PATHS,
    ) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes
        self._protected = protected_paths

    async def dispatch(self, request, call_next):
        path = request.url.path
        if not any(path.startswith(p) for p in self._protected):
            return await call_next(request)
        if request.method != "POST":
            return await call_next(request)

        cl_header = request.headers.get("content-length")
        if cl_header is None:
            logger.warning("body_size_missing_content_length", path=path)
            return JSONResponse(
                {"error": "Length Required: Content-Length header is mandatory"},
                status_code=411,
            )
        try:
            content_length = int(cl_header)
        except ValueError:
            return JSONResponse(
                {"error": "Bad Content-Length"},
                status_code=400,
            )
        if content_length > self._max_bytes:
            logger.warning(
                "body_size_exceeded",
                path=path,
                content_length=content_length,
                max_bytes=self._max_bytes,
            )
            return JSONResponse(
                {"error": f"Payload Too Large: max {self._max_bytes} bytes"},
                status_code=413,
            )
        return await call_next(request)
