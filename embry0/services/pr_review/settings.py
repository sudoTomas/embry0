"""A repo's pull request review settings, as stored in repo_preferences.pr_review (#30)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEPTHS: tuple[str, ...] = ("full", "targeted", "read_only")


@dataclass(frozen=True)
class ReviewSettings:
    enabled: bool = False
    depth: str = "full"
    model: str | None = None  # None = the deployment's heavy tier


def review_settings(prefs: Mapping[str, Any] | None) -> ReviewSettings:
    """The settings, defaults for anything unset or malformed. Never raises."""
    raw = (prefs or {}).get("pr_review")
    if not isinstance(raw, Mapping):
        return ReviewSettings()
    depth = raw.get("depth")
    model = raw.get("model")
    return ReviewSettings(
        enabled=raw.get("enabled") is True,
        depth=depth if isinstance(depth, str) and depth in DEPTHS else "full",
        model=model.strip() if isinstance(model, str) and model.strip() else None,
    )


def effective_depth(settings: ReviewSettings, *, from_fork: bool) -> str:
    """A fork is always read-only: code a job runs can read the sandbox's credentials (ADR 0008)."""
    return "read_only" if from_fork else settings.depth
