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


@pytest.mark.parametrize("where", ["root", "member"])
@pytest.mark.parametrize(
    ("text", "kind"),
    [("[]", "an array"), ("42", "a number"), ('"x"', "a string"), ("null", "null"), ("false", "false")],
)
def test_discover_refuses_a_package_json_that_is_not_an_object(tmp_path: Path, where: str, text: str, kind: str):
    """#425: such a package.json decoded and reached ``.get``; it is as unreadable as broken JSON,
    and the refusal says in JSON's words what it holds rather than calling it invalid JSON."""
    import json

    from embry0.workspace_providers import WorkspaceProviderError

    (tmp_path / "apps" / "web").mkdir(parents=True)
    root = {"name": "toy", "workspaces": ["apps/*"]}
    (tmp_path / "package.json").write_text(text if where == "root" else json.dumps(root))
    (tmp_path / "apps" / "web" / "package.json").write_text(text if where == "member" else json.dumps({"name": "web"}))

    provider = NpmWorkspacesTurboProvider(tmp_path, {})
    try:
        with pytest.raises(WorkspaceProviderError) as exc:
            provider.discover()
    except (AttributeError, TypeError) as error:
        pytest.fail(f"discover raised {type(error).__name__} on a {where} package.json holding {text}: {error}")
    assert str(exc.value).endswith(f"package.json is valid JSON but not an object: it is {kind}"), exc.value
