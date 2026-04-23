"""SafetyPolicy dataclasses — immutability, field shape, simple constructors."""

import pytest

from legion.safety.policy import ContentRule, SafetyPolicy, Verdict, evaluate_policy


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


def test_evaluate_policy_allows_when_no_rules() -> None:
    policy = SafetyPolicy()
    v = evaluate_policy(policy, "Bash", {"command": "echo hi"})
    assert v.allowed is True


def test_evaluate_policy_denies_matching_pattern() -> None:
    policy = SafetyPolicy(
        content_checks=[
            ContentRule(pattern=r"rm\s+-rf\s+/", tools=["Bash"], reason="destructive rm")
        ],
    )
    v = evaluate_policy(policy, "Bash", {"command": "rm -rf /"})
    assert v.allowed is False
    assert "destructive rm" in v.reason


def test_evaluate_policy_ignores_rule_for_unrelated_tool() -> None:
    policy = SafetyPolicy(
        content_checks=[
            ContentRule(pattern=r"rm\s+-rf\s+/", tools=["Bash"], reason="destructive")
        ],
    )
    v = evaluate_policy(policy, "Read", {"file_path": "rm -rf /"})
    assert v.allowed is True


def test_evaluate_policy_empty_tool_filter_matches_all_tools() -> None:
    policy = SafetyPolicy(
        content_checks=[ContentRule(pattern=r"secret", tools=[], reason="no secrets")],
    )
    v = evaluate_policy(policy, "Write", {"file_path": "my_secret.txt"})
    assert v.allowed is False


def test_evaluate_policy_case_insensitive() -> None:
    policy = SafetyPolicy(
        content_checks=[ContentRule(pattern=r"RM -RF", tools=["Bash"], reason="x")],
    )
    v = evaluate_policy(policy, "Bash", {"command": "rm -rf /"})
    assert v.allowed is False


def test_evaluate_policy_fails_closed_on_exception(monkeypatch) -> None:
    """If regex evaluation raises, deny the tool (fail-closed)."""
    import re

    def broken_search(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(re, "search", broken_search)
    policy = SafetyPolicy(
        content_checks=[ContentRule(pattern=r".*", tools=["Bash"], reason="test")],
    )
    v = evaluate_policy(policy, "Bash", {"command": "echo hi"})
    assert v.allowed is False
    assert "hook" in v.reason.lower() or "fail" in v.reason.lower()


def test_evaluate_policy_handles_non_string_tool_input() -> None:
    """tool_input values that aren't strings must not crash evaluation."""
    policy = SafetyPolicy(
        content_checks=[ContentRule(pattern=r"x", tools=["Bash"], reason="x")],
    )
    v = evaluate_policy(policy, "Bash", {"command": 12345})
    # Integer coerced to string "12345" — pattern "x" doesn't match — allow.
    assert v.allowed is True
