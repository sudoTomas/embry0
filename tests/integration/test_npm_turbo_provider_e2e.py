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
    assert affected.apps_to_qa == frozenset(a.package_name for a in apps)
    assert warnings == []
