"""The running watchers, one per enabled stack, kept in step with ``watcher_stacks`` without a restart (#746).

Until #746 the stacks were read once, at startup: a stack added or enabled
afterwards did not tick, one disabled or deleted kept ticking and paying for
analyses, and a changed row was not read, until the next restart, which
stops running jobs.

Two parts keep them in step now:

- each watcher reads its own row before every tick (``WatcherService.refresh``),
  so a changed setting, LogQL or tracker applies to its next tick, and a stack
  disabled or deleted ticks no more;
- :class:`WatcherFleet` looks at the table every :data:`SYNC_SECONDS` and
  starts a watcher for each stack enabled and not running, and stops the
  watcher of each stack no longer enabled. A watcher in the middle of a tick
  finishes it first. A stopped stack's metrics go
  (``LoopMetrics.watcher_stack_removed``); a started one's are seeded from
  ``watcher_runs``, as at startup.

``services`` is the one registry every reader shares (``app.state.watcher_services``:
the manual-tick API, the health check), changed in place, and ``views`` the
stacks' config views the metrics snapshot reads, as each watcher reads them now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any, Final

import structlog

from embry0.metrics.loop import NULL_METRICS, LoopMetrics

logger = structlog.get_logger(__name__)

#: How often the fleet looks at the table, in seconds: a new or re-enabled stack starts ticking within this.
SYNC_SECONDS: Final = 60.0


async def read_enabled_stacks(stacks_repo: Any) -> tuple[list[Any], list[str]]:
    """The enabled stacks, and the repositories of those whose row holds a value too deep to read (#527): each of
    those is read alone, so one leaves the others. Raises when the table cannot be read at all."""
    from embry0.storage.database import StoredValueTooDeep

    try:
        return list(await stacks_repo.list_all(enabled_only=True)), []
    except StoredValueTooDeep:
        watched, unreadable = await stacks_repo.list_readable(enabled_only=True)
        return list(watched), list(unreadable)


class _Views(Mapping[str, Any]):
    """Each running stack's config view, as its watcher reads it now: what the metrics snapshot reads."""

    def __init__(self, services: Mapping[str, Any]) -> None:
        self._services = services

    def __getitem__(self, repo: str) -> Any:
        return self._services[repo].stack_config

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._services))

    def __len__(self) -> int:
        return len(self._services)


class WatcherFleet:
    """The watchers of the enabled stacks: built, started and stopped as the table changes.

    ``build(stack)`` makes a stack's watcher; ``read_stacks()`` answers the
    enabled stacks and the repositories of those whose row cannot be read, and
    raises when the table cannot be read at all. ``run_loops`` is whether the
    scheduled loops run (``WATCHER_ENABLED``): without it the watchers are
    built for ticks started by hand, and none loops. ``on_change`` is called
    after the running set changed.
    """

    def __init__(
        self,
        *,
        build: Callable[[Any], Any],
        read_stacks: Callable[[], Awaitable[tuple[list[Any], list[str]]]],
        runs_repo: Any,
        run_loops: bool,
        background_tasks: set[asyncio.Task[Any]],
        metrics: LoopMetrics | None = None,
        on_change: Callable[[], None] | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._build = build
        self._read_stacks = read_stacks
        self._runs = runs_repo
        self._run_loops = run_loops
        self._background = background_tasks
        self._metrics = metrics or NULL_METRICS
        self._on_change = on_change
        self._sleep = sleep
        #: Each running stack's watcher, by repository.
        self.services: dict[str, Any] = {}
        self.views: Mapping[str, Any] = _Views(self.services)
        self._loops: dict[str, asyncio.Task[Any]] = {}
        #: Stopped while a tick ran: the watcher and its loop, cancelled once the tick is done.
        self._draining: dict[str, tuple[Any, asyncio.Task[Any] | None]] = {}

    async def add(self, stack: Any) -> None:
        """Build ``stack``'s watcher, seed its metrics and, with ``run_loops``, start its loop."""
        from embry0.metrics.wiring import seed_watcher_ticks

        service = self._build(stack)
        self.services[stack.repo] = service
        self._metrics.watcher_stack_cap(stack=stack.repo, cap=int(service.stack_config.watcher_max_open_proposals))
        await seed_watcher_ticks(self._metrics, self._runs, repo=stack.repo)
        if self._run_loops:
            self._start_loop(stack.repo, service)

    def _start_loop(self, repo: str, service: Any) -> None:
        task = asyncio.create_task(service.start())
        self._loops[repo] = task
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        logger.info("watcher_loop_started", stack=repo)

    def stop(self, repo: str) -> None:
        """Stop ``repo``'s watcher: no reader sees it from now on. A tick it is running is finished first."""
        service = self.services.pop(repo, None)
        task = self._loops.pop(repo, None)
        if service is None:
            return
        if service.ticking:
            self._draining[repo] = (service, task)
            logger.info("watcher_stack_draining", stack=repo)
            return
        self._retire(repo, task)

    def _retire(self, repo: str, task: asyncio.Task[Any] | None) -> None:
        if task is not None:
            task.cancel()
        self._metrics.watcher_stack_removed(repo)
        logger.info("watcher_stack_stopped", stack=repo)

    def _drain(self) -> None:
        """Cancel the loops of the stopped watchers whose tick has ended."""
        for repo, (service, task) in list(self._draining.items()):
            if not service.ticking:
                del self._draining[repo]
                self._retire(repo, task)

    async def sync(self) -> tuple[list[str], list[str]]:
        """Start the enabled stacks not running and stop the running ones no longer enabled; ``(started, stopped)``.

        A stack whose row cannot be read is neither started nor stopped: what
        runs keeps running. A table that cannot be read at all changes nothing.
        """
        self._drain()
        try:
            stacks, unreadable = await self._read_stacks()
        except Exception:  # noqa: BLE001 - nothing is stopped on a read that failed
            logger.warning("watcher_fleet_unread", exc_info=True)
            return [], []
        enabled = {stack.repo: stack for stack in stacks}
        kept = set(unreadable)
        stopped = [repo for repo in list(self.services) if repo not in enabled and repo not in kept]
        for repo in stopped:
            self.stop(repo)
        started: list[str] = []
        for repo, stack in enabled.items():
            if repo in self.services or repo in self._draining:
                continue
            try:
                await self.add(stack)
            except Exception:  # noqa: BLE001 - one stack that cannot be built leaves the others
                self.services.pop(repo, None)
                logger.error("watcher_stack_start_failed", stack=repo, exc_info=True)
                continue
            started.append(repo)
        if started or stopped:
            logger.info("watcher_stacks_synced", started=started, stopped=stopped, stacks=sorted(self.services))
            if self._on_change is not None:
                self._on_change()
        return started, stopped

    async def run(self) -> None:
        """Look at the table every :data:`SYNC_SECONDS`, for as long as the process runs."""
        while True:
            await self._sleep(SYNC_SECONDS)
            try:
                await self.sync()
            except Exception:  # noqa: BLE001 - the loop must outlive any one look
                logger.error("watcher_fleet_sync_failed", exc_info=True)
