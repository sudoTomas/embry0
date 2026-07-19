"""build_sdk_options — AgentInvocation → ClaudeAgentOptions translator."""

from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent


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


def test_build_sdk_options_wires_system_prompt() -> None:
    opts = build_sdk_options(_inv(system_prompt="You are a reviewer"))
    assert opts.system_prompt == "You are a reviewer"


def test_build_sdk_options_skips_empty_system_prompt() -> None:
    opts = build_sdk_options(_inv(system_prompt=""))
    assert opts.system_prompt is None


def test_build_sdk_options_passes_mcp_servers() -> None:
    opts = build_sdk_options(_inv())
    assert opts.mcp_servers == {"my-mcp": {"command": "npx", "args": ["mymcp"]}}


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
