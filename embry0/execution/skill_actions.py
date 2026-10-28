"""What each tool call was (#176): an action key ``kind:category``, never a skill.

The agent node counts a run's tool calls into these keys as the events
stream past and stores them on the trace (``traces.skill_actions``). The
active content pack maps each category to the skill it trains and prices
each key by its kind, so a pack that renames, merges or removes skills
re-prices every stored run.

A shell command is the category of the first program in it that is not
plain shell (``cd x && pytest`` is a test run); a file edit is the category
of the file's path (docs, tests, SQL, styles, infra, else source). Traces
written between migration 107 and #176 hold the older ``kind:skill`` keys,
which :func:`current_actions` translates through :data:`LEGACY_ACTION_KEYS`;
traces from before 107 keep only tool names, which read through
:func:`actions_from_tools` with no input: a shell call is plain shell, an
edit is source.

This module runs in the orchestrator, never in a sandbox image.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

#: Every category the classifier emits, by kind. Stable: a category is never renamed or reused.
ACTION_CATEGORIES: dict[str, tuple[str, ...]] = {
    "edit": ("docs", "tests", "sql", "styles", "infra", "source"),
    "shell": ("vcs", "test-runner", "linter", "security-scanner", "database-client", "build-tool", "ops-tool", "plain"),
    "read": ("files",),
    "search": ("code-search",),
    "browser": ("browser",),
    "log": ("log-query",),
    "web": ("web",),
    "agent": ("subagent",),
    "plan": ("todo",),
}

#: Every action key, ``kind:category``.
ACTION_KEYS: frozenset[str] = frozenset(f"{kind}:{c}" for kind, cats in ACTION_CATEGORIES.items() for c in cats)

#: The ``kind:skill`` keys traces held between migration 107 and #176, each to the category it was.
LEGACY_ACTION_KEYS: dict[str, str] = {
    "edit:coding": "edit:source",
    "edit:testing": "edit:tests",
    "edit:documentation": "edit:docs",
    "edit:databases": "edit:sql",
    "edit:design": "edit:styles",
    "edit:operations": "edit:infra",
    "shell:version_control": "shell:vcs",
    "shell:testing": "shell:test-runner",
    "shell:linting": "shell:linter",
    "shell:security": "shell:security-scanner",
    "shell:databases": "shell:database-client",
    "shell:building": "shell:build-tool",
    "shell:operations": "shell:ops-tool",
    "shell:shell": "shell:plain",
    "read:reading": "read:files",
    "search:searching": "search:code-search",
    "web:research": "web:web",
    "agent:delegation": "agent:subagent",
    "plan:planning": "plan:todo",
    "browser:browsing": "browser:browser",
    "log:debugging": "log:log-query",
}

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


def category_of_path(path: str) -> str:
    """The category of an edit of ``path``."""
    p = "/" + path.replace("\\", "/").lstrip("/").lower()
    name = PurePosixPath(p).name
    suffix = PurePosixPath(p).suffix
    if suffix in _DOC_SUFFIXES or "/docs/" in p:
        return "docs"
    if (
        name.startswith("test_")
        or name.endswith(("_test.py", "_test.go"))
        or ".test." in name
        or ".spec." in name
        or any(d in p for d in _TEST_DIRS)
    ):
        return "tests"
    if suffix == ".sql" or "/migrations/" in p:
        return "sql"
    if suffix in _DESIGN_SUFFIXES:
        return "styles"
    if (
        name.startswith("dockerfile")
        or name in {"compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"}
        or suffix in _OPS_SUFFIXES
        or any(d in p for d in _OPS_DIRS)
    ):
        return "infra"
    return "source"


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


def _category_of_segment(words: list[str]) -> str | None:
    """The category of one command, or None for plain shell."""
    if not words:
        return None
    program = PurePosixPath(words[0]).name
    args = [w for w in words[1:] if not w.startswith("-")]
    sub = args[0] if args else ""
    if program in _VERSION_CONTROL:
        return "vcs"
    if program in _TESTING or program == "unittest" or (program == "playwright" and sub == "test"):
        return "test-runner"
    if program in _LINTING:
        return "linter"
    if program in _SECURITY:
        return "security-scanner"
    if program in _DATABASES or (program == "prisma" and sub in {"migrate", "db"}):
        return "database-client"
    if program in {"docker", "podman"} and sub in {"build", "buildx"}:
        return "build-tool"
    if program in _OPERATIONS:
        return "ops-tool"
    if program in _PACKAGE_MANAGERS or program == "make":
        # The subcommand, script or target decides: ``npm run test:unit``
        # tests, ``make lint`` lints, ``npm ci`` builds.
        script = args[1] if sub == "run" and len(args) > 1 else sub
        if "test" in script:
            return "test-runner"
        if any(word in script for word in ("lint", "typecheck", "type-check", "format", "fmt", "vet", "clippy")):
            return "linter"
        if script == "audit":
            return "security-scanner"
        if program == "make" or script in _BUILD_SUBCOMMANDS or "build" in script:
            return "build-tool"
        return None
    if program in _BUILDING or (program == "uv" and sub in {"sync", "pip", "add", "lock", "build"}):
        return "build-tool"
    return None


def category_of_command(command: str) -> str:
    """The category of a shell command: its first program that is not plain shell, else ``plain``."""
    for segment in _SEGMENT_SPLIT.split(command):
        category = _category_of_segment(_words(segment.strip()))
        if category is not None:
            return category
    return "plain"


def action_of(tool_name: str, tool_input: Mapping[str, Any] | None = None) -> str | None:
    """The action key ``kind:category`` of one tool call, or None for a call the game does not count.

    Without ``tool_input`` (a trace from before ``skill_actions``) a shell
    call is plain shell and an edit is source.
    """
    data = tool_input or {}
    if tool_name == "Bash":
        command = data.get("command")
        return f"shell:{category_of_command(command) if isinstance(command, str) else 'plain'}"
    if tool_name in EDIT_TOOLS:
        path = data.get("file_path") or data.get("notebook_path")
        return f"edit:{category_of_path(path) if isinstance(path, str) and path else 'source'}"
    if tool_name in _READ_TOOLS:
        return "read:files"
    if tool_name in _SEARCH_TOOLS:
        return "search:code-search"
    if tool_name in _WEB_TOOLS:
        return "web:web"
    if tool_name in _AGENT_TOOLS:
        return "agent:subagent"
    if tool_name in _PLAN_TOOLS:
        return "plan:todo"
    if tool_name.startswith("mcp__") and "__browser_" in tool_name:
        return "browser:browser"
    if tool_name.startswith("mcp__") and tool_name.endswith("logs_query"):
        return "log:log-query"
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


def current_actions(stored: Mapping[str, int]) -> dict[str, int]:
    """A trace's stored counts in today's keys: an older ``kind:skill`` key becomes its category.

    A key that is neither (written by a later release, or by hand) is kept as
    it is; the pack's map decides whether it earns anything.
    """
    counts: dict[str, int] = {}
    for key, calls in stored.items():
        current = LEGACY_ACTION_KEYS.get(key, key)
        if isinstance(calls, int):
            counts[current] = counts.get(current, 0) + calls
    return counts
