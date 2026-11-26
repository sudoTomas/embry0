import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from tests.fixtures.budget_settings import BudgetStore


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    # GET /config/budget reads the settings document's budget section (#171); its writes, now through
    # the section, are proven on a real database in tests/unit/api/test_budget_settings.py.
    app.state.settings = BudgetStore({"max_budget_per_job_usd": 12.5})
    mock_context = MagicMock()
    mock_context.get_global = AsyncMock(return_value={"system_context": "global", "assistant_context": ""})
    mock_context.set_global = AsyncMock()
    mock_context.get_repo = AsyncMock(return_value=None)
    mock_context.set_repo = AsyncMock()
    app.state.context_repo = mock_context
    mock_provider = MagicMock()
    mock_provider.get = AsyncMock(
        # DB row values deliberately differ from Embry0Config defaults so the
        # tests prove the DB is the tier source (RAV-1238).
        return_value={
            "model_heavy": "claude-opus-5",
            "model_medium": "claude-sonnet-5",
            "model_light": "claude-haiku-4-5",
        }
    )
    app.state.provider_repo = mock_provider
    return app


@pytest.mark.asyncio
async def test_get_model_config_returns_configured_models(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "heavy" in data
    assert "medium" in data
    assert "light" in data
    assert isinstance(data["heavy"], str) and data["heavy"]
    assert isinstance(data["medium"], str) and data["medium"]
    assert isinstance(data["light"], str) and data["light"]
    assert data["heavy"] == "claude-opus-5"
    assert data["medium"] == "claude-sonnet-5"
    assert data["light"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_model_config_tiers_come_from_db_row_not_env_config(app):
    """RAV-1238: the DB provider_config row (what the runtime resolves) wins
    over Embry0Config env values — stale MODEL_* .env lines must not leak
    into the dashboard dropdowns."""
    app.state.provider_repo.get = AsyncMock(
        return_value={
            "model_heavy": "claude-opus-9",
            "model_medium": "claude-sonnet-9",
            "model_light": "claude-haiku-9",
        }
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    assert (data["heavy"], data["medium"], data["light"]) == (
        "claude-opus-9",
        "claude-sonnet-9",
        "claude-haiku-9",
    )


@pytest.mark.asyncio
async def test_model_config_falls_back_to_env_config_when_row_lacks_tiers(app):
    app.state.provider_repo.get = AsyncMock(return_value={})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    assert data["heavy"] == "claude-opus-5"  # Embry0Config default


@pytest.mark.asyncio
async def test_xai_available_when_refresher_provisioned(app, monkeypatch):
    """RAV-1238: the default grok path is the xai-proxy (SuperGrok OAuth) —
    a provisioned refresher marks xai available with no XAI_API_KEY."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    app.state.xai_refresher = MagicMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    assert xai["available"] is True
    assert any(m["model"] == "grok-4.5" for m in xai["models"])


@pytest.mark.asyncio
async def test_xai_available_via_api_key_env(app, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    app.state.xai_refresher = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    assert xai["available"] is True


@pytest.mark.asyncio
async def test_xai_unavailable_without_key_or_refresher(app, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    app.state.xai_refresher = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    assert xai["available"] is False


@pytest.mark.asyncio
async def test_get_budget(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/budget")
    assert resp.status_code == 200
    assert resp.json() == {
        "max_budget_per_job_usd": 12.5,
        "daily_cap_usd": 100.0,
        "monthly_cap_usd": 500.0,
        "rate_limit_per_author_per_hour": 5,
        "overrun_mode": "soft",
    }, "the section's values of the route's five keys, the defaults where nothing is set"


@pytest.mark.asyncio
async def test_get_global_context(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/context")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_set_global_context(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/context",
            json={"system_context": "Use TypeScript."},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200


# --- RAV-1056 Phase C: context/provider-test mutations now emit audit events ---


@pytest.mark.asyncio
async def test_set_global_context_emits_audit_with_char_counts(app, monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr("embry0.api.v1.config.emit_audit", emit)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/v1/config/context",
            json={"system_context": "Use TypeScript."},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert emit.await_count == 1
    assert emit.call_args.args[1] == "context_config_updated"
    details = emit.call_args.kwargs["details"]
    assert details["scope"] == "global"
    assert details["system_context_chars"] == len("Use TypeScript.")
    # bodies are large prompt text — only char counts may be audited
    assert "system_context" not in details


@pytest.mark.asyncio
async def test_set_repo_context_emits_audit(app, monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr("embry0.api.v1.config.emit_audit", emit)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/v1/config/context/repos/acme/backend",
            json={"system_context": "x"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert emit.call_args.args[1] == "context_config_updated"
    assert emit.call_args.kwargs["details"]["scope"] == "repo"
    assert emit.call_args.kwargs["details"]["repo"] == "acme/backend"


@pytest.mark.asyncio
async def test_delete_repo_context_emits_audit(app, monkeypatch):
    app.state.context_repo.delete_repo = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr("embry0.api.v1.config.emit_audit", emit)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/config/context/repos/acme/backend",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert emit.call_args.args[1] == "context_config_deleted"
    assert emit.call_args.kwargs["details"] == {"repo": "acme/backend"}


# --- RAV-1297: complete provider catalog + per-provider live tests ---


@pytest.mark.asyncio
async def test_catalog_includes_anthropic_first_class(app, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert anthropic["available"] is True
    assert anthropic["credential_source"] == "oauth"
    assert anthropic["default"] is True
    assert any(m["model"] == "claude-opus-5" for m in anthropic["models"])
    # Pricing metadata surfaces for Anthropic models too.
    opus = next(m for m in anthropic["models"] if m["model"] == "claude-opus-5")
    assert opus["pricing_usd_per_mtok"] == {"input": 5.0, "output": 25.0}


@pytest.mark.asyncio
async def test_catalog_anthropic_credential_source_both(app, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-not-real")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert anthropic["credential_source"] == "oauth+api_key"


@pytest.mark.asyncio
async def test_catalog_anthropic_registry_when_env_is_empty(app, monkeypatch):
    """A registry row counts when neither Anthropic env var is set. Env still wins when set."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    app.state.xai_refresher = None
    repo = MagicMock()
    repo.any_for_provider = AsyncMock(side_effect=lambda provider: provider == "anthropic")
    app.state.credentials_repo = repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert anthropic["available"] is True
    assert anthropic["credential_source"] == "registry"
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    assert xai["credential_source"] != "registry"


@pytest.mark.asyncio
async def test_catalog_anthropic_unavailable_without_credentials(app, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert anthropic["available"] is False
    assert anthropic["credential_source"] is None


@pytest.mark.asyncio
async def test_catalog_models_carry_enabled_flag_from_disabled_models(app):
    app.state.provider_repo.get = AsyncMock(
        return_value={
            "model_heavy": "claude-opus-5",
            "model_medium": "claude-sonnet-5",
            "model_light": "claude-haiku-4-5",
            "disabled_models": ["grok-4.5"],
        }
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    grok = next(m for m in xai["models"] if m["model"] == "grok-4.5")
    assert grok["enabled"] is False
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert all(m["enabled"] for m in anthropic["models"])


@pytest.mark.asyncio
async def test_catalog_lists_grok_4_7_with_its_price(app):
    """#465: an unlisted model is missing from every dashboard picker."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    xai = next(p for p in data["providers"] if p["name"] == "xai")
    listed = {m["model"]: m for m in xai["models"]}
    assert "grok-4.7" in listed, f"GET /config/models must list grok-4.7, xai models: {sorted(listed)}"
    assert listed["grok-4.7"]["pricing_usd_per_mtok"] == {"input": 2.0, "output": 6.0}
    assert listed["grok-4.7"]["enabled"] is True
    assert "grok-4.5" in listed, "grok-4.5 stays listed"


@pytest.mark.asyncio
async def test_provider_live_test_unknown_provider_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/config/providers/openai/test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_provider_live_test_anthropic_reports_checks(app, monkeypatch):
    from embry0.services.provider_probe import ProbeCheck

    async def fake_probe():
        return [ProbeCheck("oauth", "ok", "authenticated and reachable")]

    monkeypatch.setattr("embry0.services.provider_probe.probe_anthropic", fake_probe)
    emit = AsyncMock()
    monkeypatch.setattr("embry0.api.v1.config.emit_audit", emit)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/config/providers/anthropic/test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["status"] == "ok"
    assert body["checks"] == [{"source": "oauth", "status": "ok", "message": "authenticated and reachable"}]
    # Audit carries the outcome only — never credentials or provider payloads.
    assert emit.call_args.args[1] == "provider_connection_tested"
    assert emit.call_args.kwargs["details"] == {"provider": "anthropic", "status": "ok"}


@pytest.mark.asyncio
async def test_provider_live_test_xai_entitlement_hint(app, monkeypatch):
    from embry0.services.provider_probe import ProbeCheck

    async def fake_probe(refresher=None):
        return [ProbeCheck("oauth_proxy", "entitlement_refused", "HTTP 403. xAI's 403 is ambiguous...")]

    monkeypatch.setattr("embry0.services.provider_probe.probe_xai", fake_probe)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/config/providers/xai/test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "entitlement_refused"


# --- provider readiness: prefixes and the last live test per provider ---


@pytest.mark.asyncio
async def test_catalog_carries_model_prefixes_and_no_last_probe_without_audit(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    by_name = {p["name"]: p for p in data["providers"]}
    assert by_name["anthropic"]["model_prefixes"] == ["claude-"]
    assert by_name["xai"]["model_prefixes"] == ["grok-"]
    assert by_name["anthropic"]["last_probe"] is None
    assert by_name["xai"]["last_probe"] is None


@pytest.mark.asyncio
async def test_catalog_reports_the_last_connection_test_per_provider(app):
    from datetime import UTC, datetime

    tested_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    rows = {"xai": {"details": {"provider": "xai", "status": "entitlement_refused"}, "created_at": tested_at}}
    audit = MagicMock()
    audit.latest_entry = AsyncMock(side_effect=lambda action, *, detail_key, detail_value: rows.get(detail_value))
    app.state.audit_repo = audit
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        data = (await client.get("/api/v1/config/models")).json()
    by_name = {p["name"]: p for p in data["providers"]}
    assert by_name["xai"]["last_probe"] == {"status": "entitlement_refused", "checked_at": tested_at.isoformat()}
    assert by_name["anthropic"]["last_probe"] is None
    assert {call.args[0] for call in audit.latest_entry.await_args_list} == {"provider_connection_tested"}


# --- #750: a field a config route does not know is refused, never dropped with a 200 ---

_CONFIG_WRITES = [
    ("/api/v1/config/provider", {"default_model": ""}),
    ("/api/v1/config/integrations", {"telegram_chat_id": "1"}),
    ("/api/v1/config/context", {"system_context": "s"}),
    ("/api/v1/config/context/repos/acme/widgets", {"system_context": "s"}),
    ("/api/v1/config/budget", {"daily_cap_usd": 5.0}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), _CONFIG_WRITES, ids=[p.rsplit("/config/", 1)[1] for p, _ in _CONFIG_WRITES])
@pytest.mark.parametrize("unknown", ["default_modle", "provider_mode", "no_such_field"])
async def test_a_config_write_refuses_a_field_it_does_not_know_naming_it(app, path, body, unknown) -> None:
    """A misspelled field, one removed long ago (provider_mode) or a made-up one answered 200 and changed nothing, so
    the caller believed it saved. It is refused naming the field, and nothing is written."""
    app.state.integration_repo = MagicMock(update=AsyncMock(return_value={}), get=AsyncMock(return_value={}))
    app.state.provider_repo.update = AsyncMock(return_value={})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(path, json={**body, unknown: "x"}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422, f"{path} took {unknown}: {resp.status_code} {resp.text}"
    named = [e.get("loc") for e in resp.json()["detail"] if isinstance(e, dict)]
    assert ["body", unknown] in named, f"the refusal does not name {unknown}: {resp.text}"
    for written in (
        app.state.context_repo.set_global,
        app.state.context_repo.set_repo,
        app.state.provider_repo.update,
        app.state.integration_repo.update,
    ):
        written.assert_not_called()


#: The writes this fake app can take whole: the budget's goes to the settings store.
_TAKEN = [w for w in _CONFIG_WRITES if not w[0].endswith("/budget")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), _TAKEN, ids=[p.rsplit("/config/", 1)[1] for p, _ in _TAKEN])
async def test_a_config_write_of_known_fields_is_still_taken(app, path, body) -> None:
    """The budget's write goes through the settings store, proven on a database (test_budget_settings.py)."""
    app.state.integration_repo = MagicMock(update=AsyncMock(return_value={}), get=AsyncMock(return_value={}))
    app.state.provider_repo.update = AsyncMock(return_value={})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(path, json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, f"{path} did not take a field it knows: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), _CONFIG_WRITES, ids=[p.rsplit("/config/", 1)[1] for p, _ in _CONFIG_WRITES])
@pytest.mark.parametrize("misspelled", ["slack_webhook_ur", "telegram_bot_tokn"])
async def test_the_value_under_an_unknown_field_never_comes_back(app, path, body, misspelled) -> None:
    """A secret sent under a misspelled key would come back in the 422 with FastAPI's ``input``: the refusal names
    the field and never repeats its value."""
    secret = "value-under-a-misspelled-key-0123456789"
    app.state.integration_repo = MagicMock(update=AsyncMock(return_value={}), get=AsyncMock(return_value={}))
    app.state.provider_repo.update = AsyncMock(return_value={})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(path, json={**body, misspelled: secret}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422, resp.text
    assert secret not in resp.text, f"{path} echoed the value sent under {misspelled}: {resp.text}"
    assert ["body", misspelled] in [e.get("loc") for e in resp.json()["detail"]], resp.text


@pytest.mark.asyncio
async def test_no_route_echoes_the_value_under_an_unknown_field() -> None:
    """App-wide: the handler drops ``input`` from every unknown-field error, whatever the route (a model with
    extra="forbid" anywhere); another error keeps FastAPI's own answer, its input with it."""
    from fastapi.exceptions import RequestValidationError
    from starlette.requests import Request

    from embry0.api.v1.credentials import validation_error_handler

    request = Request({"type": "http", "method": "PUT", "path": "/api/v1/anything", "headers": [], "query_string": b""})
    unknown = {
        "type": "extra_forbidden",
        "loc": ("body", "tokn"),
        "msg": "Extra inputs are not permitted",
        "input": "s3",
    }
    wrong = {"type": "int_type", "loc": ("body", "count"), "msg": "Input should be a valid integer", "input": "x"}
    resp = await validation_error_handler(request, RequestValidationError([unknown, wrong]))
    detail = json.loads(resp.body)["detail"]
    assert [("input" in e) for e in detail] == [False, True], f"only the unknown field's value is left out: {detail}"


# --- the integrations' 422 carries no input values, as a credential route's does ---

_VALUE = "value-sent-with-the-wrong-type-0123456789"
_WRONG_TYPES = [
    pytest.param({"webhook_secret": 8675309123}, ["body", "webhook_secret"], "8675309123", id="number"),
    pytest.param({"slack_webhook_url": [_VALUE]}, ["body", "slack_webhook_url"], _VALUE, id="list"),
    pytest.param({"linear_api_key": {"k": _VALUE}}, ["body", "linear_api_key"], _VALUE, id="object"),
    pytest.param([_VALUE], ["body"], _VALUE, id="not-an-object"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "loc", "value"), _WRONG_TYPES)
async def test_an_integrations_value_of_the_wrong_type_never_comes_back(app, body, loc, value) -> None:
    """The integrations hold the webhook secret and the chat and tracker keys: a value sent with the wrong JSON type
    is refused naming where it was, as on a credential route, and never repeated."""
    app.state.integration_repo = MagicMock(update=AsyncMock(return_value={}), get=AsyncMock(return_value={}))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/integrations", json=body, headers={"X-Requested-With": "XMLHttpRequest"}
        )
    assert resp.status_code == 422, resp.text
    assert value not in resp.text, f"the integrations' 422 repeated the value sent: {resp.text}"
    detail = resp.json()["detail"]
    assert loc in [e.get("loc") for e in detail], f"the refusal does not say where: {resp.text}"
    assert all("input" not in e and "ctx" not in e for e in detail), detail
    app.state.integration_repo.update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [("/api/v1/config/context", {"system_context": 5}), ("/api/v1/config/provider", {"default_model": ["m"]})],
    ids=["context", "provider"],
)
async def test_another_config_route_keeps_its_422_as_it_was(app, path, body) -> None:
    """Only the integrations join the routes whose 422 carries no input: another config route answers as before."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(path, json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422, resp.text
    sent = next(iter(body.values()))
    assert [e.get("input") for e in resp.json()["detail"]] == [sent], f"{path}'s 422 changed: {resp.text}"
