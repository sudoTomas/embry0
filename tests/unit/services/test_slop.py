"""The slopometer's one shape, its lenient parser, and the Slop Dungeon's score (spec 2026-10-25, slop dungeon)."""

from __future__ import annotations

from datetime import UTC, datetime

from embry0.services.slop import (
    CLEANED_XP,
    MAX_EVIDENCE,
    XP_PER_LINE_REMOVED,
    cleaned_xp,
    dungeon_score,
    dungeons,
    parse_contract_lines,
    parse_work_nature,
    slop_brief,
    ticket_section,
)


def test_a_slop_verdict_keeps_its_known_kinds_and_clamped_score() -> None:
    verdict = parse_work_nature(
        {
            "nature": "Slop",
            "slop_score": 140,
            "kinds": ["symptom-patch", "duplication", "vibes", "duplication"],
            "evidence": [{"path": "svc/outbox.py", "why": "three retry wrappers around one insert"}, "junk"],
        }
    )
    assert verdict == {
        "nature": "slop",
        "slop_score": 100,
        "kinds": ["symptom_patch", "duplication"],
        "evidence": [{"path": "svc/outbox.py", "why": "three retry wrappers around one insert"}],
    }


def test_no_verdict_is_better_than_a_wrong_one() -> None:
    assert parse_work_nature({"nature": "feature"}) is None
    assert parse_work_nature("slop") is None
    assert parse_work_nature(None) is None


def test_a_missing_score_reads_from_the_nature_and_evidence_is_capped() -> None:
    bug = parse_work_nature({"nature": "bug"})
    slop = parse_work_nature({"nature": "slop", "evidence": [{"path": f"f{i}.py"} for i in range(20)]})
    assert bug is not None and bug["slop_score"] == 0
    assert slop is not None and slop["slop_score"] == 100 and len(slop["evidence"]) == MAX_EVIDENCE


def test_the_watcher_lines_parse_from_the_head_only() -> None:
    text = (
        "TITLE: Clean up the outbox retry wrappers\nSEVERITY: medium\nNATURE: slop\nSLOP_SCORE: 72\n"
        "SLOP_KINDS: symptom_patch, duplication\nSLOP_EVIDENCE: svc/outbox.py | three retry wrappers\n"
        "BODY:\n## Evidence\nNATURE: bug\n"
    )
    verdict = parse_contract_lines(text)
    assert verdict == {
        "nature": "slop",
        "slop_score": 72,
        "kinds": ["symptom_patch", "duplication"],
        "evidence": [{"path": "svc/outbox.py", "why": "three retry wrappers"}],
    }
    assert parse_contract_lines("TITLE: x\nBODY:\nNATURE: slop\n") is None, "a body quoting the line is no verdict"


def test_the_ticket_and_the_prompt_say_it_is_slop() -> None:
    verdict = {
        "nature": "slop",
        "slop_score": 72,
        "kinds": ["symptom_patch"],
        "evidence": [{"path": "a.py", "why": "x"}],
    }
    assert ticket_section(verdict).startswith("## Slop, not a bug\n")
    assert "slop score 72/100: symptom patch" in ticket_section(verdict)
    brief = slop_brief(verdict)
    assert "Do not add guards" in brief and "- a.py: x" in brief
    assert slop_brief({"nature": "bug", "slop_score": 0, "kinds": [], "evidence": []}) == ""


def test_a_dungeon_rises_with_slop_and_falls_as_it_is_cleaned() -> None:
    assert dungeon_score([]) == 0
    assert dungeon_score([50]) == 50
    assert dungeon_score([50, 50]) == 75
    assert dungeon_score([50]) < dungeon_score([50, 20]), "more slop scores higher"
    leads = [
        {"repo": "acme/web", "area": "Billing", "nature": "slop", "slop_score": 60},
        {"repo": "acme/web", "area": "Billing", "nature": "slop", "slop_score": 50},
        {"repo": "acme/web", "area": None, "nature": "bug", "slop_score": 0},
        {"repo": "acme/api", "area": None, "nature": "slop", "slop_score": 30},
    ]
    found = dungeons(leads, {("acme/web", "Billing"): 2, ("acme/old", None): 1})
    assert found == [
        {"repo": "acme/web", "area": "Billing", "score": 80, "open": 2, "cleaned": 2, "meter": None},
        {"repo": "acme/api", "area": None, "score": 30, "open": 1, "cleaned": 0, "meter": None},
        {"repo": "acme/old", "area": None, "score": 0, "open": 0, "cleaned": 1, "meter": None},
    ]


def test_a_dungeon_carries_its_meter_and_a_sloppy_area_has_one_without_findings() -> None:
    """The Slop-O-Meter (spec 2026-10-26): each dungeon's reading, and areas at Spaghetti Bolognese or worse."""
    at = datetime(2026, 10, 26, tzinfo=UTC)
    meters = [
        {
            "repo": "acme/web",
            "area": "Billing",
            "score": 0.73,
            "level": 4,
            "name": "Sloppy Joe",
            "provisional": True,
            "computed_at": at,
        },
        {
            "repo": "acme/web",
            "area": "",
            "score": 0.12,
            "level": 1,
            "name": "Buttered Noodles",
            "provisional": False,
            "computed_at": at,
        },
        {
            "repo": "acme/api",
            "area": "",
            "score": 0.45,
            "level": 3,
            "name": "Lasagna",
            "provisional": False,
            "computed_at": at,
        },
    ]
    leads = [{"repo": "acme/web", "area": "Billing", "nature": "slop", "slop_score": 60}]
    found = {(d["repo"], d["area"]): d for d in dungeons(leads, {}, meters)}
    assert set(found) == {("acme/web", "Billing"), ("acme/api", None)}, "a clean repository has no dungeon of its own"
    assert found[("acme/web", "Billing")]["meter"] == {
        "score": 0.73,
        "level": 4,
        "name": "Sloppy Joe",
        "provisional": True,
        "computed_at": at.isoformat(),
    }
    assert found[("acme/api", None)]["open"] == 0 and found[("acme/api", None)]["meter"]["name"] == "Lasagna"


def test_a_cleanup_earns_for_its_net_lines_removed() -> None:
    assert cleaned_xp(10, 110) == CLEANED_XP + 100 * XP_PER_LINE_REMOVED
    assert cleaned_xp(50, 10) == CLEANED_XP, "a cleanup that grew the code earns the base only"
    assert cleaned_xp(0, 50_000) == CLEANED_XP + 1_000 * XP_PER_LINE_REMOVED
