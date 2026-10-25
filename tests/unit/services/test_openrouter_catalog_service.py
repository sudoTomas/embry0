"""Reading, storing and keeping OpenRouter's live model list (embry0/services/openrouter_catalog.py)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from embry0.agents import openrouter_catalog as oc
from embry0.services.openrouter_catalog import (
    DEFAULT_REFRESH_HOURS,
    MIN_MODELS,
    OpenRouterCatalogService,
    refresh_hours,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def _payload(count: int = MIN_MODELS + 2, *, rate: str = "0.000001") -> dict[str, Any]:
    return {
        "data": [
            {
                "id": f"vendor/model-{i:02d}",
                "name": f"Vendor: Model {i}",
                "supported_parameters": ["tools"],
                "pricing": {"prompt": rate, "completion": rate},
                "context_length": 128000,
            }
            for i in range(count)
        ]
    }


class FakeRepo:
    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        self.stored = stored
        self.saved: list[tuple[str, list[dict[str, Any]], dict[str, int], datetime]] = []
        self.errors: list[tuple[str, str, datetime]] = []

    async def get(self, provider: str) -> dict[str, Any] | None:
        return self.stored

    async def save(self, provider: str, models: list[dict[str, Any]], skipped: dict[str, int], at: datetime) -> None:
        self.saved.append((provider, models, skipped, at))

    async def save_error(self, provider: str, error: str, at: datetime) -> None:
        self.errors.append((provider, error, at))


class FakeFetch:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.keys: list[str | None] = []

    async def __call__(self, secret: str | None) -> Any:
        self.keys.append(secret)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def _no_catalog() -> Iterator[None]:
    oc.install(None)
    yield
    oc.install(None)


def _service(repo: FakeRepo, fetch: FakeFetch, **kw: Any) -> OpenRouterCatalogService:
    return OpenRouterCatalogService(repo, fetch=fetch, now=lambda: NOW, **kw)  # type: ignore[arg-type]


async def test_a_read_is_stored_and_put_in_force() -> None:
    repo, fetch = FakeRepo(), FakeFetch(_payload())
    status = await _service(repo, fetch).refresh()

    catalog = oc.current()
    assert catalog is not None and len(catalog.models) == MIN_MODELS + 2
    assert catalog.fetched_at == NOW
    assert status["source"] == "live" and status["models"] == MIN_MODELS + 2 and status["last_error"] is None
    ((provider, models, skipped, at),) = repo.saved
    assert (provider, at, skipped) == ("openrouter", NOW, {})
    assert models[0]["model"] == "openrouter/vendor/model-00"


async def test_the_key_is_sent_when_one_resolves_and_its_absence_is_no_reason_to_skip() -> None:
    async def key(_provider: str) -> str | None:
        return "sk-or-test-not-real"

    async def broken(_provider: str) -> str | None:
        raise RuntimeError("registry down")

    fetch = FakeFetch(_payload(), _payload())
    await _service(FakeRepo(), fetch, resolve_secret=key).refresh()
    await _service(FakeRepo(), fetch, resolve_secret=broken).refresh()
    assert fetch.keys == ["sk-or-test-not-real", None]


async def test_a_failed_read_keeps_the_list_in_force() -> None:
    request = httpx.Request("GET", "https://openrouter.ai/api/v1/models")
    repo = FakeRepo()
    fetch = FakeFetch(
        _payload(),
        httpx.ConnectError("down", request=request),
        httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request)),
    )
    service = _service(repo, fetch)
    await service.refresh()
    before = oc.current()

    status = await service.refresh()
    assert oc.current() is before, "the last good list stays"
    assert status["models"] == MIN_MODELS + 2
    assert status["last_error"] == "could not reach OpenRouter's model list: ConnectError"
    assert (await service.refresh())["last_error"] == "OpenRouter answered 503 for its model list"
    assert [e[1] for e in repo.errors] == [
        "could not reach OpenRouter's model list: ConnectError",
        "OpenRouter answered 503 for its model list",
    ]
    assert len(repo.saved) == 1


async def test_a_list_too_short_to_be_the_catalog_changes_nothing() -> None:
    repo, fetch = FakeRepo(), FakeFetch(_payload(MIN_MODELS - 1))
    status = await _service(repo, fetch).refresh()
    assert oc.current() is None, "no list ever read: OpenRouter's static entry serves"
    assert status["source"] == "static"
    assert "fewer than" in (status["last_error"] or "")
    assert repo.saved == []


async def test_the_stored_list_is_put_in_force_at_start() -> None:
    first = FakeRepo()
    await _service(first, FakeFetch(_payload())).refresh()
    ((_, models, skipped, at),) = first.saved
    oc.install(None)

    stored = {"models": [*models, {"model": "junk"}], "skipped": {"batch": 3}, "fetched_at": at}
    service = _service(FakeRepo({**stored, "last_error": None, "last_error_at": None}), FakeFetch())
    await service.load()
    catalog = oc.current()
    assert catalog is not None and len(catalog.models) == MIN_MODELS + 2, "a bad stored entry is dropped"
    assert service.status()["skipped"] == {"batch": 3}


async def test_loading_nothing_or_a_broken_row_never_raises() -> None:
    await _service(FakeRepo(None), FakeFetch()).load()
    await _service(FakeRepo({"models": None, "skipped": None}), FakeFetch()).load()  # type: ignore[dict-item]
    assert oc.current() is None


async def test_the_loop_waits_for_a_fresh_list_and_is_off_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    await _service(FakeRepo(), FakeFetch(), interval_hours=0).start()  # returns at once

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise StopAsyncIteration  # stop the loop at its first wait

    monkeypatch.setattr("embry0.services.openrouter_catalog.asyncio.sleep", sleep)
    oc.install(oc.Catalog(models={}, fetched_at=NOW - timedelta(hours=6)))
    with pytest.raises(StopAsyncIteration):
        await _service(FakeRepo(), FakeFetch(), interval_hours=24).start()
    assert sleeps == [pytest.approx(18 * 3600)], "a list read 6 h ago is read again 18 h later"


@pytest.mark.parametrize(
    ("raw", "hours"),
    [("", DEFAULT_REFRESH_HOURS), ("6", 6.0), ("0", 0.0), ("-3", 0.0), ("soon", DEFAULT_REFRESH_HOURS)],
)
def test_the_interval_setting(raw: str, hours: float) -> None:
    assert refresh_hours({"OPENROUTER_CATALOG_REFRESH_HOURS": raw}) == hours
