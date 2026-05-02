"""Tests for the MinIO async wrapper.

Requires MinIO at $MINIO_ENDPOINT. The conftest at tests/conftest.py
exposes a `requires_minio` marker that skips when MinIO isn't reachable.
"""

from __future__ import annotations

import os

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


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_ensure_bucket_idempotent(minio_client):
    bucket = "qa-artifacts-test"
    await minio_client.ensure_bucket(bucket)
    assert await minio_client.bucket_exists(bucket)
    await minio_client.ensure_bucket(bucket)


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_set_lifecycle_policy(minio_client):
    bucket = "qa-artifacts-test"
    await minio_client.ensure_bucket(bucket)
    await minio_client.set_lifecycle_policy(bucket, expire_days=14)
    days = await minio_client.get_lifecycle_expire_days(bucket)
    assert days == 14
    await minio_client.set_lifecycle_policy(bucket, expire_days=30)
    assert await minio_client.get_lifecycle_expire_days(bucket) == 30


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_presign_put_returns_usable_url(minio_client):
    bucket = "qa-artifacts-test"
    await minio_client.ensure_bucket(bucket)
    url = await minio_client.presign_put(bucket, "smoke/result.json", expires_seconds=300)
    assert url.startswith("http://")
    import httpx
    async with httpx.AsyncClient() as c:
        r = await c.put(url, content=b'{"ok": true}')
    assert r.status_code in (200, 204)
    objects = await minio_client.list_objects(bucket, prefix="smoke/")
    assert any(o.endswith("result.json") for o in objects)


@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_presign_get_returns_object_content(minio_client):
    bucket = "qa-artifacts-test"
    await minio_client.ensure_bucket(bucket)
    put_url = await minio_client.presign_put(bucket, "smoke/g.json", 300)
    import httpx
    async with httpx.AsyncClient() as c:
        await c.put(put_url, content=b'{"hello": "world"}')
    get_url = await minio_client.presign_get(bucket, "smoke/g.json", 300)
    async with httpx.AsyncClient() as c:
        r = await c.get(get_url)
    assert r.status_code == 200
    assert r.json() == {"hello": "world"}
