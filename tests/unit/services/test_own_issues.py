"""The labels and template of the issues and jobs embry0 opens itself (#180, ``embry0.services.own_issues``).

Each is read for the repository from the settings store, and a label that
has become a trigger label since it was written is left off, logged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from embry0.settings.sections import CATALOG
from embry0.settings.sections.leads import LEADS
from embry0.settings.store import build_snapshot

try:
    from embry0.services.own_issues import own_issue_labels, section_for, trigger_labels, without_triggers
    from embry0.settings.sections.maps import MAPS
except ImportError:
    pytest.fail("nothing reads the labels of embry0's own issues from the settings document", pytrace=False)


class _Store:
    """The settings store as the readers use it: ``snapshot()`` of the documents of each scope given."""

    def __init__(self, **documents: dict[str, Any]) -> None:
        self.documents = documents

    async def snapshot(self) -> Any:
        rows = [
            {"scope": scope, "data": data, "generation": 1, "updated_at": None}
            for scope, data in {"deployment": {}, **self.documents}.items()
        ]
        return build_snapshot(CATALOG, rows, log=False)


class _Broken:
    async def snapshot(self) -> Any:
        raise RuntimeError("the store cannot be read")


def test_a_trigger_label_is_left_off_compared_as_the_trigger_compares_and_logged() -> None:
    with capture_logs() as logs:
        kept = without_triggers(
            ["ours", "Run-It", "map:shop", "OURS", "embry0"], ["run-it ", "EMBRY0", "  "], repo="acme/shop"
        )
    assert kept == ["ours", "map:shop"], f"the labels kept, in order and each once: {kept}"
    skipped = [log for log in logs if log["event"] == "issue_label_skipped"]
    assert [(log["label"], log["repo"]) for log in skipped] == [("Run-It", "acme/shop"), ("embry0", "acme/shop")], (
        f"each label left off is logged, with the repository: {logs}"
    )


async def test_the_trigger_labels_are_the_deployments_and_every_fallback_when_they_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIGGER_LABELS", "from-env")
    stored = SimpleNamespace(integration_repo=SimpleNamespace(get_trigger_labels=AsyncMock(return_value=["db"])))
    assert await trigger_labels(stored) == ["db"], "the labels the webhook reads"
    failing = SimpleNamespace(
        integration_repo=SimpleNamespace(get_trigger_labels=AsyncMock(side_effect=RuntimeError("down"))),
        config=SimpleNamespace(trigger_labels_list=["from-config"]),
    )
    with capture_logs() as logs:
        fallback = await trigger_labels(failing)
    assert set(fallback) == {"from-env", "from-config"}, (
        f"a read that fails checks every label a webhook could fall back to: {fallback}"
    )
    assert any(log["event"] == "own_issue_trigger_labels_read_failed" for log in logs), logs
    assert await trigger_labels(SimpleNamespace()) == ["from-env"], "no repository: the environment's"


async def test_each_section_is_read_for_the_repository_and_its_defaults_when_the_store_cannot_be() -> None:
    store = _Store(
        **{
            "organization:acme": {"leads": {"labels": ["team-lead"]}, "maps": {"labels": ["team-map"]}},
            "repository:acme/shop": {"maps": {"analysis_template": "deep-look"}},
        }
    )
    state = SimpleNamespace(settings=store)
    shop = await section_for(state, MAPS, "acme/shop")
    assert (shop.analysis_template, shop.labels) == ("deep-look", ("team-map",)), "the repository's, over acme's"
    assert (await section_for(state, LEADS, "acme/shop")).labels == ("team-lead",)
    assert (await section_for(state, MAPS, "globex/api")).labels == ("embry0-map",), "another organization's"
    assert (await section_for(SimpleNamespace(), MAPS, "acme/shop")).labels == ("embry0-map",), "no store"
    with capture_logs() as logs:
        broken = await section_for(SimpleNamespace(settings=_Broken()), MAPS, "acme/shop")
    assert broken.analysis_template == "analysis-default", "an issue is still opened, with the defaults"
    assert any(log["event"] == "own_issue_settings_read_failed" for log in logs), logs


async def test_an_issue_is_never_given_a_label_that_is_a_trigger_label_now() -> None:
    state = SimpleNamespace(integration_repo=SimpleNamespace(get_trigger_labels=AsyncMock(return_value=["Team-Map"])))
    assert await own_issue_labels(state, "acme/shop", ["team-map", "map:shop"]) == ["map:shop"]
