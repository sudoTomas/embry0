"""TrackerRegistry — registration, conformance, replacement (#160)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from embry0.trackers import BacklogRead, IssueTracker, TrackerIssueRef, TrackerRegistry, TriggerContext


class _Conforming:
    def __init__(self, name: str = "fake") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def handle_webhook_event(self, payload: dict[str, Any], ctx: TriggerContext) -> dict[str, Any]:
        return {"status": "ignored"}

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        return True

    async def list_backlog(self, repos: Sequence[str]) -> BacklogRead:
        return BacklogRead()


def test_registers_and_resolves_by_name():
    reg = TrackerRegistry()
    tracker = _Conforming("github")
    reg.register(tracker)
    assert reg.get("github") is tracker
    assert reg.get("nope") is None
    assert reg.names() == ["github"]


def test_registration_replaces_existing_instance():
    """The Linear route re-registers its per-delivery resolved instance —
    the registry must hand out the fresh one, never the stale one."""
    reg = TrackerRegistry()
    stale, fresh = _Conforming("linear"), _Conforming("linear")
    reg.register(stale)
    reg.register(fresh)
    assert reg.get("linear") is fresh


def test_non_conforming_object_is_rejected_at_registration():
    """Fail at wire-up time, not at first webhook delivery."""
    reg = TrackerRegistry()
    with pytest.raises(TypeError, match="does not conform"):
        reg.register(object())  # type: ignore[arg-type]


def test_real_services_conform_to_the_protocol():
    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.github_sync import GitHubSyncService
    from embry0.services.gitlab_sync import GitLabSyncService
    from embry0.services.linear_sync import LinearSyncService

    assert isinstance(GitHubSyncService(github_token="t"), IssueTracker)
    assert isinstance(LinearSyncService("k", {}), IssueTracker)
    assert isinstance(GitLabSyncService(GitLabClient(default_token="glpat-x")), IssueTracker)
