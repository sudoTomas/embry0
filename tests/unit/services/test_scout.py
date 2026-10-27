"""Scout (spec 2026-10-27): units, tasks, the due rule, reading deliverables into leads, and the loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.orchestration import dispatch_actors
from embry0.services.areas import Area
from embry0.services.scout import (
    ACTOR,
    ScoutLoop,
    ScoutSettings,
    Unit,
    crack_leads,
    due,
    parse_ledger,
    parse_weed,
    scout_settings,
    task,
    units,
    weed_leads,
)

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


def test_settings_turn_the_loop_off_without_a_model_and_drop_unknown_templates() -> None:
    off = scout_settings(SimpleNamespace(scout_model="", scout_templates="garden"))
    assert off.model == ""
    on = scout_settings(SimpleNamespace(scout_model=f" {LOCAL} ", scout_templates="garden,bogus"))
    assert on.model == LOCAL and on.templates == ("garden",)
    assert scout_settings(SimpleNamespace(scout_model=LOCAL, scout_templates="bogus")).templates == (
        "garden",
        "kintsugi-trace",
    ), "a list naming no known template falls back to both"


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
        settings=ScoutSettings(model=LOCAL, **settings),
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
    loop.settings = ScoutSettings(model="")
    assert loop.enabled is False
    assert await loop.tick() == {"harvested_leads": 0, "started": []}
    assert submitted == []


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
    from embry0.services.leads import LEAD_KINDS
    from embry0.services.world import LEAD_KINDS as WORLD_KINDS

    assert {"crack", "weed"} <= set(LEAD_KINDS) and {"crack", "weed"} <= set(WORLD_KINDS)


def test_the_scout_is_exempt_from_the_hourly_limit_its_in_flight_cap_is_its_brake() -> None:
    assert dispatch_actors.is_rate_limit_exempt(dispatch_actors.SCOUT)
