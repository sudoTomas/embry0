"""The context audit reads the settings document's ``context_audit`` section at every wake (#170).

Which files a repository's audit reads is the section's ``files`` and
``exclude_dirs`` as the repository resolves them, its organization's layer
and its own over the deployment's; how often the schedule wakes and how many
repositories it reads at once are the deployment's ``check_minutes`` and
``read_concurrency``. The loop reads them through the budget policy's view,
from the snapshot the budget was read from, so these tests hand it a view
over documents they change between wakes, as a person writes them. The
auditor's command is written from the same rules each time a task is, and
for a job built without one, from what the job read when it started.

Each test fails on a tree whose list and cadence are constants: there the
documents below are a section that release does not know, and it reads
nothing of them.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

import pytest

from embry0.services import context_audit
from embry0.services.budget_policy import BudgetView
from embry0.services.context_audit import TEMPLATE, ContextAuditLoop, RepoContext, fingerprint, start_context_audit_job
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from tests.unit.services.test_context_audit import (
    HEAD,
    T0,
    Clock,
    FakeExecutor,
    FakeForge,
    FakeJobs,
    FakeOrgs,
    FakeRegistry,
    FakeTemplates,
    org,
    reg,
)
from tests.unit.services.test_context_audit_command import _run, _state
from tests.unit.services.test_job_settings import _start, _Store

WIDGETS = "acme/widgets"
GADGETS = "globex/gadgets"
PATHSPEC = "':(glob){}'"
#: A glob of the built-in list no organization below names.
BUILT_IN = PATHSPEC.format("**/HANDOFF.md")
CONVENTIONS = PATHSPEC.format("**/CONVENTIONS.md")
STEERING = PATHSPEC.format("**/.kiro/steering/*.md")


def _section_required() -> None:
    if CATALOG.get("context_audit") is None:
        pytest.fail("the settings document has no context_audit section: the audit's files and cadence are code")


class Documents:
    """The settings documents as the loop reads them, through the budget policy's view: each stored scope to its
    values, changed in place between wakes. ``on_read`` runs after each read, with how many there were."""

    def __init__(self, written: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.data: dict[str, dict[str, Any]] = {"deployment": {}}
        for scope, values in (written or {}).items():
            self.write(scope, **values)
        self.reads = 0
        self.on_read: Callable[[int], None] | None = None

    def write(self, scope: str = "deployment", **values: Any) -> None:
        self.data.setdefault(scope, {})["context_audit"] = dict(values)

    async def view(self, organizations: Any = ()) -> BudgetView:
        self.reads += 1
        rows = [
            {"scope": scope, "data": dict(data), "generation": self.reads, "updated_at": None}
            for scope, data in self.data.items()
        ]
        snapshot = build_snapshot(CATALOG, rows, token=(self.reads,), log=False)
        if self.on_read is not None:
            self.on_read(self.reads)
        return BudgetView(snapshot)


class Audits:
    """The production loop over fakes of the forge, the registry and the jobs, its settings read from ``docs``."""

    def __init__(self, docs: Documents, *repos: str) -> None:
        self.clock = Clock(T0)
        self.forge = FakeForge()
        self.jobs = FakeJobs(self.clock)
        self.docs = docs

        async def client_for(_org: Any) -> FakeForge:
            return self.forge

        async def start_job(context: RepoContext, budget_usd: float, open_pr: bool) -> str:
            return await start_context_audit_job(
                context,
                jobs=self.jobs,
                executor=FakeExecutor(),
                templates=FakeTemplates(TEMPLATE),
                budget_usd=budget_usd,
                open_pr=open_pr,
            )

        async def no_gate() -> None:
            return None

        self.loop = ContextAuditLoop(
            organizations=FakeOrgs(org("acme"), org("globex")),
            registry=FakeRegistry(*(reg(repo) for repo in repos)),
            jobs=self.jobs,
            client_for=client_for,
            start_job=start_job,
            ask_gate=no_gate,
            budget=docs.view,
            clock=self.clock,
        )

    def task(self, repo: str) -> str:
        return str(next(r for r in reversed(self.jobs.rows) if r["repo"] == repo)["task"])

    def trees_read(self, repo: str) -> int:
        return sum(1 for call in self.forge.calls if call.startswith(f"/repos/{repo}/git/trees/"))


def _blobs(*paths: str) -> dict[str, str]:
    return {path: hashlib.sha1(path.encode()).hexdigest() for path in paths}


# ---- read at every wake ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wake_reads_the_files_each_repository_resolves_from_the_documents_as_they_are_then() -> None:
    docs = Documents()
    world = Audits(docs, WIDGETS, GADGETS)
    world.forge.put(WIDGETS, HEAD, _blobs("CLAUDE.md", "CONVENTIONS.md", "src/app.py"))
    world.forge.put(GADGETS, HEAD, _blobs("CLAUDE.md", "CONVENTIONS.md"))
    assert sorted(await world.loop.tick()) == [WIDGETS, GADGETS]
    assert "- CONVENTIONS.md" not in world.task(WIDGETS) and BUILT_IN in world.task(WIDGETS), "nothing written yet"
    for repo in (WIDGETS, GADGETS):
        world.jobs.finish(repo, "completed", T0 + timedelta(minutes=10))
    trees = world.trees_read(WIDGETS)

    # An operator names the organization's own files; nothing restarts.
    docs.write("organization:acme", files=["**/CLAUDE.md", "**/CONVENTIONS.md"])
    world.clock.now = T0 + timedelta(minutes=15)
    started = await world.loop.tick()

    assert started == [WIDGETS], (
        "the wake after the write did not read the organization's files: its repository's files changed and it "
        f"is due again, its other organization's not ({started})"
    )
    task = world.task(WIDGETS)
    assert "Instruction files (2):\n- CLAUDE.md\n- CONVENTIONS.md\n" in task, "the task names the files it selected"
    assert CONVENTIONS in task and BUILT_IN not in task, "the command lists the organization's files, whole"
    assert world.trees_read(WIDGETS) == trees + 1, "the head did not move, but the rules did: the tree is read again"
    survey = await world.loop.survey(org("globex"))
    assert survey.repos[0].context.files == ("CLAUDE.md",), "another organization still reads the built-in list"


@pytest.mark.asyncio
async def test_a_survey_that_reads_no_forge_never_serves_files_selected_with_other_rules() -> None:
    docs = Documents()
    world = Audits(docs, WIDGETS)
    world.forge.put(WIDGETS, HEAD, _blobs("CLAUDE.md", "CONVENTIONS.md"))
    assert (await world.loop.survey(org("acme"))).repos[0].context.files == ("CLAUDE.md",)
    docs.write(f"repository:{WIDGETS}", files=["**/CONVENTIONS.md"])
    later = await world.loop.survey(org("acme"), refresh=False)
    assert later.repos[0].context.files == ("CONVENTIONS.md",), (
        "the dashboard's read without refresh served a reading taken with the rules before the write"
    )
    assert (await world.loop.survey(org("acme"), refresh=False)).repos[0].context.files == ("CONVENTIONS.md",)
    assert world.trees_read(WIDGETS) == 2, "a reading with the current rules is served again without a read"


@pytest.mark.asyncio
async def test_a_wake_reads_one_snapshot_and_a_write_during_it_reaches_the_next() -> None:
    docs = Documents()
    world = Audits(docs, WIDGETS)
    world.forge.put(WIDGETS, HEAD, _blobs("CLAUDE.md", "CONVENTIONS.md"))
    # A person's write lands while the first wake runs, right after it read the documents.
    docs.on_read = lambda n: docs.write("organization:acme", files=["**/CONVENTIONS.md"]) if n == 1 else None

    assert await world.loop.tick() == [WIDGETS]
    assert docs.reads == 1, f"one wake read the documents {docs.reads} times: it could mix two versions"
    assert BUILT_IN in world.task(WIDGETS) and "- CLAUDE.md" in world.task(WIDGETS), (
        "the first wake mixed the write that landed during it into what it had read"
    )
    world.jobs.finish(WIDGETS, "completed", T0 + timedelta(minutes=5))
    world.clock.now = T0 + timedelta(minutes=15)
    assert await world.loop.tick() == [WIDGETS], "the next wake did not read the write"
    assert CONVENTIONS in world.task(WIDGETS) and BUILT_IN not in world.task(WIDGETS)


@pytest.mark.asyncio
async def test_a_wake_reads_as_many_repositories_at_once_as_read_concurrency_says() -> None:
    names = [f"acme/r{i:02}" for i in range(20)]
    docs = Documents({"deployment": {"read_concurrency": 2}})
    world = Audits(docs, *names)
    world.forge.yields = True
    for name in names:
        world.forge.put(name, HEAD, _blobs("CLAUDE.md"))
    await world.loop.survey(org("acme"))
    assert world.forge.most_in_flight == 2, f"read {world.forge.most_in_flight} at once with read_concurrency 2"
    docs.write(read_concurrency=5)
    world.forge.most_in_flight = 0
    await world.loop.survey(org("acme"))
    assert world.forge.most_in_flight == 5, f"read {world.forge.most_in_flight} at once after read_concurrency 5"


class _Stop(BaseException):
    """Ends the loop's ``start`` from a test: it logs and goes on after an Exception."""


@pytest.mark.asyncio
async def test_the_schedule_wakes_every_check_minutes_and_a_shorter_one_applies_within_15_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = Documents({"deployment": {"check_minutes": 60}})
    world = Audits(docs)
    real_sleep = asyncio.sleep
    slept: list[float] = []
    woke: list[int] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 5:
            docs.write(check_minutes=15)  # written during the second wait, after its first quarter hour
        await real_sleep(0)

    async def tick() -> list[str]:
        woke.append(len(slept))
        if len(woke) == 2:
            raise _Stop
        return []

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(world.loop, "tick", tick)
    with pytest.raises(_Stop):
        await world.loop.start()

    assert woke == [4, 5], (
        f"with check_minutes 60 the first wake comes after four quarter hours, and 15 written during the next "
        f"wait applies within one: woke after sleeps {woke}"
    )
    assert slept == [900] * 5, slept


@pytest.mark.asyncio
async def test_with_no_snapshot_to_read_a_wake_starts_nothing_and_the_wait_keeps_its_interval() -> None:
    unreadable = getattr(context_audit, "SettingsUnreadable", None)
    assert unreadable is not None, "no SettingsUnreadable: a wake that cannot read its files would guess them"
    docs = Documents({"deployment": {"check_minutes": 30}})
    world = Audits(docs, WIDGETS)
    world.forge.put(WIDGETS, HEAD, _blobs("CLAUDE.md"))
    assert await world.loop.interval_seconds() == 1800

    async def none(organizations: Any = ()) -> BudgetView:
        return BudgetView(None)

    world.loop._budget = none  # noqa: SLF001 - the reader production built it with, now unable to read
    with pytest.raises(unreadable):
        await world.loop.tick()
    assert world.jobs.rows == [], "a wake that could not read the files of any repository started a job"
    assert await world.loop.interval_seconds() == 1800, "the wait keeps the interval it last read"


def test_a_context_cannot_be_built_without_the_rules_that_selected_its_files() -> None:
    # A builder that forgot them would hand the agent the built-in list for a repository that names its own.
    try:
        RepoContext("acme/widgets", HEAD, ("CLAUDE.md",), "fp")  # type: ignore[call-arg]
    except TypeError:
        return
    pytest.fail("a RepoContext built without rules reads the built-in list in silence")


# ---- the auditor's command, written for each run ------------------------------------------


@pytest.mark.asyncio
async def test_each_job_s_auditor_is_handed_the_files_its_repository_read_when_the_job_started() -> None:
    # A job created with POST /jobs carries no command; the agent node writes it from the job's own reading.
    store = _Store(**{"organization:acme": {"context_audit": {"files": ["**/CONVENTIONS.md"]}}})
    first = await _start(store, WIDGETS)
    assert "context_audit" in (first.get("job_settings") or {}), (
        "the job did not read the context_audit section when it started: its auditor gets the built-in list"
    )
    store.write(**{"organization:acme": {"context_audit": {"files": ["**/.kiro/steering/*.md"]}}})
    second = await _start(store, WIDGETS, job_id="job-2")
    elsewhere = await _start(store, GADGETS, job_id="job-3")

    async def prompt(state: dict[str, Any]) -> str:
        ran = await _run({**_state("Audit the agent instructions"), "job_settings": state["job_settings"]}, None)
        return str(ran["prompt"])

    one, two, other = [await prompt(state) for state in (first, second, elsewhere)]
    assert CONVENTIONS in one and STEERING not in one and BUILT_IN not in one, "the first job's own files, whole"
    assert STEERING in two and CONVENTIONS not in two, "the next job's command follows the write, with no restart"
    assert BUILT_IN in other and CONVENTIONS not in other, "a repository of another organization reads the built-in"
    from_before = await _run(_state("Audit the agent instructions"), None)
    assert BUILT_IN in from_before["prompt"], "a job that started before the section reads the built-in list"


# ---- with the defaults, nothing differs ----------------------------------------------------

#: What origin/main (f677bb90), whose list and cadence were constants, wrote for the audit of acme/widgets at
#: HEAD with CLAUDE.md and docs/AGENTS.md: the sha256 of the whole task, and of the command alone.
MAIN_TASK_SHA256 = "467ad4f1a39243197f8a4f7fb21251bd9874b84fb94b4e7849093ad06a5495e7"
MAIN_COMMAND_SHA256 = "27aef1a865448cf3d4f5050dddc970edaf001c03938ba9cf43ffcc824f80af1c"
#: Paths on both sides of the built-in list, and what origin/main selected of them.
CORPUS = (
    *("CLAUDE.md", "apps/hub/src/app/api/CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md"),
    *("services/api/GEMINI.md", "notes/HANDOFF.md", "llms.txt", ".cursorrules", "sub/.windsurfrules"),
    *("sub/.clinerules", ".github/copilot-instructions.md", ".github/instructions/frontend.instructions.md"),
    *(".github/instructions/deep/api.instructions.md", ".cursor/rules/style.mdc", "pkg/.cursor/rules/deep/style.md"),
    *(".claude/skills/verify-app/SKILL.md", "apps/hub/.claude/commands/ship.md", ".claude/agents/reviewer.md"),
    *(".claude/rules/tests.md", ".grok/skills/verify/SKILL.md", ".grok/rules/style.md", ".codex/prompts/review.md"),
    *(".agents/notes/a.md", "README.md", "docs/CLAUDE.md.bak", "docs/MY-CLAUDE.md"),
    *("apps/.github/copilot-instructions.md", ".github/instructions/notes.md", ".claude/settings.json"),
    *("src/claude.ts", "node_modules/pkg/CLAUDE.md", "frontend/node_modules/x/.claude/skills/a/SKILL.md"),
    *("vendor/lib/AGENTS.md", "build/CLAUDE.md", "a/dist/b/AGENTS.md", ".venv/lib/CLAUDE.md", "venv/AGENTS.md"),
    *("CONVENTIONS.md", ".roo/rules/a.md", ".kiro/steering/b.md", "docs/agents/c.md", "x/llms.txt"),
    *("x/.codex/y/z.md", "claude.md", "Agents.md"),
)
MAIN_SELECTED = [
    *(".agents/notes/a.md", ".claude/agents/reviewer.md", ".claude/rules/tests.md"),
    *(".claude/skills/verify-app/SKILL.md", ".codex/prompts/review.md", ".cursor/rules/style.mdc", ".cursorrules"),
    *(".github/copilot-instructions.md", ".github/instructions/deep/api.instructions.md"),
    *(".github/instructions/frontend.instructions.md", ".grok/rules/style.md", ".grok/skills/verify/SKILL.md"),
    *("AGENTS.md", "AGENTS.override.md", "CLAUDE.local.md", "CLAUDE.md", "apps/hub/.claude/commands/ship.md"),
    *("apps/hub/src/app/api/CLAUDE.md", "llms.txt", "notes/HANDOFF.md", "pkg/.cursor/rules/deep/style.md"),
    *("services/api/GEMINI.md", "sub/.clinerules", "sub/.windsurfrules", "x/.codex/y/z.md", "x/llms.txt"),
]
#: The section written out at its defaults, in each place a person could write it.
AT_DEFAULTS = {"files": None, "exclude_dirs": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "written",
    [
        {},
        {"deployment": {**AT_DEFAULTS, "check_minutes": 15, "read_concurrency": 8}},
        {"organization:acme": AT_DEFAULTS, f"repository:{WIDGETS}": AT_DEFAULTS},
    ],
    ids=["nothing-written", "deployment-at-defaults", "layers-at-defaults"],
)
async def test_at_its_defaults_the_audit_selects_writes_and_waits_byte_for_byte_as_before(
    written: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _section_required()
    from embry0.services.context_audit_command import command
    from embry0.services.instruction_files import DEFAULT_RULES

    assert DEFAULT_RULES.select(CORPUS) == MAIN_SELECTED, "the built-in rules select other files than before"
    assert hashlib.sha256(command(DEFAULT_RULES).encode()).hexdigest() == MAIN_COMMAND_SHA256, (
        "the built-in command differs from the one the release before wrote"
    )
    docs = Documents(written)
    world = Audits(docs, WIDGETS)
    blobs = _blobs("CLAUDE.md", "docs/AGENTS.md", "README.md", "vendor/x/AGENTS.md", "CONVENTIONS.md")
    world.forge.put(WIDGETS, HEAD, blobs)

    assert await world.loop.tick() == [WIDGETS]
    row = world.jobs.rows[-1]
    assert hashlib.sha256(str(row["task"]).encode()).hexdigest() == MAIN_TASK_SHA256, (
        f"the task differs from the one the release before wrote for the same files:\n{row['task']}"
    )
    assert row["pipeline_config"]["context_fingerprint"] == fingerprint(
        {path: blobs[path] for path in ("CLAUDE.md", "docs/AGENTS.md")}
    ), "the fingerprint differs: every repository would be due again after the upgrade"
    settings = await world.loop.settings()
    assert (settings.read_concurrency, settings.interval_seconds) == (8, 900)

    real_sleep = asyncio.sleep
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    await world.loop.wait()
    assert repr(slept) == "[900]", f"the wait between two wakes was {slept}, not the one sleep(900) it was"
