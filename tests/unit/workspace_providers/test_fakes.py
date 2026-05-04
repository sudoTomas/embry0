from pathlib import Path

from athanor.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
    WorkspaceProvider,
)
from athanor.workspace_providers.fakes import FakeWorkspaceProvider


def test_fake_satisfies_protocol():
    fake = FakeWorkspaceProvider(
        apps=[WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@x/hub")],
        packages=[WorkspacePackage(name="@x/hub", path=Path("apps/hub"), is_app=True)],
        affected_result=AffectedSet(frozenset({"@x/hub"}), frozenset({"@x/hub"}), frozenset({"@x/hub"})),
    )
    assert isinstance(fake, WorkspaceProvider)


def test_fake_discover_returns_canned_lists():
    apps = [WorkspaceApp(name="hub", path=Path("apps/hub"), package_name="@x/hub")]
    packages = [
        WorkspacePackage(name="@x/hub", path=Path("apps/hub"), is_app=True),
        WorkspacePackage(name="@x/auth", path=Path("packages/auth"), is_app=False),
    ]
    fake = FakeWorkspaceProvider(apps=apps, packages=packages, affected_result=AffectedSet(frozenset(), frozenset(), frozenset()))
    discovered_apps, discovered_packages = fake.discover()
    assert discovered_apps == apps
    assert discovered_packages == packages


def test_fake_affected_returns_canned_result():
    canned = AffectedSet(
        directly_changed=frozenset({"@x/auth"}),
        cascade_closure=frozenset({"@x/auth", "@x/hub"}),
        apps_to_qa=frozenset({"@x/hub"}),
    )
    fake = FakeWorkspaceProvider(apps=[], packages=[], affected_result=canned)
    result = fake.affected(changed_files=[Path("packages/auth/src/index.ts")], no_cascade_packages=frozenset())
    assert result == canned


def test_fake_validate_returns_canned_warnings():
    fake = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=["app 'foo' not found in workspace"],
    )
    assert fake.validate(["foo"]) == ["app 'foo' not found in workspace"]


def test_fake_records_call_arguments():
    """Tests using FakeWorkspaceProvider need to assert about how the
    orchestrator called affected() — the fake records arg history."""
    fake = FakeWorkspaceProvider(
        apps=[], packages=[], affected_result=AffectedSet(frozenset(), frozenset(), frozenset())
    )
    fake.affected(changed_files=[Path("a.ts")], no_cascade_packages=frozenset({"@x/types"}))
    fake.affected(changed_files=[Path("b.ts")], no_cascade_packages=frozenset())
    assert len(fake.affected_calls) == 2
    assert fake.affected_calls[0].changed_files == [Path("a.ts")]
    assert fake.affected_calls[0].no_cascade_packages == frozenset({"@x/types"})
    assert fake.affected_calls[1].changed_files == [Path("b.ts")]
