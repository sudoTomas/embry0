"""Tests for _path_index — path → owning-package resolution."""

from __future__ import annotations

from pathlib import Path

from athanor.workspace_providers.npm_workspaces_turbo._path_index import (
    PackagePathIndex,
    build_path_index,
)


def _toy_repo() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "toy-monorepo"


def test_path_index_resolves_app_files_against_toy_monorepo():
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert isinstance(idx, PackagePathIndex)

    assert idx.owner_of(Path("apps/hub/app/page.tsx")) == "@toy/hub"
    assert idx.owner_of(Path("apps/companion/app/page.tsx")) == "@toy/companion"
    assert idx.owner_of(Path("apps/lane/app/page.tsx")) == "@toy/lane"


def test_path_index_resolves_package_files_against_toy_monorepo():
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert idx.owner_of(Path("packages/auth/src/index.ts")) == "@toy/auth"
    assert idx.owner_of(Path("packages/types/src/index.ts")) == "@toy/types"


def test_path_index_returns_none_for_root_files():
    """Root-level files (e.g. README.md, turbo.json, package.json) belong to
    no workspace member — they don't trigger the dep-cascade."""
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert idx.owner_of(Path("README.md")) is None
    assert idx.owner_of(Path("turbo.json")) is None
    assert idx.owner_of(Path("package.json")) is None
    assert idx.owner_of(Path("package-lock.json")) is None


def test_path_index_returns_none_for_unknown_paths():
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert idx.owner_of(Path("apps/does-not-exist/page.tsx")) is None
    assert idx.owner_of(Path("docs/architecture.md")) is None


def test_owners_of_paths_aggregates_to_set():
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    owners = idx.owners_of_paths(
        [
            Path("apps/hub/app/page.tsx"),
            Path("apps/companion/app/page.tsx"),
            Path("README.md"),  # root file → ignored
            Path("packages/auth/src/index.ts"),
            Path("apps/hub/app/about.tsx"),  # also @toy/hub
        ]
    )
    assert owners == frozenset({"@toy/hub", "@toy/companion", "@toy/auth"})


def test_path_with_leading_slash_or_dot_is_normalized():
    """Defensive: callers may pass './apps/hub/x.ts' or 'apps/hub/x.ts' — both
    must resolve to @toy/hub."""
    idx = build_path_index(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert idx.owner_of(Path("apps/hub/app/page.tsx")) == "@toy/hub"
    assert idx.owner_of(Path("./apps/hub/app/page.tsx")) == "@toy/hub"
