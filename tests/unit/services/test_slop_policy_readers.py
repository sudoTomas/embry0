"""Each reader of ``slop_policy`` reads its own repository's (#169): a job, the reviewer, the ticket, the world.

The outputs with the defaults are pinned as ``origin/main`` (``c80ba42e``)
wrote them, and an organization's layer reaches its own repositories only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.services.job_settings import STATE_KEY, job_section, read_for_job
from embry0.services.slop import dungeons, ticket_section
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from embry0.workflows.issue_to_pr.nodes._review import _slop_review_section

VERDICT = {
    "nature": "slop",
    "slop_score": 72,
    "kinds": ["symptom_patch", "duplication"],
    "evidence": [{"path": "svc/outbox.py", "why": "three retry wrappers"}],
}
#: What ``ticket_section`` and ``_slop_review_section`` wrote on ``c80ba42e`` for these inputs.
MAIN_TICKET = (
    "## Slop, not a bug\nThe watcher judged this slop (slop score 72/100: symptom patch, duplication). The code "
    "does what it says; the trouble is its shape. Clean it up — keep behavior, remove or merge code — "
    "instead of adding another guard or retry.\n- `svc/outbox.py`: three retry wrappers\n\n"
)
MAIN_REVIEW = (
    "## This change is a slop cleanup\n\nIt was judged slop, not a bug: it must keep behavior and improve shape.\n"
    "Approve only when all of these hold, and request changes otherwise, saying which:\n- behavior is unchanged: "
    "the tests pass, and no public behavior, API or output\n  moved (removing what nothing reaches is fine);\n- the "
    "change removes or merges more than it adds, or is plainly simpler;\n- it adds no new guard, retry, fallback, "
    "catch-all or flag to hide a symptom.\nThe branch adds 5 lines and removes 90 against its base.\n"
)
ROWS: list[dict[str, Any]] = [
    {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None},
    {
        "scope": "organization:acme",
        "data": {
            "slop_policy": {
                "enabled": False,
                "review_guide": "Acme reviews a cleanup by its tests alone.",
                "classifier_guide": "Acme's own words.",
                "dungeon_min_level": 4,
            }
        },
        "generation": 1,
    },
    {"scope": "repository:acme/legacy", "data": {"slop_policy": {"enabled": True}}, "generation": 1},
]


class _Store:
    async def snapshot(self) -> Any:
        return build_snapshot(CATALOG, ROWS, token=(), log=False)


def _section() -> Any:
    section = CATALOG.get("slop_policy")
    if section is None:
        pytest.fail("the settings document has no slop_policy section: slop routing is code")
    return section


def _ticket(heading: str) -> str:
    try:
        return ticket_section(VERDICT, heading=heading)
    except TypeError:
        pytest.fail("a slop ticket's heading is fixed in the code")


def test_the_default_ticket_and_review_are_what_they_were() -> None:
    assert _ticket(_section().defaults().ticket_heading) == MAIN_TICKET
    state = {"coding_agent": "slopmaster", "work_nature": {**VERDICT, "cleanup": {"added": 5, "removed": 90}}}
    assert _slop_review_section(state) == MAIN_REVIEW, "a job that read no policy reviews as before"
    assert _ticket("Shape, not behaviour").startswith("## Shape, not behaviour\n")


@pytest.mark.asyncio
async def test_a_job_reads_its_own_repository_s_policy_and_its_reviewer_hears_it() -> None:
    section = _section()
    acme = {STATE_KEY: await read_for_job(_Store(), "acme/widgets")}
    globex = {STATE_KEY: await read_for_job(_Store(), "globex/api")}
    legacy = {STATE_KEY: await read_for_job(_Store(), "acme/legacy")}
    assert job_section(acme, section).enabled is False
    assert job_section(acme, section).classifier_guide == "Acme's own words."
    assert job_section(globex, section) == section.defaults(), "acme's layer never reaches globex's jobs"
    assert job_section(legacy, section).enabled is True, "a repository's layer outranks its organization's"
    assert job_section(legacy, section).review_guide == "Acme reviews a cleanup by its tests alone."

    cleanup = {"coding_agent": "slopmaster", "work_nature": VERDICT}
    assert _slop_review_section({**cleanup, **acme}) == "Acme reviews a cleanup by its tests alone.\n", (
        "the reviewer is told the guide its job read"
    )
    assert _slop_review_section({**cleanup, **globex}).startswith("## This change is a slop cleanup\n")


def test_a_dungeon_is_drawn_from_its_repository_s_level() -> None:
    at = datetime(2026, 11, 6, tzinfo=UTC)
    meters = [
        {"repo": "acme/web", "area": "", "score": 0.5, "level": 3, "name": "Lasagna", "computed_at": at},
        {"repo": "globex/api", "area": "", "score": 0.5, "level": 3, "name": "Lasagna", "computed_at": at},
        {"repo": "initech/app", "area": "", "score": 0.9, "level": 5, "name": "Just The Slop", "computed_at": at},
    ]
    levels = {"acme/web": 4, "globex/api": 2, "initech/app": None}
    try:
        drawn = dungeons([], {}, meters, min_level=levels.__getitem__)
    except TypeError:
        pytest.fail("the level a meter draws a dungeon from is fixed in the code")
    found = {d["repo"] for d in drawn}
    assert found == {"globex/api"}, "acme draws from level 4, globex from 2, and initech's meter makes none"


def test_a_stored_verdict_is_read_back_with_every_kind_it_kept() -> None:
    """The watcher kept its stack's own kind (slop_policy.kinds); the proposals feed must not drop it."""
    from embry0.services.proposals import case_proposal

    stored = {"nature": "slop", "slop_score": 60, "kinds": ["naming", "dead_code"], "evidence": []}
    row = {
        "id": "hc-1",
        "repo": "acme/web",
        "title": "Clean up the names that lie",
        "severity": "medium",
        "occurrence_count": 3,
        "detected_at": datetime(2026, 11, 6, tzinfo=UTC),
        "created_at": datetime(2026, 11, 6, tzinfo=UTC),
        "work_nature": stored,
    }
    proposal = case_proposal(row).to_dict()
    assert proposal["slop_kinds"] == ["naming", "dead_code"], "an organization's own kind is read back as stored"
    assert "(naming, dead code)" in proposal["details"]
