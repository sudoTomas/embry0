"""Every list a GET route reads is in one order: each query's order holds a key of its rows.

Two rows written in the same transaction, or by one ``INSERT ... SELECT``,
share their ``created_at``; two issues can cost the same. A query that sorts
by such a value returns the rows that share it in whatever order the plan
happens to produce: a page of ``GET /jobs`` and the next can hold the same
job twice and skip another, the five costliest issues of ``/stats`` can be
any five of seven that cost the same, and two reads of one list can differ
though nothing changed. Each list query therefore orders by a key of its rows,
so ties are broken the same way every time.

- **The guard.** On the real app over a real database (the fixture of
  :mod:`tests.unit.api.test_aggregate_reads_inventory`), every GET route is
  read while every statement the app sends Postgres is recorded. An ``ORDER
  BY`` is total when its keys, with the columns its statement fixes to one
  value (``repo = $1``), hold a key of the table its last key is read from:
  the primary key, or a unique index that is not partial and counts no two
  rows with nulls as distinct (:func:`unique_keys`, read from the catalog);
  or, for a grouped statement, every expression it groups by. Anything else
  fails, named with the route that sent it: a position, a select-list alias,
  an expression, a cast, an aggregate, a time, a cost, a time and then a
  column some rows share. An aggregate ordered by the very value it collects
  (``ARRAY_AGG(t ORDER BY t)``) is not a list order and passes, and
  :data:`TOTAL_WITHOUT_A_KEY` names the few statements whose order is total
  for a reason no key shows, each with it.
- **Paging through ties.** The routes that page with ``limit`` and
  ``offset`` (:data:`PAGED`), given more rows than two pages that all share
  one time, written in an order that is not their ids', return each row
  exactly once across the pages, in the order of their ids; and of seven
  issues that cost the same, ``/stats`` keeps the first five by id.
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

_STOP = re.compile(r"\b(LIMIT|OFFSET|FOR|FETCH|UNION|RETURNING|WINDOW|HAVING|ORDER)\b", re.IGNORECASE)

#: A table a statement reads, and the name it calls it by: ``FROM jobs j``, ``JOIN traces AS t``.
_TABLE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)(?:\s+(?:AS\s+)?(?!(?:WHERE|ON|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|GROUP|"
    r"ORDER|LIMIT|OFFSET|USING|NATURAL|LATERAL|UNION|WINDOW|HAVING|FOR|RETURNING|SET|WITH)\b)([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
#: A column a statement fixes to one value: ``repo = $1``, ``j.status = 'open'``, ``owner IS NOT DISTINCT FROM $2``.
_FIXED = re.compile(
    r"(?<![\w.])(?:[A-Za-z_]\w*\.)?\"?([A-Za-z_]\w*)\"?\s*(?:=|IS\s+NOT\s+DISTINCT\s+FROM)\s*"
    r"(?:\$\d+|(?:lower|upper|trim)\(\s*\$\d+\s*\)|'[^']*'|\d+\b|TRUE\b|FALSE\b)",
    re.IGNORECASE,
)

#: Statements whose order is total though no key of a table shows it, by a fragment of their text, with why.
TOTAL_WITHOUT_A_KEY: dict[str, str] = {
    "\nORDER BY at, branch, key": "progression's events: each branch selects its row's own id as key, and the "
    "branches are numbered, so an event is its branch and its key",
    "ORDER BY at, fragment, label, url, value, report": "progression's facts carry no key: they are ordered by "
    "every column the caller reads, so two equal in all of them are the same fact to it (#443)",
    "ORDER BY repo, at, row_key": "an achievement fact's first merge of each repository: row_key is the job's id, "
    "or the applied proposal's",
    "UNION SELECT repo FROM watcher_stacks WHERE enabled": "the world's regions: one row per repository, a "
    "UNION of them, ordered by it",
    "WITH latest_per_repo AS": "the QA dashboard's repositories: one row per repository (its latest run, rn = 1), "
    "the repository last",
}


def sort_keys(sql: str) -> list[list[str]]:
    """The keys of each ``ORDER BY`` in ``sql``, each without its direction, in the order they appear; but an
    aggregate's ordered by the value it collects."""
    return [keys for at, keys in _lists(sql, r"\bORDER\s+BY\b") if keys != [_collected(sql, at)]]


def _lists(sql: str, keyword: str) -> list[tuple[int, list[str]]]:
    """Where each ``keyword`` clause of ``sql`` starts, and its expressions, each without a direction."""
    found = []
    for m in re.finditer(keyword, sql, re.IGNORECASE):
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
        found.append((m.start(), [_key(k) for k in keys]))
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


def tables(sql: str) -> dict[str, str]:
    """The tables ``sql`` reads, by every name it calls each: its own and its alias."""
    named: dict[str, str] = {}
    for m in _TABLE.finditer(sql):
        table = m.group(1).lower()
        named.setdefault(table, table)
        if m.group(2):
            named[m.group(2).lower()] = table
    return named


def normal(expression: str) -> str:
    """``expression`` as a key reads it: lower-cased, without quotes, spaces or the tables its columns name."""
    plain = expression.lower().replace('"', "")
    return re.sub(r"\s+", "", re.sub(r"\b[a-z_]\w*\.(?=[a-z_\"])", "", plain))


#: A table's keys: each set of expressions a unique index holds, every row distinct in them.
Keys = dict[str, list[frozenset[str]]]


def is_total(keys: list[str], sql: str, unique: Keys) -> bool:
    """Whether ordering ``sql``'s rows by ``keys`` leaves no two rows in no order: ``keys``, with the columns
    ``sql`` fixes to one value, hold a key of the table its last key is read from (any table it reads, for a key
    that names none), or every expression it groups by (its rows being its groups)."""
    held = {normal(k) for k in keys} | {normal(m.group(1)) for m in _FIXED.finditer(sql)}
    named = tables(sql)
    qualifier = re.match(r'^"?([A-Za-z_]\w*)"?\.', keys[-1])
    candidates = [named.get(qualifier.group(1).lower(), "")] if qualifier else sorted(set(named.values()))
    if any(key <= held for table in candidates for key in unique.get(table, [])):
        return True
    return any(group and {normal(g) for g in group} <= held for _at, group in _lists(sql, r"\bGROUP\s+BY\b"))


def not_ending_on_a_key(sql: str, unique: Keys) -> list[str]:
    """Each ``ORDER BY`` of ``sql`` that leaves rows in no order, as its keys."""
    return [", ".join(keys) for keys in sort_keys(sql) if not is_total(keys, sql, unique)]


#: The keys of a few tables, as :func:`unique_keys` reads them.
_UNIQUE: Keys = {
    "jobs": [frozenset({"job_id"})],
    "traces": [frozenset({"trace_id"})],
    "issues": [frozenset({"id"})],
    "credentials": [frozenset({"id"})],
    "hosts": [frozenset({"id"}), frozenset({"lower(name)"})],
    "slop_meter_weeks": [frozenset({"repo", "area", "week"})],
}

#: Orders that end on no key of their rows: a position, a select-list alias, an expression, a cast, an aggregate,
#: a quoted or parenthesised time, a day, a time and then a column some rows share, a cost, a time.
_NO_KEY = {
    "position": "SELECT created_at, job_id FROM jobs ORDER BY 1 DESC",
    "alias": "SELECT created_at AS ts FROM jobs ORDER BY ts DESC",
    "date_trunc": "SELECT * FROM jobs ORDER BY date_trunc('day', created_at)",
    "cast": "SELECT * FROM jobs ORDER BY created_at::date DESC",
    "aggregate": "SELECT repo, MAX(created_at) FROM jobs GROUP BY repo, status ORDER BY MAX(created_at) DESC",
    "time, then a shared column": "SELECT * FROM jobs ORDER BY created_at, status",
    "cost": "SELECT * FROM jobs ORDER BY total_cost_usd DESC LIMIT 5",
    "quoted": 'SELECT * FROM jobs ORDER BY "created_at" DESC',
    "a day column": "SELECT day, xp FROM progression_daily ORDER BY day",
    "parenthesised": "SELECT * FROM jobs ORDER BY (created_at) DESC",
    "nulls first": "SELECT * FROM jobs ORDER BY created_at DESC NULLS FIRST",
    "credentials by name": "SELECT * FROM credentials c ORDER BY c.provider, c.label",
    "part of a key": "SELECT * FROM slop_meter_weeks WHERE repo = $1 ORDER BY week",
    "a group, in part": "SELECT repo, status, COUNT(*) AS n FROM jobs GROUP BY repo, status ORDER BY n DESC, repo",
}

#: Orders that end on a key of their rows.
_KEYED = {
    "the row's id": "SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC LIMIT $1 OFFSET $2",
    "qualified": "SELECT * FROM traces t JOIN jobs j ON j.job_id = t.job_id ORDER BY t.created_at, t.trace_id",
    "quoted": 'SELECT * FROM jobs ORDER BY created_at, "job_id"',
    "a window": "SELECT ROW_NUMBER() OVER (PARTITION BY repo ORDER BY started_at DESC, job_id DESC) FROM jobs",
    "a subquery": "SELECT (SELECT t.agent_type FROM traces t ORDER BY t.created_at DESC, t.trace_id DESC LIMIT 1) FROM jobs",
    "the group's key": "SELECT agent_type, SUM(cost_usd) AS c FROM traces GROUP BY agent_type ORDER BY c DESC, agent_type",
    "an aggregate of its own key": "SELECT ARRAY_AGG(created_at ORDER BY created_at) FROM jobs",
    "a key with the columns fixed": "SELECT * FROM slop_meter_weeks WHERE repo = $1 AND area = $2 ORDER BY week",
    "a unique expression": "SELECT * FROM hosts h ORDER BY lower(h.name)",
}


@pytest.mark.parametrize("form", sorted(_NO_KEY))
def test_an_order_that_does_not_end_on_a_key_of_its_rows_is_found(form: str) -> None:
    assert not_ending_on_a_key(_NO_KEY[form], _UNIQUE), f"{form}: {_NO_KEY[form]}"


@pytest.mark.parametrize("form", sorted(_KEYED))
def test_an_order_that_ends_on_a_key_of_its_rows_passes(form: str) -> None:
    assert not_ending_on_a_key(_KEYED[form], _UNIQUE) == [], f"{form}: {_KEYED[form]}"


async def unique_keys(db: DatabasePool) -> Keys:
    """Every table's keys: its primary key and each unique index, as the expressions they hold. Not a partial index,
    which leaves the rows outside it free, nor one over a column that may be null, unless it counts nulls as equal."""
    rows = await db.fetch(
        """
        SELECT c.relname AS tbl,
               array_agg(pg_get_indexdef(i.indexrelid, k, true) ORDER BY k) AS exprs,
               bool_and(a.attnotnull IS NOT FALSE) OR i.indnullsnotdistinct AS keyed
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        CROSS JOIN LATERAL generate_series(1, i.indnkeyatts) AS k
        LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k - 1] AND i.indkey[k - 1] > 0
        WHERE i.indisunique AND i.indpred IS NULL AND c.relnamespace = 'public'::regnamespace
        GROUP BY c.relname, i.indexrelid, i.indnullsnotdistinct
        """
    )
    found: Keys = {}
    for row in rows:
        if row["keyed"]:
            found.setdefault(str(row["tbl"]), []).append(frozenset(normal(e) for e in row["exprs"]))
    return found


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


async def test_no_get_route_reads_rows_in_an_order_that_does_not_end_on_a_key(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    unique = await unique_keys(db_with_migrations)
    assert frozenset({"job_id"}) in unique["jobs"] and frozenset({"lower(name)"}) in unique["hosts"], unique
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
                    if any(fragment in sql for fragment in TOTAL_WITHOUT_A_KEY):
                        continue
                    for keys in not_ending_on_a_key(sql, unique):
                        found.setdefault(" ".join(sql.split())[:110] + f" … ORDER BY {keys}", set()).add(path)
    assert read > 500, f"the walk sent {read} statements: it measures nothing"
    assert not found, "orders that do not end on a key of their rows, with the routes that read them:\n" + "\n".join(
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


async def test_the_costliest_issues_of_one_cost_are_cut_in_the_order_of_their_ids(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/stats`` keeps the five costliest issues: of seven that cost the same, the five first by id, every time."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch, (COVERED,)) as (app, keys, _values):
        for issue in _IDS[:7]:
            await db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, 't', $2)", issue, COVERED)
            await db.execute(
                "INSERT INTO jobs (job_id, repo, task, status, issue_id, total_cost_usd) "
                "VALUES ($1, $2, 't', 'completed', $3, 1000)",
                f"job-{issue}",
                COVERED,
                issue,
            )
        await db.execute("ANALYZE")
        response = await call(app, "GET", "/api/v1/stats", keys.open)
        assert response.status_code == 200, response.text
        top = [row["trace_id"] for row in response.json()["top_expensive_issues"]]
    assert top == sorted(_IDS[:7])[:5], f"the five of seven issues of one cost, by id: {top}"
