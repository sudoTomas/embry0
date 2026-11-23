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
    assert (body["yaml_text"], body["data"], body["refused"]) == ("", {}, [])
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


async def test_the_lifespan_imports_the_variables_and_installs_what_another_process_writes(
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
            leads_enabled=False,
            slop_meter_interval_seconds=0,
            slop_meter_data_path=str(tmp_path / "slop-meter"),
            openai_compatible_providers="deepseek",
            openrouter_catalog_refresh_hours="12",
        )
    app = create_app(config)
    async with lifespan(app):
        assert [p.name for p in providers.enabled_openai_compatible()] == ["deepseek"], "not imported at the start"
        assert app.state.openrouter_catalog.status()["refresh_hours"] == 12.0
        # Another process changes the catalogue: this one installs it on its next look.
        await CatalogueStore(db_with_migrations).save("providers:\n  groq: {enabled: true}\n", actor="other")
        catalogue.install(None)
        for _ in range(400):
            if [p.name for p in providers.enabled_openai_compatible()] == ["groq"]:
                break
            await asyncio.sleep(0.025)
        assert [p.name for p in providers.enabled_openai_compatible()] == ["groq"], "the write was never picked up"
    ledger = await db_with_migrations.fetchval("SELECT imported FROM provider_catalogue")
    assert sorted(ledger) == ["openrouter.refresh_hours", "providers.enabled"]
