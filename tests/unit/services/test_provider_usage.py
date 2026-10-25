"""What OpenRouter says a key has spent, read for the LLM Providers page."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from embry0.services.provider_usage import CACHE_SECONDS, ProviderUsage, read_openrouter

KEY_BODY = {
    "data": {
        "usage": 12.5,
        "usage_daily": 0.25,
        "usage_weekly": 3,
        "usage_monthly": 9.75,
        "limit": 50,
        "limit_remaining": 47,
        "limit_reset": "weekly",
        "expires_at": "2027-01-23T00:00:00Z",
        "effective_budget": {"resets_at": "2026-10-26T00:00:00.000Z"},
    }
}
CREDITS_BODY = {"data": {"total_credits": 275, "total_usage": 134.861853109}}


def _client(credits_status: int = 200, key_status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        if request.url.path.endswith("/key"):
            return httpx.Response(key_status, json=KEY_BODY)
        return httpx.Response(credits_status, json=CREDITS_BODY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_the_key_s_spend_and_limit_and_the_account_s_credit() -> None:
    async with _client() as client:
        usage = await read_openrouter("sk-test", client=client)
    assert usage["key"] == {
        "spend_today_usd": 0.25,
        "spend_week_usd": 3.0,
        "spend_month_usd": 9.75,
        "spend_total_usd": 12.5,
        "limit_usd": 50.0,
        "limit_remaining_usd": 47.0,
        "limit_reset": "weekly",
        "limit_resets_at": "2026-10-26T00:00:00.000Z",
        "expires_at": "2027-01-23T00:00:00Z",
    }
    assert usage["account"] == {"credits_usd": 275.0, "used_usd": 134.8619, "remaining_usd": 140.1381}


@pytest.mark.asyncio
async def test_a_key_that_may_not_read_the_credit_still_reads_its_own_spend() -> None:
    async with _client(credits_status=403) as client:
        usage = await read_openrouter("sk-test", client=client)
    assert usage["account"] is None and usage["key"]["limit_usd"] == 50.0


@pytest.mark.asyncio
async def test_readings_are_kept_and_failures_said_plainly() -> None:
    now = [100.0]
    reads: list[str] = []

    async def resolve(provider: str) -> str | None:
        return "sk-test"

    async def read(secret: str) -> dict[str, Any]:
        reads.append(secret)
        return {"key": {}, "account": None}

    usage = ProviderUsage(resolve, read=read, clock=lambda: now[0])
    first = await usage.read("openrouter")
    assert first["status"] == "ok" and "checked_at" in first
    now[0] += CACHE_SECONDS - 1
    assert await usage.read("openrouter") is first and len(reads) == 1
    await usage.read("openrouter", fresh=True)
    assert len(reads) == 2

    async def refused(secret: str) -> dict[str, Any]:
        request = httpx.Request("GET", "https://openrouter.ai/api/v1/key")
        raise httpx.HTTPStatusError("no", request=request, response=httpx.Response(401, request=request))

    assert (await ProviderUsage(resolve, read=refused).read("openrouter"))["status"] == "unauthorized"

    async def no_key(provider: str) -> str | None:
        return None

    assert (await ProviderUsage(no_key, read=read).read("openrouter"))["status"] == "not_configured"


@pytest.mark.asyncio
async def test_the_route_reads_openrouter_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from embry0.api.v1 import config as config_api

    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    app = FastAPI()
    app.include_router(config_api.router, prefix="/api/v1")

    class FakeUsage:
        async def read(self, provider: str, *, fresh: bool = False) -> dict[str, Any]:
            return {"provider": provider, "status": "ok", "fresh": fresh}

    app.state.provider_usage = FakeUsage()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/v1/config/providers/openrouter/usage", params={"fresh": "true"})
        other = await client.get("/api/v1/config/providers/anthropic/usage")
    assert ok.status_code == 200 and ok.json() == {"provider": "openrouter", "status": "ok", "fresh": True}
    assert other.status_code == 404
