"""The context audit on a schedule (ADR 0020): only due repositories start, never two at once, never past the cap."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from structlog.testing import capture_logs

from embry0.services import context_audit
from embry0.services.context_audit import (
    ACTOR,
    AGED,
    CHANGED,
    CURRENT,
    FIRST,
    LIVE,
    OFF,
    TEMPLATE,
    Attempt,
    ContextAuditLoop,
    ForgeReadError,
    RepoContext,
    audit_task,
    due_reason,
    fingerprint,
    job_budget_usd,
    read_blobs,
    read_head,
    start_context_audit_job,
)
from embry0.services.organizations import Organization

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
FP = "f" * 64
HEAD = "1" * 40


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Resp:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeForge:
    """GitHub's commit listing and tree API, as far as the audit reads them."""

    def __init__(self) -> None:
        self.repos: dict[str, tuple[str | None, dict[str, str]]] = {}
        self.calls: list[str] = []

    def put(self, repo: str, head: str | None, files: dict[str, str]) -> None:
        """A repository whose default branch is at ``head``; None for one with no commit."""
        self.repos[repo] = (head, dict(files))

    async def get(self, path: str, **kwargs: Any) -> Resp:
        self.calls.append(path)
        parts = path.split("/")
        repo = f"{parts[2]}/{parts[3]}"
        if repo not in self.repos:
            return Resp(404, {"message": "Not Found"})
        head, files = self.repos[repo]
        if head is None:
            return Resp(409, {"message": "Git Repository is empty."})
        if parts[4] == "commits":
            assert kwargs == {"params": {"per_page": 1}}, kwargs
            return Resp(200, [{"sha": head, "commit": {"tree": {"sha": f"tree-{head}"}}}])
        assert parts[4:6] == ["git", "trees"] and parts[6] == f"tree-{head}", path
        assert kwargs == {"params": {"recursive": "1"}}, kwargs
        entries = [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]
        return Resp(
            200, {"sha": f"tree-{head}", "truncated": False, "tree": [*entries, {"path": "src", "type": "tree"}]}
        )


class FakeOrgs:
    def __init__(self, *orgs: Organization) -> None:
        self.orgs = list(orgs)

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        return [o for o in self.orgs if include_removed or o.status != "removed"]


class FakeRegistry:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = list(rows)

    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        return sorted((r for r in self.rows if include_inactive or r["active"]), key=lambda r: r["repo"])


_LIVE = {"pending", "running", "awaiting_input", "paused"}


class FakeJobs:
    """The jobs table in memory: ``create`` as the repository takes it, ``newest_per_repo`` as it answers."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.rows: list[dict[str, Any]] = []

    def add(self, repo: str, status: str, created: datetime, finished: datetime | None = None, **config: Any) -> None:
        self.rows.append(
            {
                "job_id": f"job-{len(self.rows) + 1}",
                "repo": repo,
                "status": status,
                "created_at": created,
                "finished_at": finished,
                "pipeline_template": TEMPLATE,
                "pipeline_config": config,
                "dispatched_by": None,
            }
        )

    async def create(self, **kw: Any) -> str:
        self.add(kw["repo"], "pending", self.clock(), **kw["pipeline_config"])
        self.rows[-1].update(dispatched_by=kw["dispatched_by"], task=kw["task"], created_with=kw)
        return str(self.rows[-1]["job_id"])

    async def newest_per_repo(
        self, repos: Collection[str], *, pipeline_template: str, dispatched_by: str | None = None
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for repo in repos:
            mine = [
                r
                for r in self.rows
                if r["repo"] == repo
                and (
                    r["pipeline_template"] == pipeline_template
                    or (dispatched_by and r["dispatched_by"] == dispatched_by)
                )
            ]
            if mine:
                newest = max(mine, key=lambda r: r["created_at"])
                out[repo] = {**newest, "live": any(r["status"] in _LIVE for r in mine)}
        return out

    def finish(self, repo: str, status: str, when: datetime) -> None:
        row = next(r for r in reversed(self.rows) if r["repo"] == repo)
        row.update(status=status, finished_at=when)


class FakeExecutor:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any]]] = []

    def submit_job(self, job_id: str, **kw: Any) -> None:
        self.submitted.append((job_id, kw))


class FakeTemplates:
    def __init__(self, *names: str) -> None:
        self.names = list(names)

    async def list_all(self) -> list[dict[str, Any]]:
        return [{"id": f"pt-{i}", "name": name} for i, name in enumerate(self.names)]


def org(owner: str = "acme", days: Any = 7, *, forge: str = "github", status: str = "active") -> Organization:
    settings = {} if days is None else {"context_audit_days": days}
    return Organization(owner=owner, forge=forge, settings=settings, status=status)


def reg(repo: str, *, active: bool = True, forge: str = "github") -> dict[str, Any]:
    return {"repo": repo, "forge": forge, "active": active}


class World:
    """The loop over the fakes, its jobs started through the real start path."""

    def __init__(
        self,
        *orgs: Organization,
        repos: tuple[dict[str, Any], ...] = (),
        cap: int = 3,
        templates: tuple[str, ...] = (TEMPLATE,),
        credential_error: Exception | None = None,
    ) -> None:
        self.clock = Clock(T0)
        self.forge = FakeForge()
        self.jobs = FakeJobs(self.clock)
        self.executor = FakeExecutor()
        self.templates = FakeTemplates(*templates)
        self.attempts: list[str] = []
        self.credential_error = credential_error

        async def client_for(organization: Organization) -> FakeForge:
            if self.credential_error is not None:
                raise self.credential_error
            return self.forge

        async def start_job(context: RepoContext) -> str:
            self.attempts.append(context.repo)
            return await start_context_audit_job(
                context, jobs=self.jobs, executor=self.executor, templates=self.templates, budget_usd=2.0
            )

        self.loop = ContextAuditLoop(
            organizations=FakeOrgs(*orgs),
            registry=FakeRegistry(*repos),
            jobs=self.jobs,
            client_for=client_for,
            start_job=start_job,
            max_jobs_per_tick=cap,
            clock=self.clock,
        )

    def fp(self, repo: str) -> str:
        _head, files = self.forge.repos[repo]
        return fingerprint({p: s for p, s in files.items() if p.endswith(".md")})


# ---- the fingerprint ----


def test_the_fingerprint_is_sha256_over_sorted_path_tab_blob_lines() -> None:
    blobs = {"docs/AGENTS.md": "b2" * 20, "CLAUDE.md": "a1" * 20}
    expected = hashlib.sha256(f"CLAUDE.md\t{'a1' * 20}\ndocs/AGENTS.md\t{'b2' * 20}".encode()).hexdigest()
    assert fingerprint(blobs) == expected == "398e38729769e55fc47bcf3d7abafe7ad9930e9b4905facd4075c14904031058"
    assert fingerprint(dict(reversed(list(blobs.items())))) == expected, (
        "the order the forge lists files in is not read"
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"docs/AGENTS.md": "b2" * 20, "CLAUDE.md": "c3" * 20},  # an edit
        {"docs/AGENTS.md": "b2" * 20, "AGENTS.md": "a1" * 20},  # a rename, same content
        {"docs/AGENTS.md": "b2" * 20},  # a removal
        {"docs/AGENTS.md": "b2" * 20, "CLAUDE.md": "a1" * 20, "HANDOFF.md": "d4" * 20},  # an addition
    ],
)
def test_the_fingerprint_changes_with_any_instruction_file(changed: dict[str, str]) -> None:
    assert fingerprint(changed) != fingerprint({"docs/AGENTS.md": "b2" * 20, "CLAUDE.md": "a1" * 20})


# ---- the due rule ----


def ctx(fp: str = FP, head: str = HEAD) -> RepoContext:
    return RepoContext(repo="acme/web", head_sha=head, files=("CLAUDE.md",), fingerprint=fp)


def done(days_ago: float, *, fp: str | None = FP, head: str | None = HEAD) -> Attempt:
    when = T0 - timedelta(days=days_ago)
    return Attempt(started_at=when - timedelta(minutes=10), finished_at=when, fingerprint=fp, head_sha=head)


def tried(days_ago: float, *, fp: str | None = FP, head: str | None = HEAD) -> Attempt:
    """A failed, cancelled or refused attempt: no finish time counts."""
    return Attempt(started_at=T0 - timedelta(days=days_ago), fingerprint=fp, head_sha=head)


@pytest.mark.parametrize(
    ("newest", "current", "days", "live", "reason"),
    [
        (None, ctx(), 7, False, FIRST),
        (done(1), ctx(fp="e" * 64), 7, False, CHANGED),
        (done(1), ctx(), 7, False, CURRENT),
        (done(8), ctx(head="2" * 40), 7, False, AGED),
        (done(8), ctx(), 7, False, CURRENT),  # old, but nothing moved
        (done(6.9), ctx(head="2" * 40), 7, False, CURRENT),  # moved, but not old enough
        (done(7), ctx(head="2" * 40), 7, False, AGED),  # exactly the interval
        # A failed newest job is an attempt: anchored on its start, as rediscovery's refusals are.
        (tried(1), ctx(), 7, False, CURRENT),
        (tried(1), ctx(head="2" * 40), 7, False, CURRENT),
        (tried(7), ctx(head="2" * 40), 7, False, AGED),
        (tried(1), ctx(fp="e" * 64), 7, False, CHANGED),
        # A job a person started records no fingerprint: only its age is read.
        (done(1, fp=None, head=None), ctx(), 7, False, CURRENT),
        (done(8, fp=None, head=None), ctx(), 7, False, AGED),
        # Never due while one is pending, running, awaiting input or paused, whatever else holds.
        (None, ctx(), 7, True, LIVE),
        (done(30), ctx(fp="e" * 64, head="2" * 40), 7, True, LIVE),
        # The setting off says off before anything else.
        (None, ctx(), None, False, OFF),
        (done(30), ctx(fp="e" * 64), None, True, OFF),
    ],
)
def test_due_reason(newest: Attempt | None, current: RepoContext, days: int | None, live: bool, reason: str) -> None:
    assert due_reason(newest, current, days=days, now=T0, live=live) == reason


def test_an_attempt_reads_the_job_row_it_came_from() -> None:
    row = {
        "status": "completed",
        "created_at": (T0 - timedelta(hours=1)).replace(tzinfo=None),
        "finished_at": T0.isoformat(),
        "pipeline_config": {"context_fingerprint": FP, "head_sha": HEAD, "max_budget_usd": 2.0},
    }
    assert Attempt.from_job(row) == Attempt(T0 - timedelta(hours=1), T0, FP, HEAD)
    failed = {**row, "status": "failed"}
    assert Attempt.from_job(failed) == Attempt(T0 - timedelta(hours=1), None, FP, HEAD), "only a completion finishes"
    bare = {"status": "running", "created_at": T0, "pipeline_config": None}
    assert Attempt.from_job(bare) == Attempt(T0)
    assert Attempt.from_job({"status": "pending"}) is None


# ---- reading the forge ----


@pytest.mark.asyncio
async def test_read_head_and_read_blobs_read_the_default_branch() -> None:
    forge = FakeForge()
    forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40, "src/app.py": "b" * 40})
    head = await read_head(forge, "acme/web")
    assert head == (HEAD, f"tree-{HEAD}")
    assert await read_blobs(forge, "acme/web", head[1]) == {"CLAUDE.md": "a" * 40, "src/app.py": "b" * 40}


class Scripted:
    def __init__(self, *answers: Resp) -> None:
        self.answers = list(answers)

    async def get(self, path: str, **kwargs: Any) -> Resp:
        return self.answers.pop(0)


@pytest.mark.asyncio
async def test_an_empty_repository_has_no_head_and_a_refusal_is_an_error() -> None:
    assert await read_head(Scripted(Resp(409, {"message": "Git Repository is empty."})), "acme/new") is None
    assert await read_head(Scripted(Resp(200, [])), "acme/new") is None
    with pytest.raises(ForgeReadError, match="answered 404"):
        await read_head(Scripted(Resp(404, {"message": "Not Found"})), "acme/gone")
    with pytest.raises(ForgeReadError, match="no sha or tree"):
        await read_head(Scripted(Resp(200, [{"sha": HEAD}])), "acme/odd")


@pytest.mark.asyncio
async def test_a_truncated_tree_is_refused_rather_than_fingerprinted_in_part() -> None:
    truncated = Resp(200, {"tree": [{"path": "CLAUDE.md", "type": "blob", "sha": "a" * 40}], "truncated": True})
    with pytest.raises(ForgeReadError, match="larger than one read"):
        await read_blobs(Scripted(truncated), "acme/huge", "t")
    with pytest.raises(ForgeReadError, match="answered 403"):
        await read_blobs(Scripted(Resp(403, {"message": "rate limited"})), "acme/web", "t")


# ---- the loop ----


@pytest.mark.asyncio
async def test_a_tick_starts_jobs_for_due_repositories_only() -> None:
    world = World(
        org("acme"),
        org("off", days=None),
        org("paused", status="paused"),
        org("text", days="7"),  # what POST /orgs could have stored unvalidated: not a schedule
        repos=(
            reg("acme/fresh"),
            reg("acme/never"),
            reg("acme/edited"),
            reg("acme/running"),
            reg("acme/plain"),
            reg("acme/empty"),
            reg("acme/retired", active=False),
            reg("off/web"),
            reg("paused/web"),
            reg("text/web"),
        ),
    )
    for repo in ("acme/fresh", "acme/never", "acme/edited", "acme/running", "acme/retired"):
        world.forge.put(repo, HEAD, {"CLAUDE.md": "a" * 40, ".claude/skills/x/SKILL.md": "c" * 40})
    world.forge.put("acme/plain", HEAD, {"README.md": "a" * 40, "src/app.py": "b" * 40})  # no instruction file
    world.forge.put("acme/empty", None, {})  # no commit yet
    for repo in ("off/web", "paused/web", "text/web"):
        world.forge.put(repo, HEAD, {"CLAUDE.md": "a" * 40})
    world.jobs.add(
        "acme/fresh",
        "completed",
        T0 - timedelta(days=1),
        T0 - timedelta(days=1),
        context_fingerprint=world.fp("acme/fresh"),
        head_sha=HEAD,
    )
    world.jobs.add(
        "acme/edited",
        "completed",
        T0 - timedelta(days=1),
        T0 - timedelta(days=1),
        context_fingerprint="0" * 64,
        head_sha=HEAD,
    )
    world.jobs.add("acme/running", "running", T0 - timedelta(days=30), context_fingerprint="0" * 64, head_sha="0" * 40)

    assert await world.loop.tick() == ["acme/edited", "acme/never"]
    assert world.attempts == ["acme/edited", "acme/never"]
    assert "/repos/acme/empty/commits" in world.forge.calls
    assert not any(call.startswith("/repos/acme/empty/git") for call in world.forge.calls)
    assert not any(
        c.startswith(("/repos/off/", "/repos/paused/", "/repos/text/", "/repos/acme/retired"))
        for c in world.forge.calls
    )
    job_id, submitted = world.executor.submitted[-1]
    assert submitted["repo"] == "acme/never" and submitted["kind"] == "context_audit"
    assert "- .claude/skills/x/SKILL.md" in submitted["body"] and "- CLAUDE.md" in submitted["body"]


@pytest.mark.asyncio
async def test_the_cap_holds_per_tick_and_the_rest_wait_for_the_next() -> None:
    repos = tuple(reg(f"acme/r{i}") for i in range(5)) + (reg("beta/web"),)
    world = World(org("acme"), org("beta"), repos=repos, cap=3)
    for row in repos:
        world.forge.put(row["repo"], HEAD, {"AGENTS.md": "a" * 40})
    assert await world.loop.tick() == ["acme/r0", "acme/r1", "acme/r2"]
    assert not any(c.startswith("/repos/beta/") for c in world.forge.calls), "an owner past the cap is not read"
    world.clock.now = T0 + timedelta(minutes=15)
    assert await world.loop.tick() == ["acme/r3", "acme/r4", "beta/web"], "the started ones are live, the rest go"
    world.clock.now = T0 + timedelta(minutes=30)
    assert await world.loop.tick() == []


@pytest.mark.asyncio
async def test_a_cap_of_zero_starts_nothing_and_reads_nothing() -> None:
    world = World(org("acme"), repos=(reg("acme/web"),), cap=0)
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == []
    assert world.forge.calls == []


@pytest.mark.asyncio
async def test_a_repository_is_never_audited_twice_at_once_and_waits_its_interval() -> None:
    world = World(org("acme", days=7), repos=(reg("acme/web"),))
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == ["acme/web"]
    world.clock.now = T0 + timedelta(minutes=15)
    assert await world.loop.tick() == [], "a job in progress is never joined by a second"
    world.jobs.finish("acme/web", "completed", T0 + timedelta(hours=1))
    world.forge.put("acme/web", "2" * 40, {"CLAUDE.md": "a" * 40, "src/app.py": "b" * 40})
    world.clock.now = T0 + timedelta(days=7, minutes=59)
    assert await world.loop.tick() == [], "the code moved, but the audit is not an interval old"
    world.clock.now = T0 + timedelta(days=7, hours=1)
    assert await world.loop.tick() == ["acme/web"], "an interval after it finished, with the branch moved"


@pytest.mark.asyncio
async def test_an_edited_instruction_file_is_due_at_once() -> None:
    world = World(org("acme", days=30), repos=(reg("acme/web"),))
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == ["acme/web"]
    world.jobs.finish("acme/web", "completed", T0 + timedelta(minutes=20))
    world.clock.now = T0 + timedelta(hours=1)
    world.forge.put("acme/web", "2" * 40, {"CLAUDE.md": "a" * 40, "src/app.py": "b" * 40})
    assert await world.loop.tick() == [], "a commit that touched no instruction file changes nothing"
    world.forge.put("acme/web", "3" * 40, {"CLAUDE.md": "e" * 40, "src/app.py": "b" * 40})
    assert await world.loop.tick() == ["acme/web"]
    created = world.jobs.rows[-1]["created_with"]
    assert created["pipeline_config"]["head_sha"] == "3" * 40


@pytest.mark.asyncio
async def test_a_failed_audit_waits_a_full_interval_from_its_start() -> None:
    world = World(org("acme", days=7), repos=(reg("acme/web"),))
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == ["acme/web"]
    world.jobs.finish("acme/web", "failed", T0 + timedelta(minutes=2))
    world.forge.put("acme/web", "2" * 40, {"CLAUDE.md": "a" * 40, "src/app.py": "b" * 40})
    for minutes in (15, 30, 45, 60 * 24):
        world.clock.now = T0 + timedelta(minutes=minutes)
        assert await world.loop.tick() == [], "no retry storm"
    world.clock.now = T0 + timedelta(days=7)
    assert await world.loop.tick() == ["acme/web"]


@pytest.mark.asyncio
async def test_a_missing_template_refuses_the_start_cleanly_and_once_per_interval() -> None:
    """The other half of the feature, the template, may not be there yet: the
    start is refused before any job row exists, the loop goes on with the
    next repository, and the refusal is remembered as an attempt."""
    world = World(org("acme", days=7), repos=(reg("acme/a"), reg("acme/b")), templates=("analysis-default",))
    for repo in ("acme/a", "acme/b"):
        world.forge.put(repo, HEAD, {"CLAUDE.md": "a" * 40})
    for minutes in (0, 15, 30):
        world.clock.now = T0 + timedelta(minutes=minutes)
        assert await world.loop.tick() == []
    assert world.attempts == ["acme/a", "acme/b"], "one attempt each, not one per tick"
    assert world.jobs.rows == [] and world.executor.submitted == [], "a refused start leaves no job"
    survey = await world.loop.survey(org("acme", days=7))
    assert [(s.context.repo, s.reason, s.refused_at) for s in survey.repos] == [
        ("acme/a", CURRENT, T0),
        ("acme/b", CURRENT, T0),
    ]
    world.templates.names.append(TEMPLATE)  # the template ships
    world.clock.now = T0 + timedelta(hours=1)
    world.forge.put("acme/b", "2" * 40, {"CLAUDE.md": "e" * 40})
    assert await world.loop.tick() == ["acme/b"], "a change to the files is due at once, refusal or not"
    world.clock.now = T0 + timedelta(days=7)
    world.forge.put("acme/a", "2" * 40, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == ["acme/a"], "and the rest one interval after the refusal"


@pytest.mark.asyncio
async def test_one_owner_or_repository_failing_does_not_stop_the_others() -> None:
    world = World(
        org("acme"),
        org("lab", forge="gitlab"),
        org("zeta"),
        repos=(reg("acme/gone"), reg("acme/web"), reg("lab/web", forge="gitlab"), reg("zeta/web")),
    )
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    world.forge.put("zeta/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == ["acme/web", "zeta/web"], "acme/gone answers 404, the GitLab owner is skipped"
    assert not any(c.startswith("/repos/lab/") for c in world.forge.calls)
    survey = await world.loop.survey(org("acme"))
    assert survey.unread == (("acme/gone", "reading the newest commit answered 404"),)


@pytest.mark.asyncio
async def test_an_owner_whose_credential_cannot_be_resolved_is_skipped() -> None:
    world = World(
        org("acme"),
        repos=(reg("acme/web"),),
        credential_error=HTTPException(status_code=422, detail="no github credential for acme"),
    )
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    assert await world.loop.tick() == []
    world.credential_error = None  # a person bound a working credential
    assert await world.loop.tick() == ["acme/web"]


@pytest.mark.asyncio
async def test_a_standing_problem_is_one_log_line_not_one_per_tick() -> None:
    world = World(
        org("acme"),
        repos=(reg("acme/gone"), reg("acme/web")),
        credential_error=HTTPException(status_code=422, detail="no github credential for acme"),
    )
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    with capture_logs() as logs:
        for _ in range(3):
            await world.loop.tick()
        world.credential_error = HTTPException(status_code=422, detail="credential cred-1 is disabled")
        await world.loop.tick()
        world.credential_error = None
        for _ in range(3):
            await world.loop.tick()
        world.forge.put("acme/gone", None, {})  # it answers again, with no commit
        await world.loop.tick()
        del world.forge.repos["acme/gone"]
        await world.loop.tick()
    owner = [e["detail"] for e in logs if e["event"] == "context_audit_owner_skipped"]
    assert owner == ["no github credential for acme", "credential cred-1 is disabled"]
    unread = [e["repo"] for e in logs if e["event"] == "context_audit_repo_unread"]
    assert unread == ["acme/gone", "acme/gone"], "logged once, and again after it recovered and failed anew"


@pytest.mark.asyncio
async def test_a_head_that_did_not_move_is_not_read_again() -> None:
    world = World(org("acme"), repos=(reg("acme/web"),))
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40})
    await world.loop.tick()
    world.clock.now = T0 + timedelta(minutes=15)
    await world.loop.tick()
    assert world.forge.calls == [
        "/repos/acme/web/commits",
        f"/repos/acme/web/git/trees/tree-{HEAD}",
        "/repos/acme/web/commits",
    ]


@pytest.mark.asyncio
async def test_the_survey_says_per_repository_what_the_read_endpoint_shows() -> None:
    world = World(org("acme", days=None), repos=(reg("acme/web"), reg("acme/plain")))
    world.forge.put("acme/web", HEAD, {"CLAUDE.md": "a" * 40, "docs/HANDOFF.md": "b" * 40})
    world.forge.put("acme/plain", HEAD, {"README.md": "a" * 40})
    world.jobs.add(
        "acme/web", "completed", T0 - timedelta(days=2), T0 - timedelta(days=2), context_fingerprint="0" * 64
    )
    survey = (await world.loop.survey(org("acme", days=None))).to_dict()
    assert survey == {
        "owner": "acme",
        "context_audit_days": None,
        "template": TEMPLATE,
        "repos": [
            {
                "repo": "acme/web",
                "files": 2,
                "fingerprint": fingerprint({"CLAUDE.md": "a" * 40, "docs/HANDOFF.md": "b" * 40}),
                "head_sha": HEAD,
                "newest_job": {
                    "job_id": "job-1",
                    "status": "completed",
                    "created_at": (T0 - timedelta(days=2)).isoformat(),
                },
                "refused_at": None,
                "due": False,
                "reason": OFF,
            }
        ],
        "unread": [],
    }
    with pytest.raises(context_audit.UnsupportedForge):
        await world.loop.survey(org("lab", forge="gitlab"))


# ---- starting a job ----


@pytest.mark.asyncio
async def test_a_job_starts_on_the_template_with_a_hard_budget_and_its_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    audits: list[tuple[str, str, dict[str, Any]]] = []

    async def record(_db: Any, action: str, **kw: Any) -> None:
        audits.append((action, kw["actor"], kw["details"]))

    monkeypatch.setattr(context_audit, "emit_audit", record)
    jobs, executor = FakeJobs(Clock(T0)), FakeExecutor()
    context = RepoContext(repo="acme/web", head_sha=HEAD, files=("AGENTS.md", "CLAUDE.md"), fingerprint=FP)
    job_id = await start_context_audit_job(
        context, jobs=jobs, executor=executor, templates=FakeTemplates(TEMPLATE), budget_usd=1.5
    )
    created = jobs.rows[0]["created_with"]
    assert created["repo"] == "acme/web" and created["pipeline_template"] == TEMPLATE == "context-audit"
    assert created["context"] == {"type": "git", "repo": "acme/web"}
    assert created["dispatched_by"] == ACTOR == "embry0:context-audit"
    assert created["pipeline_config"] == {
        "max_budget_usd": 1.5,
        "budget_overrun_mode": "hard",
        "context_fingerprint": FP,
        "head_sha": HEAD,
        "actor": ACTOR,
    }
    assert audits == [("job_created", ACTOR, {"job_id": job_id, "repo": "acme/web", "pipeline_template": TEMPLATE})]
    assert executor.submitted == [
        (
            job_id,
            {
                "repo": "acme/web",
                "title": created["task"].splitlines()[0],
                "body": created["task"],
                "kind": "context_audit",
            },
        )
    ]


@pytest.mark.asyncio
async def test_no_job_row_is_written_without_the_template_or_an_executor() -> None:
    jobs = FakeJobs(Clock(T0))
    with pytest.raises(LookupError, match="no pipeline template named 'context-audit'"):
        await start_context_audit_job(
            ctx(), jobs=jobs, executor=FakeExecutor(), templates=FakeTemplates(), budget_usd=2.0
        )
    with pytest.raises(LookupError):
        await start_context_audit_job(ctx(), jobs=jobs, executor=FakeExecutor(), templates=None, budget_usd=2.0)
    with pytest.raises(RuntimeError, match="executor not initialized"):
        await start_context_audit_job(
            ctx(), jobs=jobs, executor=None, templates=FakeTemplates(TEMPLATE), budget_usd=2.0
        )
    assert jobs.rows == []


def test_the_task_names_the_commit_and_every_file_up_to_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    task = audit_task(RepoContext("acme/web", HEAD, ("CLAUDE.md", "docs/AGENTS.md"), FP))
    assert "acme/web" in task.splitlines()[0]
    assert HEAD in task
    assert task.endswith("Instruction files (2):\n- CLAUDE.md\n- docs/AGENTS.md")
    monkeypatch.setattr(context_audit, "TASK_FILE_LIMIT", 2)
    long = audit_task(RepoContext("acme/web", HEAD, ("a/CLAUDE.md", "b/CLAUDE.md", "c/CLAUDE.md"), FP))
    assert long.endswith("Instruction files (3):\n- a/CLAUDE.md\n- b/CLAUDE.md\n- and 1 more")


class Config:
    def __init__(self, value: Any) -> None:
        self.context_audit_job_budget_usd = value


@pytest.mark.parametrize(
    ("value", "cap"),
    [(1.5, 1.5), ("3", 3.0), (0, 2.0), (-1, 2.0), (float("inf"), 2.0), (float("nan"), 2.0), ("x", 2.0), (None, 2.0)],
)
def test_the_budget_is_the_setting_when_positive_and_finite(value: Any, cap: float) -> None:
    assert job_budget_usd(Config(value)) == cap
    assert job_budget_usd(object()) == 2.0
