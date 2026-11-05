"""build_sdk_options — AgentInvocation → ClaudeAgentOptions translator."""

import dataclasses

import pytest

from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import SafetyPolicy, default_policy_for_agent


def _inv(**overrides) -> AgentInvocation:  # noqa: ANN002, ANN003
    defaults = {
        "agent_type": "developer",
        "prompt": "write a function",
        "system_prompt": "You are a helpful developer",
        "system_context": "Repo: example/repo",
        "model": "claude-sonnet-4-6",
        "tools": ["Read", "Write", "Bash"],
        "skills": ["review"],
        "mcp_servers": {"my-mcp": {"command": "npx", "args": ["mymcp"]}},
        "max_turns": 25,
        "timeout_seconds": 600,
        "execution_mode": "sdk",
        "auth_mode": "api_key",
        "safety_policy": default_policy_for_agent("developer"),
        "channel_config": None,
    }
    defaults.update(overrides)
    return AgentInvocation(**defaults)


def test_build_sdk_options_sets_core_fields() -> None:
    opts = build_sdk_options(_inv())
    assert opts.model == "claude-sonnet-4-6"
    assert opts.allowed_tools == ["Read", "Write", "Bash"]
    assert opts.permission_mode == "bypassPermissions"
    assert opts.max_turns == 25
    assert opts.cwd == "/workspace"


def test_build_sdk_options_wires_system_prompt_as_preset_append() -> None:
    """EMB-35: a plain string would REPLACE the CLI's default system prompt
    (stripping its tool-use guidance); the preset-append form preserves it
    and keeps the combined system block cacheable."""
    opts = build_sdk_options(_inv(system_prompt="You are a reviewer"))
    assert opts.system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": "You are a reviewer",
    }


def test_build_sdk_options_skips_empty_system_prompt() -> None:
    opts = build_sdk_options(_inv(system_prompt=""))
    assert opts.system_prompt is None


def test_build_sdk_options_passes_mcp_servers_always_loaded() -> None:
    """#501: a server's tools must never be deferred; embry0 denies the ToolSearch that loads them."""
    invocation = _inv()
    opts = build_sdk_options(invocation)
    assert opts.mcp_servers == {"my-mcp": {"command": "npx", "args": ["mymcp"], "alwaysLoad": True}}, (
        f"MCP servers must be marked alwaysLoad: {opts.mcp_servers!r}"
    )
    assert invocation.mcp_servers == {"my-mcp": {"command": "npx", "args": ["mymcp"]}}, "the invocation was mutated"


def test_build_sdk_options_overrides_an_explicit_deferred_server() -> None:
    """alwaysLoad: false would defer every tool of the server behind the ToolSearch embry0 denies."""
    opts = build_sdk_options(_inv(mcp_servers={"m": {"command": "x", "alwaysLoad": False}}))
    assert opts.mcp_servers == {"m": {"command": "x", "alwaysLoad": True}}


def test_build_sdk_options_empty_mcp_is_empty_dict() -> None:
    opts = build_sdk_options(_inv(mcp_servers={}))
    assert opts.mcp_servers == {}


def test_build_sdk_options_enables_setting_sources_when_skills_present() -> None:
    opts = build_sdk_options(_inv(skills=["code-review"]))
    # setting_sources=["project"] lets the SDK load /workspace/.claude/skills/**
    # When empty, we still point at "project" so settings.json Ring 2 rules load.
    assert "project" in opts.setting_sources


def test_build_sdk_options_setting_sources_always_project() -> None:
    # Even without skills, settings.json (Ring 2) must be loaded.
    opts = build_sdk_options(_inv(skills=[]))
    assert "project" in opts.setting_sources


def test_build_sdk_options_disallows_tool_search() -> None:
    """EMB-37: ToolSearch (server-side deferred-tool loader) is how the QA
    agent escaped its allowlist. Belt-and-suspenders alongside the Ring-3
    name deny; unknown names are a harmless no-op on older CLIs."""
    opts = build_sdk_options(_inv())
    assert "tool_search_tool_regex" in opts.disallowed_tools
    assert "tool_search_tool_bm25" in opts.disallowed_tools


def test_build_sdk_options_disables_mcp_tool_search_deferral() -> None:
    """#240: tool-search deferral makes MCP tools unreachable under embry0's
    ToolSearch denial — every SDK agent must run with it forced off, via an
    env OVERLAY (never replacing the container env that carries auth)."""
    opts = build_sdk_options(_inv(agent_type="qa"))
    assert opts.env == {"ENABLE_TOOL_SEARCH": "false"}


def test_build_sdk_options_passes_plugins_and_skills_allowlist() -> None:
    mounts = (
        {"source_id": "superpowers", "plugin_name": "superpowers", "container_path": "/opt/embry0/skills/superpowers"},
        {"source_id": "local", "plugin_name": "local", "container_path": "/opt/embry0/skills/local"},
    )
    opts = build_sdk_options(_inv(skills=["superpowers:writing-plans", "local:house-style"], skill_mounts=mounts))
    assert opts.plugins == [
        {"type": "local", "path": "/opt/embry0/skills/superpowers"},
        {"type": "local", "path": "/opt/embry0/skills/local"},
    ]
    assert opts.skills == ["superpowers:writing-plans", "local:house-style"]


_THREE_MOUNTS = (
    {"source_id": "superpowers", "plugin_name": "superpowers", "container_path": "/opt/embry0/skills/superpowers"},
    {"source_id": "local", "plugin_name": "local", "container_path": "/opt/embry0/skills/local"},
    {"source_id": "house", "plugin_name": "house-rules", "container_path": "/opt/embry0/skills/house"},
)


def test_build_sdk_options_loads_only_the_plugins_whose_skills_the_agent_lists() -> None:
    """A plugin brings its hooks: an agent that lists none of its skills must not load it."""
    opts = build_sdk_options(_inv(skills=["house-rules:style", "house-rules:tone"], skill_mounts=_THREE_MOUNTS))
    assert opts.plugins == [{"type": "local", "path": "/opt/embry0/skills/house"}], "unlisted plugins were loaded"


def test_build_sdk_options_empty_skills_loads_no_plugin() -> None:
    opts = build_sdk_options(_inv(skills=[], skill_mounts=_THREE_MOUNTS))
    assert opts.plugins == [], "an agent with no skills loaded every plugin and its hooks"
    assert opts.skills is None


def test_build_sdk_options_a_repository_skill_selects_no_plugin() -> None:
    """A bare name (no colon) is one of the repository's own .claude/skills."""
    opts = build_sdk_options(_inv(skills=["code-review", "local"], skill_mounts=_THREE_MOUNTS))
    assert opts.plugins == []
    assert opts.skills == ["code-review", "local"]


def test_build_sdk_options_empty_skills_means_no_filter() -> None:
    """[] would hide every skill, including the repository's own under .claude/skills; None leaves them."""
    opts = build_sdk_options(_inv(skills=[]))
    assert opts.skills is None
    assert opts.plugins == []


def test_build_sdk_options_starts_only_the_invocations_mcp_servers() -> None:
    """The CLI otherwise also starts the servers of the workspace's .mcp.json and of every plugin."""
    opts = build_sdk_options(_inv())
    assert opts.strict_mcp_config is True, "the CLI must start the invocation's MCP servers and no others"


def test_build_sdk_options_offers_no_tool_for_an_empty_list() -> None:
    """Without --tools "" the CLI offers every tool it has; an empty list is no tools."""
    opts = build_sdk_options(_inv(tools=[]))
    assert opts.tools == [], f"an agent with no tools would be offered the CLI's set: tools={opts.tools!r}"


def test_build_sdk_options_leaves_the_cli_set_for_a_named_list() -> None:
    tools = build_sdk_options(_inv()).tools
    assert tools is None, f"an agent with tools must keep the CLI's own set, not tools={tools!r}"


# ---- the Ring-2 deny rules ride the CLI's command line ----------------------

_AGENT_TYPES = ("triage", "developer", "review", "qa")


def _cli_rules(value: str) -> list[str]:
    """Split a --disallowedTools value as the CLI does: at commas and whitespace outside parentheses."""
    rules: list[str] = []
    current = ""
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and (char == "," or char.isspace()):
            if current:
                rules.append(current)
            current = ""
            continue
        current += char
    if current:
        rules.append(current)
    return rules


@pytest.mark.parametrize("agent_type", _AGENT_TYPES)
def test_the_cli_arguments_carry_every_deny_rule(agent_type: str) -> None:
    """The CLI keeps command-line rules for the whole session; settings.json is a file in the workspace."""
    policy = default_policy_for_agent(agent_type)
    opts = build_sdk_options(_inv(agent_type=agent_type, safety_policy=policy))
    missing = [rule for rule in policy.deny_rules if rule not in opts.disallowed_tools]
    assert not missing, f"{agent_type}: deny rules missing from the CLI's --disallowedTools: {missing}"


def test_the_sdk_command_line_carries_each_deny_rule_as_one_rule() -> None:
    """What reaches the CLI: the SDK joins disallowed_tools into one argument, which the CLI splits again."""
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    policy = default_policy_for_agent("developer")
    opts = dataclasses.replace(build_sdk_options(_inv(safety_policy=policy)), cli_path="/nonexistent/claude")
    cmd = SubprocessCLITransport(prompt="x", options=opts)._build_command()
    assert "--disallowedTools" in cmd, f"the SDK passed no --disallowedTools: {cmd}"
    received = _cli_rules(cmd[cmd.index("--disallowedTools") + 1])
    withheld = ["Monitor", "Workflow", "EnterWorktree"]
    assert received == ["tool_search_tool_regex", "tool_search_tool_bm25", *withheld, *policy.deny_rules], (
        f"the CLI would read these rules from the command line: {received}"
    )


@pytest.mark.parametrize(
    "rule", ["Read(//a,b/**)", "Read(//a b/**)", "Read(//a(b)/**)", "Read //etc/**", "Read(//etc/**)\n", ""]
)
def test_a_deny_rule_the_cli_cannot_take_whole_is_refused(rule: str) -> None:
    """A rule the command line would split or merge would refuse something else, or nothing."""
    policy = SafetyPolicy(allowed_tools=["Read"], deny_rules=["Read(//etc/**)", rule])
    try:
        opts = build_sdk_options(_inv(safety_policy=policy))
    except ValueError as exc:
        assert "cannot take on its command line" in str(exc), str(exc)
    else:
        raise AssertionError(f"{rule!r} reached the CLI as {_cli_rules(','.join(opts.disallowed_tools or []))}")


@pytest.mark.parametrize("agent_type", _AGENT_TYPES)
def test_no_agent_is_offered_monitor_even_when_it_lists_it(agent_type: str) -> None:
    """Monitor (CLI 2.1.316) runs a shell command in the background; agents run theirs through Bash."""
    opts = build_sdk_options(_inv(agent_type=agent_type, tools=["Read", "Bash", "Monitor"]))
    assert "Monitor" in (opts.disallowed_tools or []), f"{agent_type}: Monitor is offered: {opts.disallowed_tools}"


@pytest.mark.parametrize("tool", ["Workflow", "EnterWorktree"])
def test_no_agent_is_offered_a_tool_embry0_does_not_gate(tool: str) -> None:
    """Workflow runs a script that drives subagents; EnterWorktree moves the session to a new worktree."""
    opts = build_sdk_options(_inv(tools=["Read", "Bash", tool]))
    assert tool in (opts.disallowed_tools or []), f"{tool} is offered: {opts.disallowed_tools}"
