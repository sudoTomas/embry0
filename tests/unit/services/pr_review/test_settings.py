"""Reading repo_preferences.pr_review (#30)."""

import pytest

from embry0.services.pr_review.settings import ReviewSettings, effective_depth, review_settings


@pytest.mark.parametrize("prefs", [None, {}, {"pr_review": None}, {"pr_review": "on"}])
def test_an_unconfigured_repo_is_off_full_and_on_the_heavy_tier(prefs) -> None:
    assert review_settings(prefs) == ReviewSettings(enabled=False, depth="full", model=None)
    ready_authors = getattr(review_settings(prefs), "ready_authors", None)
    assert ready_authors == "team", f"an unconfigured repo must review only its team, not {ready_authors!r}"


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


def test_settings_saved_before_ready_authors_existed_read_as_team() -> None:
    settings = review_settings({"pr_review": {"enabled": True, "depth": "full", "model": None}})
    ready_authors = getattr(settings, "ready_authors", None)
    assert ready_authors == "team", f"a row without ready_authors read as {ready_authors!r}"


@pytest.mark.parametrize(
    ("raw", "expected"), [("anyone", "anyone"), ("team", "team"), ("everyone", "team"), (1, "team")]
)
def test_ready_authors_is_read_and_anything_odd_is_team(raw, expected) -> None:
    settings = review_settings({"pr_review": {"enabled": True, "ready_authors": raw}})
    read = getattr(settings, "ready_authors", None)
    assert read == expected, f"ready_authors {raw!r} read as {read!r}"


def test_a_fork_is_always_read_only() -> None:
    settings = ReviewSettings(enabled=True, depth="full")
    assert effective_depth(settings, from_fork=True) == "read_only"
    assert effective_depth(settings, from_fork=False) == "full"
