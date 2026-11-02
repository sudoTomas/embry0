"""Tests for the direct-xAI executor's builtin tool implementations (EMB-45)."""

from __future__ import annotations

from embry0.agents.xai_tools import BUILTIN_TOOL_NAMES, execute_tool, tool_defs
from embry0.safety.policy import SafetyPolicy, evaluate_ring2_paths


def test_tool_defs_filters_to_allowed():
    defs = tool_defs(["Read", "Bash"])
    assert [d["name"] for d in defs] == ["Read", "Bash"]
    # Every def has an input_schema with required fields.
    for d in defs:
        assert d["input_schema"]["type"] == "object"


def test_tool_defs_all_builtins():
    defs = tool_defs(list(BUILTIN_TOOL_NAMES))
    assert {d["name"] for d in defs} == set(BUILTIN_TOOL_NAMES)


def test_write_then_read_roundtrip(tmp_path):
    out, err = execute_tool("Write", {"file_path": "hi.txt", "content": "line1\nline2\n"}, cwd=str(tmp_path))
    assert not err and "Wrote" in out
    out, err = execute_tool("Read", {"file_path": "hi.txt"}, cwd=str(tmp_path))
    assert not err
    assert out == "1\tline1\n2\tline2"


def test_read_missing_file_is_error(tmp_path):
    out, err = execute_tool("Read", {"file_path": "nope.txt"}, cwd=str(tmp_path))
    assert err and "not found" in out


def test_read_offset_limit(tmp_path):
    execute_tool("Write", {"file_path": "f.txt", "content": "a\nb\nc\nd\n"}, cwd=str(tmp_path))
    out, err = execute_tool("Read", {"file_path": "f.txt", "offset": 2, "limit": 2}, cwd=str(tmp_path))
    assert not err
    assert out == "2\tb\n3\tc"


def test_edit_unique(tmp_path):
    execute_tool("Write", {"file_path": "e.txt", "content": "foo bar foo"}, cwd=str(tmp_path))
    out, err = execute_tool("Edit", {"file_path": "e.txt", "old_string": "bar", "new_string": "BAZ"}, cwd=str(tmp_path))
    assert not err
    content, _ = execute_tool("Read", {"file_path": "e.txt"}, cwd=str(tmp_path))
    assert "BAZ" in content


def test_edit_ambiguous_without_replace_all_is_error(tmp_path):
    execute_tool("Write", {"file_path": "e.txt", "content": "x x x"}, cwd=str(tmp_path))
    out, err = execute_tool("Edit", {"file_path": "e.txt", "old_string": "x", "new_string": "y"}, cwd=str(tmp_path))
    assert err and "occurs 3 times" in out


def test_edit_replace_all(tmp_path):
    execute_tool("Write", {"file_path": "e.txt", "content": "x x x"}, cwd=str(tmp_path))
    out, err = execute_tool(
        "Edit",
        {"file_path": "e.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        cwd=str(tmp_path),
    )
    assert not err
    content, _ = execute_tool("Read", {"file_path": "e.txt"}, cwd=str(tmp_path))
    assert content == "1\ty y y"


def test_edit_missing_old_string_is_error(tmp_path):
    execute_tool("Write", {"file_path": "e.txt", "content": "abc"}, cwd=str(tmp_path))
    out, err = execute_tool("Edit", {"file_path": "e.txt", "old_string": "zzz", "new_string": "y"}, cwd=str(tmp_path))
    assert err and "not found" in out


def test_bash_captures_output_and_exit(tmp_path):
    out, err = execute_tool("Bash", {"command": "echo hello && exit 3"}, cwd=str(tmp_path))
    assert not err
    assert "hello" in out
    assert "[exit 3]" in out


def test_bash_runs_the_command_in_bash(tmp_path):
    """The Ring-2 check reads a command as bash does; dash reads $'…' as $ and a quoted string."""
    out, err = execute_tool("Bash", {"command": "printf '[%s]' $'a\\x41'"}, cwd=str(tmp_path))
    assert not err and "[aA]" in out, f"the command ran in a shell that is not bash: {out!r}"


def test_bash_timeout(tmp_path):
    out, err = execute_tool("Bash", {"command": "sleep 5", "timeout": 1}, cwd=str(tmp_path))
    assert err and "timed out" in out


def test_glob(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")
    out, err = execute_tool("Glob", {"pattern": "**/*.py"}, cwd=str(tmp_path))
    assert not err
    assert "a.py" in out and "b.py" in out


def test_grep(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\nBETA\n")
    out, err = execute_tool("Grep", {"pattern": "beta"}, cwd=str(tmp_path))
    assert not err
    assert "a.txt:2: beta" in out
    assert "BETA" not in out  # case-sensitive by default
    out, err = execute_tool("Grep", {"pattern": "beta", "ignore_case": True}, cwd=str(tmp_path))
    assert "a.txt:3: BETA" in out


# ---- #542: a search over a parent directory hides Read-denied files -------


def _workspace_with_secret(tmp_path):
    ws = tmp_path / "ws"
    (ws / "secret").mkdir(parents=True)
    (ws / "secret" / "key.txt").write_text("token=s3cr3t-value\n")
    (ws / "open.txt").write_text("token=s3cr3t-open\n")
    return ws, SafetyPolicy(deny_rules=[f"Read(/{ws}/secret/**)"])


def test_grep_over_a_parent_dir_leaves_out_read_denied_files(tmp_path):
    ws, policy = _workspace_with_secret(tmp_path)
    ti = {"pattern": "s3cr3t", "path": str(ws)}
    assert evaluate_ring2_paths(policy, "Grep", ti, cwd=str(ws)).allowed, "precondition: the root itself is allowed"

    out, err = execute_tool("Grep", ti, cwd=str(ws), policy=policy)

    assert not err
    assert "s3cr3t-value" not in out and "key.txt" not in out, f"Grep returned a Read-denied file: {out!r}"
    assert "s3cr3t-open" in out, f"Grep must still return the allowed file: {out!r}"


def test_glob_over_a_parent_dir_leaves_out_read_denied_files(tmp_path):
    ws, policy = _workspace_with_secret(tmp_path)

    out, err = execute_tool("Glob", {"pattern": "**/*.txt", "path": str(ws)}, cwd=str(ws), policy=policy)

    assert not err
    assert "key.txt" not in out, f"Glob listed a Read-denied file: {out!r}"
    assert "open.txt" in out, f"Glob must still list the allowed file: {out!r}"
