"""Starting a step of an issue-to-pr job (#395): earlier steps' leftovers stopped, then the job's branch opened at the
first step that holds coder or docs, for the rest of the job.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.execution.sandbox_processes import LEFTOVER_STOP
from embry0.workflows.issue_to_pr import step_start
from embry0.workflows.issue_to_pr.step_start import begin_step, starts_a_step

#: A route: a step of the operator's own with no role, the coder, the reviewer (docs as seeded), the output step.
ROUTE = [
    {"agent_type": "scout", "node_id": "scout", "config": {}, "roles": []},
    {"agent_type": "developer", "node_id": "dev", "config": {}, "roles": ["coder"]},
    {"agent_type": "reviewer", "node_id": "rev", "config": {}, "roles": ["docs"]},
    {"agent_type": "output", "node_id": "out", "config": {}, "roles": []},
]
JOB = {"job_id": "job-1a2b3c4d", "issue_id": "iss-1", "task": "Fix widgets", "sandbox_container_id": "C"}
OWN_BRANCH = "embry0/iss-1-fix-widgets-1a2b3c4d"


class _Sandbox:
    """The job's sandbox: records each stop, in order with the proxy's openings."""

    def __init__(self, order: list[str], *, fails: bool = False) -> None:
        self.order = order
        self.fails = fails

    async def stop_leftover_processes(self, container: str) -> int:
        self.order.append(f"stop:{container}")
        if self.fails:
            raise RuntimeError("exec failed")
        return 0


def _proxy(order: list[str], *, fails: bool = False) -> Any:
    proxy = MagicMock()
    proxy.git_proxy_url = "http://git-proxy:9101"

    async def opened(container: str, branches: list[str]) -> None:
        order.append(f"open:{container}:{','.join(branches)}")
        if fails:
            raise RuntimeError("refused")

    proxy.set_git_push_branches = AsyncMock(side_effect=opened)
    return proxy


def _config(order: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "configurable": {
            "db": None,
            "sandbox_manager": _Sandbox(order, fails=kw.pop("stop_fails", False)),
            "proxy_manager": _proxy(order, fails=kw.pop("open_fails", False)),
            **kw,
        }
    }


async def _run_steps(steps: list[tuple[str, dict[str, Any]]], config: dict[str, Any]) -> tuple[list[Any], dict]:
    """Each (kind, state delta) in turn, as the graph runs them, the state carried between them."""
    state: dict[str, Any] = {**JOB, "push_open": False, "route_plan": ROUTE}
    seen: list[Any] = []
    for kind, delta in steps:
        state.update(delta)

        async def node(s: dict[str, Any], c: Any) -> dict[str, Any]:
            seen.append((kind, s.get("push_open")))
            return {}

        state.update(await starts_a_step(node, kind)(state, config) or {})
    return seen, state


async def test_triage_holding_docs_opens_the_branch_before_it_runs() -> None:
    order: list[str] = []
    seen, state = await _run_steps([("triage", {})], _config(order))
    assert order == ["stop:C", f"open:C:{OWN_BRANCH}"], "leftovers are stopped before the branch opens"
    assert seen == [("triage", True)] and state["push_open"] is True


async def test_a_step_without_a_role_runs_closed_and_the_first_that_holds_one_opens_it_once() -> None:
    order: list[str] = []
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, state = await _run_steps(
            [
                ("triage", {}),
                ("agent", {"route_cursor": 0}),
                ("developer", {"route_cursor": 1}),
                ("review", {"route_cursor": 2}),
                ("output", {"route_cursor": 3}),
            ],
            _config(order),
        )
    assert seen == [
        ("triage", False),
        ("agent", False),
        ("developer", True),
        ("review", True),
        ("output", True),
    ], seen
    assert order == ["stop:C", "stop:C", "stop:C", f"open:C:{OWN_BRANCH}", "stop:C", "stop:C"], (
        "each step stops what came before it; the branch opens once, at the coder's step, and stays open"
    )


async def test_a_docs_step_opens_it_for_a_route_without_a_coder() -> None:
    order: list[str] = []
    route = [{"agent_type": "context-auditor", "node_id": "a", "roles": ["docs"]}, {"agent_type": "output"}]
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, _ = await _run_steps(
            [("triage", {}), ("agent", {"route_plan": route, "route_cursor": 0}), ("output", {"route_cursor": 1})],
            _config(order),
        )
    assert seen == [("triage", False), ("agent", True), ("output", True)], "an audit's patch can still be pushed"


async def test_a_job_none_of_whose_steps_holds_a_role_never_opens_it() -> None:
    order: list[str] = []
    route = [{"agent_type": "why", "node_id": "w", "roles": []}, {"agent_type": "output", "roles": []}]
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, state = await _run_steps(
            [("triage", {}), ("agent", {"route_plan": route, "route_cursor": 0}), ("output", {"route_cursor": 1})],
            _config(order),
        )
    assert [opened for _kind, opened in seen] == [False, False, False]
    assert not [o for o in order if o.startswith("open:")] and state["push_open"] is False


async def test_a_failed_stop_keeps_a_step_that_may_push_from_starting() -> None:
    """A coder's or a docs step does not start beside a process that was not stopped: the job fails closed
    (``ERR_SANDBOX_LEFTOVERS``), and nothing is opened."""
    from embry0.execution.sandbox_processes import LeftoversNotStopped
    from embry0.safety.error_codes import ErrorCode, error_code_for_exception

    for kind, delta in (("developer", {"route_cursor": 1}), ("review", {"route_cursor": 2})):
        order: list[str] = []
        with pytest.raises(LeftoversNotStopped) as refused:
            await _run_steps([(kind, delta)], _config(order, stop_fails=True))
        assert order == ["stop:C"], f"{kind}: {order}"
        assert error_code_for_exception(refused.value) == ErrorCode.SANDBOX_LEFTOVERS


async def test_a_failed_stop_runs_a_step_with_no_role_before_the_branch_is_open_and_opens_nothing() -> None:
    order: list[str] = []
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, state = await _run_steps(
            [("triage", {}), ("agent", {"route_cursor": 0})], _config(order, stop_fails=True)
        )
    assert seen == [("triage", False), ("agent", False)] and state["push_open"] is False
    assert order == ["stop:C", "stop:C"]


@pytest.mark.parametrize("push_open", [True, None], ids=["opened", "a-checkpoint-from-before-the-flag"])
async def test_a_failed_stop_keeps_any_step_from_starting_once_the_branch_is_open(push_open: bool | None) -> None:
    from embry0.execution.sandbox_processes import LeftoversNotStopped

    order: list[str] = []

    async def node(s: dict[str, Any], c: Any) -> dict[str, Any]:
        raise AssertionError("the step started")

    state = {**JOB, "push_open": push_open, "route_plan": ROUTE, "route_cursor": 3}
    with pytest.raises(LeftoversNotStopped):
        await starts_a_step(node, "output")(state, _config(order, stop_fails=True))


async def test_a_refused_opening_is_tried_again_by_the_next_step_that_holds_a_role() -> None:
    order: list[str] = []
    config = _config(order, open_fails=True)
    seen, state = await _run_steps([("developer", {"route_cursor": 1}), ("review", {"route_cursor": 2})], config)
    assert seen == [("developer", False), ("review", False)] and state["push_open"] is False
    assert [o for o in order if o.startswith("open:")] == [f"open:C:{OWN_BRANCH}"] * 2


async def test_a_nodes_own_command_keeps_its_update_and_gains_the_window() -> None:
    order: list[str] = []

    async def node(state: dict[str, Any], config: Any) -> Command[str]:
        return Command(goto="review", update={"current_stage": "developed"})

    out = await starts_a_step(node, "developer")({**JOB, "push_open": False, "route_plan": ROUTE}, _config(order))
    assert isinstance(out, Command) and out.goto == "review"
    assert out.update == {"push_open": True, "current_stage": "developed"}


async def test_without_a_role_or_with_the_branch_open_nothing_is_written() -> None:
    order: list[str] = []
    assert await begin_step({**JOB, "push_open": True, "route_plan": ROUTE}, _config(order), "developer") == {}
    assert order == ["stop:C"], "the branch already open: stopped leftovers, nothing opened again"


def test_every_node_a_step_starts_at_is_wired_to_start_it() -> None:
    """The graph wraps each step's node (an optional wrapper nothing called would look exactly like no window)."""
    from embry0.workflows.issue_to_pr import graph as graph_module

    wrapped: list[tuple[str, str]] = []

    def recording(node: Any, kind: str) -> Any:
        wrapped.append((getattr(node, "__name__", "?"), kind))
        return starts_a_step(node, kind)

    with patch.object(graph_module, "starts_a_step", recording):
        graph_module.IssueToprWorkflow().compile()
    assert sorted(kind for _name, kind in wrapped) == sorted(step_start.STEP_KINDS), wrapped
    assert dict((kind, name) for name, kind in wrapped) == {
        "triage": "triage_node",
        "developer": "developer_node",
        "review": "review_node",
        "verify": "verify_node",
        "agent": "generic_agent_node",
        "qa": "init_orchestrator_node",
        "output": "finalize_output_node",
    }


# --- a background process from one step, gone when the next starts -----------------------------------------------


def _describe(proc: Path, pid: int, ppid: int, argv: list[str]) -> None:
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "stat").write_bytes(f"{pid} (x) S {ppid} 1 1 0 -1".encode())
    (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


class _RealStop:
    """A sandbox whose stop is the real one, run over the process table the test keeps for the sandbox: its init and
    keep-alive written, each process a step leaves linked to its real ``/proc`` entry."""

    def __init__(self, proc: Path) -> None:
        self.proc = proc

    async def stop_leftover_processes(self, container: str) -> int:
        done = subprocess.run(
            [sys.executable, "-I", "-c", LEFTOVER_STOP, str(self.proc)], capture_output=True, text=True, check=True
        )
        return int(done.stdout.strip())


async def test_a_background_process_from_one_step_is_gone_when_the_next_starts(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _describe(proc, 1, 0, ["/sbin/docker-init", "--", "sleep", "infinity"])
    (proc / "sys" / "kernel").mkdir(parents=True)
    (proc / "sys" / "kernel" / "ns_last_pid").write_bytes(b"4242\n")  # no process is created while it is read
    keepalive = subprocess.Popen(["sleep", "300"], start_new_session=True)
    _describe(proc, keepalive.pid, 1, ["sleep", "infinity"])
    started: list[subprocess.Popen[bytes]] = [keepalive]
    config = {"configurable": {"db": None, "sandbox_manager": _RealStop(proc), "proxy_manager": None}}
    still_running: list[bool] = []

    async def step_n(state: dict[str, Any], c: Any) -> dict[str, Any]:
        background = subprocess.Popen(["sleep", "300"], start_new_session=True)  # left running, as `cmd &` would be
        started.append(background)
        (proc / str(background.pid)).symlink_to(f"/proc/{background.pid}")  # in the sandbox's table while it runs
        return {}

    async def step_n_plus_1(state: dict[str, Any], c: Any) -> dict[str, Any]:
        background = started[-1]
        deadline = time.monotonic() + 10
        while background.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        still_running.append(background.poll() is None)
        return {}

    try:
        state = {**JOB, "push_open": False, "route_plan": ROUTE, "route_cursor": 0}
        await starts_a_step(step_n, "agent")(state, config)
        assert started[-1].poll() is None, "the step's background process runs while its step does"
        await starts_a_step(step_n_plus_1, "developer")({**state, "route_cursor": 1}, config)
        assert still_running == [False], "a background process from step N was still running when step N+1 started"
        assert keepalive.poll() is None, "the sandbox's keep-alive was stopped"
    finally:
        for p in started:
            if p.poll() is None:
                p.kill()
            p.wait()


@pytest.mark.parametrize("kind", ["sandbox", "everything"])
def test_an_unknown_kind_of_step_is_refused(kind: str) -> None:
    with pytest.raises(ValueError, match="not a kind of step"):
        starts_a_step(AsyncMock(), kind)


class _Rows:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def fetchrow(self, query: str, agent_type: str) -> dict[str, Any] | None:
        return self.rows.get(agent_type)


@pytest.mark.parametrize(
    ("rows", "opens"),
    [
        ({"context-auditor": {"type": "context-auditor", "roles": ["docs"]}}, True),
        ({"context-auditor": {"type": "context-auditor", "roles": []}}, False),
        ({}, True),
    ],
    ids=["still-holds-docs", "docs-taken", "a-built-in-with-no-row-runs-as-seeded"],
)
async def test_a_step_whose_agent_lost_its_role_since_planning_opens_nothing(rows: dict[str, Any], opens: bool) -> None:
    """Routing is what the rows said at planning; opening the branch asks the step's agent's row again."""
    order: list[str] = []
    route = [{"agent_type": "context-auditor", "node_id": "a", "roles": ["docs"]}, {"agent_type": "output"}]
    config = _config(order)
    config["configurable"]["db"] = _Rows(rows)
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, _ = await _run_steps([("agent", {"route_plan": route, "route_cursor": 0})], config)
    assert seen == [("agent", opens)], seen


async def test_a_coder_deleted_since_planning_opens_nothing() -> None:
    order: list[str] = []
    route = [{"agent_type": "scribe", "node_id": "s", "roles": ["coder"]}]
    config = _config(order)
    config["configurable"]["db"] = _Rows({})
    seen, _ = await _run_steps([("developer", {"route_plan": route, "route_cursor": 0})], config)
    assert seen == [("developer", False)] and not [o for o in order if o.startswith("open:")]


async def test_a_docs_agent_of_the_operators_own_deleted_since_planning_opens_nothing() -> None:
    order: list[str] = []
    route = [{"agent_type": "scout", "node_id": "s", "roles": ["docs"]}, {"agent_type": "output"}]
    config = _config(order)
    config["configurable"]["db"] = _Rows({})
    with patch.object(step_start, "_triage_roles", AsyncMock(return_value=frozenset())):
        seen, _ = await _run_steps([("agent", {"route_plan": route, "route_cursor": 0})], config)
    assert seen == [("agent", False)]


async def test_a_step_whose_agents_row_cannot_be_read_opens_nothing() -> None:
    from structlog.testing import capture_logs

    class _Down:
        async def fetchrow(self, *_a: Any) -> None:
            raise ConnectionError("database away")

    order: list[str] = []
    route = [{"agent_type": "scribe", "node_id": "s", "roles": ["coder"]}]
    config = _config(order)
    config["configurable"]["db"] = _Down()
    with capture_logs() as logs:
        seen, _ = await _run_steps([("developer", {"route_plan": route, "route_cursor": 0})], config)
    assert seen == [("developer", False)], "a step that cannot tell whether its agent holds its role opens nothing"
    assert "step_start_roles_unreadable" in [e["event"] for e in logs]


async def test_a_generic_step_reads_its_own_route_step_only() -> None:
    """A generic agent's step is the one under the cursor: another generic step of the route is another agent, whose
    roles are not this step's."""
    from embry0.workflows.issue_to_pr.step_start import step_roles_at_start

    route = [
        {"agent_type": "scout", "node_id": "s", "roles": ["docs"]},
        {"agent_type": "developer", "roles": ["coder"]},
    ]
    assert await step_roles_at_start({"route_plan": route, "route_cursor": 0}, {"configurable": {}}, "agent") == {
        "docs"
    }
    assert await step_roles_at_start({"route_plan": route, "route_cursor": 1}, {"configurable": {}}, "agent") == set()
    review_route = [{"agent_type": "developer", "roles": ["coder"]}, {"agent_type": "reviewer", "roles": ["docs"]}]
    assert await step_roles_at_start(
        {"route_plan": review_route, "route_cursor": 0}, {"configurable": {}}, "review"
    ) == {"docs"}, "a node that runs one fixed step of the route finds it wherever the cursor is"
