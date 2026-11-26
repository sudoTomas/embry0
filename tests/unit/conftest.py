"""The unit suite's own hooks; the shared fixtures are in tests/conftest.py.

The network guard (tests/fixtures/network_guard.py, #702) is installed here, once per process, when pytest imports
this file: before the unit test modules are imported, so their imports, collection, module and session fixtures and
threads that outlive their test all run under it. The hosts it allows are read now, once. Before and after each test
the hooks check the guard is still in place: a test that leaves it changed fails, and it is put back.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from tests.fixtures.network_guard import Guard, Refusal, allowed_addresses

GUARD = Guard(allowed=allowed_addresses())
GUARD.install()
GUARD.stamp_threads()
#: What was found changed and put back outside any test (an import, collection, between tests).
CHANGED_OUTSIDE: list[str] = []
#: Why the run fails though its tests passed, said in its summary.
_OUTSIDE = pytest.StashKey[str]()
#: The summary's word for it: "4 passed, 1 refused outside a test".
OUTSIDE_STAT = "refused outside a test"


def _said(refusals: list[Refusal]) -> str:
    return ", ".join(
        sorted({r.where if r.thread == "MainThread" else f"{r.where} (thread {r.thread})" for r in refusals})
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, None, None]:
    """The test running now, for every refusal while it runs; and whether it is marked ``allow_network``. A guard
    found changed before the test starts was changed outside any test: it is put back, and the run fails."""
    CHANGED_OUTSIDE.extend(GUARD.changed())
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
    swallowed the refusal; a refusal in its setup or its fixtures' teardown counts as its own. So does a test that
    leaves the guard changed, which would let every later test through: it is put back."""
    outcome = yield
    if call.when != "teardown":
        return
    said = []
    refusals = GUARD.take(item.nodeid)
    if refusals:
        said.append(
            f"{item.nodeid} tried to connect to {_said(refusals)}: the unit suite reaches no network but loopback "
            "and its test database (tests/fixtures/network_guard.py, #702)"
        )
    changed = GUARD.changed()
    if changed:
        said.append(
            f"{item.nodeid} left {', '.join(changed)} changed, which the unit suite's network guard patched: every "
            "later test would reach the network. It is put back; undo the change before the test ends, as "
            "monkeypatch does (tests/fixtures/network_guard.py, #702)"
        )
    if said:
        report = outcome.get_result()
        report.outcome = "failed"
        report.longrepr = "\n".join(said)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> Generator[None, None, None]:
    """A refusal or a change to the guard outside any test (an import, collection, a thread between tests) fails
    the run. A pytest-xdist worker says so through ``shouldfail``, which its controller reads before this hook's
    others run; a run of its own says so in its summary (``pytest_terminal_summary``)."""
    CHANGED_OUTSIDE.extend(GUARD.changed())
    outside = GUARD.outside()
    said = []
    if outside:
        said.append(f"a connection to {_said(outside)} was refused")
    if CHANGED_OUTSIDE:
        said.append(f"{', '.join(sorted(set(CHANGED_OUTSIDE)))} was changed and put back")
    if said:
        message = (
            f"the unit suite reaches no network: outside any test, {'; and '.join(said)} "
            "(tests/fixtures/network_guard.py, #702)"
        )
        if getattr(session.config, "workerinput", None) is not None:
            session.shouldfail = message
        else:
            session.config.stash[_OUTSIDE] = message
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.stats.setdefault(OUTSIDE_STAT, []).append(message)
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    yield


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """The reason a run whose tests passed failed, on lines of its own above the summary line."""
    message = terminalreporter.config.stash.get(_OUTSIDE, None)
    if message is not None:
        terminalreporter.write_sep("=", "network guard", red=True)
        terminalreporter.write_line(message, red=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    GUARD.remove_children()


@pytest.fixture(autouse=True)
def _no_model_server_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where embry0 connects to an operator's model server it resolves the host first (ADR 0032, ``server_address``).
    No unit test reaches real DNS that way: a name answers "not known" unless the test names its addresses with
    ``tests.fixtures.model_servers.fake_dns``."""
    from tests.fixtures.model_servers import fake_dns

    fake_dns(monkeypatch, {})
