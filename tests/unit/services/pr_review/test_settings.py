"""Reading repo_preferences.pr_review (#30)."""

import pytest

from embry0.services.pr_review.settings import ReviewSettings, effective_depth, review_settings


@pytest.mark.parametrize("prefs", [None, {}, {"pr_review": None}, {"pr_review": "on"}])
def test_an_unconfigured_repo_is_off_full_and_on_the_heavy_tier(prefs) -> None:
    assert review_settings(prefs) == ReviewSettings(enabled=False, depth="full", model=None)


def test_stored_settings_are_read() -> None:
    prefs = {"pr_review": {"enabled": True, "depth": "targeted", "model": "claude-sonnet-5"}}
    assert review_settings(prefs) == ReviewSettings(enabled=True, depth="targeted", model="claude-sonnet-5")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"enabled": "true"}, ReviewSettings()),  # only a real true turns it on
        ({"enabled": True, "depth": "deep"}, ReviewSettings(enabled=True)),
        ({"enabled": True, "model": "  "}, ReviewSettings(enabled=True)),
    ],
)
def test_odd_stored_values_fall_back_to_the_defaults(raw, expected) -> None:
    assert review_settings({"pr_review": raw}) == expected


def test_a_fork_is_always_read_only() -> None:
    settings = ReviewSettings(enabled=True, depth="full")
    assert effective_depth(settings, from_fork=True) == "read_only"
    assert effective_depth(settings, from_fork=False) == "full"
