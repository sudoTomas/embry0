"""A ready check's ``expect_body_regex`` is searched for in time linear in the body, checked when it is written.

A test setup's ready checks are polled until the app answers, and each answer's
body (up to 8 KiB, written by the app under test) was searched with Python's
``re``, whose time depends on the pattern: for ``(a+)+$`` it doubles with each
character of a body it almost matches. The pattern is now searched with the
linear-time engine the watcher's masks use (``embry0.services.linear_regex``),
with Python's meaning; one that engine cannot run is refused when the setup is
written, and one stored before that check fails the boot at once, saying why,
instead of raising.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.boot import run_boot_phase
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

_V2 = """version: 2
qa_required: auto
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  ready_checks:
    - http: "http://localhost:3000/health"
      expect_status: 200
      expect_body_regex: {pattern}
apps:
  app:
    boot_command: "npm run dev"
    frontend_url: "http://localhost:3000"
"""


def _setup(pattern: str) -> str:
    return _V2.format(pattern=json.dumps(pattern))


def _docker(*bodies: str) -> Any:
    """A sandbox whose boot command starts and whose probes answer 200 with each body in turn, the last for good."""
    docker = MagicMock()
    answers = ["boot_command_backgrounded", *(f"STATUS=200\n---\n{body}" for body in bodies)]

    async def run_cmd(*_args: Any, **_kwargs: Any) -> str:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    return docker


def _qa_yaml(pattern: str, boot_timeout: float = 5) -> dict[str, Any]:
    return {
        "startup": {
            "command": "npm run dev",
            "ready_checks": [
                {"http": "http://localhost:3000/health", "expect_status": 200, "expect_body_regex": pattern}
            ],
            "boot_timeout_seconds": boot_timeout,
        }
    }


# ---- refused at write -----------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        (r"(?<=status: )UP", "look-ahead and look-behind"),
        (r"(UP)\1", "back-reference"),
        (r"(?>UP)", "atomic groups"),
        (r"UP{101}", "more than 100 copies"),
        (r"\w{8}UP", "too large"),
        (r"\"status\":\"(UP", "is not a Python regular expression"),
    ],
)
def test_a_body_pattern_the_linear_engine_cannot_search_is_refused_when_written(pattern: str, why: str) -> None:
    with pytest.raises(ValueError) as refused:
        parse_qa_yaml_v2(_setup(pattern), writing=True)
    assert "expect_body_regex" in str(refused.value) and why in str(refused.value), str(refused.value)


@pytest.mark.parametrize("pattern", [r'"status":"UP"', r"(a+)+$", r"\d*", "", r"(?i)ready|ok"])
def test_a_body_pattern_it_can_search_is_stored_and_one_stored_before_reads(pattern: str) -> None:
    """``(a+)+$`` is searched in linear time like any other; a pattern matching an empty string matches every body."""
    assert parse_qa_yaml_v2(_setup(pattern), writing=True)
    assert parse_qa_yaml_v2(_setup(r"(?<=status: )UP")), "a setup stored before the check still reads"


# ---- searched at boot -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pattern", "bodies", "attempts"),
    [
        (r'"status":"UP"', ['{"status":"DOWN"}', '{"status":"UP"}'], 2),
        (r"(?i)^ready$", ["starting", "READY"], 2),
        (r"\d*", ["anything"], 1),
    ],
)
async def test_the_boot_searches_the_body_as_re_would(pattern: str, bodies: list[str], attempts: int) -> None:
    result = await run_boot_phase(qa_yaml=_qa_yaml(pattern), container_id="C", docker=_docker(*bodies), sleep_seconds=0)
    assert (result.outcome, result.attempts) == ("passed", attempts), result
    assert [bool(re.search(pattern, body)) for body in bodies] == [n == len(bodies) - 1 for n in range(len(bodies))]


@pytest.mark.asyncio
@pytest.mark.parametrize(("pattern", "why"), [(r"(?<=status: )UP", "look-ahead"), ("(UP", "not a Python regular")])
async def test_a_stored_body_pattern_it_cannot_search_fails_the_boot_at_once_saying_why(pattern: str, why: str) -> None:
    docker = _docker('{"status":"UP"}')
    result = await run_boot_phase(
        qa_yaml=_qa_yaml(pattern, boot_timeout=3), container_id="C", docker=docker, sleep_seconds=0
    )
    assert result.outcome == "startup_failed" and result.attempts == 0, result
    assert len(result.failed_checks) == 1 and result.failed_checks[0].startswith("http://localhost:3000/health: "), (
        result
    )
    assert why in result.failed_checks[0] and why in result.error_message, result
    assert docker.run_cmd.await_count == 0, "the boot command was started for a setup no check of which can pass"


_BOOT_IN_A_CHILD = """
import asyncio, json, sys, time
from unittest.mock import AsyncMock, MagicMock
from embry0.workflows.qa.boot import run_boot_phase
pattern, body = sys.argv[1], sys.argv[2]
docker = MagicMock()
calls = []
async def answer(*args, **kwargs):
    calls.append(1)
    return "boot_command_backgrounded" if len(calls) == 1 else f"STATUS=200\\n---\\n{body}"
docker.run_cmd = AsyncMock(side_effect=answer)
docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
qa_yaml = {"startup": {"command": "x", "ready_checks": [{"http": "http://localhost:3000/", "expect_status": 200, "expect_body_regex": pattern}], "boot_timeout_seconds": 0.5}}
started = time.perf_counter()
result = asyncio.run(run_boot_phase(qa_yaml=qa_yaml, container_id="C", docker=docker, sleep_seconds=0.1))
print(json.dumps({"seconds": time.perf_counter() - started, "outcome": result.outcome}))
"""

_DEADLINE_SECONDS = 20.0


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a|aa)+$", r"(\w+\s?)+$"])
def test_a_body_a_backtracking_pattern_almost_matches_is_searched_at_once(pattern: str) -> None:
    """In a child process, stopped at the deadline: Python's re took longer than any boot on 30 of these characters.
    The body is the probe's most, 8 KiB, the pattern never matching it, so the boot polls out its half-second."""
    body = "a" * 8191 + "!"
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", _BOOT_IN_A_CHILD, pattern, body],
            capture_output=True,
            text=True,
            timeout=_DEADLINE_SECONDS,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"a boot whose ready check searches {pattern!r} in an 8 KiB body was not over in {_DEADLINE_SECONDS:.0f}s "
            f"(stopped after {time.monotonic() - started:.1f}s): the body decides how long it takes"
        )
    assert done.returncode == 0, done.stderr[-500:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["outcome"] == "timeout" and got["seconds"] < 3.0, got
