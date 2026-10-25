"""Read OpenRouter's model list, keep what an agent can run, store it and put it in force.

``GET https://openrouter.ai/api/v1/models`` answers without a key; the key a
job with no repository binding would run with is sent when one resolves.
``embry0.agents.openrouter_catalog.parse`` decides what is kept and at what
price. A refresh runs at start when the stored list is older than the
interval, then every ``OPENROUTER_CATALOG_REFRESH_HOURS`` (24 by default;
0 stops the loop, POST /config/providers/openrouter/catalog/refresh still
works), and on demand.

A failed refresh, an answer that is not the list, or one with fewer than
``MIN_MODELS`` models it would keep, changes nothing in force: the last good
list stays, and the error is stored beside it for the LLM Providers page.
With no list ever stored, OpenRouter's static entry in providers.py serves.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import structlog

from embry0.agents.openrouter_catalog import Catalog, CatalogModel, current, install, parse
from embry0.storage.repositories.provider_catalogs import ProviderCatalogsRepository

logger = structlog.get_logger(__name__)

PROVIDER: Final = "openrouter"
MODELS_URL: Final = "https://openrouter.ai/api/v1/models"
TIMEOUT_SECONDS: Final = 30.0
MIN_MODELS: Final = 10
"""A list with fewer models than this is taken for a broken answer, not OpenRouter's catalog."""
REFRESH_SETTING: Final = "OPENROUTER_CATALOG_REFRESH_HOURS"
DEFAULT_REFRESH_HOURS: Final = 24.0
RETRY_SECONDS: Final = 3600.0
"""How soon the loop tries again after a failed read."""

Fetch = Callable[[str | None], Awaitable[Any]]
"""Given the key (or None), returns OpenRouter's decoded ``GET /models`` answer; raises on a failed read."""


def refresh_hours(environ: dict[str, str] | None = None) -> float:
    """The refresh interval in hours; 0 turns the loop off. A value that is not a number is the default."""
    raw = (os.environ if environ is None else environ).get(REFRESH_SETTING, "").strip()
    if not raw:
        return DEFAULT_REFRESH_HOURS
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("openrouter_catalog_refresh_setting_invalid", value=raw, default=DEFAULT_REFRESH_HOURS)
        return DEFAULT_REFRESH_HOURS


async def fetch_models(secret: str | None) -> Any:
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(MODELS_URL, headers=headers)
    response.raise_for_status()
    return response.json()


def _catalog_from(models: list[CatalogModel], fetched_at: datetime) -> Catalog:
    return Catalog(models={m.model: m for m in models}, fetched_at=fetched_at)


class OpenRouterCatalogService:
    """Keeps OpenRouter's live model list stored and in force for this process."""

    def __init__(
        self,
        repo: ProviderCatalogsRepository,
        *,
        resolve_secret: Callable[[str], Awaitable[str | None]] | None = None,
        fetch: Fetch = fetch_models,
        interval_hours: float = DEFAULT_REFRESH_HOURS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repo
        self._resolve_secret = resolve_secret
        self._fetch = fetch
        self._interval_hours = interval_hours
        self._now = now
        self._lock = asyncio.Lock()
        self._skipped: dict[str, int] = {}
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None

    async def load(self) -> None:
        """Put the stored list in force, if there is one; never raises."""
        try:
            stored = await self._repo.get(PROVIDER)
            if stored is None:
                return
            self._skipped = dict(stored["skipped"])
            self._last_error, self._last_error_at = stored["last_error"], stored["last_error_at"]
            models = [m for m in (CatalogModel.from_json(row) for row in stored["models"]) if m is not None]
            if models and isinstance(stored["fetched_at"], datetime):
                install(_catalog_from(models, stored["fetched_at"]))
                logger.info(
                    "openrouter_catalog_loaded", models=len(models), fetched_at=stored["fetched_at"].isoformat()
                )
        except Exception:
            logger.warning("openrouter_catalog_load_failed", exc_info=True)

    async def refresh(self) -> dict[str, Any]:
        """Read the list now; on success store it and put it in force. Returns ``status()``; never raises."""
        async with self._lock:
            at = self._now()
            try:
                secret = await self._secret()
                models, skipped = parse(await self._fetch(secret))
                if len(models) < MIN_MODELS:
                    raise ValueError(
                        f"OpenRouter listed {len(models)} model(s) with tool calling and a price "
                        f"(fewer than {MIN_MODELS}); keeping the list in force"
                    )
            except Exception as exc:
                message = _describe(exc)
                self._last_error, self._last_error_at = message, at
                logger.warning("openrouter_catalog_refresh_failed", error=message)
                try:
                    await self._repo.save_error(PROVIDER, message, at)
                except Exception:
                    logger.warning("openrouter_catalog_error_persist_failed", exc_info=True)
                return self.status()
            self._skipped = dict(skipped)
            self._last_error, self._last_error_at = None, None
            install(_catalog_from(models, at))
            try:
                await self._repo.save(PROVIDER, [m.to_json() for m in models], dict(skipped), at)
            except Exception:
                # In force for this process all the same; the next refresh stores it.
                logger.warning("openrouter_catalog_persist_failed", exc_info=True)
            logger.info("openrouter_catalog_refreshed", models=len(models), skipped=dict(skipped))
            return self.status()

    def status(self) -> dict[str, Any]:
        """What the LLM Providers page shows about the list."""
        catalog = current()
        return {
            "source": "live" if catalog is not None else "static",
            "models": len(catalog.models) if catalog is not None else 0,
            "fetched_at": catalog.fetched_at.isoformat() if catalog is not None else None,
            "skipped": dict(self._skipped),
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at is not None else None,
            "refresh_hours": self._interval_hours,
        }

    async def start(self) -> None:
        """Refresh when the list in force is older than the interval, then on the interval; runs until cancelled."""
        if self._interval_hours <= 0:
            return
        interval = timedelta(hours=self._interval_hours)
        while True:
            catalog = current()
            if catalog is not None:
                wait = (catalog.fetched_at + interval - self._now()).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
            if (await self.refresh())["last_error"] is not None:
                # A failed read is tried again within the hour, not the next day.
                await asyncio.sleep(min(RETRY_SECONDS, interval.total_seconds()))

    async def _secret(self) -> str | None:
        if self._resolve_secret is None:
            return None
        try:
            return await self._resolve_secret(PROVIDER)
        except Exception:
            # The list answers without a key; a key that cannot be read is no reason to skip it.
            logger.debug("openrouter_catalog_key_unavailable", exc_info=True)
            return None


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"OpenRouter answered {exc.response.status_code} for its model list"
    if isinstance(exc, httpx.HTTPError):
        return f"could not reach OpenRouter's model list: {type(exc).__name__}"
    return str(exc) or type(exc).__name__
