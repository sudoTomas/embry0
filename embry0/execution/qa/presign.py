"""Orchestrator-side presign service — mints batches of presigned URLs
scoped to a sandbox's own (job_id, attempt_n) prefix.

The sandbox authenticates by presenting the bearer token issued by
ProxyManager.enroll_sandbox() at sandbox start. The token store
returns the (job_id, attempt_n) tuple the token is bound to. The
service refuses to mint URLs outside that prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol


class PresignAuthError(ValueError):
    """Sandbox token is unknown, expired, or otherwise invalid."""


class _MinioLike(Protocol):
    async def presign_put(self, bucket: str, key: str, expires_seconds: int) -> str: ...
    async def presign_get(self, bucket: str, key: str, expires_seconds: int) -> str: ...


class _TokenStoreLike(Protocol):
    async def lookup(self, token: str) -> tuple[str, int]:
        """Return (job_id, attempt_n) for a valid sandbox token, else raise PresignAuthError."""


class QAPresignService:
    """Issues batches of presigned URLs scoped to the requesting sandbox's prefix."""

    def __init__(self, *, minio: _MinioLike, tokens: _TokenStoreLike, bucket: str) -> None:
        self._minio = minio
        self._tokens = tokens
        self._bucket = bucket

    async def mint_batch(
        self,
        *,
        sandbox_token: str,
        paths: list[str],
        expires_seconds: int,
        direction: Literal["put", "get"],
    ) -> dict[str, Any]:
        """Return {bucket, prefix, expires_at, urls: [{path, url}, ...]}."""
        job_id, attempt_n = await self._tokens.lookup(sandbox_token)
        prefix = f"{job_id}/{attempt_n}/"
        urls: list[dict[str, str]] = []
        for path in paths:
            key = prefix + path
            if direction == "put":
                url = await self._minio.presign_put(self._bucket, key, expires_seconds)
            else:
                url = await self._minio.presign_get(self._bucket, key, expires_seconds)
            urls.append({"path": path, "url": url})
        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat()
        return {
            "bucket": self._bucket,
            "prefix": prefix,
            "expires_at": expires_at,
            "urls": urls,
        }
