"""Safety policy as data — the single source of truth for agent tool gating.

Three concentric rings defend the sandbox:

1. Ring 1 (container): Docker isolation — ephemeral, non-root, network-restricted.
   Unchanged by this module; documented here as context.
2. Ring 2 (declarative): allow_rules / deny_rules rendered into .claude/settings.json.
   Claude Code enforces these before tool dispatch.
3. Ring 3 (programmable): content_checks — regex-on-input rules this module
   evaluates via evaluate_policy(). Used when Ring 2 can't express a check.

Fail-closed: if evaluate_policy raises or returns something malformed, the tool
is denied. A hook that errors silently and allows the tool through is a
security bug.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Final


@dataclass(frozen=True)
class ContentRule:
    """A Ring-3 regex pattern evaluated against a tool's input string.

    - pattern: raw regex string (searched case-insensitively, normalized input).
    - tools: list of tool names this rule applies to; empty means all tools.
    - reason: human-readable explanation shown to Claude when the rule fires.
    """

    pattern: str
    tools: list[str]
    reason: str


@dataclass(frozen=True)
class Verdict:
    """Result of evaluating a policy against a single tool call."""

    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> Verdict:
        return cls(allowed=True, reason="")

    @classmethod
    def deny(cls, reason: str) -> Verdict:
        return cls(allowed=False, reason=reason)


@dataclass(frozen=True)
class SafetyPolicy:
    """Full policy struct consumed by both executors.

    allowed_tools is passed to ClaudeAgentOptions.allowed_tools (SDK) or
    --allowedTools (CLI). allow/deny rules are rendered into a settings.json.
    content_checks are evaluated via evaluate_policy() at hook time.
    network_egress_allowlist is not enforced by this module — it is produced
    for consumption by sandbox container setup. EMB-29 enforces it for
    egress-enabled QA sandboxes via per-sandbox DOCKER-USER rules in DinD
    (see embry0/execution/egress.py); deployed-target QA derives its
    allowlist automatically from frontend_url + profile extra_hosts.
    """

    allowed_tools: list[str] = field(default_factory=list)
    allow_rules: list[str] = field(default_factory=list)
    deny_rules: list[str] = field(default_factory=list)
    content_checks: list[ContentRule] = field(default_factory=list)
    network_egress_allowlist: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    """NFKC-normalize and collapse whitespace to defeat obfuscation."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"/(?:usr/)?(?:local/)?(?:s?bin)/", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _stringify_input(tool_input: dict[str, Any]) -> str:
    """Flatten all input values into one scanable string."""
    parts: list[str] = []
    for value in tool_input.values():
        try:
            parts.append(str(value))
        except Exception:
            continue
    return " ".join(parts)


# Harness-internal tools the Claude CLI drives itself (todo tracking, skill
# and slash-command invocation). Agent tool lists never enumerate them, so
# the EMB-37 name check must exempt them — denying them burns agent turns
# for zero safety value (they touch no external surface).
_INFRA_ALLOWED_TOOLS: Final[frozenset[str]] = frozenset({"TodoWrite", "Skill", "SlashCommand"})


def evaluate_policy(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
) -> Verdict:
    """Evaluate a single tool call against the tool-name allowlist and the
    Ring-3 content checks.

    Name check (EMB-37): when ``policy.allowed_tools`` is non-empty, any
    tool name outside it (and outside ``_INFRA_ALLOWED_TOOLS``) is denied.
    This is the enforcement layer that survives runtime tool loading
    (ToolSearch/deferred tools) — the SDK's ``allowed_tools`` option is an
    auto-approve list, not an availability gate, so without this check an
    agent can call tools it discovered at runtime. An empty
    ``allowed_tools`` skips name enforcement (content checks only).

    Returns Verdict.allow() if no rule fires. Returns Verdict.deny(reason)
    if any matching rule hits. On any internal exception, returns
    Verdict.deny("safety hook failed: ...") — fail-closed.
    """
    try:
        if policy.allowed_tools and tool_name not in policy.allowed_tools and tool_name not in _INFRA_ALLOWED_TOOLS:
            return Verdict.deny(f"tool {tool_name!r} is not in this agent's allowlist")
        haystack = _normalize(_stringify_input(tool_input))
        for rule in policy.content_checks:
            if rule.tools and tool_name not in rule.tools:
                continue
            if re.search(rule.pattern, haystack, re.IGNORECASE):
                return Verdict.deny(f"Blocked by safety policy: {rule.reason}")
        return Verdict.allow()
    except Exception as exc:
        return Verdict.deny(f"safety hook failed: {exc!r}")


# ---- Default policies and rendering ----

# Ring-2 baseline: deny host-sensitive reads/writes globally.
_BASELINE_DENY_RULES: list[str] = [
    "Read(/etc/**)",
    "Read(/root/**)",
    "Read(~/.claude/**)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.gnupg/**)",
    "Read(/proc/**)",
    "Write(/etc/**)",
    "Write(/root/**)",
    "Write(/usr/**)",
    "Write(/bin/**)",
    "Write(/sbin/**)",
    "Edit(/etc/**)",
    "Edit(/root/**)",
    "Edit(/usr/**)",
    "Edit(/bin/**)",
    "Edit(/sbin/**)",
    # Sandbox must not rewrite its own permission file
    "Write(/workspace/.claude/**)",
    "Edit(/workspace/.claude/**)",
    # Cover Glob/Grep for the host-sensitive paths the existing Read denials cover
    "Glob(/etc/**)",
    "Glob(/root/**)",
    "Glob(~/.claude/**)",
    "Glob(~/.ssh/**)",
    "Glob(~/.aws/**)",
    "Glob(~/.gnupg/**)",
    "Glob(/proc/**)",
    "Grep(/etc/**)",
    "Grep(/root/**)",
    "Grep(~/.claude/**)",
    "Grep(~/.ssh/**)",
    "Grep(~/.aws/**)",
    "Grep(~/.gnupg/**)",
    "Grep(/proc/**)",
]

# Ring-2 baseline: allow workspace ops and common developer commands.
_BASELINE_ALLOW_RULES: list[str] = [
    "Read(/workspace/**)",
    "Write(/workspace/**)",
    "Edit(/workspace/**)",
    "Glob(/workspace/**)",
]

# Agent-type → tools mapping (matches current developer/triage/review semantics).
_AGENT_TOOLS: dict[str, list[str]] = {
    "triage": ["Read", "Glob", "Grep"],
    "developer": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    "review": ["Read", "Glob", "Grep", "Bash"],
}


def default_policy_for_agent(agent_type: str) -> SafetyPolicy:
    """Baseline safety policy for a given agent type.

    Encodes today's production behavior:
    - Ring 2 deny: host-sensitive paths (credentials, /etc, /root, etc.).
    - Ring 2 allow: /workspace/** for file operations.
    - Ring 3 content checks: every pattern in DANGEROUS_BASH_PATTERNS.

    Callers may layer additional rules on top (e.g., per-repo or per-pipeline
    overrides); this factory is the irreducible baseline.
    """
    from embry0.safety.patterns import DANGEROUS_BASH_PATTERNS

    content_checks = [
        ContentRule(pattern=p, tools=["Bash"], reason=f"dangerous bash pattern: {p}") for p in DANGEROUS_BASH_PATTERNS
    ]
    tools = _AGENT_TOOLS.get(agent_type, _AGENT_TOOLS["developer"])
    return SafetyPolicy(
        allowed_tools=tools,
        allow_rules=list(_BASELINE_ALLOW_RULES),
        deny_rules=list(_BASELINE_DENY_RULES),
        content_checks=content_checks,
        network_egress_allowlist=[],
    )


def render_settings_json(policy: SafetyPolicy) -> dict[str, Any]:
    """Serialize a SafetyPolicy into a Claude Code settings.json dict.

    Ring-2 enforcement: the rendered file is written into /workspace/.claude/settings.json
    by the executor before invoking Claude. Claude Code reads permissions.allow/deny
    first and enforces them before tool dispatch.
    """
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "defaultMode": "default",
            "allow": list(policy.allow_rules),
            "deny": list(policy.deny_rules),
        },
    }
