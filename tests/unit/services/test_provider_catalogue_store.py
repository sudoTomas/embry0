"""The stored provider catalogue (#173, ADR 0031) against a real Postgres: writes, refusals, history and undo, what
names a model, installing in every process, and a release that moved the seed."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.agents import catalogue, providers
from embry0.agents.catalogue import CatalogueInvalid
from embry0.services.provider_catalogue_store import (
    MAX_TEXT_BYTES,
    CatalogueStore,
    CatalogueTooLarge,
    GenerationMismatch,
    NotUndoable,
    RevisionNotFound,
    held_catalogue,
)
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]

DEEPSEEK_ON = "providers:\n  deepseek:\n    enabled: true\n"
#: DeepSeek on, its flash model's price changed.
DEEPSEEK_CHEAP = DEEPSEEK_ON + "    models:\n      deepseek-flash: {prices: [{in: 0.1, out: 0.2}]}\n"


@pytest.fixture
def store(db_with_migrations: DatabasePool) -> CatalogueStore:
    return CatalogueStore(db_with_migrations)


def _locs(exc: CatalogueInvalid) -> list[tuple[Any, ...]]:
    return [problem.loc for problem in exc.problems]


async def _revisions(db: DatabasePool) -> list[tuple[str, str, str]]:
    rows = await db.fetch(
        "SELECT source, actor, reason FROM config_revisions WHERE surface = 'provider_catalogue' ORDER BY id"
    )
    return [(r["source"], r["actor"], r["reason"]) for r in rows]


# ---- reading and writing ----


async def test_nothing_stored_is_the_shipped_catalogue(store: CatalogueStore) -> None:
    stored = await store.document()
    assert (stored.yaml_text, stored.data) == ("", {}), "migration 152 stores an empty overlay"
    installed = await store.refresh()
    assert installed.document == catalogue.seed().document
    assert providers.enabled_openai_compatible() == (), "the shipped catalogue turns no OpenAI-compatible one on"


async def test_a_write_is_in_force_at_once_and_is_a_revision(store: CatalogueStore, db_with_migrations) -> None:
    before = (await store.document()).generation
    result = await store.save(DEEPSEEK_CHEAP, actor="ops@example.test", reason="cheaper flash")
    assert (result.generation, result.changed) == (before + 1, True) and result.revision_id is not None
    assert "+    enabled: true" in result.diff
    assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"], "not installed by the write"
    assert providers.model_rates("deepseek-flash") == (0.1, 0.2)
    stored = await store.document()
    assert stored.yaml_text == DEEPSEEK_CHEAP and stored.data["providers"]["deepseek"]["enabled"] is True
    assert stored.updated_by == "ops@example.test"
    assert await _revisions(db_with_migrations) == [("person", "ops@example.test", "cheaper flash")]

    again = await store.save(DEEPSEEK_CHEAP, actor="ops@example.test")
    assert (again.generation, again.revision_id, again.changed) == (before + 1, None, False), "the same text again"
    assert len(await _revisions(db_with_migrations)) == 1


async def test_a_comment_is_kept_as_written(store: CatalogueStore) -> None:
    text = "# DeepSeek for the light tier\n" + DEEPSEEK_ON
    await store.save(text, actor="ops@example.test")
    assert (await store.document()).yaml_text == text


async def test_an_empty_text_returns_to_the_shipped_catalogue(store: CatalogueStore) -> None:
    await store.save(DEEPSEEK_ON, actor="ops@example.test")
    await store.save("", actor="ops@example.test")
    assert (await store.document()).data == {}
    assert providers.enabled_openai_compatible() == ()


async def test_a_dry_run_stores_nothing(store: CatalogueStore, db_with_migrations) -> None:
    generation = (await store.document()).generation
    preview = await store.preview(DEEPSEEK_ON)
    assert (preview.generation, preview.changed) == (generation, True) and "deepseek" in preview.diff
    assert (await store.document()).generation == generation and await _revisions(db_with_migrations) == []
    assert providers.enabled_openai_compatible() == ()
    with pytest.raises(CatalogueInvalid):
        await store.preview("providers: {nobody: {enabled: true}}\n")


async def test_if_match_names_the_generation_a_write_replaces(store: CatalogueStore) -> None:
    generation = (await store.document()).generation
    with pytest.raises(GenerationMismatch) as stale:
        await store.save(DEEPSEEK_ON, actor="a", if_generation=generation + 5)
    assert stale.value.current == generation
    with pytest.raises(GenerationMismatch):
        await store.preview(DEEPSEEK_ON, if_generation=generation + 5)
    assert (await store.document()).generation == generation, "a refused write stored something"
    saved = await store.save(DEEPSEEK_ON, actor="a", if_generation={generation + 5, generation})
    assert saved.generation == generation + 1, "If-Match may name several generations"


async def test_a_refused_text_names_every_problem_and_stores_nothing(store: CatalogueStore) -> None:
    generation = (await store.document()).generation
    text = "providers:\n  nobody: {enabled: true}\n  openai: {base_url: https://elsewhere.test}\n"
    with pytest.raises(CatalogueInvalid) as refused:
        await store.save(text, actor="a")
    locs = _locs(refused.value)
    assert ("providers", "nobody") in locs and ("providers", "openai", "base_url") in locs, locs
    assert (await store.document()).generation == generation


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("providers: [", "yaml_syntax"),
        ("- a list\n", "not_a_mapping"),
        ("providers: &a {}\nmore: *a\n", None),
        ("providers:\n  openai: {enabled: true}\n  openai: {enabled: false}\n", None),
        ("a\x00b: 1\n", "nul_character"),
    ],
)
async def test_a_text_that_is_not_a_catalogue_is_refused(store: CatalogueStore, text: str, kind: str | None) -> None:
    with pytest.raises(CatalogueInvalid) as refused:
        await store.save(text, actor="a")
    if kind is not None:
        assert [p.type for p in refused.value.problems] == [kind]
    assert (await store.document()).yaml_text == ""


async def test_a_text_past_the_bound_is_refused(store: CatalogueStore) -> None:
    filler = "#" + "x" * 1000 + "\n"
    text = DEEPSEEK_ON + filler * (MAX_TEXT_BYTES // len(filler) + 1)
    with pytest.raises(CatalogueTooLarge):
        await store.save(text, actor="a")
    fits = DEEPSEEK_ON + filler * ((MAX_TEXT_BYTES - len(DEEPSEEK_ON)) // len(filler))
    assert (await store.save(fits, actor="a")).changed, "a text within the bound is taken"


# ---- what names a model ----


async def _tiers(db: DatabasePool, **values: Any) -> None:
    """Store the provider configuration's row with ``values`` (no row is stored at first)."""
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING")
    for key, value in values.items():
        await db.execute(
            f"UPDATE provider_config SET {key} = $1{'::jsonb' if key == 'fallback_models' else ''} WHERE id = 'default'",
            value,
        )


async def _name_flash_everywhere(db: DatabasePool) -> None:
    await _tiers(db, model_heavy="deepseek-flash", fallback_models=["deepseek-v4-pro"])


async def test_a_change_may_not_take_out_a_model_a_tier_or_the_fallback_names(
    store: CatalogueStore, db_with_migrations
) -> None:
    await store.save(DEEPSEEK_ON, actor="a")
    await _name_flash_everywhere(db_with_migrations)
    generation = (await store.document()).generation
    for text in ("", "providers:\n  deepseek: {enabled: false}\n"):
        with pytest.raises(CatalogueInvalid) as refused:
            await store.save(text, actor="a")
        assert sorted(_locs(refused.value)) == [
            ("references", "fallback_models", 0),
            ("references", "tiers", "model_heavy"),
        ], f"turning DeepSeek off with {text!r}"
        assert {p.type for p in refused.value.problems} == {"in_use"}
        assert all("deepseek" not in p.msg for p in refused.value.problems), "a problem repeated the model"
    with pytest.raises(CatalogueInvalid) as refused:
        await store.save(DEEPSEEK_ON + "    models: {deepseek-flash: null}\n", actor="a")
    assert _locs(refused.value) == [("references", "tiers", "model_heavy")], "taking the model out"
    assert (await store.document()).generation == generation
    assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"]


async def test_a_change_may_not_take_out_a_model_an_agent_or_a_background_feature_names(
    store: CatalogueStore, db_with_migrations
) -> None:
    await store.save(DEEPSEEK_ON, actor="a")
    await db_with_migrations.execute("UPDATE agent_definitions SET model = 'deepseek-flash' WHERE type = 'developer'")
    await db_with_migrations.execute(
        "INSERT INTO settings_documents (scope, yaml_text, data) VALUES "
        "('deployment', '', '{\"background_models\": {\"watcher\": \"deepseek-v4-pro\"}}'::jsonb) "
        "ON CONFLICT (scope) DO UPDATE SET data = EXCLUDED.data"
    )
    with pytest.raises(CatalogueInvalid) as refused:
        await store.save("", actor="a")
    assert sorted(_locs(refused.value)) == [
        ("references", "agent_definitions", "developer"),
        ("references", "background_models", "deployment", "watcher"),
    ]


async def test_a_model_nothing_could_run_before_does_not_hold_a_change_up(
    store: CatalogueStore, db_with_migrations
) -> None:
    """A tier naming a model the catalogue does not offer now is not this change's doing."""
    await _tiers(db_with_migrations, model_heavy="deepseek-flash")
    assert (await store.save(DEEPSEEK_ON, actor="a")).revision_id is not None, "turning DeepSeek on was held up"
    await _tiers(db_with_migrations, model_heavy="gone-model")
    assert (await store.save("", actor="a")).revision_id is not None, "a model nobody offers held a change up"


async def test_a_change_waits_for_a_write_naming_a_model_and_sees_it(store: CatalogueStore, db_with_migrations) -> None:
    """Of a write naming a model and a change taking it out, one waits for the other (CatalogueStore.held)."""
    await store.save(DEEPSEEK_ON, actor="a")
    other = CatalogueStore(db_with_migrations)
    async with other.held() as held:
        assert held.enabled("deepseek")
        change = asyncio.create_task(store.save("", actor="b"))
        await _until_waiting(db_with_migrations)
        assert not change.done(), "the change did not wait for the held catalogue"
        await _tiers(db_with_migrations, model_heavy="deepseek-flash")
    with pytest.raises(CatalogueInvalid) as refused:
        await asyncio.wait_for(change, 30)
    assert _locs(refused.value) == [("references", "tiers", "model_heavy")], "the change missed the tier"


async def test_a_settings_write_is_checked_against_the_stored_catalogue(db_with_migrations) -> None:
    """``held_catalogue`` is what a settings write checks ``background_models`` against: the stored catalogue, not
    the one this process installed."""
    writer = CatalogueStore(db_with_migrations)
    await writer.save(DEEPSEEK_ON, actor="a")
    catalogue.install(None)  # another process: it has not looked yet
    assert providers.enabled_openai_compatible() == ()
    async with db_with_migrations.transaction() as conn:
        held = await held_catalogue(conn)
    assert held.enabled("deepseek")


async def _until_waiting(db: DatabasePool) -> None:
    for _ in range(300):
        waiting = await db.fetchval(
            "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
            "WHERE NOT l.granted AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        )
        tuple_waits = await db.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        if waiting or tuple_waits:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the change never waited on a lock")


async def test_a_change_waits_for_a_settings_write_naming_a_model_and_sees_it(
    store: CatalogueStore, db_with_migrations
) -> None:
    """A settings write reads the catalogue FOR SHARE on its own transaction (held_catalogue): a change of the
    catalogue waits for it, then reads the value it wrote."""
    await store.save(DEEPSEEK_ON, actor="a")
    async with db_with_migrations.transaction() as conn:
        assert (await held_catalogue(conn)).enabled("deepseek")
        change = asyncio.create_task(store.save("", actor="b"))
        await _until_waiting(db_with_migrations)
        assert not change.done(), "the change did not wait for the settings write"
        await conn.execute(
            "INSERT INTO settings_documents (scope, yaml_text, data) VALUES "
            "('deployment', '', '{\"background_models\": {\"watcher\": \"deepseek-flash\"}}'::jsonb) "
            "ON CONFLICT (scope) DO UPDATE SET data = EXCLUDED.data"
        )
    with pytest.raises(CatalogueInvalid) as refused:
        await asyncio.wait_for(change, 30)
    assert _locs(refused.value) == [("references", "background_models", "deployment", "watcher")]


# ---- history and undo ----


async def test_undo_puts_back_what_the_newest_change_replaced(store: CatalogueStore, db_with_migrations) -> None:
    await store.save(DEEPSEEK_ON, actor="a")
    second = await store.save(DEEPSEEK_CHEAP, actor="a")
    assert second.revision_id is not None
    undone = await store.undo(second.revision_id, actor="b")
    assert (await store.document()).yaml_text == DEEPSEEK_ON
    assert providers.model_rates("deepseek-flash") == (0.30, 1.20), "the undo was not installed"
    assert undone.revision_id is not None and undone.changed
    assert (await _revisions(db_with_migrations))[-1] == ("restore", "b", f"undo of change {second.revision_id}")


async def test_only_the_newest_change_while_it_is_still_stored_can_be_undone(store: CatalogueStore) -> None:
    first = await store.save(DEEPSEEK_ON, actor="a")
    second = await store.save(DEEPSEEK_CHEAP, actor="a")
    assert first.revision_id is not None and second.revision_id is not None
    with pytest.raises(NotUndoable):
        await store.undo(first.revision_id, actor="b")
    with pytest.raises(RevisionNotFound):
        await store.undo(second.revision_id + 1000, actor="b")
    undone = await store.undo(second.revision_id, actor="b")
    with pytest.raises(NotUndoable):
        await store.undo(second.revision_id, actor="b")
    assert undone.revision_id is not None


async def test_an_undo_is_checked_as_a_write_is(store: CatalogueStore, db_with_migrations) -> None:
    first = await store.save(DEEPSEEK_ON, actor="a")
    assert first.revision_id is not None
    await _tiers(db_with_migrations, model_heavy="deepseek-flash")
    with pytest.raises(CatalogueInvalid) as refused:
        await store.undo(first.revision_id, actor="b")
    assert _locs(refused.value) == [("references", "tiers", "model_heavy")]
    assert (await store.document()).yaml_text == DEEPSEEK_ON


async def test_revisions_are_the_catalogues_own(store: CatalogueStore, db_with_migrations) -> None:
    await db_with_migrations.execute(
        "INSERT INTO config_revisions (scope, surface, before, after, actor, source) "
        "VALUES ('deployment', 'settings', '{}'::jsonb, '{}'::jsonb, 'x', 'person')"
    )
    settings_id = await db_with_migrations.fetchval("SELECT max(id) FROM config_revisions")
    saved = await store.save(DEEPSEEK_ON, actor="a")
    assert [r["id"] for r in await store.revisions()] == [saved.revision_id]
    assert await store.revision(settings_id) is None, "a settings revision read as the catalogue's"
    with pytest.raises(RevisionNotFound):
        await store.undo(settings_id, actor="b")


# ---- every process ----


async def test_another_process_installs_a_write_on_its_next_look(db_with_migrations) -> None:
    writer, reader = CatalogueStore(db_with_migrations), CatalogueStore(db_with_migrations)
    await reader.refresh()
    await writer.save(DEEPSEEK_ON, actor="a")
    catalogue.install(None)  # the reader's process has the shipped one
    await reader.refresh()
    assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"]


# ---- a release that moved the seed (the design's "Seed drift") ----


#: What an earlier release stored: OpenAI on with gpt-6.1-sol taken out, Groq on, the custom provider on at a price.
DRIFTED = {
    "providers": {
        "openai": {"enabled": True, "models": {"gpt-6.1-sol": None}},
        "groq": {"enabled": True},
        "custom": {"enabled": True, "default_price": {"in": 1.0, "out": 2.0}},
    }
}


@pytest.fixture
def later_release(monkeypatch: pytest.MonkeyPatch) -> catalogue.Catalogue:
    """This process runs a later release, whose seed has no gpt-6.1-sol and no Groq."""
    from embry0.services import provider_catalogue_store

    data = catalogue.seed().document.model_dump(mode="json", by_alias=True)
    del data["providers"]["openai"]["models"]["gpt-6.1-sol"]
    del data["providers"]["groq"]
    later = catalogue.Catalogue.build(catalogue.parse(data))
    monkeypatch.setattr(catalogue, "seed", lambda: later)
    monkeypatch.setattr(provider_catalogue_store, "seed", lambda: later)
    return later


def _metrics() -> Any:
    from prometheus_client import CollectorRegistry

    from embry0.metrics.loop import LoopMetrics

    return LoopMetrics(CollectorRegistry())


def _refused_parts(metrics: Any) -> float | None:
    from embry0.metrics.loop import sample

    return sample(metrics.registry, "embry0_provider_catalogue_refused_parts")


async def _store_directly(db: DatabasePool, data: dict[str, Any]) -> int:
    """``data`` stored as another release, or a hand at the database, would: no check."""
    return int(
        await db.fetchval(
            "UPDATE provider_catalogue SET data = $1::jsonb, generation = generation + 1 RETURNING generation", data
        )
    )


async def _refusal_audits(db: DatabasePool) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT details FROM audit_log WHERE action = 'provider_catalogue.refused' ORDER BY id")
    return [r["details"] if isinstance(r["details"], dict) else json.loads(r["details"]) for r in rows]


async def test_at_the_start_every_part_that_applies_is_in_force_and_the_rest_reported(
    db_with_migrations: DatabasePool, later_release: catalogue.Catalogue
) -> None:
    """A release that dropped a model the overlay took out, and a provider it turned on, turns off nothing else."""
    generation = await _store_directly(db_with_migrations, DRIFTED)
    metrics = _metrics()
    assert _refused_parts(metrics) == 0.0, "seeded 0, so an alert on it can be told from one that never fires"
    store = CatalogueStore(db_with_migrations, metrics=metrics)
    with capture_logs() as logs:
        await store.refresh()
        await store.refresh()
    assert [p.name for p in providers.enabled_openai_compatible()] == ["openai", "custom"], "turned off"
    assert providers.model_rates("custom/llama3") == (1.0, 2.0), "the custom provider's price was dropped"
    assert [(p.loc, p.type) for p in store.refused] == [(("providers", "groq"), "not_shipped")]
    assert (store.in_force, store.in_force_generation) == ("in_part", generation)
    assert _refused_parts(metrics) == 1.0
    assert [e["event"] for e in logs].count("provider_catalogue_refused") == 1, "once a generation"
    audits = await _refusal_audits(db_with_migrations)
    assert audits == [
        {
            "generation": generation,
            "in_force": "in_part",
            "in_force_generation": generation,
            "parts": ["providers.groq"],
            "count": 1,
        }
    ], "audited once a generation"


async def test_a_running_process_keeps_its_catalogue_when_a_later_overlay_is_refused_in_part(
    store: CatalogueStore, db_with_migrations: DatabasePool
) -> None:
    metrics = _metrics()
    store.metrics = metrics
    first = (await store.save(DEEPSEEK_CHEAP, actor="a")).generation
    later = await _store_directly(db_with_migrations, {"providers": {"retired": {"enabled": True}}})
    await store.refresh()
    assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"], "it dropped what it ran on"
    assert providers.model_rates("deepseek-flash") == (0.1, 0.2)
    assert (store.in_force, store.in_force_generation) == ("earlier", first)
    assert [p.loc for p in store.refused] == [("providers", "retired")]
    assert _refused_parts(metrics) == 1.0
    assert [(a["generation"], a["in_force"]) for a in await _refusal_audits(db_with_migrations)] == [(later, "earlier")]
    fixed = await store.save("providers:\n  groq: {enabled: true}\n", actor="a")
    assert (store.in_force, store.in_force_generation, store.refused) == ("whole", fixed.generation, [])
    assert _refused_parts(metrics) == 0.0, "a fix clears it"
    assert [p.name for p in providers.enabled_openai_compatible()] == ["groq"]


async def test_a_write_is_checked_whole_however_the_stored_one_is_read(
    db_with_migrations: DatabasePool, later_release: catalogue.Catalogue
) -> None:
    """The release refuses a write of the drifted text, naming each part, so a person fixes it."""
    store = CatalogueStore(db_with_migrations)
    await _store_directly(db_with_migrations, DRIFTED)
    await store.refresh()
    text = "providers:\n  openai: {enabled: true, models: {gpt-6.1-sol: null}}\n  groq: {enabled: true}\n"
    with pytest.raises(CatalogueInvalid) as refused:
        await store.save(text, actor="a")
    assert sorted(p.type for p in refused.value.problems) == ["not_found", "not_shipped"]


def _drop_from_the_seed(monkeypatch: pytest.MonkeyPatch, provider: str, model: str) -> None:
    """From here on this process runs a later release, whose seed no longer has ``model``."""
    from embry0.services import provider_catalogue_store

    data = catalogue.seed().document.model_dump(mode="json", by_alias=True)
    del data["providers"][provider]["models"][model]
    later = catalogue.Catalogue.build(catalogue.parse(data))
    monkeypatch.setattr(catalogue, "seed", lambda: later)
    monkeypatch.setattr(provider_catalogue_store, "seed", lambda: later)


SOL_REPRICED = (
    "providers:\n  openai:\n    enabled: true\n    models:\n      gpt-6.1-sol: {prices: [{in: 3.0, out: 12.0}]}\n"
)


async def test_a_model_a_release_drops_stays_as_it_was_shipped_at_the_deployment_s_price(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gpt-6.1-sol is chat-only as shipped; the deployment re-priced it; a later release drops it."""
    await CatalogueStore(db_with_migrations).save(SOL_REPRICED, actor="a")
    shipped = await db_with_migrations.fetchval("SELECT shipped FROM provider_catalogue")
    shipped = json.loads(shipped) if isinstance(shipped, str) else shipped
    assert shipped["openai"]["gpt-6.1-sol"]["chat_only"] is True, "the write recorded what it was shipped with"
    _drop_from_the_seed(monkeypatch, "openai", "gpt-6.1-sol")
    catalogue.install(None)
    store = CatalogueStore(db_with_migrations)
    await store.refresh()
    assert (store.in_force, store.refused) == ("whole", [])
    assert [p.loc for p in store.notes] == [("providers", "openai", "models", "gpt-6.1-sol")], "kept, unnamed"
    assert providers.model_rates("gpt-6.1-sol") == (3.0, 12.0)
    assert providers.chat_only_models(["gpt-6.1-sol"]) == ["gpt-6.1-sol"], "agents would send a chat-only model tools"
    # Written again, with the drift in place: the record goes with it.
    again = await store.save(SOL_REPRICED + "# reviewed\n", actor="a")
    assert again.changed
    shipped = await db_with_migrations.fetchval("SELECT shipped FROM provider_catalogue")
    shipped = json.loads(shipped) if isinstance(shipped, str) else shipped
    assert shipped["openai"]["gpt-6.1-sol"]["chat_only"] is True, "a write after the drift lost the record"
    assert providers.chat_only_models(["gpt-6.1-sol"]) == ["gpt-6.1-sol"]


async def test_a_dated_price_is_stored_and_read_back_by_another_process(
    store: CatalogueStore, db_with_migrations: DatabasePool
) -> None:
    from datetime import UTC, datetime

    text = DEEPSEEK_ON + (
        "    models:\n      deepseek-flash: {prices: [{in: 1.0, out: 2.0}, {from: 2027-01-01, in: 9.0, out: 9.0}]}\n"
    )
    saved = await store.save(text, actor="a")
    assert saved.revision_id is not None
    catalogue.install(None)
    reader = CatalogueStore(db_with_migrations)
    installed = await reader.refresh()
    assert (reader.in_force, reader.refused) == ("whole", []), "the dated price was refused on the way back"
    assert installed.rates("deepseek-flash", at=datetime(2026, 12, 31, tzinfo=UTC)) == (1.0, 2.0)
    assert installed.rates("deepseek-flash", at=datetime(2027, 1, 1, tzinfo=UTC)) == (9.0, 9.0)
    revision = await reader.revision(saved.revision_id)
    assert (
        revision is not None
        and revision["after"]["data"]["providers"]["deepseek"]["models"]["deepseek-flash"]["prices"][1]["from"]
        == "2027-01-01"
    )
    undone = await reader.undo(saved.revision_id, actor="b")
    assert undone.changed and providers.enabled_openai_compatible() == ()


async def test_a_price_the_release_refuses_leaves_the_model_unpriced_in_force(
    store: CatalogueStore, db_with_migrations: DatabasePool
) -> None:
    """Stored by another hand with a price this release refuses: the model does not run at the seed's price."""
    await _store_directly(
        db_with_migrations,
        {
            "providers": {
                "deepseek": {"enabled": True, "models": {"deepseek-flash": {"prices": [{"in": -1, "out": 1}]}}}
            }
        },
    )
    await store.refresh()
    assert store.in_force == "in_part"
    assert ("providers", "deepseek", "models", "deepseek-flash") in [
        p.loc for p in store.refused if p.type == "withheld"
    ]
    assert providers.model_rates("deepseek-flash") is None, "it ran at the seed's price in place of the deployment's"
    assert providers.unpriced_models(["deepseek-flash"]) == ["deepseek-flash"]
    assert providers.model_rates("deepseek-v4-pro") == (1.32, 3.96), "DeepSeek's other models stay in service"


async def _recorded(db: DatabasePool) -> dict[str, Any]:
    value = await db.fetchval("SELECT shipped FROM provider_catalogue")
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.parametrize("taken_out", ["after the drop", "before the drop"])
async def test_an_undo_brings_a_dropped_model_back_as_it_was_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, taken_out: str
) -> None:
    """Re-price chat-only gpt-6.1-sol; a release drops it; take the override out; undo that: it comes back
    chat-only, named in notes, its record with it, whichever came first of the drop and the taking out."""
    db = db_with_migrations
    await CatalogueStore(db).save(SOL_REPRICED, actor="a")
    if taken_out == "before the drop":
        took_out = await CatalogueStore(db).save("providers:\n  openai:\n    enabled: true\n", actor="a")
    _drop_from_the_seed(monkeypatch, "openai", "gpt-6.1-sol")
    catalogue.install(None)
    store = CatalogueStore(db)
    await store.refresh()
    if taken_out == "after the drop":
        assert providers.chat_only_models(["gpt-6.1-sol"]) == ["gpt-6.1-sol"]
        took_out = await store.save("providers:\n  openai:\n    enabled: true\n", actor="a")
        assert "gpt-6.1-sol" in (await _recorded(db)).get("openai", {}), "the record of a dropped model was lost"
    assert took_out.revision_id is not None
    undone = await store.undo(took_out.revision_id, actor="b")
    assert undone.changed
    assert providers.model_rates("gpt-6.1-sol") == (3.0, 12.0)
    assert providers.chat_only_models(["gpt-6.1-sol"]) == ["gpt-6.1-sol"], "agents would send a chat-only model tools"
    assert [p.loc for p in store.notes] == [("providers", "openai", "models", "gpt-6.1-sol")], "kept, unnamed"
    assert (await _recorded(db))["openai"]["gpt-6.1-sol"]["chat_only"] is True, "the undo did not put the record back"
