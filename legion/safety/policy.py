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
from typing import Any


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
    for consumption by sandbox container setup.
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


def evaluate_policy(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
) -> Verdict:
    """Evaluate a single tool call against Ring-3 content checks.

    Returns Verdict.allow() if no rule fires. Returns Verdict.deny(reason)
    if any matching rule hits. On any internal exception, returns
    Verdict.deny("safety hook failed: ...") — fail-closed.
    """
    try:
        haystack = _normalize(_stringify_input(tool_input))
        for rule in policy.content_checks:
            if rule.tools and tool_name not in rule.tools:
                continue
            if re.search(rule.pattern, haystack, re.IGNORECASE):
                return Verdict.deny(f"Blocked by safety policy: {rule.reason}")
        return Verdict.allow()
    except Exception as exc:
        return Verdict.deny(f"safety hook failed: {exc!r}")
