"""Every list a GET route reads is in one order: no query's order ends on a time.

Two rows written in the same transaction, or by one ``INSERT ... SELECT``,
share their ``created_at``. A query that sorts by that time alone returns them
in whatever order the plan happens to produce: a page of ``GET /jobs`` and the
next can hold the same job twice and skip another, and two reads of one list
can differ though nothing changed. Each list query therefore ends its order
with a column that is unique (the row's id), so ties are broken the same way
every time.

- **The guard.** On the real app over a real database (the fixture of
  :mod:`tests.unit.api.test_aggregate_reads_inventory`), every GET route is
  read while every statement the app sends Postgres is recorded; an ``ORDER
  BY`` whose last key is a time (``*_at``, ``at``, ``first_seen``,
  ``last_seen``, or the first of several, ``COALESCE``) fails, named with the
  route that sent it: it leaves rows of one time in no order, whatever keys
  come before it. An aggregate ordered by the very value it collects
  (``ARRAY_AGG(t ORDER BY t)``) is not a list order and passes.
- **Paging through ties.** The routes that page with ``limit`` and
  ``offset`` (:data:`PAGED`), given more rows than two pages that all share
  one time, written in an order that is not their ids', return each row
  exactly once across the pages, in the order of their ids.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.aggregate_reads import aggregate_reads_app
from tests.fixtures.repository_writes import COVERED, call, repository_writes_app
from tests.unit.api.test_aggregate_reads_inventory import urls
from tests.unit.api.test_job_reads_inventory import get_routes

pytestmark = pytest.mark.requires_postgres

#: A sort key that is a point in time.
_TIME = re.compile(r"(?:^|\.)(?:\w+_at|at|first_seen|last_seen)$")
_FIRST_OF = re.compile(r"^(?:COALESCE|GREATEST|LEAST)\((.*)\)$", re.IGNORECASE)

_STOP = re.compile(r"\b(LIMIT|OFFSET|FOR|FETCH|UNION|RETURNING|WINDOW)\b", re.IGNORECASE)


def sort_keys(sql: str) -> list[list[str]]:
    """The keys of each ``ORDER BY`` in ``sql``, each without its direction, in the order they appear; but an
    aggregate's ordered by the value it collects."""
    found = []
    for m in re.finditer(r"\bORDER\s+BY\b", sql, re.IGNORECASE):
        collected = _collected(sql, m.start())
        depth, keys, start, i = 0, [], m.end(), m.end()
        while i < len(sql):
            c = sql[i]
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    break
                depth -= 1
            elif c == ";" and depth == 0:
                break
            elif c == "," and depth == 0:
                keys.append(sql[start:i])
                start = i + 1
            elif depth == 0 and _STOP.match(sql, i) and not sql[i - 1].isalnum() and sql[i - 1] != "_":
                break
            i += 1
        keys.append(sql[start:i])
        if [_key(k) for k in keys] != [collected]:
            found.append([_key(k) for k in keys])
    return found


def _collected(sql: str, at: int) -> str:
    """What the call ``ORDER BY`` at ``at`` sits in collects (``ARRAY_AGG(x ORDER BY ...)`` gives ``x``), or ''."""
    depth = 0
    for i in range(at - 1, -1, -1):
        if sql[i] == ")":
            depth += 1
        elif sql[i] == "(":
            if depth == 0:
                return " ".join(sql[i + 1 : at].split())
            depth -= 1
    return ""


def _key(text: str) -> str:
    words = text.split()
    while words and words[-1].upper() in {"ASC", "DESC", "NULLS", "FIRST", "LAST"}:
        words.pop()
    return " ".join(words)


def is_time(key: str) -> bool:
    """Whether ``key`` is a point in time: a time column, or the first of several (``COALESCE(a_at, b_at)``)."""
    first_of = _FIRST_OF.match(key)
    if first_of:
        return all(is_time(part.strip()) for part in first_of.group(1).split(","))
    return bool(_TIME.search(key))


def ending_on_a_time(sql: str) -> list[str]:
    """Each ``ORDER BY`` of ``sql`` whose last key is a time, as its keys."""
    return [", ".join(keys) for keys in sort_keys(sql) if is_time(keys[-1])]


def test_the_parser_finds_an_order_ending_on_a_time_and_lets_a_tiebreaker_pass() -> None:
    assert ending_on_a_time("SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1 OFFSET $2") == ["created_at"]
    assert ending_on_a_time("SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC LIMIT $1") == []
    assert ending_on_a_time("SELECT (SELECT t.a FROM traces t ORDER BY t.created_at DESC LIMIT 1) FROM x") == [
        "t.created_at"
    ]
    assert ending_on_a_time("ROW_NUMBER() OVER (PARTITION BY repo ORDER BY started_at DESC NULLS LAST) AS rn") == [
        "started_at"
    ]
    assert ending_on_a_time("SELECT DISTINCT ON (repo) * FROM merged ORDER BY repo, at") == ["repo, at"]
    assert ending_on_a_time("SELECT * FROM t ORDER BY COALESCE(finished_at, updated_at)") == [
        "COALESCE(finished_at, updated_at)"
    ]
    assert ending_on_a_time("SELECT * FROM t ORDER BY COALESCE(a.x, 0) DESC") == []
    assert ending_on_a_time("SELECT * FROM t ORDER BY lower(name)") == []
    assert ending_on_a_time("SELECT * FROM asks ORDER BY due_at") == ["due_at"]
    assert ending_on_a_time("SELECT ARRAY_AGG(created_at ORDER BY created_at) FROM x") == []
    assert ending_on_a_time("SELECT ARRAY_AGG(status ORDER BY created_at) FROM x") == ["created_at"]


async def _recorded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every statement any connection sends from now on (pooled or in a transaction), in order."""
    sent: list[str] = []
    for name in ("fetch", "fetchrow", "fetchval", "execute"):
        real: Callable[..., Any] = getattr(asyncpg.Connection, name)

        async def recording(self: Any, query: str, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
            sent.append(query)
            return await _real(self, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.Connection, name, recording)
    return sent


async def test_no_get_route_reads_rows_in_an_order_that_ends_on_a_time(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        await deployment.add_uncovered()
        sent = await _recorded(monkeypatch)
        found: dict[str, set[str]] = {}
        read = 0
        for path in get_routes(deployment.app):
            for url in urls(path, deployment.values):
                before = len(sent)
                await call(deployment.app, "GET", url, deployment.keys.open)
                read += len(sent) - before
                for sql in sent[before:]:
                    for keys in ending_on_a_time(sql):
                        found.setdefault(" ".join(sql.split())[:110] + f" … ORDER BY {keys}", set()).add(path)
    assert read > 500, f"the walk sent {read} statements: it measures nothing"
    assert not found, "orders ending on a time, with the routes that read them:\n" + "\n".join(
        f"- {sql}\n    {sorted(paths)}" for sql, paths in sorted(found.items())
    )


#: Rows that share one time, more than two pages of :data:`_PAGE`.
_TIED, _PAGE = 13, 5

#: Their ids, written to the table in this order, which is neither theirs nor its reverse.
_IDS = [f"tie-{n:02d}" for n in random.Random(7).sample(range(_TIED), _TIED)]

_AT = "TIMESTAMPTZ '2099-01-01 00:00:00+00'"

#: The audit rows' ids are numbers: the tied ones are this plus their number, all of one width.
_AUDIT_ID = 10_000


async def _jobs(db: DatabasePool) -> None:
    for job in _IDS:
        await db.execute(
            f"INSERT INTO jobs (job_id, repo, task, status, created_at, started_at) "
            f"VALUES ($1, $2, 't', 'completed', {_AT}, {_AT})",
            job,
            COVERED,
        )


async def _traces(db: DatabasePool) -> None:
    await db.execute(
        f"INSERT INTO jobs (job_id, repo, task, status, created_at) VALUES ('tie-job', $1, 't', 'completed', {_AT})",
        COVERED,
    )
    for trace in _IDS:
        await db.execute(
            f"INSERT INTO traces (trace_id, job_id, agent_type, model, result, created_at) "
            f"VALUES ($1, 'tie-job', 'developer', 'm', 'success', {_AT})",
            trace,
        )


async def _users(db: DatabasePool) -> None:
    for user in _IDS:
        await db.execute(
            f"INSERT INTO users (id, auth0_sub, email, last_login_at) VALUES ($1, $1, $1 || '@x.test', {_AT})", user
        )


async def _issues(db: DatabasePool) -> None:
    for issue in _IDS:
        await db.execute(
            f"INSERT INTO issues (id, title, repo, created_at) VALUES ($1, 't', $2, {_AT})", issue, COVERED
        )


async def _activity(db: DatabasePool) -> None:
    await db.execute(f"INSERT INTO issues (id, title, repo, created_at) VALUES ('tie-issue', 't', $1, {_AT})", COVERED)
    for n in (int(i.split("-")[1]) for i in _IDS):
        await db.execute(
            f"INSERT INTO audit_log (id, action, issue_id, created_at) VALUES ($1, 'issue.updated', 'tie-issue', {_AT})",
            _AUDIT_ID + n,
        )


async def _qa_runs(db: DatabasePool) -> None:
    await _jobs(db)
    for job in _IDS:
        await db.execute("INSERT INTO qa_app_results (job_id, app_name, status) VALUES ($1, 'web', 'passed')", job)


#: Each route that pages with ``limit`` and ``offset``: how to plant the tied rows, its URL, where its rows are, which
#: field names one, and how the ids of the planted rows read (in the order of the ids, newest first).
PAGED: dict[str, tuple[Callable[[DatabasePool], Any], str, str, str]] = {
    "jobs": (_jobs, "/api/v1/jobs", "jobs", "job_id"),
    "traces": (_traces, "/api/v1/traces?job_id=tie-job", "traces", "trace_id"),
    "users": (_users, "/api/v1/users", "users", "id"),
    "issues": (_issues, "/api/v1/issues", "issues", "id"),
    "issue activity": (_activity, "/api/v1/issues/tie-issue/activity", "", "id"),
    "QA runs": (_qa_runs, f"/api/v1/qa/repos/{COVERED}/runs", "", "job_id"),
}


@pytest.mark.parametrize("name", sorted(PAGED))
async def test_pages_through_rows_of_one_time_hold_each_once_in_the_order_of_their_ids(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    plant, url, rows, field = PAGED[name]
    async with repository_writes_app(db_with_migrations, monkeypatch, (COVERED,)) as (app, keys, _values):
        await plant(db_with_migrations)
        await db_with_migrations.execute("ANALYZE")
        read: list[str] = []
        for offset in range(0, _TIED, _PAGE):
            sep = "&" if "?" in url else "?"
            response = await call(app, "GET", f"{url}{sep}limit={_PAGE}&offset={offset}", keys.open)
            assert response.status_code == 200, response.text
            page = response.json()[rows] if rows else response.json()
            read += [str(row[field]) for row in page]
    names = {str(_AUDIT_ID + int(i[-2:])) for i in _IDS} if name == "issue activity" else set(_IDS)
    planted = [r for r in read if r in names]
    expected = sorted(names, reverse=True)
    twice = sorted({r for r in planted if planted.count(r) > 1})
    assert not twice and sorted(planted) == sorted(expected), (
        f"{name}: pages of {_PAGE} over {_TIED} rows of one time held {twice} twice "
        f"and skipped {sorted(set(expected) - set(planted))}"
    )
    assert planted == expected, f"{name}: rows of one time are not in the order of their ids: {planted}"
