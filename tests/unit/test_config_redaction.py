"""#535: every renderer of the settings model masks every secret.

Before #535, ``embry0 config`` masked a hand-kept list of seven names, and
every credential added after that list printed in clear text. These tests pin
the rule that replaced it (``embry0/config_redaction.py``) through each
renderer, and the guard that stops a new secret-looking setting from going
unclassified.

Every value here is synthetic. None is a real credential.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from unittest.mock import patch

import pytest
import structlog
from pydantic import ValidationError

import embry0
from embry0.cli.__main__ import cmd_config
from embry0.config import Embry0Config
from embry0.config_redaction import (
    MASK,
    NOT_SECRET,
    SecretSetting,
    mask_secret,
    mask_url_credentials,
    redacted_settings,
    secret_setting_names,
    unclassified_secret_looking_settings,
)
from embry0.storage.database import DatabasePool

# The issue's list: the seven the CLI already masked, and the twelve that
# printed in clear text. Pinned by name, so the tests below cannot pass by
# deriving an empty set from a broken rule.
ISSUE_535_SECRETS = frozenset(
    {
        # masked before #535
        "anthropic_api_key",
        "api_key",
        "claude_code_oauth_token",
        "github_token",
        "github_webhook_secret",
        "slack_webhook_url",
        "telegram_bot_token",
        # printed in clear text before #535
        "alertmanager_webhook_secret",
        "auth0_client_secret",
        "environment_secret_key",
        "environment_secret_keys_old",
        "gitlab_token",
        "gitlab_webhook_secret",
        "linear_api_key",
        "linear_webhook_secret",
        "minio_root_password",
        "proxy_admin_token",
        "session_secret",
        "webhook_notify_secret",
    }
)

FAKE_DB_URL = "postgresql://app:FAKE-db-password@db.example.com:5432/app"
MASKED_DB_URL = "postgresql://****@db.example.com:5432/app"


def _fake(name: str) -> str:
    """Long enough that the mask keeps its four-and-four, so a test also
    proves the middle of the value never shows."""
    return f"FAKE-{name}-0123456789abcdef"


def _all_secret_names() -> list[str]:
    """Every string secret, by the rule and by the issue's list. A string
    only: the fixture below sets each one to a fake text value."""
    names = secret_setting_names(Embry0Config) | ISSUE_535_SECRETS
    return sorted(n for n in names if Embry0Config.model_fields[n].annotation is str)


def _leaks(text: str) -> list[str]:
    """Secret names whose fake value, or the hidden middle of it, is in ``text``."""
    leaked = []
    for name in _all_secret_names():
        value = _fake(name)
        if value in text or value[4:-4] in text:
            leaked.append(name)
    return leaked


@pytest.fixture
def secret_env(tmp_path, monkeypatch) -> Iterator[dict[str, str]]:
    """A process env that sets every secret (and the DB URL) to a fake value,
    read from an empty directory so no ``.env`` file joins in."""
    env = {name.upper(): _fake(name) for name in _all_secret_names()}
    env["DATABASE_URL"] = FAKE_DB_URL
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, env, clear=True):
        yield env


def _config() -> Embry0Config:
    return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _run_cmd_config(capsys) -> tuple[str, str]:
    with pytest.raises(SystemExit):
        cmd_config(argparse.Namespace())
    captured = capsys.readouterr()
    return captured.out, captured.err


# ---- the classification ----------------------------------------------------


def test_every_secret_named_in_the_issue_is_secret():
    missing = sorted(ISSUE_535_SECRETS - secret_setting_names(Embry0Config))
    assert not missing, f"#535: these settings are credentials but would print in clear text: {missing}"


def test_every_secret_looking_setting_is_classified():
    """The guard. A new field whose name looks secret is masked anyway, but
    the reader of config.py must be able to see that it is a credential."""
    unclassified = unclassified_secret_looking_settings(Embry0Config)
    assert not unclassified, (
        f"{unclassified}: the name looks secret, so type it `SecretSetting` in embry0/config.py, "
        "or `Annotated[<type>, NOT_SECRET]` if it only looks secret"
    )


def test_guard_fires_on_a_new_unclassified_secret_looking_field():
    class _Probe(Embry0Config):
        widget_token: str = ""

    added = set(unclassified_secret_looking_settings(_Probe)) - set(unclassified_secret_looking_settings(Embry0Config))
    assert added == {"widget_token"}, "a new secret-looking field slipped past the classification guard"
    assert "widget_token" in secret_setting_names(_Probe), "an unclassified secret-looking field must still be masked"


def test_a_field_marked_not_secret_is_classified_and_shown():
    class _Probe(Embry0Config):
        widget_key_count: Annotated[int, NOT_SECRET] = 3

    assert "widget_key_count" not in unclassified_secret_looking_settings(_Probe)
    assert "widget_key_count" not in secret_setting_names(_Probe)
    with patch.dict(os.environ, {}, clear=True):
        shown = dict(redacted_settings(_Probe(_env_file=None)))  # type: ignore[call-arg]
    assert shown["widget_key_count"] == "3"


def test_a_secret_whose_name_does_not_look_secret_is_masked_by_its_type():
    class _Probe(Embry0Config):
        widget_capability_url: SecretSetting = ""

    assert "widget_capability_url" in secret_setting_names(_Probe)


# ---- every renderer --------------------------------------------------------


def test_embry0_config_masks_every_secret(secret_env, capsys):
    out, _ = _run_cmd_config(capsys)
    shown = dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith(" "))
    assert not _leaks(out), f"embry0 config printed these secrets: {_leaks(out)}"
    for name in _all_secret_names():
        assert shown.get(name) == mask_secret(_fake(name)), f"{name} is not shown masked: {shown.get(name)!r}"
    assert len(_all_secret_names()) >= len(ISSUE_535_SECRETS)


def test_embry0_config_masks_the_database_url_userinfo(secret_env, capsys):
    out, _ = _run_cmd_config(capsys)
    assert f"database_url={MASKED_DB_URL}" in out.splitlines(), out
    assert "FAKE-db-password" not in out


def test_repr_and_str_mask_every_secret(secret_env):
    config = _config()
    for rendered in (repr(config), str(config)):
        assert not _leaks(rendered), f"repr/str shows these secrets: {_leaks(rendered)}"
        assert "FAKE-db-password" not in rendered
        assert MASKED_DB_URL in rendered


def test_repr_still_shows_non_secret_values_with_their_types(secret_env):
    rendered = repr(_config())
    assert "max_budget_usd=10.0" in rendered
    assert "watcher_enabled=False" in rendered


@pytest.mark.parametrize(
    "renderer",
    [structlog.processors.JSONRenderer(), structlog.dev.ConsoleRenderer(colors=False)],
    ids=["json", "console"],
)
def test_structlog_masks_a_logged_config_object(secret_env, renderer):
    rendered = str(renderer(None, "info", {"event": "config_loaded", "config": _config()}))
    assert not _leaks(rendered), f"a logged config object shows these secrets: {_leaks(rendered)}"
    assert "FAKE-db-password" not in rendered


@pytest.mark.parametrize(
    "value",
    ["http://tok-000000000007@gitlab.example.com", "http://oauth2:tok-000000000007@gitlab.example.com"],
    ids=["bare-user-token", "password"],
)
def test_a_config_that_does_not_load_never_repeats_the_value_wherever_it_is_printed(value):
    """Every loader of the config (the API's startup, a CLI, a test) may print ``str(exc)``, into a
    traceback and the container logs: pydantic must not attach the refused value to it."""
    with patch.dict(os.environ, {"GITLAB_URL": value}, clear=True), pytest.raises(ValidationError) as refused:
        Embry0Config(_env_file=None)  # type: ignore[call-arg]
    text = str(refused.value)
    assert "tok-000000000007" not in text, f"the config error repeats the token the value carries: {text}"
    assert "input_value" not in text, text
    assert "GITLAB_URL must be an https:// URL" in text, text


@pytest.mark.parametrize(
    "value",
    ["http://tok-000000000007@gitlab.example.com", "http://reader:tok-000000000007@gitlab.example.com"],
    ids=["bare-user-token", "password"],
)
def test_a_config_load_error_never_shows_a_credential_the_value_carries(tmp_path, monkeypatch, capsys, value):
    """pydantic repeats the refused value in str(exc) (its input_value), and a URL can carry a token as
    the bare user, which no URL mask can tell from a user name. The error names the variable and why."""
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {"GITLAB_URL": value}, clear=True):
        _, err = _run_cmd_config(capsys)
    assert "tok-000000000007" not in err, f"embry0 config printed the token the refused URL carries: {err}"
    assert "input_value" not in err, err
    assert "Error loading configuration" in err, err
    assert "\n  GITLAB_URL must be an https:// URL" in err, f"one line per variable, named once: {err}"


# ---- the masks ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("", ""),
        ("short-secret", MASK),
        ("x" * 23, MASK),
        ("abcd" + "x" * 16 + "wxyz", f"abcd{MASK}wxyz"),
    ],
    ids=["unset", "short", "just-below-reveal", "long"],
)
def test_mask_secret(value, shown):
    assert mask_secret(value) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (FAKE_DB_URL, MASKED_DB_URL),
        # the whole userinfo goes: a token is often written as the bare user
        ("https://tok-000000000007@gitlab.example.com", "https://****@gitlab.example.com"),
        ("https://oauth2:tok-000000000007@gitlab.example.com/g/p.git", "https://****@gitlab.example.com/g/p.git"),
        ("postgresql://app@db.example.com:5432/app", "postgresql://****@db.example.com:5432/app"),
        ("https://loki.example.com:3100/loki", "https://loki.example.com:3100/loki"),
        ("redis://:FAKE-redis-pw@cache:6379/0", "redis://****@cache:6379/0"),
        ("https://reader:FAKE-pw@loki.example.com", "https://****@loki.example.com"),
        # urlparse reads up to the LAST '@' of the authority as the userinfo
        ("postgresql://app:FAKE@pw@db:5432/app", "postgresql://****@db:5432/app"),
        # a raw '/' (base64): the URL is broken, and that is when it gets printed
        ("postgresql://app:FAKE/pw+x=@db:5432/app", "postgresql://****@db:5432/app"),
        ("postgresql://app:FAKE%2Fpw@db:5432/app", "postgresql://****@db:5432/app"),
        # a port is not a password, even with an '@' later in the URL
        ("https://hooks.example.com:8443/notify?to=ops@example.com", None),
        ("postgresql://app:FAKE-pw@[::1]:5432/app", "postgresql://****@[::1]:5432/app"),
        # masking twice changes nothing
        ("postgresql://****@db:5432/app", None),
        (
            "postgresql://app@db/app?password=FAKE-pw&sslmode=require",
            "postgresql://****@db/app?password=****&sslmode=require",
        ),
        (
            "https://hooks.example.com/run?api-version=1&sig=FAKEsig",
            "https://hooks.example.com/run?api-version=1&sig=****",
        ),
        ("https://hooks.example.com/run?apikey=FAKE&x=1", "https://hooks.example.com/run?apikey=****&x=1"),
        (
            "primary postgresql://a:FAKE-1@h1/d, replica postgresql://b:FAKE-2@h2/d",
            "primary postgresql://****@h1/d, replica postgresql://****@h2/d",
        ),
        ("not a url at all", None),
        ('{app="web"} |= "error"', None),
    ],
)
def test_mask_url_credentials(text, shown):
    assert mask_url_credentials(text) == (text if shown is None else shown)


# ---- other renderers of a setting ------------------------------------------


@pytest.mark.parametrize(
    ("url", "logged"),
    [
        (FAKE_DB_URL, MASKED_DB_URL),
        ("postgresql://app:FAKE-db-password@db/app", "postgresql://****@db/app"),
        ("postgresql://app@db/app", "postgresql://****@db/app"),
        ("host=db user=app password=FAKE-db-password", "***"),
    ],
    ids=["with-port", "no-port", "no-password", "keyword-dsn"],
)
def test_database_pool_logs_the_url_with_its_userinfo_masked(url, logged):
    assert DatabasePool(url)._redacted_url == logged


def test_integration_settings_audit_masks_every_secret_looking_field():
    """PUT /config/integrations audits its updates through this set; a new
    secret-looking updatable field outside it would land in the audit log."""
    from embry0.config_redaction import SECRET_NAME_PATTERN
    from embry0.storage.repositories.integration_config import _SECRET_FIELDS, _UPDATABLE_FIELDS

    unmasked = sorted(f for f in _UPDATABLE_FIELDS if SECRET_NAME_PATTERN.search(f) and f not in _SECRET_FIELDS)
    assert not unmasked, f"integration settings audited in clear text: {unmasked}"


# ---- log and print calls ---------------------------------------------------

_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "critical", "exception", "fatal", "msg"})
_PRINTERS = frozenset({"print", "_info", "_warn", "_err", "HTTPException"})
_MASKERS = frozenset({"mask_url_credentials", "mask_secret", "redact_setting", "bool", "len"})
_URL_LIKE_NAME = re.compile(r"url|origin|issuer|host")


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _raw_attributes(node: ast.AST) -> Iterator[str]:
    """Attribute names read in ``node``, skipping what a masker wraps."""
    if isinstance(node, ast.Call) and _call_name(node) in _MASKERS:
        return
    if isinstance(node, ast.Attribute):
        yield node.attr
    for child in ast.iter_child_nodes(node):
        yield from _raw_attributes(child)


def _settings_logged_raw() -> list[str]:
    url_like = {
        name
        for name, field in Embry0Config.model_fields.items()
        if field.annotation is str and _URL_LIKE_NAME.search(name)
    }
    watched = secret_setting_names(Embry0Config) | url_like
    found = []
    root = Path(embry0.__file__).parent
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            is_log = isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS
            is_print = isinstance(call.func, ast.Name) and call.func.id in _PRINTERS
            if not (is_log or is_print):
                continue
            for arg in [*call.args, *(kw.value for kw in call.keywords)]:
                found += [
                    f"{path.relative_to(root.parent)}:{call.lineno} {attr}"
                    for attr in _raw_attributes(arg)
                    if attr in watched
                ]
    return found


def test_no_log_or_print_call_passes_a_secret_or_url_setting_raw():
    """Source-level guard over every log and print call in embry0/: a secret
    setting, or a URL setting that could carry a password, goes through
    mask_url_credentials / mask_secret first. A ``getattr`` read is not seen."""
    raw = _settings_logged_raw()
    assert not raw, f"settings logged or printed unmasked: {raw}"


def test_the_log_guard_sees_a_raw_url_setting():
    tree = ast.parse("logger.info('e', url=self._config.watcher_loki_url)")
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert list(_raw_attributes(call.keywords[0].value)) == ["watcher_loki_url", "_config"]
    wrapped = ast.parse("mask_url_credentials(self._config.watcher_loki_url)", mode="eval").body
    assert list(_raw_attributes(wrapped)) == []
