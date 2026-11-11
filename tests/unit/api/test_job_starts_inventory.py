"""Every route that can start a job starts one, for a key with a repository allowlist, only in a repository it covers.

A job starts in one place: ``JobsRepository.create`` makes every job row. A
job made earlier goes on through the executor's entry points
(:data:`CONTINUES`). Both lists come from the code, not from this file:

- :func:`sites` reads every module of ``embry0`` and finds each call that
  makes a job row (``<jobs>.create``) or makes the executor go on with one
  (outside the executor itself). :data:`SITES` classifies each: walked here
  (it names the routes that reach it), or why no key reaches it (a
  background loop, a signed webhook). A new call fails until it is
  classified, and a call classified as walked fails unless the walk below
  reaches it.
- Every write route under ``/api/v1`` (``app.routes``) is called on the real
  app over a real database (:mod:`tests.fixtures.issue_reads`), each on a
  fresh app: first through an admin key whose list covers ``acme/web``,
  addressed at ``globex/gizmos`` (its items, or a body naming it), then
  through an admin key without a list, the same call. :data:`CALLS` gives a
  route the body, the setup or the variants it needs for that call to start
  work; the rest are called as the write inventory calls them.

The limited key's call may start a job only in a repository it covers, and
link no new job to an issue it does not read; a call that starts nothing
outside its list passes whatever it answers (the shapes it answers in are the
other inventories'). The key without a list shows which calls start work:
the sites they reach must be exactly those :data:`STARTS` gives the call, so
a route that starts a job fails until it is classified, and a classified one
that starts nothing fails as measuring nothing.

The MCP server starts no job of its own: it reaches embry0 through these
routes with the caller's key (``embry0.mcp_server.client``), and :func:`sites`
finds no site in it.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import FrameType
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel

from embry0.api.v1.repo_old_paths import OldPathRoute
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository, job_repository
from tests.fixtures.issue_reads import CHILD, INPUT_ELSEWHERE, ISSUE, MIXED, MIXED_HIDDEN, issue_reads_app
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, OWNER, UNCOVERED, call
from tests.fixtures.routes import app_routes
from tests.unit.api.test_deployment_writes_inventory import DEPLOYMENT_WIDE
from tests.unit.api.test_repository_writes_inventory import BODIES, _app

pytestmark = pytest.mark.requires_postgres

ROOT = Path(__file__).parents[3]
_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")

# --------------------------------------------------------------------------------------------- what starts a job

#: Every name a jobs repository is bound to in the code, where a job row is made by ``<name>.create(...)``; one bound to
#: a name not here fails :func:`test_every_jobs_repository_binding_is_one_the_scan_reads`.
JOB_REPOSITORIES = frozenset({"jobs", "_jobs", "jobs_repo", "_jobs_repo", "boot_jobs_repo", "sweep_jobs_repo"})
#: The executor's entry points that go on with a job made earlier, or start one a caller made.
CONTINUES = frozenset(
    {
        "submit_job",
        "schedule_resume",
        "run_again",
        "go_on_after_account_wait",
        "relaunch",
        "resume_after_restart",
        "resume_for_issue",
        "resume_or_execute",
    }
)
EXECUTOR = "embry0.services.issue_executor"


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_dotted(node.func)}()"
    return "?"


class _Sites(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.found: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope += [node.name, "<locals>"]
        self.generic_visit(node)
        del self.scope[-2:]

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = _dotted(func.value).split(".")[-1]
            made = func.attr == "create" and receiver in JOB_REPOSITORIES
            goes_on = func.attr in CONTINUES and self.module != EXECUTOR
            if made or goes_on:
                self.found.add(f"{self.module}:{'.'.join(self.scope).removesuffix('.<locals>')}")
        self.generic_visit(node)


def sites() -> set[str]:
    """Every function in ``embry0`` that makes a job row or makes the executor go on with one, as
    ``module:qualified.name``, the name a frame's ``co_qualname`` gives it."""
    found: set[str] = set()
    for path in sorted((ROOT / "embry0").rglob("*.py")):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        visitor = _Sites(module)
        visitor.visit(ast.parse(path.read_text()))
        found |= visitor.found
    return found


_NOT_A_KEY = "reached by no key"
#: Each site, and what reaches it: the routes walked here, or why no key reaches it.
SITES: dict[str, str] = {
    "embry0.api.v1.jobs:create_job": "walked: POST /jobs",
    "embry0.api.v1.jobs:answer_job_input_as": "walked: answering a job's question, on the dashboard or in a chat",
    "embry0.api.v1.jobs:resume_job_as": "walked: resuming or stopping a paused job, and one an account held",
    "embry0.api.v1.issues:_resume_pipeline": "walked: answering an issue's question",
    "embry0.api.v1.job_review:run_again": "walked: running a job again",
    "embry0.api.v1.knowledge_maps:_start_analysis": "walked: sending a map's part",
    "embry0.api.v1.leads:_start_review": "walked: accepting a review lead",
    "embry0.api.v1.standards:start_standard_audit": "walked: a standard's audit",
    "embry0.services.area_map:start_map_areas_job": "walked: mapping a repository's areas",
    "embry0.services.comment_dispatch:CommentDispatchService.start_followup": "walked: sending a job back",
    "embry0.services.context_audit:start_context_audit_job": "walked: running an owner's context audit",
    f"{EXECUTOR}:IssueExecutor.execute": "walked: every issue's job (create, triage, answers, proposals, cases)",
    f"{EXECUTOR}:IssueExecutor.start_job": "walked: POST /jobs with the qa pipeline",
    f"{EXECUTOR}:IssueExecutor.start_onboard_job": "walked: onboard jobs (POST /jobs, setup, organization actions)",
    f"{EXECUTOR}:IssueExecutor.start_pr_review_job": f"{_NOT_A_KEY}: a pull request review a signed forge webhook asks for",
    "embry0.api.app:lifespan.<locals>._scout_submit": f"{_NOT_A_KEY}: the scout's background loop",
    "embry0.services.scout:ScoutLoop._start": f"{_NOT_A_KEY}: the scout's background loop",
    "embry0.api.app:_start_recovered_jobs": f"{_NOT_A_KEY}: the jobs a restart left, at boot",
    "embry0.services.account_waits:AccountWaitLoop.tick": f"{_NOT_A_KEY}: the account-wait loop",
    "embry0.api.v1.telegram:telegram_callback": f"{_NOT_A_KEY}: a signed chat callback",
    "embry0.api.v1.webhooks:_handle_issue_comment.<locals>._resume": f"{_NOT_A_KEY}: a signed forge webhook",
    "embry0.services.watcher:WatcherService._run_analysis_job": (
        f"{_NOT_A_KEY} here: a stack's own watcher makes its analysis job in the stack's repository, and "
        "POST /watcher/run ticks only the stacks the key covers (the stand-in watchers below, and "
        "test_watcher_routes_allowlist.py); a real tick needs a log store"
    ),
}


def _names_jobs_repository(node: ast.AST | None) -> bool:
    """Whether an annotation or a call names ``JobsRepository``."""
    if isinstance(node, ast.Name):
        return node.id == "JobsRepository"
    if isinstance(node, ast.Attribute):
        return node.attr == "JobsRepository"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "JobsRepository" in node.value
    if isinstance(node, ast.BinOp):
        return _names_jobs_repository(node.left) or _names_jobs_repository(node.right)
    if isinstance(node, ast.Subscript):
        return _names_jobs_repository(node.slice)
    return False


def _bound_name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def _is_jobs_repository(value: ast.AST | None, bound: set[str], attributes: set[str]) -> bool:
    """Whether ``value`` is a jobs repository: one it constructs, a name this scope bound to one, an attribute bound to
    one anywhere (``app.state.jobs_repo``, ``self._jobs``) read as an attribute or by ``getattr``, or either side of
    an ``or`` or a conditional of those."""
    if isinstance(value, ast.Call) and _names_jobs_repository(value.func):
        return True
    if isinstance(value, ast.Name):
        return value.id in bound
    if isinstance(value, ast.Attribute):
        return value.attr in attributes
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "getattr":
        named = value.args[1] if len(value.args) > 1 else None
        return isinstance(named, ast.Constant) and named.value in attributes
    if isinstance(value, ast.BoolOp):
        return any(_is_jobs_repository(v, bound, attributes) for v in value.values)
    if isinstance(value, ast.IfExp):
        return any(_is_jobs_repository(v, bound, attributes) for v in (value.body, value.orelse))
    return False


def _bindings(tree: ast.Module, attributes: set[str]) -> tuple[set[str], set[str]]:
    """The names ``tree`` binds to a jobs repository, and those of them it binds as an attribute: a parameter or a
    name annotated with it, and a name or an attribute assigned one (:func:`_is_jobs_repository`). A scope's walk takes
    in the functions inside it, so a closure's alias of its enclosing function's repository is found; it can only take
    a name for one too many, which fails loudly."""
    names: set[str] = set()
    as_attribute: set[str] = set()
    for scope in [tree, *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))]:
        bound: set[str] = set()
        if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
            params = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
            bound |= {a.arg for a in params if _names_jobs_repository(a.annotation)}
        nodes = list(ast.walk(scope))
        for _pass in range(2):
            for node in nodes:
                if isinstance(node, ast.Assign) and _is_jobs_repository(node.value, bound, attributes):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and (
                    _names_jobs_repository(node.annotation) or _is_jobs_repository(node.value, bound, attributes)
                ):
                    targets = [node.target]
                else:
                    continue
                for target in targets:
                    if name := _bound_name(target):
                        bound.add(name)
                        if isinstance(target, ast.Attribute):
                            as_attribute.add(name)
        names |= bound
    return names, as_attribute


def job_repository_bindings(trees: Mapping[str, ast.Module] | None = None) -> dict[str, set[str]]:
    """Every name bound to a ``JobsRepository`` in ``trees`` (by module; the code of ``embry0`` when none are given),
    with the modules that bind it (:func:`_bindings`). An attribute bound in one module is followed where another reads
    it, until no new one turns up."""
    if trees is None:
        trees = {
            ".".join(path.relative_to(ROOT).with_suffix("").parts): ast.parse(path.read_text())
            for path in sorted((ROOT / "embry0").rglob("*.py"))
        }
    attributes: set[str] = set()
    while True:
        out: dict[str, set[str]] = {}
        seen: set[str] = set()
        for module, tree in trees.items():
            names, as_attribute = _bindings(tree, attributes)
            seen |= as_attribute
            for name in names:
                out.setdefault(name, set()).add(module)
        if seen <= attributes:
            return out
        attributes |= seen


#: Two modules binding a jobs repository each way the scan follows, under names no code uses, with two names it must
#: not take for one. The first reads an attribute only the second binds, so it is found on the scan's second pass.
_PLANTED_BINDINGS = {
    "planted.reads": """
def planted(db, planted_parameter: JobsRepository | None, request, state):
    planted_made = JobsRepository(db)
    planted_alias = planted_parameter
    planted_annotated: "JobsRepository"
    planted_state = request.app.state.planted_attribute
    planted_getattr = getattr(state, "planted_attribute", None)
    planted_or = planted_parameter or JobsRepository(db)
    planted_either = planted_alias if db else None
    not_a_repository = request.app.state.issues_repo
    rows = [planted_made]


def planted_outer(planted_closed: JobsRepository):
    def inner():
        planted_inner = planted_closed
""",
    "planted.binds": """
class Planted:
    def __init__(self, db):
        self.planted_attribute = JobsRepository(db)
""",
}


def test_the_binding_scan_finds_each_way_a_jobs_repository_is_bound() -> None:
    """Not quiet by construction: the planted modules' bindings are each found, in their own module, and only they, so
    a name bound any of these ways in the code reaches the test below."""
    found = job_repository_bindings({module: ast.parse(source) for module, source in _PLANTED_BINDINGS.items()})
    reads = {
        "planted_parameter",
        "planted_made",
        "planted_alias",
        "planted_annotated",
        "planted_state",
        "planted_getattr",
        "planted_or",
        "planted_either",
        "planted_closed",
        "planted_inner",
    }
    expected = {**dict.fromkeys(reads, {"planted.reads"}), "planted_attribute": {"planted.binds"}}
    assert found == expected, (
        f"the planted bindings, and only they: missed {sorted(set(expected) - set(found))}, took "
        f"{sorted(set(found) - set(expected))}, in other modules "
        f"{sorted(n for n in set(found) & set(expected) if found[n] != expected[n])}"
    )


def test_every_jobs_repository_binding_is_one_the_scan_reads() -> None:
    """The scan finds a job row made by the name its repository goes by: every name the code binds a jobs repository
    to is one it reads, so a new name fails here before a job row made through it could go unseen."""
    found = job_repository_bindings()
    unread = {name: sorted(modules) for name, modules in found.items() if name not in JOB_REPOSITORIES}
    assert unread == {}, (
        f"names bound to a JobsRepository the scan does not read; add them to JOB_REPOSITORIES: {unread}"
    )
    unbound = sorted(JOB_REPOSITORIES - set(found))
    assert unbound == [], f"names in JOB_REPOSITORIES the code no longer binds; take them out: {unbound}"


def test_every_site_that_starts_a_job_is_classified() -> None:
    found = sites()
    unclassified = sorted(found - set(SITES))
    assert unclassified == [], (
        f"code that makes a job, or makes the executor go on with one, and is not classified: {unclassified}. Walk "
        "the routes that reach it in CALLS and STARTS, or say in SITES why no key reaches it"
    )
    stale = sorted(set(SITES) - found)
    assert stale == [], f"classified here but no longer in the code: {stale}"
    assert not any(site.startswith("embry0.mcp_server") for site in found), "the MCP server starts jobs itself"


def test_every_site_classified_as_walked_is_walked() -> None:
    walked = {site for site, how in SITES.items() if how.startswith("walked")}
    # A stand-in of this module (the watchers') is no site of the code's.
    reached = {site for site in set().union(*STARTS.values()) if site.startswith("embry0.")}
    assert sorted(walked - reached) == [], "sites classified as walked that no call in STARTS reaches"
    assert sorted(reached - walked) == [], "sites a call reaches that are not classified as walked"


# --------------------------------------------------------------------------------------------- recording what starts


@dataclass
class Started:
    """What one call started: each job row made (its repository, the issue it links, the site), and each job the
    executor went on with or a caller handed it (the job, the repository named, the site)."""

    made: list[dict[str, Any]] = field(default_factory=list)
    went_on: list[dict[str, Any]] = field(default_factory=list)

    @property
    def sites(self) -> set[str]:
        return {row["site"] for row in (*self.made, *self.went_on)}


def _site(*skip: str) -> str:
    """The function that called into a recorded entry point, past the recording wrappers and the modules ``skip``."""
    frame: FrameType | None = sys._getframe(2)
    while frame is not None and (
        frame.f_globals.get("__name__") in skip
        or (frame.f_globals.get("__name__") == __name__ and frame.f_code.co_qualname.startswith("record.<locals>"))
    ):
        frame = frame.f_back
    if frame is None:
        return "?"
    return f"{frame.f_globals['__name__']}:{frame.f_code.co_qualname}"


def record(monkeypatch: pytest.MonkeyPatch) -> Started:
    """Record every job a call makes or goes on with; no workflow runs (the executor's tasks are closed unrun)."""
    started = Started()
    make = JobsRepository.create

    async def create(self: JobsRepository, *args: Any, **kwargs: Any) -> str:
        job_id = await make(self, *args, **kwargs)
        row = {"repo": kwargs.get("repo", args[0] if args else None), "context": kwargs.get("context")}
        started.made.append(
            {
                "job_id": job_id,
                "repo": job_repository(row),
                "issue_id": kwargs.get("issue_id"),
                "site": _site("embry0.storage.repositories.jobs"),
            }
        )
        return job_id

    monkeypatch.setattr(JobsRepository, "create", create)

    def going_on(name: str) -> None:
        method = getattr(IssueExecutor, name)

        def wrapper(self: IssueExecutor, *args: Any, **kwargs: Any) -> Any:
            started.went_on.append({"entry": name, "args": args, "kwargs": kwargs, "site": _site(EXECUTOR)})
            return method(self, *args, **kwargs)

        monkeypatch.setattr(IssueExecutor, name, wrapper)

    for name in sorted(CONTINUES):
        going_on(name)

    def track(self: IssueExecutor, coro: Any, **_kwargs: Any) -> Any:
        coro.close()
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        done.set_result(None)
        return done

    monkeypatch.setattr(IssueExecutor, "_track_task", track)
    monkeypatch.setattr(IssueExecutor, "cancel_job", AsyncMock())
    return started


async def outside(db: DatabasePool, started: Started) -> list[str]:
    """What ``started`` started outside the limited key's list: a job in a repository it does not cover, or a new
    job linked to an issue of one."""
    out: list[str] = []
    for row in started.made:
        if str(row["repo"] or "").lower() != COVERED:
            out.append(f"made {row['job_id']} in {row['repo']} at {row['site']}")
        if row["issue_id"]:
            linked = await db.fetchval("SELECT repo FROM issues WHERE id = $1", row["issue_id"])
            if str(linked or "").lower() != COVERED:
                out.append(f"linked {row['job_id']} to {row['issue_id']} of {linked} at {row['site']}")
    for row in started.went_on:
        named = [row["kwargs"].get("repo"), *(a for a in row["args"] if isinstance(a, Mapping))]
        repos = [str(r) for r in named if isinstance(r, str)]
        jobs = [
            str(a) for a in (*row["args"], row["kwargs"].get("job_id")) if isinstance(a, str) and a.startswith("job-")
        ]
        for job in [*jobs, *(str(m["job_id"]) for m in row["args"] if isinstance(m, Mapping) and "job_id" in m)]:
            stored = await db.fetchrow("SELECT repo, context FROM jobs WHERE job_id = $1", job)
            if stored is not None:
                repos.append(str(job_repository(dict(stored)) or ""))
        repos += [str(row["kwargs"]["repo"])] if isinstance(row["kwargs"].get("repo"), str) else []
        out += [f"{row['entry']} went on in {repo} at {row['site']}" for repo in repos if repo.lower() != COVERED]
    return out


# --------------------------------------------------------------------------------------------- what each call needs


Setup = Callable[[FastAPI, DatabasePool], Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True)
class Call:
    """How a route is called to start work: path parameters over the uncovered repository's items, a query, a body
    (or a callable of the parameters), and a setup run on the fresh app first, which may return more parameters."""

    label: str = ""
    params: Mapping[str, str] = field(default_factory=dict)
    query: str = ""
    body: Any = None
    setup: Setup | None = None


class _OpenTickets:
    """A forge where every issue is open, and every pull request is open on its branch."""

    async def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        return {"state": "open", "title": f"Ticket {number}", "body": "It breaks.", "html_url": "", "labels": []}

    async def push_create(self, *_args: Any) -> None:
        return None

    async def push_update(self, *_args: Any) -> None:
        return None


async def _status(db: DatabasePool, table: str, key: str, value: str, **set_: Any) -> None:
    for column, to in set_.items():
        await db.execute(f"UPDATE {table} SET {column} = $2 WHERE {key} = $1", value, to)


async def _templates(app: FastAPI, _db: DatabasePool) -> None:
    from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates

    await seed_builtin_pipeline_templates(app.state.templates_repo)


async def _issue_waits(_app: FastAPI, db: DatabasePool) -> None:
    await _status(db, "issues", "id", ISSUE[UNCOVERED], status="awaiting_input")


async def _job_waits(_app: FastAPI, db: DatabasePool) -> None:
    await _status(db, "jobs", "job_id", JOB[UNCOVERED], status="awaiting_input", finished_at=None)


async def _job_paused(_app: FastAPI, db: DatabasePool) -> None:
    await _status(db, "jobs", "job_id", JOB[UNCOVERED], status="paused", finished_at=None)


async def _job_held(_app: FastAPI, db: DatabasePool) -> None:
    await _job_paused(_app, db)
    held = {"provider": "anthropic", "mode": "resume", "since": NOW.isoformat(), "until": NOW.isoformat()}
    await db.execute("UPDATE jobs SET account_wait = $2::jsonb WHERE job_id = $1", JOB[UNCOVERED], held)


async def _launched(_app: FastAPI, db: DatabasePool) -> None:
    launch = {"runner": "workflow", "issue_id": ISSUE[UNCOVERED], "additional_context": ""}
    await db.execute("UPDATE jobs SET launch = $2::jsonb WHERE job_id = $1", JOB[UNCOVERED], launch)


async def _crossed_run_again(_app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """The key's finished job, linked to an issue of the other repository."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id, launch, created_at, finished_at) "
        "VALUES ('job-crossed-done', $1, 'Fix it', 'completed', $2, $3::jsonb, $4, $4)",
        COVERED,
        MIXED_HIDDEN,
        {"runner": "workflow", "issue_id": MIXED_HIDDEN, "additional_context": ""},
        NOW,
    )
    return {"job_id": "job-crossed-done"}


async def _forge_open(app: FastAPI, _db: DatabasePool) -> None:
    app.state.github_issues_client = _OpenTickets()


async def _crossed_answer(_app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """The key's issue waits for its own job's question while a job of the other repository, newer, waits on it too:
    the key's answer is the last one blocking."""
    await db.execute("UPDATE issues SET status = 'awaiting_input' WHERE id = $1", MIXED)
    await db.execute("UPDATE issue_inputs SET status = 'answered', answer = 'done' WHERE id = $1", INPUT_ELSEWHERE)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id, created_at) "
        "VALUES ('job-mixed-mine', $1, 'Mine', 'completed', $2, $3)",
        COVERED,
        MIXED,
        NOW - timedelta(days=1),
    )
    await db.execute(
        "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ('in-mixed-mine', $1, 'job-mixed-mine', 'q')",
        MIXED,
    )
    return {"issue_id": MIXED, "input_id": "in-mixed-mine"}


async def _crossed_pull_request(app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """The key's finished job, whose pull request is the other repository's."""
    await _dispatch(app, db)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pr_url, created_at, finished_at) "
        "VALUES ('job-crossed-pr', $1, 'Fix it', 'completed', $2, $3, $3)",
        COVERED,
        f"https://github.com/{UNCOVERED}/pull/8",
        NOW,
    )
    return {"job_id": "job-crossed-pr"}


#: The issue a retry of the key's finding runs on, after it moved to the other repository; its attempt merged.
RETRY_ISSUE = "iss-retry-gizmo"


async def _crossed_case(app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """The key's watcher finding sent back for a retry on the issue its failed attempt ran on, which moved to the
    other repository."""
    await _forge_open(app, db)
    await db.execute(
        "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Retried', $2, 'closed')", RETRY_ISSUE, UNCOVERED
    )
    await db.execute(
        "INSERT INTO healing_cases (id, repo, origin, stage, title, tracker, tracker_key, issue_id, created_at, "
        "tracked_at) VALUES ('hc-retry-widget', $1, 'watcher', 'tracked', 'Retry', 'github', '91', $2, $3, $3)",
        COVERED,
        RETRY_ISSUE,
        NOW,
    )
    return {"case_id": "hc-retry-widget"}


async def _review_lead(app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """A review lead of the other repository: threads on its pull request nobody answered."""
    from embry0.services.leads import Lead
    from embry0.storage.repositories.leads import LeadsRepository

    await LeadsRepository(db).upsert(
        [
            Lead(
                kind="review",
                repo=UNCOVERED,
                source_key="review:7",
                title="Threads on #7",
                summary="s",
                details="d",
                action="a",
                severity="high",
                paths=(),
                data={"number": 7, "url": f"https://github.com/{UNCOVERED}/pull/7", "head_ref": "fix-7"},
            )
        ],
        now=NOW,
    )
    return {"proposal_id": await _offered(app, "review")}


async def _ci_lead(app: FastAPI, _db: DatabasePool) -> Mapping[str, str]:
    return {"proposal_id": await _offered(app, "ci")}


async def _offered(app: FastAPI, kind: str) -> str:
    """The id of the other repository's proposal of ``kind``, as the feed offers it."""
    feed = (await call(app, "GET", "/api/v1/proposals", MASTER)).json()["proposals"]
    (offered,) = [p["id"] for p in feed if p["repo"] == UNCOVERED and p["kind"] == kind]
    return str(offered)


async def _asked(app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """An ask about the other repository's open issue, with the code a chat answers it by."""
    # The person who answers: an owner of the repository (its preferences name ``ada``), by their GitHub login.
    await db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ('usr-ada', 'auth0|ada', "
        "'ada@example.com', 'ada', 'operator', 'ada') ON CONFLICT DO NOTHING"
    )
    decision = f"offer:issue:{CHILD[UNCOVERED]}"
    await db.execute("INSERT INTO decision_codes (decision_id, code) VALUES ($1, 'STRT01')", decision)
    await db.execute(
        "INSERT INTO asks (id, decision_id, recipient, due_at, created_at) VALUES ('ask-start', $1, "
        "'ada@example.com', $2, $2)",
        decision,
        NOW,
    )
    return {}


async def _boot_hint(_app: FastAPI, db: DatabasePool) -> None:
    await db.execute(
        "INSERT INTO org_repo_facts (owner, repo, default_branch, facts) VALUES ($1, $2, 'main', $3::jsonb)",
        OWNER[UNCOVERED],
        UNCOVERED,
        {"boot_hint": "npm start", "registered": True},
    )


async def _standard(app: FastAPI, db: DatabasePool) -> None:
    await _templates(app, db)
    made = await call(
        app, "PUT", f"/api/v1/standards/{OWNER[UNCOVERED]}/design", MASTER, {"schema": {"roles": {}}, "values": {}}
    )
    assert made.status_code < 300, made.text


async def _audits(app: FastAPI, db: DatabasePool) -> None:
    """The real context-audit schedule, starting jobs as production does, each repository read already."""
    from embry0.services.context_audit import start_context_audit_job

    await _templates(app, db)
    loop = app.state.context_audit

    async def start(context: Any, budget_usd: float, open_pr: bool, run_with: Any = None) -> str:
        return await start_context_audit_job(
            context,
            jobs=app.state.jobs_repo,
            executor=app.state.issue_executor,
            templates=app.state.templates_repo,
            budget_usd=budget_usd,
            open_pr=open_pr,
            db=app.state.db,
            audit_log_path=None,
            run_with=run_with,
        )

    async def forge(_org: Any) -> _Heads:
        return _Heads()

    loop._start_job = start  # noqa: SLF001 - the fixture's schedule starts nothing
    loop._client_for = forge  # noqa: SLF001 - and reads no forge


class _Heads:
    """The forge as a survey reads it: each repository's head is the commit its reading was taken at."""

    async def get(self, path: str, **_kwargs: Any) -> httpx.Response:
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc123", "commit": {"tree": {"sha": "tree-abc123"}}}])
        if "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [{"path": "AGENTS.md", "type": "blob", "sha": "blob-agents"}]})
        return httpx.Response(404, json={})


class _PullRequests:
    """The forge's pull requests as the comment dispatch reads them: each open on its branch."""

    async def __call__(self, repo: str, number: int) -> dict[str, Any]:
        return {
            "state": "open",
            "head": {"ref": f"fix-{number}"},
            "html_url": f"https://github.com/{repo}/pull/{number}",
        }


async def _dispatch(app: FastAPI, _db: DatabasePool) -> None:
    from embry0.services.comment_dispatch import CommentDispatchService

    service = CommentDispatchService(
        config=app.state.config,
        jobs_repo=app.state.jobs_repo,
        agent_sessions_repo=getattr(app.state, "agent_sessions_repo", None),
        executor=app.state.issue_executor,
    )
    service._fetch_pr = _PullRequests()  # type: ignore[method-assign]  # noqa: SLF001 - the forge is not reached
    app.state.comment_dispatch = service


async def _watchers(app: FastAPI, _db: DatabasePool) -> None:
    """A stand-in watcher per stack whose tick makes the analysis job a stack's watcher makes, in its repository."""

    class Watcher:
        def __init__(self, repo: str) -> None:
            self.repo = repo

        async def run_once(self, _run_with: Any = None) -> dict[str, Any]:
            job_id = await app.state.jobs_repo.create(repo=self.repo, task="analyse", pipeline_template="watcher")
            return {"job_id": job_id}

    app.state.watcher_services = {repo: Watcher(repo) for repo in (COVERED, UNCOVERED)}


async def _map_file(_app: FastAPI, db: DatabasePool) -> None:
    """The other repository's map's part is a file of it, so an agent can take it."""
    graph = {"repo": UNCOVERED, "nodes": [{"id": "n1", "kind": "file", "label": "n1", "path": "src/app.py"}]}
    await db.execute("UPDATE knowledge_maps SET graph = $2 WHERE repo = $1", UNCOVERED, json.dumps(graph))


async def _starter(_app: FastAPI, db: DatabasePool) -> None:
    """The person who started the other repository's job, known to embry0, as a chat names them."""
    await db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ('usr-ada', 'auth0|ada', "
        "'ada@example.com', 'ada', 'operator', 'ada') ON CONFLICT DO NOTHING"
    )


async def _issueless_question(_app: FastAPI, db: DatabasePool) -> Mapping[str, str]:
    """A question the other repository's job asked with no issue, as a job posted with a task asks."""
    await db.execute(
        "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ('in-issueless', NULL, $1, 'which?')",
        JOB[UNCOVERED],
    )
    await db.execute("UPDATE issue_inputs SET status = 'answered' WHERE id = 'in-gizmo'")
    return {"input_id": "in-issueless"}


#: Deployment-wide writes this walk calls with a setup of its own above.
_WALKED_OTHERWISE = frozenset({("PUT", "/api/v1/repos/{full_repo:repo}/-/docker-access")})
#: Path parameters the deployment inventory's values name by another name.
_DEPLOYMENT_PARAMS: dict[str, Mapping[str, str]] = {
    "/api/v1/sandbox-profiles/{name}": {"name": "dev-python"},
    "/api/v1/sandbox-profiles/{name}/reset": {"name": "dev-python"},
}


async def _deployment_values(app: FastAPI, db: DatabasePool) -> Mapping[str, Any]:
    """The deployment writes inventory's values (a template, a host, a skill, a source, a person), the other
    repository's left as the walk names it."""
    from tests.unit.api.test_deployment_writes_inventory import _deployment

    return {k: v for k, v in (await _deployment(app, db)).items() if k != "full_repo"}


async def _a_key(app: FastAPI, _db: DatabasePool) -> Mapping[str, str]:
    made = await call(app, "POST", "/api/v1/apikeys", MASTER, {"name": "another", "scope": "read"})
    assert made.status_code < 300, made.text
    return {"key_id": str(made.json()["id"])}


async def _owner_credential(app: FastAPI, db: DatabasePool) -> None:
    """The other owner's forge credential, so its organization's writes reach the forge (which no network serves)."""
    credentials = (await call(app, "GET", "/api/v1/credentials", MASTER)).json()
    rows = credentials if isinstance(credentials, list) else credentials.get("credentials", [])
    bound = await call(
        app,
        "PUT",
        "/api/v1/credentials/bindings",
        MASTER,
        {
            "scope_kind": "owner",
            "scope_value": f"github:{OWNER[UNCOVERED]}",
            "bindings": [{"credential_id": rows[0]["id"], "role": "forge"}],
        },
    )
    assert bound.status_code < 300, bound.text
    await db.execute("UPDATE organizations SET credential_id = $2 WHERE owner = $1", OWNER[UNCOVERED], rows[0]["id"])


async def _pack(app: FastAPI, _db: DatabasePool) -> Mapping[str, Any]:
    """The progression pack in effect, its YAML text, to write back."""
    return {"pack": (await call(app, "GET", "/api/v1/progression/pack", MASTER)).json()["text"]}


async def _proposed_setup(_app: FastAPI, db: DatabasePool) -> None:
    """The other repository's setup proposal proposes a test setup that validates."""
    from tests.unit.api.test_repository_writes_inventory import _QA_SETUP

    await db.execute(
        "UPDATE repo_setup_proposals SET proposed_yaml = $2, status = 'pending' WHERE repo = $1", UNCOVERED, _QA_SETUP
    )


def _together(*setups: Setup) -> Setup:
    async def both(app: FastAPI, db: DatabasePool) -> Mapping[str, str] | None:
        out: dict[str, str] = {}
        for setup in setups:
            out |= dict(await setup(app, db) or {})
        return out

    return both


_ISSUE_PROPOSAL = {"proposal_id": f"issue:{CHILD[UNCOVERED]}"}
#: How each route is called to start work, when it needs more than the write inventory's body; several when it starts
#: work several ways. A route not here is called once, as the write inventory calls it.
CALLS: dict[tuple[str, str], tuple[Call, ...]] = {
    ("POST", "/api/v1/jobs"): (
        Call("a task", body={"repo": UNCOVERED, "task": "Fix it"}),
        Call("a git context", body={"context": {"type": "git", "repo": UNCOVERED}, "task": "Fix it"}),
        Call("the qa pipeline", body={"repo": UNCOVERED, "pipeline": "qa", "branch": "main"}),
        Call("the onboard pipeline", body={"repo": UNCOVERED, "pipeline": "onboard"}),
    ),
    ("POST", "/api/v1/issues"): (Call(body={"title": "Fix it", "repo": UNCOVERED, "auto_triage": True}),),
    ("POST", "/api/v1/issues/{issue_id}/inputs/{input_id}/answer"): (
        Call(body={"answer": "this one"}, setup=_issue_waits),
        Call("the key's question, a job elsewhere waits", body={"answer": "this one"}, setup=_crossed_answer),
    ),
    ("POST", "/api/v1/jobs/{job_id}/inputs/{input_id}/answer"): (Call(body={"answer": "this one"}, setup=_job_waits),),
    ("POST", "/api/v1/jobs/{job_id}/resume"): (
        Call("paused", body={"choice": "abandon"}, setup=_job_paused),
        Call("held for its account", body={"choice": "continue_retrying"}, setup=_job_held),
    ),
    **{
        ("POST", path): (
            Call(setup=_launched),
            Call("the key's job, its issue elsewhere", setup=_crossed_run_again),
        )
        for path in ("/api/v1/jobs/{job_id}/run-again", "/api/v1/world/quests/{job_id}/run-again")
    },
    **{
        ("POST", path): (
            Call(body={"notes": "again"}, setup=_dispatch),
            Call("the key's job, its pull request elsewhere", body={"notes": "again"}, setup=_crossed_pull_request),
        )
        for path in ("/api/v1/jobs/{job_id}/send-back", "/api/v1/world/quests/{job_id}/send-back")
    },
    ("POST", "/api/v1/cases/{case_id}/accept"): (
        Call(body={}, setup=_forge_open),
        Call("the key's retry, its issue elsewhere", body={}, setup=_crossed_case),
    ),
    ("POST", "/api/v1/proposals/{proposal_id}/accept"): (
        Call("an issue", params=_ISSUE_PROPOSAL, body={"by": "ada"}),
        Call("a finding's case", params={"proposal_id": "case:hc-gizmo"}, body={"by": "ada"}, setup=_forge_open),
        Call("a ci lead", body={"by": "ada"}, setup=_ci_lead),
        Call("a review lead", body={"by": "ada"}, setup=_review_lead),
    ),
    ("POST", "/api/v1/asks/answer"): (
        Call(body={"code": "STRT01", "action": "start", "person": "ada@example.com"}, setup=_asked),
    ),
    ("POST", "/api/v1/asks/jobs/{job_id}/answer"): (
        Call(
            "an issue's question",
            body=lambda v: {"person": "ada@example.com", "input_id": v["input_id"], "answer": "this one"},
            setup=_together(_starter, _issue_waits, _job_waits),
        ),
        Call(
            "a job's question",
            body=lambda v: {"person": "ada@example.com", "input_id": v["input_id"], "answer": "this one"},
            setup=_together(_starter, _job_waits, _issueless_question),
        ),
    ),
    ("POST", "/api/v1/asks/jobs/{job_id}/resume"): (
        Call(body={"person": "ada@example.com", "choice": "abandon"}, setup=_together(_starter, _job_paused)),
    ),
    ("POST", "/api/v1/asks/jobs/{job_id}/stop"): (
        Call(body={"person": "ada@example.com"}, setup=_together(_starter, _job_paused)),
    ),
    ("POST", "/api/v1/orgs/{owner}/actions"): (
        Call(body={"action": "onboard", "repos": [UNCOVERED]}, setup=_boot_hint),
    ),
    ("POST", "/api/v1/orgs/{owner}/context-audit/run"): (Call(body={"repo": UNCOVERED, "force": True}, setup=_audits),),
    ("POST", "/api/v1/standards/{owner}/{kind}/audit/{repo:path}"): (Call(body={}, setup=_standard),),
    ("POST", "/api/v1/repos/{full_repo:repo}/-/areas/map"): (Call(setup=_templates),),
    ("POST", "/api/v1/maps/{slug}/send"): (
        Call(body={"node_ids": ["n1"], "by": "ada", "kind": "analysis"}, setup=_together(_templates, _map_file)),
    ),
    ("POST", "/api/v1/watcher/run"): (Call(query=f"?stack={UNCOVERED}", setup=_watchers),),
    ("POST", "/api/v1/qa/images/build"): (Call(body={"repo": UNCOVERED}),),
    ("POST", "/api/v1/repos/bootstrap"): (Call(body={"repo": UNCOVERED}),),
    ("POST", "/api/v1/leads/run"): (Call(body={"repo": UNCOVERED}),),
    # Bodies so each remaining write reaches its handler's work, not its validation: the deployment writes inventory's
    # where it has one, on what its setup makes.
    **{
        route: (Call(body=body, setup=_deployment_values, params=_DEPLOYMENT_PARAMS.get(route[1], {})),)
        for route, body in DEPLOYMENT_WIDE.items()
        if route not in _WALKED_OTHERWISE
    },
    ("POST", "/api/v1/apikeys"): (Call(body={"name": "probe", "scope": "read"}),),
    ("PUT", "/api/v1/apikeys/{key_id}/allowed-repos"): (Call(body={"allowed_repos": [UNCOVERED]}, setup=_a_key),),
    ("PATCH", "/api/v1/config/settings/{section}"): (Call(params={"section": "asks"}, body={"remind_hours": 12}),),
    ("PUT", "/api/v1/config/budget"): (Call(body={"daily_cap_usd": 50}),),
    ("POST", "/api/v1/credentials"): (
        Call(body={"kind": "forge_token", "provider": "github", "label": "probe", "secret": "y" * 24}),
    ),
    ("PUT", "/api/v1/credentials/{credential_id}"): (Call(body={"label": "renamed"}),),
    ("POST", "/api/v1/credentials/{credential_id}/rotate"): (Call(body={"secret": "z" * 24}),),
    ("PUT", "/api/v1/credentials/bindings"): (
        Call(
            body=lambda v: {
                "scope_kind": "owner",
                "scope_value": f"github:{v['owner']}",
                "bindings": [{"credential_id": v["credential_id"], "role": "forge"}],
            }
        ),
    ),
    ("POST", "/api/v1/internal/qa/presign"): (Call(body={"sandbox_token": "t" * 20, "paths": ["result.json"]}),),
    ("POST", "/api/v1/orgs"): (
        Call(body=lambda v: {"owner": v["owner"], "forge": "github", "display_name": "Renamed", "probe": False}),
    ),
    ("POST", "/api/v1/orgs/probe"): (Call(body=lambda v: {"owner": v["owner"], "credential_id": v["credential_id"]}),),
    **{
        ("POST", f"/api/v1/orgs/{{owner}}/{tail}"): (Call(setup=_owner_credential),)
        for tail in ("discover", "probe", "webhook")
    },
    ("POST", "/api/v1/pipelines/validate"): (Call(body=lambda v: v["graph"], setup=_deployment_values),),
    ("POST", "/api/v1/progression/pack/versions/{version}/restore"): (Call(params={"version": "1"}),),
    **{
        ("PUT", path): (Call(body=lambda v: v["pack"], setup=_pack),)
        for path in ("/api/v1/progression/pack", "/api/v1/progression/pack/draft")
    },
    ("POST", "/api/v1/repos"): (Call(body={"repo": UNCOVERED, "forge": "github", "notes": "Again"}),),
    ("PUT", "/api/v1/me/github-login"): (Call(body={"login": "ada"}),),
    ("POST", "/api/v1/repos/{full_repo:repo}/-/setup/proposal/{proposal_id}/apply"): (Call(setup=_proposed_setup),),
    ("PUT", "/api/v1/maps/{slug}/owner"): (Call(body=lambda v: {"owner": v["owner"]}),),
}

_EXECUTE = f"{EXECUTOR}:IssueExecutor.execute"
_ONBOARD = f"{EXECUTOR}:IssueExecutor.start_onboard_job"
_RUN_AGAIN = frozenset({"embry0.api.v1.job_review:run_again"})
_RESUME = frozenset({"embry0.api.v1.jobs:resume_job_as"})
_SEND_BACK = frozenset({"embry0.services.comment_dispatch:CommentDispatchService.start_followup"})
#: The sites each call reaches through a key without a list: the calls that start work, by (method, path, label).
STARTS: dict[tuple[str, str, str], frozenset[str]] = {
    ("POST", "/api/v1/jobs", "a task"): frozenset({"embry0.api.v1.jobs:create_job"}),
    ("POST", "/api/v1/jobs", "a git context"): frozenset({"embry0.api.v1.jobs:create_job"}),
    ("POST", "/api/v1/jobs", "the qa pipeline"): frozenset({f"{EXECUTOR}:IssueExecutor.start_job"}),
    ("POST", "/api/v1/jobs", "the onboard pipeline"): frozenset({_ONBOARD}),
    ("POST", "/api/v1/issues", ""): frozenset({_EXECUTE}),
    ("POST", "/api/v1/issues/{issue_id}/triage", ""): frozenset({_EXECUTE}),
    ("POST", "/api/v1/issues/{issue_id}/inputs/{input_id}/answer", ""): frozenset(
        {"embry0.api.v1.issues:_resume_pipeline", _EXECUTE}
    ),
    ("POST", "/api/v1/issues/{issue_id}/inputs/{input_id}/answer", "the key's question, a job elsewhere waits"): (
        frozenset({"embry0.api.v1.issues:_resume_pipeline"})
    ),
    ("POST", "/api/v1/jobs/{job_id}/inputs/{input_id}/answer", ""): frozenset(
        {"embry0.api.v1.jobs:answer_job_input_as"}
    ),
    ("POST", "/api/v1/jobs/{job_id}/resume", "paused"): _RESUME,
    ("POST", "/api/v1/jobs/{job_id}/resume", "held for its account"): _RESUME,
    ("POST", "/api/v1/asks/jobs/{job_id}/answer", "an issue's question"): frozenset(
        {"embry0.api.v1.issues:_resume_pipeline"}
    ),
    ("POST", "/api/v1/asks/jobs/{job_id}/answer", "a job's question"): frozenset(
        {"embry0.api.v1.jobs:answer_job_input_as"}
    ),
    ("POST", "/api/v1/asks/jobs/{job_id}/resume", ""): _RESUME,
    ("POST", "/api/v1/asks/jobs/{job_id}/stop", ""): _RESUME,
    **{
        ("POST", path, label): _RUN_AGAIN
        for path in ("/api/v1/jobs/{job_id}/run-again", "/api/v1/world/quests/{job_id}/run-again")
        for label in ("", "the key's job, its issue elsewhere")
    },
    **{
        ("POST", path, label): _SEND_BACK
        for path in ("/api/v1/jobs/{job_id}/send-back", "/api/v1/world/quests/{job_id}/send-back")
        for label in ("", "the key's job, its pull request elsewhere")
    },
    ("POST", "/api/v1/cases/{case_id}/accept", ""): frozenset({_EXECUTE}),
    ("POST", "/api/v1/cases/{case_id}/accept", "the key's retry, its issue elsewhere"): frozenset({_EXECUTE}),
    ("POST", "/api/v1/proposals/{proposal_id}/accept", "an issue"): frozenset({_EXECUTE}),
    ("POST", "/api/v1/proposals/{proposal_id}/accept", "a finding's case"): frozenset({_EXECUTE}),
    ("POST", "/api/v1/proposals/{proposal_id}/accept", "a ci lead"): frozenset({_EXECUTE}),
    ("POST", "/api/v1/proposals/{proposal_id}/accept", "a review lead"): frozenset(
        {"embry0.api.v1.leads:_start_review"}
    ),
    ("POST", "/api/v1/asks/answer", ""): frozenset({_EXECUTE}),
    ("POST", "/api/v1/orgs/{owner}/actions", ""): frozenset({_ONBOARD}),
    ("POST", "/api/v1/orgs/{owner}/context-audit/run", ""): frozenset(
        {"embry0.services.context_audit:start_context_audit_job"}
    ),
    ("POST", "/api/v1/standards/{owner}/{kind}/audit/{repo:path}", ""): frozenset(
        {"embry0.api.v1.standards:start_standard_audit"}
    ),
    ("POST", "/api/v1/repos/{full_repo:repo}/-/areas/map", ""): frozenset(
        {"embry0.services.area_map:start_map_areas_job"}
    ),
    ("POST", "/api/v1/repos/{full_repo:repo}/-/setup/propose", ""): frozenset({_ONBOARD}),
    ("POST", "/api/v1/maps/{slug}/send", ""): frozenset({"embry0.api.v1.knowledge_maps:_start_analysis"}),
    ("POST", "/api/v1/watcher/run", ""): frozenset({f"{__name__}:_watchers.<locals>.Watcher.run_once"}),
}


#: Calls that answer a key without a list a validation error (422) here, and why the route starts no work all the same.
NO_BODY_HERE: dict[tuple[str, str, str], str] = {}


def _unvalidated(answer: httpx.Response) -> bool:
    """Whether ``answer`` is a request that did not validate: a 422 whose detail lists what failed, before the route's
    own code ran."""
    if answer.status_code != 422:
        return False
    try:
        return isinstance(answer.json().get("detail"), list)
    except ValueError:
        return False


def write_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every write route under ``/api/v1``, as (method, path); an old path is its new path's handler."""
    return sorted(
        (method, route.path)
        for route in app_routes(app)
        if route.is_api_route and route.path.startswith("/api/v1/") and not isinstance(route.route, OldPathRoute)
        for method in (route.methods or set()) - {"GET", "HEAD", "OPTIONS"}
    )


WALK = [
    (method, path, variant) for method, path in write_routes(_app()) for variant in CALLS.get((method, path), (Call(),))
]


def test_the_walk_calls_every_classified_start() -> None:
    walked = {(method, path, variant.label) for method, path, variant in WALK}
    assert sorted(set(STARTS) - walked) == [], "starts classified for a call the walk does not make"
    assert sorted({(m, p) for m, p in CALLS} - {(m, p) for m, p, _v in WALK}) == [], "calls for routes not served"


def _url(path: str, values: Mapping[str, str]) -> str:
    named = dict(values, proposal_id=values["setup_proposal_id"]) if "/setup/proposal/" in path else dict(values)
    return _PARAMETER.sub(lambda m: named.get(m.group(1), "x"), path)


async def _call(app: FastAPI, method: str, path: str, variant: Call, values: Mapping[str, str], token: str) -> Any:
    body = variant.body if variant.body is not None else BODIES.get(path)
    body = body(values) if callable(body) else body
    return await call(app, method, f"{_url(path, values)}{variant.query}", token, body)


async def _walk(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch, method: str, path: str, variant: Call, token: str
) -> tuple[httpx.Response, Started, list[str]]:
    async with issue_reads_app(db, monkeypatch) as (app, keys, values):
        started = record(monkeypatch)
        named = dict(values[UNCOVERED]) | dict(variant.params)
        if variant.setup is not None:
            named |= dict(await variant.setup(app, db) or {})
        answer = await _call(app, method, path, variant, named, getattr(keys, token))
        return answer, started, await outside(db, started)


@pytest.mark.parametrize(("method", "path", "variant"), WALK, ids=[f"{m} {p} {v.label}".strip() for m, p, v in WALK])
async def test_a_key_starts_work_only_in_the_repositories_it_covers(
    method: str, path: str, variant: Call, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    limited, _started, beyond = await _walk(db_with_migrations, monkeypatch, method, path, variant, "limited")
    assert beyond == [], (
        f"{method} {path} {variant.label}: a key covering only {COVERED} started work outside it: {beyond} "
        f"({limited.status_code} {limited.text[:200]})"
    )


@pytest.mark.parametrize(("method", "path", "variant"), WALK, ids=[f"{m} {p} {v.label}".strip() for m, p, v in WALK])
async def test_the_calls_that_start_work_are_classified(
    method: str, path: str, variant: Call, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer, started, _beyond = await _walk(db_with_migrations, monkeypatch, method, path, variant, "open")
    problems = unclassified(method, path, variant.label, answer, started)
    assert problems == [], "; ".join(problems)


def unclassified(method: str, path: str, label: str, answer: httpx.Response, started: Started) -> list[str]:
    """What is wrong with how a call through a key without a list is classified: it did not validate, so it measures
    nothing, unless NO_BODY_HERE says why the route starts no work; or the sites it reached are not those STARTS
    gives it."""
    out: list[str] = []
    call_ = f"{method} {path} {label}".strip()
    if _unvalidated(answer) and (method, path, label) not in NO_BODY_HERE:
        out.append(
            f"{call_}: through a key without a list the call did not validate, so it measures nothing: "
            f"{answer.text[:300]}. Give it a valid body or query in CALLS, or say in NO_BODY_HERE why the route starts "
            "no work"
        )
    expected = STARTS.get((method, path, label), frozenset())
    if started.sites != expected:
        out.append(
            f"{call_}: through a key without a list it reached {sorted(started.sites)}, classified {sorted(expected)} "
            f"({answer.status_code} {answer.text[:200]}). A call that starts work is classified in STARTS with the "
            "sites it reaches; one that no longer does is taken out"
        )
    return out


class _StartBody(BaseModel):
    """The body of the route the self-test below plants: the issue to start work on."""

    issue_id: str


async def test_the_walk_reports_a_new_route_that_starts_work_from_its_body(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a planted route that starts work on the issue its body names, with no rule, is
    reported. Called as the walk calls a route it has no call for, it does not validate; called with a body, it
    reaches a site no call is classified for, and through the limited key it starts work outside the list."""
    path = "/api/v1/start-for-a-test"
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        started = record(monkeypatch)
        extra = APIRouter()

        @extra.post("/start-for-a-test")
        async def start(body: _StartBody, request: Request) -> dict[str, Any]:
            return {"job_id": await request.app.state.issue_executor.execute(body.issue_id)}

        app.include_router(extra, prefix="/api/v1")
        bare = await call(app, "POST", path, keys.open)
        bare_problems = unclassified("POST", path, "", bare, started)
        limited = await call(app, "POST", path, keys.limited, {"issue_id": ISSUE[UNCOVERED]})
        beyond = await outside(db, started)
        started.made.clear()
        started.went_on.clear()
        # Another of its issues: the first now has a job going, which a second start refuses.
        reached = await call(app, "POST", path, keys.open, {"issue_id": CHILD[UNCOVERED]})
        reached_problems = unclassified("POST", path, "", reached, started)
    assert len(bare_problems) == 1 and "did not validate" in bare_problems[0], bare_problems
    assert len(reached_problems) == 1 and "IssueExecutor.execute" in reached_problems[0], reached_problems
    assert limited.status_code < 300 and beyond, f"the limited key's call starts work outside its list: {beyond}"


# --------------------------------------------------------------------------------------------- refused, and how

#: The writes on the key's own item that would start work in a repository the key does not cover: each refused with a
#: 403 naming only the key's repository and saying why, before anything is written; the audit row names the other.
REACHING: dict[str, tuple[str, str, Setup, Any]] = {
    "a finding's retry on an issue elsewhere": (
        "POST",
        "/api/v1/cases/{case_id}/accept",
        _crossed_case,
        {},
    ),
    "the last answer, a job elsewhere waiting": (
        "POST",
        "/api/v1/issues/{issue_id}/inputs/{input_id}/answer",
        _crossed_answer,
        {"answer": "this one"},
    ),
    "sending back a job whose pull request is elsewhere": (
        "POST",
        "/api/v1/jobs/{job_id}/send-back",
        _crossed_pull_request,
        {"notes": "again"},
    ),
}


@pytest.mark.parametrize("case", list(REACHING))
async def test_a_write_that_would_start_work_elsewhere_is_refused_naming_only_the_key_s_repository(
    case: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.api.repo_allowlist import REFUSAL_CODE
    from tests.unit.api.test_repository_writes_inventory import _refusals, _tables

    db = db_with_migrations
    method, path, setup, body = REACHING[case]
    async with issue_reads_app(db, monkeypatch) as (app, keys, values):
        started = record(monkeypatch)
        named = dict(values[UNCOVERED]) | dict(await setup(app, db) or {})
        url = _url(path, named)
        tables, before = await _tables(db), await _refusals(db)
        refused = await call(app, method, url, keys.limited, body)
        audited = (await _refusals(db))[len(before) :]
        changed = sorted(name for name, digest in (await _tables(db)).items() if tables.get(name) != digest)
        nothing = (list(started.made), list(started.went_on))
        reached = await call(app, method, url, keys.open, body)
        beyond = await outside(db, started)
    detail = refused.json().get("detail", "") if refused.status_code == 403 else refused.text
    assert refused.status_code == 403 and str(detail).startswith(REFUSAL_CODE), (
        f"{case}: refused: {refused.status_code} {refused.text[:200]}"
    )
    assert COVERED in detail and UNCOVERED not in detail and OWNER[UNCOVERED] not in detail, (
        f"{case}: the refusal names the key's own repository and not the other: {detail}"
    )
    assert audited == [UNCOVERED], f"{case}: the refusal is audited once, naming {UNCOVERED}: {audited}"
    assert changed == [] and nothing == ([], []), f"{case}: the refused call wrote to {changed}, started {nothing}"
    assert reached.status_code < 400 and beyond, (
        f"{case}: a key without a list starts the work: {reached.status_code} {reached.text[:200]} {beyond}"
    )


# --------------------------------------------------------------------------------------------- the MCP tools


def _client_requests() -> dict[str, list[tuple[str, str]]]:
    """Each MCP client method's write requests, as (method, a path pattern), read from its code: an f-string's
    fields and a repository's path become wildcards."""
    tree = ast.parse((ROOT / "embry0" / "mcp_server" / "client.py").read_text())

    def pattern(node: ast.AST, names: Mapping[str, ast.AST]) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return re.escape(node.value)
        if isinstance(node, ast.JoinedStr):
            return "".join(
                re.escape(str(part.value)) if isinstance(part, ast.Constant) else ".+" for part in node.values
            )
        if isinstance(node, ast.Name) and node.id in names:
            return pattern(names[node.id], names)
        if isinstance(node, ast.Call) and _dotted(node.func).endswith(("_repo_url", "_env_base")):
            return "/api/v1/.+"
        return "UNRESOLVED"

    out: dict[str, list[tuple[str, str]]] = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Embry0Client"):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            names = {
                t.id: a.value
                for a in ast.walk(fn)
                if isinstance(a, ast.Assign)
                for t in a.targets
                if isinstance(t, ast.Name)
            }
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and _dotted(node.func) == "self._request"
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value != "GET"
                ):
                    out.setdefault(fn.name, []).append((str(node.args[0].value), pattern(node.args[1], names)))
    return out


def _tools() -> dict[str, set[str]]:
    """Each MCP tool, and the client methods it calls."""
    tree = ast.parse((ROOT / "embry0" / "mcp_server" / "server.py").read_text())
    out: dict[str, set[str]] = {}
    for fn in (n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)):
        if not any(_dotted(d).endswith("_tool") for d in fn.decorator_list):
            continue
        out[fn.name] = {
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _dotted(node.func.value) == "_get_client()"
        }
    return out


def starter_tools() -> set[str]:
    """The MCP tools that reach a route a call of which starts work (STARTS), read from the code."""
    starting = {(method, path) for method, path, _label in STARTS}
    requests = _client_requests()
    unresolved = sorted(m for m, rows in requests.items() if any(p == "UNRESOLVED" for _v, p in rows))
    assert unresolved == [], f"MCP client methods whose request path this reading cannot follow: {unresolved}"
    out = set()
    for tool, methods in _tools().items():
        for method in methods:
            for verb, pattern in requests.get(method, []):
                sample = {(v, _PARAMETER.sub("x", p)) for v, p in starting}
                if any(v == verb and re.fullmatch(pattern, path) for v, path in sample):
                    out.add(tool)
    return out


def test_the_mcp_server_reaches_embry0_only_through_its_routes() -> None:
    imported = {
        node.module
        for path in (ROOT / "embry0" / "mcp_server").glob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("embry0")
    }
    assert imported <= {"embry0.mcp_server.client", "embry0.mcp_server.server"}, (
        f"the MCP server imports embry0 beyond its HTTP client: {sorted(imported)}; it would start work past the "
        "routes this walk holds"
    )


#: Each MCP tool that reaches a route that starts work, run on the other repository's item, and whether it starts work
#: there for a key without a list.
MCP_STARTS: dict[str, tuple[Callable[[Any, Mapping[str, str]], Awaitable[Any]], bool, Setup | None]] = {
    "issue_create": (lambda s, v: s.issue_create("Fix it", repo=UNCOVERED), True, None),
    "job_create": (lambda s, v: s.job_create(task="Fix it", repo=UNCOVERED), True, None),
    "job_resume": (lambda s, v: s.job_resume(v["job_id"], "abandon", confirm=True), True, _job_paused),
    "job_answer": (lambda s, v: s.job_answer(v["job_id"], v["input_id"], "yes", confirm=True), True, _job_waits),
    "job_run_again": (lambda s, v: s.job_run_again(v["job_id"], confirm=True), True, _launched),
    "job_send_back": (lambda s, v: s.job_send_back(v["job_id"], "again", confirm=True), True, _dispatch),
    "watcher_trigger": (lambda s, v: s.watcher_trigger(timeout=5, stack=UNCOVERED), True, _watchers),
    "proposal_accept": (
        lambda s, v: s.proposal_accept(f"issue:{CHILD[UNCOVERED]}", by="ada", confirm=True),
        True,
        None,
    ),
    "proposal_decline": (
        lambda s, v: s.proposal_decline(f"issue:{CHILD[UNCOVERED]}", by="ada", confirm=True),
        False,
        None,
    ),
    "proposal_snooze": (
        lambda s, v: s.proposal_snooze(
            f"issue:{CHILD[UNCOVERED]}", by="ada", until="2099-01-01T00:00:00Z", confirm=True
        ),
        False,
        None,
    ),
    "case_accept": (lambda s, v: s.case_accept(v["case_id"], accepted_by="ada", confirm=True), True, _forge_open),
    "context_audit_run": (
        lambda s, v: s.context_audit_run(OWNER[UNCOVERED], repo=UNCOVERED, force=True, confirm=True),
        True,
        _audits,
    ),
    "standard_audit": (
        lambda s, v: s.standard_audit(OWNER[UNCOVERED], "design", UNCOVERED, confirm=True),
        True,
        _standard,
    ),
    "org_act": (
        lambda s, v: s.org_act(OWNER[UNCOVERED], "onboard", [UNCOVERED], confirm=True),
        True,
        _boot_hint,
    ),
}


def test_every_mcp_tool_that_reaches_a_start_is_run_here() -> None:
    found = starter_tools()
    assert sorted(found - set(MCP_STARTS)) == [], "MCP tools that reach a route that starts work, not run here"
    assert sorted(set(MCP_STARTS) - found) == [], "run here, but they reach no route that starts work"


@pytest.mark.parametrize("tool", list(MCP_STARTS))
async def test_an_mcp_tool_starts_work_only_in_the_repositories_the_key_covers(
    tool: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from httpx import ASGITransport

    from embry0.mcp_server import server
    from embry0.mcp_server.client import Embry0APIError, Embry0Client

    db = db_with_migrations
    run, starts, setup = MCP_STARTS[tool]
    seen: dict[str, list[str]] = {}
    async with issue_reads_app(db, monkeypatch) as (app, keys, values):
        started = record(monkeypatch)
        named = dict(values[UNCOVERED]) | dict((await setup(app, db) if setup else None) or {})
        # The limited key first: refused, it leaves the item as it was for the key without a list.
        for token in ("limited", "open"):
            started.made.clear()
            started.went_on.clear()
            client = Embry0Client(
                "http://test",
                api_key=getattr(keys, token),
                transport=ASGITransport(app=app, raise_app_exceptions=False),
            )
            monkeypatch.setattr(server, "_client", client)
            try:
                await run(server, named)
            except Embry0APIError:
                pass
            await client.aclose()
            seen[token] = await outside(db, started)
    assert seen["limited"] == [], f"{tool}: a key covering only {COVERED} started work outside it: {seen['limited']}"
    assert bool(seen["open"]) == starts, f"{tool}: a key without a list starts work there: {seen['open']}"
