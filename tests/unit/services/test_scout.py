"""Scout (spec 2026-10-27): units, tasks, the due rule, reading deliverables into leads, and the loop."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.orchestration import dispatch_actors
from embry0.services.areas import Area
from embry0.services.scout import (
    ACTOR,
    ScoutLoop,
    Unit,
    crack_leads,
    due,
    parse_ledger,
    parse_weed,
    task,
    units,
    warn_retired_tick,
    weed_leads,
)
from embry0.settings.sections.scout import ScoutSettings

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
LOCAL = "custom/qwen3-27b"

LEDGER = """# Kintsugi trace - acme/app

## Vessel
acme/app at abc123.

## Ledger

| ID | Crack | Evidence | Grade | Seal |
|---|---|---|---|---|
| K-01 | A hardcoded service key is committed | `lib/probe.mjs:4` and `_old/probe.mjs:4` | break | Rotate the key, delete both files (small) |
| K-02 | Status filter matches one spelling only | `server.mjs:218-222` | fracture | Match both spellings (medium, decision first) |
| K-03 | README lists 11 of 41 files | `grep` of README.md -> 11 | hairline | Regenerate the table (small) |

## Gates
- `npm test` -> 0 passing
"""

WEED_HEADINGS = """# Weed card - acme/app

## Weed kind
Dead code (a file nothing references).

## Weed
`lib/probe.mjs` - a one-off connectivity probe nothing imports.

## Proof
`grep -rn probe .` -> no output, exit 1.

## Pull
```diff
diff --git a/lib/probe.mjs b/lib/probe.mjs
deleted file mode 100644
--- a/lib/probe.mjs
+++ /dev/null
@@ -1,2 +0,0 @@
-import pg from "pg";
-console.log(1);
```

## Check
`node --check server.mjs` -> exit 0.
"""

WEED_BULLETS = """Weed kind** - dead code.
- **Weed** - `clearError` in `lib/connections.mjs` is an exported helper nothing calls.
- **Proof** - `grep -n -R clearError .` prints only its definition.
- **Pull** -
```diff
diff --git a/lib/connections.mjs b/lib/connections.mjs
--- a/lib/connections.mjs
+++ b/lib/connections.mjs
@@ -133,5 +133,0 @@
-export async function clearError() {}
```
- **Check** - `npm test` -> 12 passing.
"""


# ---- units, tasks, the due rule ----


def test_a_repository_with_areas_is_scouted_area_by_area_and_one_without_whole() -> None:
    areas = [Area("Leads", ("app/leads/**", "web/leads/")), Area("Empty"), Area("Infra", ("infra/**",))]
    got = units("acme/app", areas, ("garden", "kintsugi-trace"))
    assert [(u.area, u.template) for u in got] == [
        ("Leads", "garden"),
        ("Infra", "garden"),
        ("Leads", "kintsugi-trace"),
        ("Infra", "kintsugi-trace"),
    ], "an area with no paths cannot be scoped, so it is not a unit"
    assert units("acme/lib", [], ("garden",)) == [Unit("acme/lib", "", "garden")]


def test_the_task_names_the_template_s_deliverable_and_an_area_s_paths() -> None:
    garden = task(Unit("acme/app", "", "garden"))
    assert garden.startswith("Find the one weed in acme/app") and "diff that removes it" in garden
    trace = task(Unit("acme/app", "Leads", "kintsugi-trace", ("app/leads/**",)))
    assert trace.startswith("Trace the Leads area of acme/app") and "ledger of graded cracks" in trace
    assert trace.endswith("Look only under these paths: app/leads/**.")


def test_never_scouted_units_go_first_then_the_longest_ago_and_a_recent_one_waits() -> None:
    a, b, c = (Unit("acme/a", "", "garden"), Unit("acme/b", "", "garden"), Unit("acme/c", "", "garden"))
    last = {a.key: T0 - timedelta(hours=30), c.key: T0 - timedelta(hours=2)}
    assert due([a, b, c], last, T0, timedelta(hours=24)) == [b, a]


def _retired_tick_warnings(**env: str) -> tuple[bool, list[dict[str, Any]]]:
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    with capture_logs() as logs:
        warned = warn_retired_tick(config)
    return warned, [e for e in logs if e["event"] == "settings_env_retired"]


@pytest.mark.parametrize(("value", "matches"), [("60", False), ("300", True), ("soon", False)])
def test_a_tick_variable_still_set_is_named_in_one_warning_saying_the_tick_is_fixed(value: str, matches: bool) -> None:
    """SCOUT_TICK_SECONDS is no longer read; the config ignores what it does not read, so only this says so."""
    warned, events = _retired_tick_warnings(SCOUT_TICK_SECONDS=value)
    assert warned and len(events) == 1, (
        f"SCOUT_TICK_SECONDS={value!r} is set and no longer read: startup says so once, not silently: {events}"
    )
    (event,) = events
    assert (event["log_level"], event["variable"], event["matches"]) == ("warning", "SCOUT_TICK_SECONDS", matches)
    assert "300 s" in event["hint"], "the warning says the tick is fixed at 300 s"
    assert set(event) == {"event", "log_level", "variable", "matches", "hint"}, "the value itself is not logged"


def test_an_unset_tick_variable_warns_nothing() -> None:
    assert _retired_tick_warnings() == (False, []), "nothing set, nothing to say"


# ---- reading deliverables ----


def test_the_ledger_rows_are_read_and_the_gates_after_it_are_not() -> None:
    rows = parse_ledger(LEDGER)
    assert [r["id"] for r in rows] == ["K-01", "K-02", "K-03"]
    assert rows[1]["grade"] == "fracture" and rows[1]["evidence"] == "`server.mjs:218-222`"
    assert parse_ledger("no table here") == []


def test_cracks_become_leads_graded_by_severity_sized_by_their_seal_and_placed_by_their_files() -> None:
    leads = crack_leads("acme/app", "Leads", "job-1", LEDGER)
    assert [(lead.severity, lead.effort) for lead in leads] == [
        ("high", "small"),
        ("medium", "medium"),
        ("low", "small"),
    ]
    worst = leads[0]
    assert worst.kind == "crack" and worst.title == "A hardcoded service key is committed"
    assert worst.paths == ("lib/probe.mjs", "_old/probe.mjs")
    assert "Grade: break" in worst.details and "Found by scout job job-1." in worst.details
    assert worst.data["grade"] == "break" and worst.data["job_id"] == "job-1"
    again = crack_leads("acme/app", "Leads", "job-2", LEDGER)[0]
    assert again.id == worst.id, "the same crack found again is the same lead"
    assert crack_leads("acme/app", "Infra", "job-2", LEDGER)[0].id != worst.id, "another area is another lead"


def test_a_crack_s_files_are_paths_and_names_not_the_code_its_evidence_quotes() -> None:
    from embry0.services.scout import _paths

    evidence = "`server.mjs:243` logs inside the map (`console.log`), `userInfo.name` reaches `lib/auth.mjs:5`"
    assert _paths(evidence) == ("server.mjs", "lib/auth.mjs")


@pytest.mark.parametrize("report", [WEED_HEADINGS, WEED_BULLETS])
def test_a_weed_card_becomes_one_small_low_lead_with_its_checked_diff(report: str) -> None:
    (lead,) = weed_leads("acme/app", "", "job-3", report)
    assert lead.kind == "weed" and lead.severity == "low" and lead.effort == "small"
    assert lead.title.startswith("Weed: ") and "nothing" in lead.title
    assert lead.paths and lead.paths[0].startswith("lib/")
    assert lead.data["pull"].startswith("diff --git a/lib/")
    assert "Proof:" in lead.details and "Found by scout job job-3." in lead.details


def test_no_weed_is_no_lead() -> None:
    assert parse_weed("no weeds found: looked in lib/ and scripts/") is None
    assert weed_leads("acme/app", "", "job-4", "no weeds found") == []
    assert weed_leads("acme/app", "", "job-4", None) == []


# ---- the loop ----


class FakeRuns:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, *, job_id: str, repo: str, area: str, template: str, now: datetime) -> None:
        self.rows.append({"job_id": job_id, "repo": repo, "area": area, "template": template, "dispatched_at": now})

    async def last_dispatched(self) -> dict[tuple[str, str, str], datetime]:
        out: dict[tuple[str, str, str], datetime] = {}
        for r in self.rows:
            key = (r["repo"].lower(), r["area"], r["template"])
            out[key] = max(out.get(key, r["dispatched_at"]), r["dispatched_at"])
        return out

    async def dispatched_since(self, since: datetime) -> int:
        return sum(1 for r in self.rows if r["dispatched_at"] >= since)

    async def unharvested(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if "harvested" not in r]

    async def harvested(self, job_id: str, *, leads: int, now: datetime) -> None:
        next(r for r in self.rows if r["job_id"] == job_id)["harvested"] = leads


class FakeJobs:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, **kwargs: Any) -> str:
        job_id = f"job-{len(self.rows) + 1}"
        self.rows[job_id] = {"created_with": kwargs, "status": "pending"}
        return job_id

    async def update(self, job_id: str, **kwargs: Any) -> None:
        self.rows[job_id].update(kwargs)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)


class FakeLeads:
    def __init__(self) -> None:
        self.stored: list[tuple[list[Any], dict[str, str | None] | None]] = []

    async def upsert(self, leads: Any, *, now: datetime, areas: dict[str, str | None] | None = None) -> None:
        self.stored.append((list(leads), areas))


class Models:
    """``background_models`` as a test hands it to the loop: one model, or one per repository; ``set`` replaces it."""

    def __init__(self, model: str | dict[str, str]) -> None:
        self.set(model)
        self.reads = 0

    def set(self, model: str | dict[str, str]) -> None:
        self.current = model

    async def __call__(self) -> Any:
        self.reads += 1
        current = self.current
        return lambda repo: current.get(repo or "", "") if isinstance(current, dict) else current


class Policy:
    """The ``scout`` section as a test hands it to the loop: ``set`` replaces it before the next tick."""

    def __init__(self, **values: Any) -> None:
        self.set(**values)
        self.reads = 0

    def set(self, **values: Any) -> None:
        self.current = ScoutSettings(**values)

    async def __call__(self) -> ScoutSettings:
        self.reads += 1
        return self.current


def make_loop(*, repos: list[str], areas: dict[str, list[Area]] | None = None, refuse: Any = None, **settings: Any):
    runs, jobs, leads = FakeRuns(), FakeJobs(), FakeLeads()
    submitted: list[tuple[str, Unit, str]] = []

    async def list_repos() -> list[str]:
        return repos

    async def areas_for(repo: str) -> list[Area]:
        return (areas or {}).get(repo, [])

    async def gate() -> Any:
        return refuse

    async def submit(job_id: str, unit: Unit, text: str) -> None:
        submitted.append((job_id, unit, text))

    loop = ScoutLoop(
        models=Models(LOCAL),
        settings=Policy(**settings),
        runs=runs,
        jobs=jobs,
        leads=leads,
        repos=list_repos,
        areas_for=areas_for,
        ask_gate=gate,
        submit=submit,
        clock=lambda: T0,
    )
    return loop, runs, jobs, leads, submitted


@pytest.mark.asyncio
async def test_a_tick_starts_jobs_pinned_to_the_local_model_up_to_the_in_flight_cap() -> None:
    loop, runs, jobs, _, submitted = make_loop(
        repos=["acme/a", "acme/b"], areas={"acme/b": [Area("Web", ("web/**",))]}, max_in_flight=3
    )
    started = await loop.dispatch()
    assert len(started) == 3 and len(submitted) == 3
    created = jobs.rows[started[0]]["created_with"]
    assert created["dispatched_by"] == ACTOR == dispatch_actors.SCOUT
    assert created["pipeline_config"]["agent_models_override"] == {"*": LOCAL}, "a pin, never a preference"
    assert created["pipeline_config"]["budget_overrun_mode"] == "hard"
    assert created["pipeline_template"] in ("garden", "kintsugi-trace")
    assert {r["repo"] for r in runs.rows} <= {"acme/a", "acme/b"}
    area_jobs = [jid for jid in started if jobs.rows[jid]["created_with"]["repo"] == "acme/b"]
    assert all(jobs.rows[jid]["area"] == "Web" and jobs.rows[jid]["job_kind"] == "analysis" for jid in area_jobs)
    assert await loop.dispatch() == [], "three are out: none more until one is harvested"


@pytest.mark.asyncio
async def test_a_refused_gate_starts_nothing_and_writes_no_row() -> None:
    loop, runs, jobs, _, submitted = make_loop(repos=["acme/a"], refuse=SimpleNamespace(message="rate limit"))
    assert await loop.dispatch() == []
    assert runs.rows == [] and jobs.rows == {} and submitted == []


@pytest.mark.asyncio
async def test_finished_jobs_are_harvested_once_into_leads_with_their_area() -> None:
    loop, runs, jobs, leads, _ = make_loop(repos=["acme/a"], max_in_flight=2)
    await runs.record(job_id="job-9", repo="acme/a", area="Leads", template="kintsugi-trace", now=T0)
    await runs.record(job_id="job-10", repo="acme/a", area="", template="garden", now=T0)
    await runs.record(job_id="job-11", repo="acme/a", area="", template="garden", now=T0)
    jobs.rows["job-9"] = {"status": "completed", "result_summary": LEDGER}
    jobs.rows["job-10"] = {"status": "failed", "result_summary": WEED_HEADINGS}
    jobs.rows["job-11"] = {"status": "running"}

    assert await loop.harvest() == 3
    ((stored, areas),) = leads.stored
    assert [lead.kind for lead in stored] == ["crack"] * 3 and set(areas.values()) == {"Leads"}
    assert [r.get("harvested") for r in runs.rows] == [3, 0, None], "a failed job yields nothing; a running one waits"
    assert await loop.harvest() == 0 and len(leads.stored) == 1, "a harvested job is never read again"


@pytest.mark.asyncio
async def test_a_job_s_leads_are_capped_the_worst_first() -> None:
    loop, runs, jobs, leads, _ = make_loop(repos=[], max_leads_per_job=1)
    await runs.record(job_id="job-1", repo="acme/a", area="", template="kintsugi-trace", now=T0)
    jobs.rows["job-1"] = {"status": "completed", "result_summary": LEDGER}
    assert await loop.harvest() == 1
    assert leads.stored[0][0][0].severity == "high"


@pytest.mark.asyncio
async def test_without_a_model_the_loop_harvests_but_starts_nothing() -> None:
    loop, runs, jobs, _, submitted = make_loop(repos=["acme/a"])
    loop.models.set("")
    assert await loop.tick() == {"harvested_leads": 0, "started": []}
    assert submitted == []


# ---- the policy, read from the settings document's scout section every tick ----


@pytest.mark.asyncio
async def test_a_tick_reads_the_section_once_and_the_next_tick_reads_it_again() -> None:
    loop, _, jobs, _, _ = make_loop(repos=["acme/a"], templates=("garden",), max_in_flight=1)
    policy = loop.settings
    first = await loop.tick()
    assert policy.reads == 1, "harvest and dispatch read one snapshot of the section per tick"
    assert [jobs.rows[j]["created_with"]["pipeline_template"] for j in first["started"]] == ["garden"]

    policy.set(templates=("kintsugi-trace",), max_in_flight=2)
    second = await loop.tick()
    assert policy.reads == 2, "the next tick reads the section again"
    assert [jobs.rows[j]["created_with"]["pipeline_template"] for j in second["started"]] == ["kintsugi-trace"], (
        "the next tick runs the templates and the in-flight cap the section holds now, with no restart"
    )


@pytest.mark.asyncio
async def test_a_job_s_hard_cap_is_the_section_s_job_budget() -> None:
    loop, _, jobs, _, _ = make_loop(repos=["acme/a"], job_budget_usd=0.25, max_in_flight=1)
    (job_id,) = await loop.dispatch()
    config = jobs.rows[job_id]["created_with"]["pipeline_config"]
    assert (config["max_budget_usd"], config["budget_overrun_mode"]) == (0.25, "hard"), (
        "each scouting job's hard cap is scout.job_budget_usd, not a literal"
    )


@pytest.mark.asyncio
async def test_the_daily_cap_counts_the_jobs_started_in_the_last_24_hours() -> None:
    loop, runs, _, _, _ = make_loop(repos=["acme/a", "acme/b", "acme/c"], max_in_flight=5, max_jobs_per_day=2)
    for job_id, hours_ago in (("old", 25), ("recent", 3)):
        await runs.record(job_id=job_id, repo="acme/z", area="", template="garden", now=T0 - timedelta(hours=hours_ago))
        await runs.harvested(job_id, leads=0, now=T0)
    assert len(await loop.dispatch()) == 1, "one started 3 hours ago, so one more fits under 2 a day"
    assert await loop.dispatch() == [], "two in the last 24 hours: none more, though the in-flight cap has room"
    loop.settings.set(max_in_flight=5, max_jobs_per_day=None)
    assert len(await loop.dispatch()) == 4, "no daily cap: only the in-flight cap holds it, five out with one out"


@pytest.mark.asyncio
async def test_with_none_in_flight_allowed_the_loop_still_harvests_and_starts_again_when_raised() -> None:
    loop, runs, jobs, leads, submitted = make_loop(repos=["acme/a"], max_in_flight=0)
    await runs.record(job_id="job-1", repo="acme/a", area="", template="kintsugi-trace", now=T0)
    jobs.rows["job-1"] = {"status": "completed", "result_summary": LEDGER}
    assert await loop.tick() == {"harvested_leads": 3, "started": []}, "0 in flight starts none, and still harvests"
    assert submitted == []
    loop.settings.set(max_in_flight=1)
    assert len((await loop.tick())["started"]) == 1, "raised in the section: the same loop starts on its next tick"


# ---- where the leads go ----


def test_only_a_break_reaches_the_chat_the_rest_stay_on_the_page() -> None:
    from embry0.services.proposal_briefs import held
    from embry0.services.proposals import lead_proposal

    def proposal(kind: str, severity: str) -> Any:
        return lead_proposal(
            {"id": f"{kind}:1", "kind": kind, "repo": "acme/a", "title": "t", "first_seen": T0, "severity": severity}
        )

    assert held(proposal("crack", "high"), 60) is False
    assert held(proposal("crack", "medium"), 60) is True
    assert held(proposal("weed", "low"), 60) is True


def test_a_yes_on_a_crack_or_a_weed_starts_work_with_its_evidence() -> None:
    from embry0.services.leads.tasks import lead_task

    crack = lead_task(
        {
            "kind": "crack",
            "repo": "acme/a",
            "title": "Status filter matches one spelling",
            "details": "Grade: fracture",
            "area": "Leads",
            "data": {},
        }
    )
    assert crack.startswith("Mend a crack the scout found in acme/a") and "Grade: fracture" in crack
    assert "It is in the Leads area" in crack and "evidence still holds" in crack
    weed = lead_task(
        {
            "kind": "weed",
            "repo": "acme/a",
            "title": "Weed: a dead probe",
            "details": "",
            "data": {"pull": "diff --git a/x b/x"},
        }
    )
    assert "Pull a weed the scout found in acme/a: a dead probe" in weed and "```diff\ndiff --git a/x b/x\n```" in weed
    assert "Change no behavior" in weed


def test_the_world_map_knows_both_kinds() -> None:
    from embry0.services.lead_kinds import KINDS
    from embry0.services.leads import LEAD_KINDS

    assert {"crack", "weed"} <= set(LEAD_KINDS) and {"crack", "weed"} <= set(KINDS)


def test_the_scout_is_exempt_from_the_hourly_limit_its_in_flight_cap_is_its_brake() -> None:
    assert dispatch_actors.is_rate_limit_exempt(dispatch_actors.SCOUT)
