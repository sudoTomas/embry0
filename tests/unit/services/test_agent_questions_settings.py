"""A job's caps on questions and retries are its agent_questions settings, read once when it starts (#179).

ASK_USER_ROUNDS_CAP and USER_RETRY_ROUNDS_CAP were read from the environment when the agent module loaded: one
value for every job of the deployment, and the brainstorming floor was a constant. The caps are now the settings
document's ``agent_questions`` section: the deployment's values, then the job's organization's layer, then its
repository's. A job reads them when it starts and keeps them, so a change reaches the next job and never a running
one, not even after a pause and a resume. A name this change adds is imported inside each test, so a tree without
it fails each one with its own message.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from asyncio import run as run_async
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from embry0.config import Embry0Config
from embry0.orchestration.nodes.agent import _enforce_ask_user_cap
from embry0.orchestration.state import JobState
from embry0.settings.sections import CATALOG
from tests.unit.orchestration.test_ask_user_cap import _developer_row_loading_superpowers
from tests.unit.services.test_job_settings import WIDGETS, _resumer, _start, _Store

GIZMOS, GADGETS = "acme/gizmos", "globex/gadgets"
QUESTION = [{"question": "Which export?", "category": "scope", "options": []}]


def _exhausted_at(state: dict[str, Any]) -> int:
    """The first round count at which ``state``'s job may ask no more: its rounds cap, read off the real check."""
    for rounds in range(0, 64):
        exhausted, _ = _enforce_ask_user_cap({**state, "agent_question_rounds": rounds}, QUESTION)
        if exhausted:
            return rounds
    raise AssertionError("no cap below 64 rounds")


def _layers(**by_scope: dict[str, Any]) -> dict[str, Any]:
    return {scope: {"agent_questions": values} for scope, values in by_scope.items()}


@pytest.mark.asyncio
async def test_an_organization_layer_sets_the_question_cap_of_its_own_jobs() -> None:
    store = _Store(**_layers(**{"organization:acme": {"rounds_cap": 2}}))
    acme = await _start(store, WIDGETS)
    globex = await _start(store, GADGETS, job_id="job-2")
    assert (_exhausted_at(acme), _exhausted_at(globex)) == (2, 10), (
        "an organization's agent_questions.rounds_cap must cap its own jobs and no other organization's"
    )
    _, updates = _enforce_ask_user_cap({**acme, "agent_question_rounds": 2}, QUESTION)
    assert updates["error_code"] == "ERR_MAX_AGENT_QUESTIONS"
    assert "agent_questions.rounds_cap" in updates["errors"][0], (
        f"the failure must name the setting that capped the job, not a variable: {updates['errors']}"
    )


@pytest.mark.asyncio
async def test_a_repository_layer_sets_its_jobs_caps_over_its_organization_s() -> None:
    store = _Store(
        {"agent_questions": {"rounds_cap": 6}},
        **_layers(
            **{
                "organization:acme": {"rounds_cap": 2},
                f"repository:{WIDGETS}": {"rounds_cap": 4},
            }
        ),
    )
    seen = {repo: _exhausted_at(await _start(store, repo)) for repo in (WIDGETS, GIZMOS, GADGETS)}
    assert seen == {WIDGETS: 4, GIZMOS: 2, GADGETS: 6}, (
        f"each job's cap must be its repository's layer, else its organization's, else the deployment's: {seen}"
    )


def _brainstorming_developer(state: dict[str, Any], rounds: int) -> dict[str, Any]:
    """The developer's update for a run that loads the brainstorming skill and asks one question at ``rounds``."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    asked = [{"type": "agent_ask_user", "question": "Which layout?", "category": "design", "options": []}]

    async def _run(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        for event in asked:
            kwargs["on_event"](event)
        return {
            "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
            "events": asked,
            "total_cost_usd": 0.1,
        }

    job = {
        **state,
        "issue_id": "iss-1",
        "task": "design the export",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": rounds,
        "pipeline_config": {"agent_skills": {"developer": ["superpowers:brainstorming"]}, "budget_usd": 50.0},
    }

    async def go() -> Any:
        with (
            patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
            patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
            _developer_row_loading_superpowers(),
        ):
            return await developer_node(
                job, {"configurable": {"agent_runner": object(), "credentials": {}, "db": MagicMock()}}
            )

    result = run_async(go())
    assert isinstance(result, Command), result
    return dict(result.update or {})


def test_a_repository_layer_raises_the_brainstorming_floor_of_its_jobs() -> None:
    store = _Store(**_layers(**{f"repository:{WIDGETS}": {"brainstorming_floor": 20}}))
    widgets = run_async(_start(store, WIDGETS))
    gizmos = run_async(_start(store, GIZMOS, job_id="job-2"))
    assert not _brainstorming_developer(widgets, 16).get("agent_questions_exhausted"), (
        "a brainstorming developer under its repository's brainstorming_floor of 20 failed at round 16"
    )
    capped = _brainstorming_developer(gizmos, 16)
    assert capped.get("agent_questions_exhausted"), "another repository keeps the floor of 15"
    assert "agent_questions.brainstorming_floor" in capped["errors"][-1], capped["errors"]


def _triage_asking_again(state: dict[str, Any], rounds: int) -> Any:
    """Triage's result when it wants more information once more, after ``rounds`` of its own pauses."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    needs_info = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": '{"action":"needs_info","confidence":0.3,"questions":[],"reasoning":"x"}',
            }
        ],
        "total_cost_usd": 0.01,
        "current_stage": "triage_complete",
    }
    job = {
        **state,
        "task": "Fix the thing",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "triage_question_rounds": rounds,
        "total_cost_usd": 0.0,
    }
    config = {"configurable": {"agent_runner": MagicMock(), "credentials": {}, "repo_preferences_repo": None}}

    async def go() -> Any:
        with (
            patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=needs_info)),
            patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
            patch("embry0.workflows.issue_to_pr.nodes.interrupt", return_value="the monthly one") as asked,
        ):
            return await triage_node(job, config), asked.called

    return run_async(go())


def test_a_repository_layer_caps_triage_s_own_pauses_for_more_information() -> None:
    store = _Store(**_layers(**{f"repository:{WIDGETS}": {"rounds_cap": 2}}))
    widgets = run_async(_start(store, WIDGETS))
    gizmos = run_async(_start(store, GIZMOS, job_id="job-2"))
    stopped, asked = _triage_asking_again(widgets, 2)
    assert isinstance(stopped, Command) and stopped.goto == END and not asked, (
        "triage asked a person a third time past its repository's rounds_cap of 2"
    )
    assert stopped.update.get("error_code") == "ERR_MAX_TRIAGE_QUESTIONS"
    assert "agent_questions.rounds_cap" in stopped.update["errors"][0], stopped.update["errors"]
    _, asked = _triage_asking_again(gizmos, 2)
    assert asked, "another repository keeps the cap of 10: triage asks again"


def _continue_retrying(state: dict[str, Any], rounds: int) -> dict[str, Any]:
    """The update of a person's continue retrying, the job sent back ``rounds`` times already."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module

    async def go() -> dict[str, Any]:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
            patch.object(nodes_module, "interrupt", new=lambda _summary: {"choice": "continue_retrying"}),
            patch("embry0.workflows.issue_to_pr.nodes._interrupts.load_budget_settings", new=AsyncMock()),
        ):
            job = {**state, "current_stage": "developer_complete", "retry_count": 1, "user_retry_rounds": rounds}
            return dict(await nodes_module.max_retries_node(job, config=None))

    return run_async(go())


def test_an_organization_layer_sets_how_often_a_person_may_send_its_jobs_back() -> None:
    store = _Store(**_layers(**{"organization:acme": {"retry_rounds_cap": 1}}))
    acme = run_async(_start(store, WIDGETS))
    globex = run_async(_start(store, GADGETS, job_id="job-2"))
    stopped = _continue_retrying(acme, 1)
    assert stopped.get("current_stage") == "abandoned", (
        f"a second continue past the organization's retry_rounds_cap of 1 went on: {stopped.get('current_stage')}"
    )
    assert "agent_questions.retry_rounds_cap" in stopped["errors"][0], stopped["errors"]
    assert _continue_retrying(globex, 1).get("current_stage") == "developer_retry", "another organization keeps 5"


@pytest.mark.asyncio
async def test_a_running_job_keeps_the_caps_it_started_with_through_a_pause() -> None:
    """Start a job, pause it on a question, raise the cap, answer: the job keeps the cap it started with."""
    store = _Store(**_layers(**{"organization:acme": {"rounds_cap": 2}}))
    first = await _start(store, WIDGETS)
    seen: list[tuple[str, int]] = []

    async def asks(state: dict[str, Any]) -> dict[str, Any]:
        interrupt({"reason": "ask_user", "questions": [{"id": "q-1", "question": "Which export?"}]})
        seen.append(("after", _exhausted_at(state)))
        return {"current_stage": "review_complete"}

    builder = StateGraph(JobState)
    builder.add_node("asks", asks)
    builder.add_edge(START, "asks")
    builder.add_edge("asks", END)
    saver = InMemorySaver()
    await builder.compile(checkpointer=saver).ainvoke(first, {"configurable": {"thread_id": "job-1"}})

    store.write(**_layers(**{"organization:acme": {"rounds_cap": 9}}))
    ex = _resumer(builder, store)

    @asynccontextmanager
    async def shared_saver(_url: str) -> Any:
        yield saver

    with (
        patch("embry0.services._executor_run.checkpointer_context", shared_saver),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await ex.resume("iss-1", "job-1", [{"input_id": "q-1", "answer": "the monthly one"}])

    assert seen == [("after", 2)], f"the resumed job must keep the cap of 2 it started with, not the new 9: {seen}"
    second = await _start(store, WIDGETS, job_id="job-2")
    assert _exhausted_at(second) == 9, "the next job reads the document as it is now"


def test_embry0_config_names_both_variables_as_governed() -> None:
    from embry0.settings.env import governed_lines

    with patch.dict(os.environ, {"ASK_USER_ROUNDS_CAP": "12", "USER_RETRY_ROUNDS_CAP": "3"}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    lines = governed_lines(CATALOG, config)
    for variable, key in (("ASK_USER_ROUNDS_CAP", "rounds_cap"), ("USER_RETRY_ROUNDS_CAP", "retry_rounds_cap")):
        assert any(
            line.startswith(f"{variable} is governed by the settings document (agent_questions.{key})")
            for line in lines
        ), f"embry0 config does not say {variable} is governed and ignored: {lines}"


def test_a_variable_that_is_no_number_does_not_stop_a_start() -> None:
    with patch.dict(os.environ, {"ASK_USER_ROUNDS_CAP": "ten", "USER_RETRY_ROUNDS_CAP": ""}, clear=True):
        Embry0Config(_env_file=None)  # type: ignore[call-arg]


def test_no_cap_is_read_from_the_environment_any_more() -> None:
    """In a fresh process with ASK_USER_ROUNDS_CAP=2 set before the agent module loads, a job with no layer still
    has the section's cap of 10: the variable is the import's alone."""
    probe = (
        "import json\n"
        "from embry0.orchestration.nodes import agent\n"
        "q = [{'question': 'Which export?'}]\n"
        "asks = [agent._enforce_ask_user_cap({'job_id': 'j', 'agent_question_rounds': n}, q)[0] for n in (2, 10)]\n"
        "retries = getattr(agent, 'retry_rounds_cap', None)\n"
        "print(json.dumps([asks, retries({}) if retries else agent.USER_RETRY_ROUNDS_CAP]))\n"
    )
    env = {
        **os.environ,
        "ASK_USER_ROUNDS_CAP": "2",
        "USER_RETRY_ROUNDS_CAP": "1",
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    asks, retries = json.loads(done.stdout.strip().splitlines()[-1])
    assert asks == [False, True], (
        f"ASK_USER_ROUNDS_CAP still caps jobs at load time (exhausted at rounds 2 and 10): {done.stdout}"
    )
    assert retries == 5, f"USER_RETRY_ROUNDS_CAP still caps continues at load time: {retries}"


def _continue_past_ceiling(state: dict[str, Any], passed: int, *, extra: float | None = 1000.0) -> tuple[Any, Any]:
    """A person's continue of a job over a $50 ceiling that ``passed`` continues already took past it: the node's
    update, and the options its pause offered."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.settings.sections.budget import BudgetSettings

    job = {
        **state,
        "current_stage": "developer_complete",
        "retry_count": 1,
        "user_retry_rounds": passed,
        "pipeline_config": {"budget_usd": 50.0},
        "budget_cap_usd": 50.0 + 1000.0 * passed,
        "total_cost_usd": 50.5 + 1000.0 * passed,
        "budget_allowance_usd": 1000.0 * passed,
        "continues_past_ceiling": passed,
    }
    offered: dict[str, Any] = {}
    answer = {"choice": "continue_retrying", **({"extra_budget_usd": extra} if extra is not None else {})}

    def asked(summary: dict[str, Any]) -> dict[str, Any]:
        offered.update(summary)
        return answer

    async def go() -> dict[str, Any]:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
            patch.object(nodes_module, "interrupt", new=asked),
            patch(
                "embry0.workflows.issue_to_pr.nodes._interrupts.load_budget_settings",
                new=AsyncMock(return_value=BudgetSettings(max_budget_per_job_usd=50.0)),
            ),
            patch("embry0.audit.helpers.emit_audit", new=AsyncMock()),
        ):
            return dict(await nodes_module.max_retries_node(job, config={"configurable": {}}))

    return run_async(go()), offered.get("options")


def test_a_layer_cannot_raise_how_many_continues_pass_the_budget_ceiling() -> None:
    """#651 review: while continue_may_exceed_ceiling is true, each continue past the ceiling raises a job's
    allowance, and retry_rounds_cap was the only bound on how many; a repository layer could set it to any number.
    budget.continue_past_ceiling_max, the deployment's alone, bounds them whatever a layer sets."""
    store = _Store(**_layers(**{f"repository:{WIDGETS}": {"retry_rounds_cap": 100000}}))
    widgets = run_async(_start(store, WIDGETS))
    stopped, options = _continue_past_ceiling(widgets, 5)
    assert stopped.get("current_stage") == "abandoned", (
        f"a sixth continue past the $50 ceiling went on under a repository's retry_rounds_cap of 100000: {stopped}"
    )
    assert "budget.continue_past_ceiling_max" in stopped["errors"][0], stopped["errors"]
    assert options is not None and "continue_retrying" not in options, f"the pause still offered it: {options}"
    went_on, options = _continue_past_ceiling(widgets, 4)
    assert went_on.get("current_stage") == "developer_retry" and went_on.get("continues_past_ceiling") == 5, went_on
    assert "continue_retrying" in (options or []), options


def test_the_deployment_sets_how_many_continues_pass_the_budget_ceiling() -> None:
    store = _Store({"budget": {"continue_past_ceiling_max": 2}})
    job = run_async(_start(store, WIDGETS))
    assert _continue_past_ceiling(job, 2)[0].get("current_stage") == "abandoned", "the deployment's 2 was not kept"
    assert _continue_past_ceiling(job, 1)[0].get("current_stage") == "developer_retry"


def test_a_continue_that_stays_under_the_ceiling_is_not_counted_against_it() -> None:
    """Under the $50 ceiling a continue is an ordinary one: retry_rounds_cap alone governs it."""
    job = run_async(_start(_Store(), WIDGETS))
    under = {**job, "pipeline_config": {"budget_usd": 10.0}}

    async def go() -> dict[str, Any]:
        import embry0.workflows.issue_to_pr.nodes as nodes_module
        from embry0.settings.sections.budget import BudgetSettings

        state = {
            **under,
            "current_stage": "developer_complete",
            "retry_count": 1,
            "user_retry_rounds": 1,
            "total_cost_usd": 10.5,
            "continues_past_ceiling": 5,
        }
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
            patch.object(nodes_module, "interrupt", new=lambda _summary: {"choice": "continue_retrying"}),
            patch(
                "embry0.workflows.issue_to_pr.nodes._interrupts.load_budget_settings",
                new=AsyncMock(return_value=BudgetSettings(max_budget_per_job_usd=50.0)),
            ),
            patch("embry0.audit.helpers.emit_audit", new=AsyncMock()),
        ):
            return dict(await nodes_module.max_retries_node(state, config={"configurable": {}}))

    update = run_async(go())
    assert update.get("current_stage") == "developer_retry", update
    assert "continues_past_ceiling" not in update, "a continue under the ceiling was counted as one past it"


@pytest.mark.parametrize("layer", ["organization", "repository"])
def test_no_layer_may_set_how_many_continues_pass_the_budget_ceiling(layer: str) -> None:
    from embry0.settings.document import DocumentInvalid, check

    with pytest.raises(DocumentInvalid) as invalid:
        check(CATALOG, {"budget": {"continue_past_ceiling_max": 50}}, stored={}, layer=layer, inherited={})
    assert [(p.loc, p.type) for p in invalid.value.problems] == [
        (("budget", "continue_past_ceiling_max"), "not_overridable")
    ], f"the {layer} layer may set budget.continue_past_ceiling_max: {invalid.value.problems}"
