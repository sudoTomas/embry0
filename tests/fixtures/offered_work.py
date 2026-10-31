"""An app whose offered work spans two repositories, read through API keys with and without an allowlist.

Offered work is what embry0 offers a repository's owners to start: the
proposals (cases, issues, audit findings, leads), the quests on offer that
``GET /decisions`` shows for them, the leads they come from, the asks about
them and the hand-offs of them. A key whose repository allowlist does not
cover a repository reads none of that repository's offered work, as it reads
none of its quests to review.

Each source the routes read offered work from is faked here with one item
in :data:`COVERED`, the repository the limited key covers, and one in
:data:`UNCOVERED`. A route that reads the sources sees both, so a test can
tell which of them each key is shown. There is no database: a route that
needs one fails, and shows nothing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import proposals as proposals_api
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.proposals import Proposal
from embry0.services.quests import Diary, DiaryTier, Task
from embry0.services.world import WorldCache
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.fixtures.asks import asks_snapshot
from tests.unit.api.v1.test_asks_api import FakeAsks, ask
from tests.unit.api.v1.test_world_api import FakeProgression, FakeRepo

NOW = datetime.now(UTC)
#: The repository the limited key covers, and one it does not.
COVERED, UNCOVERED = "acme/web", "globex/gizmos"
#: Each repository's offered work is titled so that a response naming it can be found.
TITLES = {COVERED: "Widgets break on resize", UNCOVERED: "Gizmos overheat at noon"}
#: Each repository's proposal, by id.
PROPOSALS = {COVERED: "issue:widgets-1", UNCOVERED: "issue:gizmos-1"}
#: Who is asked about each repository's proposal: an ask row names its person, not its repository.
RECIPIENTS = {COVERED: "ada@example.com", UNCOVERED: "gizmos-owner@example.com"}
#: The repository of each decision about a proposal.
REPO_OF = {f"offer:{pid}": repo for repo, pid in PROPOSALS.items()}
LIMITED = "emb_" + "l" * 43
OPEN = "emb_" + "o" * 43


def _key(key_id: str, name: str, allowed_repos: list[str] | None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id="usr-chat",
        name=name,
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed_repos,
    )


KEYS = {
    hash_token(LIMITED): _key("key-limited00001", "limited", [COVERED]),
    hash_token(OPEN): _key("key-open00000001", "open", None),
}


def proposal(repo: str) -> Proposal:
    return Proposal(
        id=PROPOSALS[repo],
        kind="issue",
        repo=repo,
        title=TITLES[repo],
        summary=f"Open and not started: {TITLES[repo]}",
        details="",
        action="Send it to triage, which starts its pipeline",
        created_at=NOW - timedelta(hours=2),
    )


class FakeProposalRows:
    async def owners(self) -> dict[str, list[str]]:
        return {COVERED: ["ada"], UNCOVERED: ["ada"]}

    async def decisions(self) -> dict[str, Any]:
        return {}


def lead_row(repo: str) -> dict[str, Any]:
    return {
        "id": f"ticket:{repo.split('/')[1]}",
        "kind": "ticket",
        "repo": repo,
        "area": None,
        "title": TITLES[repo],
        "summary": "",
        "details": "",
        "url": None,
        "severity": "medium",
        "effort": None,
        "action": "",
        "state": "open",
        "first_seen": NOW,
        "last_seen": NOW,
        "gone_at": None,
        "data": {},
    }


class FakeLeads:
    """The leads table: ``repos`` narrows the rows as its query does, by lower-cased slug."""

    async def list(
        self,
        *,
        repo: str | None = None,
        kind: str | None = None,
        state: str | None = "open",
        limit: int = 200,
        repos: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Newest first, the uncovered repository's newest: a page must not be filled before it is narrowed.
        rows = [lead_row(UNCOVERED), lead_row(COVERED)]
        if repo:
            rows = [r for r in rows if r["repo"].lower() == repo.lower()]
        if repos is not None:
            rows = [r for r in rows if r["repo"].lower() in repos]
        return rows[:limit]

    async def open_leads(self) -> list[dict[str, Any]]:
        return []


def handoff_row(repo: str) -> dict[str, Any]:
    return {
        "id": f"ho-{repo.split('/')[1]}",
        "decision_id": f"offer:{PROPOSALS[repo]}",
        "repo": repo,
        "title": TITLES[repo],
        "from_actor": "bo@example.com",
        "to_email": "chat@example.com",
        "note": "",
        "state": "open",
        "outcome": None,
        "closed_by": None,
        "created_at": NOW,
        "closed_at": None,
    }


class FakeHandoffs:
    """The hand-offs table: ``repos`` narrows the rows as its query does, by lower-cased slug."""

    async def recent(self, *, limit: int = 100, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        rows = [handoff_row(UNCOVERED), handoff_row(COVERED)]
        if repos is not None:
            rows = [r for r in rows if r["repo"].lower() in repos]
        return rows[:limit]

    async def open_to(self, email: str) -> list[dict[str, Any]]:
        return [handoff_row(COVERED), handoff_row(UNCOVERED)]

    async def open_for(self, decision_ids: Any) -> dict[str, Any]:
        return {}

    async def get(self, handoff_id: str) -> dict[str, Any] | None:
        return next((handoff_row(r) for r in (COVERED, UNCOVERED) if handoff_row(r)["id"] == handoff_id), None)

    async def close(self, *, handoff_id: str, state: str, outcome: Any, by: str, now: Any) -> dict[str, Any] | None:
        row = await self.get(handoff_id)
        return {**row, "state": state, "outcome": outcome, "closed_by": by, "closed_at": now} if row else None


#: Each repository's finished job: a quest to review, on the world's quest board, in its loot and its scroll.
JOBS = {COVERED: "job-widgets", UNCOVERED: "job-gizmos"}


def quest_row(repo: str) -> dict[str, Any]:
    """A finished job of ``repo`` as the world's rows carry it."""
    return {
        "job_id": JOBS[repo],
        "repo": repo,
        "area": None,
        "task": f"{TITLES[repo]}\n\ndetails",
        "status": "completed",
        "delivery_state": "merged",
        "delivery_state_at": NOW - timedelta(minutes=30),
        "pr_url": None,
        "pipeline_template": "full-magnum-opus",
        "created_at": NOW - timedelta(hours=2),
        "started_at": NOW - timedelta(hours=2),
        "finished_at": NOW - timedelta(hours=1),
        "error_code": None,
        "error_message": None,
        "total_cost_usd": 1.0,
        "cost_usd": 1.0,
        "duration_ms": 60_000,
        "result_summary": "Done.",
        "dispatched_by": "someone",
        "agent_type": "developer",
        "rating": None,
        "verdict": None,
        "note": None,
        "followup_job_id": None,
        "rated_by": None,
        "rated_at": None,
    }


class GameWorld(FakeRepo):
    """The world's rows with work in both repositories: a party out on each, a quest to review, loot, cleanups."""

    async def jobs(self, *, returning_seconds: float, limit: int = 60) -> list[dict[str, Any]]:
        return [
            {"job_id": f"live-{repo.split('/')[1]}", "repo": repo, "task": TITLES[repo], "status": "running"}
            | {"created_at": NOW, "started_at": NOW}
            for repo in (COVERED, UNCOVERED)
        ]

    async def inbox(self, *, days: int, player_actors: list[str], limit: int = 30) -> list[dict[str, Any]]:
        return [quest_row(COVERED), quest_row(UNCOVERED)]

    async def loot(self, *, days: int, limit: int = 30) -> list[dict[str, Any]]:
        return [quest_row(COVERED), quest_row(UNCOVERED)]

    async def cleaned(self, *, days: int) -> dict[tuple[str, str | None], int]:
        return {(COVERED, None): 1, (UNCOVERED, None): 1}

    async def quest_log(self, agent: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return [quest_row(COVERED), quest_row(UNCOVERED)]

    async def quest(self, job_id: str) -> dict[str, Any] | None:
        return next((quest_row(repo) for repo, job in JOBS.items() if job == job_id), None)

    async def merge_approval(self, job_id: str) -> dict[str, Any] | None:
        return None


class GameProgression(FakeProgression):
    """The progression snapshot with a diary in each repository, each with a tier completed: a recent unlock."""

    async def read(self) -> Any:
        progression = await super().read()
        progression.diaries = [
            Diary(repo=repo, tiers=[DiaryTier(tier="easy", tasks=[Task(text="a first job", done_at=NOW)])])
            for repo in (COVERED, UNCOVERED)
        ]
        return progression


class AsksSettings:
    async def snapshot(self) -> Any:
        return asks_snapshot({"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "max_open_per_person": 5})


@contextmanager
def offered_work_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The app, its offered work in :data:`COVERED` and :data:`UNCOVERED`, behind :data:`LIMITED` and :data:`OPEN`."""
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        app = create_app(
            Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
        )
    app.state.db = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    chat = MagicMock(disabled=False, email="chat@example.com", tier="admin", github_login=None)
    app.state.users_repo = MagicMock(get=AsyncMock(return_value=chat), get_by_email=AsyncMock(return_value=None))
    app.state.proposals_repo = FakeProposalRows()
    app.state.proposal_briefs_repo = None
    app.state.leads_repo = FakeLeads()
    app.state.handoffs_repo = FakeHandoffs()
    world = GameWorld()
    app.state.world_repo = world
    app.state.world_cache = WorldCache()
    app.state.progression = GameProgression()
    app.state.settings = AsksSettings()
    asks = FakeAsks()
    for repo, proposal_id in PROPOSALS.items():
        asks.codes[f"offer:{proposal_id}"] = repo.split("/")[1][:4].upper()
        asks.rows.append(ask(f"ask-{repo.split('/')[1]}", f"offer:{proposal_id}", RECIPIENTS[repo]))
    asks.repos.update(REPO_OF)
    app.state.asks_repo = asks

    async def open_proposals(_state: Any) -> list[Proposal]:
        return [proposal(COVERED), proposal(UNCOVERED)]

    async def no_codeowners(_request: Any, repos: set[str]) -> dict[str, list[str]]:
        return {}

    async def no_areas(_request: Any, repos: set[str]) -> dict[str, list[Any]]:
        return {r: [] for r in repos}

    async def no_pr_stats(_request: Any, _pr_url: str | None) -> None:
        return None

    async def regions(_request: Any) -> dict[str, Any]:
        region = {"jobs": 1, "last_job_at": None, "owned": True, "watched": False, "areas": []}
        return {
            "regions": [{"repo": COVERED, **region}, {"repo": UNCOVERED, **region}],
            "frontier": [COVERED, UNCOVERED],
        }

    monkeypatch.setattr(proposals_api, "open_proposals", open_proposals)
    monkeypatch.setattr(proposals_api, "_codeowners", no_codeowners)
    monkeypatch.setattr(proposals_api, "_areas", no_areas)
    monkeypatch.setattr(world_api, "_pr_stats", no_pr_stats)
    monkeypatch.setattr(world_api, "_regions", regions)
    try:
        yield app
    finally:
        bust_api_key_cache()


async def get(app: Any, url: str, token: str, **params: Any) -> Any:
    """``url`` read with ``token``; an app error answers 500 rather than raising."""
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        return await client.get(url, params=params or None)


def names_uncovered(text: str) -> bool:
    """Whether a response names the uncovered repository's offered work, or the person asked about it."""
    return UNCOVERED in text or TITLES[UNCOVERED] in text or RECIPIENTS[UNCOVERED] in text
