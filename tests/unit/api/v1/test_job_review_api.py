"""Reviewing a finished job under ``/jobs/{job_id}/…``, with the world's old paths kept as deprecated aliases.

Reviewing is core work, not part of the game: the routes moved out of
``/world/quests/{job_id}/…`` so the game layer can be turned off without
taking review with it, and their audit rows are ``job.review.*``.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient
from packaging.version import Version

from embry0.api.app import create_app
from embry0.config import Embry0Config
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
