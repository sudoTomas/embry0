"""What each tool call trains (progression spec 2026-10-24): an action key ``kind:skill``.

The agent node counts a run's tool calls into these keys as the events
stream in and stores them on the trace (``traces.skill_actions``); the
progression service prices each key by its kind and credits its skill.
Traces from before the column keep only tool names, which read through the
same function with no input: a shell call trains Shell, an edit Coding.

A shell command trains the skill of the first program in it that is not
plain shell (``cd x && pytest`` is a test run); a file edit trains the skill
of the file's path (docs, tests, migrations, styles, infra, else Coding).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
_READ_TOOLS = frozenset({"Read", "Glob", "LS"})
_SEARCH_TOOLS = frozenset({"Grep", "ToolSearch"})
_WEB_TOOLS = frozenset({"WebSearch", "WebFetch"})
_AGENT_TOOLS = frozenset({"Agent", "Task"})
_PLAN_TOOLS = frozenset({"TodoWrite", "ExitPlanMode"})

#: Wrappers that run the program after them: ``uv run pytest`` is pytest.
_WRAPPERS = frozenset({"sudo", "time", "nice", "env", "exec", "npx", "bunx", "xargs", "command"})
_RUNNERS = {("uv", "run"), ("poetry", "run"), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx"), ("pipx", "run")}

_TESTING = frozenset(
    {"pytest", "py.test", "vitest", "jest", "mocha", "tox", "nox", "rspec", "phpunit", "ava", "karma", "behave"}
)
_LINTING = frozenset(
    {
        "ruff",
        "eslint",
        "mypy",
        "pyright",
        "tsc",
        "prettier",
        "black",
        "isort",
        "flake8",
        "pylint",
        "golangci-lint",
        "gofmt",
        "shellcheck",
        "stylelint",
        "biome",
        "pre-commit",
        "hadolint",
        "markdownlint",
        "rubocop",
    }
)
_BUILDING = frozenset({"cmake", "ninja", "pip", "pip3", "vite", "webpack", "esbuild", "rollup", "bazel"})
_DATABASES = frozenset(
    {"psql", "sqlite3", "mysql", "alembic", "flyway", "pg_dump", "pg_restore", "redis-cli", "mongosh"}
)
_OPERATIONS = frozenset(
    {
        "docker",
        "docker-compose",
        "podman",
        "kubectl",
        "helm",
        "terraform",
        "tofu",
        "systemctl",
        "journalctl",
        "ssh",
        "ansible",
        "ansible-playbook",
        "aws",
        "gcloud",
        "az",
        "flyctl",
        "vercel",
        "cloudflared",
    }
)
_SECURITY = frozenset(
    {"trivy", "gitleaks", "semgrep", "bandit", "pip-audit", "safety", "grype", "snyk", "trufflehog", "osv-scanner"}
)
_VERSION_CONTROL = frozenset({"git", "gh"})

#: Package managers whose subcommand decides: ``npm test`` tests, ``npm ci`` builds.
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun", "cargo", "go", "mvn", "mvnw", "gradle", "gradlew"})
_BUILD_SUBCOMMANDS = frozenset({"install", "ci", "add", "build", "compile", "package", "sync", "lock", "verify"})

_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".adoc"})
_DESIGN_SUFFIXES = frozenset({".css", ".scss", ".sass", ".less", ".svg"})
_OPS_SUFFIXES = frozenset({".tf", ".hcl"})
_OPS_DIRS = ("/.github/", "/infra/", "/deploy/", "/k8s/", "/helm/", "/charts/")
_TEST_DIRS = ("/tests/", "/test/", "/__tests__/", "/e2e/")


def skill_of_path(path: str) -> str:
    """The skill an edit of ``path`` trains."""
    p = "/" + path.replace("\\", "/").lstrip("/").lower()
    name = PurePosixPath(p).name
    suffix = PurePosixPath(p).suffix
    if suffix in _DOC_SUFFIXES or "/docs/" in p:
        return "documentation"
    if (
        name.startswith("test_")
        or name.endswith(("_test.py", "_test.go"))
        or ".test." in name
        or ".spec." in name
        or any(d in p for d in _TEST_DIRS)
    ):
        return "testing"
    if suffix == ".sql" or "/migrations/" in p:
        return "databases"
    if suffix in _DESIGN_SUFFIXES:
        return "design"
    if (
        name.startswith("dockerfile")
        or name in {"compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"}
        or suffix in _OPS_SUFFIXES
        or any(d in p for d in _OPS_DIRS)
    ):
        return "operations"
    return "coding"


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment, comments=True)
    except ValueError:
        words = segment.split()
    # Peel what runs the program after it, until nothing changes:
    # VAR=value, sudo, npx, ``uv run``, ``python -m``, ``timeout 60``.
    while True:
        if words and (_ENV_ASSIGNMENT.match(words[0]) or words[0] in _WRAPPERS):
            words = words[1:]
        elif len(words) >= 2 and (words[0], words[1]) in _RUNNERS:
            words = words[2:]
        elif len(words) >= 3 and words[0] in {"python", "python3"} and words[1] == "-m":
            words = words[2:]
        elif len(words) >= 2 and words[0] == "timeout":
            words = words[2:]
        else:
            return words


def _skill_of_segment(words: list[str]) -> str | None:
    """The skill one command trains, or None for plain shell."""
    if not words:
        return None
    program = PurePosixPath(words[0]).name
    args = [w for w in words[1:] if not w.startswith("-")]
    sub = args[0] if args else ""
    if program in _VERSION_CONTROL:
        return "version_control"
    if program in _TESTING or program == "unittest" or (program == "playwright" and sub == "test"):
        return "testing"
    if program in _LINTING:
        return "linting"
    if program in _SECURITY:
        return "security"
    if program in _DATABASES or (program == "prisma" and sub in {"migrate", "db"}):
        return "databases"
    if program in {"docker", "podman"} and sub in {"build", "buildx"}:
        return "building"
    if program in _OPERATIONS:
        return "operations"
    if program in _PACKAGE_MANAGERS or program == "make":
        # The subcommand, script or target decides: ``npm run test:unit``
        # tests, ``make lint`` lints, ``npm ci`` builds.
        script = args[1] if sub == "run" and len(args) > 1 else sub
        if "test" in script:
            return "testing"
        if any(word in script for word in ("lint", "typecheck", "type-check", "format", "fmt", "vet", "clippy")):
            return "linting"
        if script == "audit":
            return "security"
        if program == "make" or script in _BUILD_SUBCOMMANDS or "build" in script:
            return "building"
        return None
    if program in _BUILDING or (program == "uv" and sub in {"sync", "pip", "add", "lock", "build"}):
        return "building"
    return None


def skill_of_command(command: str) -> str:
    """The skill a shell command trains: its first program that is not plain shell, else Shell."""
    for segment in _SEGMENT_SPLIT.split(command):
        skill = _skill_of_segment(_words(segment.strip()))
        if skill is not None:
            return skill
    return "shell"


def action_of(tool_name: str, tool_input: Mapping[str, Any] | None = None) -> str | None:
    """The action key ``kind:skill`` of one tool call, or None for a call that trains nothing.

    Without ``tool_input`` (a trace from before ``skill_actions``) a shell
    call trains Shell and an edit trains Coding.
    """
    data = tool_input or {}
    if tool_name == "Bash":
        command = data.get("command")
        return f"shell:{skill_of_command(command) if isinstance(command, str) else 'shell'}"
    if tool_name in EDIT_TOOLS:
        path = data.get("file_path") or data.get("notebook_path")
        return f"edit:{skill_of_path(path) if isinstance(path, str) and path else 'coding'}"
    if tool_name in _READ_TOOLS:
        return "read:reading"
    if tool_name in _SEARCH_TOOLS:
        return "search:searching"
    if tool_name in _WEB_TOOLS:
        return "web:research"
    if tool_name in _AGENT_TOOLS:
        return "agent:delegation"
    if tool_name in _PLAN_TOOLS:
        return "plan:planning"
    if tool_name.startswith("mcp__") and "__browser_" in tool_name:
        return "browser:browsing"
    if tool_name.startswith("mcp__") and tool_name.endswith("logs_query"):
        return "log:debugging"
    return None


def count_action(counts: dict[str, int], tool_name: str, tool_input: Mapping[str, Any] | None) -> None:
    """Add one tool call to ``counts`` (action key → calls)."""
    key = action_of(tool_name, tool_input)
    if key is not None:
        counts[key] = counts.get(key, 0) + 1


def actions_from_tools(tools_called: Mapping[str, int]) -> dict[str, int]:
    """A pre-``skill_actions`` trace's tool counts as action keys."""
    counts: dict[str, int] = {}
    for name, calls in tools_called.items():
        key = action_of(name)
        if key is not None and isinstance(calls, int) and calls > 0:
            counts[key] = counts.get(key, 0) + calls
    return counts
