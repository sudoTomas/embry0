"""What each tool call trains (progression spec 2026-10-24): a shell command by its program, an edit by its path."""

from __future__ import annotations

import pytest

from embry0.execution.skill_actions import action_of, actions_from_tools, count_action, skill_of_command, skill_of_path


@pytest.mark.parametrize(
    ("command", "skill"),
    [
        ("git status", "version_control"),
        ("gh pr create --fill", "version_control"),
        ("cd frontend && npx vitest run", "testing"),
        ("sudo timeout 60 uv run pytest -q", "testing"),
        ("python -m pytest tests/unit", "testing"),
        ("./mvnw -B test", "testing"),
        ("go test ./...", "testing"),
        ("npm run test:unit", "testing"),
        ("make test", "testing"),
        ("FOO=1 python -m mypy src", "linting"),
        ("uv run ruff check .", "linting"),
        ("npm run lint", "linting"),
        ("make lint", "linting"),
        ("cargo clippy", "linting"),
        ("npm ci", "building"),
        ("uv sync --all-extras", "building"),
        ("make", "building"),
        ("docker build -t x .", "building"),
        ("go build ./cmd/x", "building"),
        ("docker compose up -d", "operations"),
        ("kubectl get pods", "operations"),
        ("psql -c 'select 1'", "databases"),
        ("alembic upgrade head", "databases"),
        ("trivy fs .", "security"),
        ("npm audit", "security"),
        ("ls -la | grep x", "shell"),
        ("npm run dev", "shell"),
        ("echo 'unterminated", "shell"),
        ("", "shell"),
    ],
)
def test_a_command_trains_the_skill_of_its_first_program_that_is_not_plain_shell(command: str, skill: str) -> None:
    assert skill_of_command(command) == skill


@pytest.mark.parametrize(
    ("path", "skill"),
    [
        ("docs/guide.md", "documentation"),
        ("README.md", "documentation"),
        ("tests/README.md", "documentation"),
        ("frontend/src/pages/__tests__/Page.test.tsx", "testing"),
        ("tests/unit/test_x.py", "testing"),
        ("pkg/thing_test.go", "testing"),
        ("db/migrations/0001_init.py", "databases"),
        ("schema.sql", "databases"),
        ("frontend/src/index.css", "design"),
        ("assets/logo.svg", "design"),
        ("Dockerfile.sandbox", "operations"),
        ("compose.yaml", "operations"),
        (".github/workflows/ci.yml", "operations"),
        ("infra/deploy.sh", "operations"),
        ("main.tf", "operations"),
        ("src/app/server.py", "coding"),
        ("/workspace/src/App.tsx", "coding"),
    ],
)
def test_an_edit_trains_the_skill_of_its_path(path: str, skill: str) -> None:
    assert skill_of_path(path) == skill


def test_every_tool_maps_to_a_kind_and_skill() -> None:
    assert action_of("Bash", {"command": "git log"}) == "shell:version_control"
    assert action_of("Edit", {"file_path": "docs/a.md"}) == "edit:documentation"
    assert action_of("NotebookEdit", {"notebook_path": "nb/explore.ipynb"}) == "edit:coding"
    assert action_of("Read") == "read:reading" and action_of("Glob") == "read:reading"
    assert action_of("Grep") == "search:searching" and action_of("ToolSearch") == "search:searching"
    assert action_of("mcp__playwright__browser_click") == "browser:browsing"
    assert action_of("mcp__vibium__browser_navigate") == "browser:browsing"
    assert action_of("mcp__context__logs_query") == "log:debugging"
    assert action_of("WebSearch") == "web:research" and action_of("WebFetch") == "web:research"
    assert action_of("Agent") == "agent:delegation" and action_of("Task") == "agent:delegation"
    assert action_of("TodoWrite") == "plan:planning"
    assert action_of("Skill") is None and action_of("mcp__github__get_issue") is None


def test_without_its_input_a_call_falls_back_to_its_tool() -> None:
    assert action_of("Bash") == "shell:shell" and action_of("Bash", {"command": None}) == "shell:shell"
    assert action_of("Write") == "edit:coding" and action_of("Edit", {"file_path": ""}) == "edit:coding"


def test_counting_and_reading_old_traces() -> None:
    counts: dict[str, int] = {}
    for name, tool_input in (("Bash", {"command": "pytest"}), ("Bash", {"command": "pytest -x"}), ("Skill", {})):
        count_action(counts, name, tool_input)
    assert counts == {"shell:testing": 2}, "a call that trains nothing is not counted"
    assert actions_from_tools({"Bash": 3, "Edit": 2, "Write": 1, "Skill": 4, "Read": 0}) == {
        "shell:shell": 3,
        "edit:coding": 3,
    }
