"""A stored JSONB value too deep and too long to read fails its read, loudly, and only a few reads show it (#527).

The codec does not read a value nested past ``json.loads`` and longer than
``DEEP_DECODE_MAX_CHARS``: reading it would hold the event loop. Before, it
read the value as far as the limit and every key after the cut went missing,
so a reader took each for its default: a budget, a review setting, a watcher
stack's allowlist, a job's tools or done-when criteria. Now the read raises
``StoredValueTooDeep`` (answered 409), so no reader can. Only a direct database
write stores such a value: the API refuses a document nested past 200 levels.
The job list and a job's detail still show the other rows, and a whole
settings document or a preferences write replaces the value.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.json_text import UNSERVABLE_TEXT
from embry0.storage.database import StoredValueTooDeep

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

#: A chain 12,000 levels deep, about 324 KB: past ``json.loads`` and past the read limit.
_JUNK = '{"p": "xxxxxxxxxxxx", "q": ' * 12_000 + '"leaf"' + "}" * 12_000


def _doc(after: dict[str, Any]) -> str:
    """``{"a": <the chain>, **after}``: jsonb sorts ``a`` first, so every key that matters is past where a cut was."""
    return '{"a": ' + _JUNK + "".join(f", {json.dumps(k)}: {json.dumps(v)}" for k, v in after.items()) + "}"


def _list(items: list[Any]) -> str:
    """``[<the chain>, *items]``: a list whose deep element comes first."""
    return "[" + _JUNK + "".join(f", {json.dumps(i)}" for i in items) + "]"


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app answering an error with its status, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


# ---- every reader fails loudly: none takes a default (the round-7 probes) ----


async def test_the_settings_document_is_not_read(client: AsyncClient) -> None:
    from embry0.settings.store import build_snapshot  # noqa: F401 - the reader a snapshot runs

    app = client.app  # type: ignore[attr-defined]
    budget = {"budget": {"max_budget_per_job_usd": 1.5, "daily_cap_usd": 3.0}}
    await app.state.db.execute("UPDATE settings_documents SET data = $1::text::jsonb", _doc(budget))
    with pytest.raises(StoredValueTooDeep, match="in settings_documents"):
        await app.state.settings._documents.all()
    answer = await client.get("/api/v1/config/settings")
    assert answer.status_code == 409 and "settings_documents" in answer.json()["detail"], answer.text


@pytest.mark.parametrize(
    ("table", "column", "where", "text", "read"),
    [
        ("jobs", "pipeline_config", "job_id = 'job-deep'", _doc({"agent_tools": {"developer": ["Read"]}}), "job"),
        ("jobs", "done_when", "job_id = 'job-deep'", _list([{"text": "criterion"}]), "done_when"),
        ("repo_preferences", "pr_review", "repo = 'acme/widgets'", _doc({"enabled": True}), "preferences"),
        ("watcher_stacks", "settings", "repo = 'acme/widgets'", _doc({"auto_accept": False}), "stack"),
        ("organizations", "settings", "owner = 'acme'", _doc({"rediscover_hours": 24}), "organization"),
        ("provider_config", "fallback_models", "true", _list(["model-1"]), "provider_config"),
        ("sandbox_profiles", "cap_drop", "name = 'deep'", _list(["ALL"]), "profile"),
        ("agent_definitions", "mcp_servers", "type = 'developer'", _doc({"docs": {"command": "x"}}), "agent"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 40 else "",
)
async def test_a_reader_meets_an_error_not_a_default(
    client: AsyncClient, table: str, column: str, where: str, text: str, read: str
) -> None:
    from embry0.services.done_when import current as done_when_current
    from embry0.services.watcher_stacks import WatcherStack
    from embry0.storage.repositories.organizations import OrganizationsRepository
    from embry0.storage.repositories.provider_config import ProviderConfigRepository
    from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    await db.execute(
        "INSERT INTO jobs (job_id, task, repo, status) VALUES ('job-deep', 't', 'acme/widgets', 'running')"
    )
    await db.execute("INSERT INTO repos (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO repo_preferences (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await WatcherStacksRepository(db).upsert(WatcherStack(repo="acme/widgets", logql='{app="x"}'))
    await db.execute("INSERT INTO organizations (owner, forge) VALUES ('acme', 'github') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO sandbox_profiles (name) VALUES ('deep') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT DO NOTHING")
    await db.execute(f'UPDATE {table} SET "{column}" = $1::text::jsonb WHERE {where}', text)

    reads = {
        "job": lambda: app.state.jobs_repo.get("job-deep"),
        "done_when": lambda: done_when_current(app.state.jobs_repo, "job-deep", None),
        "preferences": lambda: RepoPreferencesRepository(db).get("acme/widgets"),
        "stack": lambda: WatcherStacksRepository(db).get("acme/widgets"),
        "organization": lambda: OrganizationsRepository(db).get("acme"),
        "provider_config": lambda: ProviderConfigRepository(db).get(),
        "profile": lambda: app.state.profiles_repo.get("deep"),
        "agent": lambda: app.state.agent_defs_repo.get("developer"),
    }
    with pytest.raises(StoredValueTooDeep, match=f"in {table}"):
        await reads[read]()


# ---- the reads that show rows: the job list and a job's detail ----


async def test_the_job_list_still_lists_the_other_jobs(client: AsyncClient) -> None:
    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    for job_id in ("job-deep", "job-ok"):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, status, pipeline_config) VALUES ($1, 't', 'acme/widgets', "
            "'completed', $2::text::jsonb)",
            job_id,
            json.dumps({"x": 1}),
        )
    await db.execute("UPDATE jobs SET pipeline_config = $1::text::jsonb WHERE job_id = 'job-deep'", _doc({"y": 2}))

    with pytest.raises(StoredValueTooDeep):
        await app.state.jobs_repo.list_all()  # any other reader of the list still fails
    listed = await client.get("/api/v1/jobs")
    assert listed.status_code == 200, listed.text[:300]
    jobs = {job["job_id"]: job for job in listed.json()["jobs"]}
    assert set(jobs) == {"job-deep", "job-ok"}, f"the other jobs are lost with the deep one: {sorted(jobs)}"
    assert jobs["job-ok"]["pipeline_config"] == {"x": 1}
    assert jobs["job-deep"]["pipeline_config"] == UNSERVABLE_TEXT, jobs["job-deep"]["pipeline_config"]

    one = await client.get("/api/v1/jobs/job-deep")
    assert one.status_code == 200, one.text[:300]
    assert one.json()["pipeline_config"] == UNSERVABLE_TEXT
    with pytest.raises(StoredValueTooDeep, match="in jobs"):
        await app.state.jobs_repo.get("job-deep")  # a run's read still fails


# ---- the repairs ----


async def test_a_whole_settings_document_replaces_one_too_deep_to_read(client: AsyncClient) -> None:
    from embry0.settings.sections.budget import BUDGET

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    stored = _doc({"budget": {"daily_cap_usd": 3.0}})
    await db.execute("UPDATE settings_documents SET data = $1::text::jsonb", stored)

    patched = await client.patch("/api/v1/config/settings/budget", json={"daily_cap_usd": 4.0})
    assert patched.status_code == 409, patched.text[:300]
    assert "replace the whole document (PUT)" in patched.json()["detail"], patched.text

    put = await client.put(
        "/api/v1/config/settings",
        content=b"budget:\n  daily_cap_usd: 4.0\n",
        headers={"Content-Type": "application/yaml"},
    )
    assert put.status_code == 200, f"a whole document does not replace it: {put.status_code} {put.text[:300]}"
    assert len(await db.fetchval("SELECT data::text FROM settings_documents WHERE scope = 'deployment'")) < 100
    assert (await app.state.settings.snapshot()).get(BUDGET).daily_cap_usd == 4.0
    before = await db.fetchval("SELECT before::text FROM config_revisions ORDER BY id DESC LIMIT 1")
    assert json.loads(before)["data_text"] == await db.fetchval("SELECT $1::text::jsonb::text", stored), (
        "the change record does not keep the old text"
    )


async def test_a_preferences_write_replaces_a_column_too_deep_to_read(client: AsyncClient) -> None:
    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    await db.execute("INSERT INTO repos (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO repo_preferences (repo, notes) VALUES ('acme/widgets', 'kept')")
    stored = _doc({"enabled": True})
    await db.execute("UPDATE repo_preferences SET pr_review = $1::text::jsonb WHERE repo = 'acme/widgets'", stored)
    url = "/api/v1/repos/acme/widgets/-/preferences"

    beside = await client.put(url, json={"language_hint": "python"})
    assert beside.status_code == 409, beside.text[:300]
    assert "repo_preferences.pr_review" in beside.json()["detail"] and "replace pr_review" in beside.json()["detail"]

    repaired = await client.put(url, json={"pr_review": {"enabled": False}})
    assert repaired.status_code == 200, f"a write does not replace it: {repaired.status_code} {repaired.text[:300]}"
    row = await app.state.repo_preferences_repo.get("acme/widgets")
    assert row["pr_review"]["enabled"] is False and row["notes"] == "kept", row
    before = await db.fetchval(
        "SELECT before::text FROM repo_config_revisions WHERE repo = 'acme/widgets' ORDER BY id DESC LIMIT 1"
    )
    assert json.loads(before)["unread"]["pr_review"] == await db.fetchval("SELECT $1::text::jsonb::text", stored), (
        "the change record does not keep the old text"
    )
    revision_id = await db.fetchval(
        "SELECT id FROM repo_config_revisions WHERE repo = 'acme/widgets' ORDER BY id DESC LIMIT 1"
    )
    undone = await client.post(f"/api/v1/repos/acme/widgets/-/history/{revision_id}/undo")
    assert undone.status_code == 409 and "cannot be put back" in undone.json()["detail"], (
        f"an undo puts back nothing in its place, silently: {undone.status_code} {undone.text[:300]}"
    )


# ---- the run path: each config read fails the run, never defaults (round 8) ----


async def _plant(db: Any, sql: str, text: str) -> None:
    await db.execute("INSERT INTO repos (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO repo_preferences (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT DO NOTHING")
    await db.execute(sql, text)


async def test_an_agents_definition_is_not_read_as_the_codes(client: AsyncClient) -> None:
    """load_agent_definition, as the developer, review, triage, PR-review and generic agent nodes call it."""
    from embry0.orchestration.nodes.agent import load_agent_definition

    db = client.app.state.db  # type: ignore[attr-defined]
    await _plant(db, "UPDATE agent_definitions SET mcp_servers = $1::text::jsonb WHERE type = 'developer'", _doc({}))
    fallback = {"model": "m", "tools": ["Read", "Write", "Edit", "Bash"]}
    with pytest.raises(StoredValueTooDeep, match="in agent_definitions"):
        await load_agent_definition({"configurable": {"db": db}}, "developer", fallback)


async def test_the_model_policy_is_not_read_as_none(client: AsyncClient) -> None:
    from embry0.orchestration.nodes.agent import load_model_policy

    db = client.app.state.db  # type: ignore[attr-defined]
    await _plant(db, "UPDATE provider_config SET fallback_models = $1::text::jsonb", _list(["model-1"]))
    with pytest.raises(StoredValueTooDeep, match="in provider_config"):
        await load_model_policy({"configurable": {"db": db}})


async def test_the_precheck_takes_no_policy_for_none(client: AsyncClient) -> None:
    from types import SimpleNamespace

    from embry0.services._executor_run import _model_policy_for_precheck

    db = client.app.state.db  # type: ignore[attr-defined]
    await _plant(db, "UPDATE provider_config SET fallback_models = $1::text::jsonb", _list(["model-1"]))
    with pytest.raises(StoredValueTooDeep, match="in provider_config"):
        await _model_policy_for_precheck(SimpleNamespace(_db=db))  # type: ignore[arg-type]


async def test_the_budget_is_not_read_as_the_variables(client: AsyncClient) -> None:
    """With no snapshot held, the budget is read from the document: one too deep to read is not the variables."""
    from embry0.orchestration.nodes.agent import load_budget_settings

    app = client.app  # type: ignore[attr-defined]
    budget = {"budget": {"max_budget_per_job_usd": 2.0}}
    await _plant(app.state.db, "UPDATE settings_documents SET data = $1::text::jsonb", _doc(budget))
    with pytest.raises(StoredValueTooDeep, match="in settings_documents"):
        await load_budget_settings({"configurable": {"db": app.state.db, "embry0_config": app.state.config}})


async def test_the_preferences_are_not_read_as_none(client: AsyncClient) -> None:
    from embry0.orchestration.nodes.agent import _load_repo_preferences
    from embry0.workflows.pr_review.nodes import _repo_preferences

    app = client.app  # type: ignore[attr-defined]
    sql = "UPDATE repo_preferences SET pr_review = $1::text::jsonb WHERE repo = 'acme/widgets'"
    await _plant(app.state.db, sql, _doc({"enabled": True}))
    configurable = {"repo_preferences_repo": app.state.repo_preferences_repo}
    with pytest.raises(StoredValueTooDeep, match="in repo_preferences"):
        await _load_repo_preferences({"repo": "acme/widgets"}, {"configurable": configurable})
    with pytest.raises(StoredValueTooDeep, match="in repo_preferences"):
        await _repo_preferences(configurable, "acme/widgets")


async def test_a_stacks_container_allowlist_is_not_read_as_none(client: AsyncClient) -> None:
    from embry0.services.job_log_access import repository_log_containers
    from embry0.services.watcher_stacks import WatcherStack
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    app = client.app  # type: ignore[attr-defined]
    stacks = WatcherStacksRepository(app.state.db)
    await stacks.upsert(WatcherStack(repo="acme/widgets", logql='{app="x"}'))
    sql = "UPDATE watcher_stacks SET settings = $1::text::jsonb WHERE repo = 'acme/widgets'"
    await _plant(app.state.db, sql, _doc({"containers": "app-web"}))
    with pytest.raises(StoredValueTooDeep, match="in watcher_stacks"):
        await repository_log_containers("acme/widgets", {"watcher_stacks_repo": stacks}, app.state.config)


async def test_an_issue_still_shows_with_a_job_too_deep_to_read(client: AsyncClient) -> None:
    """GET /issues/{id} reads its jobs as the job list does: the issue and its other jobs still show (round 8)."""
    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    issue = await client.post("/api/v1/issues", json={"title": "t", "repo": "acme/widgets"})
    assert issue.status_code in (200, 201), issue.text[:300]
    issue_id = issue.json()["id"]
    for job_id in ("job-deep", "job-ok"):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, status, issue_id) VALUES ($1, 't', 'acme/widgets', 'completed', $2)",
            job_id,
            issue_id,
        )
    await db.execute("UPDATE jobs SET pipeline_config = $1::text::jsonb WHERE job_id = 'job-deep'", _doc({"y": 2}))

    shown = await client.get(f"/api/v1/issues/{issue_id}")
    assert shown.status_code == 200, (
        f"the whole issue fails with one of its jobs: {shown.status_code} {shown.text[:300]}"
    )
    jobs = {job["job_id"]: job for job in shown.json()["jobs"]}
    assert set(jobs) == {"job-deep", "job-ok"}, sorted(jobs)
    assert jobs["job-deep"]["pipeline_config"] == UNSERVABLE_TEXT, jobs["job-deep"]["pipeline_config"]


# ---- a loop over rows leaves the other rows (round 8) ----


async def test_one_stack_too_deep_to_read_leaves_the_others_watched(client: AsyncClient) -> None:
    from unittest.mock import AsyncMock, MagicMock

    import structlog

    from embry0.services.watcher_fleet import WatcherFleet, read_enabled_stacks
    from embry0.services.watcher_stacks import WatcherStack
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    db = client.app.state.db  # type: ignore[attr-defined]
    stacks = WatcherStacksRepository(db)
    for repo in ("acme/deep", "acme/widgets"):
        await db.execute("INSERT INTO repos (repo) VALUES ($1) ON CONFLICT DO NOTHING", repo)
        await stacks.upsert(WatcherStack(repo=repo, logql='{app="x"}'))
    await db.execute("UPDATE watcher_stacks SET settings = $1::text::jsonb WHERE repo = 'acme/deep'", _doc({}))

    async def read() -> Any:
        return await read_enabled_stacks(stacks)

    # The startup's look (#746): the fleet's first.
    fleet = WatcherFleet(
        build=lambda stack: SimpleNamespace(stack_config=SimpleNamespace(watcher_max_open_proposals=1)),
        read_stacks=read,
        runs_repo=MagicMock(last_tick_times=AsyncMock(return_value={})),
        run_loops=False,
        background_tasks=set(),
    )
    with structlog.testing.capture_logs() as logs:
        await fleet.sync()
        await fleet.sync()
    assert sorted(fleet.services) == ["acme/widgets"], "one bad stack leaves every stack unwatched"
    named = [entry for entry in logs if entry.get("event") == "watcher_stack_unreadable"]
    assert [entry.get("repo") for entry in named] == ["acme/deep"] and named[0]["log_level"] == "critical", (
        f"said once, while it stays unreadable: {logs}"
    )


async def test_one_organization_too_deep_to_read_leaves_the_others_rediscovered(client: AsyncClient) -> None:
    import structlog

    from embry0.storage.repositories.organizations import OrganizationsRepository
    from tests.unit.services.test_org_rediscovery import T0, Clock, FakeRuns, make_loop

    db = client.app.state.db  # type: ignore[attr-defined]
    for owner in ("acme", "beta"):
        await db.execute(
            "INSERT INTO organizations (owner, forge, status, settings) VALUES ($1, 'github', 'active', $2::text::jsonb)",
            owner,
            json.dumps({"rediscover_hours": 6}),
        )
    await db.execute("UPDATE organizations SET settings = $1::text::jsonb WHERE owner = 'acme'", _doc({}))
    loop = make_loop(OrganizationsRepository(db), FakeRuns(), Clock(T0))  # type: ignore[arg-type]

    with structlog.testing.capture_logs() as logs:
        await loop.tick()
        await loop.tick()  # a second tick: beta is running, acme is not logged again
    assert loop.started == ["beta"], f"one bad organization stops every rediscovery: {loop.started}"
    named = [entry for entry in logs if entry.get("event") == "org_rediscovery_unreadable"]
    assert [entry.get("owner") for entry in named] == ["acme"] and named[0]["log_level"] == "error", logs


# ---- a fresh job fails, against the real repository (round 9) ----


@pytest.mark.parametrize("path", ["run", "review"])
async def test_a_fresh_job_too_deep_to_read_fails_from_pending(client: AsyncClient, path: str) -> None:
    """The job never ran: it goes from pending to failed (ERR_STORAGE), with no started_at, and its issue reopens."""
    from unittest.mock import AsyncMock

    from embry0.services._executor_run import _run_pr_review_workflow
    from tests.unit.services.test_failed_job_pr import PR, _executor, _patches

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    issue = await app.state.issues_repo.create(title="t", repo="acme/widgets")
    issue_id = issue["id"] if isinstance(issue, dict) else issue
    await db.execute("UPDATE issues SET status = 'in_progress' WHERE id = $1", issue_id)
    job = await app.state.jobs_repo.create(repo="acme/widgets", task="t", issue_id=issue_id)
    await db.execute("UPDATE jobs SET pipeline_config = $1::text::jsonb WHERE job_id = $2", _doc({}), job)

    ex = _executor()
    ex._jobs, ex._issues = app.state.jobs_repo, app.state.issues_repo
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    started: list[str] = []

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        started.append(job_id)
        raise RuntimeError("the workflow ran")

    ex._execute_workflow_stream = _stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        if path == "run":
            await ex._run_workflow(issue_id, job, {"title": "t", "body": "", "repo": "acme/widgets"})
        else:
            await _run_pr_review_workflow(ex, job)

    row = await db.fetchrow("SELECT status, error_code, error_message, started_at FROM jobs WHERE job_id = $1", job)
    assert started == [], "the workflow started without the job's row"
    assert (row["status"], row["error_code"]) == ("failed", "ERR_STORAGE"), f"the job was never failed: {dict(row)}"
    assert row["started_at"] is None and "too deep" in str(row["error_message"]), dict(row)
    if path == "run":
        assert await db.fetchval("SELECT status FROM issues WHERE id = $1", issue_id) == "open", (
            "the issue is left in progress"
        )


@pytest.mark.parametrize("path", ["run", "review"])
async def test_a_lost_connection_before_start_fails_the_job_from_pending(client: AsyncClient, path: str) -> None:
    """Any storage failure before the job starts fails it (ERR_STORAGE), a lost connection as much as a value too
    deep to read. There is no retry: the job fails with the reason and a person runs it again, rather than its
    failure being refused from pending and the job staying queued until a restart."""
    from unittest.mock import AsyncMock

    import asyncpg

    from embry0.services._executor_run import _run_pr_review_workflow
    from embry0.services.world import failure_words
    from embry0.storage.repositories.jobs import JobsRepository
    from tests.unit.services.test_failed_job_pr import PR, _executor, _patches

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    issue = await app.state.issues_repo.create(title="t", repo="acme/widgets")
    issue_id = issue["id"] if isinstance(issue, dict) else issue
    await db.execute("UPDATE issues SET status = 'in_progress' WHERE id = $1", issue_id)
    jobs = JobsRepository(db)
    job = await jobs.create(repo="acme/widgets", task="t", issue_id=issue_id)
    write = jobs.update

    async def _update(job_id: str, **fields: Any) -> None:
        if fields.get("status") == "running":
            raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        await write(job_id, **fields)

    jobs.update = _update  # type: ignore[method-assign]
    ex = _executor()
    ex._jobs, ex._issues = jobs, app.state.issues_repo
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    started: list[str] = []

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        started.append(job_id)
        raise RuntimeError("the workflow ran")

    ex._execute_workflow_stream = _stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        if path == "run":
            await ex._run_workflow(issue_id, job, {"title": "t", "body": "", "repo": "acme/widgets"})
        else:
            await _run_pr_review_workflow(ex, job)

    row = await db.fetchrow("SELECT status, error_code, error_message, started_at FROM jobs WHERE job_id = $1", job)
    assert started == [], "the workflow started without the job marked running"
    assert (row["status"], row["error_code"]) == ("failed", "ERR_STORAGE"), f"the job was never failed: {dict(row)}"
    assert row["started_at"] is None and "connection was closed" in str(row["error_message"]), dict(row)
    assert "storage" in str(failure_words(row["error_code"], row["error_message"])), "a person is not told why"
    if path == "run":
        assert await db.fetchval("SELECT status FROM issues WHERE id = $1", issue_id) == "open", (
            "the issue is left in progress"
        )


# ---- a pinned credential too deep to read is not the deployment's (round 9) ----


async def _pinned_credential(db: Any, *, kind: str, provider: str, host: str, role: str, pinned: bool = True) -> Any:
    from embry0.storage.encryption import FernetSecretsProvider

    secrets = FernetSecretsProvider(secret_key="test-secret-key")
    cipher = secrets.encrypt("registry-token-000000000001")
    if hasattr(cipher, "__await__"):
        cipher = await cipher
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
        "VALUES ('cred-0001', $2, $3, $4, 'bound-to-widgets', $1, '****', 'someone')",
        cipher,
        kind,
        provider,
        host,
    )
    await db.execute(
        "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by, pinned) "
        "VALUES ('repo', 'acme/widgets', $1, $2, 'cred-0001', 'someone', $3)",
        role,
        provider,
        pinned,
    )
    await db.execute("UPDATE credentials SET metadata = $1::text::jsonb WHERE id = 'cred-0001'", _doc({}))
    return secrets


async def test_a_forge_credential_too_deep_to_read_fails_rather_than_taking_the_environments(
    client: AsyncClient,
) -> None:
    """No forge binding is pinned (the table's credential_bindings_pinned_check): a bound one too deep to read is
    unknown, not absent, all the same."""
    from types import SimpleNamespace

    from embry0.services.credentials.resolver import CredentialResolver
    from embry0.storage.repositories.credentials import CredentialsRepository

    db = client.app.state.db  # type: ignore[attr-defined]
    secrets = await _pinned_credential(
        db, kind="forge_token", provider="github", host="github.com", role="forge", pinned=False
    )
    resolver = CredentialResolver(
        CredentialsRepository(db),
        secrets,
        SimpleNamespace(github_token="", gitlab_token=""),
        environ={"GITHUB_TOKEN": "env-token-000000000002"},
        cache_ttl_seconds=0,
    )
    with pytest.raises(StoredValueTooDeep, match="in credential_bindings, credentials"):
        await resolver.resolve_forge("acme/widgets", "github")


async def test_a_pinned_llm_credential_too_deep_to_read_fails_rather_than_taking_the_environments(
    client: AsyncClient,
) -> None:
    from types import SimpleNamespace

    from embry0.services.credentials.resolver import CredentialResolver
    from embry0.storage.repositories.credentials import CredentialsRepository

    db = client.app.state.db  # type: ignore[attr-defined]
    secrets = await _pinned_credential(db, kind="llm_api_key", provider="anthropic", host="", role="llm")
    resolver = CredentialResolver(
        CredentialsRepository(db),
        secrets,
        SimpleNamespace(github_token="", gitlab_token="", anthropic_api_key="env-key-000000000003"),
        environ={"ANTHROPIC_API_KEY": "env-key-000000000003"},
        cache_ttl_seconds=0,
    )
    with pytest.raises(StoredValueTooDeep, match="in credential_bindings, credentials"):
        await resolver.resolve_llm("acme/widgets", "anthropic")


async def test_the_account_a_job_used_too_deep_to_read_is_not_replaced_by_another(client: AsyncClient) -> None:
    """A job that already used an account keeps it: one it can no longer read is unknown, not gone, so no other
    account of the registry takes its place."""
    from types import SimpleNamespace

    from embry0.services.credentials.resolver import CredentialResolver
    from embry0.storage.repositories.credentials import CredentialsRepository

    db = client.app.state.db  # type: ignore[attr-defined]
    secrets = await _pinned_credential(db, kind="llm_api_key", provider="anthropic", host="", role="llm")
    cipher = secrets.encrypt("registry-token-000000000004")
    if hasattr(cipher, "__await__"):
        cipher = await cipher
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
        "VALUES ('cred-0002', 'llm_api_key', 'anthropic', '', 'another-account', $1, '****', 'someone')",
        cipher,
    )
    await db.execute("DELETE FROM credential_bindings WHERE credential_id = 'cred-0001'")
    await db.execute(
        "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by) "
        "VALUES ('global', '', 'llm', 'anthropic', 'cred-0002', 'someone')"
    )
    await db.execute(
        "INSERT INTO credential_uses (credential_id, credential_label, job_id, role, provider) "
        "VALUES ('cred-0001', 'bound-to-widgets', 'job-1', 'llm', 'anthropic')"
    )
    resolver = CredentialResolver(
        CredentialsRepository(db),
        secrets,
        SimpleNamespace(github_token="", gitlab_token="", anthropic_api_key=""),
        environ={},
        cache_ttl_seconds=0,
    )
    with pytest.raises(StoredValueTooDeep, match="in credentials"):
        await resolver.resolve_llm("acme/widgets", "anthropic", job_id="job-1")

    await db.execute("UPDATE credentials SET metadata = '{}'::jsonb WHERE id = 'cred-0001'")
    used = await resolver.resolve_llm("acme/widgets", "anthropic", job_id="job-1")
    assert used.label == "bound-to-widgets", f"the readable account the job used is not kept: {used}"


# ---- boot recovery takes back the readable jobs (round 9) ----


async def test_one_in_flight_job_too_deep_to_read_leaves_the_others_to_recover(client: AsyncClient) -> None:
    from embry0.orchestration.restart import take_back_jobs
    from embry0.storage.repositories.jobs import JobsRepository

    db = client.app.state.db  # type: ignore[attr-defined]
    for job_id in ("job-deep", "job-ok"):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, status, launch) VALUES ($1, 't', 'acme/widgets', 'pending', "
            "$2::text::jsonb)",
            job_id,
            json.dumps({"kind": "issue", "issue_id": "iss-1"}),
        )
    await db.execute("UPDATE jobs SET launch = $1::text::jsonb WHERE job_id = 'job-deep'", _doc({"kind": "issue"}))

    recovered, failed = await take_back_jobs(JobsRepository(db), db, max_attempts=3)
    assert [row["job_id"] for row in recovered] == ["job-ok"], f"the readable job is not taken back: {recovered}"
    assert failed == ["job-deep"], failed
    row = await db.fetchrow("SELECT status, error_code FROM jobs WHERE job_id = 'job-deep'")
    assert (row["status"], row["error_code"]) == ("failed", "ERR_STORAGE"), dict(row)


@pytest.mark.parametrize("path", ["run", "review"])
async def test_a_fresh_job_whose_row_read_fails_fails_from_pending(client: AsyncClient, path: str) -> None:
    """#551: any failed read of the job's own row fails it from pending as a storage failure, not only one too
    deep: the error is recorded, the end is announced, and the issue goes back to open."""
    from unittest.mock import AsyncMock

    import asyncpg

    from embry0.services._executor_run import _run_pr_review_workflow
    from embry0.storage.repositories.jobs import JobsRepository
    from tests.unit.services.test_failed_job_pr import PR, _executor, _patches

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    issue = await app.state.issues_repo.create(title="t", repo="acme/widgets")
    issue_id = issue["id"] if isinstance(issue, dict) else issue
    await db.execute("UPDATE issues SET status = 'in_progress' WHERE id = $1", issue_id)
    jobs = JobsRepository(db)
    job = await jobs.create(repo="acme/widgets", task="t", issue_id=issue_id if path == "run" else None)
    jobs.get = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
    )

    ex = _executor()
    ex._jobs, ex._issues = jobs, app.state.issues_repo
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._announce_finished = AsyncMock()  # type: ignore[method-assign]
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        raise RuntimeError("the workflow ran")

    ex._execute_workflow_stream = _stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        if path == "run":
            await ex._run_workflow(issue_id, job, {"title": "t", "body": "", "repo": "acme/widgets"})
        else:
            await _run_pr_review_workflow(ex, job)
    row = await db.fetchrow("SELECT status, error_code, error_message, started_at FROM jobs WHERE job_id = $1", job)
    assert (row["status"], row["error_code"]) == ("failed", "ERR_STORAGE"), f"the job was never failed: {dict(row)}"
    assert row["started_at"] is None and "connection was closed" in str(row["error_message"]), dict(row)
    ex._announce_finished.assert_awaited_once_with(job)
    if path == "run":
        assert await db.fetchval("SELECT status FROM issues WHERE id = $1", issue_id) == "open", (
            "the issue is left in progress, so a run again is refused"
        )
        ex._post_tracker_outcome.assert_awaited()
