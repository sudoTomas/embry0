"""The asks feed read through a key limited to some repositories (#272).

The key is a real API key with an allowlist, checked by the app's own
authentication, and the decisions come from the real ``GET /decisions``
code with only its sources faked, so the key rule the feed hands asks out by
is the one the decisions list applies.

A person's unanswered asks count toward their limit whether or not the key
covers the repositories of their decisions: a key that counted only what it
can read would release asks past the limit.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterator
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
from embry0.storage import repo_renames
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.fixtures.asks import asks_snapshot
from tests.unit.api.v1.test_asks_api import FakeAsks, ask

NOW = datetime.now(UTC)
KEY = "emb_" + "c" * 43
#: The repositories the chat's key covers.
COVERED = ["acme/web", "globex/gizmos"]
#: Working hours that hold whenever the test runs: NOW is the real time.
ALWAYS = {"enabled": True, "hours": "00-24", "weekdays": "mon-sun"}


def _review(job_id: str, repo: str) -> dict[str, Any]:
    """A finished job's inbox row: a quest to review in ``repo``."""
    return {
        "job_id": job_id,
        "repo": repo,
        "area": None,
        "task": f"Fix {job_id}",
        "status": "completed",
        "delivery_state": None,
        "pr_url": None,
        "pipeline_template": "full-magnum-opus",
        "created_at": NOW - timedelta(hours=2),
        "finished_at": NOW - timedelta(hours=1),
        "error_code": None,
        "error_message": None,
        "total_cost_usd": 1.0,
        "dispatched_by": "someone",
        "agent_type": "developer",
    }


class World:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def inbox(self, *, days: int, player_actors: list[str], limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]

    async def merge_approval(self, job_id: str) -> dict[str, Any] | None:
        return None


class Settings:
    """The settings store as the feed reads it: the deployment's ``asks`` values and each organization's layer."""

    def __init__(self) -> None:
        self.deployment: dict[str, Any] = dict(ALWAYS)
        self.organizations: dict[str, dict[str, Any]] = {}

    async def snapshot(self) -> Any:
        return asks_snapshot(self.deployment, self.organizations)


def _offer(proposal_id: str, repo: str) -> Proposal:
    return Proposal(
        id=proposal_id,
        kind="issue",
        repo=repo,
        title=f"Issue {proposal_id}",
        summary="Open and not started.",
        details="",
        action="Send it to triage",
        created_at=NOW - timedelta(hours=3),
        owners=("ada",),
    )


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        app = create_app(
            Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
        )  # type: ignore[call-arg]
    app.state.db = None
    app.state.world_repo = World()
    app.state.jobs_repo = None
    app.state.settings = Settings()
    app.state.asks_repo = FakeAsks()
    row = ApiKeyRow(
        id="key-chat00000001",
        user_id="usr-chat",
        name="chat",
        prefix="emb_xxxxxxxx",
        scope="propose",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=COVERED,
    )
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(KEY) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    chat = MagicMock(disabled=False, email="chat@example.com")
    app.state.users_repo = MagicMock(get=AsyncMock(return_value=chat), get_by_email=AsyncMock(return_value=None))
    offers: list[Proposal] = []
    app.state.offers = offers

    async def feed(_request: Any) -> list[Proposal]:
        return list(offers)

    async def pr_stats(_request: Any, _pr_url: str | None) -> None:
        return None

    monkeypatch.setattr(proposals_api, "feed", feed)
    monkeypatch.setattr(world_api, "_pr_stats", pr_stats)
    yield app
    bust_api_key_cache()


def _reviews(app: Any, *rows: tuple[str, str]) -> None:
    """Make each ``(job id, repository)`` a quest to review, with a code."""
    app.state.world_repo.rows = [_review(job_id, repo) for job_id, repo in rows]
    for job_id, _repo in rows:
        app.state.asks_repo.codes[f"done:{job_id}"] = job_id.upper()


async def _get(app: Any, url: str) -> Any:
    headers = {"Authorization": f"Bearer {KEY}", "X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        resp = await client.get(url)
    assert resp.status_code == 200, f"GET {url} through the limited key: {resp.status_code} {resp.text}"
    return resp.json()


async def _due(app: Any) -> list[str]:
    return [a["id"] for a in (await _get(app, "/api/v1/asks/due"))["asks"]]


SENT = {"state": "sent", "sent_at": NOW - timedelta(hours=1)}


@pytest.mark.asyncio
async def test_a_person_holding_the_limit_through_asks_the_key_cannot_read_gets_none_through_it(app: Any) -> None:
    app.state.settings.deployment["max_open_per_person"] = 1
    _reviews(app, ("job-api", "acme/api"), ("job-web", "acme/web"))
    app.state.asks_repo.rows = [
        ask("held-api", "done:job-api", "ada@example.com", **SENT),
        ask("queued-web", "done:job-web", "ada@example.com"),
    ]
    listed = [d["id"] for d in (await _get(app, "/api/v1/decisions"))["decisions"]]
    assert listed == ["done:job-web"], f"the key covers acme/web, not acme/api: {listed}"

    due = await _due(app)
    assert due == [], (
        "ada already holds acme's one unanswered ask, about acme/api, which this key cannot read: "
        f"the key may not release another past her limit: {due}"
    )


@pytest.mark.asyncio
async def test_asks_the_key_cannot_read_count_in_their_own_organization(app: Any) -> None:
    app.state.settings.deployment["max_open_per_person"] = 1
    _reviews(app, ("job-api", "acme/api"), ("job-web", "acme/web"), ("job-gizmos", "globex/gizmos"))
    app.state.asks_repo.rows = [
        ask("held-api", "done:job-api", "ada@example.com", **SENT),
        ask("queued-web", "done:job-web", "ada@example.com"),
        ask("queued-gizmos", "done:job-gizmos", "ada@example.com"),
    ]
    due = await _due(app)
    assert due == ["queued-gizmos"], (
        "the acme/api ask the key cannot read fills ada's acme limit, so acme/web's waits; globex counts apart "
        f"and its ask goes: {due}"
    )


@pytest.mark.asyncio
async def test_an_ask_whose_decision_is_no_longer_open_still_counts_for_nothing(app: Any) -> None:
    """Unchanged: an ask about a decision that was answered meanwhile waits for the ask loop to withdraw it."""
    app.state.settings.deployment["max_open_per_person"] = 1
    _reviews(app, ("job-web", "acme/web"))
    app.state.asks_repo.rows = [
        ask("held-gone", "done:job-gone", "ada@example.com", **SENT),
        ask("queued-web", "done:job-web", "ada@example.com"),
    ]
    due = await _due(app)
    assert due == ["queued-web"], f"job-gone is no longer open, so its ask holds no slot: {due}"


@pytest.mark.asyncio
async def test_the_feed_hands_the_key_only_asks_about_decisions_the_decisions_list_shows_it(app: Any) -> None:
    """Whatever the feed reads to count, it hands out by the decisions list's own key rule."""
    _reviews(app, ("job-api", "acme/api"), ("job-web", "acme/web"))
    app.state.offers.extend([_offer("issue:api", "acme/api"), _offer("issue:web", "acme/web")])
    for decision_id in ("offer:issue:api", "offer:issue:web"):
        app.state.asks_repo.codes[decision_id] = decision_id.split(":")[-1].upper() + "Q"
    app.state.asks_repo.rows = [
        ask("q-review-api", "done:job-api", "bo@example.com"),
        ask("q-review-web", "done:job-web", "cy@example.com"),
        ask("q-offer-api", "offer:issue:api", "di@example.com"),
        ask("q-offer-web", "offer:issue:web", "ed@example.com"),
        ask("r-review-api", "done:job-api", "fay@example.com", state="sent", sent_at=NOW - timedelta(days=2)),
    ]
    listed = {d["id"] for d in (await _get(app, "/api/v1/decisions"))["decisions"]}
    handed = (await _get(app, "/api/v1/asks/due"))["asks"]
    expected = sorted(a["id"] for a in app.state.asks_repo.rows if a["decision_id"] in listed)
    assert sorted(a["id"] for a in handed) == expected, (
        f"the key is handed the asks about the decisions GET /decisions lists to it ({sorted(listed)}), "
        f"reminders included, and no other: {sorted(a['id'] for a in handed)}"
    )
    assert "r-review-api" not in expected, "the acme/api review's reminder is not the key's to send"


FUZZ_REPOS = [
    "acme/web",
    "ACME/Web",
    "acme/api",
    "globex/gizmos",
    "globex/other",
    "acme/old-web",
    "",
    None,
    "initech/tps",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(25))
async def test_the_feed_hands_a_key_exactly_the_decisions_the_list_shows_it_for_any_mix(
    app: Any, monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    """Quests on offer and to review in random repositories, renamed, mixed-case and none among them: the decisions
    the feed hands asks out about are exactly those GET /decisions lists to the key, so the two cannot drift."""
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-web": "acme/web"})
    rng = random.Random(seed)
    app.state.settings.deployment["max_open_per_person"] = 50
    reviews = [(f"job-{i}", rng.choice(FUZZ_REPOS)) for i in range(rng.randrange(0, 12))]
    _reviews(app, *reviews)  # type: ignore[arg-type]
    offers = [(f"issue:{i}", rng.choice([r for r in FUZZ_REPOS if r])) for i in range(rng.randrange(0, 12))]
    app.state.offers.extend(_offer(pid, str(repo)) for pid, repo in offers)
    rows = [ask(f"q-{job}", f"done:{job}", f"{job}@example.com") for job, _ in reviews]
    for pid, _ in offers:
        app.state.asks_repo.codes[f"offer:{pid}"] = pid.replace(":", "").upper()
        rows.append(ask(f"q-{pid}", f"offer:{pid}", f"{pid.replace(':', '')}@example.com"))
    app.state.asks_repo.rows = rows
    listed = {d["id"] for d in (await _get(app, "/api/v1/decisions?limit=500"))["decisions"]}
    handed = {a["decision"]["id"] for a in (await _get(app, "/api/v1/asks/due?limit=200"))["asks"]}
    assert handed == listed, (
        f"seed {seed}: the feed and the list disagree on {sorted(handed ^ listed)} (reviews {reviews}, offers {offers})"
    )
