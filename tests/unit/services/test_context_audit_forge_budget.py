"""The context audit spends the forge's rate limit sparingly, and leaves its last calls to the jobs (#170).

A head read sends the ETag of the last one; a branch that has not moved
answers 304, which GitHub does not count, and nothing more is read. Every
answer's rate headers are kept per credential: once the calls left fall
below ``context_audit.rate_reserve_percent`` of the credential's limit, the
organizations reading with it are deferred, no paid read and no start,
until the window ends: the reset the forge named, or fifteen minutes when
it named none this host can use, after which one call asks again.

The forge here counts what each call costs as GitHub does. The
organizations keep ``context_audit_days`` on their rows, which every release
reads (this one until a layer imported it), so a tree without the reserve
fails these tests on the forge calls alone.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from embry0.services import context_audit as ca
from embry0.services.budget_policy import BudgetView
from embry0.services.organizations import MOVED_SETTINGS, Organization
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
HEAD, MOVED = "1" * 40, "2" * 40


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Resp:
    def __init__(self, status_code: int, body: Any, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = httpx.Headers(headers)

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class RatedForge:
    """GitHub's commit listing and tree API for one credential, with its rate limit.

    Every answer carries ``X-RateLimit-Remaining``, and ``X-RateLimit-Limit``
    and ``X-RateLimit-Reset`` unless ``limit`` or ``reset`` is None. A commit
    listing carries its ETag unless ``etags`` is false, and one asked with
    that ETag in ``If-None-Match`` is a 304 that costs nothing; every other
    answer costs one call. ``calls`` is each path with the status it
    answered. Each call yields once first, so reads made at once interleave.
    """

    def __init__(
        self,
        remaining: int = 5000,
        reset: datetime | None = T0 + timedelta(hours=1),
        *,
        limit: int | None = 5000,
        etags: bool = True,
    ) -> None:
        self.repos: dict[str, tuple[str, dict[str, str]]] = {}
        self.remaining = remaining
        self.reset = reset
        self.limit = limit
        self.etags = etags
        #: Every answer without its rate headers, as a proxy that strips them would give it.
        self.headers_off = False
        self.calls: list[tuple[str, int]] = []
        self.sent: list[str | None] = []

    def put(self, repo: str, head: str, files: dict[str, str]) -> None:
        self.repos[repo] = (head, dict(files))

    def _headers(self, **extra: str) -> dict[str, str]:
        if self.headers_off:
            return dict(extra)
        headers = {"X-RateLimit-Remaining": str(self.remaining), **extra}
        if self.limit is not None:
            headers["X-RateLimit-Limit"] = str(self.limit)
        if self.reset is not None:
            headers["X-RateLimit-Reset"] = str(int(self.reset.timestamp()))
        return headers

    async def get(self, path: str, **kwargs: Any) -> Resp:
        await asyncio.sleep(0)
        parts = path.split("/")
        repo = f"{parts[2]}/{parts[3]}"
        head, files = self.repos[repo]
        if parts[4] == "commits":
            etag = f'W/"{head}"'
            sent = (kwargs.get("extra_headers") or {}).get("If-None-Match")
            self.sent.append(sent)
            if self.etags and sent == etag:
                self.calls.append((path, 304))
                return Resp(304, None, self._headers(ETag=etag))
            self.remaining -= 1
            self.calls.append((path, 200))
            tagged = {"ETag": etag} if self.etags else {}
            return Resp(200, [{"sha": head, "commit": {"tree": {"sha": f"tree-{head}"}}}], self._headers(**tagged))
        self.remaining -= 1
        self.calls.append((path, 200))
        entries = [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]
        return Resp(200, {"sha": f"tree-{head}", "truncated": False, "tree": entries}, self._headers())

    def paid(self) -> int:
        """The calls answered 200: what the reads cost."""
        return sum(1 for _, status in self.calls if status == 200)


class Jobs:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def newest_per_repo(
        self, repos: Any, *, pipeline_template: str, dispatched_by: Any = None, exclude_dispatched_by: Any = ()
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for repo in repos:
            mine = [r for r in self.rows if r["repo"] == repo]
            if mine:
                out[repo] = {**mine[-1], "live": False}
        return out

    async def result_summaries(self, job_ids: Any) -> dict[str, Any]:
        return {}


class Documents:
    """The deployment's document and each organization's layer, changed in place between wakes."""

    def __init__(self, **deployment: Any) -> None:
        self.data: dict[str, dict[str, Any]] = {"deployment": {"context_audit": dict(deployment)}}

    def snapshot(self) -> Any:
        rows = [{"scope": s, "data": d, "generation": 1, "updated_at": None} for s, d in self.data.items()]
        return build_snapshot(CATALOG, rows, token=(), log=False)


class Audits:
    """The loop over a forge per credential, its jobs recorded, every start a job that finished at once."""

    def __init__(self, orgs: list[Organization], repos: list[str], forges: dict[str, RatedForge], docs: Documents):
        self.clock = Clock()
        self.jobs = Jobs()
        self.docs = docs
        self.started: list[str] = []
        #: The organizations whose credential was resolved into a client, in order.
        self.resolved: list[str] = []

        async def view(organizations: Any = ()) -> BudgetView:
            return BudgetView(docs.snapshot(), moved=MOVED_SETTINGS)

        async def start_job(context: ca.RepoContext, budget_usd: float, open_pr: bool, run_with: Any = None) -> str:
            self.started.append(context.repo)
            self.jobs.rows.append(
                {
                    "job_id": f"job-{len(self.jobs.rows)}",
                    "repo": context.repo,
                    "status": "completed",
                    "created_at": self.clock(),
                    "finished_at": self.clock(),
                    "pipeline_template": ca.TEMPLATE,
                    "dispatched_by": ca.ACTOR,
                    "pipeline_config": {"context_fingerprint": context.fingerprint, "head_sha": context.head_sha},
                }
            )
            return str(self.jobs.rows[-1]["job_id"])

        async def client_for(org: Organization) -> RatedForge:
            self.resolved.append(org.owner)
            return forges[org.credential_id or org.owner]

        async def gate() -> None:
            return None

        class Orgs:
            async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
                return orgs

        class Registry:
            async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
                return [{"repo": repo, "forge": "github", "active": True} for repo in repos]

        self.loop = ca.ContextAuditLoop(
            organizations=Orgs(),
            registry=Registry(),
            jobs=self.jobs,
            client_for=client_for,
            start_job=start_job,
            ask_gate=gate,
            budget=view,
            clock=self.clock,
        )


def _org(owner: str, credential: str | None = None) -> Organization:
    return Organization(owner=owner, forge="github", credential_id=credential, settings={"context_audit_days": 7})


FILES = {"CLAUDE.md": "a" * 40, "docs/AGENTS.md": "b" * 40}


# ---- If-None-Match on the head read -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_head_read_answered_304_makes_no_further_call_and_costs_nothing() -> None:
    forge = RatedForge()
    forge.put("acme/widgets", HEAD, FILES)
    world = Audits([_org("acme")], ["acme/widgets"], {"acme": forge}, Documents())
    assert await world.loop.tick() == ["acme/widgets"]
    assert forge.paid() == 2 and forge.remaining == 4998, "the first read: the head, then the tree"

    world.clock.now = T0 + timedelta(minutes=15)
    assert await world.loop.tick() == [], "nothing moved, nothing is due"
    assert forge.sent[-1] == f'W/"{HEAD}"', "the second head read did not send the first one's ETag"
    assert forge.calls[2:] == [("/repos/acme/widgets/commits", 304)], (
        f"a branch that did not move is one 304 and nothing more: {forge.calls[2:]}"
    )
    assert forge.remaining == 4998, "a 304 costs nothing against the limit"
    survey = await world.loop.survey(_org("acme"), refresh=False)
    assert survey.repos[0].context.files == ("CLAUDE.md", "docs/AGENTS.md"), "the last reading stands"


@pytest.mark.asyncio
async def test_a_moved_branch_is_read_again_and_new_rules_read_the_kept_tree_once() -> None:
    forge = RatedForge()
    forge.put("acme/widgets", HEAD, FILES)
    docs = Documents()
    world = Audits([_org("acme")], ["acme/widgets"], {"acme": forge}, docs)
    await world.loop.tick()
    world.clock.now = T0 + timedelta(minutes=15)
    await world.loop.tick()
    forge.put("acme/widgets", MOVED, {**FILES, "CONVENTIONS.md": "c" * 40})
    world.clock.now = T0 + timedelta(minutes=30)
    await world.loop.tick()
    assert forge.calls[3:] == [
        ("/repos/acme/widgets/commits", 200),
        (f"/repos/acme/widgets/git/trees/tree-{MOVED}", 200),
    ], f"a branch that moved is read again, head and tree: {forge.calls[3:]}"

    docs.data["deployment"]["context_audit"]["files"] = ["**/CONVENTIONS.md"]
    world.clock.now = T0 + timedelta(minutes=45)
    await world.loop.tick()
    assert forge.calls[5:] == [
        ("/repos/acme/widgets/commits", 304),
        (f"/repos/acme/widgets/git/trees/tree-{MOVED}", 200),
    ], f"new rules on a branch that did not move read its tree once, by the kept sha: {forge.calls[5:]}"
    world.clock.now = T0 + timedelta(minutes=60)
    await world.loop.tick()
    assert forge.calls[7:] == [("/repos/acme/widgets/commits", 304)]


# ---- the rate reserve ---------------------------------------------------------------------------

REPOS = [f"acme/r{i}" for i in range(6)]
COMMITS = [f"/repos/{repo}/commits" for repo in REPOS]
HALF_HOUR = T0 + timedelta(minutes=30)


def _reserved(
    remaining: int,
    percent: int | None = None,
    *,
    reset: datetime | None = HALF_HOUR,
    limit: int | None = 5000,
    etags: bool = True,
) -> tuple[Audits, RatedForge]:
    """Six repositories of one organization read one at a time; ``percent`` None leaves the deployment's default."""
    forge = RatedForge(remaining=remaining, reset=reset, limit=limit, etags=etags)
    for repo in REPOS:
        forge.put(repo, HEAD, FILES)
    docs = Documents(read_concurrency=1)
    if percent is not None:
        docs.data["deployment"]["context_audit"]["rate_reserve_percent"] = percent
    return Audits([_org("acme")], REPOS, {"acme": forge}, docs), forge


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == event]


@pytest.mark.asyncio
async def test_below_the_default_share_an_organization_reads_and_starts_nothing_until_the_limit_resets() -> None:
    world, forge = _reserved(remaining=1003)
    with capture_logs() as logs:
        started = await world.loop.tick()
    assert forge.paid() == 4 and forge.remaining == 999, (
        f"by default the reads stop once the calls left fall below 20% of 5,000: {forge.calls}"
    )
    assert started == [], "a deferred organization starts no audit: its jobs would spend the same credential"
    reached = _events(logs, "context_audit_rate_reserve_reached")
    assert [(e["owner"], e["remaining"], e["limit"], e["reserve_percent"]) for e in reached] == [
        ("acme", 999, 5000, 20)
    ]

    world.clock.now = T0 + timedelta(minutes=15)
    with capture_logs() as logs:
        assert await world.loop.tick() == []
    assert forge.calls[4:] == [(COMMITS[0], 304), (COMMITS[1], 304)], (
        f"before the reset only the head reads that can answer 304 go out, and they cost nothing: {forge.calls[4:]}"
    )
    assert forge.paid() == 4
    assert not _events(logs, "context_audit_rate_reserve_reached"), "one warning per window"

    world.clock.now = T0 + timedelta(minutes=31)
    forge.remaining, forge.reset = 5000, T0 + timedelta(hours=2)
    assert await world.loop.tick() == REPOS[:3], "after the reset it reads and starts again, at the owner's cap"


@pytest.mark.asyncio
async def test_a_token_with_a_smaller_limit_keeps_the_same_share() -> None:
    world, forge = _reserved(remaining=900, limit=1000)
    assert await world.loop.tick() == REPOS[:3], (
        "a 1,000-an-hour token with 900 left is far above 20% of its limit: nothing is deferred"
    )
    assert forge.paid() == 12 and forge.remaining == 888

    world, forge = _reserved(remaining=205, limit=1000)
    assert await world.loop.tick() == [], "below 20% of its own limit the same token defers: nothing starts"
    assert forge.paid() == 6 and forge.remaining == 199, (
        f"the same token stops below 200, 20% of its own limit: {forge.calls}"
    )


@pytest.mark.asyncio
async def test_with_no_reset_named_the_low_state_ends_after_15_minutes_and_one_probe_asks_again() -> None:
    world, forge = _reserved(remaining=1003, reset=None, etags=False)
    assert await world.loop.tick() == [] and forge.paid() == 4, (
        f"with no reset named, the reads still stop below the share: {forge.calls}"
    )

    world.clock.now = T0 + timedelta(minutes=14)
    assert await world.loop.tick() == []
    assert len(forge.calls) == 4, f"within 15 minutes of the answer, not one call: {forge.calls[4:]}"

    world.clock.now = T0 + timedelta(minutes=16)
    with capture_logs() as logs:
        assert await world.loop.tick() == []
    assert forge.calls[4:] == [(COMMITS[0], 200)], (
        f"after 15 minutes one call, the probe, asks again; still low, it closes the window again: {forge.calls[4:]}"
    )
    probes = _events(logs, "context_audit_rate_reserve_probe")
    assert [(e["log_level"], e["owner"], e["remaining"]) for e in probes] == [("info", "acme", 999)], probes
    assert len(_events(logs, "context_audit_rate_reserve_reached")) == 1, "a new window, warned about once"

    world.clock.now = T0 + timedelta(minutes=20)
    with capture_logs() as logs:
        assert await world.loop.tick() == []
    assert len(forge.calls) == 5, "the probe's answer opened a new window: no call in it"
    assert not _events(logs, "context_audit_rate_reserve_probe"), "one probe line per window"
    assert not _events(logs, "context_audit_rate_reserve_reached"), "one warning per window"

    world.clock.now = T0 + timedelta(minutes=32)
    forge.remaining = 5000
    with capture_logs() as logs:
        assert await world.loop.tick() == REPOS[:3], "the forge's window ended unnamed: the probe finds it so"
    assert len(_events(logs, "context_audit_rate_reserve_probe")) == 1
    assert forge.calls[5] == (COMMITS[0], 200) and forge.paid() == 5 + 10, "two heads read again, four heads and trees"


@pytest.mark.asyncio
async def test_a_reset_already_past_by_this_clock_waits_15_minutes_and_a_far_one_an_hour_at_most() -> None:
    world, forge = _reserved(remaining=1003, reset=T0 - timedelta(minutes=5), etags=False)
    assert await world.loop.tick() == [] and forge.paid() == 4, (
        f"a reset this clock already passed is no licence to spend: the clocks disagree: {forge.calls}"
    )
    world.clock.now = T0 + timedelta(minutes=1)
    assert await world.loop.tick() == [] and len(forge.calls) == 4
    world.clock.now = T0 + timedelta(minutes=16)
    assert await world.loop.tick() == []
    assert forge.calls[4:] == [(COMMITS[0], 200)], f"15 minutes on, one probe: {forge.calls[4:]}"

    world, forge = _reserved(remaining=1003, reset=T0 + timedelta(days=1), etags=False)
    assert await world.loop.tick() == [] and forge.paid() == 4
    world.clock.now = T0 + timedelta(minutes=59)
    assert await world.loop.tick() == [] and len(forge.calls) == 4
    world.clock.now = T0 + timedelta(minutes=61)
    assert await world.loop.tick() == []
    assert forge.calls[4:] == [(COMMITS[0], 200)], f"a reset a day off waits an hour, then one probe: {forge.calls[4:]}"


@pytest.mark.asyncio
async def test_after_the_window_the_other_reads_wait_for_the_probe_s_answer() -> None:
    world, forge = _reserved(remaining=1003, reset=None, etags=False)
    assert await world.loop.tick() == [], "below the share nothing starts"
    world.docs.data["deployment"]["context_audit"]["read_concurrency"] = 4
    world.clock.now = T0 + timedelta(minutes=16)
    assert await world.loop.tick() == []
    assert forge.calls[4:] == [(COMMITS[0], 200)], (
        f"four reads at once, the forge still low: one probe, and the others see its answer and stay home: "
        f"{forge.calls[4:]}"
    )
    world.clock.now = T0 + timedelta(minutes=32)
    forge.remaining = 5000
    assert await world.loop.tick() == REPOS[:3], (
        "four reads at once: one probes, the others wait for its answer and go out, none deferred for it"
    )
    assert forge.paid() == 5 + 10, "the two read before: their heads again; the other four: head and tree"


@pytest.mark.asyncio
async def test_the_probe_is_logged_once_per_window_however_many_calls_it_takes() -> None:
    world, forge = _reserved(remaining=1003, reset=None, etags=False)
    assert await world.loop.tick() == [], "below the share nothing starts"
    world.clock.now = T0 + timedelta(minutes=16)
    forge.headers_off = True  # a proxy that strips them: every answer leaves the state as it was
    with capture_logs() as logs:
        assert await world.loop.tick() == REPOS[:3], "with no word on the calls left, nothing is held back"
    probes = _events(logs, "context_audit_rate_reserve_probe")
    assert len(probes) == 1, f"each call asked the forge again in the same window, logged once: {len(probes)} lines"


@pytest.mark.asyncio
async def test_free_reads_while_low_do_not_repeat_the_warning_at_every_wake() -> None:
    world, forge = _reserved(remaining=1003, reset=None)
    with capture_logs() as logs:
        await world.loop.tick()
    assert len(_events(logs, "context_audit_rate_reserve_reached")) == 1, "below the share: said once"
    for minutes in (5, 10):
        world.clock.now = T0 + timedelta(minutes=minutes)
        with capture_logs() as logs:
            assert await world.loop.tick() == []
        assert forge.calls[-2:] == [(COMMITS[0], 304), (COMMITS[1], 304)], (
            f"while low the two read before are read again, for free: {forge.calls[-2:]}"
        )
        assert not _events(logs, "context_audit_rate_reserve_reached"), (
            f"a free 304 naming no reset moves the window's end; the warning still waits out the one it gave "
            f"(at {minutes} minutes)"
        )
    world.clock.now = T0 + timedelta(minutes=20)
    with capture_logs() as logs:
        await world.loop.tick()
    assert len(_events(logs, "context_audit_rate_reserve_reached")) == 1, "still low past that window: said again"


@pytest.mark.asyncio
async def test_while_low_a_branch_that_did_not_move_is_read_free_and_one_that_moved_costs_one_call() -> None:
    world, forge = _reserved(remaining=1012)
    assert await world.loop.tick() == REPOS[:3] and forge.paid() == 12 and forge.remaining == 1000, (
        f"1,000 left of 5,000 is not below 20%: everything is read: {forge.calls}"
    )
    forge.remaining = 999  # the jobs spent one

    world.clock.now = T0 + timedelta(minutes=5)
    assert await world.loop.tick() == [], "three repositories due, every one read for free, none started while low"
    assert forge.calls[12:] == [(c, 304) for c in COMMITS], f"six heads, none moved: six free reads: {forge.calls[12:]}"

    forge.put(REPOS[2], MOVED, FILES)
    forge.put(REPOS[4], MOVED, FILES)
    world.clock.now = T0 + timedelta(minutes=15)
    survey = await world.loop.survey(_org("acme"))
    assert forge.calls[18:] == [(COMMITS[0], 304), (COMMITS[1], 304), (COMMITS[2], 200)], (
        f"a moved head costs one call, and that closes reading for the survey: {forge.calls[18:]}"
    )
    assert survey.deferred == tuple(REPOS[2:])

    world.clock.now = T0 + timedelta(minutes=16)
    survey = await world.loop.survey(_org("acme"))
    assert forge.calls[21:] == [(c, 304) for c in COMMITS[:4]] + [(COMMITS[4], 200)], (
        f"the next survey pays for the next moved head and no more: {forge.calls[21:]}"
    )
    assert survey.deferred == (REPOS[2], REPOS[4], REPOS[5])
    result = await world.loop.run(_org("acme"))
    assert result.started == ()
    assert sorted((s.repo, s.reason) for s in result.skipped) == [
        (REPOS[0], ca.CURRENT),
        (REPOS[1], ca.CURRENT),
        *((repo, ca.DEFERRED) for repo in REPOS[2:]),
    ], f"a run by hand reads for free while low, and starts nothing on what it read: {result.skipped}"


@pytest.mark.asyncio
async def test_a_read_and_a_run_by_hand_say_which_repositories_they_did_not_read() -> None:
    deferred = getattr(ca, "DEFERRED", None)
    assert deferred is not None, "no DEFERRED reason: a run by hand cannot say it left repositories unread"
    world, forge = _reserved(remaining=1003)
    survey = (await world.loop.survey(_org("acme"))).to_dict()
    assert survey["deferred"] == REPOS[2:], (
        f"two read before the share was reached, four deferred: {survey.get('deferred')}"
    )
    assert [r["repo"] for r in survey["repos"]] == REPOS[:2], "what was read before the reserve is shown"
    result = await world.loop.run(_org("acme"))
    assert [s.repo for s in result.started] == [], "nothing is read past the reserve, nor started on what was"
    assert sorted(s.repo for s in result.skipped if s.reason == deferred) == REPOS
    one = await world.loop.run(_org("acme"), repo=REPOS[4])
    assert [(s.repo, s.reason) for s in one.skipped] == [(REPOS[4], deferred)], (
        f"a repository not read is deferred, not one without instruction files: {one.skipped}"
    )


@pytest.mark.asyncio
async def test_the_reserve_is_kept_per_credential() -> None:
    shared = RatedForge(remaining=1001, reset=HALF_HOUR)
    other = RatedForge(remaining=5000)
    for repo in ("acme/a", "initech/a"):
        shared.put(repo, HEAD, FILES)
    other.put("globex/a", HEAD, FILES)
    orgs = [_org("acme", "cred-1"), _org("globex", "cred-2"), _org("initech", "cred-1")]
    world = Audits(orgs, ["acme/a", "globex/a", "initech/a"], {"cred-1": shared, "cred-2": other}, Documents())
    assert await world.loop.tick() == ["globex/a"], (
        "the credential down to its reserve defers both organizations reading with it; the other goes on"
    )
    assert [path for path, _ in shared.calls] == ["/repos/acme/a/commits", "/repos/acme/a/git/trees/tree-" + HEAD], (
        f"the second organization on the same credential makes no call: {shared.calls}"
    )
    assert world.resolved == ["acme", "globex"], (
        f"one with nothing that can answer 304 is deferred without its credential even resolved: {world.resolved}"
    )


@pytest.mark.asyncio
async def test_a_forge_that_sends_no_limit_is_never_deferred() -> None:
    world, forge = _reserved(remaining=12)
    assert await world.loop.tick() == [] and forge.paid() == 1, "12 calls left of 5,000: the first answer defers"
    world, forge = _reserved(remaining=12, limit=None)
    assert await world.loop.tick() == REPOS[:3], f"no limit header, no share of it to keep: {forge.calls}"
    assert forge.paid() == 12 and forge.remaining == 0, "with no limit to take a share of, nothing is kept back"


@pytest.mark.asyncio
async def test_a_share_of_0_never_defers() -> None:
    world, forge = _reserved(remaining=12, percent=1)
    assert await world.loop.tick() == [] and forge.paid() == 1, (
        "with 12 calls left of 5,000 and a share of 1%, the first answer defers the rest"
    )
    world, forge = _reserved(remaining=12, percent=0)
    assert await world.loop.tick() == REPOS[:3], f"a share of 0 keeps nothing back: {forge.calls}"
    assert forge.paid() == 12 and forge.remaining == 0, "a share of 0 spends the last call"
