"""The Slop-O-Meter's loop, mirror, events and judgments on fakes (spec 2026-10-26, slop-o-meter): no git, no network."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from embry0.branding import GIT_AUTHOR_EMAIL
from embry0.services.areas import Area
from embry0.services.background_scope import Enrollment, Registered
from embry0.services.slop_meter import loop as loop_mod
from embry0.services.slop_meter.context import job_meter, meter_sentence
from embry0.services.slop_meter.events import MAX_FAILURES, ApiBudget, BudgetSpent, EventReader
from embry0.services.slop_meter.history import HISTORY_VERSION, Commit
from embry0.services.slop_meter.judge import parse_judgment, user_prompt
from embry0.services.slop_meter.loop import MeterRead, SlopMeterLimits, SlopMeterLoop
from embry0.services.slop_meter.mirror import GitError, GitMirror, auth_env
from embry0.services.slop_meter.score import Outlier
from embry0.settings.sections.background import BackgroundSettings
from embry0.settings.sections.slop_meter import SlopMeterSettings
from tests.fixtures.areas import areas_of, no_areas_of

START = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
REPO = "acme/web"
FORGE = "https://forge.example"  # the base the next-page links of the fakes name


def _record(sha: str, at: datetime, files: list[tuple[str, int, int]], email: str = "dev@example.com") -> str:
    numstat = "\n".join(f"{a}\t{d}\t{p}" for p, a, d in files)
    return f"\x1e{sha}\x1f{at.isoformat()}\x1fDev\x1f{email}\x1fWork\n\x1f\n{numstat}\n"


class FakeMirror:
    def __init__(self, records: dict[str, str], order: list[str], files: dict[str, str] | None = None) -> None:
        self.records, self.order, self.files = records, order, files or {}
        self.synced: list[tuple[str, str]] = []
        self.logged: list[list[str]] = []
        self.read_paths: list[str] = []

    async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
        self.synced.append((repo, forge))

    async def history(self, repo: str) -> list[str]:
        return list(self.order)

    async def log(self, repo: str, shas: list[str]) -> str:
        self.logged.append(list(shas))
        return "".join(self.records[s] for s in shas)

    async def read(self, repo: str, path: str) -> str | None:
        self.read_paths.append(path)
        return self.files.get(path)


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.event_rows: dict[str, dict[str, dict[str, Any]]] = {}
        self.login_map: dict[str, str] = {}
        self.judged: dict[tuple[str, str], bool] = {}
        self.stored: dict[str, Any] = {}
        #: Each repository's configuration version, as its last scoring recorded it.
        self.versions_of: dict[str, str] = {}
        #: Each credential's turn (#396).
        self.turn_of: dict[str, int] = {}

    async def state(self, repo: str) -> dict[str, Any]:
        return dict(self.states.get(repo, {"repo": repo, "commits_read": 0}))

    async def held(self) -> list[str]:
        return sorted(self.states)

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

    async def store(self, repo: str, results: dict[str, Any], computed_at: datetime, config_version: str = "") -> None:
        self.stored[repo] = results
        self.versions_of[repo] = config_version

    async def versions(self) -> dict[str, str]:
        return dict(self.versions_of)

    async def turns(self) -> dict[str, int]:
        return dict(self.turn_of)

    async def set_turns(self, turns: dict[str, int]) -> None:
        self.turn_of.update(turns)


async def _no_bots() -> tuple[str, ...]:
    return ()


def _history(n: int) -> tuple[dict[str, str], list[str]]:
    order = [f"c{i:03d}" for i in range(n)]
    return {s: _record(s, START + timedelta(hours=3 * i), [("src/a.py", 20, 0)]) for i, s in enumerate(order)}, order


class _EveryRepository:
    """The ``background`` section with every repository enrolled in the meter."""

    def deployment(self) -> BackgroundSettings:
        return BackgroundSettings(slop_meter=True)

    def repo(self, repo: str | None) -> BackgroundSettings:
        return BackgroundSettings(slop_meter=True)


class NoExits:
    """The record of repositories that left the scope: never read here, since nothing is settled."""

    async def left(self, feature: str) -> dict[str, Any]:
        return {}

    async def mark(self, feature: str, repos: Any, *, at: Any) -> list[str]:
        return []

    async def clear(self, feature: str, repos: Any) -> list[str]:
        return []


async def no_audit(*, action: str, details: dict[str, Any]) -> None:
    return None


class _Meter:
    """The ``slop_meter`` section, the same for every repository: the defaults with ``values`` over them."""

    def __init__(self, **values: Any) -> None:
        self.values = SlopMeterSettings(**values)

    def deployment(self) -> SlopMeterSettings:
        return self.values

    def repo(self, repo: str | None) -> SlopMeterSettings:
        return self.values


def _read(model: str = "", **meter: Any) -> Any:
    """A tick's read with every listed repository enrolled, its outliers judged on ``model`` and the
    ``slop_meter`` section's ``meter`` keys over its defaults. Not settled, so nothing is removed: what the
    meter removes is tested in test_prune.py."""

    async def read() -> MeterRead:
        return MeterRead(
            models=lambda repo: model,
            enrollment=Enrollment(_EveryRepository(), frozenset()),
            meter=_Meter(**meter),
        )

    return read


class FakeCalls:
    """Every model it is asked about can be called; the judge itself is replaced where a test asks one."""

    def __init__(self) -> None:
        self.endpoints: list[tuple[str, str, str | None]] = []

    async def endpoint(self, feature: str, model: str, repo: str | None) -> object:
        self.endpoints.append((feature, model, repo))
        return object()


def _loop(
    mirror: FakeMirror,
    store: FakeStore,
    *,
    client: Any = None,
    per_tick: int = 3,
    model: str = "",
    owner_bots: tuple[str, ...] = (),
    calls: Any = None,
    areas: dict[str, list[Area]] | None = None,
) -> SlopMeterLoop:
    async def repos() -> Registered:
        return Registered(active=(REPO,))

    async def bots(repo: str) -> tuple[str, ...]:
        return owner_bots

    async def token_for(repo: str) -> tuple[str, str, str] | None:
        return ("github", "secret-token", "")

    async def client_for(repo: str) -> Any:
        return client

    return SlopMeterLoop(
        repos=repos,
        exits=NoExits(),
        audit=no_audit,
        read=_read(model),
        store=store,
        mirror=mirror,
        token_for=token_for,
        client_for=client_for,
        bots=bots,
        calls=calls if calls is not None else FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=per_tick),
        areas_of=areas_of(areas),
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
async def test_embry0s_identity_and_the_owners_bots_are_bots_when_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    """#184: rows read before either was known count as a bot's too, without reading the history again."""
    records = {
        "c0": _record("c0", START, [("src/a.py", 20, 0)]),
        "c1": _record("c1", START + timedelta(hours=3), [("src/a.py", 20, 0)], email="release@acme.example"),
        "c2": _record("c2", START + timedelta(hours=6), [("src/a.py", 20, 0)], email=GIT_AUTHOR_EMAIL),
    }
    mirror, store = FakeMirror(records, ["c0", "c1", "c2"]), FakeStore()
    store.event_rows[REPO] = {
        "e1": {"kind": "comment", "login": "ana", "at": START},
        "e2": {"kind": "comment", "login": "Release-Bot", "at": START},
    }
    seen: list[Any] = []
    real = loop_mod.compute

    def spy(inputs: Any) -> Any:
        seen.append(inputs)
        return real(inputs)

    monkeypatch.setattr(loop_mod, "compute", spy)
    loop = _loop(mirror, store, per_tick=10, owner_bots=("Release@Acme.example", "release-bot"))
    await loop.tick()
    for row in store.rows[REPO]:
        row["bot"] = False  # as stored by a reader that knew neither identity
    await loop.tick()
    assert len(mirror.logged) == 1, "the history was not read again"
    inputs = seen[-1]
    assert {c.sha for c in inputs.commits if c.bot} == {"c1", "c2"}, "the owner's bot and embry0's own commits"
    assert [e.login for e in inputs.events] == ["ana"], "the owner's bot's comments add no attention"


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

    async def judge(repo: str, outlier: Outlier, ep: Any, client: Any, calls: Any, **_: Any) -> tuple[bool, str]:
        asked.append(outlier.commit.sha)
        return False, "a vendored parser"

    monkeypatch.setattr("embry0.services.slop_meter.judge.judge", judge)
    calls = FakeCalls()
    loop = _loop(FakeMirror(records, order), store, per_tick=100, model="custom/local", calls=calls)
    await loop.tick()
    assert calls.endpoints == [("slop_judge", "custom/local", REPO)], (
        "the judge's endpoint is resolved for its own feature and with the repository, so its key bindings apply"
    )
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
async def test_areas_come_from_embry0_and_ignore_lists_from_the_head() -> None:
    """The repository's areas are the ones embry0 keeps (spec 2026-11-10): an areas file left in the head is not
    read, while the head's ignore lists still weigh its lines."""
    order = ["a", "b"]
    records = {
        "a": _record("a", START, [("api/x.py", 50, 0), ("web/dist/app.js", 900, 0)]),
        "b": _record("b", START + timedelta(hours=5), [("web/y.ts", 30, 0)]),
    }
    files = {
        ".prettierignore": "dist/\n",
        ".embry0/areas.yml": "areas:\n  - name: Old\n    paths: [api/**]\n",
    }
    mirror = FakeMirror(records, order, files)
    store = FakeStore()
    areas = {REPO: [Area("Api", ("api/**",)), Area("Web", ("web/**",))]}
    await _loop(mirror, store, per_tick=10, areas=areas).tick()
    results = store.stored[REPO]
    assert set(results) == {"", "Api", "Web"}, "each area embry0 keeps is scored, and the file's is not"
    assert results["Web"].weeks[-1].lines == 30, "the ignored build output weighs nothing"
    assert ".prettierignore" in mirror.read_paths
    assert not [p for p in mirror.read_paths if p.startswith(".embry0/")], "no embry0 file of the repository is read"


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
        if path == f"/repos/{r}/issues/comments":
            page = int((params or {}).get("page", 1))
            nxt = f"{FORGE}{path}?page={page + 1}" if page < self.comment_pages else None
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

    async def repos() -> Registered:
        return Registered(active=(big, small))

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", "t", "")

    async def client_for(repo: str) -> Any:
        return forges[repo]

    loop = SlopMeterLoop(
        repos=repos,
        exits=NoExits(),
        audit=no_audit,
        read=_read(api_calls_per_tick=16, rate_reserve=0),
        store=store,
        mirror=mirror,
        token_for=token_for,
        client_for=client_for,
        bots=lambda repo: _no_bots(),
        calls=FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=10, event_pages=100),
        areas_of=no_areas_of,
        clock=lambda: START + timedelta(days=60),
    )
    assert await loop.tick() is False, "the big repository waits for the next run, at the interval (#396)"
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

    async def run(
        args: Any, env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
    ) -> tuple[int, bytes, bytes]:
        calls.append((list(args), env, stdin))
        here = Path(f"/proc/self/fd/{dir_fd}")  # the directory git was started in
        if args[0] == "clone":
            (here / args[-1]).mkdir()
            (here / args[-1] / "HEAD").write_text("ref: refs/heads/main\n")
        if "config" in args:
            (here / "config").write_text("[core]\n\tbare = true\n[embry0]\n\tslopMeter = true\n")
        return 0, b"", b""

    mirror = GitMirror(tmp_path, run=run)
    await mirror.sync(REPO, forge="github", token="s3cret", gitlab_url="")
    await mirror.sync(REPO, forge="github", token="s3cret", gitlab_url="")
    await mirror.log(REPO, ["a", "b"])
    assert [c[0][2] if c[0][0] == "-C" else c[0][0] for c in calls][:3] == ["clone", "config", "fetch"], (
        "the clone is marked as the meter's own (git config embry0.slopMeter true), and the fetch of a marked "
        f"mirror marks nothing again: {[c[0][:4] for c in calls]}"
    )
    assert len(calls) == 4, "then the log is read"
    clone, marker, fetch, log = calls
    assert clone[0][:3] == ["clone", "--bare", "--quiet"] and clone[0][3] == f"https://github.com/{REPO}.git"
    assert clone[0][4] == "web.git", "the clone names the mirror in the directory above it, where git is started"
    assert marker[0][2:] == ["config", "embry0.slopMeter", "true"], "the clone is marked as the meter's own"
    assert fetch[0][2:4] == ["fetch", "--quiet"], "a marked mirror is fetched and not marked again"
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


def test_the_loop_module_names_its_limits_from_config() -> None:
    class Config:
        slop_meter_catchup_seconds = 60
        slop_meter_commits_per_tick = 7

    limits = loop_mod.slop_meter_limits(Config())
    assert limits.catchup_seconds == 60 and limits.commits_per_tick == 7 and limits.judgments_per_tick == 5


@pytest.mark.asyncio
async def test_a_repositorys_bots_are_embry0s_authors_there_and_the_owners_list() -> None:
    """#184: production's ``bots`` reads the repository's preferred author and the owner's credential's author,
    and, until its layer imported them (#169), the ``slop_meter_bots`` the owner's row still lists; a missing
    source adds nothing."""
    from types import SimpleNamespace

    class Store:
        def __init__(self, rows: dict[str, Any]) -> None:
            self.rows = rows

        async def get(self, key: str) -> Any:
            return self.rows.get(key)

    asked: list[tuple[str, str]] = []

    async def moved(section: str, owner: str, settings: Any) -> dict[str, Any]:
        asked.append((section, owner))
        return {"bots": list(settings.get("slop_meter_bots") or [])}

    prefs = Store({REPO: {"git_author_email": "agent@acme.example"}})
    org = SimpleNamespace(owner="acme", credential_id="cred-1", settings={"slop_meter_bots": ["release-bot"]})
    orgs = Store({REPO.split("/")[0]: org})
    credentials = Store({"cred-1": {"git_author_email": "bot@acme.example"}})
    got = await loop_mod.repo_bots(REPO, prefs=prefs, orgs=orgs, credentials=credentials, moved=moved)
    assert got == ["agent@acme.example", "bot@acme.example", "release-bot"]
    assert asked == [("slop_meter", "acme")], "the owner's row is read through the moved-settings reader"
    imported = await loop_mod.repo_bots(REPO, prefs=prefs, orgs=orgs, credentials=credentials)
    assert imported == ["agent@acme.example", "bot@acme.example"], (
        "without the reader the row is not read: the layer's slop_meter.bots are the section's, read when scored"
    )
    nothing = await loop_mod.repo_bots(REPO, prefs=Store({}), orgs=Store({}), credentials=Store({}))
    assert nothing == []


async def test_each_credential_keeps_its_own_budget() -> None:
    """#185: two owners on two tokens. One token near its reserve stops only its own repository,
    and the per-tick calls are counted per token, not shared between them."""
    low, high = "lowco/app", "highco/app"
    forges = {low: FakeForge(remaining=10, repo=low), high: FakeForge(comment_pages=50, repo=high)}
    records, order = _history(2)
    store = FakeStore()

    async def repos() -> Registered:
        return Registered(active=(low, high))

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", f"token-of-{repo.split('/')[0]}", "")

    async def client_for(repo: str) -> Any:
        return forges[repo]

    async def no_bots(repo: str) -> tuple[str, ...]:
        return ()

    loop = SlopMeterLoop(
        repos=repos,
        exits=NoExits(),
        audit=no_audit,
        read=_read(api_calls_per_tick=8, rate_reserve=100),
        store=store,
        mirror=FakeMirror(records, order),
        token_for=token_for,
        client_for=client_for,
        bots=no_bots,
        calls=FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=10, event_pages=100),
        areas_of=no_areas_of,
        clock=lambda: START + timedelta(days=60),
    )
    assert await loop.tick() is False, "a repository waiting for forge budget holds no catch-up (#396)"
    assert len(forges[low].calls) == 1, "the low token stops at its reserve after one call"
    assert len(forges[high].calls) == 8, (
        f"the other token reads its whole budget, not what the low one left: {len(forges[high].calls)} calls"
    )


# ---- a reader the budget stops goes on where it stopped (#396) ----


class PassForge:
    """One repository's listings as GitHub pages them: comments by update time from a cursor, a page a comment, and
    pull requests newest first, two a page, each with one review. ``repeat`` puts the last pull request of the first
    page first on the second as well, as one updated between the two reads pushes it there."""

    COMMENTS = (("ana", 1), ("bo", 2), ("carl", 3))
    PULLS = (9, 8, 7, 6)

    def __init__(self, *, repeat: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.repeat = repeat

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == f"/repos/{REPO}/issues/comments":
            since, page = params.get("since", ""), int(params.get("page", 1))
            later = [(i, login, day) for i, (login, day) in enumerate(self.COMMENTS, 1) if _stamp(day) > since]
            if len(later) < page:
                return FakeResponse(200, [])
            i, login, day = later[page - 1]
            nxt = f"{FORGE}{path}?page={page + 1}" if len(later) > page else None
            return FakeResponse(200, [_comment(i, login, day)], next_url=nxt)
        if path == f"/repos/{REPO}/pulls/comments":
            return FakeResponse(200, [])
        if path == f"/repos/{REPO}/pulls":
            first = int(params.get("page", 1)) == 1
            numbers = self.PULLS[:2] if first else (self.PULLS[1:] if self.repeat else self.PULLS[2:])
            pulls = [{"number": n, "updated_at": f"2025-03-{n:02d}T00:00:00Z"} for n in numbers]
            return FakeResponse(200, pulls, next_url=f"{FORGE}{path}?page=2" if first else None)
        if path.endswith("/reviews"):
            n = int(path.split("/")[-2])
            return FakeResponse(
                200, [{"id": 100 + n, "user": {"login": "dee"}, "submitted_at": "2025-03-10T00:00:00Z"}]
            )
        if path == f"/repos/{REPO}/commits":
            return FakeResponse(200, [{"commit": {"author": {"email": f"{params['author']}@example.com"}}}])
        return FakeResponse(404, {})


def _stamp(day: int) -> str:
    return f"2025-02-{day:02d}T10:00:00Z"


def _asked(call: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    """What a call asks for, whatever its form: the next page of a comment listing and the listing from the cursor
    that page starts after ask for the same comments, and a resumed listing asks the second way."""
    path, params = call
    if path.endswith("/issues/comments"):
        since, page = str(params.get("since", "")), int(params.get("page", 1))
        if page > 1:  # page N starts after the last comment of page N - 1, one comment a page
            later = [day for _login, day in PassForge.COMMENTS if _stamp(day) > since]
            since = _stamp(later[page - 2])
        return ("comments updated after", since)
    return (path, str(sorted({k: v for k, v in params.items() if k != "page"}.items())), params.get("page", 1))


async def _read_a_call_a_tick(forge: PassForge, **caps: int) -> dict[str, Any]:
    """Read the repository's events a call a tick, as a credential shared by many repositories deals them: each tick
    a new reader, from the cursors and logins the last stored and the place it stopped at."""
    cursors: dict[str, Any] = {"comments_since": None, "review_comments_since": None, "reviews_since": None}
    events: dict[str, tuple[str, str, str, datetime]] = {}
    logins: dict[str, str] = {}
    place = None
    for _ in range(100):
        reader = EventReader(
            forge,
            REPO,
            **cursors,
            known_logins=logins,
            stored_logins=[e[2] for e in events.values()],
            place=place,
            **caps,
        )
        try:
            await _read_all(reader, ApiBudget(1, 0))
        except BudgetSpent:
            pass
        batch = reader.batch
        events.update({e[0]: e for e in batch.events})
        logins.update(batch.logins)
        cursors = {k: getattr(batch, k) for k in cursors}
        place = reader.place
        if place is None:
            return {"events": sorted(events), "logins": logins, **cursors}
    raise AssertionError("a call a tick never finished the read")


async def _read_in_one_tick(forge: PassForge, **caps: int) -> dict[str, Any]:
    reader = EventReader(forge, REPO, comments_since=None, review_comments_since=None, reviews_since=None, **caps)
    await _read_all(reader, ApiBudget(100, 0))
    batch = reader.batch
    return {
        "events": sorted(e[0] for e in batch.events),
        "logins": dict(batch.logins),
        "comments_since": batch.comments_since,
        "review_comments_since": batch.review_comments_since,
        "reviews_since": batch.reviews_since,
    }


async def test_a_read_a_call_a_tick_makes_the_calls_one_tick_with_budget_enough_makes() -> None:
    """A reader stopped by the budget mid-listing, mid-pull-requests, mid-reviews and mid-logins goes on at the next
    tick where it stopped: every call it makes is one a single tick with budget enough makes, in the same order, and
    it reads what that tick reads. The caps hold for the pass, not for each tick: two pages of comments, three
    reviews, two logins."""
    caps = {"max_pages": 2, "max_pulls": 3, "max_logins": 2}
    whole, a_call_a_tick = PassForge(), PassForge()

    expected = await _read_in_one_tick(whole, **caps)
    got = await _read_a_call_a_tick(a_call_a_tick, **caps)

    asked, expected_asked = [_asked(c) for c in a_call_a_tick.calls], [_asked(c) for c in whole.calls]
    assert asked == expected_asked, f"the calls differ:\n  a call a tick: {asked}\n  one tick:      {expected_asked}"
    assert got == expected, f"a call a tick read {got}, one tick {expected}"


async def test_a_pull_request_found_again_on_the_next_page_is_reviewed_once() -> None:
    """A pull request updated between two pages of the listing, which a read spread over runs makes likely, pushes
    one already found onto the next page: it is reviewed once."""
    forge = PassForge(repeat=True)
    await _read_a_call_a_tick(forge, max_pages=2, max_pulls=10, max_logins=0)
    reviewed = [path for path, _ in forge.calls if path.endswith("/reviews")]
    assert sorted(reviewed) == sorted(set(reviewed)), f"a pull request was reviewed twice: {reviewed}"


@pytest.mark.parametrize(("repos", "budget"), [(70, 200), (12, 5)], ids=["70 repositories, 200 calls", "12, 5"])
async def test_many_repositories_on_one_credential_are_each_scored_within_a_bound(repos: int, budget: int) -> None:
    """#396: more repositories on one credential than one run's budget reads whole. Each run goes on where the
    last stopped, so every repository is scored within a bound, and none puts the meter on catch-up.

    A whole read is at most 7 calls here (the two listings, the pull requests, two reviews, two logins). With B
    calls a run for R repositories, B >= R gives each at least B // R calls a run, so ceil(7 / (B // R)) runs; with
    B < R the turn moves on one repository a run, so each gets B calls in any R runs: R * ceil(7 / B) runs.
    """
    names = [f"acme/app-{i:02d}" for i in range(repos)]
    forges = {name: FakeForge(repo=name) for name in names}
    records, order = _history(2)
    store = FakeStore()

    async def registered() -> Registered:
        return Registered(active=tuple(names))

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", "one-token", "")

    async def client_for(repo: str) -> Any:
        return forges[repo]

    loop = SlopMeterLoop(
        repos=registered,
        exits=NoExits(),
        audit=no_audit,
        read=_read(api_calls_per_tick=budget, rate_reserve=0),
        store=store,
        mirror=FakeMirror(records, order),
        token_for=token_for,
        client_for=client_for,
        bots=lambda repo: _no_bots(),
        calls=FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=10),
        areas_of=no_areas_of,
        clock=lambda: START + timedelta(days=60),
    )
    whole = 7
    bound = -(-whole // (budget // repos)) if budget >= repos else repos * -(-whole // budget)

    backlog, made = [], 0
    for _ in range(bound):
        backlog.append(await loop.tick())
        calls = sum(len(f.calls) for f in forges.values())
        assert calls - made <= budget, f"a run made {calls - made} calls on a budget of {budget}"
        made = calls

    unscored = [name for name in names if name not in store.stored]
    assert not unscored, f"{len(unscored)} of {repos} repositories were not scored within {bound} runs: {unscored[:5]}"
    assert not any(backlog), "a repository waiting for forge budget never puts the meter on catch-up"


def _crowd_loop(
    store: Any, forges: dict[str, Any], names: list[str], *, budget: int, gitlab: frozenset[str] = frozenset()
) -> SlopMeterLoop:
    """A meter over ``names``, every GitHub one read with one credential on ``budget`` calls a run; the ones in
    ``gitlab`` are read with another forge, which makes no event calls."""
    records, order = _history(2)

    async def registered() -> Registered:
        return Registered(active=tuple(names))

    async def token_for(repo: str) -> tuple[str, str, str]:
        return (
            ("gitlab", "other-token", "https://gitlab.example.com") if repo in gitlab else ("github", "one-token", "")
        )

    async def client_for(repo: str) -> Any:
        return None if repo in gitlab else forges[repo]

    return SlopMeterLoop(
        repos=registered,
        exits=NoExits(),
        audit=no_audit,
        read=_read(api_calls_per_tick=budget, rate_reserve=0),
        store=store,
        mirror=FakeMirror(records, order),
        token_for=token_for,
        client_for=client_for,
        bots=lambda repo: _no_bots(),
        calls=FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=10),
        areas_of=no_areas_of,
        clock=lambda: START + timedelta(days=60),
    )


async def test_the_bound_holds_per_credential_beside_a_crowd_read_otherwise() -> None:
    """#396: the turn is each credential's, so 12 repositories on one credential's 5 calls are scored within
    12 * ceil(7 / 5) = 24 runs however many other repositories the meter reads (here 60, on another forge)."""
    github = [f"acme/app-{i:02d}" for i in range(12)]
    crowd = [f"acme/crowd-{i:02d}" for i in range(60)]
    forges = {name: FakeForge(repo=name) for name in github}
    store = FakeStore()
    loop = _crowd_loop(store, forges, github + crowd, budget=5, gitlab=frozenset(crowd))
    for _ in range(24):
        await loop.tick()
    unscored = [name for name in github if name not in store.stored]
    assert not unscored, f"{len(unscored)} of 12 were not scored within 24 runs beside 60 others: {unscored}"


class FailingReviews(FakeForge):
    """A forge whose reviews listing raises every time."""

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        if path.endswith("/reviews"):
            self.calls.append((path, dict(params or {})))
            raise RuntimeError("this listing fails every time")
        return await super().get(path, repo=repo, params=params)


async def test_a_call_that_keeps_failing_is_passed_after_max_failures_and_the_read_finishes() -> None:
    """#396: a read whose next call raises is held, keeping its previous score, and goes on from that call the
    next run; the MAX_FAILURES-th failure in a row there moves it past the call, says so, and it reads on. Both pull
    requests' reviews fail: the first is passed in run 3, where the second fails a first time, and the second in run
    5, where the logins are read and the repository is scored."""
    from structlog.testing import capture_logs

    from embry0.services.slop_meter.events import MAX_FAILURES

    forge, store = FailingReviews(), FakeStore()
    loop = _crowd_loop(store, {REPO: forge}, [REPO], budget=200)
    scored_in = None
    reviews_per_run = []
    with capture_logs() as logs:
        for run in range(1, 2 * MAX_FAILURES + 1):
            before = len(forge.calls)
            await loop.tick()
            reviews_per_run.append(sum(1 for path, _ in forge.calls[before:] if path.endswith("/reviews")))
            if REPO in store.stored:
                scored_in = run
                break
    assert scored_in == 2 * MAX_FAILURES - 1, f"scored in run {scored_in}, reviews tried per run {reviews_per_run}"
    skipped = [entry for entry in logs if entry["event"] == "slop_meter_events_skipped"]
    assert [entry["stage"] for entry in skipped] == ["reviews", "reviews"], f"each pass is said once: {skipped}"
    assert reviews_per_run == [1, 1, 2, 1, 1], (
        f"each run tries the failing call once, more after a pass: {reviews_per_run}"
    )
    before = len(forge.calls)
    await loop.tick()
    again = [path for path, _ in forge.calls[before:] if path.endswith("/reviews")]
    assert again == [], f"the reviews cursor moved past the pull requests passed, so none is tried again: {again}"


class FailingLogin(FakeForge):
    """A forge whose commit search for one login raises every time, and for another raises once."""

    def __init__(self) -> None:
        super().__init__()
        self.once = True

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        author = (params or {}).get("author")
        if path.endswith("/commits") and (author == "bo" or (author == "ana" and self.once)):
            self.calls.append((path, dict(params or {})))
            self.once = self.once and author != "ana"
            raise RuntimeError("the commit search fails")
        return await super().get(path, repo=repo, params=params)


async def test_failures_count_in_a_row_and_a_login_passed_is_kept_with_no_email() -> None:
    """One failure, then a call that works, starts the count again; a login passed after MAX_FAILURES failures is
    kept with no email, so no later pass looks it up again."""
    from embry0.services.slop_meter.events import MAX_FAILURES

    forge, store = FailingLogin(), FakeStore()
    loop = _crowd_loop(store, {REPO: forge}, [REPO], budget=200)
    for _ in range(1 + MAX_FAILURES):  # ana fails once, then works; bo fails MAX_FAILURES times in a row
        await loop.tick()
    assert REPO in store.stored, f"the read finished: {store.states[REPO]}"
    assert store.login_map == {"ana": "", "bo": ""}, f"ana was looked up, bo passed with no email: {store.login_map}"
    lookups = [params["author"] for path, params in forge.calls if path.endswith("/commits")]
    assert lookups == ["ana", "ana", "bo", "bo", "bo"], f"ana's failure was not counted against bo: {lookups}"
    before = len(forge.calls)
    await loop.tick()
    assert not [c for c in forge.calls[before:] if c[0].endswith("/commits")], "no login is looked up again"


class ShiftingPulls:
    """Pull requests ``1..n``, newest updated first, a hundred a page; ``open`` adds a newer one between reads."""

    def __init__(self, n: int) -> None:
        self.updated = {number: START + timedelta(hours=number) for number in range(1, n + 1)}
        self.reviewed: list[int] = []
        self.pages = 0

    def open(self, number: int) -> None:
        self.updated[number] = START + timedelta(hours=number)

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        if path == f"/repos/{REPO}/pulls":
            self.pages += 1
            page = int((params or {}).get("page", 1))
            newest = sorted(self.updated, key=lambda n: self.updated[n], reverse=True)
            chunk = newest[(page - 1) * 100 : page * 100]
            items = [{"number": n, "updated_at": self.updated[n].strftime("%Y-%m-%dT%H:%M:%SZ")} for n in chunk]
            more = page * 100 < len(newest)
            return FakeResponse(200, items, next_url=f"{FORGE}{path}?page={page + 1}" if more else None)
        if path.endswith("/reviews"):
            self.reviewed.append(int(path.split("/")[-2]))
            return FakeResponse(200, [])
        return FakeResponse(200, [])


async def _one_run(forge: ShiftingPulls, calls: int, place: Any, *, max_pulls: int = 1_000) -> Any:
    reader = EventReader(
        forge,
        REPO,
        comments_since=START,
        review_comments_since=START,
        reviews_since=START,
        max_pages=2,
        max_pulls=max_pulls,
        place=place,
    )
    try:
        await _read_all(reader, ApiBudget(calls, 0))
    except BudgetSpent:
        pass
    return reader.place


@pytest.mark.parametrize(
    ("max_pulls", "opened", "reviewed"),
    [(1_000, 1, range(51, 251)), (50, 60, range(51, 101))],
    ids=["one opened", "more opened than the pass reviews"],
)
async def test_pull_requests_opened_between_two_runs_of_a_pass_push_no_older_one_past_the_cap(
    max_pulls: int, opened: int, reviewed: range
) -> None:
    """#396: the cap is two pages' worth of pull requests found, not two pages read. Pull requests opened between
    the runs of a pass move to the first page and bring ones already found onto the next: a pass spread over two runs
    reviews the very pull requests a pass in one run reviews (of 250, the oldest it may review of the 200 newest).
    The place keeps the numbers of all it found, not only the ones it will review, so one found on the first page
    and brought again on the next is not counted twice, however many were opened."""
    in_one_run = ShiftingPulls(250)
    assert await _one_run(in_one_run, 1_000, None, max_pulls=max_pulls) is None
    spread = ShiftingPulls(250)
    place = await _one_run(spread, 3, None, max_pulls=max_pulls)  # the two comment listings, one page of pulls
    assert place is not None and place.stage == 2 and place.found == 100, place
    for number in range(251, 251 + opened):
        spread.open(number)
    assert await _one_run(spread, 1_000, place, max_pulls=max_pulls) is None
    assert sorted(spread.reviewed) == sorted(in_one_run.reviewed) == list(reviewed), (
        f"spread over two runs, a pass reviewed {len(spread.reviewed)} pull requests from "
        f"{min(spread.reviewed)}; in one run {len(in_one_run.reviewed)} from {min(in_one_run.reviewed)}"
    )


async def test_a_stored_place_keeps_only_the_pull_requests_it_will_review_and_reads_back_whole() -> None:
    from embry0.services.slop_meter.events import ReaderPlace

    forge = ShiftingPulls(250)
    reader = EventReader(
        forge, REPO, comments_since=START, review_comments_since=START, reviews_since=START, max_pages=2, max_pulls=3
    )
    with pytest.raises(BudgetSpent):
        await _read_all(reader, ApiBudget(5, 0))  # two listings, two pages of pull requests, one review
    place = reader.place
    assert place is not None and place.stage == 3 and place.pulls_left == 2, place
    assert [number for _at, number in place.pulls] == [52, 53], (
        f"of 200 pull requests found it keeps the 2 it will still review, the oldest: {place.pulls}"
    )
    caps = {"max_pages": 2, "max_pulls": 3, "max_logins": 0}
    assert ReaderPlace.from_json(place.to_json(), **caps) == place, "a place reads back as it was stored"
    for stored in (None, "x", {}, {"stage": 9}, {"stage": 3, "pulls": [["not a time", 1]]}, {"stage": "x"}):
        assert ReaderPlace.from_json(stored, **caps) is None, f"{stored!r} names no place: the read starts over"


#: The caps of :data:`OUT_OF_RANGE`'s reader: 2 pages, 3 reviews, 2 logins (so at most 200 pull requests found).
CAPS = {"max_pages": 2, "max_pulls": 3, "max_logins": 2}
#: A place each field of which is at the end of its range for :data:`CAPS`, and one past it for each.
EDGE = {
    "stage": 2,
    "pages": 4,
    "next_page": 5,
    "pulls": [["2025-01-01T00:00:00Z", 7]] * 3,
    "found": 200,
    "seen": list(range(1, 201)),
    "pulls_left": 3,
    "logins": ["ana", "bo"],
    "failures": 2,
}
OUT_OF_RANGE = {
    "a negative page count, which lifts the page cap": {"pages": -1_000_000},
    "more pages than a pass reads": {"pages": 5},
    "a next page past the pages a pass reads": {"next_page": 6},
    "a next page of 10**30": {"next_page": 10**30},
    "a page that is text": {"pages": "3"},
    "more reviews left than the cap, which lifts it": {"pulls_left": 10**9},
    "negative reviews left": {"pulls_left": -1},
    "more pull requests kept than reviews left": {"pulls_left": 2},
    "more found than the page cap allows": {"found": 201},
    "more seen than the page cap allows": {"seen": list(range(1, 202))},
    "a pull request numbered 0": {"seen": [0]},
    "a negative failure count, which holds the skip back": {"failures": -7},
    "as many failures as move the read on": {"failures": 3},
    "more logins than the cap": {"logins": ["ana", "bo", "cy"]},
    "a login that is not text": {"logins": [{"login": "ana"}]},
    "a stage that is text": {"stage": "2"},
    "a stage that is a float": {"stage": 2.0},
}


@pytest.mark.parametrize("change", list(OUT_OF_RANGE), ids=list(OUT_OF_RANGE))
def test_a_stored_place_out_of_range_is_no_place(change: str) -> None:
    """Every number of a stored place must be one a reader with these caps can write: a row edited by hand, or caps
    lowered since it was stored, starts the read over rather than lifting a cap for the rest of the pass."""
    from embry0.services.slop_meter.events import ReaderPlace

    assert ReaderPlace.from_json(EDGE, **CAPS) is not None, "the place at the end of every range is a place"
    stored = {**EDGE, **OUT_OF_RANGE[change]}
    assert ReaderPlace.from_json(stored, **CAPS) is None, f"{change}: {OUT_OF_RANGE[change]} names no place"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [{"stage": 4, "logins": ["ana"], "pages": -1}, {"stage": 4, "logins": [f"user{i}" for i in range(21)]}],
    ids=["a negative page count", "more logins than the meter's cap"],
)
async def test_the_meter_reads_a_stored_place_against_its_own_caps(stored: dict[str, Any]) -> None:
    """The loop checks a stored place against the caps it reads with (20 logins a tick): one out of range starts
    the read over at the first listing, instead of going on from it."""
    records, order = _history(3)
    store, forge = FakeStore(), FakeForge()
    await store.set_state(REPO, reader_place=stored)
    await _loop(FakeMirror(records, order), store, client=forge, per_tick=10).tick()
    assert forge.calls and forge.calls[0][0] == f"/repos/{REPO}/issues/comments", (
        f"the read went on from a place out of range: {forge.calls[:2]}"
    )


class FailingPage(ShiftingPulls):
    """The pull request listing, whose page 2 answers 502 the first ``failures`` times it is asked for."""

    def __init__(self, n: int, failures: int) -> None:
        super().__init__(n)
        self.failures = failures

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        if path == f"/repos/{REPO}/pulls" and int((params or {}).get("page", 1)) == 2 and self.failures:
            self.failures -= 1
            return FakeResponse(502, {"message": "Server Error"})
        return await super().get(path, repo=repo, params=params)


async def _pass_over_runs(forge: ShiftingPulls) -> int:
    """One pass, over as many runs as it takes, as the loop runs them: a call that raises ends the run's reading
    (counted with ``failed()``, which may move the read past it, and it reads on); the place and the reviews cursor
    carry over to the next run. The number of runs."""
    place, cursor = None, START
    for run in range(1, 10):
        reader = EventReader(
            forge,
            REPO,
            comments_since=START,
            review_comments_since=START,
            reviews_since=cursor,
            max_pages=2,
            max_pulls=1_000,
            place=place,
        )
        while True:
            try:
                await _read_all(reader, ApiBudget(1_000, 0))
                break
            except BudgetSpent:
                break
            except Exception:  # noqa: BLE001 - as the loop does
                if not reader.failed():
                    break
        place, cursor = reader.place, reader.batch.reviews_since or cursor
        if place is None:
            return run
    raise AssertionError("the pass never finished")


async def test_a_pull_request_page_that_fails_is_read_again_and_no_older_one_is_lost() -> None:
    """#483: a page of the pull request listing that answered an error ended the listing there, and the reviews
    cursor moved past every older pull request on that page and after it, unread for good. Since #490 a status a
    retry can change stops the read at that page (ForgeStopped), and the next run reads on from it: the pass
    reviews what one with no error reviews."""
    whole = ShiftingPulls(250)
    assert await _pass_over_runs(whole) == 1
    failing = FailingPage(250, failures=1)
    runs = await _pass_over_runs(failing)
    assert sorted(failing.reviewed) == sorted(whole.reviewed) == list(range(51, 251)), (
        f"a page that failed once lost {len(set(whole.reviewed) - set(failing.reviewed))} pull requests' reviews"
    )
    assert runs == 2, f"the run whose page failed, then the run that asks it again: {runs}"


async def test_a_pull_request_page_that_keeps_failing_is_passed_after_max_failures() -> None:
    """The same page failing in MAX_FAILURES runs in a row is passed, as any call is: the pass ends, with what it
    found before that page reviewed."""
    failing = FailingPage(250, failures=MAX_FAILURES)
    assert await _pass_over_runs(failing) == MAX_FAILURES
    assert sorted(failing.reviewed) == list(range(151, 251)), "the first page's pull requests are reviewed"


class ElsewhereLinks(ShiftingPulls):
    """A pull request listing whose next-page links name another host; every call is recorded."""

    def __init__(self, n: int) -> None:
        super().__init__(n)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append((path, dict(params or {})))
        response = await super().get(path, repo=repo, params=params)
        if response.links:
            response.links = {"next": {"url": response.links["next"]["url"].replace(FORGE, ELSEWHERE)}}
        return response


ELSEWHERE = "https://elsewhere.example"


async def test_the_reader_keeps_its_page_not_the_url() -> None:
    """The next page is kept as its number and asked for on the listing's own path, through the client's base: a
    link to another host is followed there by its page number alone, and a stored place that names a URL, in a
    field of its own or where the page goes, sends no call anywhere else."""
    from embry0.services.slop_meter.events import ReaderPlace

    forge = ElsewhereLinks(250)
    assert await _one_run(forge, 1_000, None) is None
    pulls = [params.get("page", 1) for path, params in forge.calls if path == f"/repos/{REPO}/pulls"]
    assert pulls == [1, 2], f"the listing's pages, asked for by number on its path: {pulls}"

    url = f"{ELSEWHERE}/repos/{REPO}/pulls?page=2"
    caps = {"max_pages": 2, "max_pulls": 1_000, "max_logins": 0}
    assert ReaderPlace.from_json({"stage": 2, "pages": 1, "next_page": url}, **caps) is None, "a URL is no page"
    assert ReaderPlace.from_json({"stage": 2, "pages": 1, "next_page": True}, **caps) is None
    place = ReaderPlace.from_json({"stage": 2, "pages": 1, "next_url": url, "next_page": 2}, **caps)
    assert place is not None and place.next_page == 2 and "next_url" not in place.to_json(), place
    forge = ElsewhereLinks(250)
    assert await _one_run(forge, 1_000, place) is None
    assert forge.calls and all(path.startswith(f"/repos/{REPO}/") for path, _params in forge.calls), forge.calls
    assert ELSEWHERE not in repr(forge.calls), f"a call named the stored URL: {forge.calls}"
    assert forge.calls[0] == (
        f"/repos/{REPO}/pulls",
        {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100, "page": 2},
    ), forge.calls[0]


async def test_a_next_link_that_names_no_page_cuts_the_listing_there() -> None:
    """A next page this reader cannot ask for by number (a link with a cursor, say) is not followed: the listing
    stops there, as at the page cap, and the read says more was waiting."""

    class CursorLinks(FakeForge):
        async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
            response = await super().get(path, repo=repo, params=params)
            if path == f"/repos/{REPO}/issues/comments":
                response.links = {"next": {"url": f"{FORGE}{path}?after=cursor-1"}}
            return response

    for links, more in ((CursorLinks, True), (FakeForge, False)):
        forge = links()
        reader = EventReader(
            forge,
            REPO,
            comments_since=None,
            review_comments_since=None,
            reviews_since=None,
            max_pages=10,
            max_pulls=10,
            max_logins=10,
        )
        await _read_all(reader, ApiBudget(100, 0))
        assert reader.batch.more is more, f"{links.__name__}: more is {reader.batch.more}"
    forge = CursorLinks()
    await _read_all(
        EventReader(
            forge, REPO, comments_since=None, review_comments_since=None, reviews_since=None, max_pages=10, max_pulls=10
        ),
        ApiBudget(100, 0),
    )
    comments = [params for path, params in forge.calls if path == f"/repos/{REPO}/issues/comments"]
    assert comments == [{"sort": "updated", "direction": "asc", "per_page": 100}], comments
    assert all(path.startswith(f"/repos/{REPO}/") for path, _params in forge.calls), (
        f"a link was fetched: {forge.calls}"
    )


class SamePage(ShiftingPulls):
    """A pull request listing whose every page brings the first page again, all found already: shifts without end."""

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        if path == f"/repos/{REPO}/pulls" and int((params or {}).get("page", 1)) > 1:
            first = await super().get(path, repo=repo, params={**(params or {}), "page": 1})
            return FakeResponse(200, first.json(), next_url=f"{FORGE}{path}?page={int(params['page']) + 1}")
        return await super().get(path, repo=repo, params=params)


async def test_a_listing_that_brings_only_pull_requests_already_found_ends_after_twice_the_page_cap() -> None:
    """However many pages the shifts bring again, a pass reads at most twice its page cap of the listing."""
    forge = SamePage(250)
    assert await _one_run(forge, 1_000, None) is None, "the pass ends"
    assert forge.pages == 4, f"a page cap of 2 reads at most 4 pages: {forge.pages}"
    assert sorted(forge.reviewed) == list(range(151, 251)), "the one page found is reviewed, each once"
