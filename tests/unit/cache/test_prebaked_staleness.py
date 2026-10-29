"""#334 — a pre-built QA image whose base was rebuilt is not used."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from embry0.cache import prebaked_staleness as mod
from embry0.cache.prebaked_image import PrebakedImageLookup
from embry0.cache.prebaked_staleness import (
    BaseFreshness,
    BaseImageDigestResolver,
    classify,
    container_base_image,
    image_digest,
    summarize_prebuilt_images,
)
from embry0.execution.runtime import DockerRuntime
from embry0.storage.repositories.qa_image_tags import QAImageTagRow

BASE = "registry:5000/embry0-sandbox-qa:latest"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def _row(repo: str = "org/r1", tag: str = "img:t1", digest: str | None = D1) -> QAImageTagRow:
    return QAImageTagRow(
        repo=repo,
        image_tag=tag,
        lockfile_sha="x",
        built_at=datetime(2026, 9, 1, tzinfo=UTC),
        built_by="cli",
        is_active=True,
        metadata={},
        base_image=BASE if digest else None,
        base_image_digest=digest,
    )


@pytest.fixture(autouse=True)
def _fresh_warning_memory():
    mod.reset_warning_memory()
    yield
    mod.reset_warning_memory()


def _resolver(digest: str | None) -> MagicMock:
    r = MagicMock()
    r.current = AsyncMock(return_value=digest)
    return r


# ── classify ────────────────────────────────────────────────────────────────


def test_same_digest_is_fresh():
    assert classify(_row(digest=D1), D1) is BaseFreshness.FRESH


def test_different_digest_is_stale():
    assert classify(_row(digest=D1), D2) is BaseFreshness.STALE


def test_row_without_a_recorded_digest_is_unknown_not_stale():
    assert classify(_row(digest=None), D2) is BaseFreshness.UNKNOWN


def test_unresolvable_current_digest_is_unknown_not_stale():
    assert classify(_row(digest=D1), None) is BaseFreshness.UNKNOWN


# ── selection ───────────────────────────────────────────────────────────────


async def _lookup(row: QAImageTagRow | None, current: str | None) -> tuple[str | None, MagicMock]:
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=row)
    resolver = _resolver(current)
    tag = await PrebakedImageLookup(image_repo=image_repo, digest_resolver=resolver).get_tag_for_repo("org/r1")
    return tag, resolver


@pytest.mark.asyncio
async def test_fresh_image_is_used():
    tag, resolver = await _lookup(_row(), D1)
    assert tag == "img:t1"
    resolver.current.assert_awaited_once_with(BASE)


@pytest.mark.asyncio
async def test_stale_image_is_not_used_and_the_repo_is_named_once():
    with structlog.testing.capture_logs() as logs:
        first, _ = await _lookup(_row(), D2)
        second, _ = await _lookup(_row(), D2)
    assert first is None and second is None
    stale = [e for e in logs if e["event"] == "qa_prebuilt_image_stale"]
    assert len(stale) == 1
    assert stale[0]["log_level"] == "warning"
    assert stale[0]["repo"] == "org/r1"
    assert stale[0]["image_tag"] == "img:t1"
    assert "build-qa-image" in stale[0]["action"]


@pytest.mark.asyncio
async def test_stale_warning_repeats_when_the_base_moves_again():
    with structlog.testing.capture_logs() as logs:
        await _lookup(_row(), D2)
        await _lookup(_row(), "sha256:" + "3" * 64)
    assert len([e for e in logs if e["event"] == "qa_prebuilt_image_stale"]) == 2


@pytest.mark.asyncio
async def test_row_with_no_recorded_digest_is_still_used_and_logged_once():
    with structlog.testing.capture_logs() as logs:
        first, resolver = await _lookup(_row(digest=None), D2)
        second, _ = await _lookup(_row(digest=None), D2)
    assert first == second == "img:t1"
    resolver.current.assert_not_awaited()  # nothing to compare — no docker call
    unknown = [e for e in logs if e["event"] == "qa_prebuilt_image_base_unknown"]
    assert len(unknown) == 1 and unknown[0]["repo"] == "org/r1"


@pytest.mark.asyncio
async def test_unresolvable_base_keeps_the_image():
    """A docker hiccup must not silently turn every QA run into a cold one."""
    tag, _ = await _lookup(_row(), None)
    assert tag == "img:t1"


@pytest.mark.asyncio
async def test_a_raising_resolver_keeps_the_image():
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=_row())
    resolver = MagicMock()
    resolver.current = AsyncMock(side_effect=RuntimeError("boom"))
    lookup = PrebakedImageLookup(image_repo=image_repo, digest_resolver=resolver)
    assert await lookup.get_tag_for_repo("org/r1") == "img:t1"


@pytest.mark.asyncio
async def test_without_a_resolver_the_lookup_behaves_as_before():
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=_row())
    assert await PrebakedImageLookup(image_repo=image_repo).get_tag_for_repo("org/r1") == "img:t1"


# ── docker-backed resolver ──────────────────────────────────────────────────
#
# The comparison must be against the REGISTRY, not the sandbox daemon's local
# copy: nothing refreshes DinD's `embry0-sandbox-qa` on its own (#334 review),
# so a local inspect returns the build-time id forever and every image reads
# FRESH — the exact case this check exists for.

QUALIFIED = "registry:5000/embry0-sandbox-qa:latest"
LOCAL = "embry0-sandbox-qa:latest"


class FakeDocker:
    """Records docker argv and answers `pull` / `image inspect`."""

    repo = "registry:5000/embry0-sandbox-qa"

    def __init__(self, *, repo_digest: str | None = D1, local_id: str = "sha256:" + "f" * 64, pull_ok: bool = True):
        self.repo_digest = repo_digest
        self.local_id = local_id
        self.pull_ok = pull_ok
        self.calls: list[list[str]] = []
        self.pulls = 0

    def _build_base_cmd(self) -> list[str]:
        return ["docker", "-H", "tcp://dind:2376"]

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        self.calls.append(cmd)
        if "pull" in cmd:
            self.pulls += 1
            if not self.pull_ok:
                raise RuntimeError("manifest unknown")
            return cmd[-1]
        if "inspect" in cmd:
            # Real docker reports RepoDigests by repository, whether the image
            # is inspected by tag or by ID.
            repo_digests = f"{self.repo}@{self.repo_digest}," if self.repo_digest else ""
            return f"{repo_digests}|{self.local_id}"
        raise AssertionError(f"unexpected docker call: {cmd}")

    @property
    def verbs(self) -> list[str]:
        return ["pull" if "pull" in c else "inspect" for c in self.calls]


@pytest.mark.asyncio
async def test_resolver_pulls_before_inspecting_a_registry_qualified_image():
    docker = FakeDocker()
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) == D1
    assert docker.verbs == ["pull", "inspect"]
    assert docker.calls[0][-2:] == ["--quiet", QUALIFIED]


@pytest.mark.asyncio
async def test_resolver_returns_the_registry_digest_not_the_local_image_id():
    """A DinD copy a rebuild behind has a stale local id but, once pulled,
    the registry digest of the current manifest."""
    docker = FakeDocker(repo_digest=D2, local_id="sha256:" + "9" * 64)
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) == D2


@pytest.mark.asyncio
async def test_resolver_uses_the_local_id_for_an_unqualified_image():
    """No registry in the name means nothing to pull from, and the local
    daemon's copy is then the only truth there is."""
    local_id = "sha256:" + "9" * 64
    docker = FakeDocker(repo_digest=None, local_id=local_id)
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(LOCAL) == local_id
    assert docker.verbs == ["inspect"]


@pytest.mark.asyncio
async def test_resolver_returns_none_when_the_pull_fails():
    docker = FakeDocker(pull_ok=False)
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) is None


@pytest.mark.asyncio
async def test_resolver_returns_none_when_a_qualified_image_has_no_registry_digest():
    """Never fall back to the local id here: comparing a local id against a
    recorded manifest digest would read as STALE for a fresh image."""
    docker = FakeDocker(repo_digest=None)
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) is None


@pytest.mark.asyncio
async def test_resolver_caches_a_failure_for_its_own_ttl():
    """#338 review: /stats polls once per row; an unreachable daemon must not
    cost a fresh pull attempt every time."""
    docker = FakeDocker(pull_ok=False)
    now = [100.0]
    resolver = BaseImageDigestResolver(DockerRuntime(docker), failure_ttl_seconds=30, clock=lambda: now[0])
    assert await resolver.current(QUALIFIED) is None
    assert await resolver.current(QUALIFIED) is None
    assert docker.pulls == 1
    now[0] += 31
    assert await resolver.current(QUALIFIED) is None
    assert docker.pulls == 2


@pytest.mark.asyncio
async def test_resolver_caches_within_the_ttl_and_refetches_after():
    docker = FakeDocker()
    now = [100.0]
    resolver = BaseImageDigestResolver(DockerRuntime(docker), ttl_seconds=60, clock=lambda: now[0])
    await resolver.current(QUALIFIED)
    await resolver.current(QUALIFIED)
    assert docker.pulls == 1
    now[0] += 61
    await resolver.current(QUALIFIED)
    assert docker.pulls == 2


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh():
    import asyncio

    docker = FakeDocker()
    resolver = BaseImageDigestResolver(DockerRuntime(docker))
    results = await asyncio.gather(*(resolver.current(QUALIFIED) for _ in range(5)))
    assert results == [D1] * 5
    assert docker.pulls == 1


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_cancel_the_refresh():
    """/stats gives up on its budget; the pull it started must still finish and
    fill the cache, or every poll would start another one."""
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    class SlowDocker(FakeDocker):
        async def run_cmd(self, cmd, timeout=60):
            if "pull" in cmd:
                started.set()
                await release.wait()
            return await super().run_cmd(cmd, timeout)

    docker = SlowDocker()
    resolver = BaseImageDigestResolver(DockerRuntime(docker))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(resolver.current(QUALIFIED), 0.05)
    assert started.is_set()
    release.set()
    await asyncio.sleep(0)  # let the shielded refresh finish
    assert await resolver.current(QUALIFIED) == D1
    assert docker.pulls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("out", ["", "not-a-digest", "|", "|md5:zz"])
async def test_resolver_returns_none_on_output_it_cannot_parse(out):
    docker = FakeDocker()

    async def odd(cmd, timeout=60):
        return cmd[-1] if "pull" in cmd else out

    docker.run_cmd = odd  # type: ignore[method-assign]
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) is None


@pytest.mark.asyncio
async def test_container_base_image_reads_name_and_id():
    docker = FakeDocker()

    async def inspect(cmd, timeout=60):
        return f"{QUALIFIED}|{D1}"

    docker.run_cmd = inspect  # type: ignore[method-assign]
    assert await container_base_image(DockerRuntime(docker), "cid-1") == (QUALIFIED, D1)


@pytest.mark.asyncio
@pytest.mark.parametrize("out", ["", "only-name", f"{QUALIFIED}|md5:zz", f"|{D1}"])
async def test_container_base_image_refuses_output_it_cannot_trust(out):
    docker = FakeDocker()

    async def inspect(cmd, timeout=60):
        return out

    docker.run_cmd = inspect  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        await container_base_image(DockerRuntime(docker), "cid-1")


@pytest.mark.asyncio
async def test_resolver_parses_the_real_dind_inspect_output():
    """Captured from the live DinD daemon on 2026-09-20. `join` raises a
    template error there (.RepoDigests decodes as []interface{}), so the
    format string uses `range`, which leaves a trailing comma."""
    real = (
        "registry:5000/embry0-sandbox-qa@sha256:"
        "e837be5cd81b945f62c4d0459994f3bc7338fcc5acf0a21604dfd2a1a32be93b,"
        "|sha256:bbee40c58e604963943dae83ddbf85362c058a04e9da4b24e42fd2b80558955f"
    )
    docker = FakeDocker()

    async def run_cmd(cmd, timeout=60):
        assert "join" not in " ".join(cmd)
        return cmd[-1] if "pull" in cmd else real

    docker.run_cmd = run_cmd  # type: ignore[method-assign]
    digest = await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED)
    assert digest == "sha256:e837be5cd81b945f62c4d0459994f3bc7338fcc5acf0a21604dfd2a1a32be93b"


@pytest.mark.asyncio
async def test_image_digest_of_the_running_bits_matches_by_repo_name():
    """The builder inspects the container's image ID, not the tag: the tag may
    have moved on by the time the bake finishes."""
    docker = FakeDocker(repo_digest=D2)
    assert await image_digest(DockerRuntime(docker), "sha256:" + "e" * 64, repo_name=QUALIFIED) == D2
    assert docker.verbs == ["inspect"]
    assert docker.calls[0][-1] == "sha256:" + "e" * 64


# ── summary (the /stats field) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_groups_active_images_by_freshness():
    image_repo = AsyncMock()
    image_repo.list_active = AsyncMock(
        return_value=[
            _row("org/fresh", "f:1", D2),
            _row("org/stale", "s:1", D1),
            _row("org/legacy", "l:1", None),
        ]
    )
    summary = await summarize_prebuilt_images(image_repo, _resolver(D2))
    assert summary == {
        "active": 3,
        "fresh": 1,
        "stale": [{"repo": "org/stale", "image_tag": "s:1"}],
        "unknown": [{"repo": "org/legacy", "image_tag": "l:1"}],
    }


@pytest.mark.asyncio
async def test_summary_gives_up_on_its_budget_rather_than_blocking_stats():
    import asyncio

    class SlowResolver:
        async def current(self, image):
            await asyncio.sleep(10)
            return D1

    image_repo = AsyncMock()
    image_repo.list_active = AsyncMock(return_value=[_row("org/a", "a:1"), _row("org/b", "b:1")])

    loop = asyncio.get_running_loop()
    started = loop.time()
    summary = await summarize_prebuilt_images(image_repo, SlowResolver(), budget_seconds=0.05)
    assert loop.time() - started < 2
    assert summary["active"] == 2
    assert summary["stale"] == []
    assert [u["repo"] for u in summary["unknown"]] == ["org/a", "org/b"]


@pytest.mark.asyncio
async def test_summary_without_dependencies_is_none():
    assert await summarize_prebuilt_images(None, None) is None


# ── digest pins (#523) ──────────────────────────────────────────────────────


@pytest.fixture
def qa_pinned():
    from embry0.execution.image_registry import clear_image_pins, install_image_pins

    install_image_pins("registry:5000", {"embry0-sandbox-qa:latest": D2})
    yield
    clear_image_pins()


@pytest.mark.asyncio
async def test_a_pinned_base_is_current_at_its_pin_without_a_pull(qa_pinned):
    """The registry tag is what a pin must not be compared against: whatever was
    pushed to it last, new sandboxes run the pinned digest."""
    docker = FakeDocker(repo_digest=D1)
    assert await BaseImageDigestResolver(DockerRuntime(docker)).current(QUALIFIED) == D2, (
        "a pinned base's current digest must be its pin, not the tag's registry digest"
    )
    assert docker.calls == [], "resolving a pinned base must not pull the tag"


@pytest.mark.asyncio
async def test_a_bake_on_the_pinned_base_reads_fresh_and_a_rebuild_reads_stale(qa_pinned):
    resolver = BaseImageDigestResolver(DockerRuntime(FakeDocker(repo_digest=D1)))
    fresh, stale = _row(digest=D2), _row(digest=D1)  # both recorded on QUALIFIED
    assert (await mod.freshness(fresh, resolver))[0] is BaseFreshness.FRESH
    assert (await mod.freshness(stale, resolver))[0] is BaseFreshness.STALE


@pytest.mark.asyncio
async def test_image_digest_matches_a_pinned_launch_name():
    """The container of a pinned launch reports `…:latest@sha256:…` as its image.
    The digest's own colon must not be read as the tag separator."""
    docker = FakeDocker(repo_digest=D2)
    pinned_name = f"{QUALIFIED}@{D2}"
    assert await image_digest(DockerRuntime(docker), "sha256:" + "e" * 64, repo_name=pinned_name) == D2
    assert (
        await image_digest(
            DockerRuntime(docker), "sha256:" + "e" * 64, repo_name=f"registry:5000/embry0-sandbox-qa@{D2}"
        )
        == D2
    )


@pytest.mark.asyncio
async def test_an_unpinned_legacy_image_is_still_used_and_named_once(qa_pinned):
    """Baked before bakes recorded their digest: kept, so QA does not go cold on
    upgrade, and logged once so the operator knows to rebuild it."""
    row = _row(tag="embry0-qa-org_r1:2026-09-01T00-00", digest=D2)
    resolver = _resolver(D2)
    with structlog.testing.capture_logs() as logs:
        assert await mod.usable_image_tag(row, resolver) == row.image_tag
        assert await mod.usable_image_tag(row, resolver) == row.image_tag
    assert [e["event"] for e in logs].count("qa_prebuilt_image_unpinned") == 1, logs


@pytest.mark.asyncio
async def test_a_pinned_bake_is_not_called_unpinned(qa_pinned):
    row = _row(tag=f"embry0-qa-org_r1:2026-10-08T00-00@{D1}", digest=D2)
    with structlog.testing.capture_logs() as logs:
        assert await mod.usable_image_tag(row, _resolver(D2)) == row.image_tag
    assert not [e for e in logs if e["event"] == "qa_prebuilt_image_unpinned"], logs


@pytest.mark.asyncio
async def test_a_stale_legacy_image_is_not_called_unpinned(qa_pinned):
    """It is not used at all, so "runs by tag until rebuilt" would be false."""
    row = _row(tag="embry0-qa-org_r1:2026-09-01T00-00", digest=D1)
    with structlog.testing.capture_logs() as logs:
        assert await mod.usable_image_tag(row, _resolver(D2)) is None
    assert not [e for e in logs if e["event"] == "qa_prebuilt_image_unpinned"], logs


@pytest.mark.asyncio
async def test_a_digest_ending_in_a_newline_is_not_trusted():
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``sha256:…\\n``
    from a runtime that does not strip its output."""
    runtime = MagicMock()
    runtime.container_image = AsyncMock(return_value=(QUALIFIED, D1 + "\n"))
    runtime.image_digests = AsyncMock(return_value=(["registry:5000/embry0-sandbox-qa@" + D1 + "\n"], D1 + "\n"))
    try:
        await container_base_image(runtime, "cid-1")
    except ValueError:
        pass
    else:
        raise AssertionError("a container image ID ending in a newline must be refused")
    assert await image_digest(runtime, "img", repo_name=QUALIFIED) is None, "a registry digest must be refused"
    assert await image_digest(runtime, "img", repo_name="embry0-sandbox-qa:latest") is None, (
        "a local ID must be refused"
    )
