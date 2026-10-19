"""#219: a provider-routed model with no price entry must fail loud, never meter at $0.

xAI returns no cost of its own, so the price table is the only accounting for a
grok run. ``grok-`` routes by prefix, so a new grok id routes fine while the
table does not list it. These tests pin that such an id is refused wherever a
model is configured, refused again when an agent's model resolves, and recorded
as an error by the direct executor if it ever runs.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

UNPRICED = "grok-9-unpriced"
_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def test_unpriced_models_names_only_provider_models_without_a_price():
    from embry0.agents.providers import unpriced_models

    found = unpriced_models([UNPRICED, "grok-4.7", "claude-some-future-model", ""])
    assert found == [UNPRICED], (
        f"only the grok id with no price entry is unpriced (Claude cost comes from the CLI), got {found}"
    )


def test_compute_cost_does_not_price_an_unknown_model_at_zero():
    from embry0.agents.executor_xai import _compute_cost

    cost = _compute_cost(UNPRICED, 1_000_000, 1_000_000)
    assert cost is None, f"an unpriced model must not be metered as ${cost}; expected None (cost unknown)"


def test_direct_executor_records_an_error_for_an_unpriced_model(monkeypatch, tmp_path):
    import tests.unit.agents.test_executor_xai as helpers

    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN", "sandbox-bearer-token")
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    helpers._install(
        monkeypatch,
        [helpers._Resp([helpers._Block("text", text="done")], "end_turn", helpers._Usage(100, 20))],
    )
    inv = dataclasses.replace(helpers._invocation(), model=UNPRICED)
    events: list[dict[str, Any]] = []

    out = helpers._run(inv, events)

    assert out.is_error, "a run on an unpriced model must be recorded as an error, not a clean $0 run"
    assert UNPRICED in (out.error_message or ""), f"the error must name the model: {out.error_message!r}"
    assert out.error_code == "ERR_INVALID_CONFIG", f"expected ERR_INVALID_CONFIG, got {out.error_code!r}"
    assert any(e["type"] == "error" and UNPRICED in e["message"] for e in events), "an error event must be emitted"


def _resolve(model: str):
    from embry0.agents.resolver import resolve_agent_invocation

    return resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition={"model": model, "tools": [], "skills": [], "system_prompt": "", "mcp_servers": {}},
        pipeline_config={},
        max_turns=5,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "tok"},
    )


def test_resolver_refuses_an_unpriced_model():
    from embry0.execution.auth_provider import AuthConfigError

    with pytest.raises(AuthConfigError) as exc_info:
        _resolve(UNPRICED)
    assert UNPRICED in str(exc_info.value), f"the refusal must name the model: {exc_info.value}"
    assert _resolve("grok-4.7").model == "grok-4.7", "a priced grok model still resolves"


# ---- configuration-time refusals -------------------------------------------


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": []})
    provider_repo.update = AsyncMock(return_value={})
    provider_repo.create = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    for name in ("jobs_repo", "agent_defs_repo", "templates_repo"):
        repo = MagicMock()
        for method in ("create", "update", "get"):
            setattr(repo, method, AsyncMock(return_value={}))
        repo.list_all = AsyncMock(return_value=[])  # template saves read the agent rows
        setattr(app.state, name, repo)
    return app


async def _call(app, method: str, path: str, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=_HEADERS)


_GRAPH = {
    "nodes": [
        {"node_id": "t", "agent_type": "triage"},
        {"node_id": "d", "agent_type": "developer", "config": {"model": UNPRICED}},
    ],
    "edges": [{"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"}],
}


@pytest.mark.parametrize(
    ("label", "method", "path", "body", "repo_attr"),
    [
        (
            "job agent_models",
            "POST",
            "/api/v1/jobs",
            {"repo": "o/r", "task": "t", "agent_models": {"developer": UNPRICED}},
            "jobs_repo",
        ),
        (
            "agent create",
            "POST",
            "/api/v1/agents",
            {"type": "x", "description": "d", "model": UNPRICED},
            "agent_defs_repo",
        ),
        ("agent update", "PUT", "/api/v1/agents/developer", {"model": UNPRICED}, "agent_defs_repo"),
        # #468: a template names its models only on its steps' config.model.
        (
            "template create step model",
            "POST",
            "/api/v1/pipelines/templates",
            {"name": "n", "graph_definition": _GRAPH},
            "templates_repo",
        ),
        (
            "template update step model",
            "PUT",
            "/api/v1/pipelines/templates/tmpl-1",
            {"graph_definition": _GRAPH},
            "templates_repo",
        ),
        ("tier model", "PUT", "/api/v1/config/provider", {"model_heavy": UNPRICED}, "provider_repo"),
    ],
)
def test_configuring_an_unpriced_model_is_refused(app, label, method, path, body, repo_attr):
    resp = asyncio.run(_call(app, method, path, body))

    assert resp.status_code == 422, f"{label}: an unpriced model must be refused, got {resp.status_code} {resp.text}"
    assert UNPRICED in resp.json()["detail"], f"{label}: the 422 must name the model: {resp.text}"
    repo = getattr(app.state, repo_attr)
    repo.create.assert_not_awaited()
    repo.update.assert_not_awaited()
