"""Every quest to review, however many wait, on the real app over a real database (#296).

The decisions the ask loop and the feed read (``open_decisions``) took the
quests to review from the world inbox's newest 30 finished jobs, so an older
one read as answered and its ask was withdrawn. They now read every one, and
build each one's card from batched reads: one statement for the jobs, one for
their merge approvals and one for their verifications, however many wait. The
newest 30 read their pull request's numbers from the forge, as many as before;
an older one's card gives the number from its address.

The app, its keys and its two repositories are
:mod:`tests.fixtures.repository_writes`'. The forge is a stand-in that counts
the pull requests read; nothing reaches the network.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest

from embry0.api.v1 import organizations as organizations_api
from embry0.api.v1.asks import open_decisions
from embry0.services.ask_loop import AskLoop
from embry0.settings.sections.asks import ASKS
from embry0.settings.store import scoped_reader
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, UNCOVERED, call, repository_writes_app
from tests.fixtures.statements import count_statements, counted

pytestmark = pytest.mark.requires_postgres

#: How many quests to review read their pull request from the forge: the inbox's page.
PR_READS = 30
_PULL = re.compile(r"/repos/(?P<repo>[^/]+/[^/]+)/pulls/(?P<number>\d+)$")


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _Forge:
    """The organizations' forge: a pull request's numbers from its number, and no file of any repository."""

    def __init__(self) -> None:
        self.pulls: list[str] = []

    async def client(self, _request: Any, _org: Any) -> _Forge:
        return self

    async def get(self, path: str, repo: str | None = None) -> _Response:
        match = _PULL.search(path)
        if match is None:
            return _Response(404, {})
        self.pulls.append(f"{match['repo']}#{match['number']}")
        number = int(match["number"])
        return _Response(200, {"state": "open", "changed_files": 3, "additions": number % 97, "deletions": 2})


def _forge(monkeypatch: pytest.MonkeyPatch) -> _Forge:
    forge = _Forge()
    monkeypatch.setattr(organizations_api, "org_forge_client", forge.client)
    return forge


def _job_id(repo: str, i: int) -> str:
    return f"job-many-{repo.split('/')[0]}-{i:05d}"


async def _reviews(db: DatabasePool, repo: str, first: int, stop: int, *, newest_minutes_ago: float = 10) -> None:
    """Finished jobs ``first`` to ``stop - 1`` in ``repo``, each with a pull request, none rated: quests to review.

    The higher ``i``, the older: job ``i`` finished ``newest_minutes_ago + i`` minutes ago, in one statement.
    """
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
        "pr_url, delivery_state, dispatched_by, total_cost_usd, result_summary) "
        "SELECT 'job-many-' || split_part($1, '/', 1) || '-' || lpad(i::text, 5, '0'), $1, 'Fix case ' || i, "
        "'completed', 'full-magnum-opus', $2::timestamptz - make_interval(mins => i + 5), $2::timestamptz - make_interval(mins => i + 5), "
        "$2::timestamptz - make_interval(mins => i), 'https://github.com/' || $1 || '/pull/' || (1000 + i), 'pr_open', "
        "'ada@example.com', 1, 'Report ' || i "
        "FROM generate_series($3::int, $4::int) AS i",
        repo,
        NOW - timedelta(minutes=newest_minutes_ago),
        first,
        stop - 1,
    )


def _many(decisions: list[dict[str, Any]]) -> list[str]:
    return [str(d["id"]) for d in decisions if str(d["id"]).startswith("done:job-many-")]


async def test_every_review_is_read_in_the_same_statements_at_30_300_and_3000(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a tick reads: every quest to review, their cards from one statement of each kind, and the newest 30's
    pull requests from the forge, at 30, 300 and 3,000 open quests to review."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        forge = _forge(monkeypatch)
        count_statements(monkeypatch)
        await open_decisions(app)  # the settings, the areas and the owners read once, as every tick after the first
        measured: dict[int, tuple[list[str], int]] = {}
        done = 0
        for total in (30, 300, 3_000):
            await _reviews(db, COVERED, done, total)
            done = total
            forge.pulls.clear()
            decisions, statements = await counted(open_decisions(app))
            assert len(_many(decisions)) == total, (
                f"every one of the {total} quests to review is read, not the inbox's newest 30: {len(_many(decisions))}"
            )
            measured[total] = (statements.texts, len(forge.pulls))

    counts = {total: len(texts) for total, (texts, _pulls) in measured.items()}
    assert counts[300] == counts[30] and counts[3_000] == counts[30], (
        f"reading 300 or 3,000 quests to review takes as many statements as reading 30: {counts}"
    )
    texts = measured[3_000][0]
    one_each = {
        "the inbox": sum("FROM job_ratings r WHERE r.job_id = j.job_id" in t for t in texts),
        "the jobs": sum("left(result_summary" in t for t in texts),
        "their merge approvals": sum(t.startswith("SELECT DISTINCT ON (job_id) job_id, id, status") for t in texts),
        "their verifications": sum("FROM job_verifications WHERE job_id = ANY" in t for t in texts),
        "their briefs": sum("FROM job_briefs b JOIN jobs j" in t for t in texts),
        "the organization of the newest 30's pull requests": sum(
            "FROM organizations WHERE owner = $1" in t for t in texts
        ),
    }
    assert set(one_each.values()) == {1}, (
        f"the quests to review cost one statement of each kind for every card, the 30 pull requests read from the "
        f"forge one organization read for their one owner: {one_each}"
    )
    by_job = [t for t in texts if re.search(r"WHERE (job_id|j\.job_id) = \$1\b", t)]
    assert not by_job, f"no statement reads one job's row: {by_job[:3]}"
    pulls = {total: n for total, (_texts, n) in measured.items()}
    assert pulls == {30: PR_READS, 300: PR_READS, 3_000: PR_READS}, (
        f"the newest 30 read their pull request from the forge, however many wait: {pulls}"
    )


async def test_the_newest_30_read_their_pull_request_and_an_older_one_shows_its_number(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        forge = _forge(monkeypatch)
        await _reviews(db, COVERED, 0, 40)
        resp = await call(app, "GET", f"/api/v1/decisions?stage=done&repo={COVERED}&limit=500", keys.open)
    assert resp.status_code == 200, resp.text
    cards = {d["id"]: d for d in resp.json()["decisions"]}
    many = [f"done:{_job_id(COVERED, i)}" for i in range(40)]
    assert sorted(c for c in cards if c in many) == many, (
        f"all 40 quests to review are listed, the 10 past the inbox's 30 too: {len([c for c in cards if c in many])}"
    )
    pr = {i: cards[f"done:{_job_id(COVERED, i)}"]["facts"][0] for i in range(40)}
    expected = {
        i: {"label": "Pull request", "value": f"#{1000 + i} · +{(1000 + i) % 97} −2 · 3 files", "tone": ""}
        if i < PR_READS
        else {"label": "Pull request", "value": f"#{1000 + i}", "tone": ""}
        for i in range(40)
    }
    assert pr == expected, "the newest 30 show their pull request's numbers, an older one its number alone"
    assert sorted(forge.pulls) == sorted(f"{COVERED}#{1000 + i}" for i in range(PR_READS)), (
        f"only the newest 30 pull requests were read from the forge: {len(forge.pulls)}"
    )


async def _detailed_reviews(db: DatabasePool, count: int) -> None:
    """``count`` quests to review in the covered repository, each with its own report, done-when, nature, cost and
    time, and merge approvals: job ``i`` finished ``i`` minutes after the one before it, the oldest first."""
    for i in range(count):
        job, finished = _job_id(COVERED, i), NOW - timedelta(minutes=10 + i)
        done_when = {"report": {"items": [{"met": True}, {"met": i % 4 == 0}]}} if i % 2 == 0 else None
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
            "pr_url, delivery_state, dispatched_by, total_cost_usd, result_summary, done_when, work_nature) "
            "VALUES ($1, $2, $3, 'completed', 'full-magnum-opus', $4, $5, $6, $7, 'pr_open', 'ada@example.com', "
            "$8, $9, $10::jsonb, $11::jsonb)",
            job,
            COVERED,
            f"Fix case {i}",
            finished - timedelta(hours=1),
            finished - timedelta(minutes=i + 1),
            finished,
            f"https://github.com/{COVERED}/pull/{1000 + i}",
            i + 0.25,
            f"Report {i}." + ("x" * 9_000 if i == count - 2 else ""),
            done_when,
            {"nature": "slop"} if i % 5 == 0 else None,
        )
        approvals: list[tuple[str, str, dict[str, Any], timedelta]] = []
        if i % 3 == 0:
            # Two: the newest is the one the card reads.
            approvals = [
                ("old", "pending", {"checks_state": "failure"}, timedelta(minutes=2)),
                ("new", "expired", {"checks_state": "success", "recommendation": {"verdict": "merge"}}, timedelta()),
            ]
        elif i % 3 == 1:
            approvals = [("only", "pending", {"checks_state": "pending"}, timedelta())]
        for tag, status, ask, before in approvals:
            await db.execute(
                "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, job_id, status, ask, created_at) "
                "VALUES ($1, $2, $3, $4, 'abc', $5, $6, $7::jsonb, $8)",
                f"ma-{tag}-{i}",
                COVERED,
                1000 + i,
                f"https://github.com/{COVERED}/pull/{1000 + i}",
                job,
                status,
                ask,
                finished - before,
            )


def _expected_card(i: int, count: int) -> dict[str, Any]:
    """Job ``i``'s card, from what :func:`_detailed_reviews` gave it."""
    pr = f"#{1000 + i} · +{(1000 + i) % 97} −2 · 3 files" if i < PR_READS else f"#{1000 + i}"
    facts = [{"label": "Pull request", "value": pr, "tone": ""}]
    if i % 3 == 0:
        facts.append({"label": "Checks", "value": "passing", "tone": "good"})
    elif i % 3 == 1:
        facts.append({"label": "Checks", "value": "running", "tone": "warn"})
    if i % 2 == 0:
        met = i % 4 == 0
        facts.append({"label": "Done-when", "value": f"{2 if met else 1}/2 met", "tone": "good" if met else "warn"})
    facts.append({"label": "Spent", "value": f"${i + 0.25:,.2f} · {i + 1}m", "tone": ""})
    if i % 5 == 0:
        facts.append({"label": "Nature", "value": "slop cleanup", "tone": ""})
    ready = i % 3 == 0 and (i % 2 == 1 or i % 4 == 0)
    links = {"job": _job_id(COVERED, i), "pr": f"https://github.com/{COVERED}/pull/{1000 + i}"}
    if i % 3 == 1:
        links["merge_approval"] = f"ma-only-{i}"
    report = f"Report {i}." + ("x" * 9_000 if i == count - 2 else "")
    return {
        "facts": facts,
        "verdict": {"label": "Ready to merge", "tone": "good"} if ready else {"label": "Needs a look", "tone": "warn"},
        "links": links,
        "details": report[:8_000],
    }


async def test_each_review_s_card_reads_its_own_job_merge_approval_and_report(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batched reads give each card its own rows: the job's report and facts, and its newest merge approval,
    past the inbox's 30 too."""
    count = 36
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        _forge(monkeypatch)
        await _detailed_reviews(db, count)
        resp = await call(app, "GET", f"/api/v1/decisions?stage=done&repo={COVERED}&limit=500", keys.open)
    assert resp.status_code == 200, resp.text
    cards = {d["id"]: d for d in resp.json()["decisions"]}
    got = {
        i: {k: cards[f"done:{_job_id(COVERED, i)}"][k] for k in ("facts", "verdict", "links", "details")}
        for i in range(count)
        if f"done:{_job_id(COVERED, i)}" in cards
    }
    assert len(got) == count, f"all {count} quests to review are listed, past the inbox's 30 too: {sorted(got)}"
    wrong = {i: (got[i], _expected_card(i, count)) for i in range(count) if got[i] != _expected_card(i, count)}
    assert not wrong, f"each card shows its own job's rows and its newest merge approval: {wrong}"


async def test_a_key_with_an_allowlist_reads_every_review_of_its_repositories_and_none_of_another(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#297's narrowing holds on the uncut read: the inbox is read narrowed to the key's repositories in SQL, and
    the key is shown every quest to review there, however many newer ones another repository has."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        _forge(monkeypatch)
        count_statements(monkeypatch)
        await _reviews(db, COVERED, 0, 40, newest_minutes_ago=120)
        await _reviews(db, UNCOVERED, 0, 40, newest_minutes_ago=5)
        limited, statements = await counted(call(app, "GET", "/api/v1/decisions?stage=done&limit=500", keys.limited))
        everyone = await call(app, "GET", "/api/v1/decisions?stage=done&limit=500", keys.open)
    assert limited.status_code == 200 and everyone.status_code == 200, (limited.text, everyone.text)
    shown = {d["id"]: d["repo"] for d in limited.json()["decisions"]}
    own = {f"done:{_job_id(COVERED, i)}" for i in range(40)} | {f"done:{values[COVERED]['job_id']}"}
    assert {d: r for d, r in shown.items() if r != COVERED} == {}, (
        f"the limited key is shown no quest to review of a repository it does not cover: {shown}"
    )
    assert set(shown) == own, (
        f"the limited key is shown every one of its repository's 41 quests to review, though 40 newer ones wait "
        f"elsewhere: {len(set(shown) & own)} of {len(own)}"
    )
    inbox = [i for i in statements.naming("FROM job_ratings r WHERE r.job_id = j.job_id")]
    assert [statements.arguments[i][3] for i in inbox] == [[COVERED]], (
        f"the inbox is read once, narrowed in SQL to the repositories the key covers: "
        f"{[statements.arguments[i] for i in inbox]}"
    )
    open_shown = {d["id"] for d in everyone.json()["decisions"]}
    assert len(open_shown) == 82, (
        f"a key without a list reads both repositories' 82 quests to review: {len(open_shown)}"
    )


async def test_the_loop_and_the_feed_read_no_report_and_the_feed_s_page_carries_its_own(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisions the ask loop and the feed read carry no report: neither shows a card, and a report is up to
    8,000 characters a card. The feed reads the reports of the asks it hands out, so each item's decision is the one
    ``GET /decisions/{id}`` shows."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        _forge(monkeypatch)
        count_statements(monkeypatch)
        await _reviews(db, COVERED, 0, 40)
        await db.execute("UPDATE jobs SET result_summary = result_summary || ' ' || repeat('z', 9000)")
        decisions, read = await counted(open_decisions(app))
        put = await call(
            app,
            "PUT",
            "/api/v1/config/settings",
            MASTER,
            "asks:\n  enabled: true\n  hours: 00-24\n  weekdays: mon-sun\n",
        )
        assert put.status_code == 200, put.text

        async def people() -> dict[str, str]:
            return {}

        loop = AskLoop(
            settings=scoped_reader(app.state.settings, ASKS),
            asks=app.state.asks_repo,
            decisions=lambda: open_decisions(app),
            people=people,
            clock=lambda: NOW,
        )
        await loop.tick()
        feed, fed = await counted(call(app, "GET", "/api/v1/asks/due?peek=1", keys.open))
        assert feed.status_code == 200, feed.text
        handed = [a["decision"] for a in feed.json()["asks"] if a["decision"]["stage"] == "done"]
        shown = {
            d["id"]: (await call(app, "GET", f"/api/v1/decisions/{d['id']}", keys.open)).json()["details"]
            for d in handed
        }

    asked = {i: read.arguments[i][1] for i in read.naming("left(result_summary")}
    assert list(asked.values()) == [0], f"the decisions the loop and the feed read ask for no report: {asked}"
    carried = sum(len(d["details"]) for d in decisions if d["stage"] == "done")
    assert carried == 0, f"so none of their quests to review carries one: {carried} characters"
    reads = sorted((fed.arguments[i][1], len(fed.arguments[i][0])) for i in fed.naming("left(result_summary"))
    assert handed and reads == [(0, 42), (8_000, len(handed))], (
        f"the feed reads every quest to review without its report, then the reports of the {len(handed)} it hands "
        f"out: (characters, jobs) {reads}"
    )
    wrong = {d["id"]: (len(d["details"]), len(shown[d["id"]])) for d in handed if d["details"] != shown[d["id"]]}
    long = [len(r) for i, r in shown.items() if i.startswith("done:job-many-")]
    assert not wrong and long and set(long) == {8_000}, (
        f"each quest to review the feed hands out carries the report GET /decisions/{{id}} shows: {wrong}"
    )
