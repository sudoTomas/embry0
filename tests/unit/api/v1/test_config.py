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
