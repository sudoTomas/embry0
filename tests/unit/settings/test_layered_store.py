"""Settings layers in the store: an organization's and a repository's own documents (spec 2026-10-29, #161)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.settings.document import DocumentInvalid, load_yaml
from embry0.settings.scope import Scope
from embry0.settings.store import GenerationMismatch, SettingsStore, scoped_reader
from embry0.storage import repo_renames
from embry0.storage.database import DatabasePool
from tests.fixtures.settings_sections import LAYERED_CATALOG, OTHER, SAMPLE, WINDOW

pytestmark = pytest.mark.requires_postgres

ACME = Scope.organization("acme")
WIDGETS = Scope.repository("acme/widgets")


def _store(db: DatabasePool) -> SettingsStore:
    return SettingsStore(db, LAYERED_CATALOG)


async def _save(store: SettingsStore, text: str, scope: Scope | None = None, **kwargs: Any) -> Any:
    kwargs.setdefault("actor", "alex@example.com")
    return await store.save(text, load_yaml(text), scope=scope, **kwargs)


async def test_an_organization_override_changes_only_its_own_repositories(db_with_migrations: DatabasePool) -> None:
    """#161's acceptance: an organization's value is read for its repositories, and only for them."""
    store = _store(db_with_migrations)
    await _save(store, "sample:\n  per_tick: 4\n")
    await _save(store, "sample:\n  per_tick: 6\n", ACME)
    await _save(store, "sample:\n  mode: 'on'\n", WIDGETS)
    await _save(store, "sample:\n  remind_hours: 2\n", Scope.organization("group"))
    snapshot = await store.snapshot()
    read = {
        "acme/widgets": snapshot.get(SAMPLE, repo="acme/widgets"),
        "acme/gizmos": snapshot.get(SAMPLE, repo="acme/gizmos"),
        "globex/gadgets": snapshot.get(SAMPLE, repo="globex/gadgets"),
        "deployment": snapshot.get(SAMPLE),
        "no repository": snapshot.get(SAMPLE, repo=None),
        "owner acme": snapshot.get(SAMPLE, owner="acme"),
    }
    seen = {name: (s.per_tick, s.mode) for name, s in read.items()}
    assert seen == {
        "acme/widgets": (6, "on"),
        "acme/gizmos": (6, "shadow"),
        "globex/gadgets": (4, "shadow"),
        "deployment": (4, "shadow"),
        "no repository": (4, "shadow"),
        "owner acme": (6, "shadow"),
    }, "an organization's value reaches its own repositories and no other"
    assert snapshot.get(SAMPLE, repo="group/sub/project").remind_hours == 2.0, "a subgroup's project is its group's"
    assert snapshot.get(OTHER, repo="acme/widgets").limit == 5, "a section no layer may set reads the deployment's"


async def test_a_loop_reads_every_repository_from_one_query(db_with_migrations: DatabasePool, monkeypatch) -> None:
    await _save(_store(db_with_migrations), "sample:\n  per_tick: 6\n", ACME)
    store = _store(db_with_migrations)  # another process: it has read nothing yet
    calls = {"token": 0, "all": 0}
    for name in calls:
        original = getattr(store._documents, name)

        async def counted(*args: Any, _name: str = name, _original: Any = original) -> Any:
            calls[_name] += 1
            return await _original(*args)

        monkeypatch.setattr(store._documents, name, counted)
    scoped = await scoped_reader(store, SAMPLE)()
    ticks = [scoped.repo(f"acme/repo-{n}").per_tick for n in range(1000)]
    assert ticks == [6] * 1000
    assert calls == {"token": 1, "all": 1}, f"one tick over 1,000 repositories read the database {calls}"
    snapshot = scoped.snapshot
    assert snapshot.resolve(SAMPLE, repo="acme/repo-1") is snapshot.resolve(SAMPLE, repo="acme/repo-2"), (
        "a repository with no layer of its own shares its organization's resolution"
    )


async def test_another_process_sees_a_layer_write_on_its_next_snapshot(db_with_migrations: DatabasePool) -> None:
    writer, reader = _store(db_with_migrations), _store(db_with_migrations)
    assert (await reader.snapshot()).get(SAMPLE, repo="acme/widgets").mode == "shadow"
    first = await reader.snapshot()
    await _save(writer, "sample:\n  mode: 'on'\n", WIDGETS)
    second = await reader.snapshot()
    assert second is not first and second.get(SAMPLE, repo="acme/widgets").mode == "on"
    assert await reader.snapshot() is second, "nothing moved, so nothing is read again"


async def test_a_layer_starts_at_generation_zero_and_is_created_by_its_first_change(
    db_with_migrations: DatabasePool,
) -> None:
    store = _store(db_with_migrations)
    assert (await store.document(ACME))["generation"] == 0
    await store.preview("sample:\n  per_tick: 6\n", {"sample": {"per_tick": 6}}, scope=ACME)
    nothing = await _save(store, "", ACME)
    assert nothing.revision_id is None
    count = "SELECT count(*) FROM settings_documents WHERE scope = 'organization:acme'"
    assert await db_with_migrations.fetchval(count) == 0, "a dry run or an unchanged write leaves no row behind"
    first = await _save(store, "sample:\n  per_tick: 6\n", ACME, if_generation=0)
    assert first.generation == 1
    with pytest.raises(GenerationMismatch):
        await _save(store, "sample:\n  per_tick: 7\n", ACME, if_generation=0)
    deployment = await store.document()
    assert deployment["generation"] == 1, "a layer's write leaves the deployment's generation, and ETag, alone"


async def test_revisions_and_undo_belong_to_one_document(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, "sample:\n  per_tick: 4\n")
    await _save(store, "# acme\nsample:\n  per_tick: 6\n", ACME)
    second = await _save(store, "# acme again\nsample:\n  per_tick: 7\n", ACME)
    assert [r["scope"] for r in await store.revisions(scope=ACME)] == ["organization:acme"] * 2
    assert [r["scope"] for r in await store.revisions()] == ["deployment"]
    await store.undo(second.revision_id, actor="sam@example.com")
    assert (await store.document(ACME))["yaml_text"] == "# acme\nsample:\n  per_tick: 6\n"
    assert (await store.document())["yaml_text"] == "sample:\n  per_tick: 4\n", "the deployment's document is untouched"
    newest = (await store.revisions(scope=ACME))[0]
    assert (newest["source"], newest["scope"]) == ("restore", "organization:acme")


async def test_a_layer_is_refused_where_it_conflicts_with_what_it_inherits(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, "window:\n  high: 50\n")
    with pytest.raises(DocumentInvalid) as caught:
        await _save(store, "window:\n  low: 70\n", ACME)
    assert [(list(p.loc), p.type) for p in caught.value.problems] == [(["window"], "conflicts_with_inherited")]
    assert (await store.document(ACME))["generation"] == 0, "nothing was stored"


async def test_a_value_that_starts_to_conflict_reads_as_inherited_with_one_warning(
    db_with_migrations: DatabasePool,
) -> None:
    store = _store(db_with_migrations)
    await _save(store, "window:\n  high: 80\n")
    await _save(store, "window:\n  low: 70\n", ACME)
    assert (await store.snapshot()).get(WINDOW, owner="acme").low == 70
    lowered = await _save(store, "window:\n  high: 50\n")
    assert lowered.layers_refused == [
        {
            "scope": {"layer": "organization", "owner": "acme", "repo": None},
            "key": "window.low",
            "reason": "high must not be below low",
        }
    ], "the deployment write names the layer value it makes read as inherited"
    assert lowered.layers_refused_total == 1
    loop = _store(db_with_migrations)  # a loop in another process reads it on its next tick
    with capture_logs() as logs:
        snapshot = await loop.snapshot()
        reads = [snapshot.get(WINDOW, repo=f"acme/repo-{n}") for n in range(3)]
    assert [(w.low, w.high) for w in reads] == [(10, 50)] * 3, "the refused low reads as inherited, the tick goes on"
    warnings = [log for log in logs if log["event"] == "settings_layer_value_refused"]
    assert len(warnings) == 1, f"one warning per snapshot, not one per read: {warnings}"
    assert (warnings[0]["scope"], warnings[0]["key"]) == ("organization:acme", "low")


async def test_a_layer_change_is_described_with_what_it_inherits(db_with_migrations: DatabasePool) -> None:
    store = _store(db_with_migrations)
    await _save(store, "sample:\n  per_tick: 4\n")
    result = await _save(store, "sample:\n  per_tick: 6\n", ACME)
    assert result.changes == [
        {
            "key": "sample.per_tick",
            "before": None,
            "after": 6,
            "set_before": False,
            "set_after": True,
            "effective_before": 4,
            "effective_after": 6,
        }
    ], "before the organization set it, it read the deployment's 4, not the default 3"


async def test_a_layer_under_a_renamed_slug_applies_to_the_new_one(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(db_with_migrations)
    await _save(store, "sample:\n  mode: 'on'\n", Scope.repository("acme/old-widgets"))
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "acme/widgets"})
    fresh = _store(db_with_migrations)
    assert (await fresh.snapshot()).get(SAMPLE, repo="acme/widgets").mode == "on"
    assert (await fresh.snapshot()).get(SAMPLE, repo="acme/old-widgets").mode == "on"


OLD_LAYER = "# kept for the old slug\nsample:\n  mode: 'on'\nwindow:\n  high: 70\n"


async def test_a_renamed_repository_s_layer_moves_to_its_new_slug_on_its_next_write(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opened under the old slug, saved under the new one with its history, so it outlives the rename entry."""
    store = _store(db_with_migrations)
    first = await _save(store, OLD_LAYER, Scope.repository("acme/old-widgets"))
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "acme/widgets"})
    current = Scope.repository("acme/widgets")
    opened = await store.document(current)
    assert (opened["yaml_text"], opened["generation"]) == (OLD_LAYER, first.generation), (
        "the renamed repository's layer opened empty, so a save would start a new one beside it"
    )
    edited = OLD_LAYER.replace("high: 70", "high: 60")
    second = await _save(store, edited, current, if_generation=opened["generation"])
    rows = await db_with_migrations.fetch(
        "SELECT scope, generation FROM settings_documents WHERE scope LIKE 'repository:%'"
    )
    assert [(r["scope"], r["generation"]) for r in rows] == [("repository:acme/widgets", first.generation + 1)], (
        "the layer stayed under the old slug, so removing the rename entry would silently drop it"
    )
    history = await store.revisions(scope=current)
    assert [(r["id"], r["source"]) for r in history] == [
        (second.revision_id, "person"),
        (history[1]["id"], "upgrade"),
        (first.revision_id, "person"),
    ], "the history moves with the layer, and the move is recorded between the two changes"
    assert history[1]["reason"] == "moved from repository:acme/old-widgets: REPO_RENAMES maps it to acme/widgets"
    assert history[1]["before"]["yaml_text"] == history[1]["after"]["yaml_text"] == OLD_LAYER
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {})
    snapshot = await _store(db_with_migrations).snapshot()
    assert (snapshot.get(SAMPLE, repo="acme/widgets").mode, snapshot.get(WINDOW, repo="acme/widgets").high) == (
        "on",
        60,
    ), "with the rename entry gone, the repository's layer stopped applying"
    await store.undo(second.revision_id, actor="sam@example.com")
    assert (await store.document(current))["yaml_text"] == OLD_LAYER


async def test_a_dry_run_through_the_new_slug_moves_nothing(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(db_with_migrations)
    await _save(store, OLD_LAYER, Scope.repository("acme/old-widgets"))
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "acme/widgets"})
    await store.preview(OLD_LAYER, load_yaml(OLD_LAYER), scope=Scope.repository("acme/widgets"))
    unchanged = await _save(store, OLD_LAYER, Scope.repository("acme/widgets"))
    assert unchanged.revision_id is None
    rows = await db_with_migrations.fetch("SELECT scope FROM settings_documents WHERE scope LIKE 'repository:%'")
    assert [r["scope"] for r in rows] == ["repository:acme/old-widgets"], "only a change moves the layer"


async def test_a_layer_write_waits_for_a_write_to_what_it_inherits(db_with_migrations: DatabasePool) -> None:
    """The check against inherited values reads them under the lock, after a concurrent deployment write commits."""
    deployment_writer, layer_writer = _store(db_with_migrations), _store(db_with_migrations)
    await _save(deployment_writer, "window:\n  high: 80\n")
    await layer_writer.snapshot()  # cached with high 80, as a busy process would hold it
    async with deployment_writer.edit(actor="alex@example.com", source="person") as draft:
        draft.replace("window:\n  high: 50\n", {"window": {"high": 50}}, 1)
        pending = asyncio.create_task(_save(layer_writer, "window:\n  low: 70\n", ACME))
        await asyncio.sleep(0.5)
        assert not pending.done(), "the layer write did not wait for the deployment write it is checked against"
    with pytest.raises(DocumentInvalid) as caught:
        await pending
    assert [p.type for p in caught.value.problems] == ["conflicts_with_inherited"]
    assert (await layer_writer.document(ACME))["generation"] == 0, "a low above the new high was stored"


async def test_a_layer_kept_in_a_file_logs_no_ignored_key(db_with_migrations: DatabasePool) -> None:
    """``managed_by`` is a key of the document, not a section: a layer holding it sets nothing it may not."""
    await _save(_store(db_with_migrations), "managed_by: file\nsample:\n  per_tick: 6\n", ACME)
    with capture_logs() as logs:
        snapshot = await _store(db_with_migrations).snapshot()
    ignored = [log for log in logs if log["event"] == "settings_layer_keys_ignored"]
    assert ignored == [], f"every rebuild would log the layer's managed_by as a key it may not set: {ignored}"
    assert snapshot.get(SAMPLE, owner="acme").per_tick == 6
