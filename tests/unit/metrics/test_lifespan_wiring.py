"""The production wiring in ``embry0/api/app.py`` is what actually collects (#285).

Every other test here builds the app with a no-op lifespan and calls the
helpers directly, which leaves the six lines in ``app.py`` that hand the facade
to its producers untested — deleting any of them keeps the suite green, and
four PRs are about to rebase over that file.

So this runs the REAL ``lifespan`` against the test database, with only the
container runtime replaced (there is no Docker in a unit test), and asserts
the wiring it leaves behind.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.metrics.snapshot import LoopSnapshot
from embry0.orchestration import channels
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.watcher_runs import WatcherRunsRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

_LAST_TICK = datetime(2026, 9, 20, 6, 30, tzinfo=UTC)


def _runtime_stub(**sync_methods: Any) -> MagicMock:
    """A container-runtime double: every method awaits, except the named ones."""
    stub = MagicMock()
    for name, value in sync_methods.items():
        setattr(stub, name, MagicMock(return_value=value))
    return stub


@pytest.fixture
def stub_container_runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace Docker/DinD. ``lifespan`` imports these inside the function, so
    they are patched on their own modules, not on ``embry0.api.app``."""
    docker = _runtime_stub(_build_base_cmd=["docker"])
    docker.assert_sandbox_networks_or_die = AsyncMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.probe_egress_enforcement = AsyncMock(return_value=None)

    image_manager = MagicMock()
    image_manager.ensure_image = AsyncMock(return_value=False)
    image_manager.check_proxy_image_present = AsyncMock(return_value=True)

    proxy_manager = MagicMock()
    proxy_manager.start = AsyncMock()
    proxy_manager.stop = AsyncMock()

    reaper = MagicMock()
    reaper.start = MagicMock()
    reaper.stop = AsyncMock()

    classes: dict[str, MagicMock] = {}
    for module, name, value in (
        ("embry0.execution.docker_client", "DockerClient", docker),
        ("embry0.execution.proxy.manager", "ProxyManager", proxy_manager),
        ("embry0.execution.sandbox_manager", "SandboxManager", MagicMock()),
        ("embry0.execution.image_manager", "SandboxImageManager", image_manager),
        ("embry0.execution.image_manager", "ContainerReaper", reaper),
    ):
        classes[name] = MagicMock(return_value=value)
        monkeypatch.setattr(f"{module}.{name}", classes[name])
    return classes


_STACK = "acme/backend"


@pytest.fixture
async def booted(db_with_migrations: DatabasePool, stub_container_runtime: dict[str, MagicMock]) -> AsyncIterator[Any]:
    """The app after the real lifespan has run. Every background loop is off:
    this asserts on wiring, not on behaviour."""
    await db_with_migrations.execute("DELETE FROM watcher_runs")
    await db_with_migrations.execute("DELETE FROM watcher_stacks")
    await WatcherRunsRepository(db_with_migrations).create(
        window_start=_LAST_TICK - timedelta(hours=1), window_end=_LAST_TICK, action="no_issue", repo=_STACK
    )
    await db_with_migrations.execute("UPDATE watcher_runs SET created_at = $1", _LAST_TICK)

    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        # #378: a watcher exists per watched stack. These two seed one at
        # startup, so the wiring below has a watcher to assert on.
        watcher_repo=_STACK,
        watcher_logql='{compose_project="acme"}',
        watcher_linear_team_id="team-1",
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
    )
    channels.set_fold_observer(None)
    app = create_app(config)
    async with lifespan(app):
        yield app
    channels.set_fold_observer(None)


async def test_the_lifespan_hands_the_facade_to_every_producer(booted: Any) -> None:
    metrics = booted.state.loop_metrics
    assert metrics is not None
    assert booted.state.jobs_repo._metrics is metrics
    assert booted.state.traces_repo._metrics is metrics
    assert booted.state.issue_executor._metrics is metrics, "the executor counts persist failures (plan B Task 1)"
    assert booted.state.healing_cases._metrics is metrics
    assert booted.state.watcher_services, "the seeded stack must have a watcher"
    for service in booted.state.watcher_services.values():
        assert service._metrics is metrics
    assert booted.state.linear_reconciler._metrics is metrics
    assert booted.state.agent_runner._on_stale_image == metrics.sandbox_runner_stale_image
    assert channels._fold_observer is not None
    reaper = booted.state.container_reaper
    assert reaper.on_job_finished == booted.state.issue_executor._announce_finished, (
        "the paused-job reaper announces job_finished through the executor (plan B Task 3)"
    )


async def test_the_lifespan_hands_one_runtime_to_the_sandbox_manager(
    booted: Any, stub_container_runtime: dict[str, MagicMock]
) -> None:
    """#268: the runtime is chosen once, and the SandboxManager gets that one.

    ``runtime`` is optional on the manager (unit tests build it from a bare
    client), so a lifespan that stopped passing it would still boot, with a
    manager on a runtime of its own instead of the chosen one."""
    from embry0.execution.runtime import DockerRuntime

    runtime = booted.state.runtime
    assert isinstance(runtime, DockerRuntime)
    docker = stub_container_runtime["DockerClient"].return_value
    assert runtime.client is docker
    assert booted.state.docker is docker
    _, kwargs = stub_container_runtime["SandboxManager"].call_args
    assert kwargs.get("runtime") is runtime, "the lifespan must pass the chosen runtime to SandboxManager"
    # The startup network check goes through the runtime to the same client.
    docker.assert_sandbox_networks_or_die.assert_awaited_once()


async def test_the_lifespan_builds_the_db_gauge_snapshot(booted: Any) -> None:
    assert isinstance(booted.state.loop_snapshot, LoopSnapshot)

    await booted.state.loop_snapshot.refresh()

    registry = booted.state.loop_metrics.registry
    # Proof the sources are wired to real repositories, not just constructed.
    assert registry.get_sample_value("embry0_loop_snapshot_last_success_timestamp_seconds") > 0
    assert registry.get_sample_value("embry0_jobs_in_flight", {"status": "pending"}) is not None
    assert registry.get_sample_value("embry0_watcher_open_proposals", {"stack": _STACK}) == 0
    assert registry.get_sample_value("embry0_linear_reconciler_daily_dispatches") is not None


async def test_the_lifespan_seeds_the_tick_gauges_from_watcher_runs(booted: Any) -> None:
    registry = booted.state.loop_metrics.registry
    # #378: seeded per watched stack.
    assert (
        registry.get_sample_value("embry0_watcher_last_tick_timestamp_seconds", {"stack": _STACK})
        == _LAST_TICK.timestamp()
    )
    assert (
        registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": _STACK})
        == _LAST_TICK.timestamp()
    )
    assert (
        registry.get_sample_value("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": _STACK})
        == _LAST_TICK.timestamp()
    )


async def test_the_lifespan_publishes_the_configured_caps(booted: Any) -> None:
    registry = booted.state.loop_metrics.registry
    assert registry.get_sample_value("embry0_watcher_enabled") == 0
    assert registry.get_sample_value("embry0_linear_reconciler_enabled") == 0
    assert (
        registry.get_sample_value("embry0_watcher_open_proposals_cap", {"stack": _STACK})
        == booted.state.config.watcher_max_open_proposals
    )


_SMALL_STACK = "acme/widgets"


async def test_the_cap_gauges_are_each_stacks_own_numbers(
    db_with_migrations: DatabasePool, stub_container_runtime: dict[str, MagicMock]
) -> None:
    """#448: the cap is enforced per stack and a stack may override it, but
    both gauges were one global number. A stack full at 2 of 2 beside an empty
    one read 2 against 3 — blind, and no cap alert; one at 2 of 3 beside one at
    1 of 2 read 3 against 3 — an alert, and nothing capped."""
    from embry0.services.healing.service import TrackerRef
    from embry0.services.watcher_stacks import WatcherStack
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    db = db_with_migrations
    await db.execute("DELETE FROM watcher_runs")
    await db.execute("DELETE FROM watcher_stacks")
    await db.execute("TRUNCATE TABLE healing_cases CASCADE")
    stacks = WatcherStacksRepository(db)
    await stacks.upsert(WatcherStack(repo=_STACK, logql='{compose_project="acme"}', tracker="linear"))
    await stacks.upsert(
        WatcherStack(
            repo=_SMALL_STACK,
            logql='{compose_project="widgets"}',
            tracker="github",
            settings={"max_open_proposals": 2, "proposal_window_days": 3},
        )
    )
    config = Embry0Config(
        _env_file=None,
        database_url=db._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
    )
    channels.set_fold_observer(None)
    app = create_app(config)
    async with lifespan(app):
        cases = app.state.healing_cases
        await cases.open_case(repo=_STACK, origin="watcher", title="a", tracker=TrackerRef("linear", "ENG-1"))
        for key in ("11", "12"):
            await cases.open_case(repo=_SMALL_STACK, origin="watcher", title=key, tracker=TrackerRef("github", key))
        # Outside the small stack's 3-day window, inside the global 7: its own window decides.
        aged = await cases.open_case(
            repo=_SMALL_STACK, origin="watcher", title="aged", tracker=TrackerRef("github", "13")
        )
        await db.execute("UPDATE healing_cases SET detected_at = NOW() - INTERVAL '5 days' WHERE id = $1", aged["id"])
        await app.state.loop_snapshot.refresh()

        registry = app.state.loop_metrics.registry

        def gauge(name: str, stack: str) -> float | None:
            return registry.get_sample_value(name, {"stack": stack})

        assert (gauge("embry0_watcher_open_proposals", _STACK), gauge("embry0_watcher_open_proposals_cap", _STACK)) == (
            1,
            config.watcher_max_open_proposals,
        )
        assert (
            gauge("embry0_watcher_open_proposals", _SMALL_STACK),
            gauge("embry0_watcher_open_proposals_cap", _SMALL_STACK),
        ) == (2, 2)
        # No unlabelled series is left for an old rule to read a sum from.
        assert registry.get_sample_value("embry0_watcher_open_proposals") is None
        assert registry.get_sample_value("embry0_watcher_open_proposals_cap") is None
        # The gauge is the number the stack's own tick decides on.
        for repo, service in app.state.watcher_services.items():
            window = service._config.watcher_proposal_window_days  # noqa: SLF001
            assert gauge("embry0_watcher_open_proposals", repo) == await service._open_draft_count(window)  # noqa: SLF001
            assert gauge("embry0_watcher_open_proposals_cap", repo) == service._config.watcher_max_open_proposals  # noqa: SLF001


async def test_the_lifespan_starts_the_volume_reaper_with_its_state_repo(
    db_with_migrations: DatabasePool, stub_container_runtime: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reaper starts before ``_init_app_state`` fills app.state, so it
    must not read its state repository from there: with the reaper on (the
    production default) the lifespan raised AttributeError and the
    orchestrator crash-looped on boot."""
    from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository

    volume_reaper = MagicMock()
    volume_reaper.return_value.stop = AsyncMock()
    monkeypatch.setattr("embry0.cache.volume_reaper.VolumeReaper", volume_reaper)
    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=True,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
    )
    channels.set_fold_observer(None)
    app = create_app(config)
    async with lifespan(app):
        assert app.state.volume_reaper is volume_reaper.return_value
    channels.set_fold_observer(None)

    state_repo = volume_reaper.call_args.kwargs["state_repo"]
    assert isinstance(state_repo, QAVolumeStateRepository), f"reaper got state_repo={state_repo!r}"
