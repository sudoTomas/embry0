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
    opts = build_sdk_options(_inv(skills=["superpowers:writing-plans"], skill_mounts=mounts))
    assert opts.plugins == [
        {"type": "local", "path": "/opt/embry0/skills/superpowers"},
        {"type": "local", "path": "/opt/embry0/skills/local"},
    ]
    assert opts.skills == ["superpowers:writing-plans"]


def test_build_sdk_options_empty_skills_means_no_filter() -> None:
    """[] would hide every skill, including the repository's own under .claude/skills; None leaves them."""
    opts = build_sdk_options(_inv(skills=[]))
    assert opts.skills is None
    assert opts.plugins == []
