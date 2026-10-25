"""The world API (spec 2026-10-25): the guild hall, a character's quest log, a quest's scroll and the player's evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.progression import Character, Progression
from embry0.services.world import WorldCache

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime.now(UTC)


class FakeRepo:
    def __init__(self) -> None:
        self.ratings: list[dict[str, Any]] = []
        self.quest_row: dict[str, Any] | None = {
            "job_id": "job-1",
            "repo": "acme/web",
            "area": "Billing",
            "task": "[watcher] Fix the sender\n\ndetails",
            "status": "completed",
            "delivery_state": "pr_open",
            "pr_url": "https://github.com/acme/web/pull/7",
            "pipeline_template": "full-magnum-opus",
            "created_at": NOW - timedelta(hours=1),
            "started_at": NOW - timedelta(hours=1),
            "finished_at": NOW,
            "error_code": None,
            "total_cost_usd": 2.5,
            "result_summary": "Fixed the sender.",
            "rating": None,
            "verdict": None,
            "note": None,
            "followup_job_id": None,
            "rated_by": None,
            "rated_at": None,
        }

    async def jobs(self, *, returning_seconds: float, limit: int = 60) -> list[dict[str, Any]]:
        return [
            {
                "job_id": "job-live",
                "repo": "acme/web",
                "task": "Fix it",
                "status": "running",
                "created_at": NOW,
                "started_at": NOW,
            },
        ]

    async def agent_runs(self, ids: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {
            "job-live": [("agent_started", "triage"), ("agent_completed", "triage"), ("agent_started", "developer")]
        }

    async def lead_links(self, ids: list[str]) -> dict[str, str]:
        return {i: "case:3" for i in ids if i == "job-live"}

    async def last_event_at(self, ids: list[str]) -> dict[str, Any]:
        return {i: NOW for i in ids}

    async def stats(self, agent: str | None = None) -> dict[str, dict[str, Any]]:
        return {"developer": {"quests": 2, "succeeded": 1, "last_quest_at": NOW}}

    async def inbox(self, *, days: int, player_actors: list[str], limit: int = 30) -> list[dict[str, Any]]:
        return [{**self.quest_row, "agent_type": "developer"}]  # type: ignore[dict-item]

    async def loot(self, *, days: int, limit: int = 30) -> list[dict[str, Any]]:
        return [{**self.quest_row, "delivery_state_at": NOW}]  # type: ignore[dict-item]

    async def cleaned(self, *, days: int) -> dict[tuple[str, str | None], int]:
        return {("acme/web", "Billing"): 1}

    async def quest_log(self, agent: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{**self.quest_row, "cost_usd": 1.2, "duration_ms": 90_000}]  # type: ignore[dict-item]

    async def quest(self, job_id: str) -> dict[str, Any] | None:
        return dict(self.quest_row) if self.quest_row and job_id == "job-1" else None

    async def quest_runs(self, job_id: str) -> list[dict[str, Any]]:
        return [
            {
                "agent_type": "developer",
                "cost_usd": 2.0,
                "duration_ms": 60_000,
                "result": "success",
                "error_code": None,
                "created_at": NOW,
                "tools_called": {"Edit": 2},
                "skill_actions": None,
            },
            {
                "agent_type": "review",
                "cost_usd": 0.5,
                "duration_ms": 30_000,
                "result": "success",
                "error_code": None,
                "created_at": NOW,
                "tools_called": {"Read": 4},
                "skill_actions": None,
                # Provider fallback: this run stood in for a paused account's model.
                "model": "grok-4.7",
                "fallback": {
                    "instead_of": "claude-sonnet-5",
                    "provider": "anthropic",
                    "reason": "quota_exhausted",
                    "until": "2026-10-26T00:00:00+00:00",
                },
            },
        ]

    async def merge_approval(self, job_id: str) -> dict[str, Any] | None:
        return {"id": "ma-1", "status": "pending", "pr_url": "https://github.com/acme/web/pull/7"}

    async def rate(self, job_id: str, *, rated_by: str, **fields: Any) -> dict[str, Any]:
        self.ratings.append({"job_id": job_id, "rated_by": rated_by, **fields})
        return {"rated_by": rated_by, "rated_at": NOW, **{k: v for k, v in fields.items() if v is not None}}


class FakeProgression:
    async def read(self) -> Progression:
        return Progression(characters={a: Character(a) for a in ("developer", "review", "why")})


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.world_repo = FakeRepo()
    app.state.world_cache = WorldCache()
    app.state.progression = FakeProgression()

    async def leads(request: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "case:3",
                "kind": "case",
                "repo": "acme/web",
                "severity": "high",
                "occurrences": 9,
                "title": "Null sender",
            }
        ]

    async def regions(request: Any) -> dict[str, Any]:
        return {
            "regions": [{"repo": "acme/web", "jobs": 3, "areas": [{"name": "Billing", "slug": "billing"}]}],
            "frontier": ["acme/old"],
        }

    async def no_pr_stats(request: Any, pr_url: str | None) -> None:
        return None

    monkeypatch.setattr(world_api, "_leads", leads)
    monkeypatch.setattr(world_api, "_regions", regions)
    monkeypatch.setattr(world_api, "_pr_stats", no_pr_stats)
    return app


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_guild_hall_shows_every_character_the_board_and_the_inbox(app: Any) -> None:
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
    characters = {c["agent_type"]: c for c in world["characters"]}
    assert characters["developer"]["state"] == "working" and characters["developer"]["job_id"] == "job-live"
    assert characters["why"]["state"] == "idle"
    assert world["parties"][0]["lead_id"] == "case:3" and world["parties"][0]["party"] == ["triage", "developer"]
    assert world["quests"][0]["verb"] == "Attack" and world["quests"][0]["level"] > 1
    assert world["inbox"][0] == {
        "job_id": "job-1",
        "repo": "acme/web",
        "area": "Billing",
        "title": "Fix the sender",
        "outcome": "pr_open",
        "leader": "developer",
        "pr_url": "https://github.com/acme/web/pull/7",
        "finished_at": world["inbox"][0]["finished_at"],
        "cost_usd": 2.5,
        "failure": None,
        "error_code": None,
    }
    assert world["regions"][0]["areas"][0]["name"] == "Billing" and world["frontier"] == ["acme/old"]
    assert world["loot"][0]["merged"] is False


async def test_a_character_has_its_quest_log_and_an_unknown_one_is_404(app: Any) -> None:
    async with await _client(app) as client:
        dev = (await client.get("/api/v1/world/characters/developer")).json()
        missing = await client.get("/api/v1/world/characters/nobody")
    assert dev["stats"]["quests"] == 2 and dev["stats"]["success_rate"] == 0.5
    assert dev["quest_log"][0]["title"] == "Fix the sender" and dev["quest_log"][0]["seconds"] == 90.0
    assert missing.status_code == 404


async def test_a_quest_scroll_says_what_it_did_and_what_it_earned(app: Any) -> None:
    async with await _client(app) as client:
        scroll = (await client.get("/api/v1/world/quests/job-1")).json()
        missing = await client.get("/api/v1/world/quests/job-404")
    assert scroll["title"] == "Fix the sender" and scroll["report"] == "Fixed the sender."
    assert scroll["merge_approval"] == {
        "id": "ma-1",
        "status": "pending",
        "pr_url": "https://github.com/acme/web/pull/7",
    }
    assert [p["agent_type"] for p in scroll["party"]] == ["developer", "review"]
    assert scroll["xp"] and scroll["cost_usd"] == 2.5 and scroll["seconds"] == pytest.approx(3600, abs=5)
    assert scroll["evaluation"] is None and missing.status_code == 404
    assert scroll["fallbacks"] == [
        {
            "agent_type": "review",
            "model": "grok-4.7",
            "instead_of": "claude-sonnet-5",
            "provider": "anthropic",
            "reason": "quota_exhausted",
            "until": "2026-10-26T00:00:00+00:00",
            "runs": 1,
        }
    ], "the scroll names the run that ran on a stand-in, and why"
    assert scroll["account_wait"] is None, "a finished quest waits for nothing"


async def test_the_player_rates_a_quest_once_it_is_back(app: Any) -> None:
    async with await _client(app) as client:
        empty = await client.put("/api/v1/world/quests/job-1/evaluation", json={}, headers=HEADERS)
        rated = await client.put(
            "/api/v1/world/quests/job-1/evaluation", json={"rating": "good", "note": "clean"}, headers=HEADERS
        )
        app.state.world_repo.quest_row["status"] = "running"
        early = await client.put("/api/v1/world/quests/job-1/evaluation", json={"rating": "bad"}, headers=HEADERS)
    assert empty.status_code == 422
    assert rated.status_code == 200 and rated.json()["rating"] == "good"
    assert early.status_code == 409
    assert app.state.world_repo.ratings[0]["rating"] == "good" and app.state.world_repo.ratings[0]["note"] == "clean"


async def test_sending_work_back_starts_a_follow_up_on_its_pull_request(app: Any) -> None:
    dispatch = AsyncMock()
    dispatch.start_followup = AsyncMock(return_value={"dispatched": True, "job_id": "job-2", "branch": "embry0/x"})
    app.state.comment_dispatch = dispatch
    async with await _client(app) as client:
        sent = await client.post(
            "/api/v1/world/quests/job-1/send-back", json={"notes": "Handle the empty list too."}, headers=HEADERS
        )
    assert sent.status_code == 202 and sent.json()["followup_job_id"] == "job-2"
    args, kwargs = dispatch.start_followup.await_args
    assert args == ("acme/web", 7)
    assert "Handle the empty list too." in kwargs["request"] and kwargs["asks"] == "the notes"
    assert kwargs["kind"] == "send_back" and kwargs["pipeline_config"] is None
    assert app.state.world_repo.ratings[-1] == {
        "job_id": "job-1",
        "rated_by": app.state.world_repo.ratings[-1]["rated_by"],
        "verdict": "sent_back",
        "rating": "needs_work",
        "note": "Handle the empty list too.",
        "followup_job_id": "job-2",
    }


async def test_work_without_a_pull_request_cannot_be_sent_back_and_rejecting_records_it(app: Any) -> None:
    app.state.world_repo.quest_row["pr_url"] = None
    async with await _client(app) as client:
        refused = await client.post("/api/v1/world/quests/job-1/send-back", json={"notes": "x"}, headers=HEADERS)
        rejected = await client.post("/api/v1/world/quests/job-1/reject", json={"note": "not wanted"}, headers=HEADERS)
    assert refused.status_code == 409
    assert rejected.status_code == 200 and rejected.json()["closed_pr"] is False
    assert app.state.world_repo.ratings[-1]["verdict"] == "rejected"


async def test_closing_the_pull_request_on_reject_needs_admin(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a pull request is as weighty as merging one, which already needs admin."""
    from fastapi import HTTPException

    from embry0.api.v1 import world as world_api

    async def not_admin(request: Any) -> None:
        raise HTTPException(status_code=403, detail="admin only")

    monkeypatch.setattr(world_api, "ensure_admin", not_admin)
    async with await _client(app) as client:
        closing = await client.post(
            "/api/v1/world/quests/job-1/reject", json={"note": "no", "close_pr": True}, headers=HEADERS
        )
        keeping = await client.post("/api/v1/world/quests/job-1/reject", json={"note": "no"}, headers=HEADERS)
    assert closing.status_code == 403
    assert keeping.status_code == 200 and keeping.json()["closed_pr"] is False


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


def _failed_job(**overrides: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/web",
        "task": "[watcher] Fix the sender",
        "issue_number": None,
        "issue_id": "iss-1",
        "pipeline_template": "full-magnum-opus",
        "pipeline_config": {"agent_models_override": {"developer": "grok-4.7"}},
        "sandbox_profile": None,
        "context": {"type": "git", "repo": "acme/web"},
        "job_kind": "code",
        "area": "Billing",
        "launch": {"runner": "workflow", "issue_id": "iss-1"},
        **overrides,
    }


async def test_a_finished_quest_runs_again_as_a_new_job_from_its_launch_record(app: Any) -> None:
    app.state.world_repo.quest_row.update(status="failed", pr_url=None, delivery_state=None)
    app.state.jobs_repo = FakeJobs(_failed_job())
    executor = MagicMock()
    app.state.issue_executor = executor
    async with await _client(app) as client:
        again = await client.post("/api/v1/world/quests/job-1/run-again", json={}, headers=HEADERS)
    assert again.status_code == 202 and again.json()["followup_job_id"] == "job-again"
    created = app.state.jobs_repo.created[0]
    assert created["repo"] == "acme/web" and created["task"] == "[watcher] Fix the sender"
    assert created["issue_id"] == "iss-1" and created["pipeline_template"] == "full-magnum-opus"
    assert created["pipeline_config"] == {"agent_models_override": {"developer": "grok-4.7"}}
    assert app.state.jobs_repo.updates == [("job-again", {"job_kind": "code", "area": "Billing"})]
    args, kwargs = executor.run_again.call_args
    assert args == ("job-again", {"runner": "workflow", "issue_id": "iss-1"}) and kwargs["issue_id"] == "iss-1"
    assert app.state.world_repo.ratings[-1]["followup_job_id"] == "job-again"
    assert "verdict" not in app.state.world_repo.ratings[-1], "running it again is no verdict on the work"


async def test_a_quest_still_out_or_without_a_launch_record_is_not_run_again(app: Any) -> None:
    app.state.issue_executor = MagicMock()
    app.state.world_repo.quest_row["status"] = "running"
    app.state.jobs_repo = FakeJobs(_failed_job())
    async with await _client(app) as client:
        out = await client.post("/api/v1/world/quests/job-1/run-again", json={}, headers=HEADERS)
        app.state.world_repo.quest_row["status"] = "failed"
        app.state.jobs_repo = FakeJobs(_failed_job(launch=None))
        old = await client.post("/api/v1/world/quests/job-1/run-again", json={}, headers=HEADERS)
    assert out.status_code == 409 and old.status_code == 409
    assert "launch record" in old.json()["detail"]
    app.state.issue_executor.run_again.assert_not_called()


async def test_dismissing_a_quest_takes_it_out_of_the_inbox_with_no_rating(app: Any) -> None:
    app.state.world_repo.quest_row["status"] = "failed"
    async with await _client(app) as client:
        dismissed = await client.post("/api/v1/world/quests/job-1/dismiss", headers=HEADERS)
        app.state.world_repo.quest_row["status"] = "running"
        early = await client.post("/api/v1/world/quests/job-1/dismiss", headers=HEADERS)
    assert dismissed.status_code == 200 and early.status_code == 409
    assert app.state.world_repo.ratings[-1] == {
        "job_id": "job-1",
        "rated_by": app.state.world_repo.ratings[-1]["rated_by"],
    }


async def test_slop_is_cleaned_in_its_dungeon_by_the_slopmaster(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Slop dungeon (spec 2026-10-25): a slop finding is a slop lead, and its area has a dungeon with a score."""

    async def slop_leads(request: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "case:9",
                "kind": "case",
                "repo": "acme/web",
                "area": "Billing",
                "severity": "medium",
                "occurrences": 4,
                "title": "Clean up the outbox retry wrappers",
                "nature": "slop",
                "slop_score": 70,
            }
        ]

    monkeypatch.setattr(world_api, "_leads", slop_leads)
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
    [lead] = world["quests"]
    assert lead["kind"] == "slop" and lead["verb"] == "Clean up" and lead["leader"] == "slopmaster"
    assert lead["creature"] == "slop_ooze" and lead["level"] > 1
    assert world["dungeons"] == [{"repo": "acme/web", "area": "Billing", "score": 70, "open": 1, "cleaned": 1}]


async def test_a_merged_cleanup_is_the_slopmasters_and_earns_its_removed_lines(app: Any) -> None:
    row = app.state.world_repo.quest_row
    row.update(
        status="pr_merged",
        delivery_state="merged",
        pipeline_template="slop-cleanup",
        work_nature={
            "nature": "slop",
            "slop_score": 80,
            "kinds": [],
            "evidence": [],
            "cleanup": {"added": 10, "removed": 110},
        },
    )
    async with await _client(app) as client:
        scroll = (await client.get("/api/v1/world/quests/job-1")).json()
    earned = {(x["agent_type"], x["stat"]): x["xp"] for x in scroll["xp"]}
    assert earned[("developer", "coding")] == 2 * 1_400, "the developer keeps only its own runs' edits"
    assert earned[("slopmaster", "coding")] == 60_000 + 20_000 + 300 * 100
    assert scroll["work_nature"]["cleanup"] == {"added": 10, "removed": 110}
