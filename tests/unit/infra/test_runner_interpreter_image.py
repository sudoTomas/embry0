"""Both sandbox images install the agent runner's interpreter as one the agent user may run but not read.

Spec 2026-11-23, an agent run's own result: the kernel starts a program its
user cannot read as a process no other process of that user can open, from
its first instruction. The orchestrator starts the runner on it
(``RUNNER_INTERPRETER``); CI's QA image smoke checks that it starts sealed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from embry0.execution import agent_runner
from embry0.execution.dockerfile_sources import instructions

ROOT = Path(__file__).resolve().parents[3]
_INTERPRETER = "/usr/local/bin/embry0-runner-python"
_INSTALL = f'RUN install -o root -g root -m 0711 "$(readlink -f /usr/local/bin/python3)" {_INTERPRETER}'
#: The stage every sandbox image the runner runs in starts from: the whole QA file, the slim file's ``base``.
_STAGES = {"Dockerfile.sandbox": "base", "Dockerfile.sandbox.qa": None}


def _stage(name: str) -> list[str]:
    steps = instructions((ROOT / "infra" / name).read_text())
    stage = _STAGES[name]
    if stage is None:
        return steps
    start = next(i for i, step in enumerate(steps) if step.startswith("FROM ") and step.endswith(f" AS {stage}"))
    end = next((i for i in range(start + 1, len(steps)) if steps[i].startswith("FROM ")), len(steps))
    return steps[start:end]


def test_the_orchestrator_starts_the_runner_on_that_interpreter() -> None:
    assert getattr(agent_runner, "RUNNER_INTERPRETER", None) == _INTERPRETER, "the runner's interpreter moved"
    assert agent_runner.RUNNER_COMMAND[0] == _INTERPRETER, f"the runner starts on {agent_runner.RUNNER_COMMAND[0]}"


@pytest.mark.parametrize("name", sorted(_STAGES))
def test_the_image_installs_the_runner_s_interpreter_execute_only_as_root(name: str) -> None:
    steps = _stage(name)

    assert _INSTALL in steps, f"{name} does not install the runner's interpreter execute-only: {_INSTALL}"
    assert steps.index(_INSTALL) < steps.index("USER agent"), f"{name} installs it after it switches to the agent"


@pytest.mark.parametrize("name", sorted(_STAGES))
def test_the_build_fails_if_the_agent_can_read_it_or_not_run_the_sdk_on_it(name: str) -> None:
    steps = _stage(name)
    checks = [
        step
        for step in steps[steps.index("USER agent") :]
        if f"test -x {_INTERPRETER}" in step
        and f"! test -r {_INTERPRETER}" in step
        and f"{_INTERPRETER} -m embry0.agents.sdk_support" in step
    ]

    assert checks, f"{name} has no build-time check, as the agent user, that it runs the SDK on an unreadable copy"
