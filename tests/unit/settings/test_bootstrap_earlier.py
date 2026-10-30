"""Keys a release kept in a database record before the document are imported from it, once (#171).

The budget's five fleet keys lived in the ``budget_config`` row, which won
over their variables once saved. An ``EarlierSource`` reads such a record in
the import's own transaction: what it holds is imported from it, by the
environment import's rules, and never from the variable; with no record, the
variables are read as before.
"""

from __future__ import annotations

import inspect
import os
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.settings import bootstrap
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from tests.fixtures.settings_sections import TEST_CATALOG

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 10, 30)
ALL = ("sample.enabled", "sample.per_tick", "sample.remind_hours", "sample.labels", "sample.zone")


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _source(section: str, label: str, read: Any) -> Any:
    """An ``EarlierSource``; the test fails here, with its own message, on a tree that has none."""
    source = getattr(bootstrap, "EarlierSource", None)
    assert source is not None, "the settings import reads keys kept in the database before the document"
    assert "earlier" in inspect.signature(import_env_once).parameters, "import_env_once takes earlier sources"
    return source(section=section, label=label, read=read)


class Record:
    """A record kept before the document: what ``read`` answers, and how often it was read."""

    def __init__(self, values: dict[str, Any] | None) -> None:
        self.values = values
        self.reads = 0

    async def read(self, conn: Any, config: Any) -> dict[str, Any] | None:
        assert conn is not None, "the record is read on the import's own connection"
        self.reads += 1
        return None if self.values is None else dict(self.values)

    def source(self) -> Any:
        return _source("sample", "the sample table", self.read)


async def test_a_record_wins_over_the_variables_and_is_imported_once(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    record = Record({"per_tick": 7, "remind_hours": 24.0})
    config = _config(ASK_REMIND_HOURS="12", PROPOSAL_BRIEFS_PER_TICK="4", ASK_TIMEZONE="Europe/Berlin")

    result = await import_env_once(store, config, today=DAY, earlier=[record.source()])

    assert result.earlier == {"sample": {"per_tick": 7}}, "the record's value, not the variable's 4"
    assert result.imported == {"sample": {"per_tick": 7, "zone": "Europe/Berlin"}}, (
        "remind_hours equals its default in the record: nothing to write, and its variable is never read"
    )
    assert set(result.marked) == set(ALL)
    row = await store.document()
    assert row["data"] == {"sample": {"per_tick": 7, "zone": "Europe/Berlin"}}
    assert row["yaml_text"].startswith("# Imported from the sample table on 2026-10-30: per_tick.\n"), row["yaml_text"]
    assert "# Imported from the environment on 2026-10-30: ASK_TIMEZONE." in row["yaml_text"]
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"], revision["reason"]) == (
        BOOTSTRAP_ACTOR,
        "bootstrap",
        "imported from the sample table and the environment",
    )
    audit = await db_with_migrations.fetch("SELECT details FROM audit_log WHERE action = 'settings.bootstrapped'")
    assert [r["details"]["earlier"] for r in audit] == [{"sample": "the sample table"}]

    record.values = {"per_tick": 9}
    again = await import_env_once(SettingsStore(db_with_migrations, TEST_CATALOG), config, earlier=[record.source()])
    assert (again.imported, again.marked, again.revision_id) == ({}, (), None), "a record is imported once"
    assert (await store.document())["data"]["sample"]["per_tick"] == 7


async def test_without_a_record_the_variables_are_read(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    record = Record(None)

    result = await import_env_once(store, _config(PROPOSAL_BRIEFS_PER_TICK="4"), today=DAY, earlier=[record.source()])

    assert record.reads == 1
    assert (result.imported, result.earlier) == ({"sample": {"per_tick": 4}}, {})
    (revision,) = await store.revisions()
    assert revision["reason"] == "imported from the environment"


async def test_a_record_value_the_section_refuses_is_skipped_and_never_read_again(
    db_with_migrations: DatabasePool,
) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    with capture_logs() as logs:
        result = await import_env_once(
            store, _config(PROPOSAL_BRIEFS_PER_TICK="4"), today=DAY, earlier=[Record({"per_tick": 99}).source()]
        )
    assert result.imported == {}, "neither the refused record value nor the variable it won over"
    assert [(r.key, r.variable) for r in result.refused] == [("per_tick", "the sample table: per_tick")]
    refused = [e for e in logs if e["event"] == "settings_bootstrap_value_refused"]
    assert [e["key"] for e in refused] == ["sample.per_tick"]
    assert "99" not in str(refused), "a refused value is never echoed"
    assert "sample.per_tick" in result.marked


async def test_a_record_that_cannot_be_read_imports_nothing_and_marks_nothing(db_with_migrations: DatabasePool) -> None:
    """A failed import is retried at the next boot: nothing is marked, so the record is read again then."""
    store = SettingsStore(db_with_migrations, TEST_CATALOG)

    async def broken(conn: Any, config: Any) -> dict[str, Any] | None:
        raise ConnectionResetError("lost the record")

    with pytest.raises(ConnectionResetError):
        await import_env_once(
            store,
            _config(PROPOSAL_BRIEFS_PER_TICK="4"),
            earlier=[_source("sample", "the sample table", broken)],
        )
    row = await store.document()
    assert (row["data"], list(row["bootstrapped_keys"])) == ({}, []), "the variable is not imported in its place"
