"""The world (spec 2026-10-25): the rules that turn jobs, logs and proposals into the game."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.world import (
    IDLE_DAYS,
    QUEST_TEMPLATE_BY_AGENT,
    STUCK_SECONDS,
    character_entries,
    job_entry,
    job_state,
    lead_entry,
    lead_level,
    leader_of,
    party_of,
    quest_title,
    quest_xp,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def test_a_lead_is_as_menacing_as_its_severity_and_its_reach():
    quiet = lead_level({"kind": "case", "severity": "low", "occurrences": 1})
    loud = lead_level({"kind": "case", "severity": "high", "occurrences": 40})
    assert 1 <= quiet < loud <= 126
    assert lead_level({"kind": "issue", "details": "x" * 3_000, "labels": ["bug"]}) > lead_level(
        {"kind": "issue", "details": "short"}
    )
    assert lead_level({"kind": "finding"}) < lead_level({"kind": "ci_failure"})
    assert lead_level({"kind": "ticket", "level": 500}) == 126, "a finder's own estimate is kept, within the scale"
    assert lead_level({"kind": "something-new"}) == 20


def test_each_kind_has_its_verb_creature_and_leader():
    case = lead_entry({"id": "case:1", "kind": "case", "severity": "high", "occurrences": 3})
    assert (case["verb"], case["creature"], case["leader"]) == ("Attack", "creature", "developer")
    finding = lead_entry({"id": "finding:1", "kind": "finding"})
    assert (finding["verb"], finding["leader"]) == ("Repair", "context-auditor")
    unknown = lead_entry({"id": "x:1", "kind": "omen"})
    assert unknown["verb"] == "Investigate" and unknown["id"] == "x:1"


def test_the_party_is_who_set_out_and_who_works_now():
    runs = [
        ("agent_started", "triage"),
        ("agent_completed", "triage"),
        ("agent_started", "developer"),
        ("agent_started", "review"),
        ("agent_completed", "review"),
    ]
    assert party_of(runs) == (["triage", "developer", "review"], "developer")
    assert party_of(runs + [("agent_completed", "developer")]) == (["triage", "developer", "review"], None)
    assert party_of([]) == ([], None)


def test_where_a_party_is():
    assert job_state("pending", None, []) == "travelling"
    assert job_state("running", None, []) == "travelling"
    assert job_state("running", "developer", ["developer"]) == "working"
    assert job_state("running", None, ["triage"]) == "working", "between two steps it is still at the quest"
    assert job_state("awaiting_input", None, ["developer"]) == "waiting"
    assert job_state("completed", None, ["developer"]) == "returning"
    assert job_state("pr_merged", None, []) == "returning"
    assert job_state("failed", None, ["developer"]) == "defeated"
    assert job_state("cancelled", None, []) == "recalled"


def test_who_a_job_is_drawn_as():
    assert leader_of({"pipeline_template": "analysis-default"}, [], None) == "analysis"
    assert leader_of({}, ["triage", "developer"], "triage") == "developer", "triage never leads once another worked"
    assert leader_of({}, ["triage"], "triage") == "triage"
    assert leader_of({"pipeline_template": "full-magnum-opus"}, [], None) == "developer"
    assert leader_of({}, ["developer", "qa"], "qa") == "qa"


def test_a_quest_is_named_by_its_first_line_without_its_tags():
    assert quest_title("[ABC-12] [watcher] Fix the sender\n\nmore") == "Fix the sender"
    assert quest_title("") == "A quest"
    assert len(quest_title("x" * 500)) == 140


def _job(**over):
    return {
        "job_id": "job-1",
        "repo": "acme/web",
        "task": "Fix it",
        "status": "running",
        "pipeline_template": None,
        "created_at": NOW - timedelta(minutes=30),
        "started_at": NOW - timedelta(minutes=30),
        **over,
    }


def test_a_party_that_went_silent_is_stuck_and_a_defeat_says_why():
    silent = job_entry(
        _job(),
        [("agent_started", "developer")],
        lead_id="case:9",
        last_event_at=NOW - timedelta(seconds=STUCK_SECONDS + 1),
        now=NOW,
    )
    assert silent["stuck"] and silent["state"] == "working" and silent["lead_id"] == "case:9"
    busy = job_entry(_job(), [("agent_started", "developer")], lead_id=None, last_event_at=NOW, now=NOW)
    assert not busy["stuck"]
    lost = job_entry(
        _job(status="failed", error_code="ERR_AGENT_MAX_TURNS"), [], lead_id=None, last_event_at=None, now=NOW
    )
    assert lost["state"] == "defeated" and lost["failure"]


def _character(agent):
    return {"agent_type": agent, "level": 40, "tier": "steel"}


def test_each_character_is_where_its_newest_quest_is():
    parties = [
        job_entry(_job(job_id="job-a"), [("agent_started", "developer")], lead_id=None, last_event_at=NOW, now=NOW),
        job_entry(
            _job(job_id="job-b", status="completed", finished_at=NOW),
            [
                ("agent_started", "developer"),
                ("agent_completed", "developer"),
                ("agent_started", "review"),
                ("agent_completed", "review"),
            ],
            lead_id=None,
            last_event_at=NOW,
            now=NOW,
        ),
    ]
    stats = {
        "developer": {"quests": 4, "succeeded": 3, "last_quest_at": NOW},
        "why": {"quests": 1, "last_quest_at": NOW - timedelta(days=IDLE_DAYS + 1)},
    }
    entries = {
        c["agent_type"]: c
        for c in character_entries([_character(a) for a in ("developer", "review", "why", "qa")], parties, stats, NOW)
    }
    assert entries["developer"]["state"] == "working" and entries["developer"]["job_id"] == "job-a"
    assert entries["developer"]["stats"]["success_rate"] == 0.75
    assert entries["review"]["state"] == "returning" and entries["review"]["job_id"] == "job-b"
    assert entries["why"]["state"] == "idle" and entries["why"]["idle_too_long"]
    assert entries["qa"]["idle_too_long"], "a character that never went out is idle too long"
    assert entries["developer"]["can_quest"] and entries["why"]["quest_template"] == "why"
    assert not entries["qa"]["can_quest"], "QA works only inside another quest"
    assert "triage" not in QUEST_TEMPLATE_BY_AGENT


def test_a_quest_earns_its_party_experience_and_the_merge_its_developer():
    runs = [
        {
            "agent_type": "developer",
            "result": "success",
            "error_code": None,
            "tools_called": {"Edit": 3, "Bash": 2},
            "skill_actions": None,
            "created_at": NOW,
        },
        {
            "agent_type": "review",
            "result": "error",
            "error_code": "ERR_X",
            "tools_called": {"Read": 9},
            "skill_actions": None,
            "created_at": NOW,
        },
    ]
    plain = quest_xp({"status": "completed", "finished_at": NOW}, runs)
    assert plain and all(g["agent_type"] == "developer" for g in plain), "a failed run earns nothing"
    merged = quest_xp({"status": "pr_merged", "pr_url": "https://forge/pull/1", "finished_at": NOW}, runs)
    assert sum(g["xp"] for g in merged) > sum(g["xp"] for g in plain)
