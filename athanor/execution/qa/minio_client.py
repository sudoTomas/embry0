"""Async wrapper over the MinIO Python SDK.

The MinIO SDK is sync; this wrapper runs blocking calls in a threadpool so
the orchestrator's asyncio event loop isn't blocked. Used by:

- App startup: ensure the qa-artifacts bucket exists and has a lifecycle rule.
- Internal /qa/presign endpoint: mint presigned PUT URLs scoped to a job/attempt.
- Dashboard /jobs/{id}/artifacts/{path}: mint short-lived presigned GET URLs.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog
from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

logger = structlog.get_logger(__name__)


class QAMinioClient:
    """Async-friendly facade over the sync `minio` SDK."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, *, secure: bool = False) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args, **kwargs))

    async def bucket_exists(self, bucket: str) -> bool:
        return await self._run(self._client.bucket_exists, bucket)

    async def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't exist; no-op otherwise."""
        if await self.bucket_exists(bucket):
            return
        await self._run(self._client.make_bucket, bucket)
        logger.info("minio_bucket_created", bucket=bucket)

    async def set_lifecycle_policy(self, bucket: str, *, expire_days: int) -> None:
        """Apply a single-rule lifecycle that expires every object after N days."""
        config = LifecycleConfig([
            Rule(
                ENABLED,
                rule_filter=Filter(prefix=""),
                rule_id="qa-artifact-expiry",
                expiration=Expiration(days=expire_days),
            )
        ])
        await self._run(self._client.set_bucket_lifecycle, bucket, config)
        logger.info("minio_lifecycle_set", bucket=bucket, expire_days=expire_days)

    async def get_lifecycle_expire_days(self, bucket: str) -> int | None:
        try:
            cfg = await self._run(self._client.get_bucket_lifecycle, bucket)
        except S3Error as e:
            if e.code == "NoSuchLifecycleConfiguration":
                return None
            raise
        if cfg is None or not cfg.rules:
            return None
        rule = cfg.rules[0]
        if rule.expiration is None:
            return None
        return rule.expiration.days

    async def presign_put(self, bucket: str, key: str, expires_seconds: int) -> str:
        return await self._run(
            self._client.presigned_put_object,
            bucket,
            key,
            expires=timedelta(seconds=expires_seconds),
        )

    async def presign_get(self, bucket: str, key: str, expires_seconds: int) -> str:
        return await self._run(
            self._client.presigned_get_object,
            bucket,
            key,
            expires=timedelta(seconds=expires_seconds),
        )

    async def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        def _list() -> list[str]:
            return [o.object_name for o in self._client.list_objects(bucket, prefix=prefix, recursive=True)]
        return await self._run(_list)

    async def stat_object(self, bucket: str, key: str) -> dict:
        def _stat():
            o = self._client.stat_object(bucket, key)
            return {"size": o.size, "etag": o.etag, "last_modified": o.last_modified}
        return await self._run(_stat)
