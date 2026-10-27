"""Hand-offs (spec 2026-10-27): every answer settles its decision's hand-off, and its recipient may merge."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api.v1 import merge_approvals, proposals, world
from embry0.services.proposals import Proposal

SETTLED: list[tuple[str, str, str]] = []


@pytest.fixture(autouse=True)
def record_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    SETTLED.clear()

    async def settle(_state: Any, decision_id: str, outcome: str, actor: str) -> None:
        SETTLED.append((decision_id, outcome, actor))

    monkeypatch.setattr("embry0.services.handoffs.settle", settle)


class FakeProposals:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    async def record(self, **kw: Any) -> None:
        self.recorded.append(kw)


def request(*, session_email: str | None = None, **state: Any) -> Any:
    app = SimpleNamespace(state=SimpleNamespace(db=None, config=SimpleNamespace(audit_log_path=None), **state))
    session = SimpleNamespace(email=session_email) if session_email else None
    return SimpleNamespace(app=app, state=SimpleNamespace(session=session, principal=None), headers={})


@pytest.mark.asyncio
async def test_a_proposal_answer_settles_its_hand_off_and_names_the_signed_in_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("embry0.api.session.request_session", lambda r: r.state.session)
    repo = FakeProposals()
    req = request(session_email="ada@example.com", proposals_repo=repo)
    p = Proposal(
        id="crack:1",
        kind="crack",
        repo="acme/web",
        title="t",
        summary="s",
        details="",
        action="a",
        created_at=datetime.now(UTC),
    )
    out = await proposals._answer(req, p, "decline", proposals.AnswerBody(by="dashboard", reason="no"))
    assert out["by"] == "ada@example.com" and repo.recorded[0]["decided_by"] == "ada@example.com"
    assert SETTLED == [("offer:crack:1", "decline", "ada@example.com")]

    chat = request(proposals_repo=FakeProposals())
    await proposals._answer(chat, p, "snooze", proposals.AnswerBody(by="octocat"))
    assert SETTLED[-1] == ("offer:crack:1", "later", "octocat"), "a key acting for a person keeps the person it names"


class FakeWorld:
    async def rate(self, job_id: str, *, rated_by: str, **fields: Any) -> dict[str, Any]:
        return {"rated_by": rated_by, "rated_at": datetime.now(UTC), **fields}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "fields", "outcome"),
    [
        ("quest.evaluated", {"verdict": "merged"}, "merged"),
        ("quest.evaluated", {"rating": "good"}, "good"),
        ("quest.sent_back", {"verdict": "sent_back", "rating": "needs_work"}, "send_back"),
        ("quest.rejected", {"verdict": "rejected", "rating": "bad"}, "reject"),
        ("quest.restarted", {"followup_job_id": "job-2"}, "run_again"),
        ("quest.dismissed", {}, "dismiss"),
    ],
)
async def test_every_answer_on_a_finished_quest_settles_its_hand_off(
    monkeypatch: pytest.MonkeyPatch, action: Any, fields: dict[str, Any], outcome: str
) -> None:
    monkeypatch.setattr(world, "request_actor", lambda _r: "ada@example.com")
    await world._audit(request(world_repo=FakeWorld()), action, "job-1", **fields)
    assert SETTLED == [("done:job-1", outcome, "ada@example.com")]


class FakeApprovals:
    async def get(self, approval_id: str) -> dict[str, Any] | None:
        return {"id": approval_id, "job_id": "job-1"} if approval_id == "ma-1" else None


class FakeHandoffs:
    async def open_for(self, ids: Any) -> dict[str, dict[str, Any]]:
        return {"done:job-1": {"to_email": "bo@example.com"}} if "done:job-1" in set(ids) else {}


@pytest.mark.asyncio
async def test_only_the_recipient_of_the_quest_s_hand_off_may_merge_without_being_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = request(handoffs_repo=FakeHandoffs())
    monkeypatch.setattr(merge_approvals, "request_actor", lambda _r: "Bo@example.com")
    assert await merge_approvals._holds_handoff(req, FakeApprovals(), "ma-1") is True
    assert await merge_approvals._holds_handoff(req, FakeApprovals(), "ma-9") is False
    monkeypatch.setattr(merge_approvals, "request_actor", lambda _r: "cy@example.com")
    assert await merge_approvals._holds_handoff(req, FakeApprovals(), "ma-1") is False
    assert await merge_approvals._holds_handoff(request(), FakeApprovals(), "ma-1") is False
