"""The asks policy lives in the settings document (#160's pilot), end to end on a real database.

- A PUT of the document reaches the next tick of a running ask loop, with no restart.
- A bad hours, weekdays or timezone value is a 422 naming its key, and nothing is stored (#202).
- The first boot imports a non-default ``ASK*`` value once; a second boot does not.
- The real lifespan builds the ask loop with a reader of the document, not a value read once.

Per organization, through the organization layers: ``test_asks_per_organization.py``.

The section is taken from the application's catalog by name, so each test
fails with its own message on a tree that has no asks section.
"""

from __future__ import annotations

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
from embry0.services.ask_loop import AskLoop
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once, log_ignored_env
from embry0.settings.document import load_yaml
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore, scoped_reader
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
ADMIN = "emb_" + "a" * 43
DAY = date(2026, 10, 28)
# Tuesday 2026-10-27, 15:00 UTC.
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
ALWAYS = "  hours: 00-24\n  weekdays: mon-sun\n"


def _asks() -> Section[Any]:
    section = CATALOG.get("asks")
    assert section is not None, "asks is a section of the settings document"
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


class FakeAsks:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> dict[tuple[str, str], Any]:
        return {
            (r["decision_id"], r["recipient"]): None for r in self.rows if r["state"] == "expired" and r.get("sent_at")
        }

    async def code(self, decision_id: str) -> str:
        return "ABCD"

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        ask_id = f"a{len(self.rows) + 1}"
        row = {"id": ask_id, "decision_id": decision_id, "recipient": recipient, "state": "queued"}
        self.rows.append({**row, "due_at": due_at, "created_at": NOW})
        return ask_id

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ask_ids):
                r["state"] = state
        return len(set(ask_ids))


async def test_a_put_reaches_the_next_tick_of_the_running_loop(app: Any, client: AsyncClient) -> None:
    off = await _put(client, "asks:\n  enabled: false\n")
    assert off.status_code == 200, f"the settings document takes an asks section: {off.text}"
    section = _asks()
    two_days_old = {
        "id": "a1",
        "decision_id": "d1",
        "recipient": "ada@example.com",
        "state": "sent",
        "due_at": NOW - timedelta(days=2),
        "created_at": NOW - timedelta(days=2),
        "sent_at": NOW - timedelta(days=2),
    }
    asks = FakeAsks([two_days_old])
    read: list[str] = []

    async def decisions() -> list[dict[str, Any]]:
        read.append("decisions")
        return [{"id": "d1", "owners": ["ada"]}, {"id": "d2", "owners": ["ada"]}]

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    loop = AskLoop(
        settings=scoped_reader(app.state.settings, section),
        asks=asks,
        decisions=decisions,
        people=people,
        clock=lambda: NOW,
    )

    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 0}
    assert read == [], "off: the tick reads no decision"

    on = await _put(client, "asks:\n  enabled: true\n" + ALWAYS)
    assert on.status_code == 200, on.text
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 1}, (
        "turned on through the API: the same loop's next tick queues d2, and the ask two days old "
        "stays live under the default expire_days 7"
    )

    shorter = await _put(client, "asks:\n  enabled: true\n  expire_days: 1\n" + ALWAYS)
    assert shorter.status_code == 200, shorter.text
    assert (await loop.tick())["expired"] == 1, "expire_days 1: the next tick expires the ask two days old"
    assert asks.rows[0]["state"] == "expired"

    states = [r["state"] for r in asks.rows]
    off = await _put(client, "asks:\n  enabled: false\n")
    assert off.status_code == 200, off.text
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 0}
    assert [r["state"] for r in asks.rows] == states, "turned off: the next tick changes nothing"


async def test_each_bad_ask_value_is_a_422_naming_its_key_and_nothing_is_stored(app: Any, client: AsyncClient) -> None:
    text = "asks:\n  enabled: true\n  hours: nine to five\n  weekdays: mon-fry\n  timezone: Mars/Base\n"
    resp = await _put(client, text)
    assert resp.status_code == 422, resp.text
    assert [p["loc"] for p in resp.json()["detail"]] == [
        ["asks", "hours"],
        ["asks", "weekdays"],
        ["asks", "timezone"],
    ], f"each bad value is refused by its own key, not guessed: {resp.text}"
    for value in ("nine to five", "mon-fry", "Mars/Base"):
        assert value not in resp.text, f"the 422 does not repeat {value!r}"
    stored = (await client.get(URL)).json()
    assert (stored["yaml_text"], stored["data"]) == ("", {}), "a refused document stores nothing"
    assert (await app.state.settings.snapshot()).get(_asks()).enabled is False, "so no tick ever reads it"


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def test_the_first_boot_imports_a_non_default_ask_value_once_and_a_second_does_not(
    db_with_migrations: DatabasePool,
) -> None:
    store = SettingsStore(db_with_migrations)
    config = _config(
        ASK_REMIND_HOURS="12",
        ASK_HOURS="8-17",
        ASK_WEEKDAYS="Mon-Fri",
        ASKS_ENABLED="false",
        ASK_TIMEZONE="Mars/Base",
    )

    with capture_logs() as logs:
        first = await import_env_once(store, config, today=DAY)
    assert first.imported == {"asks": {"hours": "08-17", "remind_hours": 12.0}}, (
        "the values that differ from the defaults are imported; Mon-Fri reads as the default mon-fri"
    )
    refused = [(e["variable"], e["key"]) for e in logs if e["event"] == "settings_bootstrap_value_refused"]
    assert refused == [("ASK_TIMEZONE", "asks.timezone")], "an unknown zone is reported by name and not imported"
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"]) == (BOOTSTRAP_ACTOR, "bootstrap")
    imported = (await store.snapshot()).get(_asks())
    assert (imported.remind_hours, imported.hours, imported.timezone) == (12.0, "08-17", "UTC")

    text = "asks:\n  remind_hours: 6\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    again_store = SettingsStore(db_with_migrations)
    with capture_logs() as logs:
        again = await import_env_once(again_store, config, today=DAY)
        await log_ignored_env(again_store, config)
    assert (again.imported, again.revision_id) == ({}, None), "the second boot imports nothing"
    assert (await again_store.document())["data"] == {"asks": {"remind_hours": 6.0}}, "the person's value stays"
    assert len(await again_store.revisions()) == 2
    ignored = {e["variable"] for e in logs if e["event"] == "settings_env_ignored"}
    assert ignored == {"ASKS_ENABLED", "ASK_HOURS", "ASK_WEEKDAYS", "ASK_TIMEZONE", "ASK_REMIND_HOURS"}, (
        "each variable still set is logged as ignored"
    )


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_builds_the_ask_loop_with_a_reader_of_the_document(
    db_with_migrations: DatabasePool,
) -> None:
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
        ask_remind_hours=12.0,
    )
    first = create_app(config)
    async with lifespan(first):
        loop = first.state.ask_loop
        assert callable(loop.settings), (
            "the ask loop is built with a reader of the settings document, not a value read once at startup"
        )
        assert (await loop.settings()).deployment().remind_hours == 12.0, (
            "the boot imported ASK_REMIND_HOURS into what it reads"
        )
        text = "asks:\n  remind_hours: 3\n"
        await first.state.settings.save(text, load_yaml(text), actor="alex@example.com")
        assert (await loop.settings()).deployment().remind_hours == 3.0, "a write reaches the running loop's reader"
        kept = len(await first.state.settings.revisions())

    second = create_app(config)
    async with lifespan(second):
        assert (await second.state.ask_loop.settings()).deployment().remind_hours == 3.0, (
            "a second boot imports nothing again"
        )
        assert len(await second.state.settings.revisions()) == kept
