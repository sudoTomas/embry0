from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.integration_config import IntegrationConfigRepository, _mask

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def integration_repo(db_with_migrations: DatabasePool) -> IntegrationConfigRepository:
    """On the shared database fixture: no row an earlier test left, so the env fallback is what each test reads."""
    return IntegrationConfigRepository(db_with_migrations)


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
    assert config["trigger_labels"] == ["embry0"]
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
    config = await integration_repo.update(trigger_labels=["embry0", "bot"])
    assert config["trigger_labels"] == ["embry0", "bot"]


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


# --- Env-derived defaults: empty DB falls through to env ---


@pytest.mark.asyncio
async def test_get_reads_trigger_labels_from_env_when_no_row(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", "embry0,bot,agent")
    config = await integration_repo.get()
    assert config["trigger_labels"] == ["embry0", "bot", "agent"]


@pytest.mark.asyncio
async def test_get_reflects_env_secret_presence_when_no_row(integration_repo: IntegrationConfigRepository, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "abc123def456")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/X/Y/abcdefgh")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:ABCDEFGHijklmnopqrstuvwxyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")

    config = await integration_repo.get()
    assert config["webhook_secret_set"] is True
    assert config["slack_webhook_url_set"] is True
    assert config["slack_webhook_url_masked"] == "...efgh"
    assert config["telegram_bot_token_set"] is True
    assert config["telegram_bot_token_masked"] == "...wxyz"
    assert config["telegram_chat_id"] == "-1001234567890"
    # Raw secrets must never appear
    assert "webhook_secret" not in config
    assert "slack_webhook_url" not in config
    assert "telegram_bot_token" not in config


@pytest.mark.asyncio
async def test_db_row_overrides_env(integration_repo: IntegrationConfigRepository, monkeypatch):
    monkeypatch.setenv("TRIGGER_LABELS", "EnvLabel")
    await integration_repo.update(trigger_labels=["DbLabel"])
    config = await integration_repo.get()
    # DB wins
    assert config["trigger_labels"] == ["DbLabel"]


@pytest.mark.asyncio
async def test_get_telegram_credentials_returns_raw_values():
    """RAV-657 hotfix: internal senders need RAW creds — get() masks them."""
    from unittest.mock import AsyncMock, MagicMock

    from embry0.storage.repositories.integration_config import IntegrationConfigRepository

    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"telegram_bot_token": "123:raw-token", "telegram_chat_id": "42"})
    repo = IntegrationConfigRepository(db)
    assert await repo.get_telegram_credentials() == ("123:raw-token", "42")


@pytest.mark.asyncio
async def test_get_telegram_credentials_falls_back_to_env(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from embry0.storage.repositories.integration_config import IntegrationConfigRepository

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    repo = IntegrationConfigRepository(db)
    assert await repo.get_telegram_credentials() == ("env-token", "env-chat")


# --- get_trigger_labels: the single source of truth (#157) ---


@pytest.mark.asyncio
async def test_get_trigger_labels_falls_back_to_env_with_no_row(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", "embry0, ship-it")
    assert await integration_repo.get_trigger_labels() == ["embry0", "ship-it"]


@pytest.mark.asyncio
async def test_get_trigger_labels_falls_back_to_hardcoded_without_env(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.delenv("TRIGGER_LABELS", raising=False)
    assert await integration_repo.get_trigger_labels() == ["embry0"]


@pytest.mark.asyncio
async def test_get_trigger_labels_prefers_the_db_row(integration_repo: IntegrationConfigRepository, monkeypatch):
    """Editing the field in Settings must actually take effect."""
    monkeypatch.setenv("TRIGGER_LABELS", "from-env")
    await integration_repo.update(trigger_labels=["from-db"])
    assert await integration_repo.get_trigger_labels() == ["from-db"]


@pytest.mark.asyncio
async def test_get_trigger_labels_ignores_an_empty_row_value(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    """An empty set would silently disable every trigger — fall back instead."""
    monkeypatch.setenv("TRIGGER_LABELS", "from-env")
    await integration_repo.update(trigger_labels=[])
    assert await integration_repo.get_trigger_labels() == ["from-env"]


@pytest.mark.asyncio
async def test_saving_an_unrelated_field_preserves_env_trigger_labels(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    """Row creation must seed trigger_labels from env, not the column DEFAULT.

    The row appears the first time *any* Settings field is saved, and from then
    on it wins. Letting the hardcoded '["embry0"]' default apply would silently
    drop the operator's TRIGGER_LABELS the moment they set a Telegram chat id.
    """
    monkeypatch.setenv("TRIGGER_LABELS", "ship-it")
    await integration_repo.update(telegram_chat_id="12345")
    assert await integration_repo.get_trigger_labels() == ["ship-it"]


# --- Linear credentials: DB-manageable, env fallback (#157) ---


@pytest.mark.asyncio
async def test_get_linear_credentials_falls_back_to_env(integration_repo: IntegrationConfigRepository, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "env-key")
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "env-secret")
    monkeypatch.setenv("LINEAR_REPO_MAP", '{"Proj": "o/r"}')
    assert await integration_repo.get_linear_credentials() == ("env-key", "env-secret", '{"Proj": "o/r"}')


@pytest.mark.asyncio
async def test_get_linear_credentials_prefers_the_db_row(integration_repo: IntegrationConfigRepository, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "env-key")
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "env-secret")
    await integration_repo.update(linear_api_key="db-key", linear_webhook_secret="db-secret")
    api_key, secret, _ = await integration_repo.get_linear_credentials()
    assert (api_key, secret) == ("db-key", "db-secret")


@pytest.mark.asyncio
async def test_get_linear_credentials_falls_back_per_field(integration_repo: IntegrationConfigRepository, monkeypatch):
    """A row that sets only the key still gets the secret from env."""
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "env-secret")
    await integration_repo.update(linear_api_key="db-key")
    api_key, secret, _ = await integration_repo.get_linear_credentials()
    assert (api_key, secret) == ("db-key", "env-secret")


@pytest.mark.asyncio
async def test_get_never_returns_raw_linear_secrets(integration_repo: IntegrationConfigRepository, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    await integration_repo.update(linear_api_key="lin_api_supersecret", linear_webhook_secret="whsec_abcdef")
    config = await integration_repo.get()

    assert config["linear_api_key_set"] is True
    assert config["linear_api_key_masked"] == "...cret"
    assert config["linear_webhook_secret_set"] is True
    assert "lin_api_supersecret" not in str(config)
    assert "whsec_abcdef" not in str(config)


@pytest.mark.asyncio
async def test_blank_linear_secret_update_is_ignored(integration_repo: IntegrationConfigRepository, monkeypatch):
    """Saving the form without retyping a secret must not wipe it."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    await integration_repo.update(linear_api_key="keep-me")
    await integration_repo.update(linear_api_key="", linear_repo_map='{"P": "o/r"}')
    api_key, _, repo_map = await integration_repo.get_linear_credentials()
    assert api_key == "keep-me"
    assert repo_map == '{"P": "o/r"}'


# --- resolve_trigger_labels / log_effective_trigger_labels: label + source (#270) ---


@pytest.mark.asyncio
async def test_resolve_trigger_labels_names_the_db_as_source(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", "Athanor")
    await integration_repo.update(trigger_labels=["embry0"])
    resolved = await integration_repo.resolve_trigger_labels()
    assert (resolved.labels, resolved.source) == (["embry0"], "db")


@pytest.mark.asyncio
async def test_resolve_trigger_labels_names_the_env_as_source(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", "ship-it")
    resolved = await integration_repo.resolve_trigger_labels()
    assert (resolved.labels, resolved.source) == (["ship-it"], "env")


@pytest.mark.asyncio
async def test_resolve_trigger_labels_names_the_code_default_as_source(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.delenv("TRIGGER_LABELS", raising=False)
    resolved = await integration_repo.resolve_trigger_labels()
    assert (resolved.labels, resolved.source) == (["embry0"], "default")


@pytest.mark.asyncio
async def test_startup_log_states_effective_label_and_source(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", "embry0")
    await integration_repo.update(trigger_labels=["embry0"])
    with structlog.testing.capture_logs() as logs:
        await integration_repo.log_effective_trigger_labels()
    assert logs == [{"event": "trigger_labels_effective", "log_level": "info", "labels": ["embry0"], "source": "db"}]


@pytest.mark.asyncio
async def test_startup_log_warns_when_env_is_shadowed_by_a_different_db_value(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    """The live drift: .env said Athanor, the DB said embry0, the DB won, and
    an operator 'fixing' Linear to match .env would have stopped dispatch."""
    monkeypatch.setenv("TRIGGER_LABELS", "Athanor")
    await integration_repo.update(trigger_labels=["embry0"])
    with structlog.testing.capture_logs() as logs:
        await integration_repo.log_effective_trigger_labels()
    assert [e["event"] for e in logs] == ["trigger_labels_effective", "trigger_labels_env_shadowed"]
    warning = logs[1]
    assert warning["log_level"] == "warning"
    assert warning["env_labels"] == ["Athanor"]
    assert warning["effective_labels"] == ["embry0"]


@pytest.mark.asyncio
async def test_startup_log_survives_a_db_failure(monkeypatch):
    db = MagicMock()
    db.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    with structlog.testing.capture_logs() as logs:
        await IntegrationConfigRepository(db).log_effective_trigger_labels()
    assert [e["event"] for e in logs] == ["trigger_labels_resolve_failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [",", " , ,", ",,"])
async def test_env_that_parses_to_no_labels_falls_through_to_the_default(
    integration_repo: IntegrationConfigRepository, monkeypatch, raw: str
):
    """An empty label set silently disables every trigger; it must never be
    presented as the authoritative env value."""
    monkeypatch.setenv("TRIGGER_LABELS", raw)
    resolved = await integration_repo.resolve_trigger_labels()
    assert (resolved.labels, resolved.source) == (["embry0"], "default")
    assert await integration_repo.get_trigger_labels() == ["embry0"]
    assert (await integration_repo.get())["trigger_labels"] == ["embry0"]


@pytest.mark.asyncio
async def test_startup_log_warns_about_an_env_value_with_no_labels(
    integration_repo: IntegrationConfigRepository, monkeypatch
):
    monkeypatch.setenv("TRIGGER_LABELS", ",")
    with structlog.testing.capture_logs() as logs:
        await integration_repo.log_effective_trigger_labels()
    assert [e["event"] for e in logs] == ["trigger_labels_effective", "trigger_labels_env_empty"]
    assert logs[0]["source"] == "default"
    assert logs[1]["log_level"] == "warning"
    assert logs[1]["env_value"] == ","
