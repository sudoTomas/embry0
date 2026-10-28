"""The real lifespan builds the settings store and imports the environment before anything reads it.

Runs the REAL ``lifespan`` against the test database with only the container
runtime replaced, as ``tests/unit/metrics/test_lifespan_wiring.py`` does,
with the test-only section catalog in place of the application's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import embry0.api.app as app_module
from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from tests.fixtures.settings_sections import SAMPLE, TEST_CATALOG
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_imports_the_settings_environment_once(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
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
    app = create_app(config)
    async with lifespan(app):
        store = app.state.settings
        assert isinstance(store, SettingsStore) and store.catalog is TEST_CATALOG
        assert (await store.snapshot()).get(SAMPLE).remind_hours == 12.0, "the boot imported the set value"
        assert [(r["actor"], r["source"]) for r in await store.revisions()] == [(BOOTSTRAP_ACTOR, "bootstrap")]
        audit = await db_with_migrations.fetch(
            "SELECT actor, details FROM audit_log WHERE action = 'settings.bootstrapped'"
        )
        assert [(r["actor"], r["details"]["sections"]) for r in audit] == [
            (BOOTSTRAP_ACTOR, {"sample": ["remind_hours"]})
        ], "the boot's import writes its audit row"


def test_the_import_runs_before_the_app_state_and_the_loops_are_built() -> None:
    src = Path(app_module.__file__).read_text(encoding="utf-8")
    lifespan_src = src[src.index("async def lifespan(") :]
    assert lifespan_src.index("await run_migrations(db)") < lifespan_src.index("await import_env_once("), (
        "the import needs the migrated tables"
    )
    assert lifespan_src.index("await import_env_once(") < lifespan_src.index("await _init_app_state("), (
        "every loop is built after _init_app_state; the import must come first"
    )
