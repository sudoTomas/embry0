from pathlib import Path

import pytest

from athanor.cache.lockfile_hash import (
    LockfileNotFoundError,
    compute_lockfile_sha,
)


def test_compute_sha_is_deterministic(tmp_path: Path):
    f = tmp_path / "package-lock.json"
    f.write_text('{"a": 1}\n', encoding="utf-8")
    a = compute_lockfile_sha(tmp_path)
    b = compute_lockfile_sha(tmp_path)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_compute_sha_changes_with_content(tmp_path: Path):
    f = tmp_path / "package-lock.json"
    f.write_text('{"a": 1}\n', encoding="utf-8")
    sha_a = compute_lockfile_sha(tmp_path)
    f.write_text('{"a": 2}\n', encoding="utf-8")
    sha_b = compute_lockfile_sha(tmp_path)
    assert sha_a != sha_b


def test_compute_sha_missing_lockfile_raises(tmp_path: Path):
    with pytest.raises(LockfileNotFoundError):
        compute_lockfile_sha(tmp_path)


def test_compute_sha_supports_yarn_lock(tmp_path: Path):
    """Repos using yarn instead of npm — fall back to yarn.lock."""
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
    sha = compute_lockfile_sha(tmp_path)
    assert len(sha) == 64


def test_compute_sha_supports_pnpm_lock(tmp_path: Path):
    """Repos using pnpm — fall back to pnpm-lock.yaml."""
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0\n", encoding="utf-8")
    sha = compute_lockfile_sha(tmp_path)
    assert len(sha) == 64


def test_compute_sha_prefers_npm_over_yarn(tmp_path: Path):
    """If multiple lockfiles present, prefer package-lock.json (npm canonical)."""
    (tmp_path / "package-lock.json").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# yarn\n", encoding="utf-8")
    npm_sha = compute_lockfile_sha(tmp_path)
    # Different content from yarn.lock alone:
    (tmp_path / "package-lock.json").unlink()
    yarn_sha = compute_lockfile_sha(tmp_path)
    assert npm_sha != yarn_sha
