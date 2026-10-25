"""What an LLM provider says a key has spent, for the LLM Providers page.

OpenRouter alone answers this with an ordinary API key: ``GET /key`` gives
the key's spend (today, this week, this month, all time), its limit and when
the limit resets, and ``GET /credits`` the account's credit bought and used.
Read with the key a job with no repository binding would run with, the one
the connection test probes, and kept a few minutes: the page polls, the
provider need not be asked each time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

OPENROUTER_API = "https://openrouter.ai/api/v1"
TIMEOUT_SECONDS = 15.0
#: How long one reading is shown before the provider is asked again.
CACHE_SECONDS = 300.0
#: The providers whose spend can be read.
SUPPORTED = ("openrouter",)


def _money(value: Any) -> float | None:
    return round(float(value), 4) if isinstance(value, int | float) else None


async def read_openrouter(secret: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The key's spend and limit and the account's credit, from OpenRouter; raises on a failed read."""
    headers = {"Authorization": f"Bearer {secret}"}
    own = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        key_resp, credits_resp = await asyncio.gather(
            http.get(f"{OPENROUTER_API}/key", headers=headers),
            http.get(f"{OPENROUTER_API}/credits", headers=headers),
        )
    finally:
        if own:
            await http.aclose()
    key_resp.raise_for_status()
    key = (key_resp.json() or {}).get("data") or {}
    budget = key.get("effective_budget") or {}
    out: dict[str, Any] = {
        "key": {
            "spend_today_usd": _money(key.get("usage_daily")),
            "spend_week_usd": _money(key.get("usage_weekly")),
            "spend_month_usd": _money(key.get("usage_monthly")),
            "spend_total_usd": _money(key.get("usage")),
            "limit_usd": _money(key.get("limit")),
            "limit_remaining_usd": _money(key.get("limit_remaining")),
            "limit_reset": key.get("limit_reset"),
            "limit_resets_at": budget.get("resets_at"),
            "expires_at": key.get("expires_at"),
        },
        "account": None,
    }
    # The account's credit; a key the account restricts from reading it leaves this out.
    if credits_resp.status_code == 200:
        credits = (credits_resp.json() or {}).get("data") or {}
        total, used = _money(credits.get("total_credits")), _money(credits.get("total_usage"))
        out["account"] = {
            "credits_usd": total,
            "used_usd": used,
            "remaining_usd": round(total - used, 4) if total is not None and used is not None else None,
        }
    return out


class ProviderUsage:
    """Reads a provider's spend through ``resolve_secret`` and keeps each reading ``CACHE_SECONDS``."""

    def __init__(
        self,
        resolve_secret: Callable[[str], Any],
        *,
        read: Callable[[str], Any] = read_openrouter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolve_secret = resolve_secret
        self._read = read
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def read(self, provider: str, *, fresh: bool = False) -> dict[str, Any]:
        now = self._clock()
        cached = self._cache.get(provider)
        if cached and not fresh and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        secret = await self._resolve_secret(provider)
        if not secret:
            result: dict[str, Any] = {"provider": provider, "status": "not_configured"}
        else:
            try:
                result = {"provider": provider, "status": "ok", **(await self._read(secret))}
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                status = "unauthorized" if code in (401, 403) else "error"
                result = {"provider": provider, "status": status, "detail": f"{provider} answered {code}"}
            except httpx.HTTPError as exc:
                logger.info("provider_usage_unreachable", provider=provider, error=type(exc).__name__)
                result = {"provider": provider, "status": "unreachable", "detail": f"{provider} did not answer"}
        result["checked_at"] = datetime.now(UTC).isoformat()
        self._cache[provider] = (now, result)
        return result
