"""A coding job reads its done_when and salvage settings once, when it starts (#182).

The values come from the settings document for the job's repository: the
deployment's, then its organization's layer, then its own. They ride the
job's checkpointed state, so a change to the document mid-job reaches the next
job that starts and never the running one, not even after a pause and a
resume. The module is imported inside each test, so a tree without it fails
each one with its own message.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from embry0.orchestration.state import JobState
from embry0.services import done_when as dw
from embry0.services.issue_executor import IssueExecutor
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot

WIDGETS = "acme/widgets"
WIP = "wip: unfinished — {agent} stopped ({reason})"
CHORE = "chore(wip): {agent} stopped ({reason})"
TICKET = "Export the report\n\n## Definition of done\n- The CSV has a header row\n- Totals match the screen\n"


def _job_settings() -> ModuleType:
    try:
        from embry0.services import job_settings
    except ImportError:
        pytest.fail("embry0.services.job_settings is missing: no job reads the done_when and salvage sections")
    return job_settings


def _section(name: str) -> Any:
    found = CATALOG.get(name)
    assert found is not None, f"the settings document has no {name} section"
    return found


class _Store:
    """A settings store over stored rows: the deployment's document and the layers named in ``layers``."""

    def __init__(self, deployment: dict[str, Any] | None = None, **layers: dict[str, Any]) -> None:
        self.deployment = deployment or {}
        self.layers = layers
        self.reads = 0

    def write(self, deployment: dict[str, Any] | None = None, **layers: dict[str, Any]) -> None:
        self.deployment = deployment or {}
        self.layers = layers

    async def snapshot(self) -> Any:
        self.reads += 1
        rows = [{"scope": "deployment", "data": self.deployment, "generation": 1, "updated_at": None}]
        rows += [
            {"scope": scope, "data": data, "generation": 1, "updated_at": None} for scope, data in self.layers.items()
        ]
        return build_snapshot(CATALOG, rows, log=False)


def _layer(retries: int, headings: list[str], subject: str) -> dict[str, Any]:
    return {
        "done_when": {"retries": retries, "ticket_sections": headings},
        "salvage": {"commit_subject": subject},
    }


def _starter(store: Any, captured: list[dict[str, Any]]) -> IssueExecutor:
    """An executor whose ``_run_workflow`` is real up to the graph, which it hands to ``captured``."""
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value={"trace_id": None, "pipeline_config": None})
    jobs.update = AsyncMock()
    issues = MagicMock()
    issues.update = AsyncMock()

    async def fake_stream(input_value: Any, issue_id: Any, job_id: str, state_sink: Any = None) -> Any:
        captured.append(input_value)
        return None, False, None

    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = jobs
    ex._issues = issues
    ex._traces = MagicMock()
    ex._registry = MagicMock()
    ex._database_url = ""
    ex._audit_log_path = None
    ex._db = None
    ex._inputs = None
    ex._config = SimpleNamespace(environment_secret_key="k")
    ex._sandbox = None
    ex._agent_runner = None
    ex._proxy = None
    ex._event_bus = None
    ex._env_repo = None
    ex._background_tasks = set()
    ex._tasks_by_job = {}
    ex._settings = store
    ex._execute_workflow_stream = fake_stream  # type: ignore[method-assign]
    ex._cleanup_sandbox = AsyncMock()  # type: ignore[method-assign]
    return ex


async def _start(store: Any, repo: str, job_id: str = "job-1") -> dict[str, Any]:
    """The initial state ``_run_workflow`` builds for a job on ``repo``."""
    captured: list[dict[str, Any]] = []
    await _starter(store, captured)._run_workflow("iss-1", job_id, {"title": "T", "body": TICKET, "repo": repo})
    assert captured, "_run_workflow never reached the graph"
    return captured[0]


def _policy(state: dict[str, Any]) -> tuple[int, tuple[str, ...], str]:
    js = _job_settings()
    done = js.job_section(state, _section("done_when"))
    return done.retries, tuple(done.ticket_sections), js.job_section(state, _section("salvage")).commit_subject


@pytest.mark.asyncio
async def test_a_repository_layer_sets_the_headings_for_its_own_repository_only() -> None:
    js = _job_settings()
    store = _Store(**{f"repository:{WIDGETS}": {"done_when": {"ticket_sections": ["Definition of done"]}}})
    widgets = await js.read_for_job(store, WIDGETS)
    gizmos = await js.read_for_job(store, "acme/gizmos")
    assert widgets["done_when"]["ticket_sections"] == ["Definition of done"], widgets
    assert gizmos["done_when"]["ticket_sections"] == ["Acceptance criteria"], (
        f"another repository of the same organization keeps the deployment's heading: {gizmos}"
    )
    assert await js.read_for_job(store, None) == await js.read_for_job(store, "globex/gadgets"), (
        "a job with no repository reads the deployment's values, like a repository with no layer"
    )
    done_when = _section("done_when")
    found = dw.resolve(
        None, TICKET, ticket_sections=js.job_section({"job_settings": widgets}, done_when).ticket_sections
    )
    assert found is not None and [i["done"] for i in found["items"]] == [
        "The CSV has a header row",
        "Totals match the screen",
    ], f"the repository's ticket heading is its job's finish line: {found}"
    assert (
        dw.resolve(None, TICKET, ticket_sections=js.job_section({"job_settings": gizmos}, done_when).ticket_sections)
        is None
    ), "the same ticket on a repository without the layer has no finish line under Definition of done"


@pytest.mark.asyncio
async def test_an_organization_layer_reaches_its_repositories_and_a_repository_layer_wins() -> None:
    js = _job_settings()
    store = _Store(
        {"done_when": {"retries": 2}},
        **{
            "organization:acme": {"done_when": {"retries": 3}, "salvage": {"commit_subject": CHORE}},
            f"repository:{WIDGETS}": {"done_when": {"retries": 0}},
        },
    )
    seen = {repo: await js.read_for_job(store, repo) for repo in (WIDGETS, "acme/gizmos", "globex/gadgets")}
    assert {repo: (s["done_when"]["retries"], s["salvage"]["commit_subject"]) for repo, s in seen.items()} == {
        WIDGETS: (0, CHORE),
        "acme/gizmos": (3, CHORE),
        "globex/gadgets": (2, WIP),
    }, seen


@pytest.mark.asyncio
async def test_a_job_reads_its_settings_once_when_it_starts() -> None:
    store = _Store(**{f"repository:{WIDGETS}": _layer(0, ["Definition of done"], CHORE)})
    state = await _start(store, WIDGETS)
    assert "job_settings" in state, "the job started without reading its settings: every node falls back to code"
    assert store.reads == 1, f"one snapshot per job start, read {store.reads} times"
    assert _policy(state) == (0, ("Definition of done",), CHORE), state["job_settings"]


@pytest.mark.asyncio
async def test_a_change_mid_job_reaches_the_next_job_not_the_running_one_or_its_resume() -> None:
    """Start a job, pause it, change the document, resume it: it keeps what it started with."""
    store = _Store(**{f"repository:{WIDGETS}": _layer(0, ["Definition of done"], CHORE)})
    first = await _start(store, WIDGETS)
    assert "job_settings" in first, "the job started without reading its settings"
    seen: list[tuple[str, Any]] = []
    unmet = {
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False, "output": '{"done_when": [{"id": "d1", "met": false}]}'}
        ]
    }
    listed = dw.parse_done_when({"items": [{"done": "The CSV has a header row"}]})

    async def before(state: dict[str, Any]) -> dict[str, Any]:
        seen.append(("before", _policy(state)))
        return {"current_stage": "developer_complete", "sandbox_container_id": "sandbox-job-1"}

    async def asks(state: dict[str, Any]) -> dict[str, Any]:
        interrupt({"reason": "ask_user", "questions": [{"id": "q-1", "question": "Which export?"}]})
        from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

        seen.append(("after", _policy(state)))
        _updates, goto = await check_done_when(state, {}, unmet, listed, writer=lambda _e: None)
        seen.append(("check", goto))
        return {"current_stage": "review_complete"}

    builder = StateGraph(JobState)
    builder.add_node("before", before)
    builder.add_node("asks", asks)
    builder.add_edge(START, "before")
    builder.add_edge("before", "asks")
    builder.add_edge("asks", END)
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "job-1"}}
    await builder.compile(checkpointer=saver).ainvoke(first, config)
    assert seen == [("before", (0, ("Definition of done",), CHORE))], seen

    store.write(**{f"repository:{WIDGETS}": _layer(2, ["Acceptance criteria"], "fix:")})
    reads = store.reads
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

    assert seen[1:] == [("after", (0, ("Definition of done",), CHORE)), ("check", "max_retries")], (
        "a resumed job must read the settings it started with, not the document as it is now "
        f"(retries 0 pauses at the first unmet report): {seen}"
    )
    assert store.reads == reads, "a resume must not read the settings document again"
    second = await _start(store, WIDGETS, job_id="job-2")
    assert _policy(second) == (2, ("Acceptance criteria",), "fix:"), "the next job reads the document as it is now"


def _resumer(builder: StateGraph, store: Any) -> IssueExecutor:
    """An executor whose ``resume`` is real down to the graph (as test_resume_carries_pre_pause_facts.py)."""

    class _Workflow:
        def compile(self, config: dict[str, Any]) -> Any:
            return builder.compile(checkpointer=config["checkpointer"])

    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value={"repo": WIDGETS, "task": "T", "status": "awaiting_input"})
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.get = AsyncMock(return_value={"status": "awaiting_input"})
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._github_token = "t"
    ex._repo_prefs = None
    ex._settings = store
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    ex._sandbox.reenroll = AsyncMock()
    ex._forge_for = AsyncMock(return_value="github")  # type: ignore[method-assign]
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    ex._select_workflow = lambda _pipeline: _Workflow()  # type: ignore[method-assign]
    return ex


@pytest.mark.asyncio
async def test_without_a_store_or_with_one_that_fails_a_job_runs_on_the_defaults() -> None:
    js = _job_settings()
    defaults = {
        "done_when": {"retries": 1, "ticket_sections": ["Acceptance criteria"]},
        "salvage": {"commit_subject": WIP},
    }
    assert await js.read_for_job(None, WIDGETS) == defaults
    broken = MagicMock()
    broken.snapshot = AsyncMock(side_effect=RuntimeError("database is down"))
    assert await js.read_for_job(broken, WIDGETS) == defaults, "a store that cannot be read never stops a job"


def test_a_job_from_before_the_sections_reads_their_defaults() -> None:
    js = _job_settings()
    assert _policy({}) == (1, ("Acceptance criteria",), WIP), "a job that read no settings runs as before"
    kept = {"job_settings": {"done_when": {"retries": 2, "gone_key": True}, "salvage": {"commit_subject": "\n"}}}
    assert _policy(kept) == (2, ("Acceptance criteria",), WIP), (
        "a key a later release dropped is ignored, and a value the section now refuses reads as its default"
    )
    assert js.STATE_KEY == "job_settings"


def test_a_refused_key_falls_back_alone_and_is_warned_about_once_per_job() -> None:
    """A value a later release refuses reads as its default; the job keeps every other key it read."""
    from structlog.testing import capture_logs

    js = _job_settings()
    done_when = _section("done_when")
    kept = {"done_when": {"retries": 2, "ticket_sections": ["## written with its marks"]}}
    with capture_logs() as logs:
        for job_id in ("job-refused-1", "job-refused-1", "job-refused-1", "job-refused-2"):
            read = js.job_section({"job_id": job_id, "job_settings": kept}, done_when)
            assert (read.retries, tuple(read.ticket_sections)) == (2, ("Acceptance criteria",)), (
                "only the refused key falls back to its default: the job keeps the retries it read"
            )
    warned = [(e.get("job_id"), e.get("keys")) for e in logs if e.get("event") == "job_settings_refused"]
    assert warned == [("job-refused-1", ["ticket_sections"]), ("job-refused-2", ["ticket_sections"])], (
        f"one warning per job naming the refused key, not one per node call: {warned}"
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_repository_layer_saved_in_the_store_reaches_that_repository_s_next_job(
    db_with_migrations: Any,
) -> None:
    """End to end through the real store: a repository's layer as an operator saves it, read at a job's start."""
    from embry0.settings.document import load_yaml
    from embry0.settings.scope import Scope
    from embry0.settings.store import SettingsStore

    store = SettingsStore(db_with_migrations)
    text = "done_when:\n  retries: 0\n  ticket_sections: [Definition of done]\nsalvage:\n  commit_subject: 'chore(wip): {agent} stopped ({reason})'\n"
    await store.save(text, load_yaml(text), scope=Scope.repository(WIDGETS), actor="alex@example.com")
    widgets = await _start(store, WIDGETS)
    gizmos = await _start(store, "acme/gizmos", job_id="job-2")
    assert "job_settings" in widgets, "the job started without reading its settings"
    assert _policy(widgets) == (0, ("Definition of done",), CHORE), widgets["job_settings"]
    assert _policy(gizmos) == (1, ("Acceptance criteria",), WIP), "another repository keeps the defaults"
