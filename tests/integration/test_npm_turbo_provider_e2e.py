"""End-to-end: NpmTurbo provider × real toy-monorepo (no docker, no shell-out)."""

from pathlib import Path

import pytest

from athanor.workspace_providers import load_provider
from athanor.workspace_providers.npm_workspaces_turbo.provider import (
    NpmWorkspacesTurboProvider,
)


@pytest.fixture
def toy_repo() -> Path:
    return Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "toy-monorepo"


def test_registry_lookup_yields_npm_turbo(toy_repo: Path):
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    assert isinstance(provider, NpmWorkspacesTurboProvider)


def test_full_flow(toy_repo: Path):
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    apps, packages = provider.discover()
    affected = provider.affected([Path("apps/hub/app/page.tsx")], frozenset())
    warnings = provider.validate([a.name for a in apps])

    assert {a.name for a in apps} == {"hub", "companion", "lane"}
    assert {p.name for p in packages} >= {"@toy/hub", "@toy/auth", "@toy/types"}
    # Phase 3: diff-aware. apps/hub diff → @toy/hub only.
    assert affected.apps_to_qa == frozenset({"@toy/hub"})
    assert warnings == []


def test_apps_hub_only_diff_qas_only_hub(toy_repo: Path):
    """Phase-3 scenario: a PR touching only apps/hub/app/page.tsx → @toy/hub."""
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    result = provider.affected(
        [Path("apps/hub/app/page.tsx")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/hub"})


def test_packages_auth_diff_cascades_to_hub_and_companion(toy_repo: Path):
    """Phase-3 scenario: a PR touching packages/auth/src/index.ts → both apps
    that depend on @toy/auth (hub + companion)."""
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    result = provider.affected(
        [Path("packages/auth/src/index.ts")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion"})


def test_packages_types_diff_yields_no_apps_via_no_cascade(toy_repo: Path):
    """Phase-3 scenario: a PR touching packages/types/src/index.ts → 0 apps,
    because @toy/types is no_cascade in qa.yaml."""
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    result = provider.affected(
        [Path("packages/types/src/index.ts")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset()


def test_root_only_diff_yields_no_apps(toy_repo: Path):
    """Phase-3 scenario: a PR touching only root config (turbo.json, README.md)
    affects no workspace member."""
    provider = load_provider("npm-workspaces-turbo", toy_repo, {})
    result = provider.affected(
        [Path("turbo.json"), Path("README.md")],
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset()
