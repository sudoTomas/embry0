"""Async wrapper over the MinIO Python SDK.

The MinIO SDK is sync; this wrapper runs blocking calls in a threadpool so
the orchestrator's asyncio event loop isn't blocked. Used by:

- App startup: ensure the qa-artifacts bucket exists and has a lifecycle rule.
- Internal /qa/presign endpoint: mint presigned PUT URLs scoped to a job/attempt.
- Dashboard /jobs/{id}/artifacts/{path}: mint short-lived presigned GET URLs.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import structlog
from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

logger = structlog.get_logger(__name__)

_QA_LIFECYCLE_RULE_ID = "qa-artifact-expiry"


class QAMinioClient:
    """Async-friendly facade over the sync `minio` SDK."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
        region: str = "us-east-1",
    ) -> None:
        # Pass region explicitly so presign operations skip GetBucketLocation
        # (which requires DNS resolution of `endpoint`). The orchestrator-side
        # process must mint URLs for the sandbox-facing endpoint (minio-proxy)
        # which it cannot resolve itself; specifying region bypasses the lookup.
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )

    async def _run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args, **kwargs))

    async def bucket_exists(self, bucket: str) -> bool:
        return cast(bool, await self._run(self._client.bucket_exists, bucket))

    async def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't exist; no-op otherwise."""
        if await self.bucket_exists(bucket):
            return
        await self._run(self._client.make_bucket, bucket)
        logger.info("minio_bucket_created", bucket=bucket)

    async def set_lifecycle_policy(self, bucket: str, *, expire_days: int) -> None:
        """Apply a single-rule lifecycle that expires every object after N days."""
        config = LifecycleConfig(
            [
                Rule(
                    ENABLED,
                    rule_filter=Filter(prefix=""),
                    rule_id=_QA_LIFECYCLE_RULE_ID,
                    expiration=Expiration(days=expire_days),
                )
            ]
        )
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
        # Match the rule_id that set_lifecycle_policy writes — defensive against
        # other tools/callers adding rules to the same bucket.
        for rule in cfg.rules:
            if rule.rule_id == _QA_LIFECYCLE_RULE_ID:
                return rule.expiration.days if rule.expiration else None
        return None

    async def presign_put(self, bucket: str, key: str, expires_seconds: int) -> str:
        return cast(
            str,
            await self._run(
                self._client.presigned_put_object,
                bucket,
                key,
                expires=timedelta(seconds=expires_seconds),
            ),
        )

    async def presign_get(self, bucket: str, key: str, expires_seconds: int) -> str:
        return cast(
            str,
            await self._run(
                self._client.presigned_get_object,
                bucket,
                key,
                expires=timedelta(seconds=expires_seconds),
            ),
        )

    async def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        def _list() -> list[str]:
            return [o.object_name for o in self._client.list_objects(bucket, prefix=prefix, recursive=True)]

        return cast(list[str], await self._run(_list))

    async def list_objects_with_meta(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
        """Like `list_objects` but also returns last_modified/size from the listing.

        The MinIO SDK already populates `.last_modified` and `.size` on each
        item the iterator yields, so callers that want freshness/size info
        no longer need a follow-up `stat_object` per key (which would turn a
        single round trip into N+1).
        """

        def _list() -> list[dict[str, Any]]:
            return [
                {"key": o.object_name, "last_modified": o.last_modified, "size": o.size}
                for o in self._client.list_objects(bucket, prefix=prefix, recursive=True)
            ]

        return cast(list[dict[str, Any]], await self._run(_list))

    async def stat_object(self, bucket: str, key: str) -> dict[str, Any]:
        def _stat() -> dict[str, Any]:
            o = self._client.stat_object(bucket, key)
            return {"size": o.size, "etag": o.etag, "last_modified": o.last_modified}

        return cast(dict[str, Any], await self._run(_stat))

    async def get_object_bytes(self, bucket: str, key: str) -> bytes:
        """Fetch an object's full body as bytes.

        Used by the dashboard's artifact passthrough endpoint to stream
        screenshots / HARs / console logs back to the browser without exposing
        a presigned URL (the dashboard auth gates access; the bytes themselves
        flow through the orchestrator). The MinIO SDK returns an HTTPResponse
        which we read in full and then release back to the connection pool.
        """

        def _get() -> bytes:
            resp = self._client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return cast(bytes, await self._run(_get))

    async def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload bytes to MinIO. Used by orchestrator-side report_node for log
        capture (no presign needed — orchestrator holds the root creds).
        """

        def _put() -> None:
            self._client.put_object(
                bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await self._run(_put)
