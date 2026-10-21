"""GET /agents reports, as each agent's effective model, the model its runs use.

The run side is each agent run the way its executor path runs it: the model
policy ``load_model_policy`` reads from provider_config and, for the
pr-reviewer, the ``agent_models_override`` every pr-review job sets
(``_pr_review_model``: the repo's ``pr_review.model``, else the heavy tier),
both handed to ``resolve_agent_invocation``. The report side is
``annotate_agents``, the function behind GET /agents, over the same
provider_config row. This pins that the two cannot disagree for any built-in
agent under any provider configuration.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.agents.resolver import resolve_agent_invocation
from embry0.config import Embry0Config
from embry0.orchestration.nodes.agent import load_model_policy
from embry0.services._executor_run import _pr_review_model
from embry0.services.provider_readiness import annotate_agents
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.repositories.provider_config import ProviderConfigRepository
from embry0.workflows.pr_review.agent_seed import AGENT_TYPE as PR_REVIEWER
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED

BUILTIN_AGENTS = {**BUILTIN_SEED, "qa": QA_AGENT_SEED}
_TIERS = {"model_heavy": "claude-opus-5", "model_medium": "claude-sonnet-5", "model_light": "claude-haiku-4-5"}
# The provider_config row both sides read. An Exception is a failed read.
_ROWS: dict[str, dict[str, Any] | Exception] = {
    "no default": {**_TIERS, "default_model": "", "disabled_models": []},
    "default grok": {**_TIERS, "default_model": "grok-4.7", "disabled_models": []},
    "default claude": {**_TIERS, "default_model": "claude-haiku-4-5", "disabled_models": []},
    "opus disabled": {**_TIERS, "default_model": "", "disabled_models": ["claude-opus-5"]},
    "sonnet disabled, no fallback": {
        **_TIERS,
        "model_medium": "claude-sonnet-5",
        "default_model": "",
        "disabled_models": ["claude-sonnet-5"],
    },
    "heavy tier on xai": {**_TIERS, "model_heavy": "grok-4.7", "default_model": "", "disabled_models": []},
    "heavy tier from the env, default claude": {
        **_TIERS,
        "model_heavy": "",
        "default_model": "claude-haiku-4-5",
        "disabled_models": [],
    },
    "unreadable": RuntimeError("db down"),
}
# The layer under the row: Embry0Config's tiers (MODEL_HEAVY and the rest).
_CONFIG = Embry0Config(_env_file=None, model_heavy="grok-4.7")
_DB = object()


@pytest.fixture
def provider_row(request, monkeypatch) -> None:
    row = _ROWS[request.param]

    async def get(self: ProviderConfigRepository) -> dict[str, Any]:
        if isinstance(row, Exception):
            raise row
        return dict(row)

    monkeypatch.setattr(ProviderConfigRepository, "get", get)


async def _executor_job_models(agent_type: str) -> dict[str, str] | None:
    """The agent_models_override the executor itself sets for ``agent_type``'s runs.

    Only the pr-review path sets one (``_run_pr_review_workflow``); every other
    path passes on what the job asked for, and a job here asks for nothing.
    """
    if agent_type != PR_REVIEWER:
        return None
    executor = SimpleNamespace(_db=_DB, _config=_CONFIG)
    return {PR_REVIEWER: await _pr_review_model(executor, {}, "job-test")}


async def _run_model(agent_type: str, definition: dict[str, Any]) -> str:
    graph_config = {"configurable": {"db": _DB, "embry0_config": _CONFIG}}
    invocation = resolve_agent_invocation(
        agent_type=agent_type,
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides=None,
        agent_definition=definition,
        pipeline_config={},
        max_turns=10,
        timeout_seconds=60,
        credentials={"oauth_token": "tok-not-real"},
        model_policy=await load_model_policy(graph_config),
        job_agent_models=await _executor_job_models(agent_type),
    )
    return invocation.model


async def _reported(agent_type: str, definition: dict[str, Any]) -> dict[str, Any]:
    app_state = SimpleNamespace(provider_repo=ProviderConfigRepository(_DB), config=_CONFIG, xai_refresher=None)
    [agent] = await annotate_agents([{**definition, "type": agent_type}], app_state)
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_row", sorted(_ROWS), indirect=True)
@pytest.mark.parametrize("agent_type", sorted(BUILTIN_AGENTS))
async def test_the_reported_model_is_the_model_a_run_uses(agent_type: str, provider_row: None) -> None:
    definition = BUILTIN_AGENTS[agent_type]
    reported = await _reported(agent_type, definition)
    ran = await _run_model(agent_type, definition)
    assert reported["effective_model"] == ran, (
        f"GET /agents says {agent_type} runs {reported['effective_model']} "
        f"({reported['effective_model_source']}), its runs use {ran}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_row", sorted(_ROWS), indirect=True)
async def test_an_agent_with_no_model_reports_the_built_in_default(provider_row: None) -> None:
    definition = {**BUILTIN_SEED["research"], "model": ""}
    reported = await _reported("research", definition)
    assert reported["effective_model"] == await _run_model("research", definition)
    if reported["effective_model_source"] != "default_model":
        assert reported["effective_model_source"] in ("builtin_default", "disabled_fallback")
