"""Tests for the orchestrator-side presign service."""

from __future__ import annotations

import pytest

from embry0.execution.qa.presign import (
    PresignAuthError,
    QAPresignService,
)


class FakeMinioClient:
    """Records calls and returns deterministic URLs."""

    def __init__(self):
        self.calls: list[tuple[str, str, int, str]] = []

    async def presign_put(self, bucket, key, expires_seconds):
        self.calls.append((bucket, key, expires_seconds, "put"))
        return f"http://fake/{bucket}/{key}?put&exp={expires_seconds}"

    async def presign_get(self, bucket, key, expires_seconds):
        self.calls.append((bucket, key, expires_seconds, "get"))
        return f"http://fake/{bucket}/{key}?get&exp={expires_seconds}"


class FakeTokenStore:
    """Maps sandbox_token -> (job_id, attempt_n) or raises if unknown."""

    def __init__(self, tokens: dict[str, tuple[str, int]]):
        self._tokens = dict(tokens)

    async def lookup(self, token: str) -> tuple[str, int]:
        if token not in self._tokens:
            raise PresignAuthError(f"unknown token {token[:8]}...")
        return self._tokens[token]


@pytest.mark.asyncio
async def test_mint_batch_scopes_keys_to_job_attempt():
    minio = FakeMinioClient()
    tokens = FakeTokenStore({"sandbox-token-abc": ("JOB", 2)})
    svc = QAPresignService(minio=minio, tokens=tokens, bucket="qa-artifacts")
    result = await svc.mint_batch(
        sandbox_token="sandbox-token-abc",
        paths=["result.json", "screenshots/login.png"],
        expires_seconds=600,
        direction="put",
    )
    assert result["bucket"] == "qa-artifacts"
    assert result["prefix"] == "JOB/2/"
    keys = {c[1] for c in minio.calls}
    assert keys == {"JOB/2/result.json", "JOB/2/screenshots/login.png"}
    assert all(c[2] == 600 for c in minio.calls)
    assert all(c[3] == "put" for c in minio.calls)


@pytest.mark.asyncio
async def test_unknown_token_raises_auth_error():
    minio = FakeMinioClient()
    tokens = FakeTokenStore({})
    svc = QAPresignService(minio=minio, tokens=tokens, bucket="qa-artifacts")
    with pytest.raises(PresignAuthError):
        await svc.mint_batch(
            sandbox_token="unknown",
            paths=["x.json"],
            expires_seconds=300,
            direction="put",
        )
    assert minio.calls == []  # token failed before any URL was minted


@pytest.mark.asyncio
async def test_get_direction_calls_presign_get():
    minio = FakeMinioClient()
    tokens = FakeTokenStore({"t": ("JOB", 1)})
    svc = QAPresignService(minio=minio, tokens=tokens, bucket="qa-artifacts")
    await svc.mint_batch(
        sandbox_token="t",
        paths=["prior.json"],
        expires_seconds=300,
        direction="get",
    )
    assert minio.calls[0][3] == "get"
