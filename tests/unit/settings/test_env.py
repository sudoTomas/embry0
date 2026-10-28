"""``embry0 config`` names each variable the settings document governs (spec 2026-10-28)."""

from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

from embry0.cli.__main__ import cmd_config
from embry0.config import Embry0Config
from embry0.settings.env import env_name, governed_lines, governed_variables
from tests.fixtures.settings_sections import TEST_CATALOG


def test_a_variable_name_is_the_attribute_in_capitals() -> None:
    assert env_name("ask_remind_hours") == "ASK_REMIND_HOURS"


def test_only_variables_the_config_was_given_are_governed() -> None:
    with patch.dict(os.environ, {"ASK_REMIND_HOURS": "24", "SCOUT_REPOS": "acme/widgets"}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    found = [(v.name, v.path) for v in governed_variables(TEST_CATALOG, config)]
    assert found == [("ASK_REMIND_HOURS", "sample.remind_hours"), ("SCOUT_REPOS", "sample.labels")], (
        "a variable set to its default is still set, and still governed"
    )


def test_embry0_config_names_a_governed_variable(capsys, monkeypatch) -> None:
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    with patch.dict(os.environ, {"ASK_REMIND_HOURS": "12"}, clear=True), pytest.raises(SystemExit):
        cmd_config(argparse.Namespace())
    err = capsys.readouterr().err
    assert governed_lines(TEST_CATALOG, Embry0Config(_env_file=None, ask_remind_hours=12))[0] in err  # type: ignore[call-arg]
    assert "ASK_REMIND_HOURS is governed by the settings document (sample.remind_hours)" in err


@pytest.mark.parametrize("catalog", ["application", "tests"])
def test_every_env_import_names_a_config_attribute(catalog: str) -> None:
    from embry0.settings.sections import CATALOG

    sections = CATALOG if catalog == "application" else TEST_CATALOG
    missing = [
        f"{section.name}.{key} -> {imported.attr}"
        for section in sections
        for key, imported in section.imports().items()
        if imported.attr not in Embry0Config.model_fields
    ]
    assert missing == [], f"env names no Embry0Config field: {missing}"
