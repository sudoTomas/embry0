"""The variables embry0 no longer reads: startup and ``embry0 config`` name each one still set (#168)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from embry0.cli.__main__ import cmd_config
from embry0.config import Embry0Config
from embry0.settings.env import env_name
from tests.fixtures.settings_sections import TEST_CATALOG


def test_a_variable_name_is_the_attribute_in_capitals() -> None:
    assert env_name("api_rate_limit_per_minute") == "API_RATE_LIMIT_PER_MINUTE"


#: The variables no longer read at all, with what each line says runs instead (ADR 0032).
RETIRED = {
    "LOCAL_LLM_UPSTREAM": (
        "each custom credential names its own server, reached through the local-llm-proxy (ADR 0032), and "
        "`embry0 credentials import-env` copies the two variables into the environment's (custom/api-key)"
    ),
    "LOCAL_LLM_API_KEY": (
        "each custom credential names its own server, reached through the local-llm-proxy (ADR 0032), and "
        "`embry0 credentials import-env` copies the two variables into the environment's (custom/api-key)"
    ),
}
#: Variables whose setting became a constant (#168, #665), removed before 0.1.0.
REMOVED = (
    "SCOUT_TICK_SECONDS",
    "PROPOSAL_BRIEFS_PER_TICK",
    "PROPOSAL_BRIEF_INTERVAL_SECONDS",
    "JOB_BRIEFS_PER_TICK",
    "JOB_BRIEF_INTERVAL_SECONDS",
)


@pytest.mark.parametrize("variable", sorted(RETIRED))
def test_embry0_config_names_a_variable_no_longer_read(variable: str, capsys, monkeypatch) -> None:
    """``embry0 config`` lists every setting with its value; a retired one, still set, is said to be no longer read,
    not left to look like one that runs."""
    from embry0.settings import env

    lines = getattr(env, "retired_lines", None)
    assert lines is not None, "embry0 config must name each variable that is set and no longer read"
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    with patch.dict(os.environ, {variable: "4"}, clear=True), pytest.raises(SystemExit):
        cmd_config(argparse.Namespace())
    err = capsys.readouterr().err
    assert f"{variable} is no longer read: {RETIRED[variable]}; remove it" in err, err


def test_no_retired_variable_set_says_nothing_of_them() -> None:
    from embry0.settings import env

    retired_lines = getattr(env, "retired_lines", None)
    assert retired_lines is not None, "embry0 config must name each variable that is set and no longer read"
    with patch.dict(os.environ, {}, clear=True):
        assert retired_lines(Embry0Config(_env_file=None)) == []  # type: ignore[call-arg]
    with patch.dict(os.environ, dict.fromkeys(RETIRED, "1"), clear=True):
        said = retired_lines(Embry0Config(_env_file=None))  # type: ignore[call-arg]
    assert [line.split(" ", 1)[0] for line in said] == ["LOCAL_LLM_UPSTREAM", "LOCAL_LLM_API_KEY"], (
        f"each set variable once, nothing else: {said}"
    )


@pytest.mark.parametrize("variable", REMOVED)
def test_a_variable_removed_before_0_1_0_is_named_like_every_other_no_longer_read(
    variable: str, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#741 review: an operator who still sets one hears the same at startup and from ``embry0 config`` as of every
    other variable embry0 no longer reads, and neither lint calls it a typo."""
    from embry0.config import unknown_prefixed_env_keys
    from embry0.config_lint import declared_env_names, find_unknown_env_vars
    from embry0.settings.env import warn_moved_variables

    line = f"{variable} is no longer read, removed before 0.1.0; remove it"
    with capture_logs() as logs:
        warned = warn_moved_variables({variable: _VALUE}, env_file=tmp_path / "absent.env")
    hints = [e["hint"] for e in logs if e["event"] == "settings_env_retired"]
    assert (warned, hints) == ([variable], [line]), f"startup names it once, as removed before 0.1.0: {logs}"
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    with patch.dict(os.environ, {variable: _VALUE}, clear=True), pytest.raises(SystemExit):
        cmd_config(argparse.Namespace())
    err = capsys.readouterr().err
    assert line in err, f"embry0 config names it as removed before 0.1.0: {err}"
    assert "is set but is not an embry0 setting" not in err, f"a removed variable is no typo of a setting: {err}"
    environ = {variable: _VALUE}
    assert find_unknown_env_vars(environ, declared_env_names(Embry0Config)) == [], "the near-miss lint names it"
    assert unknown_prefixed_env_keys(environ) == [], "the prefix lint names it"
    assert _VALUE not in err and _VALUE not in repr(logs), "its value is never shown"


# ---- the variables a one-time import read (#168) -----------------------------------------------------------------

#: A variable a one-time import read, its value: no line or log may repeat it.
_VALUE = "zz-a-value-of-the-operator"


def _moved() -> dict[str, str]:
    import importlib

    try:
        config_retired = importlib.import_module("embry0.config_retired")
    except ImportError:
        pytest.fail("the variables a one-time import read must be listed, with where each moved")
    moved = getattr(config_retired, "MOVED_TO_DOCUMENTS", None)
    assert moved is not None, "the variables a one-time import read must be listed, with where each moved"
    return dict(moved)


def test_no_moved_variable_is_a_setting_any_more() -> None:
    """The config has no field for any of them, so nothing reads one; the list says where each one's setting is."""
    from embry0.config_lint import declared_env_names

    still = sorted(set(_moved()) & declared_env_names(Embry0Config))
    assert still == [], f"still read as settings: {still}"
    assert len(_moved()) == 53 and all(where.strip() for where in _moved().values())


@pytest.mark.parametrize("name", ["LEADS_ENABLED", "SCOUT_REPOS", "OPENAI_COMPATIBLE_PROVIDERS", "ASK_USER_ROUNDS_CAP"])
def test_startup_names_a_moved_variable_still_set_and_never_its_value(name: str, tmp_path: Path) -> None:
    from embry0.settings import env

    warn = getattr(env, "warn_moved_variables", None)
    assert warn is not None, "startup must say that a variable it no longer reads is still set"
    with capture_logs() as logs:
        warned = warn({name: _VALUE}, env_file=tmp_path / "absent.env")
    events = [e for e in logs if e["event"] == "settings_env_retired"]
    assert warned == [name] and [e["variable"] for e in events] == [name], events
    assert events[0]["log_level"] == "warning" and _moved()[name] in events[0]["hint"], events
    assert _VALUE not in repr(logs), "the value is never logged"


def test_a_moved_variable_in_the_env_file_counts_and_names_are_read_without_case(tmp_path: Path) -> None:
    from embry0.settings import env

    moved_set = getattr(env, "moved_variables_set", None)
    assert moved_set is not None, "which moved variables are set must be read from the environment and .env"
    env_file = tmp_path / ".env"
    env_file.write_text(f"SLOP_METER_REPOS={_VALUE}\nAPI_RATE_LIMIT_PER_MINUTE=10\n")
    assert moved_set({"leads_finders": "ticket"}, env_file=env_file) == ["LEADS_FINDERS", "SLOP_METER_REPOS"]
    assert moved_set({}, env_file=tmp_path / "absent.env") == []


def test_embry0_config_says_where_a_moved_variable_s_setting_lives(capsys, monkeypatch) -> None:
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    with patch.dict(os.environ, {"LEADS_ENABLED": _VALUE}, clear=True), pytest.raises(SystemExit):
        cmd_config(argparse.Namespace())
    err = capsys.readouterr().err
    assert (
        f"LEADS_ENABLED is no longer read: what it set is set in {_moved()['LEADS_ENABLED']} now; remove it" in err
    ), err
    assert "is set but is not an embry0 setting" not in err, "a moved variable is no typo of a setting"
    assert _VALUE not in err


def test_a_moved_variable_is_no_unknown_variable_to_either_lint() -> None:
    from embry0.config import unknown_prefixed_env_keys
    from embry0.config_lint import declared_env_names, find_unknown_env_vars

    environ = dict.fromkeys(_moved(), "1")
    assert find_unknown_env_vars(environ, declared_env_names(Embry0Config)) == []
    assert unknown_prefixed_env_keys(environ) == []


def test_the_configuration_doc_lists_every_moved_variable() -> None:
    text = (Path(__file__).resolve().parents[3] / "docs" / "configuration.md").read_text()
    section = text.split("### Variables no longer read", 1)[-1].split("\n## ", 1)[0]
    missing = [name for name, where in _moved().items() if f"| `{name}` | {where} |" not in section]
    assert missing == [], f"docs/configuration.md does not list: {missing}"
