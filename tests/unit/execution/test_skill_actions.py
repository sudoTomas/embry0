"""What each tool call was (#176): a shell command by its program, an edit by its path, as a category."""

from __future__ import annotations

import pytest

from embry0.execution.skill_actions import (
    ACTION_KEYS,
    LEGACY_ACTION_KEYS,
    action_of,
    actions_from_tools,
    category_of_command,
    category_of_path,
    count_action,
    current_actions,
)


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("git status", "vcs"),
        ("gh pr create --fill", "vcs"),
        ("cd frontend && npx vitest run", "test-runner"),
        ("sudo timeout 60 uv run pytest -q", "test-runner"),
        ("python -m pytest tests/unit", "test-runner"),
        ("./mvnw -B test", "test-runner"),
        ("go test ./...", "test-runner"),
        ("npm run test:unit", "test-runner"),
        ("make test", "test-runner"),
        ("FOO=1 python -m mypy src", "linter"),
        ("uv run ruff check .", "linter"),
        ("npm run lint", "linter"),
        ("make lint", "linter"),
        ("cargo clippy", "linter"),
        ("npm ci", "build-tool"),
        ("uv sync --all-extras", "build-tool"),
        ("make", "build-tool"),
        ("docker build -t x .", "build-tool"),
        ("go build ./cmd/x", "build-tool"),
        ("docker compose up -d", "ops-tool"),
        ("kubectl get pods", "ops-tool"),
        ("psql -c 'select 1'", "database-client"),
        ("alembic upgrade head", "database-client"),
        ("trivy fs .", "security-scanner"),
        ("npm audit", "security-scanner"),
        ("ls -la | grep x", "plain"),
        ("npm run dev", "plain"),
        ("echo 'unterminated", "plain"),
        ("", "plain"),
    ],
)
def test_a_command_is_the_category_of_its_first_program_that_is_not_plain_shell(command: str, category: str) -> None:
    assert category_of_command(command) == category


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("docs/guide.md", "docs"),
        ("README.md", "docs"),
        ("tests/README.md", "docs"),
        ("frontend/src/pages/__tests__/Page.test.tsx", "tests"),
        ("tests/unit/test_x.py", "tests"),
        ("pkg/thing_test.go", "tests"),
        ("db/migrations/0001_init.py", "sql"),
        ("schema.sql", "sql"),
        ("frontend/src/index.css", "styles"),
        ("assets/logo.svg", "styles"),
        ("Dockerfile.sandbox", "infra"),
        ("compose.yaml", "infra"),
        (".github/workflows/ci.yml", "infra"),
        ("infra/deploy.sh", "infra"),
        ("main.tf", "infra"),
        ("src/app/server.py", "source"),
        ("/workspace/src/App.tsx", "source"),
    ],
)
def test_an_edit_is_the_category_of_its_path(path: str, category: str) -> None:
    assert category_of_path(path) == category


def test_every_tool_maps_to_a_kind_and_category() -> None:
    assert action_of("Bash", {"command": "git log"}) == "shell:vcs"
    assert action_of("Edit", {"file_path": "docs/a.md"}) == "edit:docs"
    assert action_of("NotebookEdit", {"notebook_path": "nb/explore.ipynb"}) == "edit:source"
    assert action_of("Read") == "read:files" and action_of("Glob") == "read:files"
    assert action_of("Grep") == "search:code-search" and action_of("ToolSearch") == "search:code-search"
    assert action_of("mcp__playwright__browser_click") == "browser:browser"
    assert action_of("mcp__vibium__browser_navigate") == "browser:browser"
    assert action_of("mcp__context__logs_query") == "log:log-query"
    assert action_of("WebSearch") == "web:web" and action_of("WebFetch") == "web:web"
    assert action_of("Agent") == "agent:subagent" and action_of("Task") == "agent:subagent"
    assert action_of("TodoWrite") == "plan:todo"
    assert action_of("Skill") is None and action_of("mcp__github__get_issue") is None


def test_without_its_input_a_call_falls_back_to_its_tool() -> None:
    assert action_of("Bash") == "shell:plain" and action_of("Bash", {"command": None}) == "shell:plain"
    assert action_of("Write") == "edit:source" and action_of("Edit", {"file_path": ""}) == "edit:source"


def test_counting_and_reading_old_traces() -> None:
    counts: dict[str, int] = {}
    for name, tool_input in (("Bash", {"command": "pytest"}), ("Bash", {"command": "pytest -x"}), ("Skill", {})):
        count_action(counts, name, tool_input)
    assert counts == {"shell:test-runner": 2}, "a call that trains nothing is not counted"
    assert actions_from_tools({"Bash": 3, "Edit": 2, "Write": 1, "Skill": 4, "Read": 0}) == {
        "shell:plain": 3,
        "edit:source": 3,
    }


def test_every_key_the_classifier_emits_is_a_known_category() -> None:
    emitted = {
        action_of("Bash", {"command": c})
        for c in ("git log", "pytest", "ruff check", "trivy fs .", "psql", "make", "docker compose up", "ls")
    }
    emitted |= {
        action_of("Edit", {"file_path": p}) for p in ("a.md", "tests/t.py", "x.sql", "a.css", "Dockerfile", "a.py")
    }
    emitted |= {
        action_of(tool)
        for tool in ("Read", "Grep", "WebFetch", "Task", "TodoWrite", "mcp__x__browser_click", "mcp__x__logs_query")
    }
    assert emitted == set(ACTION_KEYS), "each category is emitted, and nothing outside the vocabulary"


def test_every_older_skill_key_reads_as_its_category() -> None:
    assert set(LEGACY_ACTION_KEYS.values()) == set(ACTION_KEYS), "every category had an older key, one to one"
    assert len(set(LEGACY_ACTION_KEYS.values())) == len(LEGACY_ACTION_KEYS)
    assert not set(LEGACY_ACTION_KEYS) & ACTION_KEYS, "an older key can never be mistaken for a category"
    assert current_actions({"shell:testing": 2, "shell:test-runner": 1, "edit:coding": 3, "bogus:x": 4}) == {
        "shell:test-runner": 3,
        "edit:source": 3,
        "bogus:x": 4,
    }, "older keys translate and merge with today's; anything else is kept for the pack to ignore"
