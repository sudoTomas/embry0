"""The asks feed with more than 500 open decisions (#273).

``GET /decisions`` shows at most 500, worst first. The feed reads every open
decision: an ask about one ranked past 500 still counts toward its person's
limit, and still goes out. The decisions come from the real decisions code,
with only the quests on offer faked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import proposals as proposals_api
from embry0.config import Embry0Config
from embry0.services.proposals import Proposal
from tests.unit.api.v1.test_asks_api import FakeAsks, FakeSettings, ask

NOW = datetime.now(UTC)
#: One more than the decisions list shows.
OPEN = 501


def _offer(i: int) -> Proposal:
    """Open issue ``i``; a higher ``i`` is newer, so ranks higher."""
    return Proposal(
        id=f"issue:{i}",
        kind="issue",
        repo="acme/widgets",
        title=f"Widgets break, case {i}",
        summary="Open and not started.",
        details="",
        action="Send it to triage",
        created_at=NOW - timedelta(hours=1) + timedelta(seconds=i),
        owners=("ada",),
    )


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.asks_repo = FakeAsks()
    app.state.settings = FakeSettings(
        {"asks": {"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "max_open_per_person": 1}}
    )
    offers = [_offer(i) for i in range(OPEN)]
    for p in offers:
        app.state.asks_repo.codes[f"offer:{p.id}"] = f"C{p.id.split(':')[1]}"

    async def feed(_request: Any) -> list[Proposal]:
        return list(offers)

    monkeypatch.setattr(proposals_api, "feed", feed)
    return app


async def _get(app: Any, url: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, f"GET {url}: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.mark.asyncio
async def test_an_ask_about_a_decision_ranked_past_500_counts_toward_its_person_s_limit(app: Any) -> None:
    listed = [d["id"] for d in (await _get(app, "/api/v1/decisions?limit=500"))["decisions"]]
    assert len(listed) == 500 and "offer:issue:0" not in listed, "the list's page ends before the oldest decision"
    app.state.asks_repo.rows = [
        ask("held-oldest", "offer:issue:0", "ada@example.com", state="sent", sent_at=NOW - timedelta(hours=1)),
        ask("queued-newest", f"offer:issue:{OPEN - 1}", "ada@example.com"),
    ]
    due = [a["id"] for a in (await _get(app, "/api/v1/asks/due"))["asks"]]
    assert due == [], (
        f"ada holds her one ask about the oldest decision, ranked 501st: it counts, so the newest waits: {due}"
    )


@pytest.mark.asyncio
async def test_an_ask_about_a_decision_ranked_past_500_goes_out(app: Any) -> None:
    app.state.asks_repo.rows = [ask("queued-oldest", "offer:issue:0", "ada@example.com")]
    due = (await _get(app, "/api/v1/asks/due"))["asks"]
    assert [(a["id"], a["decision"]["id"]) for a in due] == [("queued-oldest", "offer:issue:0")], (
        f"the oldest decision is still open, ranked 501st, and its ask is due: {[a['id'] for a in due]}"
    )
