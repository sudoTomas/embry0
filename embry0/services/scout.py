"""Scout (docs/leads.md, "The scout"): a local model that keeps finding work, around the clock.

The loop rotates through the repositories the world map shows, and each area of
one that declares areas, starting a read-only scouting job on each in turn:
``garden`` (one provable weed) and ``kintsugi-trace`` (a ledger of graded
cracks). Every job is pinned to the operator's local model (``SCOUT_MODEL``),
so the loop never spends on a hosted model: a pinned run waits for a free local
slot instead of moving to one.

When a job finishes, its deliverable becomes leads: one ``weed`` per weed card
and one ``crack`` per ledger row, the worst first and at most
``SCOUT_MAX_LEADS_PER_JOB``. The owner proposals feed offers them like every
other lead; a crack below ``break`` and every weed stay on the page instead of
being asked in chat (``embry0.services.proposal_briefs.held``).

A unit (repository, area, template) is scouted again after
``SCOUT_REVISIT_HOURS``: a model's reading differs run to run, and the lead id
of what it found before keeps a repeat from becoming a second lead.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog

from embry0.orchestration import dispatch_actors
from embry0.services.areas import Area
from embry0.services.leads import Lead, by_severity

logger = structlog.get_logger(__name__)

#: The actor of every job the loop starts, in ``jobs.dispatched_by`` and the per-actor rate limit.
ACTOR = dispatch_actors.SCOUT
#: The templates a scout may run: read-only, each with a deliverable this module reads.
TEMPLATES: tuple[str, ...] = ("garden", "kintsugi-trace")
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


@dataclass(frozen=True)
class ScoutSettings:
    #: The model every scouting job runs on; empty turns the loop off.
    model: str = ""
    templates: tuple[str, ...] = TEMPLATES
    max_in_flight: int = 2
    revisit_hours: float = 24.0
    tick_seconds: float = 300.0
    max_leads_per_job: int = 5
    #: The hard cap of a job, in case its pin is ever lost: a local model bills nothing.
    budget_usd: float = 1.0


def scout_settings(config: Any) -> ScoutSettings:
    names = [t.strip() for t in str(getattr(config, "scout_templates", "") or "").split(",") if t.strip()]
    unknown = sorted(set(names) - set(TEMPLATES))
    if unknown:
        logger.warning("scout_unknown_templates", unknown=unknown, known=list(TEMPLATES))
    templates = tuple(t for t in TEMPLATES if t in names) if names else TEMPLATES
    return ScoutSettings(
        model=str(getattr(config, "scout_model", "") or "").strip(),
        templates=templates or TEMPLATES,
        max_in_flight=max(0, int(getattr(config, "scout_max_in_flight", 2))),
        revisit_hours=float(getattr(config, "scout_revisit_hours", 24)),
        tick_seconds=max(30.0, float(getattr(config, "scout_tick_seconds", 300))),
        max_leads_per_job=max(1, int(getattr(config, "scout_max_leads_per_job", 5))),
    )


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
    candidates: Iterable[Unit], last: dict[tuple[str, str, str], datetime], now: datetime, revisit: timedelta
) -> list[Unit]:
    """The units to scout, never-scouted first, then the longest ago; one scouted within ``revisit`` waits."""
    never = datetime.min.replace(tzinfo=UTC)
    waiting = [u for u in candidates if last.get(u.key) is None or now - last[u.key] >= revisit]
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


@dataclass
class ScoutLoop:
    settings: ScoutSettings
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

    @property
    def enabled(self) -> bool:
        return bool(self.settings.model) and self.settings.max_in_flight > 0

    async def harvest(self) -> int:
        """Turn every finished scouting job into leads, once; how many leads were stored."""
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
                    self.settings.max_leads_per_job,
                )
            if found:
                area = str(run.get("area") or "") or None
                await self.leads.upsert(found, now=now, areas={lead.id: area for lead in found})
            await self.runs.harvested(str(run["job_id"]), leads=len(found), now=now)
            stored += len(found)
            logger.info("scout_harvested", job_id=run["job_id"], status=status or "gone", leads=len(found))
        return stored

    async def dispatch(self) -> list[str]:
        """Start the most overdue units while fewer than ``max_in_flight`` scouting jobs are out."""
        in_flight = len(await self.runs.unharvested())
        room = self.settings.max_in_flight - in_flight
        if room <= 0:
            return []
        now = self.clock()
        candidates: list[Unit] = []
        for repo in await self.repos():
            try:
                areas = await self.areas_for(repo)
            except Exception:  # noqa: BLE001 - a repository we cannot read is scouted whole
                areas = []
            candidates += units(repo, areas, self.settings.templates)
        started: list[str] = []
        for unit in due(
            candidates, await self.runs.last_dispatched(), now, timedelta(hours=self.settings.revisit_hours)
        ):
            if len(started) >= room:
                break
            refusal = await self.ask_gate()
            if refusal is not None:
                logger.info("scout_gate_refused", reason=getattr(refusal, "message", str(refusal))[:200])
                break
            job_id = await self._start(unit, now)
            started.append(job_id)
        return started

    async def _start(self, unit: Unit, now: datetime) -> str:
        text = task(unit)
        job_id = await self.jobs.create(
            repo=unit.repo,
            task=text,
            context={"type": "git", "repo": unit.repo},
            pipeline_template=unit.template,
            pipeline_config={
                # A pin, not a preference: with every local slot taken the run waits for one.
                "agent_models_override": {"*": self.settings.model},
                "max_budget_usd": self.settings.budget_usd,
                "budget_overrun_mode": "hard",
                "actor": ACTOR,
            },
            trace_id=f"trc-{uuid.uuid4().hex[:12]}",
            dispatched_by=ACTOR,
        )
        await self.jobs.update(job_id, job_kind="analysis", **({"area": unit.area} if unit.area else {}))
        await self.runs.record(job_id=job_id, repo=unit.repo, area=unit.area, template=unit.template, now=now)
        await self.submit(job_id, unit, text)
        logger.info("scout_started", job_id=job_id, repo=unit.repo, area=unit.area, template=unit.template)
        return job_id

    async def tick(self) -> dict[str, Any]:
        harvested = await self.harvest()
        started = await self.dispatch() if self.enabled else []
        return {"harvested_leads": harvested, "started": started}

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        if not self.enabled:
            logger.info("scout_off", reason="SCOUT_MODEL is empty" if not self.settings.model else "max_in_flight 0")
            return
        logger.info("scout_on", model=self.settings.model, templates=list(self.settings.templates))
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("scout_tick_failed", exc_info=True)
            await asyncio.sleep(self.settings.tick_seconds)
