"""The map of background models (#164), end to end on a real database.

- A bad model is a 422 naming its key, never repeating it, and nothing is stored.
- A change reaches each feature's next read, and changing one feature's model changes no other's.
- The first boot imports the variables and the provider settings' watcher model once, keeping every
  feature on the model it ran on; a second boot imports nothing.
- A hosted model's spend is recorded, counted against the daily cap and shown by /stats; past the cap
  the next call is refused before anything is sent, and the loop goes on.
- The real lifespan builds every feature on readers of the section and on the one metered call path.

The section is taken from the application's catalog by name, and each test first writes or reads it, so
each fails with its own message on a tree that has no such section.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.proposal_briefs import brief_settings
from embry0.services.proposals import Proposal
from embry0.settings.bootstrap import import_env_once
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
ADMIN = "emb_" + "a" * 43
DAY = date(2026, 10, 30)
T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)
FEATURES = ("watcher", "context_audit", "scout", "proposal_brief", "job_brief", "slop_judge")
BRIEF = {
    "title": "Stop retrying a saved email",
    "problem": "It retries forever.",
    "fix": "Stop. You get a PR.",
    "size": "small",
}


def _section() -> Section[Any]:
    section = CATALOG.get("background_models")
    assert section is not None, "background_models is a section of the settings document (#164)"
    return section


def _module() -> Any:
    _section()
    from embry0.services import background_models

    return background_models


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,custom")


@pytest.fixture
async def app(db_with_migrations: DatabasePool, providers: None) -> AsyncIterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = db_with_migrations
    app.state.settings = SettingsStore(db_with_migrations)
    keys = MagicMock()
    row = ApiKeyRow(
        id="key-admin0000001",
        user_id="usr-admin",
        name="admin-key",
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(ADMIN) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    app.state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    headers = {"Authorization": f"Bearer {ADMIN}", "X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


async def _patch(client: AsyncClient, values: dict[str, Any]) -> Any:
    return await client.patch(f"{URL}/background_models", json=values)


async def test_a_bad_model_is_a_422_naming_its_key_and_nothing_is_stored(app: Any, client: AsyncClient) -> None:
    _section()
    resp = await _patch(
        client,
        {"default": "custom/no-such", "scout": "two words", "proposal_brief": "custom/qwen", "watcher": "gpt-6-luna"},
    )
    assert resp.status_code == 422, f"a model the catalogue does not offer is refused when written: {resp.text}"
    assert sorted(tuple(p["loc"][:2]) for p in resp.json()["detail"]) == [
        ("background_models", "scout"),
        ("background_models", "watcher"),
    ], f"each bad value is refused by its own key, and only those: {resp.text}"
    assert "gpt-6-luna" not in resp.text and "two words" not in resp.text, "a 422 never repeats the value"
    assert any("custom/<model>" in p["msg"] for p in resp.json()["detail"]), (
        "a hosted model the watcher would prefer is refused: a preference for it would be a pin"
    )
    unknown = await _patch(client, {"default": "nope"})
    assert unknown.status_code == 422 and [tuple(p["loc"][:2]) for p in unknown.json()["detail"]] == [
        ("background_models", "default")
    ], unknown.text

    put = await client.put(
        URL, content="background_models:\n  job_brief: claude-sonnet-5\n", headers={"Content-Type": "application/yaml"}
    )
    assert put.status_code == 422, put.text
    assert [tuple(p["loc"][:2]) for p in put.json()["detail"]] == [("background_models", "job_brief")], (
        "a whole document meets the same check: a job brief is asked over chat completions, which Anthropic's "
        f"own API is not: {put.text}"
    )
    stored = (await client.get(URL)).json()
    assert (stored["yaml_text"], stored["data"]) == ("", {}), "a refused write stores nothing"


async def test_a_change_reaches_each_feature_s_next_read_and_changes_no_other_s(app: Any, client: AsyncClient) -> None:
    module = _module()
    readers = {feature: module.background_model(app.state.settings, feature) for feature in FEATURES}

    async def models(repo: str | None = "acme/widgets") -> dict[str, str]:
        return {feature: (await read())(repo) for feature, read in readers.items()}

    assert await models() == dict.fromkeys(FEATURES, ""), "nothing written: no feature runs on a model"
    first = await _patch(client, {"default": "custom/qwen"})
    assert first.status_code == 200, first.text
    assert await models() == {**dict.fromkeys(FEATURES, "custom/qwen"), "scout": ""}, (
        "every feature's next read has the default, but the scout, which runs only on a model named for it"
    )

    second = await _patch(client, {"proposal_brief": "gpt-6-luna", "scout": False})
    assert second.status_code == 200, second.text
    assert await models() == {
        "watcher": "custom/qwen",
        "context_audit": "custom/qwen",
        "scout": "",
        "proposal_brief": "gpt-6-luna",
        "job_brief": "custom/qwen",
        "slop_judge": "custom/qwen",
    }, "the job briefs and the judge keep the default: no feature falls back on another's model"

    await app.state.settings.set_keys(
        "background_models", {"default": "custom/acme"}, actor="alex@example.com", scope=_org("acme")
    )
    assert (await models("acme/widgets"))["watcher"] == "custom/acme", (
        "an organization's own default for its repositories"
    )
    assert (await models("other/app"))["watcher"] == "custom/qwen", "the deployment's for the rest"


def _org(owner: str) -> Any:
    from embry0.settings.scope import Scope

    return Scope.organization(owner)


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def test_the_first_boot_imports_what_each_feature_ran_on_once(
    db_with_migrations: DatabasePool, providers: None
) -> None:
    module = _module()
    db = db_with_migrations
    await db.execute(
        "INSERT INTO provider_config (id, watcher_local_model) VALUES ('default', 'custom/watcher') "
        "ON CONFLICT (id) DO UPDATE SET watcher_local_model = EXCLUDED.watcher_local_model"
    )
    config = _config(PROPOSAL_BRIEF_MODEL="custom/briefs", CONTEXT_AUDIT_LOCAL_MODEL="custom/audit")
    store = SettingsStore(db)

    await import_env_once(store, config, today=DAY)
    imported = await module.import_provider_settings_once(store, config, today=DAY)
    assert imported == {
        "default": "custom/watcher",
        "scout": False,
        "job_brief": "custom/briefs",
        "slop_judge": "custom/briefs",
    }, (
        "the watcher's model becomes the default; the job briefs and the judge keep the proposal briefs' model they "
        f"fell back on; the scout, with no variable of its own, stays on none: {imported}"
    )
    stored = (await store.document())["data"]["background_models"]
    assert stored == {
        "context_audit": "custom/audit",
        "proposal_brief": "custom/briefs",
        "default": "custom/watcher",
        "scout": False,
        "job_brief": "custom/briefs",
        "slop_judge": "custom/briefs",
    }, f"the variables first, then the provider settings: {stored}"
    lookups = {feature: (await module.background_model(store, feature)())(None) for feature in FEATURES}
    assert lookups == {
        "watcher": "custom/watcher",
        "context_audit": "custom/audit",
        "scout": "",
        "proposal_brief": "custom/briefs",
        "job_brief": "custom/briefs",
        "slop_judge": "custom/briefs",
    }, "every feature runs on the model it ran on before the upgrade"
    newest = (await store.revisions())[0]
    assert (newest["source"], newest["actor"]) == ("upgrade", "embry0:bootstrap"), newest

    await store.set_keys("background_models", {"default": "custom/chosen"}, actor="alex@example.com")
    await db.execute("UPDATE provider_config SET watcher_local_model = 'custom/later' WHERE id = 'default'")
    again = SettingsStore(db)
    assert (await import_env_once(again, config, today=DAY)).imported == {}
    assert await module.import_provider_settings_once(again, config, today=DAY) == {}, "the ledger keeps it to once"
    assert (await again.document())["data"]["background_models"]["default"] == "custom/chosen", "a person's value stays"


async def test_a_hosted_model_s_spend_is_counted_against_the_caps_and_refused_past_them(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    module = _module()
    from embry0.orchestration.budget_gate import build_budget_gate
    from embry0.services.budget_policy import BudgetPolicy
    from embry0.services.proposal_briefs import ProposalBriefWriter
    from embry0.storage.repositories.background_spend import BackgroundSpendRepository
    from embry0.storage.repositories.budget_spend import BudgetSpendRepository

    db = db_with_migrations
    assert (await _patch(client, {"proposal_brief": "gpt-6-luna"})).status_code == 200
    # The caps as an operator writes them: the settings document's budget section.
    caps = await client.patch(f"{URL}/budget", json={"daily_cap_usd": 1.0, "monthly_cap_usd": 100.0})
    assert caps.status_code == 200, caps.text
    policy = BudgetPolicy(app.state.settings, app.state.config, retry_seconds=None)
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        # gpt-6-luna is $0.10 in and $0.50 out per million tokens: this answer costs $0.60.
        usage = {"prompt_tokens": 2_000_000, "completion_tokens": 800_000}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(BRIEF)}}], "usage": usage})

    class Resolver:
        async def resolve_llm(self, repo: str, provider: str) -> Any:
            return SimpleNamespace(secret="k", metadata={"base_url": "http://models.test/v1"})

    class Briefs:
        rows: dict[str, Any] = {}

        async def all(self) -> dict[str, Any]:
            return dict(self.rows)

        async def save(self, pid: str, digest: str, brief: Any, *, model: str) -> None:
            self.rows[pid] = {"input_hash": digest}

        async def prune(self, keep: set[str]) -> int:
            return 0

    proposals = [
        Proposal(
            id=f"case:hc-{n}",
            kind="case",
            repo="acme/widgets",
            title=f"Problem {n}",
            summary="…",
            details="",
            action="Run the fix pipeline",
            created_at=T0,
            extra={},
        )
        for n in range(3)
    ]

    async def feed() -> list[Proposal]:
        return proposals

    writer = ProposalBriefWriter(
        proposals=feed,
        settings=brief_settings(app.state.settings),
        briefs=Briefs(),
        calls=module.BackgroundCalls(
            resolver=Resolver(),
            spend=BackgroundSpendRepository(db),
            gate=build_budget_gate(policy),
        ),
        per_tick=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    assert len(await writer.tick()) == 1 and [b["model"] for b in sent] == ["gpt-6-luna"]
    rollup = await BudgetSpendRepository(db).spend_rollup()
    assert rollup["daily"] == pytest.approx(0.6), f"the budget gate counts a background call's spend: {rollup}"
    stats = (await client.get("/api/v1/stats")).json()
    assert stats["daily_cost_usd"] == pytest.approx(0.6), "/stats counts it in the day's spend"
    [feature] = [f for f in stats["background_spend"]["features"] if f["feature"] == "proposal_brief"]
    assert (feature["source"], feature["calls"], feature["monthly_usd"]) == ("calls", 1, pytest.approx(0.6)), (
        "attributed to the feature that spent it"
    )

    assert len(await writer.tick()) == 1, "$0.60 of a $1.00 cap: the next call goes"
    with capture_logs() as logs:
        assert await writer.tick() == [], "$1.20 spent: the cap refuses the call, and the tick returns"
    assert len(sent) == 2, "nothing was sent past the cap"
    refused = [e for e in logs if e["event"] == "background_model_refused"]
    assert [(e["feature"], e["limit"]) for e in refused] == [("proposal_brief", "daily")], logs


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_builds_every_feature_on_readers_of_the_section(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, providers: None
) -> None:
    module = _module()
    import embry0.api.app as app_module
    from embry0.orchestration.budget_gate import BudgetGate
    from embry0.storage.repositories.background_spend import BackgroundSpendRepository

    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    await db_with_migrations.execute(
        "INSERT INTO provider_config (id, watcher_local_model) VALUES ('default', 'custom/watcher') "
        "ON CONFLICT (id) DO UPDATE SET watcher_local_model = EXCLUDED.watcher_local_model"
    )
    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
        scout_model="custom/scout",
    )
    app = create_app(config)
    async with lifespan(app):
        state = app.state

        async def scout() -> Any:
            # The scout reads its model with its section, from one snapshot a tick.
            return (await state.scout.settings()).models

        async def proposal_brief() -> Any:
            # The proposal briefs read their model with the leads section, from one snapshot a tick.
            return (await state.proposal_brief_writer._settings()).models  # noqa: SLF001

        async def slop_judge() -> Any:
            # The meter reads its judge's model with its enrollment, from one snapshot a tick (#165).
            return (await state.slop_meter._read()).models  # noqa: SLF001

        readers = {
            "proposal_brief": proposal_brief,
            "job_brief": state.job_brief_writer._models,  # noqa: SLF001
            "slop_judge": slop_judge,
            "scout": scout,
        }

        async def read() -> dict[str, str]:
            out = {feature: (await reader())("acme/widgets") for feature, reader in readers.items()}
            out["watcher"] = await app_module._watcher_local_first_model(app, "acme/widgets")()  # noqa: SLF001
            return out

        assert await read() == {
            "proposal_brief": "custom/watcher",
            "job_brief": "custom/watcher",
            "slop_judge": "custom/watcher",
            "scout": "custom/scout",
            "watcher": "custom/watcher",
        }, "the boot imported SCOUT_MODEL and the provider settings' watcher model into what each feature reads"
        await state.settings.set_keys(
            "background_models", {"default": "custom/next", "job_brief": False}, actor="alex@example.com"
        )
        assert await read() == {
            "proposal_brief": "custom/next",
            "job_brief": "",
            "slop_judge": "custom/next",
            "scout": "custom/scout",
            "watcher": "custom/next",
        }, "a write reaches every running feature's next read, with no restart"
        calls = state.background_calls
        assert isinstance(calls, module.BackgroundCalls)
        assert isinstance(calls._spend, BackgroundSpendRepository) and isinstance(calls._gate, BudgetGate)  # noqa: SLF001
        assert calls._gate._settings == state.budget_policy.settings, (  # noqa: SLF001
            "the background calls' gate reads the caps through the application's budget policy"
        )
        for loop in (state.proposal_brief_writer, state.job_brief_writer, state.slop_meter):
            assert loop._calls is calls, f"{type(loop).__name__} calls through the metered path"  # noqa: SLF001


# What each feature ran on before the section, as origin/main's app.py read it: the watcher on W, the
# context audit on C or none, the scout on S or off, the proposal briefs on P or W, the job briefs on J or the
# proposal briefs' model, the judge on the job briefs'. The reviewer's upgrade probe, as a test (#287).
def _main_chain(w: str, env: dict[str, str]) -> dict[str, str]:
    proposal = env.get("PROPOSAL_BRIEF_MODEL", "").strip() or w
    job = env.get("JOB_BRIEF_MODEL", "").strip() or proposal
    return {
        "watcher": w,
        "context_audit": env.get("CONTEXT_AUDIT_LOCAL_MODEL", "").strip(),
        "scout": env.get("SCOUT_MODEL", "").strip(),
        "proposal_brief": proposal,
        "job_brief": job,
        "slop_judge": job,
    }


@pytest.mark.parametrize(
    ("w", "env"),
    [
        ("", {}),
        ("custom/w", {}),
        ("", {"CONTEXT_AUDIT_LOCAL_MODEL": "custom/c"}),
        ("", {"SCOUT_MODEL": "custom/s"}),
        ("", {"PROPOSAL_BRIEF_MODEL": "custom/p"}),
        ("custom/w", {"JOB_BRIEF_MODEL": "custom/j"}),
        ("custom/w", {"CONTEXT_AUDIT_LOCAL_MODEL": "custom/c", "SCOUT_MODEL": "custom/s"}),
        ("", {"PROPOSAL_BRIEF_MODEL": "custom/p", "JOB_BRIEF_MODEL": "custom/j"}),
    ],
    ids=["nothing-set", "only-w", "c-alone", "s-alone", "p-alone", "w-and-j", "w-c-and-s", "p-and-j"],
)
async def test_an_upgrade_keeps_every_model_and_a_default_written_later_starts_no_scout(
    db_with_migrations: DatabasePool, providers: None, w: str, env: dict[str, str]
) -> None:
    module = _module()
    db = db_with_migrations
    if w:
        await db.execute(
            "INSERT INTO provider_config (id, watcher_local_model) VALUES ('default', $1) "
            "ON CONFLICT (id) DO UPDATE SET watcher_local_model = EXCLUDED.watcher_local_model",
            w,
        )
    config = _config(**env)
    store = SettingsStore(db)
    await import_env_once(store, config, today=DAY)
    await module.import_provider_settings_once(store, config, today=DAY)

    async def read() -> dict[str, str]:
        return {feature: (await module.background_model(store, feature)())(None) for feature in FEATURES}

    before = _main_chain(w, env)
    assert await read() == before, "the upgrade changes no feature's model"
    await store.set_keys("background_models", {"default": "custom/later"}, actor="alex@example.com")
    after = await read()
    assert (after["scout"], after["context_audit"]) == (before["scout"], before["context_audit"]), (
        "a default written later, as the Providers page writes it, never starts the scout or moves the context "
        f"audits: {after}"
    )


async def test_the_spend_reads_of_stats_are_bounded_by_the_month_through_an_index(
    db_with_migrations: DatabasePool,
) -> None:
    """/stats reports today and this month: its reads of background spend are ranges of an index, however long
    the history grows, and last month's rows are not in them (#287)."""
    _section()
    import embry0.api.v1.stats as stats_module
    from embry0.storage.repositories import background_spend

    month_sql = getattr(background_spend, "_MONTH_SQL", "")
    jobs_sql = getattr(stats_module, "_BACKGROUND_JOBS_SQL", "")
    assert "date_trunc('month', NOW())" in month_sql, "the background spend read is bounded by the month"
    assert "created_at >= date_trunc('month', NOW())" in jobs_sql, "the jobs read is bounded by the month"
    db = db_with_migrations
    async with db.transaction() as conn:
        await conn.execute("SET LOCAL enable_seqscan = off")
        spend_plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + month_sql))
        jobs_plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + jobs_sql, ["embry0:scout"]))
    assert "idx_background_spend_at" in spend_plan, spend_plan
    assert "idx_jobs_dispatched_by_created_at" in jobs_plan, jobs_plan

    await db.execute(
        "INSERT INTO background_spend (feature, model, cost_usd, at) VALUES "
        "('job_brief', 'm', 0.5, NOW()), ('job_brief', 'm', 9.0, date_trunc('month', NOW()) - INTERVAL '1 day')"
    )
    rows = await background_spend.BackgroundSpendRepository(db).this_month()
    assert [(r["feature"], r["calls"], r["monthly_usd"]) for r in rows] == [("job_brief", 1, pytest.approx(0.5))]


async def test_the_lifetime_total_counts_every_background_call_through_an_index_only_read(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    """A figure called the total is the total: what background calls spent outside any job is in
    ``total_cost_usd``, read with the stats query from the index alone, never from the table's rows (#287)."""
    _section()
    import embry0.api.v1.stats as stats_module

    total_sql = getattr(stats_module, "_BACKGROUND_TOTAL_SQL", "")
    assert total_sql, "the lifetime total reads what background calls spent"
    db = db_with_migrations
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, total_cost_usd, started_at) VALUES "
        "('job-old', 'acme/widgets', 't', 'completed', 5.0, date_trunc('month', NOW()) - INTERVAL '2 days')"
    )
    await db.execute(
        "INSERT INTO background_spend (feature, model, cost_usd, at) VALUES "
        "('proposal_brief', 'm', 0.5, NOW()), ('job_brief', 'm', 9.0, date_trunc('month', NOW()) - INTERVAL '1 day')"
    )
    stats = (await client.get("/api/v1/stats")).json()
    assert stats["total_cost_usd"] == pytest.approx(14.5), (
        f"the lifetime total is the jobs' and every background call's, last month's included: {stats['total_cost_usd']}"
    )
    assert stats["background_spend"]["total_usd"] == pytest.approx(9.5)
    assert stats["monthly_cost_usd"] == pytest.approx(0.5), "the month counts only this month's call"

    # As autovacuum leaves a table: its visibility map set, so the index alone answers.
    await db.execute("VACUUM ANALYZE background_spend")
    async with db.transaction() as conn:
        await conn.execute("SET LOCAL enable_seqscan = off")
        await conn.execute("SET LOCAL enable_bitmapscan = off")
        plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN SELECT " + total_sql))
    assert "Index Only Scan using idx_background_spend_at" in plan, (
        f"the lifetime sum reads the index alone, which carries cost_usd: {plan}"
    )


_FAILED_FIRST_BOOTS = pytest.mark.parametrize(
    ("w", "env"),
    [
        ("", {"SCOUT_MODEL": "custom/s"}),
        ("", {"CONTEXT_AUDIT_LOCAL_MODEL": "custom/c"}),
        ("custom/w", {"CONTEXT_AUDIT_LOCAL_MODEL": "custom/c"}),
        ("custom/w", {"PROPOSAL_BRIEF_MODEL": "custom/p"}),
        ("custom/w", {"JOB_BRIEF_MODEL": "gpt-6-luna"}),
        (
            "custom/w",
            {
                "SCOUT_MODEL": "custom/s",
                "CONTEXT_AUDIT_LOCAL_MODEL": "custom/c",
                "PROPOSAL_BRIEF_MODEL": "custom/p",
                "JOB_BRIEF_MODEL": "custom/j",
            },
        ),
    ],
    ids=["s-alone", "c-alone", "c-and-w", "p-and-w", "hosted-j-and-w", "everything"],
)


@_FAILED_FIRST_BOOTS
async def test_a_first_boot_whose_environment_import_failed_loses_no_variable(
    db_with_migrations: DatabasePool, providers: None, w: str, env: dict[str, str]
) -> None:
    """The reviewer's boot sequence: the first boot's environment import fails (the lifespan logs it and goes
    on), so only the provider settings' import runs; the next boot runs both. Every feature must then run on
    the model it ran on before (#287)."""
    module = _module()
    db = db_with_migrations
    if w:
        await db.execute(
            "INSERT INTO provider_config (id, watcher_local_model) VALUES ('default', $1) "
            "ON CONFLICT (id) DO UPDATE SET watcher_local_model = EXCLUDED.watcher_local_model",
            w,
        )
    config = _config(**env)
    await module.import_provider_settings_once(SettingsStore(db), config, today=DAY)
    store = SettingsStore(db)
    await import_env_once(store, config, today=DAY)
    await module.import_provider_settings_once(store, config, today=DAY)
    after = {feature: (await module.background_model(store, feature)())(None) for feature in FEATURES}
    assert after == _main_chain(w, env), f"the variables survive a failed first import: {after}"


@_FAILED_FIRST_BOOTS
async def test_a_first_boot_whose_environment_import_failed_runs_each_feature_on_its_variable_meanwhile(
    db_with_migrations: DatabasePool, providers: None, w: str, env: dict[str, str]
) -> None:
    """Between a failed environment import and the next one (the next boot, or the budget policy's retry a
    few seconds on), only the provider settings' import has run. Every feature must already run on the model it
    ran on before: a scheduled context audit never prefers watcher_local_model over CONTEXT_AUDIT_LOCAL_MODEL,
    nor a brief the default over its own variable's model, even for that while (#287)."""
    module = _module()
    db = db_with_migrations
    if w:
        await db.execute(
            "INSERT INTO provider_config (id, watcher_local_model) VALUES ('default', $1) "
            "ON CONFLICT (id) DO UPDATE SET watcher_local_model = EXCLUDED.watcher_local_model",
            w,
        )
    store = SettingsStore(db)
    await module.import_provider_settings_once(store, _config(**env), today=DAY)
    meanwhile = {feature: (await module.background_model(store, feature)())(None) for feature in FEATURES}
    assert meanwhile == _main_chain(w, env), (
        f"until the environment import runs again, a feature runs on the default in its variable's place: {meanwhile}"
    )


async def test_a_hosted_context_audit_model_is_refused_on_upgrade_and_said_so(
    db_with_migrations: DatabasePool, providers: None
) -> None:
    """The context audit prefers only a model of the operator's own server: a hosted CONTEXT_AUDIT_LOCAL_MODEL is
    refused, the audits run on their agents' own models, and a warning names the variable, never its value."""
    module = _module()
    config = _config(CONTEXT_AUDIT_LOCAL_MODEL="gpt-6-luna")
    store = SettingsStore(db_with_migrations)
    with capture_logs() as logs:
        await import_env_once(store, config, today=DAY)
        await module.import_provider_settings_once(store, config, today=DAY)
    assert (await module.background_model(store, "context_audit")())(None) == "", "no preference: their own models"
    named = [e for e in logs if e["event"] == "background_models_variable_refused"]
    assert [(e["log_level"], e["variable"]) for e in named] == [("warning", "CONTEXT_AUDIT_LOCAL_MODEL")], logs
    assert "gpt-6-luna" not in str(logs), "the value is never logged"
