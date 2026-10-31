"""Decisions (spec 2026-10-27): a quest on offer and a quest done, each as one card."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.decisions import MAX_FACTS, done, done_actions, offered, ordered

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def crack(**over: object) -> dict[str, object]:
    return {
        "id": "crack:1a2b",
        "kind": "crack",
        "repo": "acme/web",
        "area": "Auth",
        "title": "A committed service key bypasses row-level security",
        "summary": "A high crack the scout found in web: a committed service key.",
        "details": "Grade: break",
        "action": "Mend it",
        "created_at": T0.isoformat(),
        "owners": ["octocat", "hubot", "monalisa"],
        "severity": "high",
        "effort": "small",
        "paths": ["lib/probe.mjs", "_old/probe.mjs"],
        "held": False,
        "brief": None,
        **over,
    }


def test_a_quest_on_offer_reads_its_severity_first_and_its_facts_in_order() -> None:
    d = offered(crack(), ask_min=60).to_dict()
    assert d["id"] == "offer:crack:1a2b" and d["stage"] == "offered"
    assert d["verdict"] == {"label": "High", "tone": "bad"} and d["kind_label"] == "Crack"
    assert [(f["label"], f["value"]) for f in d["facts"]] == [
        ("Evidence", "lib/probe.mjs (+1)"),
        ("Size", "small"),
        ("Owners", "octocat, hubot +1"),
    ]
    assert d["why"] == "A high crack the scout found in web: a committed service key."
    assert d["yes_starts"].startswith("the agents fix what the crack names")
    assert d["actions"] == ["start", "decline", "later", "send_to"]
    assert d["at"] == T0.isoformat() and d["links"]["proposal"] == "crack:1a2b"


def test_a_brief_s_plain_words_win_and_a_kind_without_severity_reads_as_its_kind() -> None:
    brief = {
        "title": "Fix the guide",
        "problem": "The guide lies. It says five.",
        "fix": "Edit one line.",
        "size": "small",
    }
    d = offered(
        crack(kind="finding", severity=None, brief=brief, path="CLAUDE.md", line=12, paths=[]), ask_min=60
    ).to_dict()
    assert d["title"] == "Fix the guide" and d["why"] == "The guide lies." and d["yes_starts"] == "Edit one line."
    assert d["verdict"] == {"label": "Outdated instruction", "tone": "info"}
    assert d["facts"][0] == {"label": "Evidence", "value": "CLAUDE.md:12", "tone": ""}


def test_a_ticket_shows_its_readiness_and_warns_below_the_floor() -> None:
    ticket = crack(kind="ticket", severity=None, source={"readiness": 40}, paths=[])
    assert {"label": "Readiness", "value": "40/100", "tone": "warn"} in offered(ticket, ask_min=60).to_dict()["facts"]
    assert {"label": "Readiness", "value": "40/100", "tone": ""} in offered(ticket, ask_min=40).to_dict()["facts"], (
        "the floor is the ask_min given: a ticket at it is asked, so not marked"
    )


def job(**over: object) -> dict[str, object]:
    return {
        "job_id": "job-1",
        "repo": "acme/web",
        "area": "Leads",
        "title": "fix the crash loop",
        "task": "Fix the crash loop. Users saw 502s.",
        "outcome": "pr_open",
        "pr_url": "https://github.com/acme/web/pull/44",
        "template": "full-magnum-opus",
        "finished_at": T0,
        "cost_usd": 19.02,
        "seconds": 4936,
        "failure": None,
        "pr": {"number": 44, "additions": 212, "deletions": 38, "files": 6},
        "approval_ask": {"checks_state": "success", "recommendation": {"verdict": "merge", "reasons": []}},
        "done_when": {
            "items": [{"id": "d1"}],
            "report": {"items": [{"id": "d1", "met": True}, {"id": "d2", "met": True}]},
        },
        "brief": {"title": "Diagnose the leads crash loop", "why": "Users got 502s for two minutes. More."},
        **over,
    }


def test_a_clean_pull_request_reads_ready_to_merge_with_its_numbers() -> None:
    d = done(job()).to_dict()
    assert d["id"] == "done:job-1" and d["stage"] == "done"
    assert d["verdict"] == {"label": "Ready to merge", "tone": "good"}
    assert d["title"] == "Diagnose the leads crash loop" and d["why"] == "Users got 502s for two minutes."
    assert [(f["label"], f["value"]) for f in d["facts"]] == [
        ("Pull request", "#44 · +212 −38 · 6 files"),
        ("Checks", "passing"),
        ("Done-when", "2/2 met"),
        ("Spent", "$19.02 · 1h 22m"),
    ]
    assert d["actions"] == ["merge", "send_back", "reject", "run_again", "send_to"]
    assert d["yes_starts"] is None


def test_failing_checks_unmet_items_or_no_recommendation_need_a_look() -> None:
    failing = done(job(approval_ask={"checks_state": "failure", "recommendation": {"verdict": "merge"}})).to_dict()
    assert failing["verdict"] == {"label": "Checks failing", "tone": "bad"}
    unmet = {"report": {"items": [{"id": "d1", "met": True}, {"id": "d2", "met": False}]}}
    assert done(job(done_when=unmet)).to_dict()["verdict"]["label"] == "Needs a look"
    assert {"label": "Done-when", "value": "1/2 met", "tone": "warn"} in done(job(done_when=unmet)).to_dict()["facts"]
    assert done(job(approval_ask={})).to_dict()["verdict"]["label"] == "Needs a look"


def test_a_sensitive_path_is_a_careful_fact() -> None:
    ask = {
        "checks_state": "success",
        "recommendation": {"verdict": "hold", "reasons": ["Touches infra/, a sensitive path."]},
    }
    d = done(job(approval_ask=ask)).to_dict()
    assert {"label": "Careful", "value": "Touches infra/, a sensitive path.", "tone": "warn"} in d["facts"]


def test_a_failed_quest_says_why_it_failed_and_offers_to_run_again() -> None:
    d = done(job(outcome="failed", pr_url=None, pr=None, failure="The agent timed out. It ran 600s.")).to_dict()
    assert d["verdict"] == {"label": "Failed", "tone": "bad"} and d["why"] == "The agent timed out."
    assert d["actions"] == ["run_again", "dismiss", "send_to"]


def test_the_actions_follow_how_the_quest_came_back() -> None:
    assert done_actions("done", None) == ("good", "run_again", "bad", "send_to")
    assert done_actions("merged", "https://x/pull/1") == ("good", "bad", "dismiss", "send_to")
    assert done_actions("recalled", None) == ("run_again", "dismiss", "send_to")


def test_no_card_carries_more_than_six_facts_or_an_empty_one() -> None:
    d = done(
        job(
            work_nature={"nature": "slop"},
            approval_ask={"checks_state": "success", "recommendation": {"reasons": ["sensitive path infra/"]}},
        )
    )
    assert len(d.facts) <= MAX_FACTS and all(f.value for f in d.facts)
    assert offered(crack(paths=[], owners=[], effort=None), ask_min=60).facts == ()


def test_the_worst_come_first_then_the_newest() -> None:
    old_bad = done(job(job_id="a", outcome="failed", finished_at=T0 - timedelta(days=1)))
    new_bad = done(job(job_id="b", outcome="failed", finished_at=T0))
    good = done(job(job_id="c"))
    info = offered(crack(id="issue:1", kind="issue", severity=None), ask_min=60)
    assert [d.id for d in ordered([info, good, old_bad, new_bad])] == ["done:b", "done:a", "done:c", "offer:issue:1"]
