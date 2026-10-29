"""The settings store: history and undo, and a snapshot every reader sees change on its next read (#160)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, load_yaml
from embry0.settings.registry import Catalog
from embry0.settings.store import (
    WHY_NOT,
    GenerationMismatch,
    NotUndoable,
    RevisionInvalid,
    SettingsStore,
    section_reader,
)
from embry0.storage.database import DatabasePool
from embry0.storage.repositories import config_revisions
from tests.fixtures.settings_sections import BRITTLE, OTHER, SAMPLE, TEST_CATALOG

pytestmark = pytest.mark.requires_postgres

V1 = "# the first\nsample:\n  per_tick: 4\n"
V2 = "# the second\nsample:\n  per_tick: 5\n  mode: 'on'\n"


def _store(db: DatabasePool, catalog: Catalog = TEST_CATALOG) -> SettingsStore:
    return SettingsStore(db, catalog)


async def _save(store: SettingsStore, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("actor", "alex@example.com")
    return await store.save(text, load_yaml(text), **kwargs)


async def test_a_save_stores_the_text_and_records_who_and_how(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    result = await _save(store, V1, source="file", reason="weekly tuning")
    row = await store.document()
    assert (row["yaml_text"], row["data"], row["generation"]) == (V1, {"sample": {"per_tick": 4}}, 2)
    (revision,) = await store.revisions()
    assert revision["id"] == result.revision_id
    assert (revision["actor"], revision["source"], revision["reason"]) == ("alex@example.com", "file", "weekly tuning")
    assert revision["before"] == {"yaml_text": "", "data": {}, "schema_version": 1}
    assert revision["after"] == {"yaml_text": V1, "data": {"sample": {"per_tick": 4}}, "schema_version": 1}
    assert result.changes == [
        {
            "key": "sample.per_tick",
            "before": None,
            "after": 4,
            "set_before": False,
            "set_after": True,
            "effective_before": 3,
            "effective_after": 4,
        }
    ]


async def test_an_unchanged_text_stores_nothing_and_records_nothing(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    first = await _save(store, V1)
    again = await _save(store, V1)
    assert again.revision_id is None and again.generation == first.generation, "an unchanged apply is not a change"
    assert len(await store.revisions()) == 1


async def test_a_refused_document_stores_nothing(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, V1)
    with pytest.raises(DocumentInvalid):
        await _save(store, "sample:\n  per_tick: 99\n")
    assert (await store.document())["yaml_text"] == V1
    assert len(await store.revisions()) == 1


async def test_a_write_over_a_newer_generation_is_refused(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    first = await _save(store, V1)
    await _save(store, V2)
    with pytest.raises(GenerationMismatch) as caught:
        await _save(store, "sample:\n  per_tick: 6\n", if_generation=first.generation)
    assert caught.value.current == first.generation + 1
    assert (await store.document())["yaml_text"] == V2, "the stale write changed nothing"


async def test_another_process_sees_a_write_on_its_next_snapshot(db_with_migrations: DatabasePool) -> None:
    writer, reader = _store(db_with_migrations), _store(db_with_migrations)
    read = section_reader(reader, SAMPLE)
    assert (await read()).per_tick == 3, "nothing written reads as the default"
    await _save(writer, V1)
    assert (await read()).per_tick == 4, "a second store sees the write on its next read, with no restart"
    assert (await reader.snapshot()).get(OTHER).limit == 5


async def test_an_unmoved_generation_reuses_the_snapshot(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    first = await store.snapshot()
    assert await store.snapshot() is first, "the values are read again only when the generation moved"
    await _save(store, V1)
    assert (await store.snapshot()).generation == first.generation + 1


async def test_a_failed_check_keeps_the_last_snapshot(db_with_migrations: DatabasePool, monkeypatch) -> None:
    store = _store(db_with_migrations)
    await _save(store, V1)
    last = await store.snapshot()

    async def down(*args: Any) -> Any:
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(store._documents, "token", down)
    assert await store.snapshot() is last, "a tick never fails because the check did"
    fresh = _store(db_with_migrations)
    monkeypatch.setattr(fresh._documents, "token", down)
    with pytest.raises(ConnectionError):
        await fresh.snapshot()


async def test_a_stored_value_the_code_refuses_reads_as_its_default(db_with_migrations: DatabasePool) -> None:
    await db_with_migrations.execute(
        "UPDATE settings_documents SET data = $1::jsonb, generation = generation + 1",
        {"sample": {"per_tick": 99, "remind_hours": 6.0}},
    )
    snapshot = await _store(db_with_migrations).snapshot()
    sample = snapshot.get(SAMPLE)
    assert (sample.per_tick, sample.remind_hours) == (3, 6.0)
    assert [(r.section, r.key) for r in snapshot.sections.refused] == [("sample", "per_tick")]


async def test_an_error_inside_an_edit_writes_nothing(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    with pytest.raises(RuntimeError):
        async with store.edit(actor="alex@example.com", source="person") as draft:
            draft.replace("sample:\n  per_tick: 7\n", {"sample": {"per_tick": 7}}, 1)
            raise RuntimeError("stop")
    assert (await store.document())["generation"] == 1
    assert await store.revisions() == []


async def test_only_the_newest_revisions_are_kept(db_with_migrations: DatabasePool, monkeypatch) -> None:
    monkeypatch.setattr(config_revisions, "KEEP_PER_SURFACE", 3)
    store = _store(db_with_migrations)
    for n in range(1, 6):
        await _save(store, f"sample:\n  per_tick: {n}\n")
    kept = [r["after"]["data"]["sample"]["per_tick"] for r in await store.revisions()]
    assert kept == [5, 4, 3]


# ---- #160 acceptance: a PUT followed by an undo restores the previous document ----


async def test_undo_restores_the_previous_text_and_is_recorded(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, V1)
    second = await _save(store, V2)
    undone = await store.undo(second.revision_id, actor="sam@example.com")
    row = await store.document()
    assert row["yaml_text"] == V1, "undo puts back the text byte for byte, comments included"
    newest = (await store.revisions())[0]
    assert newest["id"] == undone.revision_id
    assert (newest["actor"], newest["source"], newest["reason"]) == (
        "sam@example.com",
        "restore",
        f"undo of change {second.revision_id}",
    )
    assert (await store.snapshot()).get(SAMPLE).per_tick == 4


async def test_an_undo_can_itself_be_undone(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, V1)
    second = await _save(store, V2)
    undone = await store.undo(second.revision_id, actor="sam@example.com")
    await store.undo(undone.revision_id, actor="sam@example.com")
    assert (await store.document())["yaml_text"] == V2


async def test_only_the_newest_change_can_be_undone(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    first = await _save(store, V1)
    await _save(store, V2)
    with pytest.raises(NotUndoable, match=WHY_NOT):
        await store.undo(first.revision_id, actor="sam@example.com")
    assert (await store.document())["yaml_text"] == V2


async def test_an_old_text_todays_sections_refuse_is_not_put_back(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, "other:\n  limit: 2\n")
    second = await _save(store, "other:\n  limit: 3\n")
    without_other = _store(db_with_migrations, Catalog((SAMPLE,)))
    with pytest.raises(RevisionInvalid) as caught:
        await without_other.undo(second.revision_id, actor="sam@example.com")
    assert caught.value.problems[0].msg == "That earlier version is no longer valid."
    assert [tuple(p.loc) for p in caught.value.problems[1:]] == [("other",)]
    assert (await store.document())["yaml_text"] == "other:\n  limit: 3\n"


async def test_a_database_restored_to_an_older_copy_is_read_again(db_with_migrations: DatabasePool) -> None:
    """A restore can move the generation back; a snapshot newer than the database must not outlive it."""
    store = _store(db_with_migrations)
    await _save(store, V1)
    await _save(store, V2)
    assert (await store.snapshot()).get(SAMPLE).per_tick == 5
    await db_with_migrations.execute(
        "UPDATE settings_documents SET yaml_text = $1, data = $2::jsonb, generation = 2, updated_at = NOW()",
        V1,
        {"sample": {"per_tick": 4}},
    )
    assert (await store.snapshot()).get(SAMPLE).per_tick == 4, "the restored document is what loops read"


async def test_a_stored_value_whose_check_raises_keyerror_does_not_stop_a_read(
    db_with_migrations: DatabasePool,
) -> None:
    await db_with_migrations.execute(
        "UPDATE settings_documents SET data = $1::jsonb, generation = generation + 1",
        {"brittle": {"zone": "Mars/Base", "hours": 2.0}},
    )
    store = _store(db_with_migrations, Catalog((BRITTLE,)))
    try:
        snapshot = await store.snapshot()
    except Exception as exc:  # noqa: BLE001 - the test reports what escaped
        pytest.fail(f"the first snapshot raised {type(exc).__name__}; the orchestrator would not start")
    brittle = snapshot.get(BRITTLE)
    assert (brittle.zone, brittle.hours) == ("UTC", 2.0), "only the refused key falls back to its default"
