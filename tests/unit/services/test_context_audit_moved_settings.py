"""Whether and how a repository is audited is the settings document's ``context_audit`` section now (#170).

``days`` and ``open_prs`` were an organization's ``context_audit_days`` and
``context_audit_open_prs``; each organization's values are copied into its
layer once, and until its copy succeeds it reads its row. Either way the
schedule does what it did, which the differential at the end pins against
what origin/main did with the same rows. A repository's own layer may now
take it off its organization's schedule or audit it more often.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services import context_audit as ca
from embry0.services.budget_policy import BudgetView
from embry0.services.organizations import Organization
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
HEAD, OLD = "1" * 40, "0" * 40
BLOB = "b" * 40


def _moved() -> Any:
    """The organization settings that moved into sections, as production's budget policy is given them."""
    from embry0.services.organizations import MOVED_SETTINGS

    paths = {m.path for m in MOVED_SETTINGS}
    if not {"context_audit.days", "context_audit.open_prs"} <= paths:
        pytest.fail("context_audit_days and context_audit_open_prs did not move into the context_audit section")
    return MOVED_SETTINGS


class Resp:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code, self._body = status_code, body

    def json(self) -> Any:
        return self._body


class Forge:
    """Every repository's default branch at HEAD, holding one CLAUDE.md."""

    async def get(self, path: str, **kwargs: Any) -> Resp:
        if path.endswith("/commits"):
            return Resp(200, [{"sha": HEAD, "commit": {"tree": {"sha": "tree"}}}])
        return Resp(200, {"tree": [{"path": "CLAUDE.md", "type": "blob", "sha": BLOB}]})


class Jobs:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def finished(self, repo: str, days_ago: float) -> None:
        """An audit that finished ``days_ago``, read at an older head with today's files."""
        when = T0 - timedelta(days=days_ago)
        config = {"context_fingerprint": ca.fingerprint({"CLAUDE.md": BLOB}), "head_sha": OLD}
        self._add(repo, "completed", when - timedelta(hours=1), when, ca.ACTOR, config)

    def _add(self, repo: str, status: str, created: datetime, finished: Any, by: str, config: Any) -> str:
        self.rows.append(
            {
                "job_id": f"job-{len(self.rows)}",
                "repo": repo,
                "status": status,
                "created_at": created,
                "finished_at": finished,
                "pipeline_template": ca.TEMPLATE,
                "dispatched_by": by,
                "pipeline_config": config,
            }
        )
        return str(self.rows[-1]["job_id"])

    async def newest_per_repo(
        self,
        repos: Any,
        *,
        pipeline_template: str,
        dispatched_by: Any = None,
        exclude_dispatched_by: Any = (),
        leader_agent: Any = None,
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for repo in repos:
            mine = [r for r in self.rows if r["repo"] == repo and r["dispatched_by"] not in exclude_dispatched_by]
            if mine:
                newest = max(mine, key=lambda r: r["created_at"])
                out[repo] = {**newest, "live": any(r["status"] == "pending" for r in mine)}
        return out

    async def result_summaries(self, job_ids: Any) -> dict[str, Any]:
        return {}

    def started(self) -> dict[str, bool]:
        """Each repository a job was started for, to whether it opens a pull request."""
        return {r["repo"]: r["pipeline_config"]["open_pr"] for r in self.rows if "open_pr" in r["pipeline_config"]}


class Orgs:
    def __init__(self, orgs: list[Organization]) -> None:
        self.orgs = orgs

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        return self.orgs


class Registry:
    def __init__(self, repos: list[str]) -> None:
        self.repos = repos
        self.reads = 0

    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        self.reads += 1
        return [{"repo": repo, "forge": "github", "active": True} for repo in self.repos]


def _snapshot(documents: Mapping[str, Mapping[str, Any]], imported: Mapping[str, list[str]]) -> Any:
    rows = [{"scope": "deployment", "data": {}, "generation": 1, "updated_at": None}]
    for scope in sorted(set(documents) | set(imported)):
        rows = [r for r in rows if r["scope"] != scope]
        rows.append(
            {
                "scope": scope,
                "data": dict(documents.get(scope, {})),
                "generation": 1,
                "updated_at": None,
                "bootstrapped_keys": list(imported.get(scope, [])),
            }
        )
    return build_snapshot(CATALOG, rows, token=(), log=False)


def _loop(
    orgs: list[Organization],
    repos: list[str],
    jobs: Jobs,
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    imported: Mapping[str, list[str]] | None = None,
) -> tuple[ca.ContextAuditLoop, Registry]:
    moved = _moved()
    snapshot = _snapshot(documents or {}, imported or {})

    async def view(organizations: Any = ()) -> BudgetView:
        return BudgetView(snapshot, moved=moved)

    async def start_job(context: ca.RepoContext, budget_usd: float, open_pr: bool, run_with: Any = None) -> str:
        return jobs._add(  # noqa: SLF001 - the fake's own row
            context.repo, "pending", T0, None, ca.ACTOR, {"open_pr": open_pr, "max_budget_usd": budget_usd}
        )

    async def client_for(org: Organization) -> Forge:
        return Forge()

    async def gate() -> None:
        return None

    registry = Registry(repos)
    loop = ca.ContextAuditLoop(
        organizations=Orgs(orgs),
        registry=registry,
        jobs=jobs,
        client_for=client_for,
        start_job=start_job,
        ask_gate=gate,
        budget=view,
        clock=lambda: T0,
    )
    return loop, registry


def _org(owner: str, **settings: Any) -> Organization:
    return Organization(owner=owner, forge="github", settings=settings)


IMPORTED = ["context_audit.days", "context_audit.open_prs"]


@pytest.mark.asyncio
async def test_a_repository_reads_its_schedule_and_pull_requests_through_its_layers() -> None:
    jobs = Jobs()
    loop, _ = _loop(
        [_org("acme"), _org("globex")],
        ["acme/widgets", "acme/gizmos", "globex/gadgets"],
        jobs,
        documents={
            "organization:acme": {"context_audit": {"days": 7, "open_prs": True}},
            "repository:acme/widgets": {"context_audit": {"days": False}},
        },
        imported={"organization:acme": IMPORTED, "organization:globex": IMPORTED},
    )
    assert await loop.tick() == ["acme/gizmos"], (
        "the organization's layer puts its repositories on the schedule, and a repository's days: false takes "
        "it off; another organization with nothing set stays off"
    )
    assert jobs.started() == {"acme/gizmos": True}, "the organization's open_prs reaches its repository's job"
    survey = await loop.survey(_org("acme"), refresh=False)
    assert survey.days == 7
    assert {s.context.repo: (s.days, s.reason) for s in survey.repos} == {
        "acme/widgets": (None, ca.OFF),
        "acme/gizmos": (7, ca.LIVE),
    }


@pytest.mark.asyncio
async def test_a_repository_s_own_layer_audits_it_more_often_or_without_a_pull_request() -> None:
    jobs = Jobs()
    for repo in ("acme/widgets", "acme/gizmos"):
        jobs.finished(repo, days_ago=2)
    loop, _ = _loop(
        [_org("acme")],
        ["acme/widgets", "acme/gizmos"],
        jobs,
        documents={
            "organization:acme": {"context_audit": {"days": 7, "open_prs": True}},
            "repository:acme/widgets": {"context_audit": {"days": 1, "open_prs": False}},
        },
        imported={"organization:acme": IMPORTED},
    )
    assert await loop.tick() == ["acme/widgets"], "two days after its audit, only the repository on a 1-day schedule"
    assert jobs.started() == {"acme/widgets": False}, "its own open_prs wins over its organization's"


@pytest.mark.asyncio
async def test_the_deployment_s_days_puts_every_organization_s_repositories_on_the_schedule() -> None:
    jobs = Jobs()
    loop, _ = _loop(
        [_org("acme"), _org("globex")],
        ["acme/widgets", "globex/gadgets"],
        jobs,
        documents={
            "deployment": {"context_audit": {"days": 14}},
            "organization:globex": {"context_audit": {"days": False}},
        },
    )
    assert await loop.tick() == ["acme/widgets"], "an organization's days: false turns it off under the deployment's"


@pytest.mark.asyncio
async def test_until_its_layer_imports_them_an_organization_reads_its_row_and_after_that_never() -> None:
    row = {"context_audit_days": 7, "context_audit_open_prs": True}
    pending, imported_off, imported_on = Jobs(), Jobs(), Jobs()

    loop, _ = _loop(
        [_org("acme", **row)],
        ["acme/widgets", "acme/gizmos"],
        pending,
        documents={"repository:acme/gizmos": {"context_audit": {"days": False}}},
    )
    assert await loop.tick() == ["acme/widgets"], (
        "an organization whose copy has not happened reads its row, and a repository's own layer still wins"
    )
    assert pending.started() == {"acme/widgets": True}

    loop, _ = _loop([_org("acme", **row)], ["acme/widgets"], imported_off, imported={"organization:acme": IMPORTED})
    assert await loop.tick() == [], "once its layer imported them, the row is read by nothing"

    loop, _ = _loop(
        [_org("acme", **row)],
        ["acme/widgets"],
        imported_on,
        documents={"organization:acme": {"context_audit": {"days": 3}}},
        imported={"organization:acme": IMPORTED},
    )
    assert await loop.tick() == ["acme/widgets"]
    assert imported_on.started() == {"acme/widgets": False}, "imported: its layer's values, its row's true unread"


@pytest.mark.asyncio
async def test_a_wake_reads_the_registered_repositories_once_for_every_organization() -> None:
    orgs = [_org(owner) for owner in ("acme", "globex", "initech")]
    loop, registry = _loop(
        orgs,
        ["acme/a", "globex/a", "initech/a"],
        Jobs(),
        documents={"deployment": {"context_audit": {"days": 7}}},
    )
    assert sorted(await loop.tick()) == ["acme/a", "globex/a", "initech/a"]
    assert registry.reads == 1, f"one wake read the registered repositories {registry.reads} times"


# ---- the differential: the moved settings behave as the organization row did -----------------

#: Organization rows as they stand before the release: each setting set, null, out of range, or not of its
#: type (stored once by a POST that did not check it).
ROWS: dict[str, dict[str, Any]] = {
    "alpha": {"context_audit_days": 7, "context_audit_open_prs": True},
    "beta": {"context_audit_days": 30},
    "gamma": {"context_audit_days": None},
    "delta": {"context_audit_days": 0, "context_audit_open_prs": "true"},
    "eps": {"context_audit_days": "7", "context_audit_open_prs": True},
    "zeta": {"context_audit_days": 1, "context_audit_open_prs": False},
    "eta": {"context_audit_days": 365, "context_audit_open_prs": True, "auto_register": True},
}
REPOS = ["alpha/a", "alpha/b", "beta/a", "gamma/a", "delta/a", "eps/a", "zeta/a", "zeta/b", "eta/a"]

#: What origin/main (a633bd40), which read these rows, started, with whether each job opens a pull request,
#: what its survey said of each organization and repository, and what a run by hand of gamma started.
MAIN = {
    "tick": ["alpha/a", "alpha/b", "zeta/a", "zeta/b"],
    "open_pr": {"alpha/a": True, "alpha/b": True, "zeta/a": False, "zeta/b": False},
    "surveys": {
        "alpha": (7, {"alpha/a": "live", "alpha/b": "live"}),
        "beta": (30, {"beta/a": "current"}),
        "gamma": (None, {"gamma/a": "off"}),
        "delta": (None, {"delta/a": "off"}),
        "eps": (None, {"eps/a": "off"}),
        "zeta": (1, {"zeta/a": "live", "zeta/b": "live"}),
        "eta": (365, {"eta/a": "current"}),
    },
    "run_gamma": (None, ["gamma/a"], False),
}


def _imported_layers() -> dict[str, dict[str, Any]]:
    """Each organization's layer exactly as the copy writes it: a value the old reader would have used."""
    out: dict[str, dict[str, Any]] = {}
    for owner, row in ROWS.items():
        values = {m.key: m.read(row) for m in _moved() if m.section == "context_audit" and m.read(row) is not None}
        if values:
            out[f"organization:{owner}"] = {"context_audit": values}
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("copied", [False, True], ids=["import-pending", "imported"])
async def test_the_moved_settings_audit_as_the_organization_row_did(copied: bool) -> None:
    jobs = Jobs()
    jobs.finished("alpha/b", 8)  # aged at 7 days
    jobs.finished("beta/a", 8)  # current at 30
    jobs.finished("zeta/a", 2)  # aged at 1
    jobs.finished("eta/a", 2)  # current at 365
    if copied:
        orgs = [
            _org(owner, **{k: v for k, v in row.items() if not k.startswith("context_audit_")})
            for owner, row in ROWS.items()
        ]
        documents = _imported_layers()
        imported = {f"organization:{owner}": [m.path for m in _moved()] for owner in ROWS}
    else:
        orgs = [_org(owner, **row) for owner, row in ROWS.items()]
        documents, imported = {}, {}
    loop, _ = _loop(orgs, REPOS, jobs, documents=documents, imported=imported)

    tick = await loop.tick()
    open_pr = jobs.started()
    surveys = {}
    for org in orgs:
        survey = await loop.survey(org, refresh=False)
        surveys[org.owner] = (survey.days, {s.context.repo: s.reason for s in survey.repos})
    gamma = next(org for org in orgs if org.owner == "gamma")
    run = await loop.run(gamma)
    run_gamma = (run.days, [s.repo for s in run.started], jobs.started()["gamma/a"])
    seen = {"tick": tick, "open_pr": open_pr, "surveys": surveys, "run_gamma": run_gamma}

    assert seen == MAIN, f"the moved settings audit other repositories than the rows did: {seen}"


# ---- until an organization's copy succeeds ------------------------------------------------------


def test_the_effective_view_shows_what_an_organization_not_imported_yet_reads_from_its_row() -> None:
    from embry0.settings.effective import effective_view

    view = BudgetView(_snapshot({}, {}), moved=_moved())
    row = {"context_audit_days": 7, "context_audit_open_prs": True}
    pending = view.not_imported("acme", row).get("organization:acme", {})
    assert {key: found.value for key, found in pending.items() if key[0] == "context_audit"} == {
        ("context_audit", "days"): 7,
        ("context_audit", "open_prs"): True,
    }, f"the effective view would show a default the schedule does not enforce: {pending}"
    effective = effective_view(
        CATALOG, _snapshot({}, {}), [], None, owner="acme", not_imported=view.not_imported("acme", row)
    )
    keys = {k["key"]: k for s in effective["sections"] if s["name"] == "context_audit" for k in s["keys"]}
    assert (keys["days"]["value"], keys["days"]["source"]) == (7, "not_imported")
    assert keys["days"]["not_imported"] == {"read_from": "organization", "label": "acme's own settings"}


@pytest.mark.requires_postgres
async def test_a_failed_copy_of_the_context_audit_s_settings_is_tried_again_while_the_process_runs(
    db_with_migrations: Any,
) -> None:
    """The release before copied acme's budget caps; this release's first copy of its schedule failed."""
    import os
    from unittest.mock import patch

    from embry0.config import Embry0Config
    from embry0.services.budget_policy import BudgetPolicy
    from embry0.settings.org_import import import_org_settings_once
    from embry0.settings.scope import Scope
    from embry0.settings.store import SettingsStore

    moved = _moved()
    row = {"context_audit_days": 7, "context_audit_open_prs": True, "context_audit_budget_usd": 0.5}
    org = _org("acme", **row)
    store = SettingsStore(db_with_migrations)
    await import_org_settings_once(store, [(org.owner, org.settings)], [m for m in moved if m.section == "budget"])
    assert "context_audit" not in (await store.document(Scope.organization("acme")))["data"]

    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    # A policy that never retries shows what the schedule reads meanwhile: the row.
    waiting = await BudgetPolicy(store, config, moved=moved, retry_seconds=None).view([org])
    assert waiting.moved_values("context_audit", "acme", row) == {"days": 7, "open_prs": True}, (
        "until the copy, the schedule reads the row"
    )
    view = await BudgetPolicy(store, config, moved=moved).view([org])
    layer = await store.document(Scope.organization("acme"))
    assert layer["data"].get("context_audit") == {"days": 7, "open_prs": True}, (
        "the policy did not try the copy again for an organization whose budget caps were already copied"
    )
    assert view.moved_values("context_audit", "acme", row) == {}, "copied: the row is read by nothing"


# ---- a repository renamed to another owner -------------------------------------------------------

#: Two renames across owners: alpha/r is registered under its old slug, so the rows read it with alpha;
#: beta-new/s under its new one, read with beta-new, which is not registered and so not audited.
RENAMES = {"alpha/r": "alpha-labs/r", "beta/s": "beta-new/s"}
#: What origin/main (697264d2) did with ROWS, these renames and both repositories registered as named.
MAIN_RENAMES = {
    **MAIN,
    "tick": ["alpha/a", "alpha/b", "alpha/r", "zeta/a", "zeta/b"],
    "open_pr": {"alpha/a": True, "alpha/b": True, "alpha/r": True, "zeta/a": False, "zeta/b": False},
    "surveys": {**MAIN["surveys"], "alpha": (7, {"alpha/a": "live", "alpha/b": "live", "alpha/r": "live"})},
}


async def _store_with_rows(db: Any, registered: list[str]) -> Any:
    from embry0.settings.store import SettingsStore
    from embry0.storage.repositories.organizations import OrganizationsRepository
    from embry0.storage.repositories.repos import ReposRepository

    for owner, row in ROWS.items():
        await OrganizationsRepository(db).register(owner, "github", settings=dict(row))
    for slug in registered:
        await ReposRepository(db).register(slug, forge="github")
    return SettingsStore(db)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("copied", [False, True], ids=["import-pending", "imported"])
async def test_the_real_copy_audits_as_the_rows_did_a_renamed_repository_included(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch, copied: bool
) -> None:
    import os
    from unittest.mock import patch

    from embry0.config import Embry0Config
    from embry0.services.budget_policy import BudgetPolicy
    from embry0.settings.org_import import import_org_settings_once
    from embry0.storage import repo_renames
    from embry0.storage.repositories.organizations import OrganizationsRepository
    from embry0.storage.repositories.repos import ReposRepository

    moved = _moved()
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", dict(RENAMES))
    store = await _store_with_rows(db_with_migrations, [*REPOS, "alpha/r", "beta-new/s"])
    if copied:
        await import_org_settings_once(store, list(ROWS.items()), moved)
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    # No retry: the pending case must stay pending while it is read.
    policy = BudgetPolicy(store, config, moved=moved, retry_seconds=None)
    orgs = await OrganizationsRepository(db_with_migrations).list_all()

    jobs = Jobs()
    jobs.finished("alpha/b", 8)
    jobs.finished("beta/a", 8)
    jobs.finished("zeta/a", 2)
    jobs.finished("eta/a", 2)

    async def start_job(context: ca.RepoContext, budget_usd: float, open_pr: bool, run_with: Any = None) -> str:
        return jobs._add(  # noqa: SLF001 - the fake's own row
            context.repo, "pending", T0, None, ca.ACTOR, {"open_pr": open_pr, "max_budget_usd": budget_usd}
        )

    async def client_for(org: Organization) -> Forge:
        return Forge()

    async def gate() -> None:
        return None

    loop = ca.ContextAuditLoop(
        organizations=Orgs(orgs),
        registry=ReposRepository(db_with_migrations),
        jobs=jobs,
        client_for=client_for,
        start_job=start_job,
        ask_gate=gate,
        budget=policy.view,
        clock=lambda: T0,
    )
    tick = await loop.tick()
    surveys = {}
    for org in orgs:
        survey = await loop.survey(org, refresh=False)
        surveys[org.owner] = (survey.days, {s.context.repo: s.reason for s in survey.repos})
    run = await loop.run(next(org for org in orgs if org.owner == "gamma"))
    seen = {
        "tick": tick,
        "open_pr": {r: v for r, v in jobs.started().items() if r != "gamma/a"},
        "surveys": surveys,
        "run_gamma": (run.days, [s.repo for s in run.started], jobs.started()["gamma/a"]),
    }
    assert seen == MAIN_RENAMES, f"the copy audits other repositories than the rows did: {seen}"


@pytest.mark.requires_postgres
async def test_the_copy_writes_a_renamed_repository_s_own_layer_once_and_only_one_registered_under_its_old_slug(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.settings.org_import import import_org_settings_once
    from embry0.settings.scope import Scope
    from embry0.storage import repo_renames

    moved = _moved()
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", dict(RENAMES))
    store = await _store_with_rows(db_with_migrations, ["alpha/r", "beta-new/s"])
    # alpha also holds a budget cap: an organization's alone, so it is copied into alpha's layer and no other.
    rows = [
        (owner, {**row, "context_audit_budget_usd": 3.0} if owner == "alpha" else row) for owner, row in ROWS.items()
    ]

    result = await import_org_settings_once(store, rows, moved)

    renamed = await store.document(Scope.repository("alpha-labs/r"))
    assert renamed["data"].get("context_audit") == {"days": 7, "open_prs": True}, (
        "a repository registered under alpha but renamed to another owner reads that owner's layer: it needs "
        f"alpha's values in its own: {renamed['data']}"
    )
    assert "budget" not in renamed["data"] and [r for r in result.refused if r[0] == "alpha-labs/r"] == [], (
        f"only the keys a repository's layer may set are copied into it, none refused there: {result.refused}"
    )
    budget = (await store.document(Scope.organization("alpha")))["data"].get("budget")
    assert budget == {"context_audit_job_usd": 3.0}, budget
    assert {"context_audit.days", "context_audit.open_prs"} <= set(renamed["bootstrapped_keys"])
    assert getattr(result, "renamed", {}) == {"alpha-labs/r": {"context_audit": {"days": 7, "open_prs": True}}}
    untouched = await store.document(Scope.repository("beta-new/s"))
    assert untouched["generation"] == 0, "one registered under its new slug was read with its new owner all along"

    await store.set_keys(
        "context_audit", {"days": 30}, actor="alex@example.com", scope=Scope.repository("alpha-labs/r")
    )
    again = await import_org_settings_once(store, rows, moved)
    assert getattr(again, "renamed", {}) == {} and again.imported == {}, "once"
    assert (await store.snapshot()).get(_section_of("context_audit"), repo="alpha-labs/r").days == 30


@pytest.mark.requires_postgres
async def test_a_renamed_repository_s_failed_copy_leaves_its_organization_to_be_tried_again(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.settings import org_import
    from embry0.settings.scope import Scope
    from embry0.storage import repo_renames

    moved = _moved()
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", dict(RENAMES))
    store = await _store_with_rows(db_with_migrations, ["alpha/r"])
    real = org_import._import_one

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if args[1].layer == "repository":
            raise RuntimeError("connection lost")
        return await real(*args, **kwargs)

    monkeypatch.setattr(org_import, "_import_one", flaky)
    first = await org_import.import_org_settings_once(store, [("alpha", ROWS["alpha"])], moved)
    assert first.failed == ("alpha",), "the organization's copy waits for its renamed repository's"
    assert "context_audit" not in (await store.document(Scope.organization("alpha")))["data"]
    monkeypatch.setattr(org_import, "_import_one", real)
    second = await org_import.import_org_settings_once(store, [("alpha", ROWS["alpha"])], moved)
    assert second.failed == () and "alpha" in second.imported
    assert (await store.document(Scope.repository("alpha-labs/r")))["data"]["context_audit"]["days"] == 7


def _section_of(name: str) -> Any:
    found = CATALOG.get(name)
    assert found is not None
    return found
