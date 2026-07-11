"""In-sandbox safety adapter — thin wrapper around evaluate_policy.

Workspace-boundary rules have moved to Ring 2 (declarative settings.json).
This module now only adapts the Ring-3 content-checks path for call sites
that need a boolean/reason result shaped like the legacy hook contract.

Call sites should prefer evaluate_policy(policy, ...) directly when they
already have a SafetyPolicy in hand. This wrapper exists for backward
compatibility with the in-sandbox PreToolUse hook integration.
"""

from __future__ import annotations

from typing import Any

from embry0.safety.policy import SafetyPolicy, default_policy_for_agent, evaluate_policy


def check_tool_safety(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: SafetyPolicy | None = None,
    agent_type: str = "developer",
) -> dict[str, str] | None:
    """Evaluate a tool call. Returns a denial dict if unsafe, None if safe.

    Compatibility shape: returns None on allow; returns
    {"decision": "deny", "reason": ...} on deny, matching the legacy hook
    response shape. Callers emitting events should translate this into a
    TOOL_DENIED event separately.
    """
    effective_policy = policy if policy is not None else default_policy_for_agent(agent_type)
    verdict = evaluate_policy(effective_policy, tool_name, tool_input)
    if verdict.allowed:
        return None
    return {"decision": "deny", "reason": verdict.reason}
