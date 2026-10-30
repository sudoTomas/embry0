"""Scout (docs/leads.md, "The scout"): a local model that keeps finding work, around the clock.

The loop rotates through the repositories the world map shows, and each area of
one that declares areas, starting a read-only scouting job on each in turn:
``garden`` (one provable weed) and ``kintsugi-trace`` (a ledger of graded
cracks). Every job is pinned to the scout's model, ``background_models.scout``
of the settings document (``false`` until a model is named for it), read
every tick for each repository (an organization may set its own): a pinned
run waits for its model, a free slot of the operator's own server for a
local one, instead of moving to another. A repository whose organization
runs the scout on no model is not scouted. A hosted model is metered and capped like any job,
and each job is held to ``scout.job_budget_usd``.

Its policy is the settings document's ``scout`` section
(:mod:`embry0.settings.sections.scout`), read at the top of every tick, so a
change applies on the next one: which templates run, how many jobs are out at
once and start in a day, each job's hard cap, how often a unit comes back and
how many leads a job adds. A tick takes one snapshot of the settings
documents and reads both this section and the scout's model from it, so a
write that lands during a tick cannot hand it two versions of them
(:func:`tick_settings`). It reads the section per repository: which
templates run on a repository, how often it is scouted again and how
many leads one of its jobs adds follow its organization's layer, and the
deployment's document for the rest. How many jobs are out at once, how many
start in a day and each job's hard cap are the deployment's alone: they bound
what the scout starts and spends across every organization.

When a job finishes, its deliverable becomes leads: one ``weed`` per weed card
and one ``crack`` per ledger row, the worst first and at most
``scout.max_leads_per_job``. The owner proposals feed offers them like every
other lead; a crack below ``break`` and every weed stay on the page instead of
being asked in chat (``embry0.services.proposal_briefs.held``).

A unit (repository, area, template) is scouted again after
``scout.revisit_hours``: a model's reading differs run to run, and the lead id
of what it found before keeps a repeat from becoming a second lead.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog

from embry0.orchestration import dispatch_actors
from embry0.services.areas import Area
from embry0.services.background_models import ModelLookup, model_lookup
from embry0.services.leads import Lead, by_severity
from embry0.settings.layers import PerRepository
from embry0.settings.sections.scout import SCOUT, ScoutSettings

logger = structlog.get_logger(__name__)

#: The actor of every job the loop starts, in ``jobs.dispatched_by`` and the per-actor rate limit.
ACTOR = dispatch_actors.SCOUT
#: Seconds between ticks. Not a setting: what the scout starts is bounded by the
#: section's caps, and a tick only says how soon it looks again, and so how soon
#: a change to the section applies.
TICK_SECONDS = 300.0
#: Statuses after which a job's deliverable is final.
_DONE = frozenset({"completed", "failed", "partial", "cancelled", "expired", "skipped"})
#: Kintsugi grades to lead severities (embry0/workflows/issue_to_pr/specialist_agents.py, Grades).
_GRADES = {"break": "high", "fracture": "medium", "hairline": "low"}
_SIZES = ("small", "medium", "large")
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w]{0,7})(?::(\d+))?")
#: Extensions that make a bare name a file, not ``userInfo.name`` or ``console.log`` in a quoted line.
_FILE_EXTENSIONS = frozenset(
    "py pyi js mjs cjs ts tsx jsx java kt kts go rs rb php cs swift scala c h cpp hpp sql md mdx rst txt "
    "yml yaml json jsonc toml ini cfg conf env lock sh bash ps1 html htm css scss sass less vue svelte xml "
    "gradle properties tf hcl proto graphql csv ipynb dockerfile".split()
)


def warn_retired_tick(config: Any) -> bool:
    """Warn once, at startup, while ``SCOUT_TICK_SECONDS`` is set: it is no longer read.

    In the shape of ``settings_env_ignored``: the variable, whether its value
    is what runs now (``matches``), and what to do. The value itself is not
    logged. Whether the variable is set, from the environment or ``.env``.
    """
    if "scout_tick_seconds" not in (getattr(config, "model_fields_set", None) or set()):
        return False
    try:
        matches = float(str(getattr(config, "scout_tick_seconds", "")).strip()) == TICK_SECONDS
    except ValueError:
        matches = False
    logger.warning(
        "settings_env_retired",
        variable="SCOUT_TICK_SECONDS",
        matches=matches,
        hint=f"no longer read: the scout ticks every {TICK_SECONDS:g} s, a constant; remove the variable",
    )
    return True


# ---- what one unit is asked ----


@dataclass(frozen=True)
class Unit:
    repo: str
    #: The area's name, '' for a whole repository.
    area: str
    template: str
    paths: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.repo.lower(), self.area, self.template)


def units(repo: str, areas: Sequence[Area], templates: Iterable[str]) -> list[Unit]:
    """Each template on each area of ``repo``, or on the whole repository when it declares none."""
    out: list[Unit] = []
    for template in templates:
        if areas:
            out += [Unit(repo, a.name, template, a.paths) for a in areas if a.paths]
        else:
            out.append(Unit(repo, "", template))
    return out


def task(unit: Unit) -> str:
    where = unit.repo
    scope = ""
    if unit.area:
        where = f"the {unit.area} area of {unit.repo}"
        scope = "\n\nLook only under these paths: " + ", ".join(unit.paths) + "."
    if unit.template == "garden":
        return (
            f"Find the one weed in {where} most worth pulling: code that is dead, duplicated or carries "
            "nothing, with the proof, the diff that removes it and the check that shows nothing changed." + scope
        )
    return (
        f"Trace {where}: run its own gates, read the code against its docs, and deliver a ledger of "
        "graded cracks with the evidence for each." + scope
    )


def due(
    candidates: Iterable[Unit],
    last: dict[tuple[str, str, str], datetime],
    now: datetime,
    revisit: Mapping[str, timedelta],
) -> list[Unit]:
    """The units to scout, never-scouted first, then the longest ago; one scouted within its
    repository's ``revisit`` (keyed by ``Unit.repo``) waits."""
    never = datetime.min.replace(tzinfo=UTC)
    waiting = [u for u in candidates if last.get(u.key) is None or now - last[u.key] >= revisit[u.repo]]
    return sorted(waiting, key=lambda u: last.get(u.key) or never)


# ---- what a finished job found ----


def _clean(text: str) -> str:
    return " ".join(re.sub(r"\*\*|`", "", text).split())


def _paths(text: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for m in _PATH.finditer(text or ""):
        path = m.group(1).lstrip("./")
        if "/" in path or path.rsplit(".", 1)[-1].lower() in _FILE_EXTENSIONS:
            seen.setdefault(path, None)
    return tuple(seen)


def _key_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()[:80]


def parse_ledger(report: str | None) -> list[dict[str, str]]:
    """The kintsugi ledger's rows: ``{id, crack, evidence, grade, seal}``; [] without a ledger."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in (report or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if header is not None and rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if header is None:
            if "crack" in lowered and "grade" in lowered:
                header = lowered
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) < len(header):
            continue
        row = dict(zip(header, cells, strict=False))
        if row.get("crack"):
            rows.append({k: row.get(k, "") for k in ("id", "crack", "evidence", "grade", "seal")})
    return rows


_LABELS = ("weed kind", "weed", "proof", "pull", "check", "catch it next time", "assumptions")
_LABEL = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#{1,4}\s*)?\**\s*(" + "|".join(re.escape(x) for x in _LABELS) + r")\s*\**\s*(?:[-:—]\s*)?",
    re.IGNORECASE,
)


def parse_weed(report: str | None) -> dict[str, str] | None:
    """The weed card's fields (``weed kind``, ``weed``, ``proof``, ``pull``, ``check``); None for no weed."""
    text = report or ""
    if re.search(r"no weeds found", text, re.IGNORECASE) and not re.search(r"^diff --git", text, re.MULTILINE):
        return None
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _LABEL.match(line)
        if m and not line.lstrip().startswith(("diff ", "---", "+++", "@@")):
            current = m.group(1).lower()
            fields[current] = [line[m.end() :]]
        elif current is not None:
            fields[current].append(line)
    card = {k: "\n".join(v).strip() for k, v in fields.items()}
    if not card.get("weed"):
        return None
    return card


def crack_leads(repo: str, area: str, job_id: str, report: str | None) -> list[Lead]:
    out: list[Lead] = []
    for row in parse_ledger(report):
        crack = _clean(row["crack"])
        grade = _clean(row["grade"]).lower().split(" ")[0] if row["grade"] else ""
        seal = _clean(row["seal"])
        evidence = _clean(row["evidence"])
        size = next((s for s in _SIZES if re.search(rf"\b{s}\b", seal.lower())), "medium")
        paths = _paths(row["evidence"])
        out.append(
            Lead(
                kind="crack",
                repo=repo,
                source_key=f"{area}:{paths[0] if paths else ''}:{_key_text(crack)}",
                title=crack[:200],
                summary=f"A {grade or 'graded'} crack the scout found in {repo.split('/', 1)[-1]}: {crack}"[:1000],
                details="\n".join(
                    part
                    for part in (
                        f"Grade: {grade}" if grade else "",
                        f"Evidence: {evidence}" if evidence else "",
                        f"Seal: {seal}" if seal else "",
                        f"Found by scout job {job_id}.",
                    )
                    if part
                ),
                action="Mend it: the agents fix what the crack names and open a pull request",
                severity=_GRADES.get(grade, "medium"),
                effort=size,
                paths=paths,
                data={
                    "job_id": job_id,
                    "template": "kintsugi-trace",
                    "grade": grade,
                    "evidence": evidence,
                    "seal": seal,
                },
            )
        )
    return by_severity(out)


def weed_leads(repo: str, area: str, job_id: str, report: str | None) -> list[Lead]:
    card = parse_weed(report)
    if card is None:
        return []
    weed = _clean(card["weed"])
    kind = _clean(card.get("weed kind", ""))
    pull = card.get("pull", "")
    diff = re.search(r"```diff\n(.*?)```", pull, re.DOTALL)
    diff_text = diff.group(1) if diff else pull
    paths = tuple(dict.fromkeys(re.findall(r"^diff --git a/(\S+)", diff_text, re.MULTILINE))) or _paths(weed)
    return [
        Lead(
            kind="weed",
            repo=repo,
            source_key=f"{area}:{','.join(sorted(paths)) or _key_text(weed)}",
            title=f"Weed: {weed}"[:200],
            summary=f"The scout found a weed in {repo.split('/', 1)[-1]}"
            + (f" ({kind})" if kind else "")
            + f": {weed}",
            details="\n".join(
                part
                for part in (
                    f"Kind: {kind}" if kind else "",
                    f"Proof: {_clean(card.get('proof', ''))[:1500]}" if card.get("proof") else "",
                    f"Check: {_clean(card.get('check', ''))[:500]}" if card.get("check") else "",
                    f"Found by scout job {job_id}.",
                )
                if part
            ),
            action="Pull it: the agents apply the checked diff, change no behavior, and open a pull request",
            severity="low",
            effort="small",
            paths=paths,
            data={"job_id": job_id, "template": "garden", "weed_kind": kind, "pull": diff_text[:6000]},
        )
    ]


def leads_of(template: str, repo: str, area: str, job_id: str, report: str | None, limit: int) -> list[Lead]:
    found = weed_leads(repo, area, job_id, report) if template == "garden" else crack_leads(repo, area, job_id, report)
    return found[:limit]


# ---- the loop ----


class _Runs(Protocol):
    async def record(self, *, job_id: str, repo: str, area: str, template: str, now: datetime) -> None: ...
    async def last_dispatched(self) -> dict[tuple[str, str, str], datetime]: ...
    async def dispatched_since(self, since: datetime) -> int: ...
    async def unharvested(self) -> list[dict[str, Any]]: ...
    async def harvested(self, job_id: str, *, leads: int, now: datetime) -> None: ...


class _Jobs(Protocol):
    async def create(self, **kwargs: Any) -> str: ...
    async def update(self, job_id: str, **kwargs: Any) -> Any: ...
    async def get(self, job_id: str) -> dict[str, Any] | None: ...


class _Leads(Protocol):
    async def upsert(
        self, leads: Sequence[Lead], *, now: datetime, areas: dict[str, str | None] | None = None
    ) -> None: ...


@dataclass(frozen=True)
class TickSettings:
    """What one tick of the scout reads, all from one snapshot of the settings documents."""

    #: The ``scout`` section, read per repository.
    section: PerRepository[ScoutSettings]
    #: ``repo -> model`` every scouting job of that repository is pinned to (``background_models.scout``,
    #: per repository); '' leaves the repository unscouted.
    models: ModelLookup


def tick_settings(store: Any) -> Callable[[], Awaitable[TickSettings]]:
    """The reader the scout is built with: each call takes one snapshot of the settings documents
    (``store.snapshot()``) and reads both the ``scout`` section and ``background_models.scout`` from it."""

    async def read() -> TickSettings:
        snapshot = await store.snapshot()
        return TickSettings(section=snapshot.scoped(SCOUT), models=model_lookup(snapshot, "scout"))

    return read


@dataclass
class ScoutLoop:
    #: What a tick reads, from one snapshot of the settings documents, at the top of every tick:
    #: ``tick_settings(store)`` in production.
    settings: Callable[[], Awaitable[TickSettings]]
    runs: _Runs
    jobs: _Jobs
    leads: _Leads
    #: The repositories in scope (the world map's, plus and minus the operator's lists).
    repos: Callable[[], Awaitable[list[str]]]
    #: A repository's areas; [] when it declares none or cannot be read.
    areas_for: Callable[[str], Awaitable[list[Area]]]
    #: The executor's dispatch gate for an actor: None to go, else the refusal.
    ask_gate: Callable[[], Awaitable[Any]]
    #: Starts a created job on the executor (and writes its audit row).
    submit: Callable[[str, Unit, str], Awaitable[None]]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    #: Whether the last tick could start jobs (``max_in_flight`` above 0); None before the first.
    _starting: bool | None = field(default=None, init=False, repr=False)
    #: Whether the last dispatch found a model for any repository in scope; None before the first.
    _modelled: bool | None = field(default=None, init=False, repr=False)

    async def harvest(self, read: TickSettings | None = None) -> int:
        """Turn every finished scouting job into leads, once, at most its repository's
        ``max_leads_per_job`` each; how many leads were stored."""
        if read is None:
            read = await self.settings()
        stored = 0
        now = self.clock()
        for run in await self.runs.unharvested():
            job = await self.jobs.get(str(run["job_id"]))
            status = str((job or {}).get("status") or "")
            if job is not None and status not in _DONE:
                continue
            found: list[Lead] = []
            if job is not None and status == "completed":
                found = leads_of(
                    str(run["template"]),
                    str(run["repo"]),
                    str(run.get("area") or ""),
                    str(run["job_id"]),
                    job.get("result_summary"),
                    read.section.repo(str(run["repo"])).max_leads_per_job,
                )
            if found:
                area = str(run.get("area") or "") or None
                await self.leads.upsert(found, now=now, areas={lead.id: area for lead in found})
            await self.runs.harvested(str(run["job_id"]), leads=len(found), now=now)
            stored += len(found)
            logger.info("scout_harvested", job_id=run["job_id"], status=status or "gone", leads=len(found))
        return stored

    async def dispatch(self, read: TickSettings | None = None) -> list[str]:
        """Start the most overdue units while fewer than ``max_in_flight`` scouting jobs are out,
        and fewer than ``max_jobs_per_day`` started in the last 24 hours (the deployment's), each
        pinned to its repository's model; a repository with none is not scouted. Each repository
        runs its own ``templates`` and waits its own ``revisit_hours``."""
        if read is None:
            read = await self.settings()
        section, lookup = read.section, read.models
        policy = section.deployment()
        in_flight = len(await self.runs.unharvested())
        room = policy.max_in_flight - in_flight
        if room <= 0:
            return []
        now = self.clock()
        if policy.max_jobs_per_day is not None:
            room = min(room, policy.max_jobs_per_day - await self.runs.dispatched_since(now - timedelta(days=1)))
            if room <= 0:
                return []
        modelled = {repo: lookup(repo).strip() for repo in await self.repos()}
        modelled = {repo: model for repo, model in modelled.items() if model}
        if bool(modelled) != self._modelled:
            if self._modelled is not None or not modelled:
                logger.info(
                    "scout_models" if modelled else "scout_no_model",
                    repos=len(modelled),
                    reason="background_models.scout names no model for them",
                )
            self._modelled = bool(modelled)
        candidates: list[Unit] = []
        revisit: dict[str, timedelta] = {}
        for repo in modelled:
            own = section.repo(repo)
            try:
                areas = await self.areas_for(repo)
            except Exception:  # noqa: BLE001 - a repository we cannot read is scouted whole
                areas = []
            candidates += units(repo, areas, own.templates)
            revisit[repo] = timedelta(hours=own.revisit_hours)
        started: list[str] = []
        for unit in due(candidates, await self.runs.last_dispatched(), now, revisit):
            if len(started) >= room:
                break
            refusal = await self.ask_gate()
            if refusal is not None:
                logger.info("scout_gate_refused", reason=getattr(refusal, "message", str(refusal))[:200])
                break
            job_id = await self._start(unit, now, policy, modelled[unit.repo])
            started.append(job_id)
        return started

    async def _start(self, unit: Unit, now: datetime, policy: ScoutSettings, model: str) -> str:
        text = task(unit)
        job_id = await self.jobs.create(
            repo=unit.repo,
            task=text,
            context={"type": "git", "repo": unit.repo},
            pipeline_template=unit.template,
            pipeline_config={
                # A pin, not a preference: with every local slot taken the run waits for one.
                "agent_models_override": {"*": model},
                "max_budget_usd": policy.job_budget_usd,
                "budget_overrun_mode": "hard",
                "actor": ACTOR,
            },
            trace_id=f"trc-{uuid.uuid4().hex[:12]}",
            dispatched_by=ACTOR,
        )
        await self.jobs.update(job_id, job_kind="analysis", **({"area": unit.area} if unit.area else {}))
        await self.runs.record(job_id=job_id, repo=unit.repo, area=unit.area, template=unit.template, now=now)
        await self.submit(job_id, unit, text)
        logger.info("scout_started", job_id=job_id, repo=unit.repo, area=unit.area, template=unit.template, model=model)
        return job_id

    async def tick(self, read: TickSettings | None = None) -> dict[str, Any]:
        """One pass with the policy and the models read now (or ``read``, read by the caller this
        tick): one snapshot of the settings documents, which harvest and dispatch read per repository.

        Finished jobs are read into leads whatever the policy; new jobs start
        only with the deployment's ``max_in_flight`` above 0, and only on a
        repository with a model.
        """
        if read is None:
            read = await self.settings()
        starting = read.section.deployment().max_in_flight > 0
        if starting != self._starting:
            # The first tick says only a pause: start() has said the scout is on.
            if self._starting is not None or not starting:
                logger.info("scout_starting" if starting else "scout_paused", reason="scout.max_in_flight")
            self._starting = starting
        harvested = await self.harvest(read)
        started = await self.dispatch(read) if starting else []
        return {"harvested_leads": harvested, "started": started}

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        """Tick for ever; the sections are read every tick, so a change to either needs no restart."""
        logger.info("scout_on", models="background_models.scout, read every tick")
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("scout_tick_failed", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)
