"""The developer records only a pull request of the job's own repository, of those its agent's output names."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.unit.workflows.test_developer_branch_verification import _make_state


class _Repos:
    """The repository registry: each repository's forge, GitHub for any other."""

    def __init__(self, forges: dict[str, str]) -> None:
        self.forges = forges

    async def forge_for(self, repo: str) -> str:
        return self.forges.get(repo, "github")


#: The deployment's GitLab in these tests.
GITLAB_URL = "https://gitlab.example.test"


async def _recorded(output: str, *, forges: dict[str, str] | None = None, **state: Any) -> Any:
    """The ``pr_url`` the developer node records when its agent's output is ``output``, with the repositories
    registered on ``forges`` (GitHub for any other)."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    result = {
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False, "output": output, "messages": [], "mode": "anthropic_api"}
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        configurable = {
            "agent_runner": object(),
            "credentials": {},
            "repos_repo": _Repos(forges or {}),
            "embry0_config": SimpleNamespace(gitlab_url=GITLAB_URL),
        }
        cmd = await developer_node(_make_state(**state), {"configurable": configurable})
    return cmd.update["pr_url"]


def _final(pr_url: Any) -> str:
    return json.dumps({"pr_url": pr_url, "branch": "embry0/iss-abc12345-test-task"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "state", "recorded"),
    [
        (_final("https://github.com/o/r/pull/9"), {}, "https://github.com/o/r/pull/9"),
        (_final("https://github.com/O/R/pull/9"), {}, "https://github.com/O/R/pull/9"),
        (_final("https://github.com/x/y/pull/9"), {}, None),
        (
            _final("https://github.com/x/y/pull/9"),
            {"pr_url": "https://github.com/o/r/pull/3"},
            "https://github.com/o/r/pull/3",
        ),
        (_final("not an address"), {}, None),
        (_final({"url": "https://github.com/o/r/pull/9"}), {}, None),
        (
            "Opened https://github.com/x/y/pull/1, then https://github.com/o/r/pull/9.",
            {},
            "https://github.com/o/r/pull/9",
        ),
        ("Opened https://github.com/x/y/pull/1.", {}, None),
        (
            _final("https://gitlab.example.test/g/sub/p/-/merge_requests/4"),
            {"repo": "g/sub/p", "forges": {"g/sub/p": "gitlab"}},
            "https://gitlab.example.test/g/sub/p/-/merge_requests/4",
        ),
        (_final("https://github.com/o/r/pull/9"), {"forges": {"o/r": "gitlab"}}, None),
        ("Opened https://github.com/o/r/pull/9.", {"forges": {"o/r": "gitlab"}}, None),
        (_final("https://gitlab.example.test/o/r/-/merge_requests/9"), {}, None),
        (_final("https://gitlab.com/o/r/-/merge_requests/9"), {"forges": {"o/r": "gitlab"}}, None),
        (_final("https://ghe.example.test/o/r/pull/9"), {}, None),
        (
            _final("https://github.com/o/r/pull/9"),
            {"repo": "o/r", "context": {"type": "git", "repo": "o/other"}},
            None,
        ),
    ],
    ids=[
        "own-repository",
        "own-repository-other-case",
        "other-repository",
        "other-repository-keeps-the-known-link",
        "not-an-address",
        "not-a-string",
        "fallback-takes-the-own-repository-link",
        "fallback-other-repository-only",
        "gitlab-own-project",
        "gitlab-job-github-address",
        "gitlab-job-github-address-in-the-text",
        "github-job-gitlab-address",
        "gitlab-job-another-gitlab-host",
        "github-job-enterprise-host",
        "the-cloned-repository-decides",
    ],
)
async def test_the_developer_records_only_a_pull_request_of_the_job_s_repository(
    output: str, state: dict[str, Any], recorded: str | None
) -> None:
    assert await _recorded(output, **state) == recorded


@pytest.mark.asyncio
async def test_a_renamed_repository_s_pull_request_is_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.storage import repo_renames

    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"o/r": "o/renamed"})
    assert await _recorded(_final("https://github.com/o/renamed/pull/9")) == "https://github.com/o/renamed/pull/9"
