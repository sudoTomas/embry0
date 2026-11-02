"""Ring-2 rule paths mean what Claude Code says they mean (#508).

Claude Code anchors a permission-rule path by its prefix: ``//p`` is the
filesystem root, ``~/p`` the home directory, and a single ``/p`` the PROJECT
ROOT (the directory the CLI runs in). The baseline wrote ``Read(/etc/**)``
meaning /etc; the CLI read it as /workspace/etc/**, so /etc, /proc and the
sandbox's own settings.json were open to every agent, while the direct
executor's evaluate_ring2_paths read the same rule as /etc and disagreed.

test_ring2_cli_parity.py checks the same table against the real CLI.
"""

from __future__ import annotations

import inspect
import re

import pytest

from embry0.safety import policy as policy_module
from embry0.safety.policy import (
    SafetyPolicy,
    default_policy_for_agent,
    evaluate_ring2_paths,
    render_settings_json,
)

HOME = "/home/agent"
AGENT_TYPES = ("triage", "developer", "review", "qa")
_PATH_RULE = re.compile(r"^(Read|Write|Edit|Glob|Grep)\((.*)\)$")


def _path_rules(rules: list[str]) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in map(_PATH_RULE.match, rules) if m]


# ---- the rendered settings.json -----------------------------------------


@pytest.mark.parametrize("agent_type", AGENT_TYPES)
def test_rendered_rules_root_absolute_paths_with_a_double_slash(agent_type: str) -> None:
    """A single leading ``/`` is project-relative to the CLI, so no rule it receives may use one."""
    rendered = render_settings_json(default_policy_for_agent(agent_type))["permissions"]["deny"]
    rooted_at_project = [
        f"{tool}({path})" for tool, path in _path_rules(rendered) if path.startswith("/") and not path.startswith("//")
    ]
    assert not rooted_at_project, (
        f"{agent_type}: these deny rules are relative to the project root in Claude Code, "
        f"not absolute; write them with '//': {rooted_at_project}"
    )


def test_rendered_deny_rules_name_the_host_paths_they_guard() -> None:
    deny = render_settings_json(default_policy_for_agent("triage"))["permissions"]["deny"]
    for rule in ("Read(//etc/**)", "Read(//proc/**)", "Read(//root/**)", "Edit(//workspace/.claude/settings*.json)"):
        assert rule in deny, f"settings.json must carry {rule}; it has {deny}"


def test_every_rule_the_cli_ignores_is_backed_by_one_it_enforces() -> None:
    """Claude Code skips the path in Write/Glob/Grep rules; a Read or Edit rule must carry each one."""
    rules = _path_rules(default_policy_for_agent("developer").deny_rules)
    read_paths = {path for tool, path in rules if tool == "Read"}
    edit_paths = {path for tool, path in rules if tool == "Edit"}
    unbacked = [
        f"{tool}({path})"
        for tool, path in rules
        if tool in ("Write", "Glob", "Grep") and path not in read_paths and not (tool == "Write" and path in edit_paths)
    ]
    assert not unbacked, (
        f"the CLI enforces none of these rules and no Read/Edit rule on the same path covers them: {unbacked}"
    )


# ---- the one reading of a rule path -------------------------------------


@pytest.mark.parametrize(
    ("rule_path", "absolute"),
    [
        ("//etc/**", "/etc/**"),
        ("//workspace/.claude/**", "/workspace/.claude/**"),
        ("~/.ssh/**", f"{HOME}/.ssh/**"),
        ("/etc/**", "/workspace/etc/**"),  # the #508 misreading: project root, not /
    ],
)
def test_rule_path_to_absolute_anchors_like_claude_code(rule_path: str, absolute: str) -> None:
    to_absolute = getattr(policy_module, "rule_path_to_absolute", None)
    assert to_absolute is not None, "embry0.safety.policy.rule_path_to_absolute must own rule-path anchoring"
    assert to_absolute(rule_path, cwd="/workspace", home=HOME) == absolute


@pytest.mark.parametrize("rule_path", ["*.pem", "src/**", "./src/**", "~"])
def test_rule_path_to_absolute_refuses_an_unanchored_path(rule_path: str) -> None:
    to_absolute = getattr(policy_module, "rule_path_to_absolute", None)
    assert to_absolute is not None, "embry0.safety.policy.rule_path_to_absolute must own rule-path anchoring"
    with pytest.raises(ValueError, match="not anchored"):
        to_absolute(rule_path, cwd="/workspace", home=HOME)


# ---- evaluate_ring2_paths reads rules the same way ------------------------

# (tool, tool_input, refused). The verdicts the CLI gives for the baseline rules;
# test_ring2_cli_parity.py measures them on the real CLI.
TABLE: list[tuple[str, dict[str, object], bool]] = [
    # host paths the #508 rules never matched in the CLI
    ("Read", {"file_path": "/proc/self/environ"}, True),
    ("Grep", {"pattern": "TOKEN", "path": "/proc/self/environ"}, True),
    ("Read", {"file_path": "/etc/hostname"}, True),
    ("Glob", {"pattern": "*", "path": "/etc"}, True),
    ("Glob", {"pattern": "/etc/host*"}, True),
    ("Write", {"file_path": "/workspace/.claude/settings.json", "content": "{}"}, True),
    ("Edit", {"file_path": "/workspace/.claude/settings.json", "old_string": "a", "new_string": "b"}, True),
    ("Write", {"file_path": "/workspace/.claude/settings.local.json", "content": "{}"}, True),
    ("Edit", {"file_path": "/workspace/.claude/settings.local.json", "old_string": "a", "new_string": "b"}, True),
    ("Write", {"file_path": "/usr/local/bin/tool", "content": "x"}, True),
    # a Read rule refuses writes too
    ("Write", {"file_path": f"{HOME}/.ssh/authorized_keys", "content": "x"}, True),
    ("Edit", {"file_path": f"{HOME}/.claude/settings.json", "old_string": "a", "new_string": "b"}, True),
    # the agent's own work stays open
    ("Read", {"file_path": "/workspace/README.md"}, False),
    ("Read", {"file_path": "/workspace/.claude/settings.json"}, False),
    # the rest of .claude/ is the repo's own code
    ("Write", {"file_path": "/workspace/.claude/skills/x/SKILL.md", "content": "x"}, False),
    ("Edit", {"file_path": "/workspace/.claude/skills/x/SKILL.md", "old_string": "a", "new_string": "b"}, False),
    ("Write", {"file_path": "/workspace/.claude/commands/y.md", "content": "x"}, False),
    ("Write", {"file_path": "/workspace/.claude/agents/z.md", "content": "x"}, False),
    ("Write", {"file_path": "/workspace/CLAUDE.md", "content": "x"}, False),
    ("Read", {"file_path": "/workspace/etc/notes.txt"}, False),  # what the old rule actually denied
    ("Read", {"file_path": "/workspace/proc/notes.txt"}, False),
    ("Read", {"file_path": "/tmp/scratch.txt"}, False),
    ("Write", {"file_path": "/tmp/scratch.txt", "content": "x"}, False),
    ("Write", {"file_path": "/workspace/src/app.py", "content": "x"}, False),
    ("Edit", {"file_path": "/workspace/src/app.py", "old_string": "a", "new_string": "b"}, False),
    ("Write", {"file_path": "/workspace/.qa/screenshots/home.png", "content": "x"}, False),
    ("Write", {"file_path": "/workspace/deliverables/report.md", "content": "x"}, False),
    ("Glob", {"pattern": "**/*.py", "path": "/workspace"}, False),
    ("Grep", {"pattern": "def ", "path": "/workspace"}, False),
]


@pytest.mark.parametrize(("tool", "tool_input", "refused"), TABLE)
def test_evaluate_ring2_paths_matches_the_cli_on_the_baseline(
    tool: str, tool_input: dict[str, object], refused: bool
) -> None:
    verdict = evaluate_ring2_paths(default_policy_for_agent("developer"), tool, tool_input, home=HOME)
    assert verdict.allowed is not refused, (
        f"{tool} {tool_input}: the CLI {'refuses' if refused else 'allows'} it, evaluate_ring2_paths said {verdict}"
    )


def test_a_single_slash_rule_is_anchored_at_the_project_root() -> None:
    pol = SafetyPolicy(deny_rules=["Read(/secrets/**)"])
    inside = evaluate_ring2_paths(pol, "Read", {"file_path": "/workspace/secrets/key"}, home=HOME)
    outside = evaluate_ring2_paths(pol, "Read", {"file_path": "/secrets/key"}, home=HOME)
    assert not inside.allowed, f"Read(/secrets/**) is /workspace/secrets/** in Claude Code; got {inside}"
    assert outside.allowed, f"Read(/secrets/**) does not reach /secrets in Claude Code; got {outside}"


def test_an_unanchored_rule_fails_closed() -> None:
    pol = SafetyPolicy(deny_rules=["Read(*.pem)"])
    verdict = evaluate_ring2_paths(pol, "Read", {"file_path": "/workspace/README.md"}, home=HOME)
    assert not verdict.allowed, f"a rule the predicate cannot read as the CLI does must deny; got {verdict}"
    assert "not anchored" in verdict.reason


def test_a_single_slash_rule_stays_at_the_project_root_when_the_session_moves() -> None:
    """A cd moves where relative paths resolve, not where a project-anchored rule points."""
    assert "project_root" in inspect.signature(evaluate_ring2_paths).parameters, (
        "evaluate_ring2_paths must take the project root apart from the directory the call runs in"
    )
    pol = SafetyPolicy(deny_rules=["Read(/secrets/**)"])
    moved = evaluate_ring2_paths(
        pol, "Read", {"file_path": "../secrets/key"}, cwd="/workspace/src", home=HOME, project_root="/workspace"
    )
    assert not moved.allowed, f"Read(/secrets/**) is /workspace/secrets/** wherever the session is; got {moved}"
    elsewhere = evaluate_ring2_paths(
        pol, "Read", {"file_path": "secrets/key"}, cwd="/workspace/src", home=HOME, project_root="/workspace"
    )
    assert elsewhere.allowed, f"/workspace/src/secrets is not under the rule; got {elsewhere}"
