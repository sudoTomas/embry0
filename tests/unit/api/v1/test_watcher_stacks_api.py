"""Watcher stacks API (#378): reading and editing what embry0 watches."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.watcher_stacks import SETTING_KEYS, WatcherStack

STACK = WatcherStack(
    repo="acme/backend",
    logql='{compose_project="acme"}',
    tracker_target={"team_id": "team-1"},
)


class FakeStacksRepo:
    """Stands in for the repository, keeping its write-side contract: unknown
    setting keys are dropped, and an unknown tracker is refused."""

    def __init__(self) -> None:
        self.stacks: dict[str, WatcherStack] = {}
        self.deleted: list[str] = []

    async def list_all(self, *, enabled_only: bool = False) -> list[WatcherStack]:
        return [s for s in self.stacks.values() if s.enabled or not enabled_only]

    async def get(self, repo: str) -> WatcherStack | None:
        return self.stacks.get(repo)

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        if stack.tracker not in {"linear", "github", "gitlab"}:
            raise ValueError(f"unknown tracker {stack.tracker!r}")
        kept = WatcherStack(
            repo=stack.repo,
            logql=stack.logql,
            tracker=stack.tracker,
            tracker_target=stack.tracker_target,
            settings={k: v for k, v in stack.settings.items() if k in SETTING_KEYS},
            enabled=stack.enabled,
        )
        self.stacks[stack.repo] = kept
        return kept

    async def delete(self, repo: str) -> bool:
        self.deleted.append(repo)
        return self.stacks.pop(repo, None) is not None


@pytest.fixture
def app() -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    built = create_app(config)
    built.state.watcher_stacks_repo = FakeStacksRepo()
    return built


def _client(app: Any) -> AsyncClient:
    # The CSRF marker every mutating route requires (api/middleware).
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


async def test_listing_names_what_may_be_overridden(app: Any) -> None:
    """The caller needs the vocabulary; a settings key outside it is dropped."""
    app.state.watcher_stacks_repo.stacks[STACK.repo] = STACK
    async with _client(app) as client:
        body = (await client.get("/api/v1/watcher/stacks")).json()
    assert [s["repo"] for s in body["stacks"]] == [STACK.repo]
    assert "interval_seconds" in body["overridable_settings"]


async def test_a_stack_is_created_read_back_and_deleted(app: Any) -> None:
    async with _client(app) as client:
        put = await client.put(
            "/api/v1/watcher/stacks/example-org/embry0",
            json={"logql": '{compose_project="embry0"}', "tracker": "github", "settings": {"interval_seconds": 7200}},
        )
        assert put.status_code == 200, put.text
        assert put.json()["tracker"] == "github"
        assert put.json()["ignored_settings"] == []

        got = await client.get("/api/v1/watcher/stacks/example-org/embry0")
        assert got.json()["settings"] == {"interval_seconds": 7200}

        assert (await client.delete("/api/v1/watcher/stacks/example-org/embry0")).status_code == 200
        assert (await client.get("/api/v1/watcher/stacks/example-org/embry0")).status_code == 404


async def test_an_unknown_setting_is_reported_not_stored(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.put(
            "/api/v1/watcher/stacks/o/r", json={"logql": "{x=1}", "settings": {"intervul_seconds": 60}}
        )
    assert resp.json()["ignored_settings"] == ["intervul_seconds"]
    assert resp.json()["settings"] == {}


async def test_a_bad_setting_value_is_refused_naming_it(app: Any) -> None:
    """#172: a value the tick cannot use is a 422 at write, not an error on every tick after."""
    async with _client(app) as client:
        resp = await client.put(
            "/api/v1/watcher/stacks/o/r",
            json={"logql": "{x=1}", "settings": {"auto_accept_max_per_day": "x", "auto_accept_min_severity": "hgh"}},
        )
        got = await client.get("/api/v1/watcher/stacks/o/r")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "settings.auto_accept_max_per_day" in detail and "settings.auto_accept_min_severity" in detail, detail
    assert got.status_code == 404, "nothing was stored"


async def test_an_unknown_tracker_is_refused(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.put("/api/v1/watcher/stacks/o/r", json={"logql": "{x=1}", "tracker": "jira"})
    assert resp.status_code == 422


async def test_an_empty_selector_is_refused(app: Any) -> None:
    """A stack with no selector would tick forever and find nothing."""
    async with _client(app) as client:
        resp = await client.put("/api/v1/watcher/stacks/o/r", json={"logql": ""})
    assert resp.status_code == 422


async def test_deleting_something_that_is_not_watched_is_404(app: Any) -> None:
    async with _client(app) as client:
        assert (await client.delete("/api/v1/watcher/stacks/o/r")).status_code == 404


async def test_without_storage_the_routes_say_so(app: Any) -> None:
    app.state.watcher_stacks_repo = None
    async with _client(app) as client:
        assert (await client.get("/api/v1/watcher/stacks")).status_code == 503
