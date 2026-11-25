"""run_agent_node sends each run its provider record and records its pricing on the trace (#173 PR 3)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.agents.invocation import ProviderRecord
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from tests.fixtures.budget_settings import BudgetStore
from tests.fixtures.provider_catalogue import enable_providers
from tests.unit.orchestration.nodes.test_agent_node_provider_fallback import _ProviderRowDb, _Traces


async def _run(model: str) -> tuple[dict[str, Any], _Traces]:
    captured: dict[str, Any] = {}
    traces = _Traces()

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured.update(kwargs["config"])
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1"},
        agent_runner=_Runner(),
        agent_type="developer",
        prompt="do it",
        agent_definition={**dict(BUILTIN_SEED["developer"]), "model": model},
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={
            "configurable": {
                "db": _ProviderRowDb([]),
                # The fake database holds no settings documents: the job's budget is the section's defaults.
                "settings_store": BudgetStore(),
                "traces_repo": traces,
                "job_id": "job-1",
                "llm_providers": ("anthropic", "xai", "deepseek"),
            }
        },
    )
    return captured, traces


@pytest.mark.asyncio
async def test_a_run_is_sent_its_record_and_its_trace_keeps_the_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_providers(
        monkeypatch, "deepseek", deepseek={"models": {"deepseek-flash": {"prices": [{"in": 0.5, "out": 2.5}]}}}
    )
    config, traces = await _run("deepseek-flash")
    record = ProviderRecord.from_config(config["provider_record"])
    assert record is not None and (record.name, record.rates) == ("deepseek", (0.5, 2.5)), config["provider_record"]
    assert config["pricing_usd_per_mtok"] == [0.5, 2.5], "an older sandbox image reads the rates alone"
    pricing = traces.created[0]["pricing"]
    assert {k: pricing[k] for k in ("provider", "model", "rates", "charged_from")} == {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "rates": {"in": 0.5, "out": 2.5},
        "charged_from": "catalogue",
    }
    assert pricing["resolved_at"] == record.resolved_at


@pytest.mark.asyncio
async def test_an_anthropic_run_s_trace_says_its_cli_is_charged() -> None:
    config, traces = await _run("claude-sonnet-5")
    assert config["provider_record"]["name"] == "anthropic"
    assert "pricing_usd_per_mtok" not in config
    assert traces.created[0]["pricing"]["charged_from"] == "cli"
