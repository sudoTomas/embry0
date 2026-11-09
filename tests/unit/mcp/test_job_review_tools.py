"""The MCP review tools: a finished job's evaluation read and the five review answers, through the job's own routes.

Reviewing a job is core work, not the game's (#177), so an assistant can do
through MCP what the Decisions page does. The tools reach embry0 through its
REST routes with the caller's own key (``embry0.mcp_server.client``):

- every write needs ``confirm=true`` and does nothing without it;
- on the real app over a real database (:mod:`tests.fixtures.job_reads`),
  each tool, through a key whose list covers one repository, gives the other
  repository's job exactly as it gives a job that does not exist; reading is
  not audited as a refusal, and each write's refusal is, once;
- a review recorded through the tools reads back through ``job_evaluation``
  and is audited as ``job.review.*``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import JOB, MISSING, job_reads_app
from tests.fixtures.repository_writes import COVERED, UNCOVERED
from tests.unit.api.test_repository_writes_inventory import _refusals


def review_tool(name: str) -> Callable[..., Awaitable[Any]]:
    """The MCP server's tool ``name``; a test of a tool the server does not have fails saying so."""
    found = getattr(server, name, None)
    assert found is not None, f"the MCP server has no {name} tool: an assistant cannot review a job through it"
    return found


#: Each review tool called on a job id, confirmed: the read, then the five writes.
TOOLS: dict[str, Callable[[str], Awaitable[Any]]] = {
    "job_evaluation": lambda job: review_tool("job_evaluation")(job),
    "job_evaluate": lambda job: review_tool("job_evaluate")(job, rating="good", note="clean", confirm=True),
    "job_send_back": lambda job: review_tool("job_send_back")(job, "Handle the empty list too.", confirm=True),
    "job_run_again": lambda job: review_tool("job_run_again")(job, confirm=True),
    "job_dismiss": lambda job: review_tool("job_dismiss")(job, confirm=True),
    "job_reject": lambda job: review_tool("job_reject")(job, note="not wanted", confirm=True),
}
#: The tools that act on the job: a refusal of theirs is logged and audited; the read's is not.
WRITES = sorted(set(TOOLS) - {"job_evaluation"})


class RecordingClient:
    """Stands in for Embry0Client; records every method call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., Awaitable[dict[str, str]]]:
        async def method(*args: Any, **kwargs: Any) -> dict[str, str]:
            self.calls.append((name, args, kwargs))
            return {"called": name}

        return method


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> RecordingClient:
    client = RecordingClient()
    monkeypatch.setattr(server, "_client", client)
    return client


async def test_every_review_tool_is_registered() -> None:
    tools = {registered.name for registered in await server.mcp.list_tools()}
    missing = sorted(set(TOOLS) - tools)
    assert missing == [], f"the review tools an assistant reviews a job with are not registered: {missing}"


async def test_each_review_write_refuses_without_confirm_and_names_the_job(fake_client: RecordingClient) -> None:
    refusals = {
        "job_evaluate": await review_tool("job_evaluate")("job-1", rating="good"),
        "job_send_back": await review_tool("job_send_back")("job-1", "again"),
        "job_run_again": await review_tool("job_run_again")("job-1"),
        "job_dismiss": await review_tool("job_dismiss")("job-1"),
        "job_reject": await review_tool("job_reject")("job-1", close_pr=True),
    }
    for name, refusal in refusals.items():
        assert refusal.get("error") == "confirmation_required", f"{name} acted without confirm=true: {refusal}"
        assert f"{name} again with confirm=true" in refusal["message"] and "job-1" in refusal["message"], refusal
    assert "close its pull request" in refusals["job_reject"]["message"], "the refusal says the forge is acted on"
    assert fake_client.calls == [], f"a refused tool reached the API: {fake_client.calls}"


async def test_each_review_tool_passes_what_it_was_given_on(fake_client: RecordingClient) -> None:
    await review_tool("job_evaluation")("job-1")
    await review_tool("job_evaluate")("job-1", rating="bad", verdict="merged", note="n", confirm=True)
    await review_tool("job_send_back")("job-1", "notes", model="m-1", credential_id="cred-000000000009", confirm=True)
    await review_tool("job_run_again")("job-1", model="m-1", confirm=True)
    await review_tool("job_dismiss")("job-1", confirm=True)
    await review_tool("job_reject")("job-1", note="no", close_pr=True, confirm=True)
    assert fake_client.calls == [
        ("job_evaluation", ("job-1",), {}),
        ("job_evaluate", ("job-1",), {"rating": "bad", "verdict": "merged", "note": "n"}),
        ("job_send_back", ("job-1", "notes"), {"model": "m-1", "credential_id": "cred-000000000009"}),
        ("job_run_again", ("job-1",), {"model": "m-1", "credential_id": ""}),
        ("job_dismiss", ("job-1",), {}),
        ("job_reject", ("job-1",), {"note": "no", "close_pr": True}),
    ], f"each tool passes every argument it was given on to the client: {fake_client.calls}"


def _use(monkeypatch: pytest.MonkeyPatch, app: FastAPI, key: str) -> Embry0Client:
    """The MCP server's client, calling ``app`` with ``key``."""
    client = Embry0Client("http://test", api_key=key, transport=ASGITransport(app=app, raise_app_exceptions=False))
    monkeypatch.setattr(server, "_client", client)
    return client


async def _outcome(call: Awaitable[Any]) -> Any:
    try:
        return "ok", await call
    except Embry0APIError as exc:
        return "error", exc.status_code, exc.detail


@pytest.mark.requires_postgres
@pytest.mark.parametrize("tool", list(TOOLS))
async def test_a_review_tool_gives_another_repository_s_job_as_a_job_that_does_not_exist(
    tool: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        limited = _use(monkeypatch, app, keys.limited)
        before = await _refusals(db)
        uncovered = await _outcome(TOOLS[tool](JOB[UNCOVERED]))
        refused = (await _refusals(db))[len(before) :]
        missing = await _outcome(TOOLS[tool](MISSING))
        await limited.aclose()
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await _outcome(TOOLS[tool](JOB[UNCOVERED]))
        await everyone_client.aclose()
    assert repr(uncovered).replace(JOB[UNCOVERED], "<job>") == repr(missing).replace(MISSING, "<job>"), (
        f"{tool}: the limited key gets {UNCOVERED}'s job as a missing one: {uncovered} against {missing}"
    )
    assert uncovered[:2] == ("error", 404), f"{tool}: {uncovered}"
    expected = [UNCOVERED] if tool in WRITES else []
    assert refused == expected, f"{tool}: refusals audited, naming the repository, for a write only: {refused}"
    assert everyone[:2] != ("error", 404), f"{tool}: a key without an allowlist reaches the job: {everyone}"


@pytest.mark.requires_postgres
async def test_a_review_through_the_tools_reads_back_and_is_audited(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    job = JOB[COVERED]
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        client = _use(monkeypatch, app, keys.limited)
        before = await review_tool("job_evaluation")(job)
        rated = await review_tool("job_evaluate")(job, rating="needs_work", note="Name the edge case.", confirm=True)
        after = await review_tool("job_evaluation")(job)
        rejected = await review_tool("job_reject")(job, note="not wanted", confirm=True)
        refused = [
            await _outcome(review_tool("job_send_back")(job, "again", confirm=True)),
            await _outcome(review_tool("job_run_again")(job, confirm=True)),
        ]
        await client.aclose()
        actions = [
            r["action"]
            for r in await db.fetch(
                "SELECT action FROM audit_log WHERE details->>'job_id' = $1 AND action LIKE 'job.review.%' ORDER BY id",
                job,
            )
        ]
    assert before == {
        "job_id": job,
        "rating": None,
        "verdict": None,
        "note": "",
        "followup_job_id": None,
        "by": None,
        "at": None,
    }, f"a job nobody reviewed reads as nulls: {before}"
    assert after == rated, f"the evaluation reads back as the write answered it: {after} against {rated}"
    assert (after["rating"], after["note"]) == ("needs_work", "Name the edge case.") and after["by"] and after["at"]
    assert (rejected["verdict"], rejected["closed_pr"]) == ("rejected", False), rejected
    assert [r[:2] for r in refused] == [("error", 409), ("error", 409)], (
        f"send-back and run-again answer the route's own refusal through the tools: {refused}"
    )
    assert actions == ["job.review.evaluated", "job.review.rejected"], f"the review's audit rows: {actions}"
