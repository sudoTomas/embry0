"""What the orchestrator takes from an agent run's output: only the events an agent run writes.

The runner's stdout carries what its process writes and what any process of
the same user in the sandbox writes to it, so a line is no event of the
orchestrator's own however it names itself. Only the ``agent_run`` types of
docs/contracts/events.json are taken into a job's events; any other line is
dropped and counted in the log by its type alone, never its text; the fields
only the orchestrator stamps are taken off. The runner's ``final_result`` is
read apart, as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from embry0.execution import events
from embry0.execution.agent_runner import AgentRunner
from tests.fixtures.sandbox_runner import as_the_runner

ROOT = Path(__file__).resolve().parents[3]
CONTRACT: dict[str, dict[str, Any]] = json.loads((ROOT / "docs" / "contracts" / "events.json").read_text())
FIXTURES = ROOT / "tests" / "fixtures" / "job-events"
#: What a line of another type carries, so a test can see it never reaches a log.
MARK = "zz-another-writer"


def _written_by(who: str) -> list[str]:
    return sorted(name for name, row in CONTRACT.items() if row.get("written_by") == who)


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _Lines:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _Stderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _Proc:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = _Lines(lines)
        self.stderr = _Stderr()
        self.returncode = 0

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover - only a timeout kills
        pass


_FINAL = {"type": "final_result", "agent_type": "developer", "is_error": False, "output": "done", "cost_usd": 0.01}


async def _run(events_written: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    """Run an agent whose runner writes ``events_written`` then its final result: what reached the job's events,
    the logs, and the run's output."""
    lines = [json.dumps(event).encode() + b"\n" for event in [*events_written, _FINAL]]
    docker = AsyncMock()
    docker.stream_exec = AsyncMock(side_effect=lambda **kw: as_the_runner(_Proc(lines), kw.get("stdin_data")))
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    runner = AgentRunner(sandbox_manager=AsyncMock(), docker=docker)
    taken: list[dict[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        output = await runner.run(
            container="c", config={"agent_type": "developer", "timeout_seconds": 30}, on_event=taken.append
        )
    return taken, logs, output


def test_the_contract_says_who_writes_every_type() -> None:
    assert {row.get("written_by") for row in CONTRACT.values()} == {"agent_run", "orchestrator"}, (
        "docs/contracts/events.json names, for each type, whether an agent run or the orchestrator writes it"
    )
    assert "interrupt" in _written_by("orchestrator") and "tool_call" in _written_by("agent_run")


#: Every type the contract does not say an agent run writes: the orchestrator's own.
_ORCHESTRATOR_TYPES = sorted(name for name, row in CONTRACT.items() if row.get("written_by") != "agent_run")


@pytest.mark.parametrize("kind", _ORCHESTRATOR_TYPES)
async def test_a_line_of_an_orchestrator_type_never_reaches_the_job_s_events(kind: str) -> None:
    written = {"type": kind, "timestamp": "2026-01-01T00:00:00Z", "reason": MARK, "options": [MARK]}
    taken, logs, output = await _run([written])
    assert taken == [], f"a {kind} line from the agent run's output reached the job's events: {taken}"
    assert output.output == "done", "the run's final result is still read"
    dropped = [e for e in logs if e["event"] == "agent_runner_events_dropped"]
    assert [(e["dropped"], e["types"]) for e in dropped] == [(1, {kind: 1})], dropped
    assert MARK not in json.dumps(logs, default=str), "the log names the type, never what the line held"


@pytest.mark.parametrize(
    "line",
    [
        {"type": "pipeline_stage_completed", "detail": MARK},
        {"type": 7, "detail": MARK},
        {"type": None, "detail": MARK},
        {"detail": MARK},
        {"type": "x" * 500, "detail": MARK},
    ],
    ids=["unknown type", "number", "null", "no type", "a long type"],
)
async def test_a_line_of_no_known_type_is_dropped_and_counted(line: dict[str, Any]) -> None:
    taken, logs, _ = await _run([line, line])
    assert taken == [], taken
    (dropped,) = [e for e in logs if e["event"] == "agent_runner_events_dropped"]
    assert dropped["dropped"] == 2 and sum(dropped["types"].values()) == 2, dropped
    assert all(len(kind) <= 64 for kind in dropped["types"]), "a type is named cut short"
    assert MARK not in json.dumps(logs, default=str), "the log names the type, never what the line held"


async def test_one_run_names_at_most_sixteen_dropped_types_and_counts_the_rest() -> None:
    taken, logs, _ = await _run([{"type": f"zz-kind-{n}"} for n in range(40)])
    assert taken == []
    (dropped,) = [e for e in logs if e["event"] == "agent_runner_events_dropped"]
    assert dropped["dropped"] == 40 and len(dropped["types"]) == 17 and dropped["types"]["<other>"] == 24, dropped


def _fixture_events() -> dict[str, dict[str, Any]]:
    """One event of each agent-run type the job-event fixtures hold."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        for event in json.loads(path.read_text()):
            if CONTRACT.get(event.get("type"), {}).get("written_by") == "agent_run":
                found.setdefault(event["type"], event)
    return found


def _every_agent_run_event() -> dict[str, dict[str, Any]]:
    """An event of every agent-run type: the fixtures' and, for a type they hold none of, its constructor's."""
    found = _fixture_events()
    if "agent_ask_user" not in found:
        found["agent_ask_user"] = events.agent_ask_user(question="Which port?", category="general", options=["80"])
    return found


def test_every_agent_run_type_has_an_event_to_send() -> None:
    assert sorted(_every_agent_run_event()) == _written_by("agent_run"), "a test event for every agent-run type"


async def test_every_event_an_agent_run_writes_still_reaches_the_job_s_events() -> None:
    written = list(_every_agent_run_event().values())
    taken, logs, _ = await _run(written)
    stamped = set(events.ORCHESTRATOR_STAMPED)
    expected = [{k: v for k, v in event.items() if k not in stamped} for event in written]
    assert taken == expected, "each agent-run event is taken as it was written, but for the orchestrator's own stamps"
    assert not [e for e in logs if e["event"] == "agent_runner_events_dropped"], (
        "nothing an agent run writes is dropped"
    )


async def test_an_agent_run_event_cannot_stamp_what_only_the_orchestrator_stamps() -> None:
    written = {
        "type": "text",
        "text": "hello",
        "timestamp": "t",
        "job_id": "another-job",
        "event_seq": 1,
        "prev_seq": 0,
    }
    taken, _, _ = await _run([written])
    assert taken == [{"type": "text", "text": "hello", "timestamp": "t"}], taken
