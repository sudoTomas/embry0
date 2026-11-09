"""done_when.on_unmet and salvage.enabled are read with the rest of a job's settings, once, when it starts (#182).

Both are per repository: the deployment's value, then its organization's
layer, then its own. A job keeps what it read to its end, a pause and a
resume included, and a job that started before the two keys existed reads
their defaults, which are what it did then, without a warning.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from embry0.orchestration.state import JobState
from embry0.settings.sections import CATALOG
from tests.unit.services.test_job_settings import WIDGETS, WIP, _job_settings, _resumer, _start, _Store


def _policy(state: dict[str, Any]) -> tuple[Any, Any]:
    """The job's ``(done_when.on_unmet, salvage.enabled)``, as it read them."""
    js = _job_settings()
    done_when, salvage = CATALOG.get("done_when"), CATALOG.get("salvage")
    assert done_when is not None and salvage is not None
    return (
        getattr(js.job_section(state, done_when), "on_unmet", "no on_unmet key"),
        getattr(js.job_section(state, salvage), "enabled", "no enabled key"),
    )


def test_a_job_that_kept_the_sections_before_the_keys_reads_what_it_did_then() -> None:
    from structlog.testing import capture_logs

    kept = {
        "job_id": "job-before-182",
        "job_settings": {
            "done_when": {"retries": 1, "ticket_sections": ["Acceptance criteria"]},
            "salvage": {"commit_subject": WIP},
        },
    }
    with capture_logs() as logs:
        assert _policy(kept) == ("pause", True), "a job from before the keys pauses and salvages, as it did"
        assert _policy({}) == ("pause", True), "so does a job that read no settings at all"
    assert not [e for e in logs if e.get("event") == "job_settings_refused"], "a key it never read is no warning"


@pytest.mark.asyncio
async def test_each_repository_reads_its_own_layers() -> None:
    js = _job_settings()
    store = _Store(
        {"done_when": {"on_unmet": "pause"}},
        **{
            "organization:acme": {"done_when": {"on_unmet": "review"}},
            f"repository:{WIDGETS}": {"done_when": {"on_unmet": "fail"}, "salvage": {"enabled": False}},
        },
    )
    seen = {repo: await js.read_for_job(store, repo) for repo in (WIDGETS, "acme/gizmos", "globex/gadgets", None)}
    assert {repo: _policy({"job_settings": s}) for repo, s in seen.items()} == {
        WIDGETS: ("fail", False),
        "acme/gizmos": ("review", True),
        "globex/gadgets": ("pause", True),
        None: ("pause", True),
    }, "the repository's layer over its organization's over the deployment's; no repository reads the deployment's"


@pytest.mark.asyncio
async def test_a_change_mid_job_reaches_the_next_job_not_the_running_one_or_its_resume() -> None:
    layer = {f"repository:{WIDGETS}": {"done_when": {"on_unmet": "fail"}, "salvage": {"enabled": False}}}
    store = _Store(**layer)
    first = await _start(store, WIDGETS)
    assert _policy(first) == ("fail", False), (
        f"the job did not read its repository's on_unmet and enabled: {_policy(first)}"
    )
    seen: list[tuple[str, Any]] = []

    async def before(state: dict[str, Any]) -> dict[str, Any]:
        seen.append(("before", _policy(state)))
        return {"current_stage": "developer_complete", "sandbox_container_id": "sandbox-job-1"}

    async def asks(state: dict[str, Any]) -> dict[str, Any]:
        interrupt({"reason": "ask_user", "questions": [{"id": "q-1", "question": "Which export?"}]})
        seen.append(("after", _policy(state)))
        return {"current_stage": "review_complete"}

    builder = StateGraph(JobState)
    builder.add_node("before", before)
    builder.add_node("asks", asks)
    builder.add_edge(START, "before")
    builder.add_edge("before", "asks")
    builder.add_edge("asks", END)
    saver = InMemorySaver()
    await builder.compile(checkpointer=saver).ainvoke(first, {"configurable": {"thread_id": "job-1"}})

    store.write(**{f"repository:{WIDGETS}": {"done_when": {"on_unmet": "review"}}})
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

    assert seen == [("before", ("fail", False)), ("after", ("fail", False))], (
        f"a resumed job keeps the on_unmet and salvage.enabled it started with: {seen}"
    )
    second = await _start(store, WIDGETS, job_id="job-2")
    assert _policy(second) == ("review", True), "the next job reads the layer as it is now"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_repository_layer_saved_in_the_store_reaches_that_repository_s_next_job(
    db_with_migrations: Any,
) -> None:
    from embry0.settings.document import load_yaml
    from embry0.settings.scope import Scope
    from embry0.settings.store import SettingsStore

    store = SettingsStore(db_with_migrations)
    text = "done_when:\n  on_unmet: fail\nsalvage:\n  enabled: false\n"
    await store.save(text, load_yaml(text), scope=Scope.repository(WIDGETS), actor="alex@example.com")
    widgets = await _start(store, WIDGETS)
    gizmos = await _start(store, "acme/gizmos", job_id="job-2")
    assert _policy(widgets) == ("fail", False), widgets.get("job_settings")
    assert _policy(gizmos) == ("pause", True), "another repository keeps the defaults"
