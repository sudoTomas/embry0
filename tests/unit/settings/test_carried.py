"""A setting a migration carried out of a retired column is written into the deployment's settings document at the
first start (``embry0.settings.carried``, migration 161), through the store, once."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]


def _write() -> Any:
    try:
        return importlib.import_module("embry0.settings.carried").write_carried_settings
    except ImportError:
        pytest.fail("nothing writes a carried setting into the settings document (embry0.settings.carried)")


async def _carry(db: DatabasePool, key: str, value: Any) -> None:
    await db.execute(
        "INSERT INTO settings_carried (key, value, from_where) VALUES ($1, $2::jsonb, 'provider_config.local_max_runs')",
        key,
        value,
    )


async def _left(db: DatabasePool) -> list[str]:
    return [str(r["key"]) for r in await db.fetch("SELECT key FROM settings_carried ORDER BY key")]


async def test_a_carried_value_becomes_the_document_s_and_leaves_its_row(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    store = SettingsStore(db)
    await _carry(db, "local_models.max_runs", 3)
    done = await _write()(db, store)
    assert done == {"local_models.max_runs": "written"}, done
    document = await store.document()
    assert document["data"].get("local_models") == {"max_runs": 3}, f"the document does not say it: {document['data']}"
    assert "max_runs: 3" in document["yaml_text"], f"the operator's text does not say it: {document['yaml_text']!r}"
    [revision] = await store.revisions(limit=1)
    assert revision.get("source") == "upgrade", f"the write must be recorded as the upgrade's: {revision}"
    assert await _left(db) == [], "the carried row stayed: the next start would write it again"
    assert await _write()(db, store) == {}, "a second start found something to write"


async def test_a_value_the_document_already_sets_is_kept_and_the_row_goes(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    store = SettingsStore(db)
    await store.set_keys("local_models", {"max_runs": 5}, actor="a-person")
    await _carry(db, "local_models.max_runs", 3)
    done = await _write()(db, store)
    assert done == {"local_models.max_runs": "already_set"}, done
    assert (await store.document())["data"]["local_models"] == {"max_runs": 5}, "a person's value was overwritten"
    assert await _left(db) == []


async def test_a_value_the_document_refuses_stays_for_an_admin(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    store = SettingsStore(db)
    await _carry(db, "local_models.max_runs", 99)
    with capture_logs() as logs:
        done = await _write()(db, store)
    assert done == {"local_models.max_runs": "refused"}, done
    assert await _left(db) == ["local_models.max_runs"], "a refused value must stay to be tried again"
    assert [e["log_level"] for e in logs if e["event"] == "settings_carried_refused"] == ["error"], logs
    assert "local_models" not in (await store.document())["data"]


async def test_a_table_that_cannot_be_read_holds_up_no_start(db_with_migrations: DatabasePool) -> None:
    class Down:
        async def fetch(self, *_a: Any, **_k: Any) -> Any:
            raise OSError("the database went away")

    with capture_logs() as logs:
        assert await _write()(Down(), SettingsStore(db_with_migrations)) == {}
    assert [e["event"] for e in logs] == ["settings_carried_unread"], logs
