"""One pull request, reviewed from webhook to publish, then re-reviewed on a command (#30).

Real: the webhook route, PrReviewDispatcher, IssueExecutor, the pr-review
graph, run_agent_node and every repository, against Postgres. Stubbed: the
sandbox, its docker exec, the agent, GitHub's REST API and the checkpointer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.execution.agent_runner import AgentOutput
from embry0.execution.github_client import GitHubClient
from embry0.services.issue_executor import IssueExecutor
from embry0.services.pr_review.dispatcher import PrReviewDispatcher
from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
from embry0.storage.repositories.agent_sessions import AgentSessionsRepository
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.pr_reviews import PrReviewsRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles
from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent
from embry0.workflows.pr_review.checkout import DELTA_DIFF, FILES_NUL
from embry0.workflows.pr_review.summary import review_marker
from embry0.workflows.registry import WorkflowRegistry
from tests.fixtures.webhooks import github_issue_comment, github_review_pull_request

pytestmark = [pytest.mark.requires_postgres]

SHA = "a" * 40
SHA_2 = "b" * 40
PATCH = "@@ -1,2 +1,3 @@\n a = 1\n+b = 2\n c = 3"
FIRST = {
    "summary": "One unhandled error.",
    "review_comments": [{"file": "src/app.py", "line": 2, "severity": "important", "comment": "b is never checked."}],
    "checks_run": [{"name": "unit", "command": "pytest -q", "status": "passed", "output_tail": ""}],
    "suggested_dev_checks": [],
    "previous_findings": [],
}
SECOND = {
    "summary": "The earlier finding is fixed.",
    "review_comments": [],
    "checks_run": [{"name": "unit", "command": "pytest -q", "status": "passed", "output_tail": ""}],
    "suggested_dev_checks": [],
    "previous_findings": [{"file": "src/app.py", "line": 2, "comment": "b is never checked.", "status": "resolved"}],
}


class _Docker:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def build_exec_cmd(self, cid: str, cmd: list[str]) -> list[str]:
        return ["docker", "exec", cid, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, **_kw: Any) -> str:
        if "embry0-review-checkout:" in cmd[-1]:
            self.scripts.append(cmd[-1])
            delta = "yes" if "prev=" in cmd[-1] else "none"
            return f"embry0-review-checkout: delta={delta} base={'c' * 40}"
        if cmd[-2:] == ["cat", FILES_NUL]:
            return "src/app.py\0"
        return ""


class _Sandbox:
    def __init__(self, docker: _Docker) -> None:
        self.runtime = type("Runtime", (), {"client": docker})()
        self.destroyed: list[str] = []

    def _read_oauth_token(self) -> str:
        return "oauth-test-token"

    async def create(self, job_id: str, **_kw: Any) -> tuple[str, str]:
        return f"sandbox-{job_id}", "tok"

    async def destroy(self, container_id: str) -> None:
        self.destroyed.append(container_id)


class _Runner:
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        container: str,
        config: dict[str, Any],
        network: Any = None,
        on_event: Any = None,
        *,
        resume_session: Any = None,
    ) -> AgentOutput:
        self.calls.append({"config": config, "resume_session": resume_session})
        return AgentOutput(
            agent_type="pr-reviewer",
            output=json.dumps(self.outputs.pop(0)),
            session_id=f"sess-{len(self.calls)}",
            messages=[{"role": "assistant", "content": "reviewed"}],
        )


class _Forge:
    """GitHub's REST API for one pull request whose head can move."""

    def __init__(self) -> None:
        self.head = SHA
        self.reviews: list[dict[str, Any]] = []

    def client(self) -> GitHubClient:
        async def _sleep(_seconds: float) -> None:
            return None

        return GitHubClient(
            default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)), sleep=_sleep
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/repos/acme/widgets/pulls/7":
            return httpx.Response(200, json=github_review_pull_request(head_sha=self.head)["pull_request"])
        if request.method == "GET" and path == "/repos/acme/widgets/pulls/7/files":
            return httpx.Response(200, json=[{"filename": "src/app.py", "status": "modified", "patch": PATCH}])
        if request.method == "POST" and path == "/repos/acme/widgets/pulls/7/reviews":
            self.reviews.append(json.loads(request.content))
            return httpx.Response(200, json={"id": len(self.reviews)})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


@asynccontextmanager
async def _memory_checkpointer(_url: str) -> AsyncIterator[InMemorySaver]:
    yield InMemorySaver()


async def _finished(jobs: JobsRepository, job_id: str) -> dict[str, Any]:
    for _ in range(300):
        row = await jobs.get(job_id)
        if row is not None and row["status"] in ("completed", "failed"):
            return row
        await asyncio.sleep(0.1)
    pytest.fail(f"job {job_id} never finished")


async def test_a_ready_pull_request_is_reviewed_then_re_reviewed_on_a_command(db_with_migrations: Any) -> None:
    db = db_with_migrations
    repos = ReposRepository(db)
    prefs = RepoPreferencesRepository(db)
    jobs = JobsRepository(db)
    await repos.register("acme/widgets", forge="github")
    await prefs.upsert(
        "acme/widgets",
        pr_review={"enabled": True, "depth": "targeted", "model": None},
        dev_checks=[
            {"name": "unit", "paths": ["src"], "command": "pytest -q"},
            {"name": "docs", "paths": ["docs"], "command": "make docs"},
        ],
    )
    await seed_builtin_sandbox_profiles(SandboxProfilesRepository(db))
    await seed_pr_reviewer_agent(AgentDefinitionsRepository(db))

    docker = _Docker()
    sandbox = _Sandbox(docker)
    runner = _Runner([FIRST, SECOND])
    forge = _Forge()
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_webhook_secret="")
    executor = IssueExecutor(
        issues_repo=IssuesRepository(db),
        jobs_repo=jobs,
        traces_repo=TracesRepository(db),
        workflow_registry=WorkflowRegistry(),
        database_url="postgresql://unused/unused",
        db=db,
        config=config,
        sandbox_manager=sandbox,
        agent_runner=runner,
        repo_preferences_repo=prefs,
        repos_repo=repos,
        profiles_repo=SandboxProfilesRepository(db),
        agent_sessions_repo=AgentSessionsRepository(db),
    )
    app = create_app(config)
    app.state.pr_review_dispatcher = PrReviewDispatcher(
        config=config,
        repos_repo=repos,
        prefs_repo=prefs,
        jobs_repo=jobs,
        reviews_repo=PrReviewsRepository(db),
        executor=executor,
        github=forge.client(),
        gitlab=None,
    )

    async def deliver(event: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook",
                content=json.dumps(payload).encode(),
                headers={"X-GitHub-Event": event, "Content-Type": "application/json"},
            )
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    with (
        patch("embry0.services._executor_run.checkpointer_context", _memory_checkpointer),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.workflows.pr_review.nodes._github_client", side_effect=lambda _c: forge.client()),
    ):
        # 1. Marked ready → a review lands on the pull request.
        first = await deliver("pull_request", github_review_pull_request())
        first_job = first["pr_review"]["job_id"]
        row = await _finished(jobs, first_job)
        assert row["status"] == "completed", (row["error_code"], row["error_message"])
        assert row["result_summary"] == "One unhandled error."
        assert row["pr_url"] is None, "a review job must not claim the pull request"

        [review] = forge.reviews
        assert (review["event"], review["commit_id"]) == ("COMMENT", SHA)
        assert [(c["path"], c["line"], c["side"]) for c in review["comments"]] == [("src/app.py", 2, "RIGHT")]
        assert review["comments"][0]["body"].startswith("**Important** — b is never checked.")
        assert "**passed** `unit`: `pytest -q`" in review["body"]
        assert review["body"].rstrip().endswith(review_marker(first_job, SHA))
        prompt = runner.calls[0]["config"]["prompt"]
        assert "Changed files in this branch (1):\n- src/app.py" in prompt, "the changed paths never reached the prompt"
        assert "- unit: `pytest -q`" in prompt, "targeted depth must select the check whose paths the change touches"
        assert "make docs" not in prompt, "targeted depth ran a check whose paths the change does not touch"
        assert sandbox.destroyed == [f"sandbox-{first_job}"]

        # 2. A redelivery of the same event is refused, durably.
        again = await deliver("pull_request", github_review_pull_request())
        assert again["pr_review"] == {"dispatched": False, "reason": "duplicate"}

        # 3. A push, then a command: the review continues the first one's session.
        forge.head = SHA_2
        comment = github_issue_comment(
            body="Fixed it.\n/embry0 review", repo="acme/widgets", number=7, on_pull_request=True
        )
        second = await deliver("issue_comment", comment)
        assert second["action"] == "pr_review" and second["dispatched"] is True, second
        second_job = second["job_id"]
        row = await _finished(jobs, second_job)
        assert row["status"] == "completed", (row["error_code"], row["error_message"])

    resumed = runner.calls[1]
    assert resumed["resume_session"] is not None and resumed["resume_session"].session_id == "sess-1", (
        "the re-review must continue the first review's session"
    )
    assert DELTA_DIFF in resumed["config"]["prompt"] and SHA[:12] in resumed["config"]["prompt"]
    assert f"prev={SHA}" in docker.scripts[1]
    assert len(forge.reviews) == 2
    assert forge.reviews[1]["commit_id"] == SHA_2
    assert "resolved — `src/app.py:2` b is never checked." in forge.reviews[1]["body"]

    rows = await db.fetch("SELECT job_id, trigger, posted_at FROM pr_reviews ORDER BY created_at")
    assert [(r["job_id"], r["trigger"]) for r in rows] == [(first_job, "ready"), (second_job, "command")]
    assert all(r["posted_at"] is not None for r in rows)
