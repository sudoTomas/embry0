"""Only plain variable names reach a sandbox's environment.

The environment API takes a repository variable only under a plain name
(letters, digits and underscores, not starting with a digit). A sandbox
profile's ``env_defaults`` follow the same rule, through the one shared check
(``env_override_refusal``). A profile stored before the rule, or written
around the API, still starts its sandbox, without those keys and with a log
line naming each one; so does a repository row. Neither the refusal nor the
log line shows a value: not the variable's, and not the part of a key after
its first ``=``, which a container would read as one.

The API, storage and seed sides are in tests/unit/api/test_profile_env_default_names.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.sandbox_manager import SandboxManager
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.workflows.issue_to_pr.nodes._common import _filter_user_env_for_sandbox

# Autouse: every sandbox on sandbox-restricted gets the isolation chain, which
# the docker mock cannot write.
from tests.unit.execution.test_sandbox_manager import _stub_isolation_unless_overridden  # noqa: F401

#: What follows the first "=" of the key below: never shown.
AFTER_EQUALS = "after-equals"

NOT_VARIABLE_NAMES = [
    "NAME%%",
    "NAME()",
    "1LANG",
    "LANG-NAME",
    "MY VAR",
    f"NAME={AFTER_EQUALS}",
    " LANG",
    "LANG\n",
    "my.var",
    "CAFÉ",
    "",
]

VARIABLE_NAMES = ["LANG", "lang", "_PRIVATE", "A1", "TZ", "npm_config_cache"]

# A value no infrastructure default carries, so a key that reached the env, or a value a log line shows, is
# unambiguous.
_MARK = "set-by-profile-default"


def _shown(key: str) -> str:
    """What a refusal or a log line names ``key`` as: up to its first "="."""
    return key.partition("=")[0]


@pytest.mark.parametrize("key", NOT_VARIABLE_NAMES)
def test_the_shared_check_refuses_a_key_that_is_not_a_variable_name(key: str) -> None:
    refusal = env_override_refusal(key, _MARK)
    assert refusal is not None, f"{key!r} is not a variable name, yet the shared env check passes it"
    assert repr(_shown(key)) in refusal, f"the refusal does not name {key!r}: {refusal}"
    assert _MARK not in refusal, f"the refusal shows the value: {refusal}"


def test_a_key_with_an_equals_sign_is_named_up_to_it() -> None:
    refusal = env_override_refusal(f"NAME={AFTER_EQUALS}", "1") or ""
    assert AFTER_EQUALS not in refusal, f"the refusal shows what follows the key's '=': {refusal}"
    assert "'NAME' (cut at its first '=')" in refusal, f"the refusal does not say the key was cut: {refusal}"


@pytest.mark.parametrize("key", VARIABLE_NAMES)
def test_the_shared_check_still_passes_a_plain_name(key: str) -> None:
    assert env_override_refusal(key, "1") is None


# --- a profile's env_defaults at sandbox create ------------------------------


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> SandboxManager:
    from embry0.execution.runtime import egress as egress_mod

    enforcer = MagicMock()
    enforcer.container_ip = AsyncMock(return_value="172.20.0.9")
    enforcer.ensure_helper_image = AsyncMock()
    enforcer.apply = AsyncMock()
    enforcer.clear = AsyncMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(return_value="tok-abc123")
    proxy_mgr.unenroll_sandbox = AsyncMock()
    return SandboxManager(docker=docker, proxy_manager=proxy_mgr)


async def _created_env(manager: SandboxManager, profile: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    with capture_logs() as logs:
        await manager.create(job_id="job-env-names", profile=profile)
    return manager._docker.build_run_cmd.call_args.kwargs.get("env", {}) or {}, logs


def _drop_lines(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


def _named(lines: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return sorted((entry["key"], entry["key_cut_at_equals"]) for entry in lines)


def _expected(keys: list[str]) -> list[tuple[str, bool]]:
    return sorted((_shown(key), "=" in key) for key in keys)


async def test_a_profile_default_that_is_not_a_variable_name_is_left_out(manager: SandboxManager) -> None:
    env_defaults = {**{key: _MARK for key in NOT_VARIABLE_NAMES}, "LANG": "C.UTF-8"}
    env, logs = await _created_env(manager, {"env_defaults": env_defaults})

    reached = sorted(key for key, value in env.items() if value == _MARK)
    assert reached == [], f"profile env_defaults that are not variable names reached the sandbox env: {reached}"
    assert env.get("LANG") == "C.UTF-8", "a plain profile default was dropped along with the others"
    lines = _drop_lines(logs, "profile_env_default_invalid_name_dropped")
    assert _named(lines) == _expected(NOT_VARIABLE_NAMES), (
        f"each key left out must be named in its own log line: {logs}"
    )
    assert _MARK not in repr(logs), f"a log line shows the value: {logs}"
    assert AFTER_EQUALS not in repr(logs), f"a log line shows what follows a key's '=': {logs}"


async def test_plain_profile_defaults_still_reach_the_sandbox(manager: SandboxManager) -> None:
    env, logs = await _created_env(manager, {"env_defaults": dict.fromkeys(VARIABLE_NAMES, _MARK)})

    assert {key for key, value in env.items() if value == _MARK} == set(VARIABLE_NAMES)
    assert _drop_lines(logs, "profile_env_default_invalid_name_dropped") == []


@pytest.mark.requires_postgres
async def test_a_profile_stored_before_the_check_starts_without_the_key(
    db_with_migrations: DatabasePool, manager: SandboxManager
) -> None:
    # Written straight to the table, as a row stored before the check would be.
    await db_with_migrations.execute(
        "INSERT INTO sandbox_profiles (name, env_defaults) VALUES ($1, $2)",
        "stored-before",
        {"NAME()": _MARK, "LANG": "C.UTF-8"},
    )
    stored = await SandboxProfilesRepository(db_with_migrations).get("stored-before")
    assert stored is not None and "NAME()" in stored["env_defaults"], f"the row was not stored: {stored}"

    env, logs = await _created_env(manager, stored)

    assert "NAME()" not in env, "a stored profile default that is not a variable name reached the sandbox env"
    assert env.get("LANG") == "C.UTF-8"
    assert _named(_drop_lines(logs, "profile_env_default_invalid_name_dropped")) == [("NAME()", False)]


# --- a repository's rows at sandbox create -----------------------------------


def test_a_repository_row_that_is_not_a_variable_name_is_left_out() -> None:
    rows = [{"key": key, "value": _MARK, "var_type": "config", "scope": "app"} for key in NOT_VARIABLE_NAMES]
    rows.append({"key": "LANG", "value": "C.UTF-8", "var_type": "config", "scope": "app"})

    with capture_logs() as logs:
        env = _filter_user_env_for_sandbox(rows, qa_active=False)

    assert env == {"LANG": "C.UTF-8"}, f"repository rows that are not variable names reached the sandbox env: {env}"
    lines = _drop_lines(logs, "user_env_invalid_name_dropped")
    assert _named(lines) == _expected(NOT_VARIABLE_NAMES), (
        f"each row left out must be named in its own log line: {logs}"
    )
    assert _MARK not in repr(logs), f"a log line shows the value: {logs}"
    assert AFTER_EQUALS not in repr(logs), f"a log line shows what follows a key's '=': {logs}"
