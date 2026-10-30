"""The scout's policy lives in the settings document (#168), end to end on a real database.

- A PUT of the document reaches the next tick of a running scout loop, with no restart:
  its templates, its in-flight cap and each job's hard cap.
- The daily cap counts the scouting jobs stored in the last 24 hours.
- A bad value is a 422 naming its key, never repeating it, and nothing is stored.
- The first boot imports a non-default ``SCOUT_*`` value once; a second boot does not.
- The real lifespan builds the scout loop with a reader of the document, not a value read once,
  and warns once while the retired ``SCOUT_TICK_SECONDS`` is still set.

The section is taken from the application's catalog by name, and each test
first writes or reads it, so each fails with its own message on a tree that
has no scout section.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.scout import ScoutLoop, TickSettings, tick_settings
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once, log_ignored_env
from embry0.settings.document import load_yaml
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.scout import ScoutRunsRepository
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
ADMIN = "emb_" + "a" * 43
DAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
LOCAL = "custom/local-model"


def _scout() -> Section[Any]:
    section = CATALOG.get("scout")
    assert section is not None, "scout is a section of the settings document"
    return section


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = db_with_migrations
    app.state.settings = SettingsStore(db_with_migrations)
    keys = MagicMock()
    row = ApiKeyRow(
        id="key-admin0000001",
        user_id="usr-admin",
        name="admin-key",
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(ADMIN) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    app.state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    headers = {"Authorization": f"Bearer {ADMIN}", "X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


async def _put(client: AsyncClient, text: str) -> Any:
    return await client.put(URL, content=text, headers={"Content-Type": "application/yaml"})


class FakeJobs:
    """The jobs a loop creates; each stays pending, so it stays in flight."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, **kwargs: Any) -> str:
        job_id = f"job-{len(self.rows) + 1:03d}"
        self.rows[job_id] = {"created_with": kwargs, "status": "pending"}
        return job_id

    async def update(self, job_id: str, **kwargs: Any) -> None:
        self.rows[job_id].update(kwargs)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)

    def template(self, job_id: str) -> str:
        return str(self.rows[job_id]["created_with"]["pipeline_template"])

    def budget(self, job_id: str) -> float:
        return float(self.rows[job_id]["created_with"]["pipeline_config"]["max_budget_usd"])


def _loop(store: SettingsStore, db: DatabasePool, jobs: FakeJobs, clock: list[datetime]) -> ScoutLoop:
    async def repos() -> list[str]:
        return ["acme/widgets", "acme/gadgets"]

    async def areas_for(repo: str) -> list[Any]:
        return []

    async def gate() -> Any:
        return None

    async def submit(job_id: str, unit: Any, text: str) -> None:
        return None

    leads = MagicMock(upsert=AsyncMock())

    production = tick_settings(store)

    async def settings() -> TickSettings:
        # The store's scout section, as production reads it; every repository on the local model.
        return dataclasses.replace(await production(), models=lambda repo: LOCAL)

    return ScoutLoop(
        settings=settings,
        runs=ScoutRunsRepository(db),
        jobs=jobs,
        leads=leads,
        repos=repos,
        areas_for=areas_for,
        ask_gate=gate,
        submit=submit,
        clock=lambda: clock[0],
    )


async def test_a_put_reaches_the_next_tick_of_the_running_loop(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    first = await _put(client, "scout:\n  templates: [garden]\n  max_in_flight: 1\n")
    assert first.status_code == 200, (
        f"the settings document takes the scout's templates and in-flight cap: {first.text}"
    )
    jobs = FakeJobs()
    loop = _loop(app.state.settings, db_with_migrations, jobs, [NOW])

    started = (await loop.tick())["started"]
    assert [jobs.template(j) for j in started] == ["garden"], "one job, of the one template the section names"

    second = await _put(client, "scout:\n  templates: [kintsugi-trace]\n  max_in_flight: 2\n")
    assert second.status_code == 200, second.text
    started = (await loop.tick())["started"]
    assert [jobs.template(j) for j in started] == ["kintsugi-trace"], (
        "the same loop's next tick runs the template the PUT named, and one more job fits under the raised cap"
    )

    stopped = await _put(client, "scout:\n  templates: [kintsugi-trace]\n  max_in_flight: 0\n")
    assert stopped.status_code == 200, stopped.text
    assert (await loop.tick())["started"] == [], "max_in_flight 0: the next tick starts nothing"


async def test_a_job_s_hard_cap_is_the_section_s_job_budget_not_a_literal(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    resp = await _put(client, "scout:\n  job_budget_usd: 0.25\n")
    assert resp.status_code == 200, f"the settings document takes scout.job_budget_usd: {resp.text}"
    jobs = FakeJobs()
    loop = _loop(app.state.settings, db_with_migrations, jobs, [NOW])

    first = (await loop.tick())["started"]
    assert first and {jobs.budget(j) for j in first} == {0.25}, "each job's hard cap is scout.job_budget_usd"

    resp = await _put(client, "scout:\n  job_budget_usd: 2.5\n  max_in_flight: 3\n")
    assert resp.status_code == 200, resp.text
    later = (await loop.tick())["started"]
    assert later and {jobs.budget(j) for j in later} == {2.5}, "a new cap reaches the next job started"


async def test_the_daily_cap_counts_the_jobs_stored_in_the_last_24_hours(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    resp = await _put(client, "scout:\n  max_in_flight: 4\n  max_jobs_per_day: 2\n")
    assert resp.status_code == 200, f"the settings document takes scout.max_jobs_per_day: {resp.text}"
    runs = ScoutRunsRepository(db_with_migrations)
    for job_id, hours in (("job-old", 25), ("job-recent", 3)):
        await runs.record(
            job_id=job_id, repo="acme/other", area="", template="garden", now=NOW - timedelta(hours=hours)
        )
        await runs.harvested(job_id, leads=0, now=NOW)
    assert await runs.dispatched_since(NOW - timedelta(days=1)) == 1, "a job 25 hours old is outside the day"

    jobs = FakeJobs()
    clock = [NOW]
    loop = _loop(app.state.settings, db_with_migrations, jobs, clock)
    assert len((await loop.tick())["started"]) == 1, "one started 3 hours ago, so one more fits under 2 a day"
    assert (await loop.tick())["started"] == [], "two in 24 hours: none more, though two of four in flight are free"

    clock[0] = NOW + timedelta(hours=22)
    assert len((await loop.tick())["started"]) == 1, "22 hours on, the job of 3 hours ago has left the day"


async def test_each_bad_scout_value_is_a_422_naming_its_key_and_nothing_is_stored(
    app: Any, client: AsyncClient
) -> None:
    text = "scout:\n  templates: [garden, weed-whacker]\n  max_in_flight: 999\n  job_budget_usd: 5000\n"
    resp = await _put(client, text)
    assert resp.status_code == 422, f"a bad scout value is refused when it is written: {resp.text}"
    assert sorted(tuple(p["loc"][:2]) for p in resp.json()["detail"]) == [
        ("scout", "job_budget_usd"),
        ("scout", "max_in_flight"),
        ("scout", "templates"),
    ], f"each bad value is refused by its own key: {resp.text}"
    for value in ("weed-whacker", "999", "5000"):
        assert value not in resp.text, f"the 422 does not repeat {value!r}"
    stored = (await client.get(URL)).json()
    assert (stored["yaml_text"], stored["data"]) == ("", {}), "a refused document stores nothing"
    assert (await app.state.settings.snapshot()).get(_scout()).max_in_flight == 2, "so no tick ever reads it"


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def test_the_first_boot_imports_a_non_default_scout_value_once_and_a_second_does_not(
    db_with_migrations: DatabasePool,
) -> None:
    store = SettingsStore(db_with_migrations)
    config = _config(
        SCOUT_MODEL=LOCAL,
        SCOUT_TEMPLATES="garden,bogus",
        SCOUT_MAX_IN_FLIGHT="2",
        SCOUT_REVISIT_HOURS="48",
        SCOUT_MAX_LEADS_PER_JOB="500",
    )

    with capture_logs() as logs:
        first = await import_env_once(store, config, today=DAY)
    assert first.imported.get("scout") == {"templates": ["garden"], "revisit_hours": 48.0}, (
        "the values that differ from the defaults are imported as the scout read them: garden,bogus ran garden; "
        f"SCOUT_MAX_IN_FLIGHT=2 is the default, and SCOUT_MODEL is no key of the section: {first.imported}"
    )
    assert first.imported.get("background_models") == {"scout": LOCAL}, (
        f"SCOUT_MODEL is imported into the map of background models (#164): {first.imported}"
    )
    refused = [(e["variable"], e["key"]) for e in logs if e["event"] == "settings_bootstrap_value_refused"]
    assert refused == [("SCOUT_MAX_LEADS_PER_JOB", "scout.max_leads_per_job")], (
        "a value past the section's bound is reported by name and not imported"
    )
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"]) == (BOOTSTRAP_ACTOR, "bootstrap")
    imported = (await store.snapshot()).get(_scout())
    assert (imported.templates, imported.revisit_hours, imported.max_leads_per_job) == (("garden",), 48.0, 5)

    text = "scout:\n  revisit_hours: 6\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    again_store = SettingsStore(db_with_migrations)
    with capture_logs() as logs:
        again = await import_env_once(again_store, config, today=DAY)
        await log_ignored_env(again_store, config)
    assert (again.imported, again.revision_id) == ({}, None), "the second boot imports nothing"
    assert (await again_store.document())["data"] == {"scout": {"revisit_hours": 6.0}}, "the person's value stays"
    assert len(await again_store.revisions()) == 2
    ignored = {e["variable"] for e in logs if e["event"] == "settings_env_ignored"}
    assert ignored == {
        "SCOUT_TEMPLATES",
        "SCOUT_MAX_IN_FLIGHT",
        "SCOUT_REVISIT_HOURS",
        "SCOUT_MAX_LEADS_PER_JOB",
        "SCOUT_MODEL",
    }, "each policy variable still set is logged as ignored, SCOUT_MODEL too since #164"


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_builds_the_scout_loop_with_a_reader_of_the_document(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lifespan configures structlog, which would replace capture_logs' processors.
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
        scout_revisit_hours=12.0,
        scout_tick_seconds="60",
    )
    first = create_app(config)
    with capture_logs() as logs:
        async with lifespan(first):
            loop = first.state.scout
            assert callable(loop.settings), (
                "the scout loop is built with a reader of the settings document, not a value read once at startup"
            )
            assert (await loop.settings()).section.deployment().revisit_hours == 12.0, (
                "the boot imported SCOUT_REVISIT_HOURS into what it reads"
            )
            text = "scout:\n  revisit_hours: 3\n"
            await first.state.settings.save(text, load_yaml(text), actor="alex@example.com")
            assert (await loop.settings()).section.deployment().revisit_hours == 3.0, (
                "a write reaches the running loop's reader"
            )
            kept = len(await first.state.settings.revisions())
    retired = [e.get("variable") for e in logs if e["event"] == "settings_env_retired"]
    assert retired == ["SCOUT_TICK_SECONDS"], (
        f"the real startup warns once that SCOUT_TICK_SECONDS is set and no longer read: {retired}"
    )

    second = create_app(config)
    async with lifespan(second):
        assert (await second.state.scout.settings()).section.deployment().revisit_hours == 3.0, (
            "a second boot imports nothing again"
        )
        assert len(await second.state.settings.revisions()) == kept
