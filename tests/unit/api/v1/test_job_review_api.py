"""Reviewing a finished job under ``/jobs/{job_id}/…``, with the world's old paths kept as deprecated aliases.

Reviewing is core work, not part of the game: the routes moved out of
``/world/quests/{job_id}/…`` so the game layer can be turned off without
taking review with it, and their audit rows are ``job.review.*``.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient
from packaging.version import Version

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.execution.forge_client import ForgeWrite
from tests.fixtures.routes import app_routes

REPO = Path(__file__).resolve().parents[4]
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime.now(UTC)

#: Each review route: its method, its path's last part, the request body, and the audit action it records.
ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("PUT", "evaluation", {"rating": "good", "note": "clean"}, "job.review.evaluated"),
    ("POST", "send-back", {"notes": "Handle the empty list too."}, "job.review.sent_back"),
    ("POST", "run-again", {}, "job.review.restarted"),
    ("POST", "dismiss", None, "job.review.dismissed"),
    ("POST", "reject", {"note": "not wanted"}, "job.review.rejected"),
]


class FakeReviewStore:
    """The job with its evaluation so far (``quest``) and the evaluation writes (``rate``)."""

    def __init__(self) -> None:
        self.ratings: list[dict[str, Any]] = []
        self.job_row: dict[str, Any] = {
            "job_id": "job-1",
            "repo": "acme/widgets",
            "task": "Fix the sender",
            "status": "completed",
            "delivery_state": "pr_open",
            "pr_url": "https://github.com/acme/widgets/pull/7",
            "rating": None,
            "verdict": None,
            "note": None,
            "followup_job_id": None,
            "rated_by": None,
            "rated_at": None,
        }

    async def quest(self, job_id: str) -> dict[str, Any] | None:
        return dict(self.job_row) if job_id == "job-1" else None

    async def rate(self, job_id: str, *, rated_by: str, **fields: Any) -> dict[str, Any]:
        self.ratings.append({"job_id": job_id, "rated_by": rated_by, **fields})
        return {"rated_by": rated_by, "rated_at": NOW, **{k: v for k, v in fields.items() if v is not None}}


class FakeJobs:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.created: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return dict(self.row) if self.row and job_id == self.row["job_id"] else None

    async def create(self, **fields: Any) -> str:
        self.created.append(fields)
        return "job-again"

    async def update(self, job_id: str, **fields: Any) -> None:
        self.updates.append((job_id, fields))


class Repos:
    """The repository registry: the forge each repository is registered on, matched in any case; GitHub, the
    default forge, for any other."""

    def __init__(self, **forges: str) -> None:
        self.forges = {repo.lower(): forge for repo, forge in forges.items()}

    async def forge_or_none(self, repo: str) -> str | None:
        return self.forges.get(repo.lower(), "github")


class AuditRows:
    """The database as ``emit_audit`` sees it: keeps the action of every audit row written."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    async def execute(self, sql: str, *args: Any) -> str:
        if "INSERT INTO audit_log" in sql:
            self.actions.append(args[0])
        return "INSERT 0 1"


class Handoffs:
    def __init__(self) -> None:
        self.closed: list[tuple[str, str]] = []

    async def close(self, *, decision_id: str, outcome: str, **_: Any) -> None:
        self.closed.append((decision_id, outcome))


def _failed_job(**overrides: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Fix the sender",
        "issue_number": None,
        "issue_id": "iss-1",
        "pipeline_template": "full-magnum-opus",
        "pipeline_config": {"agent_models_override": {"developer": "grok-4.7"}},
        "sandbox_profile": None,
        "context": {"type": "git", "repo": "acme/widgets"},
        "job_kind": "code",
        "area": "Billing",
        "launch": {"runner": "workflow", "issue_id": "iss-1"},
        **overrides,
    }


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = AuditRows()
    app.state.world_repo = FakeReviewStore()
    app.state.handoffs_repo = Handoffs()
    dispatch = MagicMock()
    dispatch.start_followup = AsyncMock(return_value={"dispatched": True, "job_id": "job-2"})
    app.state.comment_dispatch = dispatch
    app.state.jobs_repo = FakeJobs(_failed_job())
    app.state.issue_executor = MagicMock()
    app.state.repos_repo = Repos()
    return app


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _call(client: AsyncClient, method: str, path: str, body: dict[str, Any] | None) -> Any:
    return await client.request(method, path, json=body, headers=HEADERS)


@pytest.mark.parametrize(("method", "what", "body", "action"), ROUTES, ids=[r[1] for r in ROUTES])
@pytest.mark.parametrize("prefix", ["/api/v1/jobs", "/api/v1/world/quests"], ids=["jobs", "world-alias"])
async def test_every_review_route_records_its_job_review_action(
    app: Any, prefix: str, method: str, what: str, body: dict[str, Any] | None, action: str
) -> None:
    """The new path and its world alias both answer, and the row each writes is ``job.review.*``."""
    async with await _client(app) as client:
        resp = await _call(client, method, f"{prefix}/job-1/{what}", body)
    assert resp.status_code in (200, 202), f"{method} {prefix}/job-1/{what} answered {resp.status_code}: {resp.text}"
    assert resp.json()["job_id"] == "job-1" and resp.json()["by"], resp.json()
    assert app.state.db.actions == [action], f"{method} {prefix}/job-1/{what} recorded {app.state.db.actions}"
    assert app.state.handoffs_repo.closed and app.state.handoffs_repo.closed[0][0] == "done:job-1", (
        "the review settles the job's hand-off"
    )


async def test_each_answer_settles_the_job_s_hand_off_with_its_outcome(app: Any) -> None:
    async with await _client(app) as client:
        for method, what, body, _ in ROUTES:
            resp = await _call(client, method, f"/api/v1/jobs/job-1/{what}", body)
            assert resp.status_code in (200, 202), f"{what}: {resp.status_code} {resp.text}"
    assert [outcome for _, outcome in app.state.handoffs_repo.closed] == [
        "good",
        "send_back",
        "run_again",
        "dismiss",
        "reject",
    ]


def _review_routes(app: Any) -> dict[tuple[str, str], Any]:
    return {
        (method, route.path): route
        for route in app_routes(app)
        for method in route.methods or ()
        if route.path.startswith(("/api/v1/jobs/{job_id}/", "/api/v1/world/quests/{job_id}/"))
    }


def test_the_world_paths_are_deprecated_aliases_that_call_the_job_routes() -> None:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    routes = _review_routes(app)
    operations = app.openapi()["paths"]
    for method, what, _, _ in ROUTES:
        new, old = f"/api/v1/jobs/{{job_id}}/{what}", f"/api/v1/world/quests/{{job_id}}/{what}"
        assert (method, new) in routes, f"{method} {new} is not served"
        assert (method, old) in routes, f"{method} {old} is no longer served: the alias was dropped too early"
        assert routes[(method, old)].route.endpoint is routes[(method, new)].route.endpoint, (
            f"{method} {old} runs other code than {method} {new}"
        )
        alias = operations[old][method.lower()]
        assert alias.get("deprecated") is True, f"{method} {old} is not marked deprecated in the OpenAPI"
        assert f"{method} {new}" in alias["description"], f"{method} {old} does not name its successor"
        assert "removed in 0." in alias["description"], f"{method} {old} does not say when it goes away"
        assert not operations[new][method.lower()].get("deprecated"), f"{method} {new} is marked deprecated"


def _aliases(router: APIRouter) -> set[tuple[str, str]]:
    return {(method, route.path) for route in router.routes for method in getattr(route, "methods", None) or ()}


def test_only_the_routes_that_had_a_world_path_get_a_deprecated_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """A review route added later never had an old path, and a route outside ``/jobs/{job_id}/`` must not get a
    deprecated duplicate at its own path, which would mark the real route deprecated in the OpenAPI."""
    from embry0.api.v1 import job_review

    async def later(job_id: str) -> dict[str, Any]:
        return {}

    extra = APIRouter()
    extra.add_api_route("/jobs/{job_id}/later", later, methods=["POST"])
    extra.add_api_route("/elsewhere/{job_id}", later, methods=["POST"])
    monkeypatch.setattr(job_review.router, "routes", [*job_review.router.routes, *extra.routes])
    built = _aliases(job_review._deprecated_aliases())
    assert built == {(method, f"/world/quests/{{job_id}}/{what}") for method, what, _, _ in ROUTES}, (
        f"aliases built for routes that never had a world path: {sorted(built)}"
    )


def test_the_aliases_refuse_to_build_without_one_of_the_routes_they_stand_for(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.api.v1 import job_review

    kept = [route for route in job_review.router.routes if getattr(route, "path", "") != "/jobs/{job_id}/dismiss"]
    monkeypatch.setattr(job_review.router, "routes", kept)
    with pytest.raises(RuntimeError, match="dismiss"):
        job_review._deprecated_aliases()


def aliases_overdue(version: str) -> list[str]:
    """The deprecated alias paths still served at ``version``, when it is the release that removes them or later."""
    from embry0.api.v1 import job_review

    if Version(version) < Version(job_review.ALIASES_REMOVED_IN):
        return []
    return sorted(path for _method, path in _aliases(job_review.deprecated_router))


def test_the_world_aliases_are_gone_by_the_release_that_removes_them() -> None:
    from embry0.api.v1 import job_review

    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    overdue = aliases_overdue(version)
    assert overdue == [], (
        f"embry0 {version} is {job_review.ALIASES_REMOVED_IN} or later, and the deprecated world aliases are still "
        f"served: {overdue}. Remove job_review.deprecated_router, its include_router in app.py and its docs/api.md note"
    )


def test_the_removal_check_fires_at_the_removal_release() -> None:
    """The check above is quiet before the removal release only: it is not quiet by construction."""
    from embry0.api.v1 import job_review

    assert len(aliases_overdue(job_review.ALIASES_REMOVED_IN)) == len(ROUTES)
    assert aliases_overdue("0.1.9") == []


async def test_a_person_rates_a_job_once_it_has_finished(app: Any) -> None:
    async with await _client(app) as client:
        empty = await client.put("/api/v1/jobs/job-1/evaluation", json={}, headers=HEADERS)
        rated = await client.put(
            "/api/v1/jobs/job-1/evaluation", json={"rating": "good", "note": "clean"}, headers=HEADERS
        )
        app.state.world_repo.job_row["status"] = "running"
        early = await client.put("/api/v1/jobs/job-1/evaluation", json={"rating": "bad"}, headers=HEADERS)
        missing = await client.put("/api/v1/jobs/job-404/evaluation", json={"rating": "bad"}, headers=HEADERS)
    assert empty.status_code == 422, f"an empty evaluation answered {empty.status_code}"
    assert rated.status_code == 200 and rated.json()["rating"] == "good", rated.text
    assert early.status_code == 409, f"evaluating a running job answered {early.status_code}"
    assert missing.status_code == 404 and missing.json()["detail"] == "no job job-404", missing.text
    assert app.state.world_repo.ratings == [
        {"job_id": "job-1", "rated_by": rated.json()["by"], "rating": "good", "verdict": None, "note": "clean"}
    ]


async def test_sending_work_back_starts_a_follow_up_on_its_pull_request(app: Any) -> None:
    async with await _client(app) as client:
        sent = await client.post(
            "/api/v1/jobs/job-1/send-back", json={"notes": "Handle the empty list too."}, headers=HEADERS
        )
    assert sent.status_code == 202 and sent.json()["followup_job_id"] == "job-2", sent.text
    args, kwargs = app.state.comment_dispatch.start_followup.await_args
    assert args == ("acme/widgets", 7)
    assert "Handle the empty list too." in kwargs["request"] and kwargs["asks"] == "the notes"
    assert kwargs["kind"] == "send_back" and kwargs["pipeline_config"] is None
    assert app.state.world_repo.ratings[-1] == {
        "job_id": "job-1",
        "rated_by": sent.json()["by"],
        "verdict": "sent_back",
        "rating": "needs_work",
        "note": "Handle the empty list too.",
        "followup_job_id": "job-2",
    }


async def test_work_without_a_pull_request_cannot_be_sent_back_and_rejecting_records_it(app: Any) -> None:
    app.state.world_repo.job_row["pr_url"] = None
    async with await _client(app) as client:
        refused = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
        rejected = await client.post("/api/v1/jobs/job-1/reject", json={"note": "not wanted"}, headers=HEADERS)
    assert refused.status_code == 409, f"sending back a job with no pull request answered {refused.status_code}"
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is False, rejected.text
    assert app.state.world_repo.ratings[-1]["verdict"] == "rejected"


async def test_closing_the_pull_request_on_reject_needs_admin(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a pull request is as weighty as merging one, which already needs admin."""
    from embry0.api import deps

    async def not_admin(request: Any, authorization: str = "") -> None:
        raise HTTPException(status_code=403, detail="admin only")

    monkeypatch.setattr(deps, "_admin_dependency", not_admin)
    async with await _client(app) as client:
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS)
        keeping = await client.post("/api/v1/jobs/job-1/reject", json={"note": "no"}, headers=HEADERS)
    assert closing.status_code == 403, f"closing the pull request without admin answered {closing.status_code}"
    assert keeping.status_code == 200 and keeping.json()["closed_pr"] is False, keeping.text


#: Pull requests of the job's repository an action cannot act on, the forge the job's repository is registered on,
#: and the forge the refusal names (#181): on a host this deployment does not reach, or no pull request at all.
NOT_ACTED_ON = [
    ("https://gitlab.other.test/acme/widgets/-/merge_requests/7", "gitlab", "gitlab at gitlab.other.test"),
    ("https://ghe.example.test/acme/widgets/pull/7", "github", "github at ghe.example.test"),
    ("https://code.example.test/acme/widgets/changes/7", "github", "code.example.test"),
]


class Organizations:
    """The registered organizations, by owner, which they store lowercase; keeps every owner read."""

    def __init__(self, **forges: str) -> None:
        self.forges = forges
        self.statuses: dict[str, str] = {}
        self.reads: list[str] = []

    async def get(self, owner: str) -> Any:
        self.reads.append(owner)
        forge = self.forges.get(owner.lower())
        status = self.statuses.get(owner.lower(), "active")
        return SimpleNamespace(owner=owner, forge=forge, status=status) if forge else None


class Forge:
    """An organization's pull requests on its forge: keeps every write as (forge, what, repo, number[, body]), and
    answers a close with ``close_status``, a comment with ``comment_status``."""

    def __init__(self) -> None:
        self.close_status, self.comment_status = 200, 201
        self.writes: list[tuple[Any, ...]] = []
        self.made_for: list[str] = []

    def of(self, forge: str) -> Any:
        writes, outer = self.writes, self

        class _PullRequests:
            async def comment(self, repo: str, number: int, body: str) -> ForgeWrite:
                writes.append((forge, "comment", repo, number, body))
                return ForgeWrite(outer.comment_status)

            async def close(self, repo: str, number: int) -> ForgeWrite:
                writes.append((forge, "close", repo, number))
                return ForgeWrite(outer.close_status)

        return _PullRequests()


@pytest.fixture
def forge(app: Any, monkeypatch: pytest.MonkeyPatch) -> Forge:
    from embry0.api.v1 import organizations as organizations_api

    pulls = Forge()

    async def org_pull_requests(_request: Any, org: Any) -> Any:
        pulls.made_for.append(f"{org.owner}@{org.forge}")
        return pulls.of(org.forge)

    monkeypatch.setattr(organizations_api, "org_pull_requests", org_pull_requests)
    app.state.organizations_repo = Organizations(acme="github")
    return pulls


@pytest.mark.parametrize(
    ("pr_url", "registered_on", "forge_name"), NOT_ACTED_ON, ids=["gitlab", "github-enterprise", "other"]
)
async def test_a_pull_request_an_action_cannot_act_on_is_refused_naming_its_forge(
    app: Any, forge: Forge, pr_url: str, registered_on: str, forge_name: str
) -> None:
    """Sending back and closing work on github.com and the deployment's GitLab: on another host, or for an address
    that names no pull request, each answers 422 naming it, and does nothing else. Before, sending back said the job
    had no pull request, and closing answered closed_pr false with no error and recorded the rejection."""
    app.state.repos_repo = Repos(**{"acme/widgets": registered_on})
    app.state.world_repo.job_row["pr_url"] = pr_url
    async with await _client(app) as client:
        sent = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS)
    assert (sent.status_code, sent.json()["detail"]) == (
        422,
        f"sending work back is not supported for this forge ({forge_name})",
    ), sent.text
    assert (closing.status_code, closing.json()["detail"]) == (
        422,
        f"closing a pull request is not supported for this forge ({forge_name})",
    ), closing.text
    app.state.comment_dispatch.start_followup.assert_not_awaited()
    assert (app.state.world_repo.ratings, forge.writes) == ([], []), "a refused action recorded or wrote something"


async def test_a_merge_request_of_the_deployment_s_gitlab_is_sent_back_on_its_forge(app: Any) -> None:
    app.state.config.gitlab_url = "https://gitlab.example.test"
    app.state.repos_repo = Repos(**{"acme/platform/widgets": "gitlab"})
    app.state.world_repo.job_row.update(
        repo="acme/platform/widgets", pr_url="https://gitlab.example.test/acme/platform/widgets/-/merge_requests/7"
    )
    async with await _client(app) as client:
        sent = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
    assert sent.status_code == 202, sent.text
    args, kwargs = app.state.comment_dispatch.start_followup.await_args
    assert (args, kwargs["forge"]) == (("acme/platform/widgets", 7), "gitlab")


async def test_rejecting_closes_a_gitlab_merge_request_with_its_organization_s_credential(
    app: Any, forge: Forge
) -> None:
    app.state.config.gitlab_url = "https://gitlab.example.test"
    app.state.repos_repo = Repos(**{"acme/platform/widgets": "gitlab"})
    app.state.organizations_repo = Organizations(acme="gitlab")
    app.state.world_repo.job_row.update(
        repo="acme/platform/widgets", pr_url="https://gitlab.example.test/acme/platform/widgets/-/merge_requests/7"
    )
    async with await _client(app) as client:
        rejected = await client.post(
            "/api/v1/jobs/job-1/reject", json={"note": "not wanted", "close_pr": True}, headers=HEADERS
        )
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is True, rejected.text
    assert forge.made_for == ["acme@gitlab"]
    assert forge.writes == [
        ("gitlab", "comment", "acme/platform/widgets", 7, "not wanted"),
        ("gitlab", "close", "acme/platform/widgets", 7),
    ]


async def test_a_close_the_forge_refuses_is_a_502_and_records_nothing(app: Any, forge: Forge) -> None:
    forge.close_status = 403
    async with await _client(app) as client:
        rejected = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
    assert rejected.status_code == 502 and "403" in rejected.json()["detail"], rejected.text
    assert app.state.world_repo.ratings == []


async def test_a_refused_comment_still_closes(app: Any, forge: Forge) -> None:
    forge.comment_status = 403
    async with await _client(app) as client:
        rejected = await client.post(
            "/api/v1/jobs/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS
        )
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is True, rejected.text
    assert [w[1] for w in forge.writes] == ["comment", "close"]


async def test_rejecting_closes_a_github_pull_request_with_its_organization_s_client(app: Any, forge: Forge) -> None:
    async with await _client(app) as client:
        rejected = await client.post(
            "/api/v1/jobs/job-1/reject", json={"note": "not wanted", "close_pr": True}, headers=HEADERS
        )
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is True, rejected.text
    assert forge.writes == [
        ("github", "comment", "acme/widgets", 7, "not wanted"),
        ("github", "close", "acme/widgets", 7),
    ]
    assert app.state.organizations_repo.reads == ["acme"]


async def test_closing_needs_the_owner_registered_on_the_pull_request_s_forge(app: Any, forge: Forge) -> None:
    """An owner registered on GitLab holds no credential that reaches a GitHub pull request of the same name."""
    app.state.organizations_repo = Organizations(acme="gitlab")
    async with await _client(app) as client:
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
        app.state.organizations_repo = Organizations()
        unregistered = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
    assert (closing.status_code, closing.json()["detail"]) == (
        409,
        "acme is registered on gitlab, but the job's pull request is on github",
    ), closing.text
    assert unregistered.status_code == 409, unregistered.text
    assert (app.state.world_repo.ratings, forge.writes) == ([], [])


@pytest.mark.parametrize(("status", "sent"), [("removed", False), ("paused", True)])
async def test_a_removed_organization_s_pull_request_is_sent_back_nowhere(
    app: Any, forge: Forge, status: str, sent: bool
) -> None:
    """Sending work back reads the pull request again with its organization's credential and starts a follow-up: for
    a removed organization neither, and the answer says why (#721); a paused one's is sent back."""
    app.state.organizations_repo.statuses["acme"] = status
    async with await _client(app) as client:
        answer = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
    if sent:
        assert answer.status_code == 202, answer.text
        return
    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "the organization acme was removed: no job starts on its repositories until it is made active again",
    ), answer.text
    app.state.comment_dispatch.start_followup.assert_not_awaited()


@pytest.mark.parametrize(("status", "closes"), [("removed", False), ("paused", True), ("active", True)])
async def test_a_removed_organization_s_credential_closes_no_pull_request(
    app: Any, forge: Forge, status: str, closes: bool
) -> None:
    """Closing a job's pull request acts with its owner's organization's credential: a removed organization's is not
    used, and the answer says why; a paused one still closes (#721)."""
    app.state.organizations_repo.statuses["acme"] = status
    async with await _client(app) as client:
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS)
    if closes:
        assert closing.status_code == 200, closing.text
        assert forge.writes, "the pull request was closed"
        return
    assert (closing.status_code, closing.json()["detail"]) == (
        409,
        "the organization acme was removed: embry0 does not act with its credential",
    ), closing.text
    assert (app.state.world_repo.ratings, forge.writes) == ([], [])


async def test_an_action_on_a_job_s_pull_request_acts_only_on_the_job_s_own_repository(app: Any, forge: Forge) -> None:
    """A job's pull request address names another repository than the job's: sending back and closing refuse it
    (409) and act on nothing."""
    app.state.world_repo.job_row["pr_url"] = "https://github.com/globex/gizmos/pull/5"
    app.state.organizations_repo = Organizations(acme="github", globex="github")
    async with await _client(app) as client:
        sent = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS)
    for answer in (sent, closing):
        assert (answer.status_code, answer.json()["detail"]) == (
            409,
            "the job's pull request is not in the job's repository",
        ), answer.text
    app.state.comment_dispatch.start_followup.assert_not_awaited()
    assert (app.state.world_repo.ratings, forge.writes, app.state.organizations_repo.reads) == ([], [], [])


@pytest.mark.parametrize(
    ("pr_url", "renames"),
    [
        ("https://github.com/ACME/Widgets/pull/7", {}),
        ("https://github.com/acme/gadgets/pull/7", {"acme/widgets": "acme/gadgets"}),
    ],
    ids=["other-case", "renamed"],
)
async def test_the_job_s_repository_is_compared_ignoring_case_and_after_renames(
    app: Any, forge: Forge, monkeypatch: pytest.MonkeyPatch, pr_url: str, renames: dict[str, str]
) -> None:
    from embry0.storage import repo_renames

    monkeypatch.setattr(repo_renames, "REPO_RENAMES", renames)
    app.state.world_repo.job_row["pr_url"] = pr_url
    async with await _client(app) as client:
        rejected = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is True, rejected.text
    assert [w[1] for w in forge.writes] == ["close"]


async def test_closing_checks_the_key_reaches_the_pull_request_s_repository(
    app: Any, forge: Forge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing acts on the forge, so it checks the key's reach as sending back does."""
    from embry0.api.v1 import job_review

    reached: list[tuple[str, list[str | None], str]] = []

    async def enforce_reach(_request: Any, shown: str, repos: Any, *, action: str, reason: str) -> None:
        reached.append((shown, list(repos), action))

    monkeypatch.setattr(job_review, "enforce_reach", enforce_reach)
    async with await _client(app) as client:
        rejected = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
        kept = await client.post("/api/v1/jobs/job-1/reject", json={}, headers=HEADERS)
    assert rejected.status_code == 200 and kept.status_code == 200, (rejected.text, kept.text)
    assert reached == [("acme/widgets", ["acme/widgets"], "close a job's pull request")]


@pytest.mark.parametrize(
    ("registered_on", "pr_url"),
    [
        ("gitlab", "https://github.com/acme/widgets/pull/7"),
        ("github", "https://gitlab.com/acme/widgets/-/merge_requests/7"),
    ],
    ids=["gitlab-job-github-address", "github-job-gitlab-address"],
)
async def test_a_pull_request_on_another_forge_is_not_the_job_s_repository(
    app: Any, forge: Forge, registered_on: str, pr_url: str
) -> None:
    """The same path on another forge is another repository: refused (409) both ways, and nothing is acted on."""
    app.state.repos_repo = Repos(**{"acme/widgets": registered_on})
    app.state.organizations_repo = Organizations(acme=registered_on)
    app.state.world_repo.job_row["pr_url"] = pr_url
    async with await _client(app) as client:
        sent = await client.post("/api/v1/jobs/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
        closing = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
    for answer in (sent, closing):
        assert (answer.status_code, answer.json()["detail"]) == (
            409,
            "the job's pull request is not in the job's repository",
        ), answer.text
    app.state.comment_dispatch.start_followup.assert_not_awaited()
    assert (app.state.world_repo.ratings, forge.writes, app.state.organizations_repo.reads) == ([], [], [])


async def test_a_merged_pull_request_is_not_closed_on_any_forge(app: Any, forge: Forge) -> None:
    app.state.world_repo.job_row.update(
        delivery_state="merged", pr_url="https://gitlab.com/acme/widgets/-/merge_requests/7"
    )
    async with await _client(app) as client:
        rejected = await client.post("/api/v1/jobs/job-1/reject", json={"close_pr": True}, headers=HEADERS)
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is False, rejected.text
    assert (forge.writes, app.state.organizations_repo.reads) == ([], [])


async def test_a_finished_job_runs_again_as_a_new_job_from_its_launch_record(app: Any) -> None:
    app.state.world_repo.job_row.update(status="failed", pr_url=None, delivery_state=None)
    async with await _client(app) as client:
        again = await client.post("/api/v1/jobs/job-1/run-again", json={}, headers=HEADERS)
    assert again.status_code == 202 and again.json()["followup_job_id"] == "job-again", again.text
    created = app.state.jobs_repo.created[0]
    assert created["repo"] == "acme/widgets" and created["task"] == "Fix the sender"
    assert created["issue_id"] == "iss-1" and created["pipeline_template"] == "full-magnum-opus"
    assert created["pipeline_config"] == {"agent_models_override": {"developer": "grok-4.7"}}
    assert app.state.jobs_repo.updates == [("job-again", {"job_kind": "code", "area": "Billing"})]
    args, kwargs = app.state.issue_executor.run_again.call_args
    assert args == ("job-again", {"runner": "workflow", "issue_id": "iss-1"}) and kwargs["issue_id"] == "iss-1"
    assert app.state.world_repo.ratings[-1]["followup_job_id"] == "job-again"
    assert "verdict" not in app.state.world_repo.ratings[-1], "running it again is no verdict on the work"


async def test_a_job_not_finished_or_without_a_launch_record_is_not_run_again(app: Any) -> None:
    app.state.world_repo.job_row["status"] = "running"
    async with await _client(app) as client:
        out = await client.post("/api/v1/jobs/job-1/run-again", json={}, headers=HEADERS)
        app.state.world_repo.job_row["status"] = "failed"
        app.state.jobs_repo = FakeJobs(_failed_job(launch=None))
        old = await client.post("/api/v1/jobs/job-1/run-again", json={}, headers=HEADERS)
    assert out.status_code == 409 and old.status_code == 409, (out.status_code, old.status_code)
    assert "launch record" in old.json()["detail"]
    app.state.issue_executor.run_again.assert_not_called()


async def test_dismissing_a_job_takes_it_out_of_the_inbox_with_no_rating(app: Any) -> None:
    app.state.world_repo.job_row["status"] = "failed"
    async with await _client(app) as client:
        dismissed = await client.post("/api/v1/jobs/job-1/dismiss", headers=HEADERS)
        app.state.world_repo.job_row["status"] = "running"
        early = await client.post("/api/v1/jobs/job-1/dismiss", headers=HEADERS)
    assert dismissed.status_code == 200 and early.status_code == 409, (dismissed.status_code, early.status_code)
    assert app.state.world_repo.ratings == [{"job_id": "job-1", "rated_by": dismissed.json()["by"]}]


async def test_the_evaluation_reads_as_it_stands(app: Any) -> None:
    """``GET /jobs/{job_id}/evaluation``: nulls and an empty note before anyone reviewed the job, then the review."""
    async with await _client(app) as client:
        before = await client.get("/api/v1/jobs/job-1/evaluation")
        app.state.world_repo.job_row.update(
            rating="needs_work", verdict="sent_back", note="Name the edge case.", followup_job_id="job-2"
        )
        app.state.world_repo.job_row.update(rated_by="ada@example.com", rated_at=NOW)
        after = await client.get("/api/v1/jobs/job-1/evaluation")
        missing = await client.get("/api/v1/jobs/job-404/evaluation")
    assert before.status_code == 200, f"GET /jobs/job-1/evaluation answered {before.status_code}: {before.text}"
    assert before.json() == {
        "job_id": "job-1",
        "rating": None,
        "verdict": None,
        "note": "",
        "followup_job_id": None,
        "by": None,
        "at": None,
    }, f"a job nobody reviewed reads as nulls, with an empty note: {before.json()}"
    assert after.json() == {
        "job_id": "job-1",
        "rating": "needs_work",
        "verdict": "sent_back",
        "note": "Name the edge case.",
        "followup_job_id": "job-2",
        "by": "ada@example.com",
        "at": NOW.isoformat(),
    }, f"the evaluation reads as the review routes answer it: {after.json()}"
    assert missing.status_code == 404 and missing.json()["detail"] == "no job job-404", missing.text
    assert app.state.db.actions == [] and app.state.world_repo.ratings == [], "reading records nothing"
    assert app.state.handoffs_repo.closed == [], "reading settles no hand-off"


async def test_the_evaluation_reads_while_the_job_runs(app: Any) -> None:
    """Only answering waits for the job to finish; what was said about it so far reads at any time."""
    app.state.world_repo.job_row["status"] = "running"
    async with await _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/evaluation")
    assert resp.status_code == 200 and resp.json()["by"] is None, f"{resp.status_code} {resp.text}"


def test_reading_the_evaluation_has_no_world_alias() -> None:
    """It never had a path under the world, so it gets no deprecated one."""
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    routes = _review_routes(app)
    assert ("GET", "/api/v1/jobs/{job_id}/evaluation") in routes, "GET /jobs/{job_id}/evaluation is not served"
    assert ("GET", "/api/v1/world/quests/{job_id}/evaluation") not in routes, (
        "a world alias was built for the evaluation's read, which never had a world path"
    )
