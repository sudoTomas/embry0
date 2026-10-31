"""The world API (spec 2026-10-25): the guild hall, a character's quest log and a quest's scroll.

Reviewing the work moved to ``/jobs/{job_id}/…``: ``test_job_review_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.progression import Character, Progression
from embry0.services.progression_pack import Rules, default_rules
from embry0.services.world import WorldCache

NOW = datetime.now(UTC)


class FakeRepo:
    def __init__(self) -> None:
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


class FakeProgression:
    async def rules(self) -> Rules:
        return default_rules()

    async def read(self) -> Progression:
        rules = default_rules()
        return Progression(
            characters={a: Character(a, rules=rules) for a in ("developer", "review", "why")}, rules=rules
        )


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


async def test_the_world_names_every_kind_a_quest_can_be(app: Any) -> None:
    """What each kind is called comes from the lead-kind catalogue's one endpoint (#166), GET /lead-kinds,
    which answers with the game off too; the guild's read carries each quest's own game words."""
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
        resp = await client.get("/api/v1/lead-kinds")
    assert resp.status_code == 200, f"GET /lead-kinds serves the lead-kind catalogue: {resp.status_code}"
    assert "lead_kinds" not in world, "the catalogue has one endpoint, GET /lead-kinds, not a copy in GET /world"
    kinds = {k["id"]: k for k in resp.json()["kinds"]}
    assert list(kinds) == [
        "case",
        "issue",
        "finding",
        "ticket",
        "ci",
        "alert",
        "flaky",
        "review",
        "crack",
        "weed",
        "slop",
    ]
    assert kinds["crack"] == {
        "id": "crack",
        "label": "Crack",
        "plural": "Cracks",
        "about": "a crack the scout found in the code",
        "found_by": "the scout",
        "lead": True,
    }, "only what a client reads is served, its plain names and whether it is a lead: every served field is API"
    assert kinds["slop"]["label"] == "Slop, not a bug" and kinds["slop"]["lead"] is False
    quest = world["quests"][0]
    assert (quest["verb"], quest["creature"]) == ("Attack", "case_bug"), (
        "a quest still carries its own verb and creature"
    )


async def test_the_catalogue_is_revalidated_by_its_etag(app: Any) -> None:
    """It changes only with a release: a client keeps it and asks again with its ETag on every reload, so a new
    kind shows on the first reload after a deploy and a 304 is all it costs otherwise."""
    async with await _client(app) as client:
        first = await client.get("/api/v1/lead-kinds")
        etag = first.headers.get("etag")
        assert etag and etag.startswith('"'), f"the catalogue carries an ETag: {first.headers}"
        assert first.headers.get("cache-control") == "private, no-cache", "a browser asks again on every use"
        again = await client.get("/api/v1/lead-kinds", headers={"If-None-Match": etag})
        assert (again.status_code, again.content) == (304, b""), "unchanged: a 304, no body"
        assert again.headers.get("etag") == etag
        other = await client.get("/api/v1/lead-kinds", headers={"If-None-Match": '"an-older-release"'})
        assert other.status_code == 200 and other.json() == first.json(), "another release's tag gets the catalogue"


def test_the_etag_is_the_catalogue_s_hash() -> None:
    """A deploy that changes a kind changes the tag: it is computed from what is served, not fixed."""
    from embry0.api.v1 import lead_kinds as route
    from embry0.services.lead_kinds import catalogue

    assert route.ETAG == route.etag_of({"kinds": catalogue()}), "the tag is the hash of the catalogue served"
    renamed = [{**kind, "label": f"{kind['label']}!"} if kind["id"] == "crack" else kind for kind in catalogue()]
    assert route.etag_of({"kinds": renamed}) != route.ETAG, "a kind named differently is a new tag"


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
    assert world["dungeons"] == [
        {"repo": "acme/web", "area": "Billing", "score": 70, "open": 1, "cleaned": 1, "meter": None}
    ]


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


async def test_the_hall_names_each_job_by_its_brief_once_written(app: Any) -> None:
    """Job briefs (spec 2026-10-26): a plain title in the hall, the inbox, the loot, the log and the scroll."""

    class Briefs:
        async def current(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
            titles = {"job-live": "Find why the sender drops mail", "job-1": "Stop the sender dropping mail"}
            return {i: {"title": titles[i]} for i in job_ids if i in titles}

    app.state.job_briefs_repo = Briefs()
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
        scroll = (await client.get("/api/v1/world/quests/job-1")).json()
        dev = (await client.get("/api/v1/world/characters/developer")).json()
    assert world["parties"][0]["title"] == "Find why the sender drops mail"
    assert world["inbox"][0]["title"] == "Stop the sender dropping mail"
    assert world["loot"][0]["title"] == "Stop the sender dropping mail"
    assert scroll["title"] == "Stop the sender dropping mail"
    assert dev["quest_log"][0]["title"] == "Stop the sender dropping mail"


async def test_a_job_without_a_brief_keeps_its_task_s_first_line(app: Any) -> None:
    class Unreadable:
        async def current(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
            raise RuntimeError("database away")

    app.state.job_briefs_repo = Unreadable()
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
    assert world["inbox"][0]["title"] == "Fix the sender"


async def test_a_dungeon_shows_its_slop_o_meter_reading(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Slop-O-Meter (spec 2026-10-26): a scored area has a dungeon with its level, even with no open finding."""

    class Meters:
        async def scores(self, repo: str | None = None) -> list[dict[str, Any]]:
            return [
                {
                    "repo": "acme/web",
                    "area": "Billing",
                    "score": 0.52,
                    "level": 3,
                    "name": "Lasagna",
                    "provisional": False,
                    "computed_at": NOW,
                }
            ]

    async def no_leads(request: Any) -> list[dict[str, Any]]:
        return []

    app.state.slop_meter_repo = Meters()
    monkeypatch.setattr(world_api, "_leads", no_leads)
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
    [dungeon] = world["dungeons"]
    assert dungeon["area"] == "Billing" and dungeon["open"] == 0 and dungeon["cleaned"] == 1
    assert dungeon["meter"] == {
        "score": 0.52,
        "level": 3,
        "name": "Lasagna",
        "provisional": False,
        "computed_at": NOW.isoformat(),
    }
