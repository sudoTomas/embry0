"""The Slop-O-Meter's loop, mirror, events and judgments on fakes (spec 2026-10-26, slop-o-meter): no git, no network."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from embry0.services.slop_meter import loop as loop_mod
from embry0.services.slop_meter.context import job_meter, meter_sentence
from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader
from embry0.services.slop_meter.history import HISTORY_VERSION, Commit
from embry0.services.slop_meter.judge import parse_judgment, user_prompt
from embry0.services.slop_meter.loop import SlopMeterLoop, SlopMeterSettings
from embry0.services.slop_meter.mirror import GitError, GitMirror, auth_env
from embry0.services.slop_meter.score import Outlier

START = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
REPO = "acme/web"


def _record(sha: str, at: datetime, files: list[tuple[str, int, int]], email: str = "dev@example.com") -> str:
    numstat = "\n".join(f"{a}\t{d}\t{p}" for p, a, d in files)
    return f"\x1e{sha}\x1f{at.isoformat()}\x1fDev\x1f{email}\x1fWork\n\x1f\n{numstat}\n"


class FakeMirror:
    def __init__(self, records: dict[str, str], order: list[str], files: dict[str, str] | None = None) -> None:
        self.records, self.order, self.files = records, order, files or {}
        self.synced: list[tuple[str, str]] = []
        self.logged: list[list[str]] = []

    async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
        self.synced.append((repo, forge))

    async def history(self, repo: str) -> list[str]:
        return list(self.order)

    async def log(self, repo: str, shas: list[str]) -> str:
        self.logged.append(list(shas))
        return "".join(self.records[s] for s in shas)

    async def read(self, repo: str, path: str) -> str | None:
        return self.files.get(path)


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.event_rows: dict[str, dict[str, dict[str, Any]]] = {}
        self.login_map: dict[str, str] = {}
        self.judged: dict[tuple[str, str], bool] = {}
        self.stored: dict[str, Any] = {}

    async def state(self, repo: str) -> dict[str, Any]:
        return dict(self.states.get(repo, {"repo": repo, "commits_read": 0}))

    async def set_state(self, repo: str, **fields: Any) -> None:
        self.states.setdefault(repo, {"repo": repo, "commits_read": 0}).update(fields)

    async def add_commits(self, repo: str, first_seq: int, rows: list[dict[str, Any]]) -> None:
        self.rows.setdefault(repo, []).extend(rows)

    async def commits(self, repo: str) -> list[dict[str, Any]]:
        return list(self.rows.get(repo, []))

    async def authors(self, repo: str) -> list[str]:
        return sorted({r["author_email"] for r in self.rows.get(repo, [])})

    async def forget_history(self, repo: str) -> None:
        self.rows[repo] = []
        await self.set_state(repo, commits_read=0, head_sha=None)

    async def add_events(self, repo: str, events: list[tuple[str, str, str, datetime]]) -> None:
        for eid, kind, login, at in events:
            self.event_rows.setdefault(repo, {}).setdefault(eid, {"kind": kind, "login": login, "at": at})

    async def events(self, repo: str) -> list[dict[str, Any]]:
        return list(self.event_rows.get(repo, {}).values())

    async def logins(self) -> dict[str, str]:
        return dict(self.login_map)

    async def set_login(self, login: str, email: str) -> None:
        self.login_map[login.lower()] = email

    async def judgments(self, repo: str) -> dict[str, bool]:
        return {sha: v for (r, sha), v in self.judged.items() if r == repo}

    async def judge(self, repo: str, sha: str, *, slop: bool, reason: str, model: str) -> None:
        self.judged[(repo, sha)] = slop

    async def store(self, repo: str, results: dict[str, Any], computed_at: datetime) -> None:
        self.stored[repo] = results


def _history(n: int) -> tuple[dict[str, str], list[str]]:
    order = [f"c{i:03d}" for i in range(n)]
    return {s: _record(s, START + timedelta(hours=3 * i), [("src/a.py", 20, 0)]) for i, s in enumerate(order)}, order


def _loop(
    mirror: FakeMirror, store: FakeStore, *, client: Any = None, per_tick: int = 3, model: str = ""
) -> SlopMeterLoop:
    async def repos() -> list[str]:
        return [REPO]

    async def token_for(repo: str) -> tuple[str, str, str] | None:
        return ("github", "secret-token", "")

    async def client_for(repo: str) -> Any:
        return client

    async def model_fn() -> str:
        return model

    return SlopMeterLoop(
        repos=repos,
        store=store,
        mirror=mirror,
        token_for=token_for,
        client_for=client_for,
        model=model_fn,
        resolver=None,
        settings=SlopMeterSettings(commits_per_tick=per_tick),
        clock=lambda: START + timedelta(days=60),
    )


@pytest.mark.asyncio
async def test_a_history_is_read_in_bounded_batches_and_scored_once_caught_up() -> None:
    records, order = _history(7)
    mirror, store = FakeMirror(records, order), FakeStore()
    loop = _loop(mirror, store, per_tick=3)
    assert await loop.tick() is True and REPO not in store.stored, "a backlog is not scored yet"
    assert await loop.tick() is True
    assert await loop.tick() is False, "the last batch reaches the head"
    assert mirror.logged == [order[0:3], order[3:6], order[6:7]]
    assert store.states[REPO]["commits_read"] == 7 and store.states[REPO]["head_sha"] == "c006"
    assert set(store.stored[REPO]) == {""} and store.states[REPO]["error"] is None
    # Nothing new: no read, one more scoring.
    assert await loop.tick() is False and len(mirror.logged) == 3


@pytest.mark.asyncio
async def test_a_rewritten_history_is_read_again_from_the_start() -> None:
    records, order = _history(4)
    mirror, store = FakeMirror(records, order), FakeStore()
    loop = _loop(mirror, store, per_tick=10)
    await loop.tick()
    new_records, new_order = _history(3)
    new_order = ["x" + s for s in new_order]
    mirror.records = {"x" + s: r.replace(s, "x" + s, 1) for s, r in new_records.items()}
    mirror.order = new_order
    await loop.tick()
    assert [r["sha"] for r in store.rows[REPO]] == new_order


@pytest.mark.asyncio
async def test_a_history_read_the_older_way_is_read_again() -> None:
    """A repository read before renames were found (version 1) is read again from the start, once."""
    records, order = _history(4)
    mirror, store = FakeMirror(records, order), FakeStore()
    loop = _loop(mirror, store, per_tick=10)
    await loop.tick()
    assert store.states[REPO]["history_version"] == HISTORY_VERSION
    store.states[REPO]["history_version"] = 1
    await loop.tick()
    assert mirror.logged == [order, order], "the whole history was read again"
    assert [r["sha"] for r in store.rows[REPO]] == order and store.states[REPO]["history_version"] == HISTORY_VERSION
    await loop.tick()
    assert len(mirror.logged) == 2, "and only once"


@pytest.mark.asyncio
async def test_a_failing_repository_records_its_error_and_the_rest_go_on() -> None:
    class Broken(FakeMirror):
        async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
            raise GitError("git clone exited 128: repository not found")

    store = FakeStore()
    assert await _loop(Broken({}, []), store).tick() is False
    assert "repository not found" in store.states[REPO]["error"]


@pytest.mark.asyncio
async def test_an_outlier_is_judged_by_the_model_and_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    records, order = _history(40)
    big = START + timedelta(days=200)
    records["big"] = _record("big", big, [("src/drop.py", 9_000, 0)])
    order.append("big")
    store = FakeStore()
    asked: list[str] = []

    async def endpoint(model: str, resolver: Any) -> object:
        return object()

    async def judge(repo: str, outlier: Outlier, ep: Any, client: Any) -> tuple[bool, str]:
        asked.append(outlier.commit.sha)
        return False, "a vendored parser"

    monkeypatch.setattr("embry0.services.proposal_briefs.resolve_endpoint", endpoint)
    monkeypatch.setattr("embry0.services.slop_meter.judge.judge", judge)
    loop = _loop(FakeMirror(records, order), store, per_tick=100, model="custom/local")
    await loop.tick()
    assert asked == ["big"] and store.judged[(REPO, "big")] is False
    assert store.stored[REPO][""].provisional is False
    await loop.tick()
    assert asked == ["big"], "a judgment is never asked again"


@pytest.mark.asyncio
async def test_without_a_model_an_outlier_leaves_the_score_provisional() -> None:
    records, order = _history(40)
    records["big"] = _record("big", START + timedelta(days=200), [("src/drop.py", 9_000, 0)])
    order.append("big")
    store = FakeStore()
    await _loop(FakeMirror(records, order), store, per_tick=100).tick()
    assert store.stored[REPO][""].provisional is True


@pytest.mark.asyncio
async def test_areas_and_ignore_lists_come_from_the_head() -> None:
    order = ["a", "b"]
    records = {
        "a": _record("a", START, [("api/x.py", 50, 0), ("web/dist/app.js", 900, 0)]),
        "b": _record("b", START + timedelta(hours=5), [("web/y.ts", 30, 0)]),
    }
    files = {
        ".prettierignore": "dist/\n",
        ".embry0/areas.yml": "areas:\n  - name: Api\n    paths: [api/**]\n  - name: Web\n    paths: [web/**]\n",
    }
    store = FakeStore()
    await _loop(FakeMirror(records, order, files), store, per_tick=10).tick()
    results = store.stored[REPO]
    assert set(results) == {"", "Api", "Web"}
    assert results["Web"].weeks[-1].lines == 30, "the ignored build output weighs nothing"


class FakeResponse:
    def __init__(self, status: int, data: Any, *, next_url: str | None = None, remaining: int | None = 4_000) -> None:
        self.status_code, self._data = status, data
        self.links = {"next": {"url": next_url}} if next_url else {}
        self.headers = {"x-ratelimit-remaining": str(remaining)} if remaining is not None else {}

    def json(self) -> Any:
        return self._data


def _comment(i: int, login: str, day: int) -> dict[str, Any]:
    stamp = f"2025-02-{day:02d}T10:00:00Z"
    return {"id": i, "user": {"login": login}, "created_at": stamp, "updated_at": stamp}


class FakeForge:
    """GitHub's listings for one repository, paged by hand; every call is recorded."""

    def __init__(self, *, comment_pages: int = 1, remaining: int = 4_000, repo: str = REPO) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.comment_pages = comment_pages
        self.remaining = remaining
        self.repo = repo

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append((path, dict(params or {})))
        r = self.repo
        if path == f"/repos/{r}/issues/comments" or path.startswith("page:"):
            page = int(path.split(":")[1]) if path.startswith("page:") else 1
            nxt = f"page:{page + 1}" if page < self.comment_pages else None
            items = [_comment(page, "ana", min(page, 28))]
            if page == 1:
                items.append(_comment(1000, "renovate[bot]", 2))
            return FakeResponse(200, items, next_url=nxt, remaining=self.remaining)
        if path == f"/repos/{r}/pulls/comments":
            return FakeResponse(200, [], remaining=self.remaining)
        if path == f"/repos/{r}/pulls":
            pulls = [
                {"number": 7, "updated_at": "2025-02-05T00:00:00Z"},
                {"number": 6, "updated_at": "2025-01-01T00:00:00Z"},
            ]
            return FakeResponse(200, pulls, remaining=self.remaining)
        if path.endswith("/reviews"):
            return FakeResponse(200, [{"id": 99, "user": {"login": "bo"}, "submitted_at": "2025-02-04T12:00:00Z"}])
        if path == f"/repos/{r}/commits":
            email = "bo@example.com" if (params or {}).get("author") == "bo" else None
            return FakeResponse(200, [{"commit": {"author": {"email": email}}}] if email else [])
        return FakeResponse(404, {})


async def _read_all(reader: EventReader, budget: ApiBudget) -> None:
    while await reader.step(budget):
        pass


@pytest.mark.asyncio
async def test_forge_events_are_read_a_call_at_a_time_from_their_cursors() -> None:
    forge = FakeForge()
    since = datetime(2025, 1, 15, tzinfo=UTC)
    reader = EventReader(
        forge,
        REPO,
        comments_since=since,
        review_comments_since=since,
        reviews_since=since,
        max_pages=2,
        max_pulls=10,
        max_logins=5,
    )
    budget = ApiBudget(100, 2_000)
    await _read_all(reader, budget)
    assert forge.calls[0] == (
        f"/repos/{REPO}/issues/comments",
        {"sort": "updated", "direction": "asc", "since": "2025-01-15T00:00:00Z", "per_page": 100},
    )
    batch = reader.batch
    assert {e[0] for e in batch.events} == {"ic:1", "ic:1000", "rv:99"}, "only pull request 7 was updated since"
    assert batch.comments_since == datetime(2025, 2, 2, 10, tzinfo=UTC) and batch.more is False
    assert batch.review_comments_since == since, "an empty listing keeps its cursor"
    assert batch.reviews_since == datetime(2025, 2, 5, tzinfo=UTC)
    assert batch.logins == {"ana": "", "bo": "bo@example.com"}, "bots are never looked up"
    assert budget.made == len(forge.calls) == 6


@pytest.mark.asyncio
async def test_a_listing_cut_by_the_budget_resumes_after_its_last_page() -> None:
    forge = FakeForge(comment_pages=5)
    reader = EventReader(
        forge, REPO, comments_since=None, review_comments_since=None, reviews_since=None, max_pages=10, max_pulls=10
    )
    budget = ApiBudget(3, 0)
    with pytest.raises(BudgetSpent):
        await _read_all(reader, budget)
    assert not reader.done and budget.made == 3
    assert {e[0] for e in reader.batch.events} >= {"ic:1", "ic:2", "ic:3"}
    assert reader.batch.comments_since == datetime(2025, 2, 3, 10, tzinfo=UTC), "read oldest first, up to page 3"
    assert reader.batch.review_comments_since is None, "a listing not started keeps its cursor"


@pytest.mark.asyncio
async def test_the_rate_reserve_stops_the_ticks_forge_calls() -> None:
    forge = FakeForge(comment_pages=5, remaining=1_500)
    reader = EventReader(
        forge, REPO, comments_since=None, review_comments_since=None, reviews_since=None, max_pages=10, max_pulls=10
    )
    budget = ApiBudget(100, 2_000)
    with pytest.raises(BudgetSpent):
        await _read_all(reader, budget)
    assert budget.made == 1 and budget.remaining == 1_500 and budget.spent, "one call, then the reserve is reached"


def test_the_scope_is_the_maps_repositories_plus_and_minus_the_operators_lists() -> None:
    shown = ["acme/web", "Acme/API", "acme/old"]
    assert loop_mod.scope(shown) == ["Acme/API", "acme/old", "acme/web"]
    assert loop_mod.scope(shown, include="acme/extra, acme/web", exclude="ACME/OLD,") == [
        "Acme/API",
        "acme/extra",
        "acme/web",
    ]


@pytest.mark.asyncio
async def test_a_large_backlog_shares_the_budget_round_robin() -> None:
    """Two repositories: one with a long comment history. Each gets a call in turn; neither starves."""
    big, small = "acme/big", "acme/small"
    forges = {big: FakeForge(comment_pages=50, repo=big), small: FakeForge(repo=small)}
    records, order = _history(2)
    store = FakeStore()

    class Mirrors(FakeMirror):
        async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
            self.synced.append((repo, forge))

    mirror = Mirrors(records, order)

    async def repos() -> list[str]:
        return [big, small]

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", "t", "")

    async def client_for(repo: str) -> Any:
        return forges[repo]

    async def no_model() -> str:
        return ""

    loop = SlopMeterLoop(
        repos=repos,
        store=store,
        mirror=mirror,
        token_for=token_for,
        client_for=client_for,
        model=no_model,
        resolver=None,
        settings=SlopMeterSettings(commits_per_tick=10, event_pages=100, api_calls_per_tick=16, rate_reserve=0),
        clock=lambda: START + timedelta(days=60),
    )
    assert await loop.tick() is True, "the big repository still has comments to read"
    # The small one needs 7 calls (2 listings, the pulls, 2 reviews, 2 logins); turns alternate,
    # so it finishes on the 14th call and the big one has the last 2 to itself.
    assert len(forges[small].calls) == 7, "the small repository read everything it had"
    assert len(forges[big].calls) == 9, "the big one had its turns and the rest of the budget"
    assert small in store.stored and big not in store.stored, "only a caught-up repository is scored"
    assert len(mirror.synced) == 2, "git fetches are not API calls and are not budgeted"
    assert store.states[big]["comments_since"] == datetime(2025, 2, 9, 10, tzinfo=UTC), "it resumes after page 9"
    # The next tick starts with the other repository, and the big one goes on from its cursor.
    forges[big].calls.clear()
    await loop.tick()
    assert forges[big].calls[0][1]["since"] == "2025-02-09T10:00:00Z"


@pytest.mark.asyncio
async def test_the_loop_maps_the_logins_its_events_name() -> None:
    records, order = _history(2)
    store = FakeStore()
    await _loop(FakeMirror(records, order), store, client=FakeForge(), per_tick=10).tick()
    assert store.login_map == {"ana": "", "bo": "bo@example.com"}, "bots are never looked up"
    assert store.states[REPO]["comments_since"] == datetime(2025, 2, 2, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_the_mirror_clones_then_fetches_and_keeps_the_token_out_of_its_arguments(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, str], bytes | None]] = []

    async def run(args: Any, env: dict[str, str], stdin: bytes | None, timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((list(args), env, stdin))
        if args[0] == "clone":
            (Path(args[-1])).mkdir(parents=True)
            (Path(args[-1]) / "HEAD").write_text("ref: refs/heads/main\n")
        return 0, b"", b""

    mirror = GitMirror(tmp_path, run=run)
    await mirror.sync(REPO, forge="github", token="s3cret", gitlab_url="")
    await mirror.sync(REPO, forge="github", token="s3cret", gitlab_url="")
    await mirror.log(REPO, ["a", "b"])
    clone, fetch, log = calls
    assert clone[0][:3] == ["clone", "--bare", "--quiet"] and clone[0][3] == f"https://github.com/{REPO}.git"
    assert fetch[0][2:4] == ["fetch", "--quiet"]
    assert all("s3cret" not in " ".join(c[0]) for c in calls), "the token is never an argument"
    expected = base64.b64encode(b"x-access-token:s3cret").decode()
    assert clone[1]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
    assert "--stdin" in log[0] and log[2] == b"a\nb\n"
    assert auth_env("gitlab", "t")["GIT_CONFIG_VALUE_0"].endswith(base64.b64encode(b"oauth2:t").decode())
    with pytest.raises(ValueError):
        mirror.path("../etc")


def test_a_judgment_is_parsed_strictly_and_the_prompt_says_what_the_commit_did() -> None:
    assert parse_judgment('{"slop": false, "reason": "moved the parser"}') == (False, "moved the parser")
    with pytest.raises(ValueError):
        parse_judgment('{"slop": "maybe"}')
    commit = Commit("abc123def4567", START, "Dev", "dev@example.com", "Import the parser", (("src/p.py", 9000, 0),))
    prompt = user_prompt(REPO, Outlier(commit, 9_000.0, None, 12_000.0, 40))
    assert "Net weighted lines added: 9,000" in prompt and "- src/p.py (+9000 −0)" in prompt and "40 days" in prompt


@pytest.mark.asyncio
async def test_a_job_hears_its_areas_reading_else_its_repositorys(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"repo": REPO, "area": "", "score": 0.31, "level": 2, "name": "Spaghetti Bolognese", "provisional": False},
        {"repo": REPO, "area": "Billing", "score": 0.71, "level": 4, "name": "Sloppy Joe", "provisional": True},
    ]

    class Repo:
        def __init__(self, db: Any) -> None:
            pass

        async def scores(self, repo: str) -> list[dict[str, Any]]:
            return rows

    class Jobs:
        def __init__(self, area: str | None) -> None:
            self.area = area

        async def get(self, job_id: str) -> dict[str, Any]:
            return {"job_id": job_id, "area": self.area}

    monkeypatch.setattr("embry0.storage.repositories.slop_meter.SlopMeterRepository", Repo)
    state = {"repo": REPO, "job_id": "j1"}
    scope, row = await job_meter({"configurable": {"db": object(), "jobs_repo": Jobs("Billing")}}, state)
    assert scope == f"the Billing area of {REPO}" and row["name"] == "Sloppy Joe"
    assert meter_sentence(scope, row).endswith(
        "(score 0.71) (provisional: a large commit is not judged yet): about 71% of its weighted lines landed "
        "beyond what its human contributors could attend to."
    )
    scope, row = await job_meter({"configurable": {"db": object(), "jobs_repo": Jobs(None)}}, state)
    assert scope == REPO and row["name"] == "Spaghetti Bolognese"
    assert await job_meter({"configurable": {}}, state) is None


def test_the_loop_module_names_its_settings_from_config() -> None:
    class Config:
        slop_meter_interval_seconds = 0
        slop_meter_commits_per_tick = 7

    settings = loop_mod.slop_meter_settings(Config())
    assert settings.interval_seconds == 0 and settings.commits_per_tick == 7 and settings.judgments_per_tick == 5
