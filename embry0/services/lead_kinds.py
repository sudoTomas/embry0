"""Lead kinds: every kind of work embry0 offers a person, in one catalogue.

A kind is what a quest on offer is: a problem the log watcher keeps seeing, an
open issue, a context audit finding, a lead a finder or the scout brought in,
or slop. Everything that says something per kind reads it here:

- which kinds a finder or the scout stores as leads (:mod:`embry0.services.leads`)
  and which the owner proposals feed carries (:mod:`embry0.services.proposals`);
- the quest board's verb, creature, leader and starting level
  (:mod:`embry0.services.world`);
- a decision card's label and what a yes starts (:mod:`embry0.services.decisions`);
- what a brief's prompt says the kind is, and which severities are asked in
  chat (:mod:`embry0.services.proposal_briefs`);
- the dashboard's names for each kind (its labels, what it is and what finds
  it), which ``GET /world`` serves as ``lead_kinds``.

The world's art manifest (``frontend/public/world/manifest.json``) names the
creature that stands for each kind on its own, because it is an art file; a
test holds the two equal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from embry0.services.slop import SLOPMASTER

#: The severities of a scout's lead asked in chat; the rest stay on the page, so a
#: scout running all day does not fill the chat (spec 2026-10-27).
SCOUT_CHAT_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})


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
    #: The severities asked in chat; None asks every one.
    chat_severities: frozenset[str] | None = None

    def served(self) -> dict[str, Any]:
        """What ``GET /world`` serves of it: what the dashboard reads, and nothing more, because
        every field served is API to keep."""
        return {
            "id": self.id,
            "label": self.label,
            "plural": self.plural,
            "about": self.about,
            "found_by": self.found_by,
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
        base_level=25,
        chat_severities=SCOUT_CHAT_SEVERITIES,
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
        leader=SLOPMASTER,
        lead=True,
        base_level=6,
        chat_severities=SCOUT_CHAT_SEVERITIES,
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
        leader=SLOPMASTER,
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


def catalogue() -> list[dict[str, Any]]:
    """Every kind's names, as ``GET /world`` serves them, in the catalogue's order."""
    return [kind.served() for kind in KINDS.values()]
