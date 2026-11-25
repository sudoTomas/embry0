"""The real lifespan builds the settings store before anything reads it, and names a retired variable still set.

Runs the REAL ``lifespan`` against the test database with only the container
runtime replaced, as ``tests/unit/metrics/test_lifespan_wiring.py`` does,
with the test-only section catalog in place of the application's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.config_retired import MOVED_TO_DOCUMENTS
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from tests.fixtures.settings_sections import SAMPLE, TEST_CATALOG
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture


def _config(db: DatabasePool, **extra: Any) -> Embry0Config:
    return Embry0Config(
        _env_file=None,
        database_url=db._url,  # noqa: SLF001
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
        **extra,
    )


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_builds_the_settings_store_and_writes_nothing_into_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The store reads the catalog in place; with no environment import, the boot stores no revision and no audit row."""
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASK_REMIND_HOURS", "12")
    app = create_app(_config(db_with_migrations))
    async with lifespan(app):
        store = app.state.settings
        assert isinstance(store, SettingsStore) and store.catalog is TEST_CATALOG
        assert (await store.snapshot()).get(SAMPLE).remind_hours == SAMPLE.defaults().remind_hours, (
            "a variable the release no longer reads changes nothing"
        )
        assert await store.revisions() == [], "the boot writes no revision"
        audit = await db_with_migrations.fetch("SELECT action FROM audit_log WHERE action = 'settings.bootstrapped'")
        assert audit == [], "and no import audit row"


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_warns_once_for_each_moved_variable_still_set_never_its_value(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A variable a one-time import read before is no longer read: the start says so, with where its setting lives."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    monkeypatch.chdir(tmp_path)  # no .env in the working directory: only the environment below
    for name in MOVED_TO_DOCUMENTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAX_BUDGET_USD", "73.25")
    monkeypatch.setenv("scout_repos", "acme/widgets")  # read without regard to case, as the config reads it
    app = create_app(_config(db_with_migrations))
    with capture_logs() as logs:
        async with lifespan(app):
            pass
    warned = [e for e in logs if e["event"] == "settings_env_retired"]
    assert [(e["log_level"], e["variable"]) for e in warned] == [
        ("warning", "MAX_BUDGET_USD"),
        ("warning", "SCOUT_REPOS"),
    ], f"the start names each moved variable still set, once, in the registry's order: {warned}"
    assert "budget.max_budget_per_job_usd" in warned[0]["hint"], warned[0]
    assert "background.scout" in warned[1]["hint"], warned[1]
    for value in ("73.25", "acme/widgets"):
        assert not any(value in repr(e) for e in logs), f"a variable's value is never logged: {value}"


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_says_a_deployment_skipped_the_release_that_imported_its_budget(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved budget_config row no import ever carried into the document: its caps read as the defaults now, so
    the start says so at error level and in the audit log (embry0.settings.upgrade_check)."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    await db_with_migrations.execute("INSERT INTO budget_config (id, daily_cap_usd) VALUES ('default', 5.0)")
    app = create_app(_config(db_with_migrations))
    with capture_logs() as logs:
        async with lifespan(app):
            pass
    said = [(e["log_level"], e.get("section")) for e in logs if e["event"] == "settings_upgrade_skipped"]
    assert said == [("error", "budget")], f"the start must say what a skipped release would have imported: {said}"
    flagged = await db_with_migrations.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'settings.upgrade.flagged'"
    )
    assert flagged == 1


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_a_layer_under_a_renamed_slug_applies_from_the_first_snapshot(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first snapshot is built before ``REPO_RENAMES`` is installed; it must still read a renamed repository's layer."""
    from embry0.storage import repo_renames
    from tests.fixtures.settings_sections import LAYERED_CATALOG

    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {})  # restored after the test; the lifespan installs its own
    monkeypatch.setattr("embry0.settings.sections.CATALOG", LAYERED_CATALOG)
    await db_with_migrations.execute(
        "INSERT INTO settings_documents (scope, yaml_text, data, generation) VALUES ($1, $2, $3::jsonb, 1)",
        "repository:acme/old-widgets",
        "sample:\n  mode: 'on'\n",
        {"sample": {"mode": "on"}},
    )
    app = create_app(_config(db_with_migrations, repo_renames='{"acme/old-widgets": "acme/widgets"}'))
    async with lifespan(app):
        assert repo_renames.REPO_RENAMES, "the lifespan installed the renames"
        snapshot = await app.state.settings.snapshot()
        assert snapshot.get(SAMPLE, repo="acme/widgets").mode == "on", (
            "the cached first snapshot lost the layer stored under the repository's old slug"
        )
        assert snapshot.get(SAMPLE, repo="acme/old-widgets").mode == "on"
