"""Every JSON value the database stores reads back, however deep: no GET route answers 500 for its depth (#431).

For each json and jsonb column of every table, the test writes one row of each
table, then puts a value 300 levels deep in that column (on every row of the
table), as a writer other than the API or a row from before the write-side
checks could, and reads every GET route the API describes with its path filled
from those rows. A route that answers 500 must answer 500 with a value 5 levels
deep too: then its failure is the value's shape (a list where it expects one of
strings), not its depth. The column list is read from the schema, and a column
no row of :data:`ROWS` can hold fails the test, so a new one is covered from the
day it is added.
"""

from __future__ import annotations

import errno
import ipaddress
import re
import socket
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

JOB, ISSUE, CASE, REPO, OWNER = "job-deep", "iss-deep", "case-deep", "acme/widgets", "acme"
MAP = '{"format": "embry0.map/1", "slug": "deep-map", "nodes": [{"id": "part-1", "label": "Part"}], "edges": []}'

#: One row of each table with a json or jsonb column, in an order that writes a row before the rows that name it:
#: the columns it must have, as SQL expressions. A table the app's start already filled (its built-in agents and
#: templates, the deployment's settings) gets no row of its own; its rows take the value.
ROWS: dict[str, dict[str, str]] = {
    "issues": {"id": f"'{ISSUE}'", "title": "'Deep'", "repo": f"'{REPO}'"},
    "jobs": {
        "job_id": f"'{JOB}'",
        "task": "'t'",
        "repo": f"'{REPO}'",
        "issue_id": f"'{ISSUE}'",
        "status": "'completed'",
        "started_at": "now()",
    },
    "credentials": {
        "id": "'cred-0001'",
        "kind": "'forge_token'",
        "provider": "'github'",
        "host": "'github.com'",
        "label": "'l'",
        "secret_ciphertext": "'x'",
        "masked_preview": "'****'",
        "created_by": "'someone'",
    },
    "organizations": {"owner": f"'{OWNER}'", "forge": "'github'"},
    "healing_cases": {"id": f"'{CASE}'", "repo": f"'{REPO}'", "origin": "'watcher'", "issue_id": f"'{ISSUE}'"},
    "healing_case_events": {"case_id": f"'{CASE}'", "event": "'e'", "to_stage": "'detected'"},
    "traces": {
        "trace_id": "'trace-deep'",
        "job_id": f"'{JOB}'",
        "agent_type": "'developer'",
        "model": "'m'",
        "result": "'success'",
    },
    "agent_sessions": {
        "id": "'session-deep'",
        "job_id": f"'{JOB}'",
        "agent_type": "'developer'",
        "mode": "'sdk'",
        "messages": """'[{"role": "user"}]'""",
    },
    "audit_log": {"action": "'job.created'", "issue_id": f"'{ISSUE}'"},
    "config_revisions": {"id": "4242", "scope": "'deployment'", "surface": "'settings'"},
    "deliverables": {"id": "'deliverable-deep'", "job_id": f"'{JOB}'", "type": "'report'"},
    "digest_deliveries": {"day": "CURRENT_DATE"},
    "hosts": {"id": "'host-deep'", "name": "'h'", "transport": "'ssh'"},
    "integration_config": {"id": "'default'"},
    "job_verifications": {
        "job_id": f"'{JOB}'",
        "repo": f"'{REPO}'",
        "tier": "'t'",
        "gate_verdict": "'pass'",
        "browser_qa": "'skipped'",
        "report": "'{}'",
    },
    "knowledge_maps": {
        "slug": "'deep-map'",
        "title": "'Deep'",
        "graph": f"'{MAP}'",
        "owner": f"'{OWNER}'",
        "repo": f"'{REPO}'",
    },
    "leads": {"id": "'lead-deep'", "kind": "'crack'", "repo": f"'{REPO}'", "source_key": "'k'", "title": "'t'"},
    "log_signatures": {
        "signature_id": "'signature-deep'",
        "repo": f"'{REPO}'",
        "template": "'t'",
        "first_seen_at": "now()",
        "last_seen_at": "now()",
    },
    "merge_approvals": {
        "id": "'approval-deep'",
        "repo": f"'{REPO}'",
        "pr_number": "1",
        "pr_url": "'https://forge.example/acme/widgets/pull/1'",
        "head_sha": "'abc'",
        "job_id": f"'{JOB}'",
    },
    "org_discovery_runs": {"id": "'run-deep'", "owner": f"'{OWNER}'", "status": "'done'"},
    "org_needs": {"id": "'need-deep'", "owner": f"'{OWNER}'", "kind": "'k'", "signature": "'s'", "title": "'t'"},
    "org_repo_facts": {"owner": f"'{OWNER}'", "repo": f"'{REPO}'", "run_id": "'run-deep'"},
    "pipeline_templates": {"id": "'template-deep'", "name": "'deep'", "graph_definition": "'{}'"},
    "pr_reviews": {
        "id": "'review-deep'",
        "repo": f"'{REPO}'",
        "number": "1",
        "head_sha": "'abc'",
        "trigger": "'ready'",
        "trigger_key": "'k'",
        "job_id": f"'{JOB}'",
    },
    "progression_packs": {
        "version": "1",
        "status": "'active'",
        "schema_version": "1",
        "source": "'person'",
        "follows_default": "false",
        "text": "'x'",
        "document": "'{}'",
    },
    "progression_rollups": {
        "pack_hash": "'h'",
        "fold_version": "'v'",
        "timezone": "'UTC'",
        "through_day": "CURRENT_DATE",
        "boundary": "now()",
        "state": "'{}'",
        "rewards": "'[]'",
        "built_at": "now()",
        "rebuilt_at": "now()",
    },
    "provider_catalogs": {"provider": "'xai'"},
    "provider_config": {"id": "'default'"},
    "qa_app_results": {"job_id": f"'{JOB}'", "app_name": "'web'", "status": "'passed'"},
    "qa_image_tags": {"repo": f"'{REPO}'", "image_tag": "'t'", "lockfile_sha": "'s'"},
    "qa_run_metadata": {
        "job_id": f"'{JOB}'",
        "apps_to_qa": "'{web}'",
        "apps_skipped": "'{}'",
        "force_all_apps": "false",
    },
    "repo_config_revisions": {"repo": f"'{REPO}'", "surface": "'qa_config'"},
    "repo_preferences": {"repo": f"'{REPO}'"},
    "repo_areas": {"repo": f"'{REPO}'", "yaml_text": "'areas: []'", "data": "'{}'"},
    "repo_setup_proposals": {"id": "'setup-deep'", "repo": f"'{REPO}'", "status": "'pending'"},
    "repo_standard_state": {"repo": f"'{REPO}'", "owner": f"'{OWNER}'", "kind": "'design'"},
    "sandbox_profiles": {"name": "'deep-profile'"},
    "skill_sources": {"id": "'source-deep'", "kind": "'inline'"},
    "slop_meter_commits": {"repo": f"'{REPO}'", "sha": "'abc'", "seq": "0", "authored_at": "now()"},
    "slop_meter_repos": {"repo": f"'{REPO}'", "reader_place": """'{"stage": 2}'"""},
    "standards": {
        "id": "'standard-deep'",
        "owner": f"'{OWNER}'",
        "kind": "'design'",
        "version": "1",
        "status": "'active'",
    },
    "watcher_stacks": {"repo": f"'{REPO}'", "logql": "'{app=\"x\"}'"},
    "agent_definitions": {},
    "settings_documents": {},
}

#: Rows beside :data:`ROWS`, for routes that read more than one row of a table or a table with no json column: the
#: progression pack's draft, the credential bound to the repository, and the map of its areas waiting for a decision.
MORE_ROWS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "progression_packs",
        {
            "version": "2",
            "status": "'draft'",
            "schema_version": "1",
            "source": "'person'",
            "follows_default": "false",
            "text": "'x'",
            "document": "'{}'",
        },
    ),
    (
        "credential_bindings",
        {
            "scope_kind": "'repo'",
            "scope_value": f"'{REPO}'",
            "role": "'forge'",
            "provider": "'github'",
            "credential_id": "'cred-0001'",
            "created_by": "'someone'",
        },
    ),
    (
        "repo_areas_proposals",
        {"id": "'areas-proposal-deep'", "repo": f"'{REPO}'", "status": "'pending'", "proposed_text": "'areas: []'"},
    ),
)

#: What fills each path parameter, by name; a route with another one is not read.
PARAMS = {
    "job_id": JOB,
    "run_id": JOB,
    "issue_id": ISSUE,
    "case_id": CASE,
    "agent_type": "developer",
    "owner": OWNER,
    "full_repo": REPO,
    "repo": REPO,
    "kind": "design",
    "slug": "deep-map",
    "node_id": "part-1",
    "app": "web",
    "approval_id": "approval-deep",
    "host_id": "host-deep",
    "source_id": "source-deep",
    "deliverable_id": "deliverable-deep",
    "provider_name": "xai",
    "version": "1",
    "attempt_n": "1",
    "template_id": "template-deep",
    "name": "deep-profile",
    "revision_id": "4242",
    "decision_id": f"done:{JOB}",
}

#: Query strings a route needs before it answers, by its path.
QUERY = {
    "/api/v1/maps/deep-map/parts/part-1": "owner=acme",
    "/api/v1/asks/recap": "since=2026-01-01T00:00:00Z",
    "/api/v1/asks/of": "person=someone@example.com",
    "/api/v1/credentials/bindings": "scope_kind=repo&scope_value=acme/widgets",
}

_OBJECTS = "read from object storage, which the test app has none of; no json column"
_FORGE = "asks the forge; no json column"

#: The GET routes the sweep fills and cannot read, each with why. Every other one must answer 200 with the rows
#: written, before anything is planted: a route that answers 503 for a service the test app lacks, or 404 for a
#: row it has not got, would otherwise pass while reading nothing.
UNREAD: dict[str, str] = {
    "/api/v1/auth/login": "single sign-on, which the test app has not configured",
    "/api/v1/auth/callback": "single sign-on, which the test app has not configured",
    "/api/v1/auth/me": "the browser session's own identity; no stored row",
    f"/api/v1/jobs/{JOB}/deliverables/deliverable-deep/download": _OBJECTS,
    f"/api/v1/jobs/{JOB}/artifacts/screenshots/latest": _OBJECTS,
    f"/api/v1/jobs/{JOB}/qa/attempts": _OBJECTS,
    f"/api/v1/jobs/{JOB}/qa/attempts/1/result": _OBJECTS,
    f"/api/v1/jobs/{JOB}/qa/screenshots/latest": _OBJECTS,
    f"/api/v1/qa/runs/{JOB}/apps/web/artifacts/design": _OBJECTS,
    f"/api/v1/jobs/{JOB}/qa/services": "asks the container runtime; no json column",
    "/api/v1/logs/containers": "asks Loki; no json column",
    "/api/v1/logs/query": "asks Loki; no json column",
    "/api/v1/config/providers/xai/usage": "asks the provider for its spend; no stored row",
    "/api/v1/orgs/acme/webhook": _FORGE,
    "/api/v1/orgs/acme/context-audit": _FORGE,
    "/api/v1/github/repos": _FORGE,
    "/api/v1/github/discoverable-repos": _FORGE,
    "/api/v1/admin/retention/preview": "counts what a retention sweep would delete; a service the test app lacks",
    "/api/v1/progression/preview": "compiles the draft pack, which no value of the sweep's shape is (422); the draft"
    " itself is read at /progression/pack/draft",
    "/api/v1/credentials/resolve": "answers 422 without a repository, and the sweep names none: resolving one"
    " decrypts its credential's secret, and the sweep's row has none the app can decrypt",
    "/api/v1/repos/acme/widgets/-/qa-config": "the QA config is stored as text, in no json column",
    "/api/v1/repos/acme/widgets/qa-config": "the QA config is stored as text, in no json column",
}


def _nested(levels: int) -> Any:
    value: Any = "leaf"
    for _ in range(levels):
        value = {"a": value}
    return value


async def _columns(db: Any) -> list[tuple[str, str, str]]:
    """Every json and jsonb column of the schema: (table, column, the JSON kind its value takes)."""
    rows = await db.fetch(
        """
        SELECT c.table_name, c.column_name, c.data_type, COALESCE(c.column_default, '') AS d,
               COALESCE((SELECT string_agg(pg_get_constraintdef(k.oid), ' ') FROM pg_constraint k
                         WHERE k.conrelid = (quote_ident(c.table_name))::regclass AND k.contype = 'c'), '') AS checks
        FROM information_schema.columns c
        JOIN information_schema.tables t USING (table_schema, table_name)
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' AND c.data_type IN ('json', 'jsonb')
        ORDER BY 1, 2
        """
    )
    out = []
    for r in rows:
        column, checks = r["column_name"], r["checks"]
        assert r["data_type"] == "jsonb", f"{r['table_name']}.{column} is {r['data_type']}: plant it as text"
        array = re.search(rf"jsonb_typeof\(\"?{column}\"?\) = 'array'", checks) or str(r["d"]).startswith("'[")
        out.append((r["table_name"], column, "array" if array else "object"))
    return out


async def _write_rows(app: Any) -> None:
    """The rows of :data:`ROWS`, beside any the app's start wrote, the repository registered, and the watcher's
    stacks and runs and the merge approvals readable (the test app starts neither service)."""
    from embry0.services.merge_approvals import MergeApprovalService
    from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository
    from embry0.storage.repositories.watcher_runs import WatcherRunsRepository
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    db = app.state.db
    await db.execute(f"INSERT INTO repos (repo) VALUES ('{REPO}') ON CONFLICT DO NOTHING")
    for table, values in [*ROWS.items(), *MORE_ROWS]:
        if not values:
            continue
        columns = ", ".join(f'"{c}"' for c in values)
        await db.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({', '.join(values.values())}) ON CONFLICT DO NOTHING"
        )
    if getattr(app.state, "watcher_stacks_repo", None) is None:
        app.state.watcher_stacks_repo = WatcherStacksRepository(db)
    if getattr(app.state, "watcher_runs_repo", None) is None:
        app.state.watcher_runs_repo = WatcherRunsRepository(db)
    if getattr(app.state, "merge_approvals", None) is None:
        app.state.merge_approvals = MergeApprovalService(
            repo=MergeApprovalsRepository(db), github=None, notifier=None, config=app.state.config
        )


def _paths(app: Any) -> list[str]:
    paths = []
    for path, operations in app.openapi()["paths"].items():
        if "get" not in operations or not path.startswith("/api/v1/") or path.endswith("/events"):
            continue  # an event stream never ends
        names = re.findall(r"{([^}]+)}", path)
        if any(name not in PARAMS for name in names):
            continue
        filled = re.sub(r"{name}", "widgets", path) if "watcher" in path else path
        for name in names:
            filled = filled.replace("{" + name + "}", PARAMS.get(name, ""))
        paths.append(filled + (f"?{QUERY[filled]}" if filled in QUERY else ""))
    return paths


_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _loopback(host: Any) -> bool:
    if host is None:
        return True
    host = (host.decode() if isinstance(host, bytes) else str(host)).strip("[]")
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """No connection leaves this host: a name to resolve or an address to connect to that is not loopback is
    refused, and the test fails naming it. The database is on loopback; a route that asks the forge must ask the
    stand-in :func:`client` gives it, never a real one with whatever token the environment holds."""
    tried: list[str] = []
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def refused_connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            tried.append(f"connect {address[0]}")
            raise OSError(errno.ENETUNREACH, "the sweep reaches no host but this one")
        return connect(self, address)

    def refused_connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            tried.append(f"connect {address[0]}")
            return errno.ENETUNREACH
        return connect_ex(self, address)

    def refused_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _loopback(host):
            tried.append(f"resolve {host!r}")
            raise socket.gaierror(socket.EAI_NONAME, "the sweep reaches no host but this one")
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", refused_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", refused_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", refused_getaddrinfo)
    yield tried
    assert not tried, f"the test tried to reach another host {len(tried)} times: {sorted(set(tried))}"


class _Forge:
    """The forge, as the environment detection reads it: a template naming one variable, for any token."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def get(self, _path: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(status_code=200, text="SWEEP_VARIABLE=\n")


@pytest.fixture
async def client(api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """The test app, answering an error with its status rather than raising it, as a server does, and with no rate
    limit: the sweep reads each route many times a minute, and a read the limit answers 429 measures nothing. The
    environment detection reads the forge through :class:`_Forge`, so it is read with no call out."""
    from embry0.api.middleware.rate_limit import RateLimitMiddleware

    monkeypatch.setattr("embry0.api.v1.environment.GitHubClient", _Forge)
    app = api_client.app  # type: ignore[attr-defined]
    limits = [m for m in app.user_middleware if m.cls is RateLimitMiddleware]
    assert limits and app.middleware_stack is None, "lift the rate limit before the app's first request builds it"
    for limit in limits:
        limit.kwargs["requests_per_minute"] = 10**9
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


async def test_every_json_column_has_a_row_to_hold_a_value(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    missing = sorted({table for table, _column, _kind in await _columns(db)} - set(ROWS))
    assert not missing, f"tables with a json column and no row in ROWS (add one, so its column is read deep): {missing}"
    await _write_rows(client.app)  # type: ignore[attr-defined]
    empty = [table for table in ROWS if not await db.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table})")]
    assert not empty, f"tables still empty after writing ROWS: {empty}"
    paths = _paths(client.app)  # type: ignore[attr-defined]
    stale = sorted(set(UNREAD) - set(paths))
    assert not stale, f"UNREAD names routes the sweep does not read any more: {stale}"
    answers = {path: (await client.get(path)).status_code for path in paths}
    unread = [
        f"GET {path} answered {status}" for path, status in answers.items() if status != 200 and path not in UNREAD
    ]
    assert not unread, (
        "these answer other than 200 with the rows written, so the sweep would read nothing there: give them what"
        " they need (a row, a query string, a service) or name them in UNREAD with why:\n" + "\n".join(unread)
    )
    readable = sorted(path for path in UNREAD if answers[path] == 200)
    assert not readable, f"these answer 200 now: take them out of UNREAD, so the sweep reads them: {readable}"


#: Keys a column's check asks of its object, beside the deep one.
SHAPES = {("hosts", "log_source"): {"kind": "journald"}}
#: Where in a column's object its readers look, where not under any key: a map's details are by part, and a settings
#: change holds its documents' values by section and key (``data.<section>.<key>``), which its read lists.
PATHS: dict[tuple[str, str], tuple[str, ...]] = {
    ("knowledge_maps", "details"): ("part-1",),
    ("config_revisions", "before"): ("data", "sweep", "x"),
    ("config_revisions", "after"): ("data", "sweep", "x"),
}


async def _store(db: Any, table: str, column: str, value: Any) -> None:
    """Every row of ``table`` holds ``value`` in ``column`` (the pool's codec writes the object, not its text)."""
    await db.execute(f'UPDATE {table} SET "{column}" = $1', value)


def _deep(table: str, column: str, kind: str, levels: int) -> Any:
    *outer, key = PATHS.get((table, column), ("x",))
    value: dict[str, Any] = {**SHAPES.get((table, column), {}), key: _nested(levels)}
    for name in reversed(outer):
        value = {name: value}
    return [value] if kind == "array" else value


async def _keep(db: Any, table: str, column: str) -> Any:
    """What every row of ``table`` holds in ``column`` now, by its primary key (else a unique index: a knowledge
    map's is its slug and owner), for :func:`_restore`."""
    index = await db.fetchval(
        "SELECT indexrelid FROM pg_index WHERE indrelid = $1::regclass AND indisunique "
        "AND indpred IS NULL AND indexprs IS NULL ORDER BY indisprimary DESC, indexrelid LIMIT 1",
        table,
    )
    assert index, f"{table} has no primary key or unique index to put {column} back by"
    keys = [
        str(r["attname"])
        for r in await db.fetch(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) WHERE i.indexrelid = $1",
            index,
        )
    ]
    names = ", ".join(f'"{k}"' for k in keys)
    return keys, await db.fetch(f'SELECT {names}, "{column}" AS kept FROM {table}')


async def _restore(db: Any, table: str, column: str, kept: Any) -> None:
    keys, rows = kept
    where = " AND ".join(f'"{k}" IS NOT DISTINCT FROM ${i + 2}' for i, k in enumerate(keys))
    for row in rows:
        await db.execute(f'UPDATE {table} SET "{column}" = $1 WHERE {where}', row["kept"], *(row[k] for k in keys))


@pytest.mark.parametrize("table", sorted(ROWS))
async def test_no_route_answers_500_for_a_deep_value_in_a_json_column(client: AsyncClient, table: str) -> None:
    """Each json column of ``table`` alone, 300 levels deep, then put back. A route that answers 500 is read again
    at once with the value 5 levels deep, so what other reads did meanwhile cannot tell them apart."""
    db = client.app.state.db  # type: ignore[attr-defined]
    columns = [(column, kind) for name, column, kind in await _columns(db) if name == table]
    assert columns, f"{table} has no json column any more: take it out of ROWS"
    await _write_rows(client.app)  # type: ignore[attr-defined]
    paths = [path for path in _paths(client.app) if path not in UNREAD]  # type: ignore[attr-defined]
    problems, unread = [], []
    for column, kind in columns:
        kept = await _keep(db, table, column)
        await _store(db, table, column, _deep(table, column, kind, 300))
        deep = await db.fetchval(f"""SELECT bool_and(jsonb_path_exists("{column}", '$.**{{250}}')) FROM {table}""")
        assert deep, f"{table}.{column} does not hold a value 250 levels deep: the test would read nothing deep"
        for path in paths:
            status = (await client.get(path)).status_code
            if status not in (200, 500):
                unread.append(f"{table}.{column}: GET {path} answered {status}")
            if status != 500:
                continue
            await _store(db, table, column, _deep(table, column, kind, 5))
            if (await client.get(path)).status_code != 500:
                problems.append(f"{table}.{column}: GET {path}")
            await _store(db, table, column, _deep(table, column, kind, 300))
        await _restore(db, table, column, kept)
    assert not unread, "these reads answered neither the rows nor a 500, so they measured nothing:\n" + "\n".join(
        unread
    )
    assert not problems, "these answer 500 with a value 300 levels deep, and not with one 5 deep:\n" + "\n".join(
        problems
    )
