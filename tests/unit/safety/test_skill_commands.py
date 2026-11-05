"""A skill's inline shell commands get the checks a Bash call gets (embry0.safety.skill_commands).

The CLI runs a skill's ``!`…``` and ```` ```! ```` commands itself, so no tool
call reaches the hook for them. It runs the text it loaded when the session
started, so embry0 reads every skill before the session starts
(SkillSnapshot), and gate_tool_call refuses a Skill call when a command the
loaded text or the file as it is now runs would be refused as Bash, when the
agent may not run Bash at all, or when the skill was not in the snapshot or
cannot be read or parsed.
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from typing import Any

import pytest

from embry0.safety.policy import SafetyPolicy, default_policy_for_agent, gate_tool_call

HOME = "/home/agent"
FENCE = "```"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.skill_commands")
    assert spec is not None, "embry0.safety.skill_commands must read the shell commands a skill runs"
    import embry0.safety.skill_commands as module

    return module


def _policy(*, bash: bool = True) -> SafetyPolicy:
    dev = default_policy_for_agent("developer")
    tools = ["Read", "Grep", "Glob", "Bash"] if bash else ["Read", "Grep", "Glob"]
    return SafetyPolicy(allowed_tools=tools, deny_rules=dev.deny_rules, content_checks=dev.content_checks)


def _skill(workspace: Path, name: str, body: str, *, kind: str = "skills") -> Path:
    if kind == "skills":
        path = workspace / ".claude" / "skills" / name / "SKILL.md"
    else:
        path = workspace / ".claude" / "commands" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: probe\n---\n{body}\n")
    return path


def _snapshot(workspace: Path, skill_dirs: dict[str, str] | None = None) -> Any:
    snapshot = getattr(_module(), "SkillSnapshot", None)
    assert snapshot is not None, "skill_commands.SkillSnapshot must read the skills a session loads before it starts"
    return snapshot.take(str(workspace), skill_dirs or {})


def _gate(
    workspace: Path,
    tool_input: dict[str, Any],
    *,
    bash: bool = True,
    skill_dirs: dict[str, str] | None = None,
    skills: Any = None,
    cwd: str | None = None,
    tool: str = "Skill",
) -> Any:
    return gate_tool_call(
        _policy(bash=bash),
        tool,
        tool_input,
        cwd=cwd or str(workspace),
        home=HOME,
        skills=skills if skills is not None else _snapshot(workspace, skill_dirs),
        project_root=str(workspace),
    )


# ---- the commands a skill runs -------------------------------------------------------


def test_skill_commands_finds_inline_and_fenced_commands() -> None:
    text = "Status: !`git status`\nAt line start:\n!`echo one`\n" + FENCE + "!\nls -la\n" + FENCE + "\n"
    assert _module().skill_commands(text, "") == ["ls -la", "git status", "echo one"]


def test_a_braced_argument_placeholder_is_filled_in_too() -> None:
    commands = _module().skill_commands('!`echo "$(head -1 ${ARGUMENTS})"`', "/etc/passwd")
    assert 'echo "$(head -1 /etc/passwd)"' in commands, f"${{ARGUMENTS}} is read as filled in: {commands}"


def test_skill_commands_skips_an_ordinary_code_span() -> None:
    """`x !`cmd`` inside a code span is text the CLI does not run."""
    text = "Use `!`echo hi`` in your skill, or a!`no` without a space.\n"
    assert _module().skill_commands(text, "") == []
    # an inline code span that happens to hold " !`…`" is blanked before the search
    assert _module().skill_commands("Inline code like `a !`echo hi` b` is text.\n", "") == []


def test_skill_commands_fills_in_the_arguments() -> None:
    text = "---\nname: s\narguments: [target]\n---\nLog: !`git log $ARGUMENTS`\nOne: !`cat $1`\nNamed: !`cat $target`\n"
    commands = _module().skill_commands(text, "/etc/passwd")
    assert "git log /etc/passwd" in commands and "cat /etc/passwd" in commands, commands
    assert commands.count("cat /etc/passwd") == 2, commands


# ---- the gate ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "HOME: !`printenv HOME`",
        'SUB: !`echo "$(head -1 /etc/passwd)"`',
        "BASHC: !`bash -c 'head -1 /etc/hostname'`",
        FENCE + "!\ncat ~/.ssh/id_rsa\n" + FENCE,
    ],
)
def test_a_skill_that_runs_a_refused_command_is_refused(tmp_path: Path, line: str) -> None:
    _module()
    _skill(tmp_path, "repo-skill", line)
    verdict = _gate(tmp_path, {"skill": "repo-skill"})
    assert not verdict.allowed, f"a skill running {line!r} was allowed"
    assert "skill 'repo-skill' runs" in verdict.reason, verdict.reason


def test_a_skill_with_no_commands_is_allowed(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "plain", "Write tests first. Mention /etc/passwd and `!` freely.")
    verdict = _gate(tmp_path, {"skill": "plain"})
    assert verdict.allowed, verdict.reason


def test_a_skill_whose_commands_pass_is_allowed(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "status", "!`git status --short`")
    verdict = _gate(tmp_path, {"skill": "status"})
    assert verdict.allowed, verdict.reason


def test_an_agent_without_bash_may_not_run_a_skill_that_runs_commands(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "status", "!`git status --short`")
    refused = _gate(tmp_path, {"skill": "status"}, bash=False)
    assert not refused.allowed and "may not run any" in refused.reason, refused.reason
    _skill(tmp_path, "plain", "No commands here.")
    assert _gate(tmp_path, {"skill": "plain"}, bash=False).allowed


def test_an_empty_allowlist_runs_no_skill_that_runs_commands(tmp_path: Path) -> None:
    """An empty allowlist names no tool: no Bash, so no skill command either."""
    _module()
    _skill(tmp_path, "status", "!`git status --short`")
    dev = default_policy_for_agent("developer")
    empty = SafetyPolicy(allowed_tools=[], deny_rules=dev.deny_rules, content_checks=dev.content_checks)
    verdict = gate_tool_call(
        empty, "Skill", {"skill": "status"}, cwd=str(tmp_path), home=HOME, skills=_snapshot(tmp_path)
    )
    assert not verdict.allowed and "may not run any" in verdict.reason, f"an empty allowlist ran a skill: {verdict}"


def test_the_arguments_are_checked_where_the_skill_puts_them(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "show", "!`cat $ARGUMENTS`")
    assert not _gate(tmp_path, {"skill": "show", "args": "/etc/passwd"}).allowed
    assert _gate(tmp_path, {"skill": "show", "args": "README.md"}).allowed


def test_a_command_file_and_a_slash_command_are_checked(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "probe-cmd", "!`printenv HOME`", kind="commands")
    assert not _gate(tmp_path, {"skill": "probe-cmd"}).allowed
    verdict = _gate(tmp_path, {"command": "/probe-cmd x"}, tool="SlashCommand")
    assert not verdict.allowed, verdict.reason


def test_a_skill_of_a_mounted_source_is_checked(tmp_path: Path) -> None:
    _module()
    source = tmp_path / "skills-src"
    (source / "skills" / "deploy").mkdir(parents=True)
    (source / "skills" / "deploy" / "SKILL.md").write_text("!`cat /root/.bashrc`\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    verdict = _gate(workspace, {"skill": "house:deploy"}, skill_dirs={"house": str(source)})
    assert not verdict.allowed, verdict.reason


def test_a_skill_written_after_the_session_started_is_refused(tmp_path: Path) -> None:
    """The CLI may load a skill written mid-session from text embry0 never read: it is refused."""
    _module()
    skills = _snapshot(tmp_path)
    _skill(tmp_path, "later", "No commands yet.")
    verdict = _gate(tmp_path, {"skill": "later"}, skills=skills)
    assert not verdict.allowed and "when the session started" in verdict.reason, verdict.reason


def test_a_skill_is_checked_as_the_session_loaded_it(tmp_path: Path) -> None:
    """The CLI runs the text it loaded: an agent rewriting the file to something harmless changes nothing."""
    _module()
    _skill(tmp_path, "cached", "!`printenv HOME`")
    skills = _snapshot(tmp_path)
    _skill(tmp_path, "cached", "harmless now")
    verdict = _gate(tmp_path, {"skill": "cached"}, skills=skills)
    assert not verdict.allowed and "printenv" in verdict.reason, f"the loaded text runs printenv: {verdict}"


def test_a_skill_is_checked_as_its_file_is_now(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "edited", "harmless")
    skills = _snapshot(tmp_path)
    _skill(tmp_path, "edited", "!`printenv HOME`")
    verdict = _gate(tmp_path, {"skill": "edited"}, skills=skills)
    assert not verdict.allowed and "printenv" in verdict.reason, f"the file now runs printenv: {verdict}"


def test_a_skill_that_changed_while_the_session_loaded_it_is_refused(tmp_path: Path) -> None:
    """Between the snapshot and the CLI's load the file may have held anything; the change shows in its ctime."""
    _module()
    path = _skill(tmp_path, "raced", "harmless")
    _skill(tmp_path, "steady", "harmless")
    skills = _snapshot(tmp_path)
    time.sleep(0.05)  # past the filesystem clock's tick, as the CLI's start always is
    original = path.read_bytes()
    path.write_text("---\nname: raced\ndescription: probe\n---\n!`printenv HOME`\n")
    path.write_bytes(original)  # the same text again: only its ctime (and mtime) tell
    skills.settle()
    verdict = _gate(tmp_path, {"skill": "raced"}, skills=skills)
    assert not verdict.allowed and "changed while the session loaded it" in verdict.reason, verdict.reason
    assert _gate(tmp_path, {"skill": "steady"}, skills=skills).allowed, "an untouched skill stays allowed"


def _swap_directory_and_back(tmp_path: Path) -> None:
    """Rename the skill's directory away, put another in its place, then put the first one back."""
    skills = tmp_path / ".claude" / "skills"
    stash = tmp_path / "stash"
    stash.mkdir(exist_ok=True)
    os.rename(skills / "x", stash / "xa")
    os.rename(stash / "x", skills / "x")
    os.rename(skills / "x", stash / "x")
    os.rename(stash / "xa", skills / "x")


def _flip_symlink_and_back(tmp_path: Path) -> None:
    link = tmp_path / ".claude" / "skills" / "x"
    os.unlink(link)
    os.symlink("../src/y", link)
    os.unlink(link)
    os.symlink("../src/x", link)


@pytest.mark.parametrize("bash", [True, False], ids=["with-bash", "without-bash"])
@pytest.mark.parametrize("swap", [_swap_directory_and_back, _flip_symlink_and_back], ids=["directory", "symlink"])
def test_a_skill_swapped_and_restored_while_the_session_loaded_it_is_refused(
    tmp_path: Path, swap: Any, bash: bool
) -> None:
    """The file is the same inode with the same times afterwards; its directory, or the link to it, is not."""
    _module()
    if swap is _swap_directory_and_back:
        _skill(tmp_path, "x", "harmless")
        (tmp_path / "stash" / "x").mkdir(parents=True)
        (tmp_path / "stash" / "x" / "SKILL.md").write_text("!`printenv HOME`\n")
    else:
        for name, body in (("x", "harmless"), ("y", "!`printenv HOME`")):
            (tmp_path / ".claude" / "src" / name).mkdir(parents=True)
            (tmp_path / ".claude" / "src" / name / "SKILL.md").write_text(body + "\n")
        (tmp_path / ".claude" / "skills").mkdir(parents=True)
        os.symlink("../src/x", tmp_path / ".claude" / "skills" / "x")
    skills = _snapshot(tmp_path)
    assert skills.files.get("x"), "the snapshot holds skill x"
    time.sleep(0.05)  # past the filesystem clock's tick, as the CLI's start always is
    swap(tmp_path)
    skills.settle()
    verdict = _gate(tmp_path, {"skill": "x"}, skills=skills, bash=bash)
    assert not verdict.allowed and "changed while the session loaded it" in verdict.reason, (
        f"{swap.__name__}: the CLI may have loaded the other skill: {verdict}"
    )


@pytest.mark.parametrize("name", ["nested", "sub:nested"])
def test_a_skill_in_a_subdirectory_is_refused(tmp_path: Path, name: str) -> None:
    """The CLI loads sub/.claude/skills when the agent touches a file there, from text embry0 cannot pin."""
    _module()
    folder = tmp_path / "sub" / ".claude" / "skills" / "nested"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("harmless\n")
    verdict = _gate(tmp_path, {"skill": name})
    assert not verdict.allowed, f"skill {name!r} from a subdirectory was allowed: {verdict}"


def test_a_skill_is_found_by_its_frontmatter_name_too(tmp_path: Path) -> None:
    _module()
    path = tmp_path / ".claude" / "skills" / "folder" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: other\ndescription: probe\n---\n!`printenv HOME`\n")
    for name in ("folder", "other"):
        verdict = _gate(tmp_path, {"skill": name})
        assert not verdict.allowed and "printenv" in verdict.reason, f"{name!r}: {verdict}"


def test_a_skills_commands_are_checked_where_the_session_is(tmp_path: Path) -> None:
    """The CLI runs a skill's commands in the session's directory, not the workspace's."""
    _module()
    _skill(tmp_path, "save", "!`echo '{}' > settings.json`")
    refused = _gate(tmp_path, {"skill": "save"}, cwd="/workspace/.claude")
    assert not refused.allowed and "settings.json" in refused.reason, f"run in /workspace/.claude: {refused}"
    assert _gate(tmp_path, {"skill": "save"}, cwd="/workspace").allowed, "run in /workspace it writes no settings"


def test_an_executor_with_no_skills_refuses_every_skill(tmp_path: Path) -> None:
    _module()
    _skill(tmp_path, "plain", "No commands.")
    verdict = gate_tool_call(_policy(), "Skill", {"skill": "plain"}, cwd=str(tmp_path), home=HOME)
    assert not verdict.allowed and "reads no skills" in verdict.reason, verdict


@pytest.mark.parametrize("name", ["missing", "../escape", "a/b", "", "x y"])
def test_a_skill_that_cannot_be_found_is_refused(tmp_path: Path, name: str) -> None:
    _module()
    verdict = _gate(tmp_path, {"skill": name})
    assert not verdict.allowed, f"skill {name!r} was allowed though embry0 cannot read it"


def test_a_skill_file_that_resolves_outside_its_directory_is_refused(tmp_path: Path) -> None:
    _module()
    outside = tmp_path / "outside.md"
    outside.write_text("no commands\n")
    workspace = tmp_path / "workspace"
    (workspace / ".claude" / "skills" / "link").mkdir(parents=True)
    os.symlink(outside, workspace / ".claude" / "skills" / "link" / "SKILL.md")
    verdict = _gate(workspace, {"skill": "link"})
    assert not verdict.allowed and "resolves outside" in verdict.reason, verdict.reason


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (b"---\nname: x\nno closing line\n!`echo hi`\n", "does not close"),
        (b"\xff\xfe not utf-8", "not UTF-8"),
    ],
)
def test_a_skill_file_that_cannot_be_parsed_is_refused(tmp_path: Path, content: bytes, why: str) -> None:
    _module()
    path = tmp_path / ".claude" / "skills" / "bad" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    verdict = _gate(tmp_path, {"skill": "bad"})
    assert not verdict.allowed and why in verdict.reason, verdict.reason
