from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.cache.image_builder import (
    ImageBuildError,
    ImageBuildResult,
    _make_image_tag,
    build_qa_image,
)
from embry0.execution.runtime import DockerRuntime


def test_make_image_tag_lowercases_uppercase_repo_names():
    """Regression for ultrareview bug_017. Docker requires repository
    name components to be lowercase; mixed-case GitHub repos like
    'Vercel/Next.js' would otherwise fail at commit time."""
    tag = _make_image_tag("Vercel/Next.js")
    # Everything before the ':' (the timestamp tag separator) must be lowercase
    name_part, _, _ = tag.partition(":")
    assert name_part == name_part.lower(), tag
    assert name_part == "embry0-qa-vercel_next.js"


BASE_IMAGE = "registry:5000/embry0-sandbox-qa:latest"
BASE_DIGEST = "sha256:" + "a" * 64
BASE_LOCAL_ID = "sha256:" + "b" * 64
PUSHED_DIGEST = "sha256:" + "c" * 64


async def _docker_answers(cmd, timeout=60, **kw):
    """`docker push` reports the digest it pushed, as the real CLI does."""
    if "push" in cmd:
        tag = cmd[-1].rsplit(":", 1)[-1]
        return f"The push refers to repository [{cmd[-1]}]\n{tag}: digest: {PUSHED_DIGEST} size: 735"
    return ""


@pytest.fixture(autouse=True)
def base_image_probes(monkeypatch):
    """#334: the builder reads the base image off its own container and asks
    for that image's comparable digest. Both probes are stubbed here; they are
    covered for real in test_prebaked_staleness.py."""
    probes = {
        "container_base_image": AsyncMock(return_value=(BASE_IMAGE, BASE_LOCAL_ID)),
        "image_digest": AsyncMock(return_value=BASE_DIGEST),
        "pull_image": AsyncMock(return_value=True),
    }
    for name, probe in probes.items():
        monkeypatch.setattr(f"embry0.cache.image_builder.{name}", probe)
    return probes


@pytest.fixture
def stub_helpers(tmp_path: Path):
    """Provide a tuple of mocked dependencies that build_qa_image consumes."""

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("sb-builder-1", "tok-" + "A" * 40))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda cid, cmd: ["docker", "exec", cid, *cmd]
    docker.run_cmd = AsyncMock(side_effect=_docker_answers)
    docker.commit_container = AsyncMock(return_value="org_r1:2026-05-05T06-00")

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim", "extra_networks": []})

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    image_repo = AsyncMock()
    image_repo.record = AsyncMock()

    return {
        "sandbox_mgr": sandbox_mgr,
        "runtime": DockerRuntime(docker),
        "profiles_repo": profiles_repo,
        "proxy_mgr": proxy_mgr,
        "image_repo": image_repo,
    }


@pytest.mark.asyncio
async def test_build_qa_image_happy_path(stub_helpers, monkeypatch, tmp_path):
    """build_qa_image clones, npm ci's, turbo builds, commits, and records."""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)

    # Stub lockfile hash so we don't need a real package-lock.json
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
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
    cmds = [c for c in stub_helpers["runtime"].client.run_cmd.call_args_list]
    cmd_strs = [" ".join(c.args[0]) for c in cmds]
    assert any("npm ci" in s for s in cmd_strs)
    assert any("turbo run build" in s for s in cmd_strs)

    # commit was called and result was persisted
    stub_helpers["runtime"].client.commit_container.assert_awaited_once()
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
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
        lambda root: "abc",
    )

    async def crash(cmd, timeout=None, **_kwargs):
        joined = " ".join(cmd)
        if "npm ci" in joined:
            raise RuntimeError("npm exited non-zero")
        return ""

    stub_helpers["runtime"].client.run_cmd = crash

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


@pytest.mark.asyncio
async def test_build_qa_image_honors_custom_install_command(stub_helpers, monkeypatch):
    """RAV-1400: cache.install_command replaces the npm ci default verbatim."""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
        lambda root: "deadbeef" * 8,
    )

    await build_qa_image(
        repo="acme/console",
        branch="master",
        built_by="cli",
        install_command="npm ci --prefer-offline --legacy-peer-deps",
        **stub_helpers,
    )

    cmd_strs = [" ".join(c.args[0]) for c in stub_helpers["runtime"].client.run_cmd.call_args_list]
    assert any("npm ci --prefer-offline --legacy-peer-deps" in s for s in cmd_strs), cmd_strs


@pytest.mark.asyncio
async def test_build_qa_image_binds_repo_into_sandbox_enrollment(stub_helpers, monkeypatch):
    """RAV-1400: same enrollment fix as the warmer — clone needs the owner token."""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
        lambda root: "deadbeef" * 8,
    )

    await build_qa_image(
        repo="acme/console",
        branch="master",
        built_by="cli",
        **stub_helpers,
    )

    create_kwargs = stub_helpers["sandbox_mgr"].create.call_args.kwargs
    assert create_kwargs.get("repo") == "acme/console"


@pytest.mark.asyncio
async def test_build_qa_image_tags_and_pushes_qualified_name(stub_helpers, monkeypatch):
    """RAV-1400: sandbox launches registry-qualify the image name, so the
    bake must tag + push it or every launch 404s on manifest lookup."""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
        lambda root: "deadbeef" * 8,
    )

    await build_qa_image(
        repo="org/r1",
        branch="main",
        built_by="cli",
        image_registry="registry:5000",
        **stub_helpers,
    )

    cmd_strs = [" ".join(c.args[0]) for c in stub_helpers["runtime"].client.run_cmd.call_args_list]
    assert any("tag org_r1:2026-05-05T06-00 registry:5000/org_r1:2026-05-05T06-00" in s for s in cmd_strs), cmd_strs
    assert any("push registry:5000/org_r1:2026-05-05T06-00" in s for s in cmd_strs), cmd_strs


@pytest.mark.asyncio
async def test_build_qa_image_records_the_pushed_image_by_digest(stub_helpers, monkeypatch):
    """#523: the bake runs as `<tag>@<digest>`, the digest its own push reported,
    so a later push to the same tag is never what a QA sandbox starts from."""
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")

    result = await build_qa_image(
        repo="org/r1", branch="main", built_by="cli", image_registry="registry:5000", **stub_helpers
    )

    pinned = f"org_r1:2026-05-05T06-00@{PUSHED_DIGEST}"
    recorded = stub_helpers["image_repo"].record.await_args.kwargs["image_tag"]
    assert recorded == pinned, f"the bake must be recorded by the digest its push reported, got {recorded!r}"
    assert result.image_tag == pinned


@pytest.mark.asyncio
async def test_build_qa_image_refuses_to_record_a_push_without_a_digest(stub_helpers, monkeypatch):
    """An unpinned bake would run by tag: fatal, like a failed push."""
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")
    stub_helpers["runtime"].client.run_cmd = AsyncMock(return_value="")

    with pytest.raises(ImageBuildError, match="reported no digest"):
        await build_qa_image(
            repo="org/r1", branch="main", built_by="cli", image_registry="registry:5000", **stub_helpers
        )
    stub_helpers["image_repo"].record.assert_not_awaited()
    stub_helpers["sandbox_mgr"].destroy.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_qa_image_records_a_pinned_base_by_its_tag(stub_helpers, monkeypatch, base_image_probes):
    """#523: the builder sandbox launched as `…:latest@<pin>`. Staleness asks
    whether the TAG has moved since, so the tag is what gets recorded."""
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")
    base_image_probes["container_base_image"].return_value = (f"{BASE_IMAGE}@{BASE_DIGEST}", BASE_LOCAL_ID)

    await build_qa_image(repo="org/r1", branch="main", built_by="cli", **stub_helpers)

    kwargs = stub_helpers["image_repo"].record.await_args.kwargs
    assert kwargs["base_image"] == BASE_IMAGE, "the base must be recorded without its pin"
    assert kwargs["base_image_digest"] == BASE_DIGEST
    base_image_probes["image_digest"].assert_awaited_once_with(
        stub_helpers["runtime"], BASE_LOCAL_ID, repo_name=BASE_IMAGE
    )


@pytest.mark.asyncio
async def test_build_qa_image_bakes_on_qa_profile_lineage(stub_helpers, monkeypatch):
    """RAV-1400: the baked tag replaces the subtask profile's base_image, so
    the bake must start from the browser-equipped QA image, not slim."""

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr(
        "embry0.cache.image_builder.compute_lockfile_sha",
        lambda root: "deadbeef" * 8,
    )

    await build_qa_image(repo="org/r1", branch="main", built_by="cli", **stub_helpers)

    profile_lookups = [c.args[0] for c in stub_helpers["profiles_repo"].get.call_args_list]
    assert profile_lookups[0] == "qa-external"


@pytest.mark.asyncio
async def test_build_qa_image_clones_the_repo_forge(stub_helpers, monkeypatch):
    seen = {}

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        seen.update(kw)
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "deadbeef" * 8)

    await build_qa_image(
        repo="acme/group/backend",
        branch="dev",
        built_by="cli",
        forge="gitlab",
        gitlab_url="https://gitlab.example.com",
        **stub_helpers,
    )

    assert seen["forge"] == "gitlab"
    assert seen["gitlab_url"] == "https://gitlab.example.com"
    assert stub_helpers["sandbox_mgr"].create.await_args.kwargs["forge"] == "gitlab"


async def _fake_clone(**kw):
    from embry0.workflows.qa._subtask_prep import ClonedSandbox

    return ClonedSandbox(head_sha="abc123")


@pytest.mark.asyncio
async def test_build_qa_image_records_the_base_image_it_was_committed_from(
    stub_helpers, monkeypatch, base_image_probes
):
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")

    await build_qa_image(repo="org/r1", branch="main", built_by="cli", **stub_helpers)

    # The digest is taken from the bits the container actually runs, not from
    # the tag - the tag can move while a bake runs.
    base_image_probes["image_digest"].assert_awaited_once_with(
        stub_helpers["runtime"], BASE_LOCAL_ID, repo_name=BASE_IMAGE
    )
    kwargs = stub_helpers["image_repo"].record.await_args.kwargs
    assert kwargs["base_image"] == BASE_IMAGE
    assert kwargs["base_image_digest"] == BASE_DIGEST


@pytest.mark.asyncio
async def test_build_qa_image_refreshes_the_base_before_baking_on_it(stub_helpers, monkeypatch, base_image_probes):
    """#338 review: nothing else refreshes DinD's copy of embry0-sandbox-qa, so
    without this the bake lands on a base that is already a rebuild behind and
    selection would flag the fresh image stale the moment it is recorded."""
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")
    stub_helpers["profiles_repo"].get = AsyncMock(
        return_value={"name": "qa-external", "base_image": "embry0-sandbox-qa:latest", "extra_networks": []}
    )
    order: list[str] = []
    base_image_probes["pull_image"].side_effect = lambda *a, **kw: order.append("pull") or True
    stub_helpers["sandbox_mgr"].create.side_effect = lambda *a, **kw: (
        order.append("create")
        or (
            "sb-builder-1",
            "tok-" + "A" * 40,
        )
    )

    await build_qa_image(repo="org/r1", branch="main", built_by="cli", image_registry="registry:5000", **stub_helpers)

    assert order == ["pull", "create"]
    assert base_image_probes["pull_image"].await_args.args[1] == BASE_IMAGE


@pytest.mark.asyncio
async def test_build_qa_image_records_no_base_when_the_digest_cannot_be_determined(
    stub_helpers, monkeypatch, base_image_probes
):
    """Fail open: an untrackable image is still a useful image, and "unknown"
    keeps using it. Recording a half pair, or a digest in the wrong form, would
    flag a fresh image as stale."""
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")
    base_image_probes["image_digest"].return_value = None

    result = await build_qa_image(repo="org/r1", branch="main", built_by="cli", **stub_helpers)

    assert result.image_tag == "org_r1:2026-05-05T06-00"
    kwargs = stub_helpers["image_repo"].record.await_args.kwargs
    assert kwargs["base_image"] is None
    assert kwargs["base_image_digest"] is None


@pytest.mark.asyncio
async def test_build_qa_image_records_no_base_when_the_container_cannot_be_read(
    stub_helpers, monkeypatch, base_image_probes
):
    monkeypatch.setattr("embry0.cache.image_builder.prep_qa_sandbox_clone", _fake_clone)
    monkeypatch.setattr("embry0.cache.image_builder.compute_lockfile_sha", lambda root: "abc")
    base_image_probes["container_base_image"].side_effect = ValueError("unexpected docker inspect output")

    await build_qa_image(repo="org/r1", branch="main", built_by="cli", **stub_helpers)

    kwargs = stub_helpers["image_repo"].record.await_args.kwargs
    assert kwargs["base_image"] is None
    base_image_probes["image_digest"].assert_not_awaited()
