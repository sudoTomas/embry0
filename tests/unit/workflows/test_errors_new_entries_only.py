"""A node's ``errors`` update holds its new entries only (#434).

``JobState.errors`` is ``Annotated[list[str], operator.add]``: LangGraph
appends whatever list a node returns to the stored one. Three paths returned
the stored list plus their entry, so every earlier error was stored again:
the developer's unfinished-run and branch-missing pauses, and the done-when
pause. The repeats then showed in a later failure's message and in the next
run's prompt.

Each path is driven with an earlier error already stored, and its update must
not carry it. And a walk of the code the JobState graphs run fails on any
update that hands an appended key its own stored list again
(``tests/unit/workflows/appended_updates.py`` says what counts).
"""

from __future__ import annotations

import ast
import operator
import pathlib
import textwrap
import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import embry0
from embry0.orchestration.state import JobState
from tests.fixtures.sandbox_git_local import reads_branch_tip
from tests.unit.workflows.appended_updates import echoes, graph_nodes
from tests.unit.workflows.test_developer_branch_verification import _dirty_tree_docker, _make_state, _turn_cap_result
from tests.unit.workflows.test_done_when_check import _UNMET, LIST, _dev_state, _result, _with

EARLIER = "an earlier error, stored once"
SRC = pathlib.Path(embry0.__file__).parent


def _appended_keys() -> set[str]:
    """The JobState keys whose reducer appends (``operator.add``)."""
    hints = typing.get_type_hints(JobState, include_extras=True)
    return {
        key
        for key, hint in hints.items()
        if typing.get_origin(hint) is typing.Annotated and operator.add in typing.get_args(hint)[1:]
    }


def _fields() -> set[str]:
    return set(typing.get_type_hints(JobState, include_extras=True))


def _sources(root: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(root.parent)): path.read_text()
        for top in ("workflows", "orchestration")
        for path in sorted((root / top).rglob("*.py"))
    }


def test_no_update_to_an_appended_key_repeats_its_stored_value() -> None:
    keys = _appended_keys()
    assert {"errors", "agent_outputs"} <= keys, f"the appended keys were not read, so this measures nothing: {keys}"
    sources = _sources(SRC)
    nodes = graph_nodes([ast.parse(text) for text in sources.values()], "JobState")
    assert {"developer_node", "review_node", "triage_node"} <= nodes, (
        f"the JobState graphs' nodes were not read: {nodes}"
    )
    found = echoes(sources, keys=keys, fields=_fields())
    assert found == [], f"updates that store the state's own list again (it is appended to): {found}"


# ---- the walk itself, on modules written for it (the review's probes) ------------------------------


def _probe(nodes: list[str], body: str, *, extra: dict[str, str] | None = None) -> list[str]:
    """The walk over a module of nodes, run by a graph built on JobState, beside ``extra`` modules."""
    graph = "\n".join(
        [
            "from langgraph.graph import StateGraph",
            "",
            "def build():",
            "    builder = StateGraph(JobState)",
            *(f'    builder.add_node("{name}", {name})' for name in nodes),
        ]
    )
    sources = {"graph.py": graph + "\n", "nodes.py": textwrap.dedent(body), **(extra or {})}
    return echoes(sources, keys=_appended_keys(), fields=_fields())


_ECHOES = """
from typing import Any


def a2_subscript_plus(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": state["errors"] + ["x"]}  # line 6


def a3_starred(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": [*state.get("errors", []), "x"]}  # line 10


def a4_list_call(state: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    updates["errors"] = list(state.get("errors") or []) + ["x"]  # line 15
    return updates


def _with_stored(state: dict[str, Any], entry: str) -> list[str]:
    return (state.get("errors") or []) + [entry]


def a5_helper(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": _with_stored(state, "x")}  # line 24


def a6_alias(state: dict[str, Any]) -> dict[str, Any]:
    errs = list(state.get("errors") or [])
    errs.append("x")
    return {"errors": errs}  # line 30


def a8_kwargs(state: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    updates.update(errors=state.get("errors", []) + ["x"])  # line 35
    return dict(agent_outputs=state.get("agent_outputs", []) + [{}])  # line 36


def a9_other_name(job_state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": job_state.get("errors", []) + ["x"]}  # line 40


def a10_augassign(state: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {"current_stage": "x"}
    updates["errors"] += state.get("errors", [])  # line 45
    return updates


def a11_key_in_a_name(state: dict[str, Any]) -> dict[str, Any]:
    key = "errors"
    return {key: state.get(key, []) + ["x"]}  # line 51


def a12_copy(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": state.get("errors", []).copy() + ["x"]}  # line 55


def a13_comprehension(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": [e for e in state.get("errors", [])] + ["x"]}  # line 59


def a14_slice(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": state.get("errors", [])[:] + ["x"]}  # line 63


def a15_generator(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": list(e for e in state["errors"]) + ["x"]}  # line 67


def a16_entry(state: dict[str, Any]) -> dict[str, Any]:
    return {"errors": [state["errors"][-1], "x"]}  # line 71
"""

_NODE_NAMES = ["a2_subscript_plus", "a3_starred", "a4_list_call", "a5_helper", "a6_alias", "a8_kwargs"]
_NODE_NAMES += ["a9_other_name", "a10_augassign", "a11_key_in_a_name", "a12_copy", "a13_comprehension", "a14_slice"]
_NODE_NAMES += ["a15_generator", "a16_entry"]


def test_the_walk_finds_each_way_an_update_can_carry_the_stored_list() -> None:
    found = _probe(_NODE_NAMES, _ECHOES)
    expected = [f"nodes.py:{line} {key}" for line, key in [(6, "errors"), (10, "errors"), (15, "errors")]]
    expected += [f"nodes.py:{line} {key}" for line, key in [(24, "errors"), (30, "errors"), (35, "errors")]]
    expected += [f"nodes.py:{line} {key}" for line, key in [(36, "agent_outputs"), (40, "errors")]]
    expected += [f"nodes.py:{line} {key}" for line, key in [(45, "errors"), (51, "errors")]]
    expected += [f"nodes.py:{line} errors" for line in (55, 59, 63, 67, 71)]
    assert found == sorted(expected), f"the walk missed or misplaced an update carrying the stored list: {found}"


def test_the_walk_leaves_what_is_not_a_jobstate_update_alone() -> None:
    """Another state type's replacing list, and a payload that only reports the stored list (the review's probes)."""
    subtask = """
        from typing import Any
        from langgraph.graph import StateGraph


        def build_subtask():
            builder = StateGraph(SubTaskState)
            builder.add_node("replace", fp_replace_semantics)


        def fp_replace_semantics(state: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
            # SubTaskState.agent_outputs has no reducer (replace): carrying the stored list forward is correct.
            return {"agent_outputs": (state.get("agent_outputs") or []) + [out]}
        """
    payload = """
        from typing import Any


        def fp_event_payload(state: dict[str, Any]) -> dict[str, Any]:
            # Not a state update: an event/summary payload that reports the stored errors.
            return {"type": "summary", "errors": state.get("errors") or []}


        def event_payload_node(state: dict[str, Any]) -> dict[str, Any]:
            writer(fp_event_payload(state))
            # A copy of the state with the list on it, to read: not an update.
            check_budget({**state, "errors": state.get("errors", []) + ["x"]})
            # The stored list's length in a sentence: not the list.
            return {"errors": [f"{len(state.get('errors') or [])} errors so far"]}
        """
    found = _probe(["event_payload_node"], payload, extra={"qa/subtask.py": textwrap.dedent(subtask)})
    assert found == [], f"the walk flagged what is not a JobState update carrying the stored list: {found}"


async def _developer(result: dict[str, Any], docker: Any) -> Any:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
    ):
        return await developer_node(
            _make_state(errors=[EARLIER]),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )


@pytest.mark.asyncio
async def test_the_branch_missing_pause_adds_its_entry_alone() -> None:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])
    result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "made the changes", "mode": "x"}],
        "events": [],
        "total_cost_usd": 0.05,
    }
    command = await _developer(result, docker)
    assert command.update["current_stage"] == "dev_branch_missing"
    errors = command.update["errors"]
    assert EARLIER not in errors and len(errors) == 1, f"the update stores the earlier error again: {errors}"


@pytest.mark.asyncio
async def test_the_unfinished_run_pause_adds_its_entries_alone() -> None:
    docker, _calls = _dirty_tree_docker()
    # run_agent_node adds the run's own error to its update, as it does for every run that errors.
    run = {**_turn_cap_result(), "errors": ["developer: Reached maximum number of turns (50)"]}
    command = await _developer(run, docker)
    assert command.update["current_stage"] == "dev_run_unfinished"
    errors = command.update["errors"]
    assert EARLIER not in errors, f"the update stores the earlier error again: {errors}"
    assert errors[0] == "developer: Reached maximum number of turns (50)" and "UNFINISHED" in errors[-1], (
        f"the run's own error and the pause's entry are both this update's: {errors}"
    )
    assert len(errors) == 2, errors


@pytest.mark.asyncio
async def test_the_done_when_pause_adds_its_entry_alone() -> None:
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    updates, goto = await check_done_when(
        {"job_id": "J", "errors": [EARLIER], **_with(retries=0)}, {}, _result(_UNMET), LIST, writer=lambda _e: None
    )
    assert goto == "max_retries"
    assert updates["errors"] == ["Done-when items still not met: The cause is written down"], (
        f"the done-when pause stores the earlier error again: {updates['errors']}"
    )


@pytest.mark.asyncio
async def test_a_run_that_errored_keeps_its_error_beside_the_done_when_pause() -> None:
    """The developer laid the done-when check's updates over its own: the check's errors replaced the run's."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    probes: list[str] = []

    def run_cmd(cmd: list[str], **_kwargs: Any) -> str:
        # The branch is free when the developer starts, and on origin after its push, at the sandbox's own
        # tip of it (#435).
        if reads_branch_tip(cmd):
            return "abc\n"
        if "ls-remote" not in cmd:
            return ""
        probes.append(cmd[-1])
        return "" if len(probes) == 1 else f"abc\trefs/heads/{cmd[-1]}"

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])
    run = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Connection reset",
                "output": "",
                "mode": "x",
            }
        ],
        "errors": ["developer: Connection reset"],
        "events": [],
        "total_cost_usd": 0.05,
    }
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
    ):
        command = await developer_node(
            _dev_state(done_when=LIST, errors=[EARLIER], **_with(retries=0)),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )
    assert command.goto == "max_retries", (command.goto, command.update.get("current_stage"))
    errors = command.update["errors"]
    assert errors[0] == "developer: Connection reset" and errors[-1].startswith("Done-when items still not met"), (
        f"the run's error and the pause's entry, and nothing stored before: {errors}"
    )
    assert len(errors) == 2, errors


@pytest.mark.parametrize("node", ["developer", "review"])
@pytest.mark.asyncio
async def test_the_question_cap_keeps_the_runs_error_beside_its_own(node: str) -> None:
    """A run that errored and asked past the cap: the cap's failure was laid over the run's error and replaced it."""
    from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP
    from embry0.workflows.issue_to_pr.nodes import developer_node, review_node
    from tests.unit.workflows.test_command_routing import _make_review_config, _state

    question = {"type": "agent_ask_user", "question": "May I drop the old table?", "category": "general"}
    output = '{"decision": "approved", "reasoning": "ok"}' if node == "review" else ""

    async def run(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_event"](dict(question))
        return {
            "agent_outputs": [{"agent_type": node, "is_error": False, "output": output, "cost_usd": 0.0}],
            "errors": [f"{node}: Connection reset"],
            "total_cost_usd": 0.0,
        }

    state = _state(errors=[EARLIER], agent_question_rounds=DEFAULT_ASK_USER_CAP, agent_outputs=[], pr_url="http://x")
    function, config = {
        "developer": (developer_node, None),
        "review": (review_node, _make_review_config(object())),
    }[node]
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
    ):
        command = await function(state, config)
    update = command.update
    assert update.get("agent_questions_exhausted"), f"the cap was not reached: {update.get('current_stage')}"
    assert update["errors"] == [
        f"{node}: Connection reset",
        f"Agent exceeded {DEFAULT_ASK_USER_CAP} rounds of asking the user — giving up.",
    ], f"the run's own error and the cap's, and nothing stored before: {update['errors']}"


def test_the_cap_joins_its_errors_after_the_runs() -> None:
    from embry0.orchestration.nodes.agent import with_cap_updates

    joined = with_cap_updates({"errors": ["run"]}, {"errors": ["cap"]})["errors"]
    assert joined == ["run", "cap"], f"the cap's errors replaced the run's: {joined}"
    assert with_cap_updates({"errors": ["run"]}, {"pending_agent_questions": []})["errors"] == ["run"]
    assert with_cap_updates({}, {"errors": ["cap"]})["errors"] == ["cap"]
