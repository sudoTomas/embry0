"""An invocation that reads no configuration from its workspace (#58).

A pull request review that runs nothing works in a checkout the pull
request's author wrote. The CLI must read none of its Claude configuration:
no project settings, CLAUDE.md, commands, skills or agents. The Ring-2 deny
rules, which reach every other agent through the workspace's settings.json
as well as on the command line, reach it as flag settings and on the command
line. Every other agent is unchanged.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import SafetyPolicy, default_policy_for_agent, render_settings_json


def _inv(**overrides: Any) -> AgentInvocation:
    defaults: dict[str, Any] = {
        "agent_type": "pr-reviewer",
        "prompt": "review it",
        "system_prompt": "You review pull requests",
        "system_context": "Repo: example/repo",
        "model": "claude-sonnet-4-6",
        "tools": ["Read", "Glob", "Grep"],
        "skills": ["review"],
        "mcp_servers": {"given": {"type": "stdio", "command": "given-server"}},
        "max_turns": 25,
        "timeout_seconds": 600,
        "execution_mode": "sdk",
        "auth_mode": "api_key",
        "safety_policy": default_policy_for_agent("pr-reviewer"),
        "channel_config": None,
    }
    defaults.update(overrides)
    return AgentInvocation(**defaults)


def test_an_invocation_reads_the_workspaces_configuration_unless_told_not_to() -> None:
    assert _inv().workspace_config is True


def test_an_untrusted_workspace_loads_no_setting_source() -> None:
    options = build_sdk_options(_inv(workspace_config=False))
    assert options.setting_sources == [], f"the CLI would read the workspace's {options.setting_sources} settings"
    assert options.strict_mcp_config is True, "the workspace's .mcp.json servers would start"


def test_the_ring2_rules_reach_the_cli_without_the_workspaces_settings_file() -> None:
    """Both of the layers the CLI cannot be talked out of: its flag settings, and its command line."""
    policy = SafetyPolicy(allowed_tools=["Read"], deny_rules=["Read(//etc/**)", "Edit(//workspace/.git/**)"])
    options = build_sdk_options(_inv(workspace_config=False, safety_policy=policy))
    assert options.settings is not None, "no flag settings: the deny rules ride only the command line"
    assert json.loads(options.settings) == render_settings_json(policy)
    assert json.loads(options.settings)["permissions"]["deny"] == ["Read(//etc/**)", "Edit(//workspace/.git/**)"]
    assert {"Read(//etc/**)", "Edit(//workspace/.git/**)"} <= set(options.disallowed_tools)


def test_every_other_option_is_the_same_for_an_untrusted_workspace() -> None:
    trusted, untrusted = build_sdk_options(_inv()), build_sdk_options(_inv(workspace_config=False))
    names = [f.name for f in dataclasses.fields(trusted)]
    differ = sorted(n for n in names if getattr(trusted, n) != getattr(untrusted, n))
    assert differ == ["setting_sources", "settings"], differ


def test_every_other_agent_still_reads_its_workspace() -> None:
    """Unchanged: the project setting source, and no flag settings."""
    for agent_type in ("developer", "triage", "qa", "pr-reviewer"):
        options = build_sdk_options(_inv(agent_type=agent_type, safety_policy=default_policy_for_agent(agent_type)))
        assert (options.setting_sources, options.settings) == (["project"], None), agent_type


def test_the_sandbox_reads_the_flag_from_its_config() -> None:
    from embry0.sandbox.runner import _invocation_from_config

    base = {"prompt": "p", "agent_type": "pr-reviewer", "tools": ["Read"]}
    assert _invocation_from_config({**base, "workspace_config": False}).workspace_config is False
    assert _invocation_from_config(base).workspace_config is True, "a config without the key reads the workspace"
    assert _invocation_from_config({**base, "workspace_config": True}).workspace_config is True
    untrusted = build_sdk_options(_invocation_from_config({**base, "workspace_config": False}))
    assert untrusted.setting_sources == [] and untrusted.settings is not None


@pytest.mark.parametrize("value", [0, "false", "False", "true", 1, None, "", [], {}, "yes"])
def test_the_sandbox_reads_the_workspace_only_on_a_real_true(value: Any) -> None:
    """A present key keeps the workspace out unless it is exactly True: a falsy value of another type (0, "false")
    must not leave its configuration on, and a truthy one of another type ("true", 1) is not True either."""
    from embry0.sandbox.runner import _invocation_from_config

    base = {"prompt": "p", "agent_type": "pr-reviewer", "tools": ["Read"]}
    invocation = _invocation_from_config({**base, "workspace_config": value})
    assert invocation.workspace_config is False, f"workspace_config={value!r} read the workspace"
    assert build_sdk_options(invocation).setting_sources == []


async def test_run_agent_node_sends_the_flag_to_the_sandbox() -> None:
    from embry0.execution.agent_runner import AgentOutput
    from embry0.orchestration.nodes.agent import run_agent_node
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    shipped: list[dict[str, Any]] = []

    class _Runner:
        async def run(self, container: str, config: dict[str, Any], *args: Any, **kwargs: Any) -> AgentOutput:
            shipped.append(dict(config))
            return AgentOutput(agent_type="triage", is_error=False, output="ok")

    for workspace_config in (False, True):
        await run_agent_node(
            state={"sandbox_container_id": "c1", "total_cost_usd": 0.0},
            agent_runner=_Runner(),
            agent_type="triage",
            prompt="do it",
            agent_definition=dict(BUILTIN_SEED["triage"]),
            credentials={"api_key": "", "oauth_token": "oat-000000000001"},
            global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
            workspace_config=workspace_config,
        )
    untrusted, trusted = shipped
    assert untrusted.get("workspace_config") is False, f"the sandbox was not told: {sorted(untrusted)}"
    assert "workspace_config" not in trusted, "every other run's config is unchanged"
