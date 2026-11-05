"""Tool overrides resolved at run time are checked against the tool catalog.

A definition is checked when it is written, but a template node and triage
(``agent_tools``) can name any tool. The operator's list (the definition's or
the template node's) loses every name embry0 does not gate, logged, before
triage narrows it; triage, which can only narrow, then picks among the tools
the run can have. No agent is offered an ungated tool; the job still runs
with the rest.
"""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from embry0.agents.resolver import resolve_agent_invocation


def _args(**over):  # noqa: ANN003, ANN202
    args = {
        "agent_type": "developer",
        "prompt": "do stuff",
        "system_context": "",
        "global_defaults": {"execution_mode": "sdk", "auth_mode": "oauth"},
        "repo_prefs": None,
        "job_overrides": None,
        "agent_definition": {
            "model": "claude-sonnet-4-6",
            "tools": ["Read", "Write", "Edit", "Bash"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {"tracker": {"command": "npx", "args": ["tracker-mcp"]}},
            "execution_mode": None,
            "auth_mode": None,
        },
        "pipeline_config": {},
        "max_turns": 10,
        "timeout_seconds": 60,
        "credentials": {"api_key": "", "oauth_token": "oat-000000000001"},
    }
    args.update(over)
    return args


def _dropped(captured: list[dict]) -> list[str]:
    return [t for e in captured if e.get("event") == "agent_tools_dropped" for t in e.get("tools", [])]


def test_a_template_node_cannot_grant_a_tool_embry0_does_not_gate() -> None:
    structlog.reset_defaults()
    with capture_logs() as captured:
        inv = resolve_agent_invocation(
            **_args(template_config={"agent_tools": {"developer": ["Read", "Monitor", "Workflow", "mcp__tracker__x"]}})
        )
    assert inv.tools == ["Read", "mcp__tracker__x"], f"the resolved tools kept an ungated tool: {inv.tools}"
    assert _dropped(captured) == ["Monitor", "Workflow"], f"the drop must be logged: {captured}"


def _not_granted(captured: list[dict]) -> list[str]:
    return [t for e in captured if e.get("event") == "triage_tools_not_granted" for t in e.get("tools", [])]


def test_triage_cannot_grant_a_tool_embry0_does_not_gate() -> None:
    structlog.reset_defaults()
    with capture_logs() as captured:
        inv = resolve_agent_invocation(
            **_args(pipeline_config={"agent_tools": {"developer": ["Bash", "EnterWorktree", "mcp__elsewhere__run"]}})
        )
    assert inv.tools == ["Bash"], f"the resolved tools kept an ungated tool: {inv.tools}"
    assert _not_granted(captured) == ["EnterWorktree", "mcp__elsewhere__run"], captured


def test_the_operators_ungated_tools_are_dropped_before_triage_narrows() -> None:
    """Triage asking only for a dropped tool keeps none of the list, so the read-only tools stay."""
    template = {"agent_tools": {"developer": ["Read", "Bash", "Monitor"]}}
    structlog.reset_defaults()
    with capture_logs() as captured:
        inv = resolve_agent_invocation(
            **_args(template_config=template, pipeline_config={"agent_tools": {"developer": ["Monitor"]}})
        )
    assert _dropped(captured) == ["Monitor"], f"the operator's ungated tool must be dropped and logged: {captured}"
    assert inv.tools == ["Read"], (
        f"triage chose among tools the run cannot have: {inv.tools}; a request that keeps none of the "
        "operator's gated tools leaves their read-only ones"
    )


def test_triage_narrows_by_the_catalogs_names() -> None:
    """Task is the subagent tool's older name, on either side of the narrowing."""
    for operator, asked in ((["Read", "Task"], ["Task"]), (["Read", "Agent"], ["Task"]), (["Read", "Task"], ["Agent"])):
        inv = resolve_agent_invocation(
            **_args(
                template_config={"agent_tools": {"developer": operator}},
                pipeline_config={"agent_tools": {"developer": asked}},
            )
        )
        assert inv.tools == ["Agent"], f"the operator's {operator} narrowed to {asked} gave {inv.tools}"


def test_an_older_tool_name_resolves_to_the_catalog_name() -> None:
    inv = resolve_agent_invocation(**_args(template_config={"agent_tools": {"developer": ["Read", "Task"]}}))
    assert inv.tools == ["Read", "Agent"], f"Task is the subagent tool's older name: {inv.tools}"


def test_catalog_tools_pass_unchanged() -> None:
    inv = resolve_agent_invocation(**_args())
    assert inv.tools == ["Read", "Write", "Edit", "Bash"]
