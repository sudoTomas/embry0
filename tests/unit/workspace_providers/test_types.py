from pathlib import Path

import pytest

from athanor.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
    WorkspaceProviderError,
)


def test_workspace_app_is_frozen():
    app = WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@raven/hub")
    with pytest.raises(Exception):
        app.name = "other"  # type: ignore[misc]


def test_workspace_app_equality_uses_all_fields():
    a = WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@raven/hub")
    b = WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@raven/hub")
    c = WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@raven/other")
    assert a == b
    assert a != c


def test_workspace_package_distinguishes_apps_from_libs():
    pkg = WorkspacePackage(name="@raven/auth", path=Path("packages/auth"), is_app=False)
    assert pkg.is_app is False


def test_affected_set_apps_to_qa_subset_of_closure():
    s = AffectedSet(
        directly_changed=frozenset({"@raven/auth"}),
        cascade_closure=frozenset({"@raven/auth", "@raven/hub"}),
        apps_to_qa=frozenset({"@raven/hub"}),
    )
    assert s.apps_to_qa <= s.cascade_closure
    assert s.directly_changed <= s.cascade_closure


def test_workspace_provider_error_is_exception():
    with pytest.raises(WorkspaceProviderError):
        raise WorkspaceProviderError("boom")
