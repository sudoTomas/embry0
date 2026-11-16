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
