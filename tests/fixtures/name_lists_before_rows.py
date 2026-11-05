"""What the code said by built-in name before the rows said it (#167), copied as it stood.

The lists and the literals the template and agent columns replaced, kept
here only to prove the seeded rows, and an upgraded database's, answer
exactly what they did: ``SPECIALIST_TEMPLATE_NAMES`` (specialist_agents.py),
``_TEMPLATE_DELIVERS`` (job_briefs.py), ``LEADER_BY_TEMPLATE`` and
``QUEST_TEMPLATE_BY_AGENT`` (services/world.py), ``STATION_OF``
(frontend/src/lib/worldHall.ts), the coding agents (route_plan.py and
_salvage.py), ``specialist_job_kind`` (route_plan.py) and the coder of a
job (progression.py's ``_CODER``, world.py's ``quest_xp``). Nothing outside
the tests reads them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SPECIALIST_TEMPLATE_NAMES = frozenset(
    {
        "kintsugi-trace",
        "garden",
        "interrogate",
        "why",
        "how",
        "blast-radius",
        "comment-pass",
        "design-audit",
        "context-audit",
        "map-areas",
    }
)

SPECIALIST_AGENT_TYPES = (
    "kintsugi",
    "gardener",
    "interrogator",
    "why",
    "how",
    "blast-radius",
    "comment-sicko",
    "design-auditor",
    "context-auditor",
    "area-mapper",
)

TEMPLATE_DELIVERS = {
    "analysis-default": "an analysis of the production logs, and a ticket when it finds a problem worth one",
    "context-audit": "a report of the claims that look wrong in the files that steer coding agents",
    "map-areas": "a pull request that maps the repository's areas",
    "pr-review": "a review on the pull request",
    "qa": "a test report",
    "onboard": "a test setup for the repository",
}

LEADER_BY_TEMPLATE = {
    "analysis-default": "analysis",
    "research-default": "research",
    "ops-default": "ops",
    "qa": "qa",
    "pr-review": "pr-reviewer",
    "onboard": "onboarding",
    "kintsugi-trace": "kintsugi",
    "garden": "gardener",
    "interrogate": "interrogator",
    "why": "why",
    "how": "how",
    "blast-radius": "blast-radius",
    "comment-pass": "comment-sicko",
    "design-audit": "design-auditor",
    "context-audit": "context-auditor",
    "map-areas": "area-mapper",
    "slop-cleanup": "slopmaster",
}

QUEST_TEMPLATE_BY_AGENT: dict[str, str | None] = {
    "developer": None,
    **{
        agent: template
        for template, agent in LEADER_BY_TEMPLATE.items()
        if template not in ("qa", "pr-review", "onboard")
    },
}

STATION_OF = {
    "developer": "anvil",
    "review": "lectern",
    "pr-reviewer": "lectern",
    "interrogator": "lectern",
    "qa": "alchemy_table",
    "kintsugi": "alchemy_table",
    "analysis": "map_table",
    "research": "map_table",
    "ops": "map_table",
    "area-mapper": "map_table",
    "blast-radius": "map_table",
}

CODING_AGENTS = frozenset({"developer", "slopmaster"})


def specialist_job_kind(agents: Iterable[str]) -> str | None:
    """``"analysis"`` for a route whose agents (``output`` aside) are all specialists, else None."""
    steps = [a for a in agents if a != "output"]
    if steps and all(a in SPECIALIST_AGENT_TYPES for a in steps):
        return "analysis"
    return None


def route_agents(graph: Mapping[str, Any]) -> list[str]:
    """A graph's steps' agent types, as build_route_plan linearized them (triage dropped)."""
    return [str(n.get("agent_type")) for n in graph.get("nodes") or [] if n.get("agent_type") != "triage"]


def recorded_kind(template: str, graph: Mapping[str, Any], default_for_kind: str | None) -> str | None:
    """The kind plan_route recorded a job of ``template`` as: its default kind, else the specialist rule."""
    return default_for_kind or specialist_job_kind(route_agents(graph))


def coder(template: str | None) -> str:
    """The character a job's pull request was credited to."""
    return "slopmaster" if template == "slop-cleanup" else "developer"


def delivers(template: str, kind: str) -> str:
    """What a job's brief said it delivers."""
    kinds = {
        "code": "a pull request with the change",
        "research": "a written report",
        "analysis": "a written analysis",
        "ops": "the operation done, and a report of it",
    }
    return TEMPLATE_DELIVERS.get(template) or kinds.get(kind) or "the result of the work"
