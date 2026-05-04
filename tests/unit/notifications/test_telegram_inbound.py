"""Telegram reply-to-message routes the answer to the correct issue_inputs row.

The handler in ``athanor/api/v1/telegram.py`` looks up an input by its
``telegram_message_id`` regardless of which node originally asked the
question — so triage ``needs_info`` and developer/review ``agent_ask_user``
rows go through the same path. Plan B Task 4 locks that behavior with a
regression test for the agent ask-user case (the triage case is exercised
in higher-level integration tests, but we cover it here too for cheap
defense in depth).

These tests drive the FastAPI handler through ``httpx.AsyncClient`` against
an in-memory ASGI app whose ``app.state`` repos are mocks, mirroring
``test_github_inbound.py``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig

WEBHOOK_SECRET = "test-telegram-secret"


def _make_app(*, input_row: dict | None, pending_blocking_after: int = 0):
    """Build a test app with mocked repos.

    ``input_row`` is what ``inputs_repo.get_by_telegram_message`` returns
    (or None to simulate an unmatched reply). ``pending_blocking_after``
    is the value ``count_pending_blocking`` returns after the answer is
    recorded — 0 means the workflow should resume.
    """
    config = AthanorConfig(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        telegram_bot_token="",  # disables outbound edit calls in test
        telegram_chat_id="",
    )
    app = create_app(config)

    inputs_repo = MagicMock()
    inputs_repo.get_by_telegram_message = AsyncMock(return_value=input_row)
    inputs_repo.answer = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=pending_blocking_after)

    issues_repo = MagicMock()
    issues_repo.get = AsyncMock(return_value={"id": "iss-1", "status": "awaiting_input"})

    executor = MagicMock()
    executor.resume_for_issue = AsyncMock()

    app.state.inputs_repo = inputs_repo
    app.state.issues_repo = issues_repo
    app.state.issue_executor = executor
    app.state.telegram_webhook_secret = WEBHOOK_SECRET
    return app, inputs_repo, executor


def _telegram_reply_payload(
    *,
    replied_message_id: int,
    text: str,
    username: str | None = None,
    chat_id: int = 999,
) -> dict:
    sender: dict = {}
    if username is not None:
        sender["username"] = username
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "from": sender,
            "reply_to_message": {"message_id": replied_message_id},
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_telegram_reply_routes_developer_ask_user_input():
    """A reply to a developer's agent_ask_user message gets routed through
    the same ``inputs_repo.answer`` call as triage needs_info — no
    asking_node-specific branch — and resumes the pipeline once no
    blocking inputs remain."""
    input_row = {
        "id": "inp-dev-1",
        "issue_id": "iss-1",
        "asking_node": "developer",
        "status": "pending",
    }
    app, inputs_repo, executor = _make_app(input_row=input_row)

    payload = _telegram_reply_payload(replied_message_id=555, text="yes please", username="user42")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/telegram/callback",
            content=json.dumps(payload).encode(),
            headers={
                "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered"
    assert body["input_id"] == "inp-dev-1"
    inputs_repo.get_by_telegram_message.assert_awaited_once_with(555)
    inputs_repo.answer.assert_awaited_once_with("inp-dev-1", answer="yes please", answered_by="telegram:user42")
    executor.resume_for_issue.assert_awaited_once_with("iss-1")


@pytest.mark.asyncio
async def test_telegram_reply_routes_triage_needs_info_input():
    """The original triage path still works — same code, same call shape."""
    input_row = {
        "id": "inp-triage-1",
        "issue_id": "iss-1",
        "asking_node": "triage",
        "status": "pending",
    }
    app, inputs_repo, executor = _make_app(input_row=input_row)

    payload = _telegram_reply_payload(replied_message_id=777, text="bug in login", username="reporter")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/telegram/callback",
            content=json.dumps(payload).encode(),
            headers={
                "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    inputs_repo.answer.assert_awaited_once_with("inp-triage-1", answer="bug in login", answered_by="telegram:reporter")
    executor.resume_for_issue.assert_awaited_once_with("iss-1")


@pytest.mark.asyncio
async def test_telegram_reply_does_not_resume_when_blocking_inputs_remain():
    """If other blocking inputs are still pending, the answer is recorded
    but the pipeline is NOT resumed."""
    input_row = {
        "id": "inp-dev-2",
        "issue_id": "iss-1",
        "asking_node": "developer",
        "status": "pending",
    }
    app, inputs_repo, executor = _make_app(input_row=input_row, pending_blocking_after=1)

    payload = _telegram_reply_payload(replied_message_id=556, text="ok", username="alice")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/telegram/callback",
            content=json.dumps(payload).encode(),
            headers={
                "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    inputs_repo.answer.assert_awaited_once()
    executor.resume_for_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_reply_falls_back_to_chat_id_when_username_missing():
    """Anonymous Telegram replies (no ``from.username``) fall back to chat id
    in the audit trail rather than a bare ``"telegram"`` label."""
    input_row = {
        "id": "inp-dev-3",
        "issue_id": "iss-1",
        "asking_node": "developer",
        "status": "pending",
    }
    app, inputs_repo, _ = _make_app(input_row=input_row)

    payload = _telegram_reply_payload(replied_message_id=558, text="sure", username=None, chat_id=4242)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/telegram/callback",
            content=json.dumps(payload).encode(),
            headers={
                "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    inputs_repo.answer.assert_awaited_once_with("inp-dev-3", answer="sure", answered_by="telegram:4242")
