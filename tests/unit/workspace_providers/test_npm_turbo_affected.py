from pathlib import Path

import pytest

from athanor.workspace_providers.npm_workspaces_turbo.provider import (
    NpmWorkspacesTurboProvider,
)


@pytest.fixture
def toy_repo() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "toy-monorepo"


def test_affected_returns_all_apps_regardless_of_diff_phase_1(toy_repo: Path):
    """Phase 1: affected() ignores changed_files; returns every app in the
    workspace. Phase 3 will replace with real diff-based filtering."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})

    no_changes = provider.affected([], frozenset())
    one_change = provider.affected([Path("apps/hub/app/page.tsx")], frozenset())
    package_change = provider.affected([Path("packages/auth/src/index.ts")], frozenset())

    expected = frozenset({"@toy/hub", "@toy/companion", "@toy/lane"})
    assert no_changes.apps_to_qa == expected
    assert one_change.apps_to_qa == expected
    assert package_change.apps_to_qa == expected


def test_affected_invariants(toy_repo: Path):
    """apps_to_qa ⊆ cascade_closure; directly_changed ⊆ cascade_closure."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected([Path("apps/hub/app/page.tsx")], frozenset())
    assert result.apps_to_qa <= result.cascade_closure
    assert result.directly_changed <= result.cascade_closure


def test_affected_no_cascade_does_not_yet_filter_phase_1(toy_repo: Path):
    """Phase 1 stub does not honor no_cascade_packages — that's Phase 3.
    This test PINS that behavior so Phase 3's behavior change is intentional."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("packages/types/src/index.ts")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    # In Phase 1, no_cascade is ignored — every app still QA'd.
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion", "@toy/lane"})
