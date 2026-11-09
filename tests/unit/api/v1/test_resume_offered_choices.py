"""A resume names a choice the job's pause offered, or it is refused (#182 review, decision a).

The pause lists its choices on its ``interrupt`` event (``max_retries_node``'s
summary): "merge as is" is not offered where a stopped run's work is only in
the sandbox (salvage off), nor a continue at a hard budget ceiling or where
the sandbox has no toolchain. Any other choice is a 409 naming what the pause
offers, and the job stays paused with its sandbox: nothing is scheduled. The
node still reads such a choice as a stop, as a backstop for a path that does
not come through here. A pause recorded without options takes any choice.
The chat route is the same check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
UNCOMMITTED = ["continue_retrying", "abandon"]
AT_CEILING = ["merge_as_is", "abandon"]


def _pause(options: list[str] | None) -> dict[str, Any] | None:
    if options is None:
        return None
    return {"type": "interrupt", "node": "max_retries", "summary": {"reason": "max_retries", "options": options}}


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    jobs = MagicMock()
    jobs.get = AsyncMock(
        return_value={"job_id": "job-paused123", "status": "paused", "repo": "owner/repo", "issue_id": "iss-1"}
    )
    jobs.update = AsyncMock()
    jobs.latest_interrupt = AsyncMock(return_value=_pause(UNCOMMITTED))
    app.state.jobs_repo = jobs
    app.state.issue_executor = MagicMock(schedule_resume=MagicMock(), cancel_job=AsyncMock(), draining=False)
    return app


async def _resume(app: Any, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/jobs/job-paused123/resume", json=body, headers=HEADERS)


@pytest.mark.asyncio
async def test_merge_as_is_where_the_work_is_only_in_the_sandbox_is_a_409_and_the_job_stays_paused(app: Any) -> None:
    resp = await _resume(app, {"choice": "merge_as_is"})
    assert resp.status_code == 409, (
        f"a choice the pause did not offer must be refused, not read as a stop that destroys the sandbox: "
        f"{resp.status_code} {resp.text}"
    )
    assert resp.json()["detail"] == {
        "message": 'This pause does not offer "merge it as it is"; it offers "continue" or "stop".',
        "choice": "merge_as_is",
        "offered": ["continue_retrying", "abandon"],
    }, "a chat shows the message as it is; an API caller reads the ids"
    app.state.issue_executor.schedule_resume.assert_not_called()
    app.state.issue_executor.cancel_job.assert_not_awaited()
    app.state.jobs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_continue_at_a_hard_budget_ceiling_is_a_409(app: Any) -> None:
    app.state.jobs_repo.latest_interrupt = AsyncMock(return_value=_pause(AT_CEILING))
    resp = await _resume(app, {"choice": "continue_retrying"})
    assert resp.status_code == 409, f"continuing past a hard ceiling is not offered: {resp.status_code} {resp.text}"
    detail = resp.json()["detail"]
    assert isinstance(detail, dict) and detail.get("offered") == ["merge_as_is", "abandon"], (
        f"the 409 carries the offered ids for an API caller: {detail}"
    )
    app.state.issue_executor.schedule_resume.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "choice"),
    [
        (UNCOMMITTED, "continue_retrying"),
        (UNCOMMITTED, "abandon"),
        (AT_CEILING, "merge_as_is"),
        (AT_CEILING, "abandon"),
    ],
)
async def test_an_offered_choice_resumes_as_before(app: Any, options: list[str], choice: str) -> None:
    app.state.jobs_repo.latest_interrupt = AsyncMock(return_value=_pause(options))
    resp = await _resume(app, {"choice": choice})
    assert resp.status_code == 200, resp.text
    assert resp.json()["choice"] == choice
    app.state.issue_executor.schedule_resume.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [None, {"type": "interrupt", "summary": {}}, RuntimeError("store down")])
async def test_a_pause_recorded_without_options_takes_any_choice(app: Any, found: Any) -> None:
    app.state.jobs_repo.latest_interrupt = (
        AsyncMock(side_effect=found) if isinstance(found, Exception) else AsyncMock(return_value=found)
    )
    resp = await _resume(app, {"choice": "merge_as_is"})
    assert resp.status_code == 200, f"nothing says what the pause offers: the node is the check: {resp.text}"


@pytest.mark.asyncio
async def test_a_chat_s_continue_the_pause_does_not_offer_gets_the_same_409(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat route runs the same resume: its 409 is a sentence the chat can show as it is."""
    from embry0.api.v1 import asks as asks_api

    app.state.jobs_repo.latest_interrupt = AsyncMock(return_value=_pause(AT_CEILING))
    monkeypatch.setattr(asks_api, "_their_job", AsyncMock(return_value={"job_id": "job-paused123", "status": "paused"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/asks/jobs/job-paused123/resume",
            json={"person": "ada@example.com", "choice": "continue_retrying"},
            headers=HEADERS,
        )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"the 409 is a message for the chat and the ids for an API caller: {detail}"
    assert detail["message"] == ('This pause does not offer "continue"; it offers "merge it as it is" or "stop".'), (
        "the chat relay shows a person's words"
    )
    app.state.issue_executor.schedule_resume.assert_not_called()
