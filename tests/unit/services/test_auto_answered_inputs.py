"""A question an agent answers itself is recorded as an input of its job (#410).

An agent may ask a question it already has an answer for
(``ask_user(..., importance="auto_answerable", suggested_answer=...)``). The
workflow does not wait for it: ``_enforce_ask_user_cap`` puts it on the
node's update as ``auto_answered_agent_questions``. Nothing read that key,
so the question and the answer the agent went on with reached neither the
job's inputs nor the dashboard, and nobody could override it.

Each of the three nodes that ask (triage, developer, review) is run with its
agent emitting one such question, and its real update is streamed through
the executor's ``_process_stream``, on a real database: one ``auto_answered``
input of the job comes out, under its issue, with the suggested answer and
an ``issue.input_created`` audit row. Then, on the real app, the job's inputs
route shows it to a key that reads the job; to a key that does not, another
repository's job has no questions, as a missing job has none (#318); and a
person can override it through the answer route.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, TypedDict
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from embry0.orchestration.checkpoint import checkpointer_context
from embry0.orchestration.restart import ShutdownInterrupt
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issue_inputs import IssueInputsRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.workflows.issue_to_pr.nodes import developer_node, review_node, triage_node
from tests.fixtures.repository_writes import COVERED, MASTER, UNCOVERED, call, ids, repository_writes_app
from tests.unit.workflows.test_command_routing import _make_review_config, _state, _triage_config

pytestmark = pytest.mark.requires_postgres

QUESTION = "Which database should this use?"
AUTO = {
    "type": "agent_ask_user",
    "question": QUESTION,
    "category": "general",
    "importance": "auto_answerable",
    "suggested_answer": "PostgreSQL, as the rest of the service does",
}
BLOCKING = {"type": "agent_ask_user", "question": "May I drop the old table?", "category": "general"}
#: How many questions one run may answer itself and have recorded (agent.MAX_AUTO_ANSWERED_PER_RUN).
MAX_AUTO_ANSWERED_PER_RUN = 20


class _Run:
    """A run_agent_node whose agent emits ``events``, then returns ``output``."""

    def __init__(self, events: list[dict[str, Any]], output: str) -> None:
        self.events = events
        self.output = output

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        for event in self.events:
            kwargs["on_event"](dict(event))
        agent_type = kwargs.get("agent_type", "unknown")
        return {
            "agent_outputs": [
                {
                    "agent_type": agent_type,
                    "is_error": False,
                    "output": self.output,
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": f"{agent_type}_complete",
        }


class _Graph:
    """A graph that streams one node's update and stops there."""

    def __init__(self, node: str, update: dict[str, Any]) -> None:
        self._event = ("updates", {node: update})

    async def astream(self, input_value: Any, config: Any = None, stream_mode: Any = None) -> Any:
        yield self._event

    async def aget_state(self, config: Any) -> None:
        return None


async def _node_update(node: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """The update ``node`` returns when its agent emits ``events``."""
    run, config, state = {
        "developer": (_Run(events, ""), None, _state()),
        "review": (
            _Run(events, '{"decision": "approved", "reasoning": "ok"}'),
            _make_review_config(object()),
            _state(agent_outputs=[], pr_url="http://x"),
        ),
        "triage": (_Run(events, '{"action": "proceed"}'), _triage_config(MagicMock()), _state()),
    }[node]
    function = {"developer": developer_node, "review": review_node, "triage": triage_node}[node]
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
        patch("embry0.orchestration.nodes.triage.parse_triage_response", return_value={"action": "proceed"}),
    ):
        result = await function(state, config)
    return dict(result.update if isinstance(result, Command) else result)


def _executor(db: DatabasePool) -> Any:
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = db
    executor._jobs = JobsRepository(db)
    executor._inputs = IssueInputsRepository(db)
    executor._audit_log_path = None
    return executor


async def _job(db: DatabasePool, job_id: str, issue_id: str) -> None:
    await db.execute(
        "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Pick a database', $2, 'in_progress')",
        issue_id,
        COVERED,
    )
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id) VALUES ($1, $2, 'Pick a database', 'running', $3)",
        job_id,
        COVERED,
        issue_id,
    )


@pytest.mark.parametrize("node", ["triage", "developer", "review"])
@pytest.mark.parametrize("with_blocking", [False, True], ids=["alone", "beside-a-blocking-question"])
async def test_a_question_the_agent_answered_itself_becomes_one_auto_answered_input(
    db_with_migrations: DatabasePool, node: str, with_blocking: bool
) -> None:
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update(node, [AUTO, BLOCKING] if with_blocking else [AUTO])

    await _executor(db_with_migrations)._process_stream(_Graph(node, update), {}, {}, "iss-1", "job-1")

    rows = await IssueInputsRepository(db_with_migrations).list_by_job("job-1")
    recorded = [
        (r["asking_node"], r["importance"], r["status"], r["auto_answer"], r["issue_id"], QUESTION in r["question"])
        for r in rows
    ]
    assert recorded == [(node, "auto_answerable", "auto_answered", AUTO["suggested_answer"], "iss-1", True)], (
        f"the {node} node's question the agent answered itself is recorded once: {recorded}"
    )
    audit = await db_with_migrations.fetch(
        "SELECT actor, details FROM audit_log WHERE action = 'issue.input_created' AND issue_id = 'iss-1'"
    )
    assert [(a["actor"], a["details"]["input_id"]) for a in audit] == [(node, rows[0]["id"])], audit


async def test_a_question_with_no_suggested_answer_is_still_recorded(db_with_migrations: DatabasePool) -> None:
    """The workflow went on without an answer; the record says so instead of a pending question nobody waits on."""
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [{**AUTO, "suggested_answer": None}])

    await _executor(db_with_migrations)._process_stream(_Graph("developer", update), {}, {}, "iss-1", "job-1")

    rows = await IssueInputsRepository(db_with_migrations).list_by_job("job-1")
    assert [(r["status"], r["auto_answer"]) for r in rows] == [("auto_answered", None)], (
        f"a question the agent answered without naming an answer is recorded as auto-answered: {rows}"
    )


async def test_the_inputs_route_shows_it_only_to_a_key_that_reads_the_job_and_a_person_can_override_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        recorded = {}
        for repo in (COVERED, UNCOVERED):
            job_id, issue_id = values[repo]["job_id"], values[repo]["issue_id"]
            update = await _node_update("developer", [AUTO])
            await _executor(db_with_migrations)._process_stream(_Graph("developer", update), {}, {}, issue_id, job_id)
            found = [
                r["id"]
                for r in await IssueInputsRepository(db_with_migrations).list_by_job(job_id)
                if r["status"] == "auto_answered"
            ]
            assert len(found) == 1, f"{repo}: the question the agent answered itself is recorded once: {found}"
            recorded[repo] = found[0]

        seen = {}
        for repo in (COVERED, UNCOVERED):
            job_id = ids(repo)["job_id"]
            resp = await call(app, "GET", f"/api/v1/jobs/{job_id}/inputs", keys.limited)
            seen[repo] = (
                resp.status_code,
                [r["id"] for r in resp.json()] if resp.status_code == 200 and isinstance(resp.json(), list) else None,
            )
        missing = await call(app, "GET", "/api/v1/jobs/job-that-does-not-exist/inputs", keys.limited)
        everyone = await call(app, "GET", f"/api/v1/jobs/{ids(UNCOVERED)['job_id']}/inputs", MASTER)

        job_id = ids(COVERED)["job_id"]
        override = await call(
            app,
            "POST",
            f"/api/v1/jobs/{job_id}/inputs/{recorded[COVERED]}/answer",
            keys.limited,
            {"answer": "MySQL, the team runs it"},
        )
        after = await IssueInputsRepository(db_with_migrations).get(recorded[COVERED])

    assert seen[COVERED][0] == 200 and recorded[COVERED] in (seen[COVERED][1] or []), seen
    assert seen[UNCOVERED] == (missing.status_code, missing.json()) == (200, []), (
        f"another repository's job has no questions for the key, as a missing one has none: {seen}"
    )
    assert everyone.status_code == 200 and recorded[UNCOVERED] in [r["id"] for r in everyone.json()], everyone.text
    assert override.status_code == 200, override.text
    assert after is not None
    assert (after["status"], after["answer"], after["auto_answer"]) == (
        "answered",
        "MySQL, the team runs it",
        AUTO["suggested_answer"],
    ), "an override answers the row and keeps what the agent went on with"


async def _rows(db: DatabasePool, job_id: str = "job-1") -> list[dict[str, Any]]:
    return await IssueInputsRepository(db).list_by_job(job_id)


async def _audits(db: DatabasePool, issue_id: str = "iss-1") -> int:
    return int(
        await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'issue.input_created' AND issue_id = $1", issue_id
        )
    )


def _ask(n: int) -> dict[str, Any]:
    return {**AUTO, "question": f"Question {n:02d}?", "suggested_answer": f"answer {n}"}


async def test_two_questions_of_one_update_are_two_inputs(db_with_migrations: DatabasePool) -> None:
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [_ask(1), _ask(2)])

    await _executor(db_with_migrations)._process_stream(_Graph("developer", update), {}, {}, "iss-1", "job-1")

    answers = sorted(r["auto_answer"] for r in await _rows(db_with_migrations))
    assert answers == ["answer 1", "answer 2"], f"each question of the update is its own input: {answers}"
    assert await _audits(db_with_migrations) == 2


async def test_a_run_records_at_most_its_cap_and_says_so(db_with_migrations: DatabasePool) -> None:
    """They count toward no round cap, and each is a row, an audit row and a line of a resume's Q&A."""
    await _job(db_with_migrations, "job-1", "iss-1")
    with structlog.testing.capture_logs() as logs:
        update = await _node_update("developer", [_ask(n) for n in range(MAX_AUTO_ANSWERED_PER_RUN + 5)])

    await _executor(db_with_migrations)._process_stream(_Graph("developer", update), {}, {}, "iss-1", "job-1")

    rows = await _rows(db_with_migrations)
    assert len(rows) == MAX_AUTO_ANSWERED_PER_RUN, f"{len(rows)} recorded of {MAX_AUTO_ANSWERED_PER_RUN + 5}"
    capped = [entry for entry in logs if entry["event"] == "agent_auto_answered_questions_capped"]
    assert [(c["asked"], c["kept"]) for c in capped] == [(MAX_AUTO_ANSWERED_PER_RUN + 5, MAX_AUTO_ANSWERED_PER_RUN)]


async def test_the_questions_of_a_run_past_the_round_cap_are_still_recorded(db_with_migrations: DatabasePool) -> None:
    """The run asked a blocking question past the cap and failed; the ones it answered itself are its record."""
    from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP

    await _job(db_with_migrations, "job-1", "iss-1")
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=_Run([AUTO, BLOCKING], "")),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
    ):
        result = await developer_node(_state(agent_question_rounds=DEFAULT_ASK_USER_CAP), None)
    update = dict(result.update if isinstance(result, Command) else result)
    assert update.get("agent_questions_exhausted") is True, "the run is past the cap"

    await _executor(db_with_migrations)._process_stream(_Graph("developer", update), {}, {}, "iss-1", "job-1")

    recorded = [r["auto_answer"] for r in await _rows(db_with_migrations)]
    assert recorded == [AUTO["suggested_answer"]], f"the question the failed run answered itself: {recorded}"


async def test_an_update_whose_stage_did_not_change_is_still_recorded(db_with_migrations: DatabasePool) -> None:
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [AUTO])

    class _TwoUpdates(_Graph):
        async def astream(self, input_value: Any, config: Any = None, stream_mode: Any = None) -> Any:
            yield ("updates", {"developer": {"current_stage": update["current_stage"]}})
            yield ("updates", {"developer": update})

    await _executor(db_with_migrations)._process_stream(_TwoUpdates("developer", update), {}, {}, "iss-1", "job-1")

    assert len(await _rows(db_with_migrations)) == 1, "a question rides an update whatever its stage"


async def test_a_failed_write_is_logged_and_the_others_still_recorded(db_with_migrations: DatabasePool) -> None:
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [_ask(1), _ask(2)])
    executor = _executor(db_with_migrations)
    real = executor._inputs.record_auto_answer
    first = update["auto_answered_agent_questions"][0]["input_id"]

    async def flaky(**kwargs: Any) -> bool:
        if kwargs["input_id"] == first:
            raise ConnectionError("the database went away")
        return await real(**kwargs)

    executor._inputs.record_auto_answer = flaky
    with structlog.testing.capture_logs() as logs:
        try:
            await executor._process_stream(_Graph("developer", update), {}, {}, "iss-1", "job-1")
        except ConnectionError as exc:
            pytest.fail(f"a failed write of a question failed the workflow: {exc}")

    recorded = [r["auto_answer"] for r in await _rows(db_with_migrations)]
    assert recorded == ["answer 2"], f"the question after the failed write is still recorded: {recorded}"
    failed = [entry for entry in logs if entry["event"] == "auto_answer_persist_failed"]
    assert [entry["input_id"] for entry in failed] == [first], logs


async def test_an_update_streamed_twice_writes_its_questions_once(db_with_migrations: DatabasePool) -> None:
    """The same update twice, as a replay streams it, with and without the ids the node gives its questions."""
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [_ask(1)])
    without_ids = {
        **update,
        "auto_answered_agent_questions": [
            {k: v for k, v in q.items() if k != "input_id"} for q in update["auto_answered_agent_questions"]
        ],
    }
    for streamed in (update, without_ids):
        for _ in range(2):
            await _executor(db_with_migrations)._process_stream(_Graph("developer", streamed), {}, {}, "iss-1", "job-1")

    answers = [r["auto_answer"] for r in await _rows(db_with_migrations)]
    assert answers == ["answer 1", "answer 1"], f"one row with the node's id, one for the id-less copy: {answers}"
    assert await _audits(db_with_migrations) == 2, "an audit row for each row written, none for a replay"


@pytest.mark.parametrize("at_the_cap", [False, True], ids=["asks", "past-the-round-cap"])
async def test_triage_after_a_qa_failure_keeps_the_questions_it_answered_itself_when_it_asks(
    db_with_migrations: DatabasePool, at_the_cap: bool
) -> None:
    """Triage, run again after a QA failure, answers a question itself (folded into its result), then its
    decision asks the person a question. That second cap call answers its own, empty, list of questions the
    agent answered itself; laid over the result, it dropped the folded one, on both of its paths."""
    from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP
    from tests.unit.workflows.issue_to_pr.test_qa_failure_routing_in_triage import (
        _make_config,
        _make_qa_failed_state,
        _triage_decision_with_qa_failure_action,
    )

    await _job(db_with_migrations, "job-1", "iss-1")
    output = _triage_decision_with_qa_failure_action({"kind": "ask_user", "question": "Which behaviour is intended?"})
    state = _make_qa_failed_state()
    if at_the_cap:
        state["agent_question_rounds"] = DEFAULT_ASK_USER_CAP
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=_Run([AUTO], output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _event: None),
    ):
        result = await triage_node(state, _make_config())
    assert isinstance(result, Command) and result.goto == ("max_retries" if at_the_cap else "ask_user_interrupt")
    update = dict(result.update or {})
    carried = [q["auto_answer"] for q in update.get("auto_answered_agent_questions") or []]
    assert carried == [AUTO["suggested_answer"]], f"triage's update dropped the question it answered itself: {carried}"

    await _executor(db_with_migrations)._process_stream(_Graph("triage", update), {}, {}, "iss-1", "job-1")

    assert [(r["asking_node"], r["auto_answer"]) for r in await _rows(db_with_migrations)] == [
        ("triage", AUTO["suggested_answer"])
    ]


def test_joining_cap_updates_keeps_both_lists_once_each() -> None:
    from embry0.orchestration.nodes.agent import with_cap_updates

    a, b = {"input_id": "inp-a", "question": "A?"}, {"input_id": "inp-b", "question": "B?"}
    assert with_cap_updates({"auto_answered_agent_questions": [a]}, {"auto_answered_agent_questions": []})[
        "auto_answered_agent_questions"
    ] == [a], "an empty second list keeps the first"
    assert with_cap_updates({"auto_answered_agent_questions": [a]}, {"auto_answered_agent_questions": [a, b]})[
        "auto_answered_agent_questions"
    ] == [a, b], "both, each once, the earlier first"
    assert with_cap_updates({"x": 1}, {"auto_answered_agent_questions": [b], "x": 2}) == {
        "x": 2,
        "auto_answered_agent_questions": [b],
    }, "with nothing folded before, the cap's updates as they are"


# ---- a real graph, drained after the node and resumed ----


class _State(TypedDict, total=False):
    auto_answered_agent_questions: list[dict[str, Any]]
    current_stage: str


def _two_step_graph(update: dict[str, Any], executor: Any, saver: Any) -> Any:
    """``developer`` returns ``update``'s questions and asks for a drain while it runs; ``review`` follows."""

    async def developer(state: _State) -> dict[str, Any]:
        executor._draining = True  # a deploy asks for a drain while the node runs
        return {
            "auto_answered_agent_questions": update["auto_answered_agent_questions"],
            "current_stage": "developer_complete",
        }

    async def review(state: _State) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"current_stage": "review_complete"}

    builder = StateGraph(_State)
    builder.add_node("developer", developer)
    builder.add_node("review", review)
    builder.add_edge(START, "developer")
    builder.add_edge("developer", "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=saver)


@asynccontextmanager
async def _memory_saver(_db: DatabasePool) -> Any:
    yield InMemorySaver()


@asynccontextmanager
async def _postgres_saver(db: DatabasePool) -> Any:
    async with checkpointer_context(db._url) as saver:  # noqa: SLF001
        yield saver


@pytest.mark.parametrize(
    "saver",
    [_memory_saver, pytest.param(_postgres_saver, marks=pytest.mark.changes_schema)],
    ids=["in-memory checkpointer", "postgres checkpointer"],
)
async def test_a_drained_and_resumed_job_records_each_question_once(
    db_with_migrations: DatabasePool, saver: Any
) -> None:
    """A drain stops the run after the node's update streamed and before its step's checkpoint is committed;
    the resume streams the node's writes again, marked cached. That replay used to record each question a
    second time."""
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [AUTO])
    executor = _executor(db_with_migrations)
    executor._draining = False
    config = {"configurable": {"thread_id": "job-1"}}
    replayed: list[Any] = []
    async with saver(db_with_migrations) as checkpointer:
        graph = _two_step_graph(update, executor, checkpointer)
        with pytest.raises(ShutdownInterrupt):
            await executor._process_stream(graph, {}, config, "iss-1", "job-1", stop_on_drain=True)
        after_drain = len(await _rows(db_with_migrations))
        executor._draining = False
        stream = graph.astream

        async def watched(*args: Any, **kwargs: Any) -> Any:
            async for event in stream(*args, **kwargs):
                replayed.append(event)
                yield event

        graph.astream = watched
        with structlog.testing.capture_logs() as logs:
            await executor._process_stream(graph, None, config, "iss-1", "job-1", stop_on_drain=True)

    assert any(
        isinstance(event[1], dict) and (event[1].get("__metadata__") or {}).get("cached") for event in replayed
    ), f"the resume did not replay the node's update, so this measures nothing: {replayed}"
    rows = await _rows(db_with_migrations)
    assert (after_drain, len(rows)) == (1, 1), f"rows after the drain and after the resume: {(after_drain, len(rows))}"
    assert await _audits(db_with_migrations) == 1, "the replay wrote no second audit row"
    assert not [entry for entry in logs if entry["event"] == "auto_answer_persist_failed"], "the replay wrote nothing"


async def test_a_question_whose_first_write_failed_is_written_when_its_update_is_replayed(
    db_with_migrations: DatabasePool,
) -> None:
    """Skipping a replayed update would lose a question whose first write never happened (the process died, or
    the write failed): the replay is the one chance left to record it."""
    await _job(db_with_migrations, "job-1", "iss-1")
    update = await _node_update("developer", [AUTO])
    executor = _executor(db_with_migrations)
    executor._draining = False
    real = executor._inputs.record_auto_answer

    async def down(**_kwargs: Any) -> bool:
        raise ConnectionError("the database went away")

    executor._inputs.record_auto_answer = down
    config = {"configurable": {"thread_id": "job-1"}}
    graph = _two_step_graph(update, executor, InMemorySaver())
    with pytest.raises(ShutdownInterrupt):
        await executor._process_stream(graph, {}, config, "iss-1", "job-1", stop_on_drain=True)
    assert await _rows(db_with_migrations) == [], "the first write failed"
    executor._draining = False
    executor._inputs.record_auto_answer = real
    await executor._process_stream(graph, None, config, "iss-1", "job-1", stop_on_drain=True)

    recorded = [r["auto_answer"] for r in await _rows(db_with_migrations)]
    assert recorded == [AUTO["suggested_answer"]], f"the replay recorded the question: {recorded}"
