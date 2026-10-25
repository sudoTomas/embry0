"""What each lead's job is asked to do (spec 2026-10-25): what was seen, where, and the rule a quick fix would break."""

from __future__ import annotations

import pytest

from embry0.services.leads.tasks import lead_task


def _row(kind: str, data: dict, **over: object) -> dict:
    return {
        "kind": kind,
        "repo": "acme/web",
        "title": "T",
        "details": "D",
        "url": "https://u",
        "area": None,
        "data": data,
        **over,
    }


def test_each_kind_says_what_to_do_and_what_not_to():
    flaky = lead_task(_row("flaky", {"check": "unit", "shas": ["a" * 40, "b" * 40], "branch": "main"}, area="Billing"))
    assert "Make the check unit on acme/web reliable." in flaky and "aaaaaaa, bbbbbbb" in flaky
    assert "It is in the Billing area of the repository." in flaky and "Do not add retries" in flaky
    dep = lead_task(
        _row("alert", {"source": "dependabot", "package": "lodash", "fixed": "4.17.21", "manifest": "package.json"})
    )
    assert "Upgrade lodash to 4.17.21 or later in package.json" in dep
    scan = lead_task(_row("alert", {"source": "code-scanning"}))
    assert "Do not suppress or dismiss the alert." in scan
    review = lead_task(_row("review", {"number": 42, "head_ref": "feat/x"}))
    assert "Commit and push to feat/x" in review and "do NOT create a new pull request" in review


def test_a_ticket_has_no_task_of_its_own():
    with pytest.raises(ValueError, match="no task"):
        lead_task(_row("ticket", {}))
