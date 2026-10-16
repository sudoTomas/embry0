"""A repo's pull request review settings, as stored in repo_preferences.pr_review (#30)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEPTHS: tuple[str, ...] = ("full", "targeted", "read_only")
#: Whose ready pull requests start a review on their own: "team" = people
#: with write access, "anyone" = whoever opens or readies one.
READY_AUTHORS: tuple[str, ...] = ("team", "anyone")


@dataclass(frozen=True)
class ReviewSettings:
    enabled: bool = False
    depth: str = "full"
    model: str | None = None  # None = the deployment's heavy tier
    ready_authors: str = "team"


def review_settings(prefs: Mapping[str, Any] | None) -> ReviewSettings:
    """The settings, defaults for anything unset or malformed. Never raises.

    Settings saved before ``ready_authors`` existed carry no such key and
    read as ``"team"``, as does any value that is not one of
    :data:`READY_AUTHORS`: an odd value narrows who triggers a review, never
    widens it.
    """
    raw = (prefs or {}).get("pr_review")
    if not isinstance(raw, Mapping):
        return ReviewSettings()
    depth = raw.get("depth")
    model = raw.get("model")
    ready_authors = raw.get("ready_authors")
    return ReviewSettings(
        enabled=raw.get("enabled") is True,
        depth=depth if isinstance(depth, str) and depth in DEPTHS else "full",
        model=model.strip() if isinstance(model, str) and model.strip() else None,
        ready_authors=ready_authors if isinstance(ready_authors, str) and ready_authors in READY_AUTHORS else "team",
    )


def effective_depth(settings: ReviewSettings, *, from_fork: bool) -> str:
    """A fork is always read-only: code a job runs can read the sandbox's credentials (ADR 0008)."""
    return "read_only" if from_fork else settings.depth
