"""pr-review nodes: init, review, publish, cleanup (#30)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import ErrorCode
from embry0.storage.repositories.repos import UnregisteredRepoError
from embry0.workflows.pr_review.checkout import DELTA_DIFF, FILES_NUL
from embry0.workflows.pr_review.nodes import cleanup_node, init_pr_review_node, publish_node, review_node
from embry0.workflows.pr_review.publish import PublishError, PublishResult

SHA = "a" * 40
SHA_OLD = "9" * 40
VERDICT = {
    "summary": "One real bug.",
    "review_comments": [{"file": "src/app.py", "line": 2, "severity": "important", "comment": "Unhandled error."}],
    "checks_run": [],
    "suggested_dev_checks": [],
    "previous_findings": [],
}


def _review(**overrides: Any) -> dict[str, Any]:
    base = {
        "pipeline": "pr-review",
        "review_id": "prr-1",
        "trigger_key": f"ready:acme/widgets#7@{SHA}",
        "repo": "acme/widgets",
        "forge": "github",
        "number": 7,
        "url": "https://github.com/acme/widgets/pull/7",
        "title": "Add the login form",
        "body": "Adds the login form.",
        "author": "octocat",
        "head_ref": "feature/login",
        "head_sha": SHA,
        "base_ref": "main",
        "from_fork": False,
        "trigger": "ready",
        "depth": "full",
        "model": None,
    }
    return {**base, **overrides}


class _Docker:
    def __init__(
        self,
        *,
        checkout_out: str = f"embry0-review-checkout: delta=none base={'b' * 40}",
        fail_checkout: str = "",
        files_out: str = "src/app.py\0README.md\0",
    ) -> None:
        self.checkout_out = checkout_out
        self.fail_checkout = fail_checkout
        self.files_out = files_out
        self.cmds: list[list[str]] = []

    def build_exec_cmd(self, cid: str, cmd: list[str]) -> list[str]:
        return ["docker", "exec", cid, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, **_kw: Any) -> str:
        self.cmds.append(cmd)
        if "embry0-review-checkout:" in cmd[-1]:
            if self.fail_checkout:
                raise RuntimeError(f"Docker command failed: {self.fail_checkout}")
            return self.checkout_out
        if cmd[-2:] == ["cat", FILES_NUL]:
            return self.files_out
        return ""

    @property
    def checkout_script(self) -> str:
        return next(c[-1] for c in self.cmds if "embry0-review-checkout:" in c[-1])


class _Sandbox:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.destroyed: list[str] = []

    async def create(self, job_id: str, **kwargs: Any) -> tuple[str, str]:
        self.created.append({"job_id": job_id, **kwargs})
        return "sandbox-1", "tok-1"

    async def destroy(self, container_id: str) -> None:
        self.destroyed.append(container_id)


class _Profiles:
    async def get(self, name: str) -> dict[str, Any] | None:
        return {"name": name, "base_image": "embry0-sandbox:latest"} if name in ("slim", "dev-python") else None


class _Prefs:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    async def get(self, repo: str) -> dict[str, Any] | None:
        return self.row


class _Repos:
    def __init__(self, *, registered: bool = True) -> None:
        self.registered = registered

    async def forge_for(self, repo: str) -> str:
        if not self.registered:
            raise UnregisteredRepoError(repo)
        return "github"


class _Sessions:
    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}

    async def get(self, job_id: str, agent_type: str) -> dict[str, Any] | None:
        return self.rows.get((job_id, agent_type))

    async def upsert(
        self,
        *,
        job_id: str,
        agent_type: str,
        mode: str,
        session_id: Any = None,
        messages: Any = None,
        session_blob: Any = None,
    ) -> None:
        self.rows[(job_id, agent_type)] = {
            "job_id": job_id,
            "agent_type": agent_type,
            "mode": mode,
            "session_id": session_id,
            "messages": messages,
            "session_blob": session_blob,
        }


class _Reviews:
    def __init__(self, previous: list[dict[str, Any]] | None = None) -> None:
        self.previous = previous or []
        self.asked: list[tuple[str, int, str]] = []
        self.outcomes: list[dict[str, Any]] = []

    async def previous_reviews(
        self, repo: str, number: int, *, exclude_job_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        self.asked.append((repo, number, exclude_job_id))
        return self.previous

    async def record_outcome(self, review_id: str, *, verdict: Any, posted: bool) -> None:
        self.outcomes.append({"review_id": review_id, "verdict": verdict, "posted": posted})


class _Runner:
    def __init__(self, output: str | None = None, *, is_error: bool = False) -> None:
        self.output = json.dumps(VERDICT) if output is None else output
        self.is_error = is_error
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
        self.calls.append({"container": container, "config": config, "resume_session": resume_session})
        return AgentOutput(
            agent_type="pr-reviewer",
            is_error=self.is_error,
            error_message="Reached maximum number of turns (80)" if self.is_error else "",
            output=self.output,
            session_id="sess-new",
            messages=[{"role": "assistant", "content": "done"}],
        )


def _config(**overrides: Any) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "job_id": "job-1",
        "docker": _Docker(),
        "sandbox_manager": _Sandbox(),
        "profiles_repo": _Profiles(),
        "proxy_manager": None,
        "repos_repo": _Repos(),
        "repo_preferences_repo": _Prefs({"dev_checks": [{"name": "unit", "paths": [], "command": "pytest -q"}]}),
        "agent_sessions_repo": _Sessions(),
        "pr_reviews_repo": _Reviews(),
        "agent_runner": _Runner(),
        "credentials": {"api_key": "", "oauth_token": "tok"},
        "embry0_config": None,
    }
    configurable.update(overrides)
    return {"configurable": configurable}


def _state(**review: Any) -> dict[str, Any]:
    return {"job_id": "job-1", "repo": "acme/widgets", "pr_review": _review(**review)}


# ---- init ------------------------------------------------------------------


async def test_init_checks_out_a_same_repo_head_on_the_default_profile() -> None:
    config = _config()
    state = {**_state(), "user_env_vars": [{"key": "API_URL", "value": "http://api.test", "scope": "app"}]}
    out = await init_pr_review_node(state, config)

    rv = out["pr_review"]
    assert rv["final_status"] == "pending", rv.get("failure_summary")
    assert out["sandbox_container_id"] == "sandbox-1" and rv["sandbox_id"] == "sandbox-1"
    assert rv["changed_files"] == ["src/app.py", "README.md"] and rv["delta"] == "none"
    assert rv["base_sha"] == "b" * 40
    assert rv["dev_checks"] == [{"name": "unit", "paths": [], "command": "pytest -q"}]
    created = config["configurable"]["sandbox_manager"].created[0]
    assert created["profile"]["name"] == "slim" and created["env"]["API_URL"] == "http://api.test"
    assert created.get("git_push", False) is False, "a review sandbox never pushes"
    script = config["configurable"]["docker"].checkout_script
    assert "+refs/heads/feature/login:refs/embry0-review/head" in script and SHA in script
    assert config["configurable"]["pr_reviews_repo"].asked == [], "a ready review has no previous review to continue"


async def test_init_fetches_a_forks_head_from_the_base_repo_and_injects_no_env() -> None:
    config = _config()
    state = {
        **_state(from_fork=True, depth="read_only"),
        "user_env_vars": [{"key": "API_URL", "value": "x", "scope": "app"}],
    }
    out = await init_pr_review_node(state, config)

    assert out["pr_review"]["final_status"] == "pending"
    assert "+refs/pull/7/head:refs/embry0-review/head" in config["configurable"]["docker"].checkout_script
    assert "API_URL" not in config["configurable"]["sandbox_manager"].created[0]["env"], (
        "a fork's sandbox must not get the repo's env vars"
    )


async def test_init_uses_the_repos_sandbox_profile_preference() -> None:
    config = _config(repo_preferences_repo=_Prefs({"sandbox_profile": "dev-python"}))
    await init_pr_review_node(_state(), config)
    assert config["configurable"]["sandbox_manager"].created[0]["profile"]["name"] == "dev-python"


async def test_init_fails_without_a_profile_before_creating_anything() -> None:
    config = _config(repo_preferences_repo=_Prefs({"sandbox_profile": "gone"}))
    out = await init_pr_review_node(_state(), config)
    assert (out["pr_review"]["final_status"], out["pr_review"]["error_code"]) == (
        "failed",
        ErrorCode.PR_REVIEW_INIT_FAILED.value,
    )
    assert config["configurable"]["sandbox_manager"].created == []


async def test_init_refuses_an_unregistered_repo() -> None:
    out = await init_pr_review_node(_state(), _config(repos_repo=_Repos(registered=False)))
    assert out["pr_review"]["error_code"] == ErrorCode.REPO_UNREGISTERED.value


async def test_a_head_that_is_gone_fails_init_and_leaves_the_sandbox_for_cleanup() -> None:
    gone = f"embry0-review: commit {SHA} is no longer on the forge; the branch was rewritten"
    out = await init_pr_review_node(_state(), _config(docker=_Docker(fail_checkout=gone)))
    rv = out["pr_review"]
    assert (rv["final_status"], rv["error_code"]) == ("failed", ErrorCode.PR_REVIEW_INIT_FAILED.value)
    assert "no longer on the forge" in rv["failure_summary"]
    assert out["sandbox_container_id"] == "sandbox-1" and rv["sandbox_id"] == "sandbox-1"


async def test_a_command_review_copies_the_latest_session_it_finds() -> None:
    donor_session = {
        "job_id": "job-old",
        "agent_type": "pr-reviewer",
        "mode": "claude_max",
        "session_id": "sess-old",
        "messages": [{"role": "user", "content": "x"}],
        "session_blob": b"{}",
    }
    sessions = _Sessions({("job-old", "pr-reviewer"): donor_session})
    previous = [
        {"job_id": "job-newer", "head_sha": "8" * 40, "verdict": VERDICT},  # no session row
        {"job_id": "job-old", "head_sha": SHA_OLD, "verdict": VERDICT},
    ]
    reviews = _Reviews(previous)
    config = _config(agent_sessions_repo=sessions, pr_reviews_repo=reviews)

    out = await init_pr_review_node(_state(trigger="command"), config)

    assert reviews.asked == [("acme/widgets", 7, "job-1")]
    assert out["pr_review"]["previous"] == {
        "job_id": "job-old",
        "head_sha": SHA_OLD,
        "verdict": VERDICT,
        "session_copied": True,
    }
    copied = sessions.rows[("job-1", "pr-reviewer")]
    assert (copied["session_id"], copied["session_blob"], copied["mode"]) == ("sess-old", b"{}", "claude_max")
    assert SHA_OLD in config["configurable"]["docker"].checkout_script, "the delta must start at the reviewed sha"


async def test_a_command_review_without_any_session_keeps_the_newest_verdict() -> None:
    reviews = _Reviews([{"job_id": None, "head_sha": SHA_OLD, "verdict": VERDICT}])  # its job was purged
    out = await init_pr_review_node(_state(trigger="command"), _config(pr_reviews_repo=reviews))
    assert out["pr_review"]["previous"] == {
        "job_id": None,
        "head_sha": SHA_OLD,
        "verdict": VERDICT,
        "session_copied": False,
    }


# ---- review ----------------------------------------------------------------


def _ready(**review: Any) -> dict[str, Any]:
    state = _state(**review)
    state["pr_review"].update(
        {
            "final_status": "pending",
            "sandbox_id": "sandbox-1",
            "changed_files": ["src/app.py"],
            "delta": review.get("delta", "none"),
            "dev_checks": [],
        }
    )
    state["sandbox_container_id"] = "sandbox-1"
    return state


async def test_a_first_review_gets_the_full_prompt_and_stores_the_verdict() -> None:
    config = _config()
    out = await review_node(_ready(), config)

    runner = config["configurable"]["agent_runner"]
    [call] = runner.calls
    assert "Pull request #7 on acme/widgets" in call["config"]["prompt"] and call["resume_session"] is None
    assert call["config"]["agent_type"] == "pr-reviewer" and "Bash" in call["config"]["tools"]
    assert call["config"]["timeout_seconds"] == 3600
    assert out["pr_review"]["verdict"]["summary"] == "One real bug."
    assert ("job-1", "pr-reviewer") in config["configurable"]["agent_sessions_repo"].rows, (
        "the session must be kept for the next review"
    )


async def test_read_only_depth_takes_bash_away() -> None:
    config = _config()
    await review_node(_ready(depth="read_only"), config)
    assert config["configurable"]["agent_runner"].calls[0]["config"]["tools"] == ["Read", "Glob", "Grep"]


async def test_the_jobs_model_reaches_the_runner() -> None:
    config = _config()
    state = {**_ready(), "agent_models_override": {"pr-reviewer": "claude-sonnet-5"}}
    await review_node(state, config)
    assert config["configurable"]["agent_runner"].calls[0]["config"]["model"] == "claude-sonnet-5"


def _copied_session_state(delta: str) -> tuple[dict[str, Any], dict[str, Any]]:
    sessions = _Sessions(
        {
            ("job-1", "pr-reviewer"): {
                "job_id": "job-1",
                "agent_type": "pr-reviewer",
                "mode": "claude_max",
                "session_id": "sess-old",
                "messages": None,
                "session_blob": b"{}",
            }
        }
    )
    state = _ready(trigger="command", delta=delta)
    state["pr_review"]["previous"] = {
        "job_id": "job-old",
        "head_sha": SHA_OLD,
        "verdict": VERDICT,
        "session_copied": True,
    }
    return state, _config(agent_sessions_repo=sessions)


async def test_a_resumed_review_with_a_delta_gets_the_delta_prompt() -> None:
    state, config = _copied_session_state("yes")
    await review_node(state, config)
    call = config["configurable"]["agent_runner"].calls[0]
    assert DELTA_DIFF in call["config"]["prompt"] and SHA_OLD[:12] in call["config"]["prompt"]
    assert call["resume_session"] is not None and call["resume_session"].session_id == "sess-old"


async def test_a_resumed_review_of_a_rewritten_branch_gets_the_rewritten_prompt() -> None:
    state, config = _copied_session_state("rewritten")
    await review_node(state, config)
    prompt = config["configurable"]["agent_runner"].calls[0]["config"]["prompt"]
    assert "was rewritten" in prompt and DELTA_DIFF not in prompt


async def test_without_a_session_the_earlier_findings_ride_the_full_prompt() -> None:
    state = _ready(trigger="command")
    state["pr_review"]["previous"] = {"job_id": None, "head_sha": SHA_OLD, "verdict": VERDICT, "session_copied": False}
    config = _config()
    await review_node(state, config)
    call = config["configurable"]["agent_runner"].calls[0]
    assert call["resume_session"] is None
    assert (
        "An earlier review of this pull request" in call["config"]["prompt"]
        and "Unhandled error." in call["config"]["prompt"]
    )


async def test_an_unreadable_verdict_fails_the_job_and_posts_nothing() -> None:
    config = _config(agent_runner=_Runner("I looked around and it seems fine."))
    out = await review_node(_ready(), config)
    rv = out["pr_review"]
    assert (rv["final_status"], rv["error_code"]) == ("failed", ErrorCode.PR_REVIEW_NO_VERDICT.value)
    assert "verdict" not in rv and "seems fine" in rv["raw_output_tail"]


async def test_an_agent_error_fails_the_job() -> None:
    out = await review_node(_ready(), _config(agent_runner=_Runner(is_error=True)))
    assert (
        out["pr_review"]["final_status"] == "failed"
        and "maximum number of turns" in out["pr_review"]["failure_summary"]
    )


async def test_review_does_nothing_after_a_failed_init() -> None:
    state = _ready()
    state["pr_review"]["final_status"] = "failed"
    config = _config()
    assert await review_node(state, config) == {}
    assert config["configurable"]["agent_runner"].calls == []


# ---- publish ---------------------------------------------------------------


def _reviewed(**review: Any) -> dict[str, Any]:
    state = _ready(**review)
    state["pr_review"]["verdict"] = VERDICT
    return state


@pytest.mark.parametrize(("forge", "target"), [("github", "publish_github"), ("gitlab", "publish_gitlab")])
async def test_publish_posts_on_the_jobs_forge_and_records_it(forge: str, target: str) -> None:
    reviews = _Reviews()
    config = _config(pr_reviews_repo=reviews)
    poster = AsyncMock(return_value=PublishResult(inline_count=1, listed_count=0))
    with (
        patch(f"embry0.workflows.pr_review.publish.{target}", poster),
        patch("embry0.workflows.pr_review.nodes._github_client", return_value="gh"),
        patch("embry0.workflows.pr_review.nodes._gitlab_client", return_value="gl"),
    ):
        out = await publish_node(_reviewed(forge=forge), config)

    assert out["pr_review"]["final_status"] == "completed"
    args, kwargs = poster.call_args
    assert args == ("gh" if forge == "github" else "gl",)
    assert (kwargs["repo"], kwargs["number"], kwargs["head_sha"]) == ("acme/widgets", 7, SHA)
    assert kwargs["ctx"].job_url == "http://localhost:8200/jobs/job-1"
    assert reviews.outcomes == [{"review_id": "prr-1", "verdict": VERDICT, "posted": True}]


async def test_a_refused_publish_fails_the_job_with_the_forges_message_and_keeps_the_verdict() -> None:
    reviews = _Reviews()
    poster = AsyncMock(side_effect=PublishError("GitHub refused the review (403): Resource not accessible"))
    with (
        patch("embry0.workflows.pr_review.publish.publish_github", poster),
        patch("embry0.workflows.pr_review.nodes._github_client"),
    ):
        out = await publish_node(_reviewed(), _config(pr_reviews_repo=reviews))
    rv = out["pr_review"]
    assert (rv["final_status"], rv["error_code"]) == ("failed", ErrorCode.PR_REVIEW_PUBLISH_FAILED.value)
    assert "Resource not accessible" in rv["failure_summary"]
    assert reviews.outcomes == [{"review_id": "prr-1", "verdict": VERDICT, "posted": False}]


# ---- cleanup ---------------------------------------------------------------


async def test_cleanup_destroys_the_sandbox_and_ends_an_unfinished_review_as_failed() -> None:
    config = _config()
    out = await cleanup_node(_ready(), config)
    assert config["configurable"]["sandbox_manager"].destroyed == ["sandbox-1"]
    assert out["sandbox_container_id"] is None
    assert (out["pr_review"]["final_status"], out["pr_review"]["error_code"]) == (
        "failed",
        ErrorCode.PR_REVIEW_NO_VERDICT.value,
    )


async def test_cleanup_keeps_a_completed_review_completed() -> None:
    state = _reviewed()
    state["pr_review"]["final_status"] = "completed"
    out = await cleanup_node(state, _config())
    assert out["pr_review"]["final_status"] == "completed"


async def test_the_graph_runs_init_review_publish_cleanup() -> None:
    from embry0.workflows.pr_review.graph import PrReviewWorkflow

    graph = PrReviewWorkflow().compile().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {
        ("__start__", "init_pr_review"),
        ("init_pr_review", "review"),
        ("review", "publish"),
        ("publish", "cleanup"),
        ("cleanup", "__end__"),
    } <= edges


# ---- controller additions: env and Bash for forks, path listing, publish errors, outcome records ----


@pytest.mark.parametrize(("from_fork", "depth"), [(True, "full"), (False, "read_only")])
async def test_a_fork_or_a_read_only_depth_gets_no_repo_env(from_fork: bool, depth: str) -> None:
    config = _config()
    state = {
        **_state(from_fork=from_fork, depth=depth),
        "user_env_vars": [{"key": "API_URL", "value": "x", "scope": "app"}],
    }
    await init_pr_review_node(state, config)
    assert "API_URL" not in config["configurable"]["sandbox_manager"].created[0]["env"], (
        f"from_fork={from_fork} depth={depth}: code this sandbox runs could read the repo's env vars"
    )


@pytest.mark.parametrize(("from_fork", "depth"), [(True, "full"), (True, "targeted"), (False, "read_only")])
async def test_a_fork_or_a_read_only_depth_has_no_bash(from_fork: bool, depth: str) -> None:
    config = _config()
    await review_node(_ready(from_fork=from_fork, depth=depth), config)
    tools = config["configurable"]["agent_runner"].calls[0]["config"]["tools"]
    assert tools == ["Read", "Glob", "Grep"], f"from_fork={from_fork} depth={depth} kept {tools}"


async def test_changed_paths_are_read_unquoted_and_without_control_characters() -> None:
    listing = "src/café.py\0with space.py\0evil\x1b[2J\nname.py\0\0"
    out = await init_pr_review_node(_state(), _config(docker=_Docker(files_out=listing)))
    assert out["pr_review"]["changed_files"] == ["src/café.py", "with space.py", "evil[2Jname.py"]


def _status_error(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/repos/acme/widgets/pulls/7/files")
    response = httpx.Response(status, json={"message": message}, request=request)
    return httpx.HTTPStatusError(f"Client error '{status}'", request=request, response=response)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(403, "Resource not accessible by integration"), "Resource not accessible by integration"),
        (httpx.ConnectError("connection refused by api.github.com"), "connection refused by api.github.com"),
    ],
)
async def test_a_forge_transport_or_status_error_fails_publish_with_the_forges_words(
    error: Exception, expected: str
) -> None:
    reviews = _Reviews()
    poster = AsyncMock(side_effect=error)
    with (
        patch("embry0.workflows.pr_review.publish.publish_github", poster),
        patch("embry0.workflows.pr_review.nodes._github_client"),
    ):
        out = await publish_node(_reviewed(), _config(pr_reviews_repo=reviews))
    rv = out["pr_review"]
    assert (rv["final_status"], rv["error_code"]) == ("failed", ErrorCode.PR_REVIEW_PUBLISH_FAILED.value)
    assert expected in rv["failure_summary"], rv["failure_summary"]
    if isinstance(error, httpx.HTTPStatusError):
        assert "403" in rv["failure_summary"], rv["failure_summary"]
    assert reviews.outcomes == [{"review_id": "prr-1", "verdict": VERDICT, "posted": False}]


async def test_a_gitlab_publish_that_failed_after_inline_comments_records_them_as_posted() -> None:
    reviews = _Reviews()
    poster = AsyncMock(side_effect=PublishError("GitLab refused the review summary (500): boom", inline_posted=2))
    with (
        patch("embry0.workflows.pr_review.publish.publish_gitlab", poster),
        patch("embry0.workflows.pr_review.nodes._gitlab_client"),
    ):
        out = await publish_node(_reviewed(forge="gitlab"), _config(pr_reviews_repo=reviews))
    rv = out["pr_review"]
    assert (rv["final_status"], rv["error_code"]) == ("failed", ErrorCode.PR_REVIEW_PUBLISH_FAILED.value)
    assert rv["inline_count"] == 2 and "2 inline comments" in rv["failure_summary"], rv["failure_summary"]
    assert reviews.outcomes == [{"review_id": "prr-1", "verdict": VERDICT, "posted": True}], (
        "comments that landed on the merge request must count as posted"
    )


async def test_no_failure_before_publish_writes_an_outcome() -> None:
    """record_outcome(verdict=None) would erase a stored verdict: no failure path may call it."""
    for runner in (_Runner("no verdict here"), _Runner(is_error=True)):
        reviews = _Reviews()
        await review_node(_ready(), _config(agent_runner=runner, pr_reviews_repo=reviews))
        assert reviews.outcomes == []
    reviews = _Reviews()
    out = await publish_node(_ready(), _config(pr_reviews_repo=reviews))  # no verdict in state
    assert out["pr_review"]["error_code"] == ErrorCode.PR_REVIEW_NO_VERDICT.value and reviews.outcomes == []


# ---- fix round 1: a crashed or overridden review stays contained ----


class _CrashingRunner(_Runner):
    async def run(
        self,
        container: str,
        config: dict[str, Any],
        network: Any = None,
        on_event: Any = None,
        *,
        resume_session: Any = None,
    ) -> AgentOutput:
        raise RuntimeError("Docker command failed: exec died, token=ghp_" + "x" * 36)


async def test_a_crashed_reviewer_fails_the_job_and_the_graph_still_cleans_up() -> None:
    from embry0.workflows.pr_review.graph import PrReviewWorkflow

    config = _config(agent_runner=_CrashingRunner())
    final = await PrReviewWorkflow().compile().ainvoke(_state(), config)

    rv = final["pr_review"]
    assert rv["final_status"] == "failed", rv
    assert rv["error_code"] == ErrorCode.PR_REVIEW_NO_VERDICT.value, rv["error_code"]
    assert "the pull request reviewer crashed" in rv["failure_summary"] and "exec died" in rv["failure_summary"]
    assert "ghp_" + "x" * 36 not in rv["failure_summary"], "the crash text must be redacted"
    assert config["configurable"]["sandbox_manager"].destroyed == ["sandbox-1"], "cleanup must run after a crash"


@pytest.mark.parametrize(("from_fork", "depth"), [(True, "full"), (False, "read_only")])
async def test_a_tools_override_cannot_give_bash_back(from_fork: bool, depth: str) -> None:
    config = _config()
    state = {
        **_ready(from_fork=from_fork, depth=depth),
        "pipeline_config": {"agent_tools": {"pr-reviewer": ["Read", "Bash"]}},
    }
    await review_node(state, config)
    tools = config["configurable"]["agent_runner"].calls[0]["config"]["tools"]
    assert "Bash" not in tools, f"from_fork={from_fork} depth={depth}: the agent_tools override restored {tools}"


async def test_a_tools_override_still_applies_where_bash_is_allowed() -> None:
    config = _config()
    state = {**_ready(), "pipeline_config": {"agent_tools": {"pr-reviewer": ["Read", "Bash"]}}}
    await review_node(state, config)
    assert config["configurable"]["agent_runner"].calls[0]["config"]["tools"] == ["Read", "Bash"]
