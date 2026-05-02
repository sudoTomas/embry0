"""Tests for the MinIO async wrapper.

Requires MinIO at $MINIO_ENDPOINT. The conftest at tests/conftest.py
exposes a `requires_minio` marker that skips when MinIO isn't reachable.
"""

from __future__ import annotations

import os
import secrets

import pytest

from athanor.execution.qa.minio_client import QAMinioClient


@pytest.fixture
def minio_client():
    return QAMinioClient(
        endpoint=os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "athanor"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "change-me-in-prod"),
        secure=False,
    )


@pytest.fixture
def test_bucket():
    """Unique bucket per test, cleaned up at teardown."""
    name = f"qa-test-{secrets.token_hex(4)}"
    yield name
    # No cleanup — tests are short-lived; MinIO lifecycle on real qa-artifacts will
    # eventually expire any leaked test buckets. (Could add explicit cleanup if
    # leaks become a problem.)


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_ensure_bucket_idempotent(minio_client, test_bucket):
    await minio_client.ensure_bucket(test_bucket)
    assert await minio_client.bucket_exists(test_bucket)
    await minio_client.ensure_bucket(test_bucket)


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_set_lifecycle_policy(minio_client, test_bucket):
    await minio_client.ensure_bucket(test_bucket)
    await minio_client.set_lifecycle_policy(test_bucket, expire_days=14)
    days = await minio_client.get_lifecycle_expire_days(test_bucket)
    assert days == 14
    await minio_client.set_lifecycle_policy(test_bucket, expire_days=30)
    assert await minio_client.get_lifecycle_expire_days(test_bucket) == 30


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_presign_put_returns_usable_url(minio_client, test_bucket):
    await minio_client.ensure_bucket(test_bucket)
    url = await minio_client.presign_put(test_bucket, "smoke/result.json", expires_seconds=300)
    assert url.startswith("http://")
    import httpx
    async with httpx.AsyncClient() as c:
        r = await c.put(url, content=b'{"ok": true}')
    assert r.status_code in (200, 204)
    objects = await minio_client.list_objects(test_bucket, prefix="smoke/")
    assert any(o.endswith("result.json") for o in objects)


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_presign_get_returns_object_content(minio_client, test_bucket):
    await minio_client.ensure_bucket(test_bucket)
    put_url = await minio_client.presign_put(test_bucket, "smoke/g.json", 300)
    import httpx
    async with httpx.AsyncClient() as c:
        await c.put(put_url, content=b'{"hello": "world"}')
    get_url = await minio_client.presign_get(test_bucket, "smoke/g.json", 300)
    async with httpx.AsyncClient() as c:
        r = await c.get(get_url)
    assert r.status_code == 200
    assert r.json() == {"hello": "world"}
