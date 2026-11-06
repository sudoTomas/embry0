"""The lists of built-in names that decided behaviour are gone (#167): the rows decide.

Each name below held behaviour an operator's own agent or template could
not take part in. A test that imported one would keep it alive, so this
checks they are not there to import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("embry0.workflows.issue_to_pr.specialist_agents", "SPECIALIST_TEMPLATE_NAMES"),
        ("embry0.workflows.issue_to_pr.route_plan", "specialist_job_kind"),
        ("embry0.workflows.issue_to_pr.nodes._salvage", "CODING_AGENTS"),
        ("embry0.services.job_briefs", "_TEMPLATE_DELIVERS"),
        ("embry0.services.world", "LEADER_BY_TEMPLATE"),
        ("embry0.services.world", "QUEST_TEMPLATE_BY_AGENT"),
        ("embry0.storage.repositories.progression", "_CODER"),
        ("embry0.storage.repositories.progression", "AUDIT_TEMPLATE"),
    ],
)
def test_the_name_list_is_gone(module: str, name: str) -> None:
    import importlib

    assert not hasattr(importlib.import_module(module), name), f"{module}.{name} still decides by built-in name"


def test_the_coding_agents_are_listed_once() -> None:
    from embry0.workflows import row_behaviour
    from embry0.workflows.issue_to_pr import route_plan

    assert route_plan.CODING_AGENT_TYPES is row_behaviour.CODING_AGENT_TYPES


def test_the_progression_and_world_queries_credit_from_the_rows() -> None:
    from embry0.storage.repositories import world
    from embry0.storage.repositories.progression import events_sql

    for sql in (events_sql(), world._CLEANED_SQL):
        for literal in ("'slop-cleanup'", "'context-audit'"):
            assert literal not in sql, f"a query still names the template {literal}: the job's recorded credit says it"
        assert "coding_agent" in sql, "the query credits the job from what it recorded when it was planned"
        assert "pipeline_templates" not in sql, "a past job's credit must not move with today's template rows"


def test_the_guild_hall_draws_each_station_from_the_row() -> None:
    hall = (REPO / "frontend" / "src" / "lib" / "worldHall.ts").read_text()
    assert "STATION_OF" not in hall, "the hall still maps agent names to stations"
