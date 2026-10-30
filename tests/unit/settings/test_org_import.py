"""An organization's settings that moved into a section are copied into its layer, once (spec 2026-10-29, §9).

Proven with the test-only section, whose ``per_tick`` and ``remind_hours`` an
organization may set: the first feature to move a key builds this once, as
#160 built the environment import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.settings.bootstrap import BOOTSTRAP_ACTOR
from embry0.settings.scope import Scope
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from tests.fixtures.settings_sections import SAMPLE, TEST_CATALOG

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 10, 30)


async def import_org_settings_once(*args: Any, **kwargs: Any) -> Any:
    """The import; the test fails here, with its own message, on a tree that has none."""
    try:
        from embry0.settings.org_import import import_org_settings_once as run
    except ImportError:
        pytest.fail("no copy of the organization settings that moved into a section (embry0.settings.org_import)")
    return await run(*args, **kwargs)


def _read_per_tick(settings: Mapping[str, Any]) -> int | None:
    """How the old code read its key: a whole number from 1 to 50, else the deployment's."""
    value = settings.get("sample_per_tick")
    return value if type(value) is int and 1 <= value <= 50 else None


@dataclass(frozen=True)
class Moved:
    legacy: str
    section: str
    key: str
    read: Any


MOVED = (
    Moved("sample_per_tick", "sample", "per_tick", _read_per_tick),
    Moved("sample_remind", "sample", "remind_hours", lambda s: s.get("sample_remind")),
)


async def test_a_moved_setting_is_copied_into_the_organizations_layer_once(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)

    result = await import_org_settings_once(store, [("acme", {"sample_per_tick": 7, "other": True})], MOVED, today=DAY)

    assert result.imported == {"acme": {"sample": {"per_tick": 7}}}
    layer = await store.document(Scope.organization("acme"))
    assert layer["data"] == {"sample": {"per_tick": 7}}
    assert layer["yaml_text"] == (
        "# Imported from the organization's settings on 2026-10-30: sample_per_tick.\nsample:\n  per_tick: 7\n"
    )
    assert list(layer["bootstrapped_keys"]) == ["sample.per_tick"], "a key absent from the settings is not marked"
    (revision,) = await store.revisions(scope=Scope.organization("acme"))
    assert (revision["actor"], revision["source"]) == (BOOTSTRAP_ACTOR, "bootstrap")
    audit = await db_with_migrations.fetch(
        "SELECT actor, details FROM audit_log WHERE action = 'settings.bootstrapped'"
    )
    assert [(r["actor"], r["details"]) for r in audit] == [
        (BOOTSTRAP_ACTOR, {"scope": "organization:acme", "sections": {"sample": ["per_tick"]}})
    ]
    assert (await store.snapshot()).get(SAMPLE, owner="acme").per_tick == 7

    again = await import_org_settings_once(store, [("acme", {"sample_per_tick": 9})], MOVED, today=DAY)
    assert again.imported == {}, "copied once: the old row is read by nothing after the move"
    assert len(await store.revisions(scope=Scope.organization("acme"))) == 1


async def test_a_value_taken_out_of_the_layer_is_never_copied_back(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    await import_org_settings_once(store, [("acme", {"sample_per_tick": 7})], MOVED)
    await store.set_keys("sample", {"per_tick": None}, actor="alex@example.com", scope=Scope.organization("acme"))

    again = await import_org_settings_once(store, [("acme", {"sample_per_tick": 7})], MOVED)

    assert again.imported == {}
    assert (await store.snapshot()).get(SAMPLE, owner="acme").per_tick == 3, "it inherits the deployment's"


async def test_null_or_no_setting_copies_nothing_and_writes_no_layer(db_with_migrations: DatabasePool) -> None:
    """Null meant the deployment's value, which a layer that does not set the key inherits."""
    store = SettingsStore(db_with_migrations, TEST_CATALOG)

    result = await import_org_settings_once(store, [("acme", {"sample_per_tick": None}), ("beta", {})], MOVED)

    assert result.imported == {}
    assert await store.layers() == [], "no layer row is created for nothing"


async def test_a_value_the_old_reader_read_as_the_deployments_is_not_copied(db_with_migrations: DatabasePool) -> None:
    """A row POST /orgs stored unchecked once: the old reader read 99 as the deployment's, so the layer must too."""
    store = SettingsStore(db_with_migrations, TEST_CATALOG)

    result = await import_org_settings_once(store, [("acme", {"sample_per_tick": 99})], MOVED)

    assert (result.imported, result.refused) == ({}, ())
    assert (await store.snapshot()).get(SAMPLE, owner="acme").per_tick == 3
    assert await store.layers() == [], "nothing to copy writes no layer"


async def test_a_value_the_section_refuses_is_not_copied_and_the_rest_is(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    with capture_logs() as logs:
        result = await import_org_settings_once(store, [("acme", {"sample_per_tick": 4, "sample_remind": -1.0})], MOVED)

    assert result.imported == {"acme": {"sample": {"per_tick": 4}}}
    assert [(owner, key) for owner, key, _ in result.refused] == [("acme", "sample.remind_hours")]
    events = [e for e in logs if e["event"] == "settings_org_value_refused"]
    assert [(e["owner"], e["key"]) for e in events] == [("acme", "sample.remind_hours")]
    assert "-1" not in str(events), "a refused value is never echoed"
    layer = await store.document(Scope.organization("acme"))
    assert sorted(layer["bootstrapped_keys"]) == ["sample.per_tick", "sample.remind_hours"], "not tried again"


async def test_a_key_already_set_in_the_layer_wins_and_is_marked(db_with_migrations: DatabasePool) -> None:
    """Written there since, through the layer or the old route standing in for it: that is the newer value."""
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    await store.set_keys("sample", {"per_tick": 12}, actor="alex@example.com", scope=Scope.organization("acme"))

    result = await import_org_settings_once(store, [("acme", {"sample_per_tick": 7})], MOVED)

    assert result.imported == {}
    layer = await store.document(Scope.organization("acme"))
    assert (layer["data"], list(layer["bootstrapped_keys"])) == ({"sample": {"per_tick": 12}}, ["sample.per_tick"])


async def test_an_owner_no_layer_can_be_named_for_is_skipped(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations, TEST_CATALOG)
    with capture_logs() as logs:
        result = await import_org_settings_once(store, [("not an owner!", {"sample_per_tick": 7})], MOVED)
    assert result.imported == {}
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["settings_org_import_skipped"]
