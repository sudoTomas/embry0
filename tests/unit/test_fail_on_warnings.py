"""``--fail-on-warnings`` (tests/conftest.py): a run whose tests passed but warned fails (#336)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tests import conftest

ROOT = Path(conftest.__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class _Reporter:
    def __init__(self, warnings: int) -> None:
        self.stats: dict[str, list[Any]] = {"warnings": [object()] * warnings} if warnings else {}
        self.lines: list[str] = []

    def write_line(self, line: str, **_markup: Any) -> None:
        self.lines.append(line)


def _finish(*, flag: bool, warnings: int, status: int = pytest.ExitCode.OK, worker: bool = False) -> tuple[int, Any]:
    reporter = _Reporter(warnings)
    config = SimpleNamespace(
        getoption=lambda name, default=None: flag if name == "--fail-on-warnings" else default,
        pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter if name == "terminalreporter" else None),
    )
    if worker:
        config.workerinput = {"workerid": "gw0"}
    session = SimpleNamespace(config=config, exitstatus=status)
    conftest.pytest_sessionfinish(session, status)  # type: ignore[arg-type]
    return session.exitstatus, reporter


def test_a_run_that_passed_but_warned_fails_under_the_flag() -> None:
    status, reporter = _finish(flag=True, warnings=1)
    assert status == pytest.ExitCode.TESTS_FAILED, "a passing run that warned must fail under --fail-on-warnings"
    assert reporter.lines and "warned 1 time" in reporter.lines[0], reporter.lines


@pytest.mark.parametrize(
    ("flag", "warnings", "status", "worker"),
    [
        (False, 3, pytest.ExitCode.OK, False),  # without the flag, warnings are only reported
        (True, 0, pytest.ExitCode.OK, False),  # no warning, nothing to fail
        (True, 3, pytest.ExitCode.TESTS_FAILED, False),  # a failed run keeps its own status
        (True, 3, pytest.ExitCode.OK, True),  # a worker leaves the decision to the controller
    ],
)
def test_the_flag_changes_nothing_else(flag: bool, warnings: int, status: int, worker: bool) -> None:
    assert _finish(flag=flag, warnings=warnings, status=status, worker=worker)[0] == status


def test_ci_runs_the_unit_suite_with_the_flag() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"].values()
    steps = [step for job in jobs for step in job.get("steps", [])]
    runs = [str(step.get("run", "")) for step in steps if " tests/unit/ " in str(step.get("run", ""))]
    assert runs and all("--fail-on-warnings" in run for run in runs), f"CI's unit suite must fail on warnings: {runs}"


#: Tests that leave a resource for the collector to find open, each of which Python reports with a
#: ``ResourceWarning`` it ignores by default (#708).
_LEAKS = {
    "an HTTP session": (
        "import asyncio\nimport aiohttp\n\n\ndef test_leaks():\n"
        "    async def leak():\n        aiohttp.ClientSession()\n\n    asyncio.run(leak())\n"
    ),
    "a socket": "import socket\n\n\ndef test_leaks():\n    socket.socket()\n",
    "a file": "def test_leaks():\n    open(__file__)\n",
}

_CLOSES = (
    "import asyncio\nimport aiohttp\n\n\ndef test_closes():\n"
    "    async def close():\n        async with aiohttp.ClientSession():\n            pass\n\n"
    "    asyncio.run(close())\n"
)


def _pytest_of(tmp_path: Path, test: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """A pytest of its own over one test file, under this suite's conftest and ``--fail-on-warnings``,
    as CI runs the unit suite; nothing from the environment or a configuration file changes it."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_probe.py").write_text(test)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
         "--rootdir", str(tmp_path), "--fail-on-warnings", *args, str(tmp_path / "test_probe.py")],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix:///nonexistent.sock", **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )  # fmt: skip


@pytest.mark.parametrize("leak", sorted(_LEAKS))
def test_a_test_that_leaves_a_resource_open_fails_the_run(tmp_path: Path, leak: str) -> None:
    """Python ignores ``ResourceWarning`` unless told otherwise, so ``--fail-on-warnings`` never saw an
    unclosed session, socket or file (#708): the conftest turns it on."""
    done = _pytest_of(tmp_path, _LEAKS[leak])
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, f"a test that leaves {leak} open must fail the run:\n{out}"
    assert "1 passed, 1 warning" in out and "ResourceWarning" in out, out
    assert "--fail-on-warnings: the tests warned 1 time(s)" in out, out


def test_a_leak_in_a_pytest_xdist_worker_fails_the_run_too(tmp_path: Path) -> None:
    """CI runs the suite in workers; each turns the warning on for itself and the controller counts it.
    The database is unreachable on purpose: a worker that cannot create its own runs on."""
    done = _pytest_of(
        tmp_path, _LEAKS["an HTTP session"], "-n", "2", TEST_DATABASE_URL="postgresql://probe@127.0.0.1:1/probe"
    )
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, f"a worker's leak must fail the run:\n{out}"
    assert "Unclosed client session" in out and "--fail-on-warnings: the tests warned 1 time(s)" in out, out


def test_a_test_that_closes_what_it_opens_passes(tmp_path: Path) -> None:
    done = _pytest_of(tmp_path, _CLOSES)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.OK, out
    assert "1 passed in" in out, out


def test_a_test_may_still_say_why_it_leaves_a_resource_open(tmp_path: Path) -> None:
    """The warning is on in pytest's own filters, so a ``filterwarnings`` mark, which a reviewer reads with
    its reason, still outranks it; the configuration file cannot (tests/unit/test_ci_workflow.py)."""
    marked = _LEAKS["a socket"].replace(
        "def test_leaks", "import pytest\n\n\n@pytest.mark.filterwarnings('ignore::ResourceWarning')\ndef test_leaks"
    )
    done = _pytest_of(tmp_path, marked)
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.OK, out
    assert "1 passed in" in out, out


@pytest.mark.parametrize(
    "silence",
    [
        'pytestconfig.addinivalue_line("filterwarnings", "ignore::ResourceWarning")',
        'pytestconfig.known_args_namespace.pythonwarnings = ["ignore::ResourceWarning"]',
    ],
    ids=["filterwarnings", "-W"],
)
def test_a_test_that_silences_warnings_for_later_tests_fails_and_they_are_put_back(
    tmp_path: Path, silence: str
) -> None:
    """A test could add a filter every later test in its process runs with; it fails, naming the change, and the
    next test's leak is still counted."""
    done = _pytest_of(
        tmp_path,
        f"import socket\n\n\ndef test_a_silences(pytestconfig):\n    {silence}\n\n\n"
        "def test_b_leaks():\n    socket.socket()\n",
        "-p",
        "no:randomly",
    )
    out = done.stdout + done.stderr
    assert "test_probe.py::test_a_silences changed the warning filters every later test runs with" in out, (
        f"a test that silences warnings for every later test must fail:\n{out}"
    )
    assert "1 warning" in out and "ResourceWarning" in out, f"the next test's leak must still be counted:\n{out}"
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, out


def test_a_leak_held_in_a_reference_cycle_by_the_last_test_is_reported(tmp_path: Path) -> None:
    """Only the collector frees what a cycle holds; the process would end first, and the warning go unseen."""
    done = _pytest_of(
        tmp_path,
        "def test_leaks_in_a_cycle():\n    held = {'file': open(__file__)}\n    held['self'] = held\n",
    )
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.TESTS_FAILED, f"a leak in a cycle must fail the run:\n{out}"
    assert "1 passed, 1 warning" in out and "ResourceWarning" in out, out
