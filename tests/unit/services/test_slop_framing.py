"""Slop reads as a cleanup everywhere it is shown (spec 2026-10-25, slop dungeon)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from embry0.orchestration.nodes.triage import build_triage_system_prompt, parse_triage_response
from embry0.services.progression import price
from embry0.services.proposals import case_proposal
from embry0.services.slop import CLEANED_XP, XP_PER_LINE_REMOVED
from embry0.services.world import lead_entry
from embry0.storage.repositories.progression import ProgressEvent
from embry0.workflows.issue_to_pr.nodes._review import _slop_review_section

NOW = datetime(2026, 10, 25, tzinfo=UTC)
SLOP = {"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch", "duplication"], "evidence": []}


def _case(**extra: object) -> dict[str, object]:
    return {
        "id": "hc-1",
        "repo": "acme/web",
        "title": "Clean up the outbox retry wrappers",
        "severity": "medium",
        "occurrence_count": 4,
        "detected_at": NOW,
        "created_at": NOW,
        **extra,
    }


def test_a_slop_case_is_proposed_as_a_cleanup() -> None:
    proposal = case_proposal(_case(work_nature=SLOP)).to_dict()
    assert proposal["summary"].startswith("The watcher found slop in production (4x, medium), a cleanup, not a fix")
    assert proposal["action"] == "Run the Slopmaster's cleanup on the draft ticket"
    assert proposal["nature"] == "slop" and proposal["slop_score"] == 72
    assert proposal["slop_kinds"] == ["symptom_patch", "duplication"]
    assert proposal["details"].startswith("Slop, not a bug: slop score 72/100 (symptom patch, duplication)")


def test_a_case_without_a_verdict_reads_as_before() -> None:
    proposal = case_proposal(_case()).to_dict()
    assert proposal["action"] == "Run the fix pipeline on the draft ticket" and "nature" not in proposal
    bug = case_proposal(_case(work_nature={"nature": "bug", "slop_score": 10})).to_dict()
    assert bug["action"] == "Run the fix pipeline on the draft ticket" and bug["nature"] == "bug"


def test_a_slop_proposal_is_a_slop_lead_on_the_board() -> None:
    lead = lead_entry(case_proposal(_case(work_nature=SLOP)).to_dict())
    assert (lead["kind"], lead["verb"], lead["leader"], lead["creature"]) == (
        "slop",
        "Clean up",
        "slopmaster",
        "slop_ooze",
    )


def test_the_reviewer_checks_a_cleanup_only_on_a_cleanup() -> None:
    section = _slop_review_section(
        {"coding_agent": "slopmaster", "work_nature": {**SLOP, "cleanup": {"added": 5, "removed": 90}}}
    )
    assert "## This change is a slop cleanup" in section
    assert "adds 5 lines and removes 90" in section
    assert _slop_review_section({"coding_agent": "developer"}) == ""


def test_a_merged_cleanup_is_priced_by_its_net_lines_removed() -> None:
    event = ProgressEvent("cleaned", "slopmaster", NOW, json.dumps({"added": 10, "removed": 60}))
    assert price(event) == [("coding", CLEANED_XP + 50 * XP_PER_LINE_REMOVED)]
    assert price(ProgressEvent("cleaned", "slopmaster", NOW, None)) == [("coding", CLEANED_XP)]


def test_triage_is_asked_for_the_verdict_and_a_bad_one_never_fails_it() -> None:
    prompt = build_triage_system_prompt(
        default_model="m", tier_models={"model_heavy": "h", "model_medium": "m", "model_light": "l"}
    )
    assert "## Bug or slop" in prompt and "symptom_patch" in prompt and "<<SLOP_GUIDE>>" not in prompt
    decision = parse_triage_response(json.dumps({"action": "proceed", "confidence": 0.8, "work_nature": SLOP}))
    assert decision["work_nature"] == SLOP
    junk = parse_triage_response(json.dumps({"action": "proceed", "confidence": 0.8, "work_nature": "slop?"}))
    assert junk["work_nature"] == "slop?", "kept loose here; plan_route drops what it cannot read"
