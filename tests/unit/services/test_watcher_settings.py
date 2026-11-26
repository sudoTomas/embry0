"""The watcher reads the settings document's ``watcher`` section on every tick (#738).

Whether a stack's drafts are auto-accepted, the bar they must clear, and how
long a signature found clean is held were the ``WATCHER_*`` variables, read
once at startup. They are the deployment's section now: a change reaches the
next tick without a restart, a stack's own value of a key still wins, and a
section that cannot be read is the one last read.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import embry0.api.app as app_module
from embry0.services.watcher_stacks import StackConfig, WatcherStack
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from tests.unit.services.test_watcher import _config, _service


def _model(**values: Any) -> Any:
    section = CATALOG.get("watcher")
    if section is None:
        pytest.fail("the settings document has no watcher section: the watcher's policy is read from the environment")
    return section.validate(values)


def _stack(**settings: Any) -> StackConfig:
    return StackConfig(_config(), WatcherStack(repo="owner/repo", logql='{app="x"}', settings=settings))


def _watcher(reads: list[Any], **stack: Any) -> Any:
    """A watcher with no log lines whose section reader answers ``reads`` in turn (an exception is raised)."""
    svc = _service(config=_stack(**stack), lines=[])
    svc._watcher_settings = AsyncMock(side_effect=reads)
    svc._auto_acceptor = AsyncMock(return_value=[])
    return svc


@pytest.mark.asyncio
async def test_a_change_to_the_section_reaches_the_next_tick_without_a_restart() -> None:
    svc = _watcher([_model(), _model(auto_accept=True, auto_accept_max_per_day=4)])

    await svc.run_once()
    assert svc._auto_acceptor.await_count == 0, "auto-accept is off in the section the first tick read"
    await svc.run_once()
    assert svc._auto_acceptor.await_count == 1, "the section turned it on, and the next tick read it"
    assert svc._auto_acceptor.await_args.args[1].max_per_day == 4, "the bar is the section's as that tick read it"


@pytest.mark.asyncio
async def test_a_section_that_cannot_be_read_is_the_one_last_read() -> None:
    svc = _watcher([_model(auto_accept=True), RuntimeError("database is down"), _model()])

    await svc.run_once()
    await svc.run_once()
    assert svc._auto_acceptor.await_count == 2, "a blip keeps the section last read: auto-accept stays on"
    await svc.run_once()
    assert svc._auto_acceptor.await_count == 2, "read again, the section turned it off"


@pytest.mark.asyncio
async def test_before_any_read_works_the_section_s_defaults_apply_and_a_stack_s_own_value_still_does() -> None:
    off = _watcher([RuntimeError("database is down")])
    await off.run_once()
    assert off._auto_acceptor.await_count == 0, "the defaults: auto-accept off"

    own = _watcher([RuntimeError("database is down")], auto_accept=True)
    await own.run_once()
    assert own._auto_acceptor.await_count == 1, "a stack that turned it on for itself keeps it on"


@pytest.mark.asyncio
async def test_a_watcher_without_a_reader_reads_the_section_s_defaults() -> None:
    svc = _service(config=_stack(), lines=[])
    svc._auto_acceptor = AsyncMock(return_value=[])
    await svc.run_once()
    assert svc._auto_acceptor.await_count == 0


def _watcher_service_call() -> ast.Call:
    source = pathlib.Path(app_module.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "WatcherService":
            return node
    pytest.fail("no WatcherService(...) construction found in embry0/api/app.py")


def test_each_watcher_reads_the_deployment_s_section() -> None:
    """The reader is optional in the signature: a watcher built without it reads the defaults for good."""
    passed = {kw.arg: ast.unparse(kw.value) for kw in _watcher_service_call().keywords if kw.arg}
    assert passed.get("watcher_settings") == "_watcher_settings(app)", (
        f"each watcher must read the settings document's watcher section, not {passed.get('watcher_settings')!r}"
    )


@pytest.mark.asyncio
async def test_the_reader_reads_the_deployment_s_section_now() -> None:
    reader_for = getattr(app_module, "_watcher_settings", None)
    assert reader_for is not None, "app.py has no reader of the watcher section"
    rows = [
        {
            "scope": "deployment",
            "data": {"watcher": {"auto_accept_max_per_day": 5}},
            "generation": 1,
            "updated_at": None,
        }
    ]

    class Store:
        async def snapshot(self) -> Any:
            return build_snapshot(CATALOG, rows, token=(), log=False)

    read = reader_for(SimpleNamespace(state=SimpleNamespace(settings=Store())))
    assert (await read()).auto_accept_max_per_day == 5
    rows[0] = {
        "scope": "deployment",
        "data": {"watcher": {"auto_accept_max_per_day": 1}},
        "generation": 2,
        "updated_at": None,
    }
    assert (await read()).auto_accept_max_per_day == 1, "read on every call"
