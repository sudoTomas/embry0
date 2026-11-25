"""The custom provider's old defaults, kept once where a deployment relied on them (#173, "Plan" item 5).

The custom provider shipped a $0 price for any model and doubled turn and time budgets, and every background call
to it sent a JSON schema and turned the server's thinking off. Its defaults are now none of that, so at the first start
of the release, every custom model the deployment names in its stored configuration or ran is written into its
overlay at those old defaults, with the old factors: free, its thinking switch as a request extra, held to a call's
schema. Once: the ``imported`` ledger is marked whether or not there was anything to keep.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.agents import catalogue
from embry0.agents.providers import offered_problem
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]

KEPT = {"free": True, "structured_output": "json_schema"}
#: A model a ``background_models`` value names also gets the server's thinking switch every background call sent; no
#: other does, since a model's request extras go with an agent's run too, which never sent it.
KEPT_BACKGROUND = {**KEPT, "request_extras": {"chat_template_kwargs": {"enable_thinking": False}}}


def _store_module() -> Any:
    from embry0.services import provider_catalogue_store

    if not hasattr(provider_catalogue_store.CatalogueStore, "import_custom_defaults"):
        pytest.fail("a start must keep what the deployment relied on of the custom provider's old defaults")
    return provider_catalogue_store


async def _names_everywhere(db: DatabasePool) -> None:
    """A custom model named in each place the import reads."""
    await db.execute(
        "INSERT INTO provider_config (id, model_heavy, fallback_models) VALUES ('default', 'custom/tier', $1::jsonb) "
        "ON CONFLICT (id) DO UPDATE SET model_heavy = EXCLUDED.model_heavy, fallback_models = EXCLUDED.fallback_models",
        ["custom/fallback"],
    )
    await db.execute("UPDATE agent_definitions SET model = 'custom/agent' WHERE type = 'developer'")
    await db.execute(
        "INSERT INTO settings_documents (scope, data) VALUES ('deployment', $1::jsonb) "
        "ON CONFLICT (scope) DO UPDATE SET data = EXCLUDED.data",
        {"background_models": {"slop_judge": "custom/background"}},
    )
    await _traced(db, "custom/traced")
    await db.execute(
        "INSERT INTO jobs (job_id, task, status, pipeline_config) VALUES ('job-2', 't', 'completed', $1::jsonb)",
        {"agent_models_override": {"developer": "custom/job", "qa": "grok-4.7"}},
    )


async def _traced(db: DatabasePool, model: str) -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, task, status) VALUES ('job-1', 't', 'completed') ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result) "
        "VALUES ('trc-' || $1, 'job-1', 'developer', $1, 'success')",
        model,
    )


async def _row(db: DatabasePool) -> Any:
    return await db.fetchrow("SELECT yaml_text, data, imported FROM provider_catalogue WHERE scope = 'deployment'")


async def test_every_custom_model_the_deployment_names_or_ran_is_read(db_with_migrations: DatabasePool) -> None:
    module = _store_module()
    await _names_everywhere(db_with_migrations)
    async with db_with_migrations.transaction() as conn:
        found = await module.read_custom_models(conn)
    assert found.models == [
        "custom/agent",
        "custom/background",
        "custom/fallback",
        "custom/job",
        "custom/tier",
        "custom/traced",
    ], found
    assert found.background == {"custom/background"}, "only a background_models value's model is a background one"


async def test_the_models_it_relied_on_keep_the_old_defaults_once(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    await store.save("providers:\n  custom:\n    enabled: true\n", actor="admin")
    await _names_everywhere(db_with_migrations)
    kept = await store.import_custom_defaults()
    assert len(kept) == 6, kept
    row = await _row(db_with_migrations)
    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
    custom = data["providers"]["custom"]
    assert custom["factors"] == {"turns": 2.0, "timeout": 2.0}, "the old factors are kept"
    assert custom["models"] == {model: KEPT_BACKGROUND if model == "custom/background" else KEPT for model in kept}, (
        "the thinking switch is kept for the background model only: an agent's run on the others keeps the server's"
    )
    assert "custom.relied_on" in row["imported"]
    assert row["yaml_text"].startswith("# Kept once on"), row["yaml_text"][:80]
    sources = [
        r["source"]
        for r in await db_with_migrations.fetch(
            "SELECT source FROM config_revisions WHERE surface = 'provider_catalogue' ORDER BY id"
        )
    ]
    assert sources == ["person", "upgrade"], sources
    audited = await db_with_migrations.fetch(
        "SELECT details FROM audit_log WHERE action = 'provider_catalogue.custom_imported'"
    )
    assert len(audited) == 1 and audited[0]["details"]["count"] == 6, audited
    assert offered_problem("custom/traced") is None, "the store installed what it wrote"

    assert await store.import_custom_defaults() == [], "it runs once"
    assert (
        len(await db_with_migrations.fetch("SELECT 1 FROM config_revisions WHERE surface = 'provider_catalogue'")) == 2
    )


async def test_a_deployment_that_never_used_one_is_left_as_it_is(db_with_migrations: DatabasePool) -> None:
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    assert await store.import_custom_defaults() == []
    row = await _row(db_with_migrations)
    assert (row["yaml_text"], "custom.relied_on" in row["imported"]) == ("", True), "nothing written, the mark set"
    assert (
        await db_with_migrations.fetch("SELECT 1 FROM audit_log WHERE action = 'provider_catalogue.custom_imported'")
        == []
    )


async def test_what_the_deployment_set_itself_is_kept(db_with_migrations: DatabasePool) -> None:
    """Its own factors and a model it already lists stay as it wrote them; only the others are added."""
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    own = (
        "providers:\n  custom:\n    enabled: true\n    factors: {turns: 3.0}\n"
        "    models:\n      custom/tier: {prices: [{in: 0.5, out: 1.0}]}\n"
    )
    await store.save(own, actor="admin")
    await _names_everywhere(db_with_migrations)
    kept = await store.import_custom_defaults()
    assert "custom/tier" not in kept and len(kept) == 5, kept
    data = (await _row(db_with_migrations))["data"]
    data = data if isinstance(data, dict) else json.loads(data)
    custom = data["providers"]["custom"]
    assert custom["factors"] == {"turns": 3.0}, "the deployment's own factors stay"
    assert custom["models"]["custom/tier"] == {"prices": [{"in": 0.5, "out": 1.0}]}
    assert custom["enabled"] is True


async def test_a_default_price_the_deployment_set_prices_them_instead_of_free(db_with_migrations: DatabasePool) -> None:
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    await store.save("providers:\n  custom:\n    default_price: {in: 0.2, out: 0.4}\n", actor="admin")
    await _traced(db_with_migrations, "custom/traced")
    await store.import_custom_defaults()
    data = (await _row(db_with_migrations))["data"]
    data = data if isinstance(data, dict) else json.loads(data)
    entry = data["providers"]["custom"]["models"]["custom/traced"]
    assert "free" not in entry and entry["prices"] == [{"in": 0.2, "out": 0.4}], entry


async def test_an_overlay_the_catalogue_refuses_is_left_and_said_at_error_level(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    await _traced(db_with_migrations, "custom/traced")

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise catalogue.CatalogueInvalid([catalogue.Problem(("providers",), "refused", "refused")])

    monkeypatch.setattr(module, "merge", refuse)
    with capture_logs() as logs:
        assert await store.import_custom_defaults() == []
    said = [(e["event"], e["log_level"]) for e in logs if "custom_import" in e["event"]]
    assert said == [("provider_catalogue_custom_import_refused", "error")], said
    row = await _row(db_with_migrations)
    assert (row["yaml_text"], "custom.relied_on" in row["imported"]) == ("", True)


async def test_once_kept_a_later_start_keeps_nothing_again(db_with_migrations: DatabasePool) -> None:
    """The ledger, not what the overlay holds, says it ran: a deployment that took the kept models out later does not
    get them back at its next start."""
    module = _store_module()
    store = module.CatalogueStore(db_with_migrations)
    await _traced(db_with_migrations, "custom/traced")
    assert await store.import_custom_defaults() == ["custom/traced"]
    await store.save("", actor="admin")  # the deployment clears its overlay
    assert await store.import_custom_defaults() == [], "a later start must not keep them again"
    assert (await _row(db_with_migrations))["yaml_text"] == ""
