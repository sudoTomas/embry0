"""The identity vocabulary on disk equals the one in code; every builtin has a valid identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embry0.agents import identity

REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "docs" / "contracts" / "identity.json"


def test_contract_file_matches_the_code() -> None:
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing; run: python -m embry0.agents.identity --write-contract docs/contracts/identity.json"
    )
    assert json.loads(CONTRACT_PATH.read_text(encoding="utf-8")) == identity.contract(), (
        "docs/contracts/identity.json differs from embry0/agents/identity.py; regenerate it with "
        "python -m embry0.agents.identity --write-contract docs/contracts/identity.json and commit the diff"
    )


def test_every_builtin_identity_is_valid() -> None:
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    assert set(identity.BUILTIN_IDENTITY) == set(BUILTIN_SEED), (
        "every builtin agent needs an identity and nothing else does"
    )
    for agent_type, ident in identity.BUILTIN_IDENTITY.items():
        identity.validate_avatar(ident["avatar"])
        identity.validate_color(ident["color"])
        assert 0 < len(ident["display_name"]) <= identity.DISPLAY_NAME_MAX, agent_type
        assert len(ident["tagline"]) <= identity.TAGLINE_MAX, agent_type


def test_defaults_are_in_the_vocabulary() -> None:
    assert identity.DEFAULT_AVATAR in identity.AVATARS
    assert identity.DEFAULT_COLOR in identity.PALETTE
    assert len(identity.AVATARS) == 32


def test_the_adventurer_presets_follow_the_legacy_roster() -> None:
    assert identity.AVATARS[:12] == identity.LEGACY_AVATARS
    assert identity.AVATARS[12:] == identity.ADVENTURER_AVATARS
    assert len(identity.ADVENTURER_AVATARS) == 20
    for expected in (
        "starter",
        "bronze-warrior",
        "dragon-warrior",
        "champion",
        "apprentice",
        "archmage",
        "scout",
        "sharpshooter",
    ):
        assert expected in identity.ADVENTURER_AVATARS
    assert identity.validate_avatar("rune-warrior") == "rune-warrior"


@pytest.mark.parametrize("bad", ["", "wizard", "Smith"])
def test_validate_avatar_names_the_roster(bad: str) -> None:
    with pytest.raises(ValueError, match="avatar must be one of seer, smith"):
        identity.validate_avatar(bad)
