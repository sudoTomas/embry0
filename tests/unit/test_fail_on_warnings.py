"""``--fail-on-warnings`` (tests/conftest.py): a run whose tests passed but warned fails (#336)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tests import conftest

WORKFLOW = Path(conftest.__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


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
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["backend"]["steps"]
    runs = [str(step.get("run", "")) for step in steps if "pytest tests/unit" in str(step.get("run", ""))]
    assert runs and all("--fail-on-warnings" in run for run in runs), f"CI's unit suite must fail on warnings: {runs}"
