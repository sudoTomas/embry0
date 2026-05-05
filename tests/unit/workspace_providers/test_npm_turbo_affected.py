"""Phase-3 contract tests for NpmWorkspacesTurboProvider.affected().

Replaces the Phase-1 pin tests that asserted the stub returned all apps
regardless of diff. Phase 3 makes affected() diff-aware, honoring the
workspace dep graph + per-package no_cascade tags.
"""

from pathlib import Path

import pytest

from athanor.workspace_providers.npm_workspaces_turbo.provider import (
    NpmWorkspacesTurboProvider,
)


@pytest.fixture
def toy_repo() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "toy-monorepo"


def test_no_changes_yields_no_apps_to_qa(toy_repo: Path):
    """Empty diff → empty affected set."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected([], frozenset())
    assert result.directly_changed == frozenset()
    assert result.cascade_closure == frozenset()
    assert result.apps_to_qa == frozenset()


def test_app_only_change_qas_only_that_app(toy_repo: Path):
    """A diff confined to apps/hub → only @toy/hub QA'd."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("apps/hub/app/page.tsx")],
        no_cascade_packages=frozenset(),
    )
    assert result.directly_changed == frozenset({"@toy/hub"})
    assert result.apps_to_qa == frozenset({"@toy/hub"})


def test_package_change_cascades_to_dependent_apps(toy_repo: Path):
    """@toy/auth changes → @toy/hub + @toy/companion (both depend on auth) QA'd."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("packages/auth/src/index.ts")],
        no_cascade_packages=frozenset(),
    )
    assert result.directly_changed == frozenset({"@toy/auth"})
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion"})


def test_no_cascade_package_change_yields_no_apps(toy_repo: Path):
    """@toy/types is no_cascade → diff there → 0 apps QA'd."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("packages/types/src/index.ts")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert "@toy/types" in result.directly_changed
    assert result.apps_to_qa == frozenset()


def test_root_files_do_not_trigger_qa(toy_repo: Path):
    """Changes to root files (README, turbo.json, etc.) are not owned by any
    workspace member — apps_to_qa stays empty."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("README.md"), Path("turbo.json"), Path("package.json")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.directly_changed == frozenset()
    assert result.apps_to_qa == frozenset()


def test_mixed_diff_only_keeps_cascade_paths_through_non_no_cascade(toy_repo: Path):
    """Diff hits both @toy/auth and @toy/types; @toy/types is no_cascade.
    @toy/hub depends on both → QA'd via @toy/auth path. @toy/companion depends
    only on @toy/auth → QA'd. @toy/lane depends only on @toy/types → NOT QA'd."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [
            Path("packages/auth/src/index.ts"),
            Path("packages/types/src/index.ts"),
        ],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion"})


def test_invariants_hold_against_toy_monorepo(toy_repo: Path):
    """apps_to_qa ⊆ cascade_closure; directly_changed ⊆ cascade_closure."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    for diff in (
        [],
        [Path("apps/hub/app/page.tsx")],
        [Path("packages/auth/src/index.ts")],
        [Path("packages/types/src/index.ts")],
        [Path("packages/auth/src/index.ts"), Path("packages/types/src/index.ts")],
    ):
        for nc in (frozenset(), frozenset({"@toy/types"})):
            r = provider.affected(diff, nc)
            assert r.directly_changed <= r.cascade_closure
            assert r.apps_to_qa <= r.cascade_closure


def test_directly_changed_app_is_qad_even_if_no_cascade_blocks_cascade(toy_repo: Path):
    """If apps/lane itself has changes, lane is QA'd regardless of the fact
    that @toy/types (its only graph dep) is no_cascade. no_cascade affects
    cascade reach, not self-membership."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    result = provider.affected(
        [Path("apps/lane/app/page.tsx")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/lane"})
