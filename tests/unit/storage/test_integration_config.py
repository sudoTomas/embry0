import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.integration_config import IntegrationConfigRepository, _mask


@pytest.fixture
async def integration_repo(pg_pool: asyncpg.Pool) -> IntegrationConfigRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield IntegrationConfigRepository(db)
    await db.close()


# --- _mask unit tests (pure, no DB needed) ---


def test_mask_empty_string():
    assert _mask("") == ""


def test_mask_short_string_at_or_below_visible():
    assert _mask("abc", visible=4) == "***"
    assert _mask("abcd", visible=4) == "****"


def test_mask_long_string_shows_suffix():
    assert _mask("supersecrettoken", visible=4) == "...oken"
    assert _mask("abcdefgh", visible=4) == "...efgh"


def test_mask_exactly_visible_plus_one():
    assert _mask("abcde", visible=4) == "...bcde"


# --- Repository integration tests ---


@pytest.mark.asyncio
async def test_get_returns_defaults(integration_repo: IntegrationConfigRepository):
    config = await integration_repo.get()
    assert config["trigger_labels"] == ["Legion"]
    assert config["webhook_secret_set"] is False
    assert config["slack_webhook_url_set"] is False
    assert config["telegram_bot_token_set"] is False
    assert config["telegram_chat_id"] == ""
    # Raw secrets must never appear
    assert "webhook_secret" not in config
    assert "slack_webhook_url" not in config
    assert "telegram_bot_token" not in config


@pytest.mark.asyncio
async def test_update_trigger_labels(integration_repo: IntegrationConfigRepository):
    config = await integration_repo.update(trigger_labels=["Legion", "bot"])
    assert config["trigger_labels"] == ["Legion", "bot"]


@pytest.mark.asyncio
async def test_update_webhook_secret_sets_flag(integration_repo: IntegrationConfigRepository):
    config = await integration_repo.update(webhook_secret="mysecretvalue")
    assert config["webhook_secret_set"] is True
    # Raw secret must still not appear
    assert "webhook_secret" not in config


@pytest.mark.asyncio
async def test_slack_url_gets_masked_on_read(integration_repo: IntegrationConfigRepository):
    url = "https://hooks.slack.com/services/TXXXX/BXXXX/abcdefgh"
    config = await integration_repo.update(slack_webhook_url=url)
    assert config["slack_webhook_url_set"] is True
    assert config["slack_webhook_url_masked"] == "...efgh"
    assert "slack_webhook_url" not in config


@pytest.mark.asyncio
async def test_telegram_token_gets_masked_on_read(integration_repo: IntegrationConfigRepository):
    token = "1234567890:ABCDEFGHijklmnopqrstuvwxyz"
    config = await integration_repo.update(telegram_bot_token=token)
    assert config["telegram_bot_token_set"] is True
    assert config["telegram_bot_token_masked"] == "...wxyz"
    assert "telegram_bot_token" not in config


@pytest.mark.asyncio
async def test_telegram_chat_id_returned_as_plain(integration_repo: IntegrationConfigRepository):
    config = await integration_repo.update(telegram_chat_id="-1001234567890")
    assert config["telegram_chat_id"] == "-1001234567890"
