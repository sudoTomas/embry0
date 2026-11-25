"""The unit suite's own hooks; the shared fixtures are in tests/conftest.py.

The network guard (tests/fixtures/network_guard.py, #702) is installed here, once per process, when pytest imports
this file: before the unit test modules are imported, so their imports, collection, module and session fixtures and
threads that outlive their test all run under it. The hosts it allows are read now, once.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from tests.fixtures.network_guard import Guard, Refusal, allowed_addresses

GUARD = Guard(allowed=allowed_addresses())
GUARD.install()
GUARD.stamp_threads()


def _said(refusals: list[Refusal]) -> str:
    return ", ".join(
        sorted({r.where if r.thread == "MainThread" else f"{r.where} (thread {r.thread})" for r in refusals})
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, None, None]:
    """The test running now, for every refusal while it runs; and whether it is marked ``allow_network``."""
    GUARD.current = item.nodeid
    GUARD.allowing = item.get_closest_marker("allow_network") is not None
    try:
        yield
    finally:
        GUARD.current = None
        GUARD.allowing = False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, Any, None]:
    """A test that tried to connect fails at its teardown, naming itself and every address, even if its code
    swallowed the refusal; a refusal in its setup or its fixtures' teardown counts as its own."""
    outcome = yield
    if call.when != "teardown":
        return
    refusals = GUARD.take(item.nodeid)
    if refusals:
        report = outcome.get_result()
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} tried to connect to {_said(refusals)}: the unit suite reaches no network but loopback "
            "and its test database (tests/fixtures/network_guard.py, #702)"
        )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> Generator[None, None, None]:
    """A refusal outside any test (an import, collection, a thread between tests) fails the run. A pytest-xdist
    worker says so through ``shouldfail``, which its controller reads before this hook's others run."""
    outside = GUARD.outside()
    if outside:
        message = (
            f"the unit suite reaches no network: outside any test, a connection to {_said(outside)} was refused "
            "(tests/fixtures/network_guard.py, #702)"
        )
        if getattr(session.config, "workerinput", None) is not None:
            session.shouldfail = message
        else:
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_line(message, red=True)
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    yield
