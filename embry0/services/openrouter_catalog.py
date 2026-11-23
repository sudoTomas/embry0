"""Read OpenRouter's model list, keep what an agent can run, store it and put it in force.

``GET https://openrouter.ai/api/v1/models`` answers without a key; the key a
job with no repository binding would run with is sent when one resolves.
``embry0.agents.openrouter_catalog.parse`` decides what is kept and at what
price. While the provider catalogue turns OpenRouter on, a refresh runs when
the list in force is older than OpenRouter's ``refresh_hours`` there (24 as
shipped; 0 stops the loop, POST /config/providers/openrouter/catalog/refresh
still works), and on demand. The loop reads both each minute, so a change to
the catalogue applies without a restart.

A failed refresh, an answer that is not the list, or one with fewer than
``MIN_MODELS`` models it would keep, changes nothing in force: the last good
list stays, and the error is stored beside it for the LLM Providers page.
With no list ever stored, OpenRouter's entry in the provider catalogue serves.
"""

from __future__ import annotations

import asyncio
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
DEFAULT_REFRESH_HOURS: Final = 24.0
RETRY_SECONDS: Final = 3600.0
"""How soon the loop tries again after a failed read."""
LOOK_SECONDS: Final = 60.0
"""How often the loop looks again at whether OpenRouter is on and how often to read its list."""

Fetch = Callable[[str | None], Awaitable[Any]]
"""Given the key (or None), returns OpenRouter's decoded ``GET /models`` answer; raises on a failed read."""


def refresh_hours() -> float:
    """OpenRouter's ``refresh_hours`` in the installed provider catalogue; 0 stops the loop."""
    from embry0.agents.catalogue import current as installed

    entry = installed().document.providers.get(PROVIDER)
    hours = entry.refresh_hours if entry is not None else None
    return DEFAULT_REFRESH_HOURS if hours is None else hours


def enabled() -> bool:
    """Whether the installed provider catalogue turns OpenRouter on."""
    from embry0.agents.catalogue import current as installed

    return installed().enabled(PROVIDER)


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
        interval_hours: float | Callable[[], float] = DEFAULT_REFRESH_HOURS,
        enabled: Callable[[], bool] = lambda: True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._repo = repo
        self._resolve_secret = resolve_secret
        self._fetch = fetch
        self._interval = interval_hours if callable(interval_hours) else (lambda: float(interval_hours))
        self._enabled = enabled
        self._sleep = sleep
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
            "refresh_hours": self._interval(),
        }

    async def start(self) -> None:
        """While OpenRouter is on, refresh when the list in force is older than the interval; runs until cancelled.

        It looks again at least every :data:`LOOK_SECONDS`, so turning
        OpenRouter on or off, or changing the interval, applies within a
        minute. A failed read is tried again after :data:`RETRY_SECONDS` (or
        the interval, if shorter), not the interval later.
        """
        retry_at: datetime | None = None
        while True:
            hours = self._interval()
            if hours <= 0 or not self._enabled():
                await self._sleep(LOOK_SECONDS)
                continue
            interval = timedelta(hours=hours)
            catalog = current()
            due = [
                m for m in (catalog.fetched_at + interval if catalog is not None else None, retry_at) if m is not None
            ]
            wait = (max(due) - self._now()).total_seconds() if due else 0.0
            if wait > 0:
                await self._sleep(min(wait, LOOK_SECONDS))
                continue
            failed = (await self.refresh())["last_error"] is not None
            retry_at = self._now() + min(timedelta(seconds=RETRY_SECONDS), interval) if failed else None

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
