"""Lead kinds: every kind of work embry0 offers a person, in one catalogue.

A kind is what a quest on offer is: a problem the log watcher keeps seeing, an
open issue, a context audit finding, a lead a finder or the scout brought in,
or slop. Everything that says something per kind reads it here:

- which kinds a finder or the scout stores as leads (:mod:`embry0.services.leads`)
  and which the owner proposals feed carries (:mod:`embry0.services.proposals`);
- the quest board's verb, creature, leader and starting level
  (:mod:`embry0.services.world`);
- a decision card's label and what a yes starts (:mod:`embry0.services.decisions`);
- what a brief's prompt says the kind is (:mod:`embry0.services.proposal_briefs`),
  and the proposal ids a brief must never carry (:func:`id_pattern`);
- the dashboard's names for each kind (its labels, what it is and what finds
  it), which ``GET /lead-kinds`` serves.

The catalogue is content, not policy: which severities of a kind are asked in
chat is the operator's, the settings document's ``leads.ask_severities``
(:mod:`embry0.settings.sections.leads`, which checks the kinds it names here).
So this module imports nothing of embry0, and a section may import it.

The world's art manifest (``frontend/public/world/manifest.json``) names the
creature that stands for each kind on its own, because it is an art file; a
test holds the two equal, and the other places a kind is spelled out
(``tests/unit/services/test_lead_kind_parity.py``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

#: The severities a lead can have, least first.
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
#: The severities a watcher case can have: a lead's, the alerts' ``warning``, and ``unrated`` for one that
#: came with none (``embry0.services.proposals.case_proposal``, ``embry0.services.healing.signals``).
CASE_SEVERITIES: tuple[str, ...] = (*SEVERITIES, "warning", "unrated")
#: The agent that leads a weed and slop (``embry0.services.slop.SLOPMASTER``; a test holds them equal).
_SLOPMASTER = "slopmaster"


@dataclass(frozen=True)
class LeadKind:
    id: str
    #: One of it, as a badge or a decision card says it.
    label: str
    #: Several of it, as a filter says them.
    plural: str
    #: What it is, in a phrase: a brief's prompt and the board's hover read it.
    about: str
    #: What brings it in.
    found_by: str
    #: What an owner's yes starts, in words, when the quest has no brief of its own.
    starts: str
    #: The button that sends a party: what the player does to it.
    verb: str
    #: What stands in the world for it.
    creature: str
    #: The character a quest of this kind is led by.
    leader: str
    #: Stored as a lead (the ``leads`` table) by a finder, the scout or a knowledge map;
    #: the other kinds are read from rows other features write.
    lead: bool = False
    #: A kind of owner proposal. Slop is not one: it is what the board calls a
    #: proposal of another kind that the slopometer judged slop.
    proposal: bool = True
    #: Where its level starts. A case, an issue and a ticket read theirs from what
    #: they ask instead (:func:`embry0.services.world.lead_level`).
    base_level: int = 20
    #: The severities a proposal of this kind carries; () for a kind that carries none (an open issue, a
    #: context audit finding), which ``leads.ask_severities`` therefore cannot name.
    severities: tuple[str, ...] = ()

    def served(self) -> dict[str, Any]:
        """What ``GET /lead-kinds`` serves of it: what the dashboard reads, and nothing more, because
        every field served is API to keep. Its plain names only: the verb, creature, leader and
        level are the game's, which each quest on ``GET /world`` carries."""
        return {
            "id": self.id,
            "label": self.label,
            "plural": self.plural,
            "about": self.about,
            "found_by": self.found_by,
            "lead": self.lead,
        }


_CATALOGUE: tuple[LeadKind, ...] = (
    LeadKind(
        id="case",
        label="Production error",
        plural="Production errors",
        about="a problem the log watcher keeps seeing in production",
        found_by="the log watcher",
        starts="the agents investigate the problem, fix it and open a pull request",
        verb="Attack",
        creature="case_bug",
        leader="developer",
        severities=CASE_SEVERITIES,
    ),
    LeadKind(
        id="issue",
        label="Open issue",
        plural="Open issues",
        about="an open issue nobody has started",
        found_by="the issue tracker",
        starts="the agents plan the issue and start the work, usually ending in a pull request",
        verb="Start quest",
        creature="quest_giver",
        leader="developer",
    ),
    LeadKind(
        id="finding",
        label="Outdated instruction",
        plural="Outdated instructions",
        about="a claim in a file that steers coding agents, which an audit thinks is wrong",
        found_by="the context audit",
        starts="a pull request that changes that one claim in the file",
        verb="Repair",
        creature="audit_signpost",
        leader="context-auditor",
        base_level=9,
    ),
    # The leads finders bring in (spec 2026-10-25, leads).
    LeadKind(
        id="ticket",
        label="Backlog ticket",
        plural="Backlog tickets",
        about="a backlog ticket",
        found_by="the backlog intake",
        starts="the agents take the ticket and work it, usually ending in a pull request",
        verb="Start quest",
        creature="quest_giver",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
    ),
    LeadKind(
        id="ci",
        label="Failing check",
        plural="Failing checks",
        about="a failing CI check",
        found_by="the failing-CI finder",
        starts="the agents find why the check fails and open a pull request that fixes it",
        verb="Attack",
        creature="ci_golem",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
        base_level=45,
    ),
    LeadKind(
        id="alert",
        label="Security alert",
        plural="Security alerts",
        about="a firing alert",
        found_by="the alerts finder",
        starts="the agents fix what the alert names and open a pull request",
        verb="Attack",
        creature="alert_wyrm",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
        base_level=40,
    ),
    LeadKind(
        id="flaky",
        label="Flaky check",
        plural="Flaky checks",
        about="a flaky check",
        found_by="the flaky-check finder",
        starts="the agents find why the check is unreliable and open a pull request",
        verb="Attack",
        creature="flaky_imp",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
        base_level=30,
    ),
    LeadKind(
        id="review",
        label="Unanswered review",
        plural="Unanswered reviews",
        about="review comments nobody answered",
        found_by="the unanswered-review finder",
        starts="the agents answer the open review comments on the pull request's branch",
        verb="Talk",
        creature="review_ghost",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
        base_level=12,
    ),
    # The scout's finds (spec 2026-10-27): a crack is mended like a bug, a weed
    # is pulled like slop, by the Slopmaster.
    LeadKind(
        id="crack",
        label="Crack",
        plural="Cracks",
        about="a crack the scout found in the code",
        found_by="the scout",
        starts="the agents fix what the crack names and open a pull request",
        verb="Mend",
        creature="crack_pot",
        leader="developer",
        lead=True,
        severities=SEVERITIES,
        base_level=25,
    ),
    LeadKind(
        id="weed",
        label="Weed",
        plural="Weeds",
        about="dead or duplicated code the scout found",
        found_by="the scout or a knowledge map",
        starts="the agents remove the dead code, change no behavior, and open a pull request",
        verb="Pull",
        creature="weed_sprout",
        leader=_SLOPMASTER,
        lead=True,
        severities=SEVERITIES,
        base_level=6,
    ),
    # Slop dungeon (spec 2026-10-25): a finding judged slop, not a bug. It is
    # cleaned, not fought, and the Slopmaster leads it.
    LeadKind(
        id="slop",
        label="Slop, not a bug",
        plural="Slop",
        about="code whose shape is the trouble, not its behavior",
        found_by="the slopometer",
        starts="the Slopmaster cleans it up, keeps its behavior and opens a pull request",
        verb="Clean up",
        creature="slop_ooze",
        leader=_SLOPMASTER,
        proposal=False,
    ),
)

#: Every kind, by id, in the catalogue's order.
KINDS: Mapping[str, LeadKind] = MappingProxyType({kind.id: kind for kind in _CATALOGUE})
#: What a kind the catalogue does not know reads as on the board: a mystery to investigate.
UNKNOWN_KIND = LeadKind(
    id="unknown",
    label="Mystery",
    plural="Mysteries",
    about="work of a kind embry0 does not know",
    found_by="",
    starts="",
    verb="Investigate",
    creature="mystery",
    leader="developer",
    proposal=False,
)
#: Creatures the art manifest has no sheet for yet: the map draws each as its original
#: pixel glyph (frontend CreatureIcon's GLYPHS), and the art guard checks the glyph exists.
GLYPH_CREATURES: frozenset[str] = frozenset({"slop_ooze", "crack_pot", "weed_sprout"})


def lead_kind(kind: str) -> LeadKind:
    """The kind ``kind`` names; :data:`UNKNOWN_KIND` when the catalogue has none."""
    return KINDS.get(kind, UNKNOWN_KIND)


def ids(*, lead: bool | None = None, proposal: bool | None = None) -> tuple[str, ...]:
    """The kinds' ids in the catalogue's order, only the leads or the proposals when asked."""
    return tuple(
        k.id
        for k in KINDS.values()
        if (lead is None or k.lead == lead) and (proposal is None or k.proposal == proposal)
    )


def rated() -> Mapping[str, tuple[str, ...]]:
    """The kinds of proposal that carry a severity, each with the severities it can carry, in the catalogue's order."""
    return MappingProxyType({k.id: k.severities for k in KINDS.values() if k.proposal and k.severities})


def id_pattern() -> str:
    """The pattern of a proposal's id, ``<kind>:<key>``, for every kind of proposal: what a brief must
    never carry, since it says nothing to a person."""
    return rf"\b(?:{'|'.join(map(re.escape, ids(proposal=True)))}):[0-9a-z-]{{4,}}"


def catalogue() -> list[dict[str, Any]]:
    """Every kind's names, as ``GET /lead-kinds`` serves them, in the catalogue's order."""
    return [kind.served() for kind in KINDS.values()]
