"""The provider catalogue API (#173, ADR 0031) against a real Postgres: read, write, dry run, If-Match, refusals,
revisions and undo, and who may do each."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.agents import catalogue
from embry0.storage.database import DatabasePool
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = [pytest.mark.requires_postgres]

CATALOG = "/api/v1/config/providers/catalog"
YAML = {"Content-Type": "application/yaml"}
DEEPSEEK_ON = "# DeepSeek for the light tier\nproviders:\n  deepseek:\n    enabled: true\n"
DEEPSEEK_CHEAP = DEEPSEEK_ON + "    models:\n      deepseek-flash: {prices: [{in: 0.1, out: 0.2}]}\n"


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    yield api_client


def _db(client: AsyncClient) -> DatabasePool:
    return client.app.state.db  # type: ignore[attr-defined,no-any-return]


async def _audits(client: AsyncClient) -> list[tuple[str, dict[str, Any]]]:
    rows = await _db(client).fetch(
        "SELECT action, details FROM audit_log WHERE action LIKE 'provider_catalogue.%' ORDER BY id"
    )
    return [(r["action"], r["details"] if isinstance(r["details"], dict) else json.loads(r["details"])) for r in rows]


async def _put(client: AsyncClient, text: str | bytes, **headers: str) -> Any:
    body = text.encode("utf-8") if isinstance(text, str) else text
    return await client.put(CATALOG, content=body, headers={**YAML, **headers})


# ---- reading ----


async def test_with_nothing_stored_the_catalogue_is_the_shipped_one(client: AsyncClient) -> None:
    resp = await client.get(CATALOG)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["yaml_text"], body["data"], body["refused"], body["notes"], body["in_force"]) == (
        "",
        {},
        [],
        [],
        "whole",
    )
    assert resp.headers["etag"] == f'"{body["generation"]}"'
    assert body["catalogue"] == catalogue.seed().document.model_dump(mode="json", by_alias=True)
    assert body["catalogue"]["providers"]["deepseek"]["enabled"] is False
    shipped = await client.get(f"{CATALOG}/shipped")
    assert shipped.status_code == 200 and "deepseek:" in shipped.text
    assert shipped.headers["content-type"].startswith("application/yaml")


async def test_a_read_answers_what_another_process_wrote(client: AsyncClient) -> None:
    """The catalogue in force and the stored overlay are one answer, even before this process's next look."""
    from embry0.services.provider_catalogue_store import CatalogueStore

    await CatalogueStore(_db(client)).save(DEEPSEEK_ON, actor="other")
    catalogue.install(None)  # this process has not looked since
    body = (await client.get(CATALOG)).json()
    assert body["yaml_text"] == DEEPSEEK_ON
    assert body["catalogue"]["providers"]["deepseek"]["enabled"] is True, "the catalogue in force was not the stored"


async def test_a_read_carries_the_shipped_catalogue_beside_the_one_in_force(client: AsyncClient) -> None:
    """A client tells what the overlay changes, and puts a field back, from the catalogue embry0 ships (#173, the
    Providers page)."""
    from embry0.services.provider_catalogue_store import CatalogueStore

    await CatalogueStore(_db(client)).save(DEEPSEEK_CHEAP, actor="ops@example.com")
    body = (await client.get(CATALOG)).json()
    assert "shipped" in body, "a read must carry the shipped catalogue"
    assert body["shipped"]["providers"]["deepseek"]["enabled"] is False
    assert body["catalogue"]["providers"]["deepseek"]["enabled"] is True
    shipped_flash = body["shipped"]["providers"]["deepseek"]["models"]["deepseek-flash"]["prices"]
    assert shipped_flash != body["catalogue"]["providers"]["deepseek"]["models"]["deepseek-flash"]["prices"]


async def test_a_read_says_how_much_of_the_stored_overlay_is_in_force(client: AsyncClient) -> None:
    """An overlay an earlier release stored names a provider this one does not ship: the rest is in force."""
    await _db(client).execute(
        "UPDATE provider_catalogue SET data = $1::jsonb, generation = generation + 1",
        {"providers": {"retired": {"enabled": True}, "deepseek": {"enabled": True}}},
    )
    body = (await client.get(CATALOG)).json()
    assert (body["in_force"], body["in_force_generation"]) == ("in_part", body["generation"])
    assert body["catalogue"]["providers"]["deepseek"]["enabled"] is True, "a part that applies was not in force"
    assert [p["loc"] for p in body["refused"]] == [["providers", "retired"]]
    clean = (await client.get(CATALOG)).json()
    assert clean["refused"] == body["refused"], "read again, the same answer"


# ---- writing ----


async def test_a_write_is_stored_as_written_in_force_at_once_and_audited(client: AsyncClient) -> None:
    generation = (await client.get(CATALOG)).json()["generation"]
    resp = await client.put(CATALOG, content=DEEPSEEK_CHEAP, headers=YAML, params={"reason": "cheaper flash"})
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert (saved["generation"], saved["changed"]) == (generation + 1, True) and saved["revision_id"]
    read = await client.get(CATALOG)
    assert read.headers["etag"] == f'"{generation + 1}"'
    body = read.json()
    assert body["yaml_text"] == DEEPSEEK_CHEAP, "the comment was not kept"
    assert body["catalogue"]["providers"]["deepseek"]["enabled"] is True
    assert (await client.get(CATALOG, params={"format": "yaml"})).text == DEEPSEEK_CHEAP
    models = (await client.get("/api/v1/config/models")).json()
    assert "deepseek" in {p["name"] for p in models["providers"]}, "the write was not in force at once"
    assert await _audits(client) == [
        (
            "provider_catalogue.updated",
            {
                "generation": generation + 1,
                "revision_id": saved["revision_id"],
                "source": "person",
                "reason": "cheaper flash",
            },
        )
    ]


async def test_a_dated_price_is_stored_and_another_process_reads_it(client: AsyncClient) -> None:
    from datetime import UTC, datetime

    from embry0.services.provider_catalogue_store import CatalogueStore

    text = DEEPSEEK_ON + (
        "    models:\n      deepseek-flash: {prices: [{in: 1.0, out: 2.0}, {from: 2027-01-01, in: 9.0, out: 9.0}]}\n"
    )
    resp = await _put(client, text)
    assert resp.status_code == 200, resp.text
    body = (await client.get(CATALOG)).json()
    assert body["data"]["providers"]["deepseek"]["models"]["deepseek-flash"]["prices"][1]["from"] == "2027-01-01"
    catalogue.install(None)  # another process
    reader = CatalogueStore(_db(client))
    installed = await reader.refresh()
    assert (reader.in_force, reader.refused) == ("whole", [])
    assert installed.rates("deepseek-flash", at=datetime(2027, 1, 2, tzinfo=UTC)) == (9.0, 9.0)
    assert installed.rates("deepseek-flash", at=datetime(2026, 12, 2, tzinfo=UTC)) == (1.0, 2.0)


async def test_the_same_text_again_records_nothing(client: AsyncClient) -> None:
    first = (await _put(client, DEEPSEEK_ON)).json()
    again = await _put(client, DEEPSEEK_ON)
    assert again.status_code == 200
    assert (again.json()["revision_id"], again.json()["changed"], again.json()["generation"]) == (
        None,
        False,
        first["generation"],
    )
    assert len(await _audits(client)) == 1


async def test_a_dry_run_says_what_would_change_and_stores_nothing(client: AsyncClient) -> None:
    generation = (await client.get(CATALOG)).json()["generation"]
    resp = await client.put(CATALOG, content=DEEPSEEK_ON, headers=YAML, params={"dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["dry_run"], body["changed"], body["generation"]) == (True, True, generation)
    assert "+    enabled: true" in body["diff"]
    assert (await client.get(CATALOG)).json()["generation"] == generation
    assert await _audits(client) == []


async def test_if_match_refuses_a_write_over_a_newer_catalogue(client: AsyncClient) -> None:
    generation = (await client.get(CATALOG)).json()["generation"]
    stale = await _put(client, DEEPSEEK_ON, **{"If-Match": f'"{generation + 3}"'})
    assert stale.status_code == 409 and f"generation {generation}" in stale.json()["detail"]
    dry = await client.put(
        CATALOG, content=DEEPSEEK_ON, headers={**YAML, "If-Match": f'"{generation + 3}"'}, params={"dry_run": True}
    )
    assert dry.status_code == 409
    ok = await _put(client, DEEPSEEK_ON, **{"If-Match": f'"{generation}"'})
    assert ok.status_code == 200, ok.text


async def test_a_refusal_names_every_problem_and_never_a_value(client: AsyncClient) -> None:
    text = (
        "providers:\n  nobody-we-know: {enabled: true}\n"
        "  openai:\n    base_url: https://secret-host.example.test\n"
        "    models:\n      gpt-6-luna: {request_extras: {model: hidden-model-name}}\n"
    )
    resp = await _put(client, text)
    assert resp.status_code == 422, resp.text
    problems = resp.json()["detail"]
    assert {tuple(p["loc"][:3]) for p in problems} >= {
        ("providers", "nobody-we-know"),
        ("providers", "openai", "base_url"),
        ("providers", "openai", "models"),
    }, problems
    for value in ("secret-host", "hidden-model-name"):
        assert value not in resp.text, f"the refusal repeated {value!r}"
    assert (await client.get(CATALOG)).json()["yaml_text"] == ""


async def test_a_text_past_the_bound_is_a_413(client: AsyncClient) -> None:
    resp = await _put(client, DEEPSEEK_ON + "#" * (256 * 1024))
    assert resp.status_code == 413
    assert (await client.get(CATALOG)).json()["yaml_text"] == ""


async def test_a_problem_of_the_text_in_a_model_is_a_422_that_names_no_id(client: AsyncClient) -> None:
    text = "providers:\n  deepseek:\n    models:\n      zz-model: {free: true, free: false}\n"
    resp = await _put(client, text)
    assert resp.status_code == 422, resp.text
    assert [(p["loc"], p["type"]) for p in resp.json()["detail"]] == [(["providers"], "duplicate_key")], resp.text
    assert "zz-model" not in resp.text, "the 422 repeats the model's id the operator wrote"


async def test_a_body_that_is_not_utf8_is_a_422(client: AsyncClient) -> None:
    resp = await _put(client, b"providers: {deepseek: {enabled: true}} # \xff\n")
    assert resp.status_code == 422 and resp.json()["detail"][0]["type"] == "encoding"


async def test_a_change_that_takes_out_a_tier_model_is_refused(client: AsyncClient) -> None:
    assert (await _put(client, DEEPSEEK_ON)).status_code == 200
    tier = await client.put("/api/v1/config/provider", json={"model_light": "deepseek-flash"})
    assert tier.status_code == 200, tier.text
    resp = await _put(client, "")
    assert resp.status_code == 422
    assert [p["loc"] for p in resp.json()["detail"]] == [["references", "tiers", "model_light"]]
    assert resp.json()["detail"][0]["type"] == "in_use"


async def test_a_tier_is_checked_against_the_stored_catalogue(client: AsyncClient) -> None:
    """A write that names a model reads the stored catalogue, not only what this process installed."""
    await _written_elsewhere(client, DEEPSEEK_ON)
    tier = await client.put("/api/v1/config/provider", json={"default_model": "deepseek-flash"})
    assert tier.status_code == 200, f"a model the stored catalogue offers was refused: {tier.text}"
    off = await _put(client, "")
    assert off.status_code == 422 and off.json()["detail"][0]["loc"] == ["references", "tiers", "default_model"]


# ---- history and undo ----


async def test_revisions_and_undo(client: AsyncClient) -> None:
    first = (await _put(client, DEEPSEEK_ON)).json()
    second = (await _put(client, DEEPSEEK_CHEAP)).json()
    listed = (await client.get(f"{CATALOG}/revisions")).json()["revisions"]
    assert [(r["id"], r["undoable"]) for r in listed] == [(second["revision_id"], True), (first["revision_id"], False)]
    assert listed[1]["why_not"] and listed[0]["why_not"] is None
    one = (await client.get(f"{CATALOG}/revisions/{second['revision_id']}")).json()
    assert (one["before_text"], one["after_text"]) == (DEEPSEEK_ON, DEEPSEEK_CHEAP)
    assert (await client.get(f"{CATALOG}/revisions/{second['revision_id'] + 99}")).status_code == 404

    assert (await client.post(f"{CATALOG}/revisions/{first['revision_id']}/undo")).status_code == 409
    undone = await client.post(f"{CATALOG}/revisions/{second['revision_id']}/undo")
    assert undone.status_code == 200, undone.text
    assert (await client.get(CATALOG)).json()["yaml_text"] == DEEPSEEK_ON
    assert (await client.post(f"{CATALOG}/revisions/{second['revision_id'] + 99}/undo")).status_code == 404
    actions = [action for action, _ in await _audits(client)]
    assert actions == ["provider_catalogue.updated"] * 2 + ["provider_catalogue.change_undone"]
    newest = (await client.get(f"{CATALOG}/revisions", params={"limit": 1})).json()["revisions"]
    assert newest[0]["source"] == "restore"


async def test_an_undo_that_takes_out_a_tier_model_is_refused(client: AsyncClient) -> None:
    first = (await _put(client, DEEPSEEK_ON)).json()
    assert (await client.put("/api/v1/config/provider", json={"model_light": "deepseek-flash"})).status_code == 200
    resp = await client.post(f"{CATALOG}/revisions/{first['revision_id']}/undo")
    assert resp.status_code == 422 and resp.json()["detail"][0]["loc"] == ["references", "tiers", "model_light"]


# ---- who may ----


def test_writes_are_admin_tier_and_reads_are_not() -> None:
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from tests.fixtures.routes import app_routes

    gated: dict[tuple[str, str], bool] = {}
    for route in app_routes(create_app()):
        if not route.path.startswith(CATALOG):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        for method in route.methods or ():
            gated[(method, route.path)] = _admin_dependency in calls
    assert gated == {
        ("GET", CATALOG): False,
        ("PUT", CATALOG): True,
        ("GET", f"{CATALOG}/shipped"): False,
        ("GET", f"{CATALOG}/revisions"): False,
        ("GET", f"{CATALOG}/revisions/{{revision_id}}"): False,
        ("POST", f"{CATALOG}/revisions/{{revision_id}}/undo"): True,
    }


async def test_a_key_with_an_allowlist_reads_it_and_changes_nothing(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.repository_writes import call, repository_writes_app

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        saved = await call(app, "PUT", CATALOG, keys.open, DEEPSEEK_ON)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision_id"]
        read = await call(app, "GET", CATALOG, keys.limited)
        assert read.status_code == 200 and read.json()["yaml_text"] == DEEPSEEK_ON, "a limited key reads it"
        assert (await call(app, "GET", f"{CATALOG}/revisions", keys.limited)).status_code == 200
        for method, url, body in (
            ("PUT", CATALOG, ""),
            ("POST", f"{CATALOG}/revisions/{revision}/undo", None),
        ):
            refused = await call(app, method, url, keys.limited, body)
            assert refused.status_code == 403 and str(refused.json()["detail"]).startswith("repo_not_allowed"), (
                method,
                url,
                refused.text,
            )
        stored = await db_with_migrations.fetchval("SELECT yaml_text FROM provider_catalogue")
        assert stored == DEEPSEEK_ON, "a refused write changed the catalogue"


# ---- writes that name a model read the stored catalogue ----


async def _written_elsewhere(client: AsyncClient, text: str) -> None:
    """``text`` stored by another process: this one has not looked since, and still runs on the shipped catalogue."""
    from embry0.services.provider_catalogue_store import CatalogueStore

    await CatalogueStore(_db(client)).save(text, actor="other")
    catalogue.install(None)


async def test_an_agent_is_checked_against_the_stored_catalogue(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.provider_catalogue import enable_providers

    await _written_elsewhere(
        client, DEEPSEEK_ON + "    models:\n      deepseek-next: {prices: [{in: 3.0, out: 9.0}]}\n"
    )
    enable_providers(monkeypatch, "deepseek")  # what this process had: DeepSeek on, no deepseek-next
    resp = await client.put("/api/v1/agents/developer", json={"model": "deepseek-next"})
    assert resp.status_code == 200, f"a model the stored catalogue prices was refused: {resp.text}"
    off = await _put(client, DEEPSEEK_ON)
    assert off.status_code == 422 and off.json()["detail"][0]["loc"] == ["references", "agent_definitions", "developer"]


#: DeepSeek on with a model this process does not ship; a template or a review names it once it is saved.
DEEPSEEK_NEXT = DEEPSEEK_ON + "    models:\n      deepseek-next: {prices: [{in: 3.0, out: 9.0}]}\n"
#: That, and a local model this process installs, which another process then takes out of the stored catalogue.
CUSTOM_GONE = DEEPSEEK_NEXT + "  custom:\n    enabled: true\n    models:\n      custom/gone: {free: true}\n"
CUSTOM_TAKEN_OUT = DEEPSEEK_NEXT + "  custom:\n    enabled: true\n"


def _template(name: str, model: str) -> dict[str, Any]:
    return {
        "name": name,
        "graph_definition": {
            "nodes": [
                {"node_id": "t", "agent_type": "triage"},
                {"node_id": "d", "agent_type": "developer", "config": {"model": model}},
            ],
            "edges": [{"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"}],
        },
    }


async def _taken_out_elsewhere(client: AsyncClient) -> None:
    """This process installs :data:`CUSTOM_GONE`; another stores :data:`CUSTOM_TAKEN_OUT` (custom/gone out), and
    this one has not looked since."""
    import yaml

    put = await _put(client, CUSTOM_GONE)
    assert put.status_code == 200, put.text
    await _db(client).execute(
        "UPDATE provider_catalogue SET yaml_text = $1, data = $2::jsonb, generation = generation + 1",
        CUSTOM_TAKEN_OUT,
        yaml.safe_load(CUSTOM_TAKEN_OUT),
    )


async def _template_saver(client: AsyncClient, write: str) -> Any:
    """A save of a template whose developer step runs ``model``: a new template, or a new graph for one."""
    templates = "/api/v1/pipelines/templates"
    existing = None
    if write == "update":
        made = await client.post(templates, json=_template("acme-flow", "claude-sonnet-5"))
        assert made.status_code == 201, made.text
        existing = made.json()["id"]

    async def save(name: str, model: str) -> Any:
        body = _template(name, model)
        if existing:
            return await client.put(f"{templates}/{existing}", json={"graph_definition": body["graph_definition"]})
        return await client.post(templates, json=body)

    return save


@pytest.mark.parametrize("write", ["create", "update"])
async def test_a_template_may_name_a_model_the_stored_catalogue_offers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, write: str
) -> None:
    from tests.fixtures.provider_catalogue import enable_providers

    save = await _template_saver(client, write)
    await _written_elsewhere(client, DEEPSEEK_NEXT)
    enable_providers(monkeypatch, "deepseek")  # what this process had: DeepSeek on, no deepseek-next
    taken = await save("acme-next", "deepseek-next")
    assert taken.status_code in (200, 201), f"{write}: a model the stored catalogue prices was refused: {taken.text}"
    off = await _put(client, DEEPSEEK_ON)
    assert off.status_code == 422 and off.json()["detail"][0]["loc"][:2] == ["references", "pipeline_templates"]


@pytest.mark.parametrize("write", ["create", "update"])
async def test_a_template_may_not_name_a_model_another_process_took_out(client: AsyncClient, write: str) -> None:
    save = await _template_saver(client, write)
    await _taken_out_elsewhere(client)
    refused = await save("acme-gone", "custom/gone")
    assert refused.status_code == 422, (
        f"{write}: a template naming a model another process took out of the stored catalogue was saved: "
        f"{refused.status_code} {refused.text}"
    )


REVIEW = "/api/v1/repos/acme/widgets/-/preferences"


async def test_a_review_may_name_a_model_the_stored_catalogue_offers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.provider_catalogue import enable_providers

    await _written_elsewhere(client, DEEPSEEK_NEXT)
    enable_providers(monkeypatch, "deepseek")  # what this process had: DeepSeek on, no deepseek-next
    taken = await client.put(REVIEW, json={"pr_review": {"enabled": True, "model": "deepseek-next"}})
    assert taken.status_code == 200, f"a model the stored catalogue prices was refused: {taken.text}"
    off = await _put(client, DEEPSEEK_ON)
    assert off.status_code == 422
    assert off.json()["detail"][0]["loc"] == ["references", "repo_preferences", "acme/widgets", "pr_review"]


async def test_a_review_may_not_name_a_model_another_process_took_out(client: AsyncClient) -> None:
    await _taken_out_elsewhere(client)
    refused = await client.put(REVIEW, json={"pr_review": {"enabled": True, "model": "custom/gone"}})
    assert refused.status_code == 422, (
        f"a review model another process took out of the stored catalogue was saved: {refused.status_code} "
        f"{refused.text}"
    )


async def test_a_settings_write_is_checked_against_the_stored_catalogue(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.provider_catalogue import enable_providers

    url = "/api/v1/config/settings/background_models"
    await _written_elsewhere(client, DEEPSEEK_ON)
    taken = await client.patch(url, json={"proposal_brief": "deepseek-flash"})
    assert taken.status_code == 200, f"a model the stored catalogue offers was refused: {taken.text}"
    enable_providers(monkeypatch, "groq")  # this process alone offers Groq
    refused = await client.patch(url, json={"proposal_brief": "groq/openai/gpt-oss-20b"})
    assert refused.status_code == 422, f"a model the stored catalogue does not offer was taken: {refused.text}"
    off = await _put(client, "")
    assert off.status_code == 422
    assert [p["loc"] for p in off.json()["detail"]] == [
        ["references", "background_models", "deployment", "proposal_brief"]
    ]


# ---- the production lifespan ----


async def test_the_lifespan_installs_the_stored_catalogue_and_what_another_process_writes(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import asyncio
    import os
    from unittest.mock import patch

    from embry0.agents import providers
    from embry0.api.app import create_app, lifespan
    from embry0.config import Embry0Config
    from embry0.services import provider_catalogue_store
    from embry0.services.provider_catalogue_store import CatalogueStore

    monkeypatch.setattr(provider_catalogue_store, "REFRESH_SECONDS", 0.05)
    await CatalogueStore(db_with_migrations).save(
        "providers:\n  deepseek: {enabled: true}\n  openrouter: {refresh_hours: 12}\n", actor="ops@example.com"
    )
    catalogue.install(None)
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(  # type: ignore[call-arg]
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
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )
    app = create_app(config)
    async with lifespan(app):
        assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"], "not installed at the start"
        assert app.state.openrouter_catalog.status()["refresh_hours"] == 12.0
        # Another process changes the catalogue: this one installs it on its next look. This process is put back
        # on the shipped catalogue first: put back after the write, a look in between installs the write, the reset
        # undoes it, and the generation no longer moves, so nothing installs it again.
        catalogue.install(None)
        await CatalogueStore(db_with_migrations).save("providers:\n  groq: {enabled: true}\n", actor="other")
        for _ in range(400):
            if [p.name for p in providers.enabled_openai_compatible()] == ["groq"]:
                break
            await asyncio.sleep(0.025)
        assert [p.name for p in providers.enabled_openai_compatible()] == ["groq"], "the write was never picked up"


async def test_the_lifespan_keeps_the_custom_models_a_deployment_used_before_it_installs_the_catalogue(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    tmp_path: Any,
) -> None:
    """A custom model a tier names, from before the custom provider's defaults changed (#173), runs after the start:
    the start keeps it free before the catalogue that no longer prices it is installed."""
    import os
    from unittest.mock import patch

    from embry0.agents.providers import offered_problem
    from embry0.api.app import create_app, lifespan
    from embry0.config import Embry0Config
    from embry0.services.provider_catalogue_store import CatalogueStore

    await CatalogueStore(db_with_migrations).save("providers:\n  custom: {enabled: true}\n", actor="ops@example.com")
    await db_with_migrations.execute(
        "INSERT INTO provider_config (id, model_light) VALUES ('default', 'custom/qwen') "
        "ON CONFLICT (id) DO UPDATE SET model_light = EXCLUDED.model_light"
    )
    catalogue.install(None)
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(  # type: ignore[call-arg]
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
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )
    app = create_app(config)
    async with lifespan(app):
        assert offered_problem("custom/qwen") is None, "the start must keep the tier's custom model, free"
        assert offered_problem("custom/never-used") == "the catalogue has no price for it"


@pytest.mark.parametrize("as_text", [True, False], ids=["text", "Path"])
async def test_a_write_and_its_undo_are_audited_to_the_database_and_the_file(
    client: AsyncClient, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, as_text: bool
) -> None:
    from pathlib import Path

    path = tmp_path / "audit" / "audit.jsonl"
    monkeypatch.setattr(client.app.state.config, "audit_log_path", str(path) if as_text else path)  # type: ignore[attr-defined]
    put = await _put(client, DEEPSEEK_ON)
    assert put.status_code == 200, put.text
    undone = await client.post(f"{CATALOG}/revisions/{put.json()['revision_id']}/undo")
    assert undone.status_code == 200, undone.text
    expected = ["provider_catalogue.updated", "provider_catalogue.change_undone"]
    assert [action for action, _ in await _audits(client)] == expected
    lines = Path(path).read_text().splitlines()
    assert [json.loads(line)["action"] for line in lines] == expected


# ---- a number JSON cannot hold (#756 review) ----

_LOCAL = (
    "providers:\n  custom:\n    models:\n      custom/local:\n        free: true\n        request_extras: {extras}\n"
)


@pytest.mark.parametrize(
    ("extras", "key"),
    [
        ("{temperature: .inf}", "temperature"),
        ("{temperature: -.inf}", "temperature"),
        ("{temperature: .nan}", "temperature"),
        ("{temperature: 1e400}", "temperature"),
        ("{chat_template_kwargs: {min_p: .inf}}", "chat_template_kwargs"),
        ("{chat_template_kwargs: {weights: [1, .nan]}}", "chat_template_kwargs"),
    ],
    ids=["inf", "minus-inf", "nan", "1e400", "nested-object", "nested-list"],
)
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "save"])
async def test_a_request_extra_with_a_number_json_cannot_hold_is_refused(
    client: AsyncClient, extras: str, key: str, dry_run: bool
) -> None:
    """The overlay is stored as JSON (jsonb), which holds no infinity and no NaN: the dry run said fine and the save
    then failed in Postgres. Both refuse it, naming the extra, and nothing is stored."""
    generation = (await client.get(CATALOG)).json()["generation"]
    params = {"dry_run": True} if dry_run else {}
    resp = await client.put(CATALOG, content=_LOCAL.format(extras=extras).encode(), headers=YAML, params=params)
    assert resp.status_code == 422, f"{extras}: {resp.status_code} {resp.text}"
    named = [p for p in resp.json()["detail"] if p.get("type") == "finite_number"]
    assert named and named[0]["loc"][-1] == key, f"the refusal names {key}: {resp.text}"
    assert (await client.get(CATALOG)).json()["generation"] == generation, "something was stored"
