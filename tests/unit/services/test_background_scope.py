"""The one repository scope rule for background features (#165), on settings snapshots: no database.

A repository is read by a feature when it is registered, active and its
``background.<feature>`` key resolves to true: the deployment's document,
then its organization's layer, then its own (spec 2026-10-29). Each snapshot
is built by :func:`embry0.settings.store.build_snapshot` from rows shaped as
the store reads them, so every layer resolves exactly as in production.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

import pytest

from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from embry0.storage import repo_renames

REGISTERED = ["acme/widgets", "acme/gadgets", "Globex/Gizmos", "group/sub/project", "group/tools", "other/kit"]


def _scope() -> Any:
    """The module that holds the rule; on a tree without it, a failure that says so."""
    try:
        return importlib.import_module("embry0.services.background_scope")
    except ModuleNotFoundError:
        pytest.fail(
            "embry0.services.background_scope holds the one rule every background feature reads its repositories "
            "by; it does not exist, so each feature still picks them its own way"
        )


def _enrollment(
    deployment: Mapping[str, Any] | None = None,
    layers: Mapping[str, Mapping[str, Any]] | None = None,
    imported: tuple[str, ...] = ("background.scout", "background.slop_meter"),
) -> Any:
    rows: list[dict[str, Any]] = [
        {
            "scope": "deployment",
            "data": {"background": dict(deployment)} if deployment is not None else {},
            "generation": 1,
            "updated_at": None,
            "bootstrapped_keys": list(imported),
        }
    ]
    for scope, values in (layers or {}).items():
        rows.append({"scope": scope, "data": {"background": dict(values)}, "generation": 1, "updated_at": None})
    return _scope().enrollment(build_snapshot(CATALOG, rows, log=False))


def test_nothing_enrolls_a_repository_in_the_scout_or_the_meter_by_default() -> None:
    read = _enrollment()
    assert read.scope("scout", REGISTERED) == [], "a new deployment scouts nothing until it enrolls a repository"
    assert read.scope("slop_meter", REGISTERED) == [], "and mirrors nothing"
    assert read.scope("leads", REGISTERED) == sorted(REGISTERED, key=str.lower), (
        "leads read every registered repository unless a layer takes it out"
    )


def test_an_organization_enrolls_its_repositories_and_a_repository_opts_out_of_it() -> None:
    read = _enrollment(
        layers={
            "organization:acme": {"slop_meter": True},
            "repository:acme/gadgets": {"slop_meter": False},
            "repository:other/kit": {"slop_meter": True},
        }
    )
    assert read.scope("slop_meter", REGISTERED) == ["acme/widgets", "other/kit"], (
        "acme's layer enrolls its repositories, acme/gadgets's own layer takes it out again, other/kit enrolls "
        "itself, and every other organization reads the deployment's false"
    )
    assert read.scope("scout", REGISTERED) == [], "enrolling a repository in the meter enrolls it in nothing else"


def test_the_deployment_enrolls_everything_and_a_layer_takes_an_organization_out() -> None:
    read = _enrollment(
        deployment={"scout": True},
        layers={"organization:globex": {"scout": False}, "organization:group": {"leads": False}},
    )
    assert read.scope("scout", REGISTERED) == [
        "acme/gadgets",
        "acme/widgets",
        "group/sub/project",
        "group/tools",
        "other/kit",
    ], (
        "the deployment's true enrolls every registered repository but those of globex, whose layer says false; "
        "the organization is the slug's first segment, compared without regard to case"
    )
    assert "group/sub/project" not in read.scope("leads", REGISTERED), (
        "a GitLab project in a subgroup belongs to its top group, so group's layer takes it out of leads"
    )


def test_a_gitlab_subgroup_project_reads_its_top_group_s_layer_and_its_own() -> None:
    read = _enrollment(
        layers={"organization:group": {"slop_meter": True}, "repository:group/sub/project": {"scout": True}}
    )
    assert read.scope("slop_meter", REGISTERED) == ["group/sub/project", "group/tools"], (
        "group's layer enrolls every project under it, a subgroup's included"
    )
    assert read.scope("scout", REGISTERED) == ["group/sub/project"], "a subgroup project's own layer is read too"


def test_a_layer_written_under_a_repository_s_old_slug_still_applies_after_its_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "acme/widgets"})
    read = _enrollment(layers={"repository:acme/old-widgets": {"slop_meter": True}})
    assert read.scope("slop_meter", REGISTERED) == ["acme/widgets"], (
        "the layer of acme/old-widgets is acme/widgets's after REPO_RENAMES maps one to the other"
    )


def test_only_registered_repositories_are_read_each_once_as_the_registry_spells_it() -> None:
    read = _enrollment(deployment={"slop_meter": True}, layers={"repository:ghost/repo": {"slop_meter": True}})
    assert read.scope("slop_meter", ["acme/widgets", "ACME/Widgets", "other/kit"]) == ["acme/widgets", "other/kit"], (
        "a repository is named once, however often or however spelled the registry lists it, and a layer of a "
        "repository nobody registered enrolls nothing"
    )
    assert read.scope("slop_meter", ["acme/widgets", "other/kit"], among=["OTHER/KIT"]) == ["other/kit"], (
        "a feature's own rule narrows the scope (the leads finders read only where someone answers), without "
        "regard to case"
    )


def test_what_a_feature_does_not_read_is_settled_only_once_its_import_has_run() -> None:
    assert _enrollment(imported=()).settled("slop_meter") is False, (
        "before the import enrolled what the meter read, what it does not read may still be its own"
    )
    assert _enrollment(imported=("background.slop_meter",)).settled("slop_meter") is True
    assert _enrollment(imported=()).settled("leads") is True, "leads import nothing, so they are settled at once"
    with pytest.raises(ValueError, match="not a background feature"):
        _enrollment().scope("watcher", REGISTERED)


async def test_the_candidates_are_the_registered_active_repositories() -> None:
    class Registry:
        async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
            rows = [{"repo": "acme/widgets", "active": True}, {"repo": "acme/old", "active": False}]
            return rows if include_inactive else rows[:1]

    assert await _scope().registered_reader(Registry())() == ["acme/widgets"], (
        "an inactive repository is read by no background feature"
    )


async def test_every_reader_takes_the_same_one_of_two_registry_spellings() -> None:
    """Two registry rows that differ only by case: the meter read one and moved its rows to the other every
    tick, reading the history again and paying for its judgments again. Each reader takes the active row's
    spelling, then the oldest row's, then the first by code point."""
    from datetime import UTC, datetime

    old, new = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)

    class Registry:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
            return [r for r in self.rows if include_inactive or r["active"]]

    cases = [
        (  # the review's probe: the active row is the one its preferences are kept under
            [
                {"repo": "acme/widgets", "active": True, "created_at": old},
                {"repo": "Acme/Widgets", "active": False, "created_at": new},
            ],
            "acme/widgets",
        ),
        (  # an older deactivated row: the active one still wins
            [
                {"repo": "acme/widgets", "active": True, "created_at": new},
                {"repo": "Acme/Widgets", "active": False, "created_at": old},
            ],
            "acme/widgets",
        ),
        (  # both active: the older registration
            [
                {"repo": "acme/widgets", "active": True, "created_at": old},
                {"repo": "Acme/Widgets", "active": True, "created_at": new},
            ],
            "acme/widgets",
        ),
        (  # both active, registered together: the first by code point
            [
                {"repo": "acme/widgets", "active": True, "created_at": old},
                {"repo": "Acme/Widgets", "active": True, "created_at": old},
            ],
            "Acme/Widgets",
        ),
    ]
    module = _scope()
    for rows, expected in cases:
        for order in (rows, list(reversed(rows))):
            registered = await module.registry_reader(Registry(order))()
            assert (registered.active, registered.inactive) == ((expected,), ()), (
                f"one spelling per repository, {expected} for {rows}, whatever order the registry answers in, "
                f"active when any of its rows is: {registered}"
            )
            assert await module.registered_reader(Registry(order))() == [expected], (
                "the scout and the leads take the same spelling as the meter"
            )


async def test_the_meter_reads_the_registry_once_split_by_whether_a_repository_is_active() -> None:
    asked: list[bool] = []

    class Registry:
        async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
            asked.append(include_inactive)
            return [{"repo": "acme/widgets", "active": True}, {"repo": "acme/old", "active": False}]

    reader = getattr(_scope(), "registry_reader", None)
    assert reader is not None, "the meter keeps what it read of an inactive repository, so it lists those too"
    registered = await reader(Registry())()
    assert (registered.active, registered.inactive, registered.every) == (
        ("acme/widgets",),
        ("acme/old",),
        ("acme/widgets", "acme/old"),
    )
    assert asked == [True], "one read of the registry, inactive repositories included"


def test_the_grace_period_is_the_deployment_s() -> None:
    from datetime import timedelta

    read = _enrollment(deployment={"prune_after_days": 30}, layers={"organization:acme": {"slop_meter": True}})
    assert read.prune_after == timedelta(days=30)
    assert _enrollment().prune_after == timedelta(days=7), "a week when nothing sets it"
