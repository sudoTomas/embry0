"""SafetyPolicy dataclasses — immutability, field shape, simple constructors."""

import pytest

from legion.safety.policy import ContentRule, SafetyPolicy, Verdict


def test_safety_policy_is_frozen() -> None:
    policy = SafetyPolicy(
        allowed_tools=["Read"],
        allow_rules=[],
        deny_rules=[],
        content_checks=[],
        network_egress_allowlist=[],
    )
    with pytest.raises(Exception):
        policy.allowed_tools = ["Write"]  # type: ignore[misc]


def test_content_rule_is_frozen() -> None:
    rule = ContentRule(
        pattern=r"rm\s+-rf\s+/",
        tools=["Bash"],
        reason="destructive",
    )
    with pytest.raises(Exception):
        rule.pattern = "x"  # type: ignore[misc]


def test_verdict_allow_and_deny_helpers() -> None:
    allow = Verdict.allow()
    assert allow.allowed is True
    assert allow.reason == ""

    deny = Verdict.deny("blocked")
    assert deny.allowed is False
    assert deny.reason == "blocked"
