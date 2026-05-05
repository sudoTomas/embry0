from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.cache.image_builder import (
    ImageBuildError,
    ImageBuildResult,
    _make_image_tag,
    build_qa_image,
)


def test_make_image_tag_lowercases_uppercase_repo_names():
    """Regression for ultrareview bug_017. Docker requires repository
    name components to be lowercase; mixed-case GitHub repos like
    'Vercel/Next.js' would otherwise fail at commit time."""
    tag = _make_image_tag("Vercel/Next.js")
    # Everything before the ':' (the timestamp tag separator) must be lowercase
    name_part, _, _ = tag.partition(":")
    assert name_part == name_part.lower(), tag
    assert name_part == "athanor-qa-vercel_next.js"


@pytest.fixture
def stub_helpers(tmp_path: Path):
    """Provide a tuple of mocked dependencies that build_qa_image consumes."""

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("sb-builder-1", "tok-" + "A" * 40))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda cid, cmd: ["docker", "exec", cid, *cmd]
    docker.run_cmd = AsyncMock(return_value="")
    docker.commit_container = AsyncMock(return_value="org_r1:2026-05-05T06-00")

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim", "extra_networks": []})

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    image_repo = AsyncMock()
    image_repo.record = AsyncMock()

    return {
        "sandbox_mgr": sandbox_mgr,
        "docker": docker,
        "profiles_repo": profiles_repo,
        "proxy_mgr": proxy_mgr,
        "image_repo": image_repo,
    }


@pytest.mark.asyncio
async def test_build_qa_image_happy_path(stub_helpers, monkeypatch, tmp_path):
    """build_qa_image clones, npm ci's, turbo builds, commits, and records."""

    async def fake_clone(**kw):
        from athanor.workflows.qa._subtask_prep import ClonedSandbox
        return ClonedSandbox(head_sha="abc123")
    monkeypatch.setattr(
        "athanor.cache.image_builder.prep_qa_sandbox_clone", fake_clone
    )

    # Stub lockfile hash so we don't need a real package-lock.json
    monkeypatch.setattr(
        "athanor.cache.image_builder.compute_lockfile_sha",
        lambda root: "deadbeef" * 8,
    )

    result = await build_qa_image(
        repo="org/r1",
        branch="main",
        built_by="cli",
        **stub_helpers,
    )

    assert isinstance(result, ImageBuildResult)
    assert result.image_tag == "org_r1:2026-05-05T06-00"
    assert result.lockfile_sha == "deadbeef" * 8
    assert result.head_sha == "abc123"

    # The right sequence of docker exec calls:
    # 1. npm ci
    # 2. turbo run build --output-logs=hash-only
    cmds = [c for c in stub_helpers["docker"].run_cmd.call_args_list]
    cmd_strs = [" ".join(c.args[0]) for c in cmds]
    assert any("npm ci" in s for s in cmd_strs)
    assert any("turbo run build" in s for s in cmd_strs)

    # commit was called and result was persisted
    stub_helpers["docker"].commit_container.assert_awaited_once()
    stub_helpers["image_repo"].record.assert_awaited_once()
    record_kwargs = stub_helpers["image_repo"].record.await_args.kwargs
    assert record_kwargs["repo"] == "org/r1"
    assert record_kwargs["lockfile_sha"] == "deadbeef" * 8

    # cleanup
    stub_helpers["sandbox_mgr"].destroy.assert_awaited_once_with("sb-builder-1")


@pytest.mark.asyncio
async def test_build_qa_image_destroys_sandbox_on_npm_ci_failure(stub_helpers, monkeypatch):
    """If `npm ci` fails, the bootstrap sandbox is still destroyed."""

    async def fake_clone(**kw):
        from athanor.workflows.qa._subtask_prep import ClonedSandbox
        return ClonedSandbox(head_sha="abc")
    monkeypatch.setattr(
        "athanor.cache.image_builder.prep_qa_sandbox_clone", fake_clone
    )
    monkeypatch.setattr(
        "athanor.cache.image_builder.compute_lockfile_sha",
        lambda root: "abc",
    )

    async def crash(cmd, timeout=None):
        joined = " ".join(cmd)
        if "npm ci" in joined:
            raise RuntimeError("npm exited non-zero")
        return ""
    stub_helpers["docker"].run_cmd = crash

    with pytest.raises(ImageBuildError) as exc:
        await build_qa_image(
            repo="org/r1",
            branch="main",
            built_by="cli",
            **stub_helpers,
        )
    assert "npm ci" in str(exc.value)
    stub_helpers["sandbox_mgr"].destroy.assert_awaited_once()
    stub_helpers["image_repo"].record.assert_not_awaited()
