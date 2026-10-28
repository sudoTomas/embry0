"""A section's environment values are imported into the document once (#160 acceptance)."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import Field, model_validator
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once, log_ignored_env
from embry0.settings.document import load_yaml
from embry0.settings.registry import Catalog, Section, SectionModel
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.settings_documents import SettingsDocumentsRepository
from tests.fixtures.settings_sections import BRITTLE, OTHER, SAMPLE, TEST_CATALOG

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 10, 28)


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def test_a_value_an_operator_set_is_imported_once_and_recorded(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    config = _config(ASK_REMIND_HOURS="12")

    result = await import_env_once(store, config, today=DAY)

    assert result.imported == {"sample": {"remind_hours": 12.0}}
    assert result.marked == (
        "sample.enabled",
        "sample.per_tick",
        "sample.remind_hours",
        "sample.labels",
        "sample.zone",
    ), "every key a section imports from is marked"
    row = await store.document()
    assert row["data"] == {"sample": {"remind_hours": 12.0}}
    assert row["yaml_text"] == (
        "# Imported from the environment on 2026-10-28: ASK_REMIND_HOURS.\nsample:\n  remind_hours: 12.0\n"
    )
    assert tuple(row["bootstrapped_keys"]) == (
        "sample.enabled",
        "sample.per_tick",
        "sample.remind_hours",
        "sample.labels",
        "sample.zone",
    )
    (revision,) = await store.revisions()
    assert (revision["id"], revision["actor"], revision["source"]) == (result.revision_id, BOOTSTRAP_ACTOR, "bootstrap")
    audit = await db_with_migrations.fetch(
        "SELECT actor, details FROM audit_log WHERE action = 'settings.bootstrapped'"
    )
    assert [(r["actor"], r["details"]) for r in audit] == [
        (BOOTSTRAP_ACTOR, {"sections": {"sample": ["remind_hours"]}, "revision_id": result.revision_id})
    ], "the import writes its audit row through the store's own pool, with no pool passed in"
    assert (await store.snapshot()).get(SAMPLE).remind_hours == 12.0


async def test_a_second_boot_does_not_import_again(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    config = _config(ASK_REMIND_HOURS="12")
    await import_env_once(store, config, today=DAY)
    text = "sample:\n  remind_hours: 6.0\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")

    again = await import_env_once(SettingsStore(db_with_migrations, TEST_CATALOG), config, today=DAY)

    assert (again.imported, again.marked, again.revision_id) == ({}, (), None)
    assert (await store.document())["data"] == {"sample": {"remind_hours": 6.0}}, "the person's value stays"
    assert len(await store.revisions()) == 2


async def test_a_value_a_person_removed_is_not_imported_again(db_with_migrations: DatabasePool) -> None:
    """The ledger, not the document's content, says a section was imported."""
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    config = _config(ASK_REMIND_HOURS="12")
    await import_env_once(store, config, today=DAY)
    await store.save("# back to the defaults\n", {}, actor="alex@example.com")

    again = await import_env_once(SettingsStore(db_with_migrations, TEST_CATALOG), config, today=DAY)

    assert again.imported == {}, "the environment value came back after a person removed it"
    assert (await store.snapshot()).get(SAMPLE).remind_hours == 24.0


async def test_a_value_equal_to_the_default_or_unset_is_not_imported(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    result = await import_env_once(store, _config(ASK_REMIND_HOURS="24", SCOUT_REPOS=""), today=DAY)
    assert (result.imported, result.marked) == (
        {},
        ("sample.enabled", "sample.per_tick", "sample.remind_hours", "sample.labels", "sample.zone"),
    ), "every key is marked even when nothing came"
    row = await store.document()
    assert (row["yaml_text"], row["data"], await store.revisions()) == ("", {}, [])


async def test_a_refused_value_is_skipped_and_the_rest_imported(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    config = _config(ASK_TIMEZONE="Mars/Base", SCOUT_REPOS="acme/widgets, acme/gadgets", ASKS_ENABLED="true")
    with capture_logs() as logs:
        result = await import_env_once(store, config, today=DAY)
    assert result.imported == {"sample": {"enabled": True, "labels": ["acme/widgets", "acme/gadgets"]}}
    assert [(r.key, r.variable) for r in result.refused] == [("zone", "ASK_TIMEZONE")]
    refused = [entry for entry in logs if entry["event"] == "settings_bootstrap_value_refused"]
    assert [(e["variable"], e["key"], e["reason"]) for e in refused] == [
        ("ASK_TIMEZONE", "sample.zone", "not a time zone this system knows")
    ]


async def test_an_import_keeps_the_comments_already_written(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    text = "# our policy\nother:\n  limit: 2  # reviewed\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    await import_env_once(store, _config(PROPOSAL_BRIEFS_PER_TICK="7"), today=DAY)
    row = await store.document()
    assert row["yaml_text"] == (
        "# our policy\nother:\n  limit: 2  # reviewed\n\n"
        "# Imported from the environment on 2026-10-28: PROPOSAL_BRIEFS_PER_TICK.\nsample:\n  per_tick: 7\n"
    )
    assert row["data"] == {"other": {"limit": 2}, "sample": {"per_tick": 7}}


async def test_two_processes_booting_together_import_once(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each import holds the document open a while after reading it, so without the row lock all of
    them would read an empty ledger and import; with it, the others wait and find the section done."""
    lock = SettingsDocumentsRepository.lock

    async def slow_lock(self: SettingsDocumentsRepository, conn: Any, scope: str) -> dict[str, Any]:
        row = await lock(self, conn, scope)
        await asyncio.sleep(0.3)
        return row

    monkeypatch.setattr(SettingsDocumentsRepository, "lock", slow_lock)
    config = _config(ASK_REMIND_HOURS="12")
    stores = [SettingsStore(db_with_migrations, TEST_CATALOG) for _ in range(4)]
    results = await asyncio.gather(*(import_env_once(s, config, today=DAY) for s in stores))
    assert sum(1 for r in results if r.imported) == 1, "the row lock lets one import through"
    assert len(await stores[0].revisions()) == 1


async def test_a_catalog_with_nothing_to_import_touches_nothing(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, Catalog((OTHER,)))
    result = await import_env_once(store, _config(ASK_REMIND_HOURS="12"), today=DAY)
    assert (result.imported, result.marked) == ({}, ())
    assert (await store.document())["generation"] == 1


async def test_a_variable_still_set_after_the_import_is_named_at_every_boot(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    config = _config(ASK_REMIND_HOURS="12", ASK_TIMEZONE="Europe/Berlin")
    with capture_logs() as before_import:
        assert await log_ignored_env(store, config) == [], "before the import the variable is still its source"
    assert not [e for e in before_import if e["event"] == "settings_env_ignored"]

    await import_env_once(store, config, today=DAY)
    text = "sample:\n  remind_hours: 6.0\n  zone: Europe/Berlin\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    with capture_logs() as logs:
        ignored = await log_ignored_env(store, config)
    assert [v.name for v in ignored] == ["ASK_REMIND_HOURS", "ASK_TIMEZONE"]
    warned = {e["variable"]: e["matches"] for e in logs if e["event"] == "settings_env_ignored"}
    assert warned == {"ASK_REMIND_HOURS": False, "ASK_TIMEZONE": True}


class _Grown(SectionModel):
    enabled: bool = Field(default=False, description="On or off.")


class _GrownLater(SectionModel):
    enabled: bool = Field(default=False, description="On or off.")
    remind_hours: float = Field(default=24.0, gt=0, description="A key a later release adds.")


async def test_a_key_a_later_release_adds_to_an_imported_section_still_imports_its_variable(
    db_with_migrations: DatabasePool,
) -> None:
    config = _config(ASKS_ENABLED="true", ASK_REMIND_HOURS="12")
    before = Section(name="grown", model=_Grown, summary="Before.", env={"enabled": "asks_enabled"})
    after = Section(
        name="grown",
        model=_GrownLater,
        summary="After.",
        env={"enabled": "asks_enabled", "remind_hours": "ask_remind_hours"},
    )
    await import_env_once(SettingsStore(db_with_migrations, Catalog((before,))), config, today=DAY)
    store = SettingsStore(db_with_migrations, Catalog((after,)))

    result = await import_env_once(store, config, today=DAY)

    assert result.imported == {"grown": {"remind_hours": 12.0}}, (
        "a key added to a section later was skipped because its section was imported before"
    )
    row = await store.document()
    assert row["data"] == {"grown": {"enabled": True, "remind_hours": 12.0}}
    assert load_yaml(row["yaml_text"]) == row["data"], "the text still holds one section, in one block"


async def test_a_variable_whose_check_raises_keyerror_is_skipped_and_the_rest_imported(
    db_with_migrations: DatabasePool,
) -> None:
    store = SettingsStore(db_with_migrations, Catalog((BRITTLE,)))
    config = _config(ASK_TIMEZONE="Mars/Base", ASK_REMIND_HOURS="12")
    try:
        result = await import_env_once(store, config, today=DAY)
    except Exception as exc:  # noqa: BLE001 - the test reports what escaped
        pytest.fail(f"one bad variable aborted the whole import with {type(exc).__name__}, at every boot")
    assert result.imported == {"brittle": {"hours": 12.0}}
    assert [(r.key, r.variable) for r in result.refused] == [("zone", "ASK_TIMEZONE")]


async def test_a_stored_value_this_release_refuses_does_not_block_the_import(db_with_migrations: DatabasePool) -> None:
    """A newer release stored ``per_tick: 99``; this one allows 20. It reads as its default, and the import goes on."""
    await db_with_migrations.execute(
        "UPDATE settings_documents SET data = $1::jsonb, yaml_text = $2, generation = generation + 1",
        {"sample": {"per_tick": 99}},
        "sample:\n  per_tick: 99\n",
    )
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    result = await import_env_once(store, _config(ASK_REMIND_HOURS="12"), today=DAY)
    assert result.imported == {"sample": {"remind_hours": 12.0}}, (
        f"a stored value this release refuses blocked the import: {[r.reason for r in result.refused]}"
    )
    assert (await store.document())["data"] == {"sample": {"per_tick": 99, "remind_hours": 12.0}}


class _Range(SectionModel):
    low: int = Field(default=0, ge=0, description="The low end.")
    high: int = Field(default=10, ge=0, description="The high end.")

    @model_validator(mode="after")
    def _ordered(self) -> _Range:
        if self.low > self.high:
            raise ValueError("low must not be above high")
        return self


RANGE = Section(name="range", model=_Range, summary="A range.", env={"low": "leads_review_stale_days"})


async def test_a_value_refused_only_with_the_other_values_is_tried_again_at_the_next_boot(
    db_with_migrations: DatabasePool,
) -> None:
    store = SettingsStore(db_with_migrations, Catalog((RANGE,)))
    text = "range:\n  high: 5\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    config = _config(LEADS_REVIEW_STALE_DAYS="7")

    first = await import_env_once(store, config, today=DAY)

    assert first.imported == {}
    assert "range.low" not in (await store.document())["bootstrapped_keys"], (
        "a value refused for another key's reason was marked imported, so it is never tried again"
    )
    text = "range:\n  high: 10\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    second = await import_env_once(store, config, today=DAY)
    assert second.imported == {"range": {"low": 7}}
