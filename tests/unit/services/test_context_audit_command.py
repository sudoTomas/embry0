"""The context auditor's command reaches it in its task, not in its stored prompt (#170).

A builtin agent's prompt reaches a deployment's stored row only when the row
is created, and the stored row wins. So the file list and the report shape
the code reads back ride in each job's task, built when the task is, and a
deployment whose row an older release stored still runs the current command.

These tests reach the command through what exists either way (the task
builder, the job start and the generic node), so each fails with its own
message on a tree where the command lives in the prompt.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from embry0.safety.patterns import is_dangerous_command
from embry0.services import context_audit, instruction_files, world
from embry0.services.context_audit import RepoContext, audit_task, start_context_audit_job
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.workflows.issue_to_pr.generic_agent import generic_agent_node
from embry0.workflows.issue_to_pr.specialist_agents import REPORT_TARGET_CHARS

HEAD = "1" * 40
SECTION = "## How to run this audit"
# git's own form of each glob and skipped directory: what the command must hold.
PATHSPEC = "':(glob){}'"
EXCLUDE = "':(exclude,glob)**/{}/**'"


def _task(files: tuple[str, ...] = ("CLAUDE.md", "docs/AGENTS.md")) -> str:
    return audit_task(RepoContext("acme/widgets", HEAD, files, "fp"))


def _command(text: str) -> str:
    found = re.search(r"\n```\n(git ls-files -- [^\n]+)\n```\n", text)
    assert found, "the task does not hand the agent the command in a code block of its own"
    return found.group(1)


# ---- the task carries the command ----------------------------------------------


def test_the_task_carries_the_command_with_every_glob_and_skipped_directory() -> None:
    task = _task()
    missing = [g for g in instruction_files.INSTRUCTION_FILE_GLOBS if PATHSPEC.format(g) not in task]
    assert not missing, f"the audit's task does not carry the instruction-file globs {missing}"
    searched = [d for d in sorted(instruction_files.SKIPPED_DIRS) if EXCLUDE.format(d) not in task]
    assert not searched, f"the audit's task does not exclude the skipped directories {searched}"
    assert task.startswith("Audit the files that steer a coding agent in acme/widgets"), "the title line stays first"
    assert task.index("Instruction files (2):") < task.index(SECTION), "the files found come before the command"


def test_the_task_reads_the_list_when_it_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    # A list that changed after the module was imported reaches the next task.
    changed = (*instruction_files.INSTRUCTION_FILE_GLOBS, "**/CONVENTIONS.md")
    monkeypatch.setattr(instruction_files, "INSTRUCTION_FILE_GLOBS", changed)
    assert PATHSPEC.format("**/CONVENTIONS.md") in _task(), "the task's command was fixed before the list changed"


def test_the_command_lists_what_instruction_files_lists(tmp_path: Any) -> None:
    # git's glob magic is not the module's matcher, so run the task's own
    # command in a repository that holds listed, unlisted and vendored files.
    paths = [
        "CLAUDE.md",
        "apps/hub/src/app/api/CLAUDE.md",
        "CLAUDE.local.md",
        "AGENTS.md",
        "AGENTS.override.md",
        "services/api/GEMINI.md",
        "notes/HANDOFF.md",
        "llms.txt",
        ".cursorrules",
        "sub/.windsurfrules",
        "sub/.clinerules",
        ".github/copilot-instructions.md",
        ".github/instructions/frontend.instructions.md",
        ".github/instructions/deep/api.instructions.md",
        ".cursor/rules/style.mdc",
        "pkg/.cursor/rules/deep/style.md",
        ".claude/skills/verify-app/SKILL.md",
        "apps/hub/.claude/commands/ship.md",
        ".claude/agents/reviewer.md",
        ".claude/rules/tests.md",
        ".grok/skills/verify/SKILL.md",
        ".grok/rules/style.md",
        ".codex/prompts/review.md",
        ".agents/notes/a.md",
        # Not instruction files.
        "README.md",
        "docs/CLAUDE.md.bak",
        "docs/MY-CLAUDE.md",
        "apps/.github/copilot-instructions.md",
        ".github/instructions/notes.md",
        ".claude/settings.json",
        "src/claude.ts",
        # Instruction files of vendored or generated trees.
        "node_modules/pkg/CLAUDE.md",
        "frontend/node_modules/x/.claude/skills/a/SKILL.md",
        "vendor/lib/AGENTS.md",
        "build/CLAUDE.md",
        "a/dist/b/AGENTS.md",
        ".venv/lib/CLAUDE.md",
        "venv/AGENTS.md",
    ]
    for path in paths:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    listed = subprocess.run(
        ["sh", "-c", _command(_task())], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.split()

    expected = instruction_files.instruction_files(paths)
    assert len(expected) == 24, "the repository must hold files on both sides of the list"
    assert sorted(listed) == expected, "the task's command and instruction_files() disagree"


def test_the_task_fixes_the_report_shape_the_code_reads() -> None:
    # report_counts reads the first two lines, parse_findings the table under
    # the Findings heading, audit_delivery the diff under the Pull heading.
    task = _task()
    assert "The first line is `Stale: N` and nothing else" in task, "the task does not fix the Stale line"
    assert "where N is the number of `misleads` and `dead` findings" in task, "the task does not say what N counts"
    assert "The second line is `Files: M` and nothing else" in task, "the task does not fix the Files line"
    assert task.index("`Stale: N`") < task.index("`Files: M`") < task.index("a `## Findings` heading")
    assert "\n| Severity | Location | Claim | Evidence | Fix |\n" in task, "the task lost the findings table's columns"
    assert "at most 40 rows" in task
    sections = [
        "`## Pull`",
        "`## Unverifiable`",
        "`## Unreachable from a clone`",
        "`## Not covered`",
        "`## Assumptions`",
    ]
    positions = [task.index(section) for section in sections]
    assert positions == sorted(positions), "the sections after the table keep their order"
    assert "`Stale: 0`, then `Files: 0`" in task, "a repository with no instruction files still opens with both"


def test_the_task_grades_findings_with_the_severities_the_code_ranks() -> None:
    # world.py ranks a finding by its severity word, so every word the task
    # defines must be one it knows: a rename on either side fails here.
    task = _task()
    defined = re.findall(r"^- `([a-z-]+)`: ", task, re.MULTILINE)
    assert defined, "the task defines no severities"
    unranked = [word for word in defined if word not in world._SEVERITY]
    assert not unranked, f"the task grades with severities world.py does not rank: {unranked}"
    assert "not counted as stale" in task


def test_the_task_says_what_to_cut_to_fit() -> None:
    task = _task()
    assert f"the diff included, stays under {REPORT_TARGET_CHARS:,}" in task, "the task does not bound the report"
    assert "the `drift` rows and their hunks" in task
    assert "Never cut the first two lines or a `misleads` row" in task


def test_the_task_prescribes_no_command_the_safety_policy_blocks() -> None:
    # The sandbox runs every Bash call through the same patterns as the prompts'.
    task = _task()
    quoted = re.findall(r"```[^\n]*\n(.*?)```", task, re.S) + re.findall(r"(?<!`)`([^`\n]+)`(?!`)", task)
    assert quoted, "the task quotes no command"
    blocked = [(command, pattern) for command in quoted if (pattern := is_dangerous_command(command)) is not None]
    assert not blocked, f"the task prescribes commands the safety policy blocks: {blocked}"


# ---- every job the service starts carries it -------------------------------------


class _Jobs:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return f"job-{len(self.created)}"


class _Executor:
    def submit_job(self, job_id: str, **kwargs: Any) -> None:
        return None


class _Templates:
    async def list_all(self) -> list[dict[str, Any]]:
        return [{"name": "context-audit"}]


@pytest.mark.asyncio
async def test_every_job_the_service_starts_carries_the_command_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_audit(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(context_audit, "emit_audit", no_audit)
    jobs = _Jobs()
    ctx = RepoContext("acme/widgets", HEAD, ("CLAUDE.md",), "fp")
    start = {"jobs": jobs, "executor": _Executor(), "templates": _Templates(), "budget_usd": 1.0}
    await start_context_audit_job(ctx, **start)
    # An owner's fix names its one claim; its report keeps the same shape.
    await start_context_audit_job(ctx, task="Fix one outdated claim in acme/widgets", **start, open_pr=True)

    scheduled, fix = (row["task"] for row in jobs.created)
    assert scheduled.count("git ls-files -- ") == 1, "the scheduled audit's task carries the command once"
    assert fix.startswith("Fix one outdated claim in acme/widgets"), "the fix's own task stays first"
    assert fix.count("git ls-files -- ") == 1, "a fix an owner asked for gets no command, or two"
    assert "| Severity | Location | Claim | Evidence | Fix |" in fix, "the fix's task does not fix the report shape"


# ---- the agent gets it, whatever prompt the deployment stored -----------------------


# A context-auditor row as an older release stored it: its prompt carries
# that release's command, a shorter list, and its report shape. The stored
# row wins over the seed, and nothing rewrites it short of a reset.
OLDER_PROMPT = """You are embry0's context-auditor agent. You find stale lines in \
the instruction files and prove each one.

## The files

The command lists exactly those files; run it as written:

```
git ls-files -- ':(glob)**/CLAUDE.md' ':(glob)**/AGENTS.md'
```

## Deliverable shape

The first line is `Stale: N` and nothing else."""


def _state(task: str, agent_type: str = "context-auditor") -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "task": task,
        "repo": "acme/widgets",
        "route_plan": [{"agent_type": agent_type, "node_id": agent_type, "config": {}}, {"agent_type": "output"}],
        "route_cursor": 0,
        "qa": {},
    }


async def _run(state: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def run(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"agent_outputs": [], "current_stage": f"{kwargs['agent_type']}_complete"}

    async def load(_config: Any, _agent_type: str) -> dict[str, Any] | None:
        return stored

    config = {"configurable": {"agent_runner": object(), "credentials": {}}}
    with (
        patch("embry0.workflows.issue_to_pr.generic_agent.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.generic_agent.load_agent_definition_or_none", load),
        patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", run),
    ):
        await generic_agent_node(state, config)
    return captured


@pytest.mark.asyncio
async def test_an_older_stored_prompt_still_gets_the_current_command_through_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The list changed since the row was stored; the agent is not reset.
    changed = (*instruction_files.INSTRUCTION_FILE_GLOBS, "**/CONVENTIONS.md")
    monkeypatch.setattr(instruction_files, "INSTRUCTION_FILE_GLOBS", changed)
    stored = {**BUILTIN_SEED["context-auditor"], "system_prompt": OLDER_PROMPT}

    ran = await _run(_state(_task()), stored)

    assert ran["agent_definition"]["system_prompt"] == OLDER_PROMPT, "the stored row runs as stored: no reset"
    prompt = ran["prompt"]
    missing = [g for g in changed if PATHSPEC.format(g) not in prompt]
    assert not missing, f"an agent whose prompt an older release stored never sees the current globs {missing}"
    assert "| Severity | Location | Claim | Evidence | Fix |" in prompt, "nor the current report shape"
    assert "Where your system prompt gives another file list, another command" in prompt, (
        "the task does not say its command wins over the older prompt's"
    )
    assert prompt.count("git ls-files -- ") == 1, "the task carries the command once"


@pytest.mark.asyncio
async def test_a_task_built_without_the_command_gets_it_when_the_agent_runs() -> None:
    # A job created with POST /jobs naming the context-audit template carries
    # the person's words only; the agent still gets the command and the shape.
    ran = await _run(_state("Audit acme/widgets"), None)
    prompt = ran["prompt"]
    assert "Task:\nAudit acme/widgets\n" in prompt, "the person's task stays first"
    missing = [g for g in instruction_files.INSTRUCTION_FILE_GLOBS if PATHSPEC.format(g) not in prompt]
    assert not missing, f"a context-audit task written by hand reaches the agent without the globs {missing}"
    assert prompt.count("git ls-files -- ") == 1, "the agent is handed one command"
    assert "| Severity | Location | Claim | Evidence | Fix |" in prompt, "nor the report shape"

    other = await _run(_state("Why is the cache keyed this way?", agent_type="why"), None)
    assert "git ls-files -- " not in other["prompt"], "only the context auditor's task gets the command"
