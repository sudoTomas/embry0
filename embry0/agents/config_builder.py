"""Pure AgentInvocation → ClaudeAgentOptions translator for SDK mode.

Deterministic: given a fixed AgentInvocation, returns a fully-populated
ClaudeAgentOptions with no I/O. This function contains every SDK-specific
translation decision; any future SDK option change should land here.

Phase 1 only implements build_sdk_options(). build_cli_args() arrives in Phase 2.
"""

from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions

from embry0.agents.invocation import AgentInvocation


def build_sdk_options(invocation: AgentInvocation) -> ClaudeAgentOptions:
    """Render an AgentInvocation into a ClaudeAgentOptions for the Agent SDK.

    - allowed_tools: from invocation.tools.
    - model: from invocation.model.
    - permission_mode: always 'bypassPermissions' (sandbox is the real boundary;
      Ring 2 permissions.deny and Ring 3 hook still run).
    - max_turns / cwd: fixed to embry0's sandbox conventions.
    - system_prompt: invocation.system_prompt (None when empty to let the SDK
      apply its default rather than an empty string that shadows it).
    - setting_sources: ["project"] so the SDK loads /workspace/.claude/settings.json
      for Ring 2 permissions and /workspace/.claude/skills/** for skill files.
    - mcp_servers: passed through as-is from invocation.mcp_servers.
    - env: not set here — executors merge auth_provider.resolve_env output
      into the subprocess env before spawning.

    Notes:
    - max_budget_usd is NOT set here; budget caps live at the pipeline level
      in embry0, not per-invocation.
    - permission_mode stays 'bypassPermissions' intentionally; Rings 2 and 3
      cover the enforcement we actually want. See design §3.3.
    """
    return ClaudeAgentOptions(
        model=invocation.model,
        allowed_tools=list(invocation.tools),
        # EMB-37: ToolSearch loads deferred tools into context at runtime,
        # which is how the QA agent reached browser_run_code_unsafe despite
        # its tools list. Denying the two ToolSearch server-tool names is
        # belt-and-suspenders (unknown names are a no-op on older CLIs);
        # the enforcing layer is the Ring-3 tool-name check in
        # embry0.safety.policy.evaluate_policy.
        disallowed_tools=["tool_search_tool_regex", "tool_search_tool_bm25"],
        permission_mode="bypassPermissions",
        max_turns=invocation.max_turns,
        cwd="/workspace",
        system_prompt=invocation.system_prompt if invocation.system_prompt else None,
        setting_sources=["project"],
        mcp_servers=dict(invocation.mcp_servers),
    )
