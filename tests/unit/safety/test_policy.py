"""SafetyPolicy dataclasses — immutability, field shape, simple constructors."""

import pytest

from embry0.safety.policy import (
    ContentRule,
    SafetyPolicy,
    Verdict,
    default_policy_for_agent,
    evaluate_policy,
    render_settings_json,
)


def test_safety_policy_is_frozen() -> None:
    policy = SafetyPolicy(
        allowed_tools=["Read"],
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
        content_checks=[ContentRule(pattern=r"rm\s+-rf\s+/", tools=["Bash"], reason="destructive rm")],
    )
    v = evaluate_policy(policy, "Bash", {"command": "rm -rf /"})
    assert v.allowed is False
    assert "destructive rm" in v.reason


def test_evaluate_policy_ignores_rule_for_unrelated_tool() -> None:
    policy = SafetyPolicy(
        content_checks=[ContentRule(pattern=r"rm\s+-rf\s+/", tools=["Bash"], reason="destructive")],
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


def test_default_policy_includes_workspace_deny_rules() -> None:
    policy = default_policy_for_agent("developer")
    assert any("/etc/" in r for r in policy.deny_rules)
    # ~/.claude/** covers the host's OAuth credentials file.
    assert any("~/.claude" in r for r in policy.deny_rules)


def test_default_policy_includes_dangerous_bash_content_checks() -> None:
    policy = default_policy_for_agent("developer")
    patterns_in_policy = {r.pattern for r in policy.content_checks}
    from embry0.safety.patterns import DANGEROUS_BASH_PATTERNS

    for p in DANGEROUS_BASH_PATTERNS:
        assert p in patterns_in_policy, f"missing pattern from default policy: {p}"


def test_default_policy_readonly_agent_omits_write_tools() -> None:
    policy = default_policy_for_agent("triage")
    assert "Write" not in policy.allowed_tools
    assert "Edit" not in policy.allowed_tools
    # Triage only needs Read/Glob/Grep
    assert "Read" in policy.allowed_tools


def test_render_settings_json_shape() -> None:
    """Only the deny rules: the CLI drops a project's allow rules in an untrusted workspace,
    and under bypassPermissions there is nothing for an allow rule or a default mode to do."""
    policy = SafetyPolicy(
        allowed_tools=["Read", "Bash"],
        deny_rules=["Read(/etc/**)"],
        content_checks=[],
        network_egress_allowlist=[],
    )
    rendered = render_settings_json(policy)
    assert rendered["permissions"] == {"deny": ["Read(/etc/**)"]}, (
        f"settings.json must carry the deny rules and nothing else, got {rendered['permissions']}"
    )
    assert "$schema" in rendered
    assert "claude-code-settings" in rendered["$schema"]


def test_render_settings_json_omits_empty_sections() -> None:
    policy = SafetyPolicy()
    rendered = render_settings_json(policy)
    assert rendered["permissions"] == {"deny": []}, f"got {rendered['permissions']}"


def test_default_policy_unknown_agent_falls_back_to_developer_tools() -> None:
    """Unknown agent types get the developer tool set (broad but scoped)."""
    unknown = default_policy_for_agent("nonexistent_agent_type")
    developer = default_policy_for_agent("developer")
    assert unknown.allowed_tools == developer.allowed_tools


def test_baseline_denies_workspace_claude_settings_writes():
    pol = default_policy_for_agent("developer")
    assert "Write(//workspace/.claude/settings*.json)" in pol.deny_rules
    assert "Edit(//workspace/.claude/settings*.json)" in pol.deny_rules


def test_baseline_denies_glob_grep_for_host_paths():
    pol = default_policy_for_agent("triage")
    for path in ("//etc/**", "//root/**", "~/.claude/**", "~/.ssh/**", "~/.aws/**", "~/.gnupg/**", "//proc/**"):
        assert f"Glob({path})" in pol.deny_rules
        assert f"Grep({path})" in pol.deny_rules


# ---------------------------------------------------------------------------
# EMB-37: default-deny tool-name enforcement
# ---------------------------------------------------------------------------


def test_evaluate_policy_denies_tool_not_in_allowlist() -> None:
    policy = SafetyPolicy(allowed_tools=["Read", "Bash"])
    v = evaluate_policy(policy, "mcp__playwright__browser_run_code_unsafe", {"code": "1"})
    assert v.allowed is False
    assert "allowlist" in v.reason


def test_evaluate_policy_allows_tool_in_allowlist() -> None:
    policy = SafetyPolicy(allowed_tools=["Read", "mcp__playwright__browser_navigate"])
    v = evaluate_policy(policy, "mcp__playwright__browser_navigate", {"url": "http://x"})
    assert v.allowed is True


def test_evaluate_policy_exempts_harness_internal_tools() -> None:
    """TodoWrite/Skill/SlashCommand are CLI-harness tools agent tool lists
    never enumerate — denying them burns turns for zero safety value."""
    policy = SafetyPolicy(allowed_tools=["Read"])
    for name in ("TodoWrite", "Skill", "SlashCommand"):
        assert evaluate_policy(policy, name, {}).allowed is True


def test_evaluate_policy_unset_allowlist_skips_name_check() -> None:
    """No allowlist configured (None) -> name enforcement off (content checks only)."""
    policy = SafetyPolicy(allowed_tools=None)
    v = evaluate_policy(policy, "AnythingGoes", {})
    assert v.allowed is True, f"an unset allowlist must check no names: {v.reason}"


def test_evaluate_policy_content_checks_still_fire_for_allowed_names() -> None:
    policy = SafetyPolicy(
        allowed_tools=["Bash"],
        content_checks=[ContentRule(pattern=r"rm\s+-rf\s+/", tools=["Bash"], reason="destructive rm")],
    )
    v = evaluate_policy(policy, "Bash", {"command": "rm -rf /"})
    assert v.allowed is False
    assert "destructive rm" in v.reason


def test_evaluate_policy_empty_allowlist_allows_no_tool() -> None:
    """An empty list is no tools; only the CLI's own (TodoWrite, Skill) stay callable."""
    policy = SafetyPolicy(allowed_tools=[])
    assert not evaluate_policy(policy, "Bash", {"command": "true"}).allowed, "an empty allowlist allowed Bash"
    assert evaluate_policy(policy, "TodoWrite", {}).allowed, "the CLI's own tools stay callable"
