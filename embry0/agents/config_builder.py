"""Pure AgentInvocation → ClaudeAgentOptions translator for SDK mode.

Deterministic: given a fixed AgentInvocation, returns a fully-populated
ClaudeAgentOptions with no I/O. This function contains every SDK-specific
translation decision; any future SDK option change should land here.

Phase 1 only implements build_sdk_options(). build_cli_args() arrives in Phase 2.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from embry0.agents.invocation import AgentInvocation


def _always_loaded(server: Any) -> Any:
    """An MCP server config whose tools the CLI never defers."""
    return {**server, "alwaysLoad": True} if isinstance(server, dict) else server


def build_sdk_options(invocation: AgentInvocation) -> ClaudeAgentOptions:
    """Render an AgentInvocation into a ClaudeAgentOptions for the Agent SDK.

    - allowed_tools: from invocation.tools.
    - model: from invocation.model.
    - permission_mode: always 'bypassPermissions' (sandbox is the real boundary;
      Ring 2 permissions.deny and Ring 3 hook still run).
    - max_turns / cwd: fixed to embry0's sandbox conventions.
    - system_prompt: invocation.system_prompt wrapped as a claude_code
      preset-APPEND (None when empty to let the SDK apply its default). A
      plain string would REPLACE the CLI's default system prompt and strip
      its tool-use guidance; append preserves it, and the combined system
      block participates in the CLI's prompt caching (EMB-35).
    - setting_sources: ["project"] so the SDK loads /workspace/.claude/settings.json
      for Ring 2 permissions and /workspace/.claude/skills/** for skill files.
    - plugins: the mounted skill sources (invocation.skill_mounts) whose
      plugin name prefixes a skill in invocation.skills; none when it is empty.
    - skills: invocation.skills as the allowlist over the plugins' skills and
      the repository's own; None (no filter) when the list is empty.
    - mcp_servers: from invocation.mcp_servers, each marked ``alwaysLoad``
      (see the comment at the field).
    - env: not set here — Anthropic auth rides the sandbox container env
      (injected by SandboxManager at create) and the CLI subprocess
      inherits it; provider-routed agents (grok) get a per-agent env
      overlay in SdkAgentExecutor.run instead.

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
        system_prompt=(
            {"type": "preset", "preset": "claude_code", "append": invocation.system_prompt}
            if invocation.system_prompt
            else None
        ),
        setting_sources=["project"],
        # Spec 3.3: a mounted skill source is a local plugin, loaded only for
        # an agent that lists one of its skills: a plugin brings its hooks too
        # (a SessionStart hook injects context into every session). The
        # effective skill list is the allowlist. None, not [], when the list is
        # empty: [] hides every skill, including the repository's own under
        # /workspace/.claude/skills, which setting_sources=["project"] loads.
        plugins=[
            {"type": "local", "path": m["container_path"]}
            for m in invocation.skill_mounts
            if any(skill.startswith(m["plugin_name"] + ":") for skill in invocation.skills)
        ],
        skills=list(invocation.skills) if invocation.skills else None,
        # #501: alwaysLoad keeps every tool of the server in the prompt, never
        # deferred behind tool search. ENABLE_TOOL_SEARCH=false (below) already
        # stops deferral at start-up, and the first turn waits for the servers
        # (measured on CLI 2.1.292: a server 20s late still made turn 1). But
        # since 2.1.267 tools that appear mid-session, as a server reconnects,
        # arrive DEFERRED when ToolSearch is off, which is #240's failure: the
        # agent cannot load them. alwaysLoad servers are exempt (2.1.269).
        mcp_servers={name: _always_loaded(server) for name, server in invocation.mcp_servers.items()},
        # #240: the CLI's MCP tool-search feature DEFERS MCP tool schemas
        # behind a ToolSearch call when an agent's tool count crosses a
        # (server-side, flag-gated) threshold — observed flipping on
        # between 2026-09-03 and 2026-09-04 on CLI 2.1.92: the QA agent's
        # 54 tools (5 base + 49 vibium) started deferring while playwright's
        # 27 never did. embry0 denies ToolSearch by design (EMB-37 above +
        # Ring-3), so deferred tools become PERMANENTLY unreachable — the
        # vibium QA agent silently lost its whole browser. Deferral is
        # incompatible with the exact-allowlist model: force it off for
        # every SDK agent. options.env OVERLAYS the inherited container env
        # (claude_agent_sdk subprocess_cli), so auth vars are unaffected.
        env={"ENABLE_TOOL_SEARCH": "false"},
        # #237: one stream-json line per message, and a browser tool result
        # with inline base64 (vibium browser_screenshot without a filename
        # measured 544KB on a trivial page; real pages exceed 1MB) blows the
        # SDK's 1MB default line buffer — the transport dies mid-run and the
        # CLI's pending hook callbacks fail with "Stream closed"
        # (job-d0e3d0ab5a35). 16MB matches mcp_client._STREAM_LIMIT, chosen
        # there for the same reason (browser payloads run to hundreds of KB).
        max_buffer_size=16 * 1024 * 1024,
    )
