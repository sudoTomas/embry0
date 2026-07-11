from pathlib import Path

import pytest

from embry0.workspace_providers.npm_workspaces_turbo.provider import (
    NpmWorkspacesTurboProvider,
)


@pytest.fixture
def toy_repo() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "toy-monorepo"


def test_discover_finds_three_apps(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    apps, _packages = provider.discover()
    names = sorted(a.name for a in apps)
    assert names == ["companion", "hub", "lane"]


def test_discover_app_package_name_matches_package_json(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    apps, _ = provider.discover()
    by_name = {a.name: a for a in apps}
    assert by_name["hub"].package_name == "@toy/hub"
    assert by_name["companion"].package_name == "@toy/companion"
    assert by_name["lane"].package_name == "@toy/lane"


def test_discover_app_path_is_relative_to_repo_root(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    apps, _ = provider.discover()
    by_name = {a.name: a for a in apps}
    assert by_name["hub"].path == Path("apps/hub")


def test_discover_includes_apps_and_packages_in_packages_list(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    _apps, packages = provider.discover()
    by_name = {p.name: p for p in packages}
    assert "@toy/hub" in by_name and by_name["@toy/hub"].is_app is True
    assert "@toy/auth" in by_name and by_name["@toy/auth"].is_app is False
    assert "@toy/types" in by_name and by_name["@toy/types"].is_app is False


def test_discover_with_overridden_globs(toy_repo: Path):
    """A repo with non-conventional layout passes its globs via config."""
    # Phase-1: still resolves against toy layout because we pass conventional globs.
    provider = NpmWorkspacesTurboProvider(toy_repo, {"apps_glob": "apps/*", "packages_glob": "packages/*"})
    apps, packages = provider.discover()
    assert len(apps) == 3
    assert len(packages) == 5  # 3 apps + 2 libs


def test_discover_missing_package_json_raises(tmp_path: Path):
    from embry0.workspace_providers import WorkspaceProviderError

    provider = NpmWorkspacesTurboProvider(tmp_path, {})
    with pytest.raises(WorkspaceProviderError) as exc:
        provider.discover()
    assert "package.json" in str(exc.value)
