"""Tests for the Ring-2 path predicate the direct-xAI executor uses (EMB-45).

This reproduces the deny-rule enforcement Claude Code does from settings.json;
a gap here is a sandbox-escape surface, so the deny paths are exercised hard.
"""

from __future__ import annotations

import pytest

from embry0.safety.policy import (
    _glob_to_regex,
    default_policy_for_agent,
    evaluate_ring2_paths,
    gate_tool_call,
)

HOME = "/home/agent"


def _dev_policy():
    return default_policy_for_agent("developer")


# ---- glob translation ----------------------------------------------------


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("/etc/**", "/etc/passwd", True),
        ("/etc/**", "/etc", True),  # trailing /** also matches the dir itself
        ("/etc/**", "/etc/ssl/certs/ca.pem", True),
        ("/etc/**", "/etcetera/x", False),  # must not match a sibling prefix
        ("/workspace/**", "/workspace/src/main.py", True),
        ("/workspace/.claude/**", "/workspace/.claude/settings.json", True),
        ("/workspace/.claude/**", "/workspace/src/app.py", False),
    ],
)
def test_glob_to_regex(glob, path, matches):
    import re

    assert bool(re.match(f"^{_glob_to_regex(glob)}$", path)) is matches


# ---- deny enforcement ----------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", "/etc/passwd"),
        ("Read", "/root/.bashrc"),
        ("Read", "/proc/self/environ"),
        ("Write", "/etc/hosts"),
        ("Write", "/usr/bin/evil"),
        ("Write", "/bin/sh"),
        ("Edit", "/root/x"),
        ("Write", "/workspace/.claude/settings.json"),
        ("Edit", "/workspace/.claude/settings.json"),
    ],
)
def test_denied_host_paths(tool, path):
    v = evaluate_ring2_paths(_dev_policy(), tool, {"file_path": path}, home=HOME)
    assert not v.allowed, f"{tool} {path} should be denied"


def test_denied_home_paths_expand_tilde():
    pol = _dev_policy()
    for tool, path in [
        ("Read", f"{HOME}/.ssh/id_rsa"),
        ("Read", f"{HOME}/.aws/credentials"),
        ("Read", f"{HOME}/.claude/.credentials.json"),
        ("Grep", f"{HOME}/.ssh"),
        ("Glob", f"{HOME}/.gnupg"),
    ]:
        v = evaluate_ring2_paths(pol, tool, {"file_path": path, "path": path}, home=HOME)
        assert not v.allowed, f"{tool} {path} should be denied"


def test_path_traversal_is_normalized_then_denied():
    # A workspace-relative path that climbs out to /etc must still be caught.
    v = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "/workspace/../etc/passwd"}, home=HOME)
    assert not v.allowed


def test_relative_traversal_resolved_against_cwd():
    v = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "../../etc/shadow"}, cwd="/workspace/sub", home=HOME)
    assert not v.allowed


# ---- allowed paths -------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", "/workspace/src/main.py"),
        ("Write", "/workspace/out.txt"),
        ("Edit", "/workspace/pkg/mod.py"),
        ("Read", "relative/file.py"),  # resolves under cwd=/workspace
        ("Glob", "/workspace/src"),
        ("Grep", "/workspace"),
    ],
)
def test_allowed_workspace_paths(tool, path):
    v = evaluate_ring2_paths(_dev_policy(), tool, {"file_path": path, "path": path}, home=HOME)
    assert v.allowed, f"{tool} {path} should be allowed"


def test_glob_bare_pattern_is_not_a_path_target():
    # A relative glob pattern with no path arg is scoped to the workspace, not denied.
    v = evaluate_ring2_paths(_dev_policy(), "Glob", {"pattern": "**/*.py"}, home=HOME)
    assert v.allowed


def test_glob_absolute_pattern_is_checked():
    v = evaluate_ring2_paths(_dev_policy(), "Glob", {"pattern": "/etc/**"}, home=HOME)
    assert not v.allowed


def test_non_filesystem_tool_allowed():
    # Bash isn't in _PATH_KEYS — Ring-2 path check is a no-op (Bash is gated by
    # Ring-3 content rules instead).
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": "cat /etc/passwd"}, home=HOME)
    assert v.allowed


# ---- combined gate -------------------------------------------------------


def test_gate_denies_tool_not_in_allowlist():
    v = gate_tool_call(_dev_policy(), "NetTool", {"file_path": "/workspace/x"}, home=HOME)
    assert not v.allowed  # name gate (evaluate_policy)


def test_gate_denies_dangerous_bash():
    v = gate_tool_call(_dev_policy(), "Bash", {"command": "rm -rf /"}, home=HOME)
    assert not v.allowed  # Ring-3 content check


def test_gate_denies_host_read_via_ring2():
    v = gate_tool_call(_dev_policy(), "Read", {"file_path": "/etc/passwd"}, home=HOME)
    assert not v.allowed  # Ring-2 path check


def test_gate_allows_normal_workspace_read():
    v = gate_tool_call(_dev_policy(), "Read", {"file_path": "/workspace/main.py"}, home=HOME)
    assert v.allowed
