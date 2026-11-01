"""The decisions API (spec 2026-10-27): quests on offer and quests to review, listed and read in one shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.repo_allowlist import key_covers
from embry0.api.v1 import decisions as decisions_api
from embry0.api.v1 import proposals as proposals_api
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.proposals import Proposal
from embry0.settings.scope import DEPLOYMENT
from embry0.settings.sections import CATALOG
from embry0.settings.sections.ui import UI
from embry0.settings.store import build_snapshot
from tests.fixtures.leads import MemorySettings

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime.now(UTC)


def proposal(pid: str, kind: str, *, severity: str | None = None, held_ticket: bool = False) -> Proposal:
    extra: dict[str, Any] = {"severity": severity, "paths": ["src/app.py"]}
    if held_ticket:
        extra["source"] = {"readiness": 10}
    return Proposal(
        id=pid,
        kind=kind,
        repo="acme/web",
        title=f"{kind} title",
        summary=f"The {kind} summary. More words.",
        details="details",
        action="Start it",
        created_at=NOW - timedelta(hours=1),
        owners=("octocat",),
        extra=extra,
    )


class FakeWorld:
    def __init__(self) -> None:
        self.row = {
            "job_id": "job-1",
            "repo": "acme/web",
            "area": None,
            "task": "Fix the crash loop.\nMore.",
            "status": "completed",
            "delivery_state": None,
            "pr_url": "https://github.com/acme/web/pull/7",
            "pipeline_template": "full-magnum-opus",
            "created_at": NOW - timedelta(hours=2),
            "finished_at": NOW - timedelta(minutes=30),
            "error_code": None,
            "error_message": None,
            "total_cost_usd": 3.5,
            "dispatched_by": "someone",
            "agent_type": "developer",
        }

    async def inbox(
        self, *, days: int, player_actors: list[str], limit: int = 30, repos: Any = None
    ) -> list[dict[str, Any]]:
        return [dict(self.row)]

    async def quest(self, job_id: str) -> dict[str, Any] | None:
        return dict(self.row) if job_id == "job-1" else None

    async def merge_approval(self, job_id: str) -> dict[str, Any] | None:
        return {
            "id": "ma-1",
            "status": "pending",
            "ask": '{"checks_state": "success", "recommendation": {"verdict": "merge"}}',
        }


class FakeJobs:
    async def get(self, job_id: str) -> dict[str, Any] | None:
        return {
            "result_summary": "The report.",
            "done_when": None,
            "work_nature": None,
            "started_at": NOW - timedelta(hours=1),
        }


class FakeBriefs:
    async def current(self, job_ids: Any) -> dict[str, dict[str, Any]]:
        return {"job-1": {"title": "Fix the leads crash loop", "why": "Users got 502s."}}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.world_repo = FakeWorld()
    app.state.jobs_repo = FakeJobs()
    app.state.job_briefs_repo = FakeBriefs()
    app.state.proposal_briefs_repo = None
    app.state.settings = MemorySettings()
    offers = [
        proposal("issue:1", "issue"),
        proposal("crack:abc", "crack", severity="high"),
        proposal("ticket:t1", "ticket", held_ticket=True),
    ]

    async def feed(_request: Any) -> list[Proposal]:
        return offers

    async def pr_stats(_request: Any, pr_url: str | None) -> dict[str, Any] | None:
        return {"number": 7, "state": "open", "files": 3, "additions": 40, "deletions": 2} if pr_url else None

    monkeypatch.setattr(proposals_api, "feed", feed)
    monkeypatch.setattr(world_api, "_pr_stats", pr_stats)
    return app


async def get(app: Any, url: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=HEADERS)


@pytest.mark.asyncio
async def test_every_decision_is_listed_worst_first_in_one_shape(app: Any) -> None:
    resp = await get(app, "/api/v1/decisions")
    assert resp.status_code == 200, resp.text
    decisions = resp.json()["decisions"]
    assert [d["id"] for d in decisions] == ["offer:crack:abc", "done:job-1", "offer:issue:1", "offer:ticket:t1"]
    review = decisions[1]
    assert review["verdict"] == {"label": "Ready to merge", "tone": "good"}
    assert review["title"] == "Fix the leads crash loop" and review["why"] == "Users got 502s."
    assert review["facts"][0] == {"label": "Pull request", "value": "#7 · +40 −2 · 3 files", "tone": ""}
    assert review["links"]["merge_approval"] == "ma-1", "a pending merge approval is what Merge goes through"
    assert decisions[3]["held"] is True, "a ticket below the readiness floor is shown, marked held"


@pytest.mark.asyncio
async def test_a_stage_or_held_filter_narrows_the_list(app: Any) -> None:
    offered = (await get(app, "/api/v1/decisions?stage=offered&include_held=false")).json()["decisions"]
    assert [d["id"] for d in offered] == ["offer:crack:abc", "offer:issue:1"]
    done = (await get(app, "/api/v1/decisions?stage=done")).json()["decisions"]
    assert [d["id"] for d in done] == ["done:job-1"]


@pytest.mark.asyncio
async def test_one_decision_is_read_by_its_id(app: Any) -> None:
    review = (await get(app, "/api/v1/decisions/done:job-1")).json()
    assert review["id"] == "done:job-1" and review["actions"][0] == "merge"
    assert (await get(app, "/api/v1/decisions/done:job-9")).status_code == 404
    assert (await get(app, "/api/v1/decisions/bogus")).status_code == 404


@pytest.mark.asyncio
async def test_a_key_limited_to_some_repos_lists_only_theirs_and_refuses_nothing(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    audited: list[Any] = []

    async def audit(*args: Any, **kwargs: Any) -> None:
        audited.append(args)

    monkeypatch.setattr("embry0.api.repo_allowlist.emit_audit", audit)
    request = SimpleNamespace(
        state=SimpleNamespace(principal=SimpleNamespace(allowed_repos=("acme/web",), key_name="chat", key_id="k-1")),
        app=app,
    )
    with structlog.testing.capture_logs() as logs:
        assert key_covers(request, "acme/web") is True
        assert key_covers(request, "acme/other") is False
    assert not logs and not audited, "a list read every minute hides another repo's decisions without a refusal"


class GameOff:
    """A settings store whose document turns the game layer off (``ui.game: false``, #177).

    A real snapshot of that document, so every section a decision reads (``leads`` too) reads as it would.
    """

    async def snapshot(self) -> Any:
        rows = [{"scope": DEPLOYMENT, "data": {UI.name: {"game": False}}, "generation": 1, "updated_at": None}]
        return build_snapshot(CATALOG, rows, token=(), log=False)


@pytest.mark.asyncio
async def test_a_finished_job_is_a_quest_while_the_game_is_on(app: Any) -> None:
    listed = (await get(app, "/api/v1/decisions?stage=done")).json()["decisions"]
    assert [d["kind_label"] for d in listed] == ["Quest done"], "the game's word stays while the game is on"
    assert (await get(app, "/api/v1/decisions/done:job-1")).json()["kind_label"] == "Quest done"


@pytest.mark.asyncio
async def test_a_finished_job_is_a_job_while_the_game_is_off(app: Any) -> None:
    app.state.settings = GameOff()
    listed = (await get(app, "/api/v1/decisions")).json()["decisions"]
    labels = [d["kind_label"] for d in listed if d["stage"] == "done"]
    assert labels == ["Job done"], f"no game word on a decision card while the game is off: {labels}"
    one = (await get(app, "/api/v1/decisions/done:job-1")).json()
    assert one["kind_label"] == "Job done", f"nor on the one read by its id: {one['kind_label']}"


@pytest.mark.parametrize("stage", ["offered", "done", "a-stage-added-later", None])
def test_a_decision_of_any_stage_is_listed_only_to_a_key_that_covers_its_repository(stage: str | None) -> None:
    """``listed_to`` grants by the repository alone: a stage it does not know is hidden from a key that does not
    cover the repository, not shown by default."""
    limited = SimpleNamespace(state=SimpleNamespace(principal=SimpleNamespace(allowed_repos=("acme/web",))))
    session = SimpleNamespace(state=SimpleNamespace())
    other = {"stage": stage, "repo": "globex/gizmos"}
    assert decisions_api.listed_to(limited, other) is False, f"stage {stage!r} of an uncovered repository is hidden"
    assert decisions_api.listed_to(limited, {**other, "repo": "acme/web"}) is True, "its own repository is listed"
    assert decisions_api.listed_to(session, other) is True, "a session reads every repository's decisions"
