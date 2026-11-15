"""Tests for the Telegram callback handler's auth posture."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.v1.telegram import router
from embry0.config import TelegramSenderAllowlist

CHAT_ID = "-1001234567890"


def _make_app(secret: str, inputs_repo=None, issues_repo=None, executor=None, config=None):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.telegram_webhook_secret = secret
    app.state.inputs_repo = inputs_repo or MagicMock()
    app.state.issues_repo = issues_repo or MagicMock()
    app.state.issue_executor = executor or MagicMock()
    app.state.config = config or MagicMock(
        telegram_bot_token="",
        telegram_chat_id=CHAT_ID,
        telegram_sender_allowlist=None,
    )
    return app


def _reply_update(chat_id: str | int = CHAT_ID, sender_id: int = 42, message_id: int = 777) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": message_id + 1,
            "chat": {"id": chat_id},
            "from": {"id": sender_id, "username": "operator"},
            "text": "the answer",
            "reply_to_message": {"message_id": message_id},
        },
    }


def test_callback_returns_503_when_secret_empty():
    """Empty secret → 503, never accept."""
    app = _make_app(secret="")
    client = TestClient(app)
    resp = client.post("/api/v1/telegram/callback", json={"update_id": 1})
    assert resp.status_code == 503
    assert resp.json()["error"] == "telegram_callback_unconfigured"


def test_callback_returns_401_when_secret_mismatched():
    app = _make_app(secret="real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_callback_passes_when_secret_matches():
    """Correct secret + allowed chat → 200; empty payload is 'ignored'."""
    app = _make_app(secret="real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json={"update_id": 1, "message": {"chat": {"id": CHAT_ID}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    # The handler returns "ignored" because there's no reply_to_message
    assert resp.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# Chat / sender scoping (#247)
# ---------------------------------------------------------------------------


def _answering_app(config=None):
    """App whose repo resolves any replied-to message id to a pending input."""
    inputs_repo = MagicMock()
    inputs_repo.get_by_telegram_message = AsyncMock(
        return_value={"id": "inp-1", "issue_id": "iss-1", "status": "pending"}
    )
    inputs_repo.count_pending_blocking = AsyncMock(return_value=1)
    executor = MagicMock()
    executor.resume_for_issue = AsyncMock()
    app = _make_app(secret="real-secret", inputs_repo=inputs_repo, executor=executor, config=config)
    return app, inputs_repo


@pytest.fixture
def record_answer_spy(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr("embry0.api.v1.telegram.record_answer", spy)
    return spy


def test_reply_from_foreign_chat_records_nothing(record_answer_spy):
    """A colliding message_id from another chat must not answer the input."""
    app, inputs_repo = _answering_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(chat_id="999888777"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 204
    assert not resp.content
    inputs_repo.get_by_telegram_message.assert_not_awaited()
    record_answer_spy.assert_not_awaited()


def test_reply_rejected_when_chat_id_unconfigured(record_answer_spy):
    """An unset telegram_chat_id rejects everything — never accept-all."""
    config = MagicMock(
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_sender_allowlist=None,
    )
    app, inputs_repo = _answering_app(config=config)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 204
    inputs_repo.get_by_telegram_message.assert_not_awaited()
    record_answer_spy.assert_not_awaited()


def test_reply_from_non_allowlisted_sender_records_nothing(record_answer_spy):
    config = MagicMock(
        telegram_bot_token="",
        telegram_chat_id=CHAT_ID,
        telegram_sender_allowlist=TelegramSenderAllowlist(ids=frozenset({111, 222}), invalid=()),
    )
    app, inputs_repo = _answering_app(config=config)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(sender_id=333),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 204
    inputs_repo.get_by_telegram_message.assert_not_awaited()
    record_answer_spy.assert_not_awaited()


def test_reply_rejected_when_allowlist_has_no_valid_ids(record_answer_spy):
    """An allowlist of usernames parses to no ids and must admit nobody, never everybody."""
    config = MagicMock(
        telegram_bot_token="",
        telegram_chat_id=CHAT_ID,
        telegram_sender_allowlist=TelegramSenderAllowlist(ids=frozenset(), invalid=("@operator",)),
    )
    app, inputs_repo = _answering_app(config=config)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(sender_id=42),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 204
    inputs_repo.get_by_telegram_message.assert_not_awaited()
    record_answer_spy.assert_not_awaited()


def test_non_dict_callback_query_after_gate_is_ignored_not_500(record_answer_spy):
    """The gate coerces untrusted members; the handlers after it must too."""
    app, inputs_repo = _answering_app()
    client = TestClient(app)
    update = _reply_update()
    update["callback_query"] = "pwn"
    update["message"].pop("reply_to_message", None)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    inputs_repo.get_by_telegram_message.assert_not_awaited()


def test_non_dict_reply_to_after_gate_is_ignored_not_500(record_answer_spy):
    app, inputs_repo = _answering_app()
    client = TestClient(app)
    update = _reply_update()
    update["message"]["reply_to_message"] = "pwn"
    resp = client.post(
        "/api/v1/telegram/callback",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    inputs_repo.get_by_telegram_message.assert_not_awaited()


def test_reply_from_allowlisted_sender_is_answered(record_answer_spy):
    config = MagicMock(
        telegram_bot_token="",
        telegram_chat_id=CHAT_ID,
        telegram_sender_allowlist=TelegramSenderAllowlist(ids=frozenset({111, 222}), invalid=()),
    )
    app, _ = _answering_app(config=config)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(sender_id=222),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    record_answer_spy.assert_awaited_once()


def test_reply_from_configured_chat_is_answered(record_answer_spy):
    """The happy path still records the answer and resumes when unblocked."""
    app, inputs_repo = _answering_app()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "answered", "input_id": "inp-1"}
    record_answer_spy.assert_awaited_once()
    assert record_answer_spy.await_args.kwargs["answer"] == "the answer"
    assert record_answer_spy.await_args.kwargs["answered_by"] == "telegram:operator"
    app.state.issue_executor.resume_for_issue.assert_awaited_once_with("iss-1")


def test_reply_chat_id_compared_as_string(record_answer_spy):
    """Telegram sends chat.id as an int; the configured value is a string."""
    app, _ = _answering_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(chat_id=int(CHAT_ID)),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    record_answer_spy.assert_awaited_once()


def test_already_answered_still_short_circuits(record_answer_spy):
    app, inputs_repo = _answering_app()
    inputs_repo.get_by_telegram_message = AsyncMock(
        return_value={"id": "inp-1", "issue_id": "iss-1", "status": "answered"}
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_answered"
    record_answer_spy.assert_not_awaited()


def test_callback_query_from_foreign_chat_is_rejected():
    """Inline-button acks also cost an outbound Telegram call — gate them too."""
    app, _ = _answering_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": 42},
                "message": {"message_id": 5, "chat": {"id": "999888777"}},
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 204


def test_a_reply_another_answer_beat_to_the_question_is_already_answered(record_answer_spy):
    """#499: the reply read the question pending, but another answer was written first: nothing of it goes on."""
    app, inputs_repo = _answering_app()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)
    record_answer_spy.return_value = False
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json=_reply_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "already_answered"}, f"a reply that lost the question was taken: {resp.json()}"
    app.state.issue_executor.resume_for_issue.assert_not_awaited()
